"""FastAPI application — contract surface for docs/contracts/API.md.

Every domain route from API.md is mounted here. Entities live in an
in-memory store and pipeline work runs synchronously inside the request,
so the frontend can exercise the whole flow end to end.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import hashlib
import ipaddress
import json
import logging
import os
import re
import sys
import tempfile
import threading
import zipfile
from collections.abc import AsyncIterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from io import BytesIO
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from urllib.parse import urlparse
from uuid import uuid4

import yaml
from fastapi import FastAPI, File, Form, Header, Query, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from lxml import etree
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sse_starlette.sse import EventSourceResponse

from deckdna.audit import catalog as audit_catalog
from deckdna.audit.basic import audit_deck
from deckdna.audit.config import default_audit_config
from deckdna.audit.contextual import run_contextual_audit
from deckdna.audit.issues import AuditIssue as _RuntimeAuditIssue
from deckdna.contracts.audit_issue import AuditIssue
from deckdna.contracts.audit_issue import Bbox as IssueBbox
from deckdna.contracts.audit_issue import Status as IssueStatus
from deckdna.contracts.content_pack import ContentPack
from deckdna.contracts.deck_plan import Brief, DeckPlan
from deckdna.contracts.design_dna import DesignDNA
from deckdna.contracts.evidence_graph import EvidenceGraph
from deckdna.contracts.quality_passport import ModelProfile
from deckdna.contracts.serialize import to_schema_dict
from deckdna.contracts.variant_spec import Strategy
from deckdna.errors import DeckDNAError
from deckdna.evaluation import measures
from deckdna.evaluation.quality_passport import assemble_quality_passport
from deckdna.generation import pipeline
from deckdna.ingestion import content_parsers
from deckdna.ingestion.content_parsers import parse_file
from deckdna.logging_setup import configure_logging
from deckdna.planning import content_style
from deckdna.planning.config import load_generation_config
from deckdna.planning.evidence import build_evidence_graph
from deckdna.planning.story_director import plan_deck, plan_deck_llm
from deckdna.planning.story_director_v2 import plan_deck_llm_v2
from deckdna.pptx.exporting import previews as _previews
from deckdna.pptx.exporting.render import render_pdf
from deckdna.pptx.fitting import text_fit
from deckdna.pptx.opc.package import OpcPackage
from deckdna.providers.base import ModelGateway
from deckdna.providers.factory import build_gateway
from deckdna.providers.openai_compat import OpenAICompatibleProvider
from deckdna.repair import outcomes as repair_outcomes
from deckdna.repair.apply import apply_repairs
from deckdna.repair.planner import plan_repairs_with_report
from deckdna.settings import settings
from deckdna.template import autopsy
from deckdna.template import dna as dna_builder

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"
SCHEMA_VERSION = "freeze-1"

# api/app.py -> deckdna -> backend -> repo root; used to locate skill/manifest.yaml
# regardless of the process working directory.
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Page[T](BaseModel):
    """Opaque-cursor page envelope (API.md §1)."""

    items: list[T]
    next_cursor: str | None = None


class ErrorBody(BaseModel):
    """Typed error object from API.md §1."""

    code: str
    message: str
    stage: str | None = None
    retryable: bool = False
    request_id: str | None = None
    details: dict[str, Any] = {}


class ErrorEnvelope(BaseModel):
    error: ErrorBody


# ---------------------------------------------------------------------------
# Request/response DTOs (entity shapes from docs/contracts/API.md;
# cross-workstream artifact payloads reuse deckdna.contracts models).
# ---------------------------------------------------------------------------


class JobState(StrEnum):
    queued = "queued"
    running = "running"
    awaiting_user = "awaiting_user"
    completed = "completed"
    failed = "failed"
    canceled = "canceled"


class JobKind(StrEnum):
    template_analysis = "template_analysis"
    content_ingestion = "content_ingestion"
    generation = "generation"
    audit = "audit"
    repair = "repair"
    export = "export"


class JobError(BaseModel):
    code: str
    message: str
    stage: str | None = None


class IssueOutcome(BaseModel):
    """Исход repair по одной выбранной проблеме (contract D2)."""

    issue_id: str
    status: Literal["fixed", "failed", "skipped", "planned"]
    action: str | None = None
    summary: str | None = None
    reason: str | None = None
    # стабильный между ревизиями идентификатор проблемы (см. AuditIssue)
    fingerprint: str | None = None


class RepairJobResult(BaseModel):
    """Типизированный итог repair-job: счётчики — числа, не строки.

    ``applied`` — только реально исправленное (проблема исчезла из
    повторного аудита); остальное — ``failed`` / ``skipped`` /
    ``unresolved`` с причиной в ``outcomes``."""

    applied: int = 0
    skipped: int = 0
    failed: int = 0
    unresolved: int = 0
    not_implemented: int = 0
    audit_id: str | None = None
    deck_revision: int | None = None
    deck_artifact_id: str | None = None
    outcomes: list[IssueOutcome] = []


class Job(BaseModel):
    id: str
    kind: JobKind
    state: JobState
    stage: str | None = None
    progress: float = Field(ge=0.0, le=1.0)
    project_id: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: JobError | None = None
    result_ids: dict[str, str] = {}
    # Типизированный результат (сейчас — у repair-job); result_ids
    # остаётся ради совместимости со строковыми счётчиками.
    result: RepairJobResult | None = None


class ModelSpec(BaseModel):
    text: str
    vision: str
    embedding: str | None = None
    image: str | None = None


class ProviderCapabilities(BaseModel):
    structured_output: bool = False
    tool_calls: bool = False
    image_input: bool = False
    embeddings: bool = False


class ProviderSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    base_url: str
    api_token: str = Field(min_length=1)
    models: ModelSpec
    capabilities: ProviderCapabilities = ProviderCapabilities()
    timeout_seconds: int = 90
    max_concurrency: int = 4
    ttl_seconds: int = 14400
    project_id: str | None = None

    @field_validator("base_url")
    @classmethod
    def _base_url_is_safe(cls, value: str) -> str:
        # base_url публикуется в ProviderSession наружу — userinfo с
        # кредами в URL утёк бы в ответ; креды места в api_token, не в URL.
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an http(s) URL")
        if parsed.username or parsed.password:
            raise ValueError("base_url must not carry userinfo credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not carry query or fragment data")
        return value


def _validate_provider_network_target(base_url: str, *, allow_private: bool) -> None:
    """Reject obvious SSRF destinations before creating a provider lease.

    URL hostnames are not resolved here: DNS resolution in an API request can
    itself be attacker-controlled and a later DNS rebinding still requires an
    egress policy. Literal IPs and reserved local names are deterministic and
    therefore blocked by default. Operators of a trusted local workspace can
    explicitly opt in for loopback/private stubs.
    """
    if allow_private:
        return
    hostname = (urlparse(base_url).hostname or "").rstrip(".").casefold()
    if hostname in {
        "localhost",
        "localhost.localdomain",
        "host.docker.internal",
        "metadata.google.internal",
    } or hostname.endswith(".localhost"):
        raise DeckDNAError(
            "invalid_input",
            "provider base_url targets a local or metadata hostname; "
            "set allow_private_networks only in a trusted local deployment",
            http_status=422,
        )
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        # getaddrinfo accepts abbreviated/decimal/hex IPv4 spellings such
        # as 127.1, 2130706433 and 0x7f000001 as 127.0.0.1. They are not
        # parsed by ipaddress, so reject ambiguous numeric-only hosts before
        # they can be treated as ordinary public DNS names.
        numeric_host = r"(?:0[xX][0-9a-fA-F]+|[0-9]+)(?:\.(?:0[xX][0-9a-fA-F]+|[0-9]+))*"
        if re.fullmatch(numeric_host, hostname):
            raise DeckDNAError(
                "invalid_input",
                "provider base_url has an ambiguous numeric hostname",
                http_status=422,
            ) from None
        return
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise DeckDNAError(
            "invalid_input",
            "provider base_url targets a private, loopback, link-local, "
            "multicast, reserved or unspecified address",
            http_status=422,
        )


class ProviderSession(BaseModel):
    """Session view — токен и рабочие лимиты хранятся в lease-сторе
    (in-memory lease store) и никогда не возвращаются API."""

    id: str
    label: str
    base_url: str
    models: ModelSpec
    capabilities: ProviderCapabilities
    project_id: str | None = None
    created_at: datetime
    expires_at: datetime


class CapabilityProbe(BaseModel):
    capability: str
    status: Literal["ok", "fail", "skip"]
    detail: str | None = None


class ProviderSessionTestResult(BaseModel):
    session_id: str
    results: list[CapabilityProbe]
    tested_at: datetime


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1)
    default_language: str = "ru"
    target_slide_count: int = Field(default=12, ge=3, le=40)


class ProjectPatch(BaseModel):
    name: str | None = None
    default_language: str | None = None
    target_slide_count: int | None = Field(default=None, ge=3, le=40)


class Project(BaseModel):
    id: str
    name: str
    status: Literal["draft", "ready", "archived"]
    default_language: str
    target_slide_count: int
    template_id: str | None = None
    content_pack_id: str | None = None
    # последний прогон проекта (D10) — вычисляется из STORE.runs
    latest_run_id: str | None = None
    latest_run_state: JobState | None = None
    latest_run_stage: str | None = None
    created_at: datetime
    updated_at: datetime


class TemplateAsset(BaseModel):
    id: str
    project_id: str
    filename: str
    media_type: str
    package_type: Literal["pptx", "potx"]
    sha256: str
    size_bytes: int
    artifact_id: str
    validation_status: Literal["pending", "valid", "invalid"]
    created_at: datetime


class TemplateAnalysisRequest(BaseModel):
    provider_session_id: str | None = None
    config_version: str | None = None
    # ADR-018: same auto/explicit/off contract as GenerationCreate/
    # PlanRequest/RepairRequest (see _gateway_for) — gates ONLY the new
    # exemplar content_description enrichment; autopsy itself stays fully
    # deterministic either way (see build_design_dna's own docstring).
    use_llm: bool | None = None


class AnalysisAccepted(BaseModel):
    job_id: str
    analysis_id: str


class TemplateAnalysis(BaseModel):
    id: str
    template_id: str
    revision: int
    status: Literal["running", "completed", "failed"]
    parser_version: str
    config_version: str | None = None
    design_dna_revision: int | None = None
    preview_artifact_ids: list[str]
    package_inventory: dict[str, Any] | None = None
    warnings: list[str]
    created_at: datetime
    finished_at: datetime | None = None


class TemplateDetail(TemplateAsset):
    latest_analysis: TemplateAnalysis | None = None


class SlidePreviewMeta(BaseModel):
    id: str
    slide_index: int
    role: str | None = None
    purpose: str | None = None
    title: str | None = None
    part: str | None = None
    mutability: str | None = None
    preview_artifact_id: str | None = None


class ContentPackAccepted(BaseModel):
    content_pack: ContentPack
    job: Job


class VariantRequest(BaseModel):
    strategy: Literal["faithful", "balanced", "visual", "custom"]
    # Принимается под будущее: сейчас no-op — в пайплайне нет ни одного
    # источника управляемой случайности (детерминированный путь её не
    # нуждается, LLM-семплинг через этот API не сидируется). Реальный
    # смысл появится вместе с LLM top-k reranking'ом / tie-breaking.
    seed: int | None = Field(
        default=None,
        description=(
            "Accepted for future use; currently a no-op — the pipeline has "
            "no source of controlled randomness to seed (deterministic path "
            "needs none; LLM sampling is not seed-wired through this API)."
        ),
    )
    # Принимается, но на генерацию не применяется и на вариант не
    # сохраняется. На эндпоинтах анализа/аудита config_version реально
    # пишется в запись как metadata — там он не no-op.
    config_version: str | None = Field(
        default=None,
        description=(
            "Accepted but currently not applied to generation behaviour or "
            "stored on the variant (on analysis/audit endpoints it IS "
            "recorded as metadata)."
        ),
    )


class GenerationCreate(BaseModel):
    template_id: str
    content_pack_id: str
    provider_session_id: str | None = None
    brief: Brief
    variants: list[VariantRequest] = Field(min_length=1, max_length=3)
    # Как и VariantRequest.config_version: принимается, на генерацию не
    # применяется и никуда не пишется (в отличие от того же поля на
    # эндпоинтах анализа/аудита, где оно — metadata записи).
    config_version: str | None = Field(
        default=None,
        description=(
            "Accepted but currently not applied to generation behaviour or "
            "stored on the run."
        ),
    )
    # Как VariantRequest.seed: accepted-for-future-use, сейчас no-op —
    # сидировать нечего, источников случайности в пайплайне нет.
    seed: int | None = Field(
        default=None,
        description=(
            "Accepted for future use; currently a no-op — the pipeline has "
            "no source of controlled randomness to seed."
        ),
    )
    # Режим модели (паритет с `deckdna generate --llm`): планирование
    # (plan_deck_llm), rerank образцов, переписывание переполненного текста
    # (text_fit) и contextual VLM-аудит. provider_session_id выбирает gateway
    # по кредам lease-сессии; null — авто (см. _gateway_for).
    use_llm: bool | None = Field(
        default=None,
        description=(
            "Model mode. null (default) = auto: the model is used whenever a "
            "provider is available (provider_session_id, or a real server "
            "provider configured via DECKDNA_PROVIDER_* with mock off). "
            "true = force the model (server gateway, mock if configured so). "
            "false = deterministic path, no model calls."
        ),
    )
    # Собрать варианты по уже готовому плану (D7): либо id набора планов
    # из POST /projects/{id}/plans, либо сам DeckPlan (после правки
    # пользователем). Оба сразу — 422. Планирование при этом пропускается.
    deck_plan_id: str | None = None
    deck_plan: DeckPlan | None = None


class GenerationAccepted(BaseModel):
    generation_id: str
    job_id: str
    variant_ids: list[str]


class VariantMetrics(BaseModel):
    validity: float | None = None
    editability_pei: int | None = None
    issues_total: int | None = None
    # соответствие шаблону, одно число 0..1 (среднее по четырём осям
    # паспорта): карточка варианта не открывает паспорт (D3)
    style_fidelity: float | None = None
    # сколько проблем конвейер исправил сам до ревизии 1 (Q2)
    auto_fixed: int | None = None


class VariantAxes(BaseModel):
    """Оси различий вариантов, 0..1 (D11) — из configs/variants.default.yaml."""

    text_density: float
    layout_diversity: float
    visualization: float


PipelineStage = Literal["content", "plan", "compose", "audit", "render", "passport"]


class VariantSummary(BaseModel):
    id: str
    run_id: str
    strategy: str
    status: JobState
    rationale: str | None = None
    # этап конвейера, на котором сейчас вариант (None вне running) и
    # время его работы — экран генерации показывает «готово · 0:32» (D4)
    stage: PipelineStage | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    axes: VariantAxes | None = None
    deck_artifact_id: str | None = None
    montage_artifact_id: str | None = None
    metrics: VariantMetrics | None = None
    audit_status: Literal["not_started", "running", "completed", "failed"] = "not_started"
    export_ids: list[str] = []
    # Что реально спланировало колоду и сколько VLM-issues вернул
    # contextual-аудит (None = LLM-режим выключен / не запускался).
    planner: str | None = None
    contextual_issues: int | None = None
    # id плана ЭТОГО варианта — при разных стратегиях планы различаются,
    # run-level deck_plan хранит план первого варианта.
    deck_plan_id: str | None = None


class GenerationDetail(BaseModel):
    id: str
    project_id: str
    state: JobState
    job_id: str
    events_url: str
    template_id: str
    content_pack_id: str
    deck_plan_id: str | None = None
    deck_plan: DeckPlan | None = None
    variants: list[VariantSummary]
    parent_run_id: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


class SlideInfo(BaseModel):
    id: str
    variant_id: str
    index: int
    slide_plan_id: str | None = None
    purpose: str | None = None
    title: str | None = None
    revision: int = 1
    preview_artifact_id: str | None = None


class AuditRequest(BaseModel):
    provider_session_id: str | None = None
    config_version: str | None = None


class AuditAccepted(BaseModel):
    audit_id: str
    job_id: str


class IssueSummary(BaseModel):
    info: int = 0
    warning: int = 0
    error: int = 0
    blocker: int = 0


class AuditRun(BaseModel):
    id: str
    variant_id: str
    deck_revision: int
    status: Literal["running", "completed", "failed"]
    deterministic_status: Literal["pending", "running", "completed", "failed"]
    contextual_status: Literal["pending", "running", "completed", "failed", "skipped"]
    issue_count: int
    summary_by_severity: IssueSummary
    summary_by_type: dict[str, int]
    config_version: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


class FixPreview(BaseModel):
    """Что будет сделано для проблемы — считает тот же планировщик (D8)."""

    title_ru: str | None = None
    description_ru: str
    actions: list[str] = []


class AuditIssueOut(AuditIssue):
    """Ответная форма замороженного контракта AuditIssue.

    Контракт (schemas/audit-issue.schema.json) не меняется: поля ниже —
    аддитивное расширение API-слоя (ADR-011). Все опциональны."""

    fingerprint: str | None = None
    fix_preview: FixPreview | None = None
    # bbox, обрезанный в [0,1]; сам bbox не трогается — у
    # layout.out_of_bounds выход за слайд и есть суть проблемы (Q4)
    clipped_bbox: IssueBbox | None = None


class RepairRequest(BaseModel):
    # с моделью переполненный текст переписывается (text_fit), а не
    # обрезается с «…»; null — авто, как у генерации
    provider_session_id: str | None = None
    selected_issue_ids: list[str] = Field(min_length=1)
    max_iterations: int = Field(default=2, ge=1, le=5)
    use_llm: bool | None = Field(
        default=None,
        description=(
            "Model mode. null (default) = auto: the model is used whenever a "
            "provider is available (provider_session_id, or a real server "
            "provider configured via DECKDNA_PROVIDER_* with mock off). "
            "true = force the model (server gateway, mock if configured so). "
            "false = deterministic path, no model calls."
        ),
    )


class RepairAccepted(BaseModel):
    job_id: str
    audit_id: str
    deck_revision: int


class RepairPreview(BaseModel):
    """Ответ ``POST /audits/{id}/repairs?dry_run=true``: что было бы
    сделано, без применения (D2). Ревизия и проблемы не меняются."""

    audit_id: str
    deck_revision: int
    dry_run: Literal[True] = True
    outcomes: list[IssueOutcome]


class IssueDismissRequest(BaseModel):
    reason: str = Field(min_length=1)


class ExportRequest(BaseModel):
    formats: list[Literal["pptx", "pdf", "html", "quality_passport"]] = Field(min_length=1)


class ExportAccepted(BaseModel):
    export_id: str
    job_id: str


class ExportArtifact(BaseModel):
    format: str
    artifact_id: str
    sha256: str
    size_bytes: int
    mime_type: str
    download_url: str


class ExportRecord(BaseModel):
    id: str
    variant_id: str
    deck_revision: int
    job_id: str
    artifacts: list[ExportArtifact]
    created_at: datetime


class PlanRequest(BaseModel):
    """План до вёрстки (D7): структура колоды, которую можно поправить."""

    content_pack_id: str
    brief: Brief
    strategies: list[Literal["faithful", "balanced", "visual", "custom"]] = Field(
        default_factory=lambda: ["faithful", "balanced", "visual"],
        min_length=1,
        max_length=3,
    )
    template_id: str | None = None
    provider_session_id: str | None = None
    use_llm: bool | None = Field(
        default=None,
        description=(
            "Model mode. null (default) = auto: the model is used whenever a "
            "provider is available (provider_session_id, or a real server "
            "provider configured via DECKDNA_PROVIDER_* with mock off). "
            "true = force the model (server gateway, mock if configured so). "
            "false = deterministic path, no model calls."
        ),
    )


class PlanEntry(BaseModel):
    strategy: str
    deck_plan: DeckPlan


class PlanSet(BaseModel):
    plan_id: str
    project_id: str
    content_pack_id: str
    plans: list[PlanEntry]
    created_at: datetime


class GenerationSummary(BaseModel):
    """Строка списка прогонов проекта (D10) — без тяжёлого DeckPlan."""

    id: str
    project_id: str
    state: JobState
    stage: str | None = None
    job_id: str
    template_id: str
    content_pack_id: str
    variant_ids: list[str]
    parent_run_id: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


class RuleInfoOut(BaseModel):
    """Строка каталога правил аудита (D8)."""

    code: str
    title_ru: str
    category: str
    deterministic: bool
    default_severity: str
    repairable: bool
    fix_title_ru: str | None = None
    threshold: str | float | int | None = None


# ---------------------------------------------------------------------------
# In-memory store — Postgres repositories and a real job queue replace it
# in a deployment build; the store exists to keep responses coherent
# (created entities are visible to subsequent GETs).
# ---------------------------------------------------------------------------


class _Artifact(BaseModel):
    id: str
    project_id: str | None
    type: str
    mime_type: str
    filename: str
    sha256: str
    size_bytes: int
    created_at: datetime
    data: bytes = b""


class _InternalRun(BaseModel):
    """GenerationRun bookkeeping: run fields plus owned child records."""

    detail: GenerationDetail
    variant_strategy: dict[str, str] = {}
    deck_revision: dict[str, int] = {}
    request: GenerationCreate | None = None
    # variant_id -> {format -> artifact_id} for artifacts the pipeline produced
    variant_artifacts: dict[str, dict[str, str]] = {}
    # variant_id -> фактический DeckPlan варианта (run.detail.deck_plan —
    # только план первого варианта; VLM-аудиту нужен план именно этого
    # варианта, иначе checks граундятся в чужом плане)
    variant_plans: dict[str, DeckPlan] = {}
    # variant_id -> данные, нужные, чтобы лениво пересобрать паспорт
    # ревизии после repair (compose_report, pack, brief, usage, ...)
    variant_reports: dict[str, dict[str, Any]] = {}
    # готовые планы, по которым собираются варианты (D7); strategy -> plan
    supplied_plans: dict[str, DeckPlan] = {}
    plan_source: Literal["generated", "plan_set", "user"] = "generated"


class _Store:
    def __init__(self) -> None:
        self.projects: dict[str, Project] = {}
        self.provider_sessions: dict[str, ProviderSession] = {}
        # Lease-держатель api_token + рабочих полей ProviderSessionCreate
        # (timeout/concurrency не входят в публичную модель сессии).
        # Токен никогда не сериализуется в ответы — in-memory lease store,
        # семантика lease: TTL + revoke по DELETE.
        self.provider_secrets: dict[str, dict[str, Any]] = {}
        self.templates: dict[str, TemplateAsset] = {}
        self.analyses: dict[str, TemplateAnalysis] = {}
        self.design_dna: dict[str, DesignDNAOut] = {}
        self.template_slides: dict[str, list[SlidePreviewMeta]] = {}
        self.content_packs: dict[str, ContentPack] = {}
        self.pack_artifacts: dict[str, list[str]] = {}
        # Upload IDs are server-generated (ADR-023); this mapping records
        # ownership independently of parser-supplied/content-derived IDs.
        self.content_pack_projects: dict[str, set[str]] = {}
        self.evidence_graphs: dict[str, EvidenceGraph] = {}
        self.runs: dict[str, _InternalRun] = {}
        self.slides: dict[str, list[SlideInfo]] = {}
        self.audits: dict[str, AuditRun] = {}
        self.issues: dict[str, list[AuditIssueOut]] = {}
        self.plans: dict[str, PlanSet] = {}
        self.exports: dict[str, ExportRecord] = {}
        self.jobs: dict[str, Job] = {}
        self.artifacts: dict[str, _Artifact] = {}
        self.idempotency: dict[tuple[str, str, str], dict[str, Any]] = {}


STORE = _Store()


def _require[T](mapping: dict[str, T], key: str, what: str) -> T:
    try:
        return mapping[key]
    except KeyError:
        raise DeckDNAError("not_found", f"{what} not found: {key}") from None


def _require_session(
    session_id: str, project_id: str | None = None
) -> ProviderSession:
    """Provider session с проверкой lease: живой TTL + project scope.

    Истёкшая сессия вытесняется (вместе с токеном) и отвечает 404 —
    как несуществующая. Сессия, привязанная к проекту, не может быть
    использована в чужом проекте.
    """
    session = _require(STORE.provider_sessions, session_id, "provider session")
    if session.expires_at <= _utcnow():
        STORE.provider_sessions.pop(session_id, None)
        STORE.provider_secrets.pop(session_id, None)
        raise DeckDNAError(
            "not_found", f"provider session expired: {session_id}"
        )
    if (
        project_id is not None
        and session.project_id is not None
        and session.project_id != project_id
    ):
        raise DeckDNAError(
            "invalid_input",
            f"provider session {session_id} is bound to project "
            f"{session.project_id}, not {project_id}",
        )
    return session


def _require_owned_template(template_id: str, project_id: str) -> TemplateAsset:
    """Template lookup scoped to *project_id* — generation/
    plan inputs previously checked only that template_id existed anywhere in
    STORE, so an ID from project B worked unchanged inside project A. A
    cross-project ID is reported exactly like a missing one (404), not a
    distinct "wrong project" error — same discipline as _require itself."""
    template = _require(STORE.templates, template_id, "template")
    if template.project_id != project_id:
        raise DeckDNAError("not_found", f"template not found: {template_id}")
    return template


def _require_owned_content_pack(pack_id: str, project_id: str) -> ContentPack:
    """Content pack lookup scoped to the project that uploaded it."""
    pack = _require(STORE.content_packs, pack_id, "content pack")
    owners = STORE.content_pack_projects.get(pack_id)
    if not owners or project_id not in owners:
        raise DeckDNAError("not_found", f"content pack not found: {pack_id}")
    return pack


def _session_gateway(session: ProviderSession) -> OpenAICompatibleProvider:
    """Gateway по lease-секрету сессии (не env settings)."""
    secret = _require(
        STORE.provider_secrets, session.id, "provider session secret"
    )
    return OpenAICompatibleProvider(
        base_url=session.base_url,
        api_key=secret["api_token"],
        model_text=session.models.text,
        model_vision=session.models.vision or "",
        model_embed=session.models.embedding or "",
        timeout_s=float(secret["timeout_seconds"]),
        chat_options=settings.provider_chat_options,
    )


def _server_provider_ready() -> bool:
    """На сервере настроен настоящий провайдер (не offline-mock)."""
    return (
        not settings.mock_provider
        and bool(settings.provider_base_url)
        and bool(settings.provider_api_key)
        and bool(settings.model_text)
    )


def _gateway_for(
    body: GenerationCreate | PlanRequest | RepairRequest | TemplateAnalysisRequest,
    project_id: str | None = None,
) -> ModelGateway | None:
    """Выбор gateway: где модель используется, а где нет.

    - ``provider_session_id`` → gateway по кредам сессии (сессия и есть
      источник провайдера, модель включена);
    - ``use_llm=false`` → детерминированный путь, без модели;
    - ``use_llm=true`` → серверный gateway (``build_gateway()``: настоящий
      провайдер или mock, как настроен сервер);
    - ``use_llm=null`` (по умолчанию) → **авто**: модель включается, если на
      сервере настроен настоящий провайдер; offline-mock сам по себе
      модель не включает — фиктивные ответы не выдаются за работу модели.
    """
    if body.provider_session_id is not None:
        return _session_gateway(_require_session(body.provider_session_id, project_id))
    if body.use_llm is False:
        return None
    if body.use_llm is True:
        return build_gateway()
    return build_gateway() if _server_provider_ready() else None


def _store_artifact(
    data: bytes,
    *,
    type: str,
    mime_type: str,
    filename: str,
    project_id: str | None = None,
) -> _Artifact:
    artifact = _Artifact(
        id=_new_id("art"),
        project_id=project_id,
        type=type,
        mime_type=mime_type,
        filename=filename,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        created_at=_utcnow(),
        data=data,
    )
    STORE.artifacts[artifact.id] = artifact
    return artifact


def _checked_upload_filename(filename: str) -> str:
    """Keep untrusted multipart names from becoming paths during staging."""
    if (
        not filename
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or any(ord(char) < 32 or ord(char) == 127 for char in filename)
    ):
        raise DeckDNAError(
            "invalid_input",
            "uploaded filename must be a plain file name without path separators "
            "or control characters",
            http_status=422,
        )
    return filename


_UPLOAD_CHUNK_BYTES = 1024 * 1024


async def _read_upload_limited(file: UploadFile, limit_mb: int) -> bytes:
    """Read *file* in bounded chunks, rejecting as soon as the running
    total crosses *limit_mb* -- never fully buffers an oversized upload
    before checking its size (``await file.read()``
    with no size argument reads the whole body regardless of how large
    it is, so the existing post-hoc ``len(data) > limit`` check only
    rejects after the damage -- unbounded memory/disk use -- is done)."""
    limit = limit_mb * 1024 * 1024
    total = 0
    chunks: list[bytes] = []
    while True:
        chunk = await file.read(_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise DeckDNAError(
                "invalid_input",
                f"upload exceeds {limit_mb} MB limit",
                http_status=413,
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _complete_job(kind: JobKind, project_id: str | None, result_ids: dict[str, str]) -> Job:
    now = _utcnow()
    job = Job(
        id=_new_id("job"),
        kind=kind,
        state=JobState.completed,
        progress=1.0,
        project_id=project_id,
        created_at=now,
        started_at=now,
        finished_at=now,
        result_ids=result_ids,
    )
    STORE.jobs[job.id] = job
    return job


# ---------------------------------------------------------------------------
# Stub payload factories — minimal schema-valid samples so the frontend can
# build real screens against realistic shapes; the pipeline replaces these
# with actual outputs at run time.
# ---------------------------------------------------------------------------

_PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

# Mirrors the suffix dispatch in ingestion.content_parsers._PARSERS — the
# first uploaded file with one of these becomes the parsed pack source.
# Derived from the ingestion parser registry — stays in sync automatically.
_SUPPORTED_CONTENT_SUFFIXES = set(content_parsers._PARSERS)
_MIME_BY_FORMAT = {"pptx": _PPTX_MIME, "pdf": "application/pdf", "html": "text/html"}


_PART_NUMBER = re.compile(r"(\d+)\.xml$")


def _part_number(name: str) -> int:
    match = _PART_NUMBER.search(name)
    return int(match.group(1)) if match else 0


def _pptx_part_names(data: bytes) -> dict[str, list[str]]:
    """Real OOXML part names from the uploaded package (census, no invention)."""
    patterns = {
        "slides": re.compile(r"^ppt/slides/slide\d+\.xml$"),
        "layouts": re.compile(r"^ppt/slideLayouts/slideLayout\d+\.xml$"),
        "masters": re.compile(r"^ppt/slideMasters/slideMaster\d+\.xml$"),
        "themes": re.compile(r"^ppt/theme/theme\d+\.xml$"),
    }
    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = zf.namelist()
    return {
        key: sorted((n for n in names if rx.match(n)), key=_part_number)
        for key, rx in patterns.items()
    }


def _pptx_slide_size(data: bytes) -> tuple[int, int]:
    """sldSz from ppt/presentation.xml — real EMU dimensions of the package."""
    with zipfile.ZipFile(BytesIO(data)) as zf:
        root = etree.fromstring(zf.read("ppt/presentation.xml"))
    node = root.find(".//p:sldSz", autopsy.NS)
    if node is None or node.get("cx") is None or node.get("cy") is None:
        raise DeckDNAError("package_corrupt", "presentation.xml has no sldSz")
    return int(node.get("cx", "0")), int(node.get("cy", "0"))


def _pptx_theme_fonts(
    data: bytes, theme_parts: list[str]
) -> dict[str, tuple[str | None, str | None]]:
    """Per-theme (majorFont, minorFont) latin typefaces, read from each theme part."""
    out: dict[str, tuple[str | None, str | None]] = {}
    with zipfile.ZipFile(BytesIO(data)) as zf:
        for part in theme_parts:
            root = etree.fromstring(zf.read(part))
            fonts: dict[str, str | None] = {}
            for tag in ("majorFont", "minorFont"):
                node = root.find(f".//a:fontScheme/a:{tag}/a:latin", autopsy.NS)
                fonts[tag] = node.get("typeface") if node is not None else None
            out[part] = (fonts["majorFont"], fonts["minorFont"])
    return out


class DnaConflict(BaseModel):
    """Расхождение declared и observed и решение системы (D1)."""

    kind: Literal["font", "color"]
    detail: str
    resolution: str


class DesignDNAOut(DesignDNA):
    """Ответная форма замороженного контракта DesignDNA (ADR-011):
    к контракту добавлены необязательные ``conflicts`` и ``confidence``."""

    conflicts: list[DnaConflict] = []
    # уверенность по группам 0..1: palette, fonts, sizes, grid, anchors, layouts, roles
    confidence: dict[str, float] = {}


def _forensics_to_design_dna(
    forensics: autopsy.TemplateForensics,
    data: bytes,
    template_id: str,
    analysis_id: str,
    slide_ids: dict[int, str],
) -> tuple[DesignDNAOut, dict[int, dict[str, Any]], list[Any]]:
    """TemplateForensics + пакет → DesignDNA (template/dna.py).

    Всё вычисляется из самого пакета; поля, для которых в пакете нет
    данных, остаются пустыми — ничего не выдумывается. Возвращает ещё и
    роль/заголовок/mutability каждого слайда для списка слайдов шаблона,
    и (ADR-018) сырые ``_SlideFeatures`` — вызывающий код опционально
    прогоняет их через ``dna_builder.describe_exemplars()`` (LLM,
    отдельно от этого полностью детерминированного построения)."""
    build = dna_builder.build_design_dna(
        data,
        forensics,
        template_id,
        analysis_id,
        slide_ids,
        _utcnow(),
        SCHEMA_VERSION,
    )
    out = DesignDNAOut(
        **build.design_dna.model_dump(),
        conflicts=[DnaConflict(**c.to_dict()) for c in build.conflicts],
        confidence=build.confidence,
    )
    return out, build.slide_meta, build.features


# ---------------------------------------------------------------------------
# Application, middleware and error handling.
# ---------------------------------------------------------------------------


# API server: stdout carries no machine-readable contract (unlike the CLI/
# skill entrypoints, see logging_setup.py) -- safe to log there directly,
# and that's what `docker compose logs api` captures.
configure_logging(stream=sys.stdout)

app = FastAPI(
    title="DeckDNA",
    version="0.1.0",
    openapi_url="/openapi.json",
    responses={
        "4XX": {
            "model": ErrorEnvelope,
            "description": "Client error — typed DeckDNAError envelope (API.md §1)",
        },
        "5XX": {
            "model": ErrorEnvelope,
            "description": "Server/dependency error — typed DeckDNAError envelope",
        },
    },
)


def _mark_binary_fields(node: Any) -> None:
    # FastAPI emits OpenAPI 3.1 `contentMediaType` for UploadFile. Swagger UI
    # only renders a file picker for `format: binary` (and never for array
    # items), so add the 3.0-style marker alongside for codegen/UI clients.
    if isinstance(node, dict):
        if node.get("type") == "string" and node.get("contentMediaType"):
            node["format"] = "binary"
        for value in node.values():
            _mark_binary_fields(value)
    elif isinstance(node, list):
        for value in node:
            _mark_binary_fields(value)


def _custom_openapi() -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema
    spec = get_openapi(
        title=app.title, version=app.version, routes=app.routes
    )
    _mark_binary_fields(spec)
    app.openapi_schema = spec
    return spec


app.openapi = _custom_openapi  # type: ignore[method-assign]

_STATUS_BY_CODE = {
    "invalid_input": 422,
    "package_corrupt": 422,
    "unsupported_encrypted_template": 422,
    "not_found": 404,
    "not_implemented": 501,
    "idempotency_conflict": 409,
    "state_conflict": 409,
    "provider_unavailable": 503,
    "time_budget_exceeded": 503,
    "internal_error": 500,
}


_IDEMPOTENCY_LOCKS: dict[tuple[str, str, str], asyncio.Lock] = {}


def _idempotency_lock(cache_key: tuple[str, str, str]) -> asyncio.Lock:
    lock = _IDEMPOTENCY_LOCKS.get(cache_key)
    if lock is None:
        lock = asyncio.Lock()
        _IDEMPOTENCY_LOCKS[cache_key] = lock
    return lock


@app.middleware("http")
async def contract_middleware(request: Request, call_next: Any) -> Response:
    """Attach X-Request-ID; implement API.md §12 idempotency replay (in-memory).

    The record was read before ``call_next`` with no
    reservation of the key, so two concurrent requests sharing a key
    could both see "no record yet" and both execute the mutation (e.g.
    both create a generation job). Holding a per-cache-key asyncio.Lock
    across the read-execute-write section makes it single-flight: the
    second concurrent request blocks until the first has written its
    record, then replays it instead of re-executing. Different keys use
    different locks (created on demand, same pattern as
    ``_variant_lock``), so unrelated requests never contend.

    Separately, only caching responses whose content-type starts with
    "application/json" never matched a bodyless 2xx (204 No Content —
    every DELETE in this API) -- its record was never written, so a
    retried DELETE with the same key re-ran the handler and got 404
    (already deleted) instead of the original 204 API.md §12 promises.
    """
    request.state.request_id = uuid4().hex
    headers = {"X-Request-ID": request.state.request_id}
    is_mutation = request.method in {"POST", "PATCH", "DELETE"}
    idem_key = request.headers.get("idempotency-key") if is_mutation else None
    cache_key = (request.method, request.url.path, idem_key or "")
    if not idem_key:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    body_hash = hashlib.sha256(await request.body()).hexdigest()
    async with _idempotency_lock(cache_key):
        record = STORE.idempotency.get(cache_key)
        if record is not None:
            if record["body_hash"] != body_hash:
                err = DeckDNAError(
                    "idempotency_conflict",
                    "Idempotency-Key was already used with a different request body",
                )
                return JSONResponse(
                    status_code=409,
                    content=err.to_envelope(request.state.request_id),
                    headers=headers,
                )
            if record["body"]:
                return Response(
                    content=record["body"],
                    status_code=record["status"],
                    media_type="application/json",
                    headers=headers,
                )
            return Response(
                content=record["body"], status_code=record["status"], headers=headers
            )

        response = await call_next(request)
        body_bytes = b"".join([chunk async for chunk in response.body_iterator])
        if 200 <= response.status_code < 300 and (
            not body_bytes
            or response.headers.get("content-type", "").startswith("application/json")
        ):
            STORE.idempotency[cache_key] = {
                "body_hash": body_hash,
                "status": response.status_code,
                "body": body_bytes,
            }
        out_headers = dict(response.headers)
        out_headers["X-Request-ID"] = request.state.request_id
        return Response(
            content=body_bytes,
            status_code=response.status_code,
            headers=out_headers,
        )


@app.exception_handler(DeckDNAError)
async def deckdna_error_handler(request: Request, exc: DeckDNAError) -> JSONResponse:
    status = exc.http_status or _STATUS_BY_CODE.get(exc.code) or (
        503 if exc.retryable else 422
    )
    return JSONResponse(
        status_code=status,
        content=exc.to_envelope(getattr(request.state, "request_id", None)),
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # API.md §1: even malformed-domain-input failures keep the error envelope.
    # exc.errors() несёт `input` (сырое тело запроса — может содержать
    # api_token) и `ctx` (значения валидаторов); оба вырезаются — в конверт
    # ошибки не должен попадать ни один байт пользовательского ввода, в
    # котором могут лежать секреты.
    errors = [
        {key: value for key, value in error.items() if key not in {"input", "ctx"}}
        for error in exc.errors()
    ]
    err = DeckDNAError(
        "invalid_input",
        "request validation failed",
        details={"errors": jsonable_encoder(errors)},
    )
    return JSONResponse(
        status_code=422,
        content=err.to_envelope(getattr(request.state, "request_id", None)),
    )


IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


# ---------------------------------------------------------------------------
# §2 Provider sessions
# ---------------------------------------------------------------------------


@app.post(f"{API_PREFIX}/provider-sessions", status_code=201, tags=["provider-sessions"])
async def create_provider_session(
    body: ProviderSessionCreate, idempotency_key: IdempotencyKey = None
) -> ProviderSession:
    _validate_provider_network_target(
        body.base_url, allow_private=settings.provider_allow_private_networks
    )
    if body.project_id is not None:
        _require(STORE.projects, body.project_id, "project")
    now = _utcnow()
    session = ProviderSession(
        id=_new_id("sess"),
        label=body.label,
        base_url=body.base_url,
        models=body.models,
        capabilities=body.capabilities,
        project_id=body.project_id,
        created_at=now,
        expires_at=now + timedelta(seconds=body.ttl_seconds),
    )
    STORE.provider_sessions[session.id] = session
    # Lease: токен и рабочие лимиты хранятся отдельно от публичной
    # модели и никогда не возвращаются API.
    STORE.provider_secrets[session.id] = {
        "api_token": body.api_token,
        "timeout_seconds": body.timeout_seconds,
        "max_concurrency": body.max_concurrency,
    }
    return session


@app.post(f"{API_PREFIX}/provider-sessions/{{session_id}}/test", tags=["provider-sessions"])
async def test_provider_session(session_id: str) -> ProviderSessionTestResult:
    """Реальные minimal capability-пробы против endpoint'а сессии.

    Для каждой заявленной capability — один живой round-trip с кредами
    сессии (structured_output: json_schema-вызов; image_input: +1px PNG
    data URL; embeddings: один embed-вызов). ok — проба прошла, fail —
    endpoint/creды не ответили (detail = typed code, без секретов),
    skip — capability не заявлена клиентом либо непробиваема
    (tool_calls).
    """
    session = _require_session(session_id)
    caps = session.capabilities.model_dump()
    gateway = _session_gateway(session)
    results: list[CapabilityProbe] = []
    try:
        for name, declared in caps.items():
            if not declared:
                results.append(
                    CapabilityProbe(
                        capability=name,
                        status="skip",
                        detail="not declared by client",
                    )
                )
                continue
            try:
                if name == "structured_output":
                    await gateway.probe_structured_output()
                elif name == "image_input":
                    await gateway.probe_image_input()
                elif name == "embeddings":
                    await gateway.embed(["probe"])
                else:  # tool_calls — проба не реализована
                    results.append(
                        CapabilityProbe(
                            capability=name,
                            status="skip",
                            detail="no probe implemented",
                        )
                    )
                    continue
            except DeckDNAError as exc:
                results.append(
                    CapabilityProbe(
                        capability=name, status="fail", detail=exc.code
                    )
                )
                continue
            results.append(CapabilityProbe(capability=name, status="ok"))
    finally:
        close = getattr(gateway, "aclose", None)
        if close is not None:
            await close()
    return ProviderSessionTestResult(
        session_id=session.id, results=results, tested_at=_utcnow()
    )


@app.delete(
    f"{API_PREFIX}/provider-sessions/{{session_id}}",
    status_code=204,
    tags=["provider-sessions"],
)
async def delete_provider_session(session_id: str) -> Response:
    _require(STORE.provider_sessions, session_id, "provider session")
    del STORE.provider_sessions[session_id]
    # revoke lease: токен вытесняется вместе с сессией.
    STORE.provider_secrets.pop(session_id, None)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# §3 Projects
# ---------------------------------------------------------------------------


@app.post(f"{API_PREFIX}/projects", status_code=201, tags=["projects"])
async def create_project(body: ProjectCreate, idempotency_key: IdempotencyKey = None) -> Project:
    now = _utcnow()
    project = Project(
        id=_new_id("prj"),
        name=body.name,
        status="draft",
        default_language=body.default_language,
        target_slide_count=body.target_slide_count,
        created_at=now,
        updated_at=now,
    )
    STORE.projects[project.id] = project
    return project


@app.get(f"{API_PREFIX}/projects", tags=["projects"])
async def list_projects(
    cursor: str | None = Query(default=None), limit: int = Query(default=50, ge=1, le=200)
) -> Page[Project]:
    items = sorted(STORE.projects.values(), key=lambda p: p.created_at)
    return _paginate([_project_view(p) for p in items], cursor, limit)


@app.get(f"{API_PREFIX}/projects/{{project_id}}", tags=["projects"])
async def get_project(project_id: str) -> Project:
    return _project_view(_require(STORE.projects, project_id, "project"))


@app.get(f"{API_PREFIX}/projects/{{project_id}}/generations", tags=["generations"])
async def list_project_generations(
    project_id: str,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> Page[GenerationSummary]:
    """Прогоны проекта, новые первыми (D10): экран проектов и «продолжить»
    не зависят от localStorage браузера."""
    _require(STORE.projects, project_id, "project")
    runs = sorted(
        (r for r in STORE.runs.values() if r.detail.project_id == project_id),
        key=lambda r: r.detail.created_at,
        reverse=True,
    )
    return _paginate(
        [
            GenerationSummary(
                id=r.detail.id,
                project_id=project_id,
                state=r.detail.state,
                stage=next((v.stage for v in r.detail.variants if v.stage), None),
                job_id=r.detail.job_id,
                template_id=r.detail.template_id,
                content_pack_id=r.detail.content_pack_id,
                variant_ids=[v.id for v in r.detail.variants],
                parent_run_id=r.detail.parent_run_id,
                created_at=r.detail.created_at,
                finished_at=r.detail.finished_at,
            )
            for r in runs
        ],
        cursor,
        limit,
    )


@app.patch(f"{API_PREFIX}/projects/{{project_id}}", tags=["projects"])
async def patch_project(project_id: str, body: ProjectPatch) -> Project:
    project = _require(STORE.projects, project_id, "project")
    update = body.model_dump(exclude_none=True)
    updated = project.model_copy(update={**update, "updated_at": _utcnow()})
    STORE.projects[project_id] = updated
    return _project_view(updated)


@app.delete(f"{API_PREFIX}/projects/{{project_id}}", status_code=204, tags=["projects"])
async def delete_project(project_id: str) -> Response:
    _require(STORE.projects, project_id, "project")
    _cascade_delete_project(project_id)
    del STORE.projects[project_id]
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# §4 Templates (tool: inspect_template)
# ---------------------------------------------------------------------------

_TEMPLATE_MIME = {
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "potx": "application/vnd.openxmlformats-officedocument.presentationml.template",
}


@app.post(f"{API_PREFIX}/projects/{{project_id}}/templates", status_code=201, tags=["templates"])
async def upload_template(
    project_id: str,
    file: Annotated[UploadFile, File()],
    idempotency_key: IdempotencyKey = None,
) -> TemplateAsset:
    project = _require(STORE.projects, project_id, "project")
    filename = _checked_upload_filename(file.filename or "template.pptx")
    package_type = filename.rsplit(".", 1)[-1].lower()
    if package_type not in _TEMPLATE_MIME:
        raise DeckDNAError(
            "invalid_input",
            f"unsupported template type: {filename}; expected .pptx or .potx",
            http_status=415,
        )
    data = await _read_upload_limited(file, settings.max_upload_mb)
    # Previously any bytes with a .pptx/.potx extension
    # got validation_status="valid" without the package ever being opened
    # -- a corrupt upload only surfaced later, at /analyze. OpcPackage.open
    # already does the limited structural check this needs (zip integrity,
    # required parts, well-formed XML) and raises package_corrupt itself.
    with tempfile.NamedTemporaryFile(suffix=f".{package_type}") as tmp:
        tmp.write(data)
        tmp.flush()
        OpcPackage.open(tmp.name)
    artifact = _store_artifact(
        data,
        type="template.original",
        mime_type=_TEMPLATE_MIME[package_type],
        filename=filename,
        project_id=project_id,
    )
    template = TemplateAsset(
        id=_new_id("tpl"),
        project_id=project_id,
        filename=filename,
        media_type=artifact.mime_type,
        package_type=package_type,
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        artifact_id=artifact.id,
        validation_status="valid",
        created_at=_utcnow(),
    )
    STORE.templates[template.id] = template
    STORE.projects[project_id] = project.model_copy(
        update={"template_id": template.id, "updated_at": _utcnow()}
    )
    return template


@app.post(f"{API_PREFIX}/templates/{{template_id}}/analyze", status_code=202, tags=["templates"])
async def analyze_template(
    template_id: str,
    body: TemplateAnalysisRequest,
    idempotency_key: IdempotencyKey = None,
) -> AnalysisAccepted:
    template = _require(STORE.templates, template_id, "template")
    gateway = _gateway_for(body, template.project_id)
    return await _analyze_template(template_id, body, gateway)


async def _analyze_template(
    template_id: str, body: TemplateAnalysisRequest, gateway: ModelGateway | None
) -> AnalysisAccepted:
    """Shared analysis using the caller's gateway and usage counters."""
    template = _require(STORE.templates, template_id, "template")
    if body.provider_session_id is not None:
        # autopsy сам по себе детерминирован (build_design_dna не тронут);
        # сессия используется ADR-018's content_description-обогащением
        # ниже — валидируем lease (TTL + project scope) сразу.
        _require_session(body.provider_session_id, template.project_id)
    analysis = await _run_template_analysis(template, body, gateway)
    job = _complete_job(
        JobKind.template_analysis,
        template.project_id,
        {"analysis_id": analysis.id, "design_dna_revision": "1"},
    )
    return AnalysisAccepted(job_id=job.id, analysis_id=analysis.id)


async def _run_template_analysis(
    template: TemplateAsset, body: TemplateAnalysisRequest, gateway: ModelGateway | None
) -> TemplateAnalysis:
    """Autopsy + Design DNA (+ ADR-018 описания образцов, если есть модель).

    Общая для ``POST /analyze`` и генерации: живой путь пользователя
    (фронт) ``/analyze`` не вызывает, поэтому генерация запускает анализ
    сама, если DNA шаблона ещё нет — иначе ADR-018 никогда не работает."""
    template_id = template.id
    artifact = _require(STORE.artifacts, template.artifact_id, "template package bytes")
    analysis_id = _new_id("ana")
    try:
        with tempfile.TemporaryDirectory(prefix="deckdna-autopsy-") as tmpdir:
            package_path = Path(tmpdir) / "template.package"
            package_path.write_bytes(artifact.data)
            forensics = autopsy.analyze_template(package_path)
        slide_ids = {i: _new_id("tsl") for i in range(forensics.slides)}
        dna, slide_meta, features = _forensics_to_design_dna(
            forensics, artifact.data, template_id, analysis_id, slide_ids
        )
    except (zipfile.BadZipFile, KeyError, etree.XMLSyntaxError) as exc:
        raise DeckDNAError("package_corrupt", f"cannot parse OOXML package: {exc}") from exc

    # ADR-018/020: cached enrichment, including deferred analysis when
    # the UI first supplies its provider during generation. Honest no-op (dna.exemplars keep
    # content_description=None) when no gateway is available or the call
    # fails; analyze never blocks on this.
    if gateway is not None:
        descriptions = await dna_builder.describe_exemplars(features, gateway)
        if descriptions:
            for exemplar in dna.exemplars:
                item = descriptions.get(exemplar.part)
                if item is not None:
                    exemplar.content_description = item.description
                    exemplar.content_is_entity_specific = item.is_entity_specific
                    exemplar.content_is_template_meta = item.is_template_meta
    inventory = forensics.to_dict()
    inventory["path"] = template.filename
    analysis = TemplateAnalysis(
        id=analysis_id,
        template_id=template_id,
        revision=1,
        status="completed",
        parser_version="autopsy-1",
        config_version=body.config_version,
        design_dna_revision=1,
        preview_artifact_ids=[],
        package_inventory=inventory,
        warnings=[],
        created_at=_utcnow(),
        finished_at=_utcnow(),
    )
    STORE.analyses[analysis.id] = analysis
    STORE.design_dna[template_id] = dna
    STORE.template_slides[template_id] = [
        SlidePreviewMeta(
            id=slide_ids[e.slide_index],
            slide_index=e.slide_index,
            part=e.part,
            role=e.role,
            title=slide_meta.get(e.slide_index, {}).get("title"),
            mutability=slide_meta.get(e.slide_index, {}).get("mutability"),
        )
        for e in dna.exemplars
    ]
    return analysis


@app.get(f"{API_PREFIX}/templates/{{template_id}}", tags=["templates"])
async def get_template(template_id: str) -> TemplateDetail:
    template = _require(STORE.templates, template_id, "template")
    latest = next(
        (a for a in STORE.analyses.values() if a.template_id == template_id),
        None,
    )
    return TemplateDetail(**template.model_dump(), latest_analysis=latest)


@app.get(f"{API_PREFIX}/templates/{{template_id}}/design-dna", tags=["templates"])
async def get_design_dna(template_id: str) -> DesignDNAOut:
    _require(STORE.templates, template_id, "template")
    return _require(STORE.design_dna, template_id, "design DNA (run analyze first)")


@app.get(f"{API_PREFIX}/templates/{{template_id}}/slides", tags=["templates"])
async def list_template_slides(
    template_id: str,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> Page[SlidePreviewMeta]:
    _require(STORE.templates, template_id, "template")
    items = STORE.template_slides.get(template_id, [])
    return Page[SlidePreviewMeta](items=items[:limit])


# ---------------------------------------------------------------------------
# §5 Content packs (tool: ingest_content)
# ---------------------------------------------------------------------------


@app.post(
    f"{API_PREFIX}/projects/{{project_id}}/content-packs",
    status_code=202,
    tags=["content-packs"],
)
async def upload_content_pack(
    project_id: str,
    files: Annotated[list[UploadFile], File()],
    brief: Annotated[str | None, Form(description="Optional JSON-encoded brief")] = None,
    idempotency_key: IdempotencyKey = None,
) -> ContentPackAccepted:
    project = _require(STORE.projects, project_id, "project")
    # Until ingestion can merge provenance from multiple source files, reject
    # the entire request.  Keeping extra files as artifacts made a successful
    # response claim they were included in the pack when they were not.
    if len(files) != 1:
        raise DeckDNAError(
            "invalid_input",
            "content pack upload requires exactly one file; multi-file ingestion is not supported",
            http_status=422,
        )
    parsed_brief: dict[str, Any] = {}
    if brief:
        try:
            parsed_brief = json.loads(brief)
        except json.JSONDecodeError:
            raise DeckDNAError("invalid_input", "brief form field must be valid JSON") from None
    artifacts: list[_Artifact] = []
    for file in files:
        filename = _checked_upload_filename(file.filename or "content.bin")
        # Content-pack upload previously had no size limit at all
        # (unlike upload_template's max_upload_mb check) -- an arbitrarily
        # large file here would be read fully into memory.
        data = await _read_upload_limited(file, settings.max_upload_mb)
        artifacts.append(
            _store_artifact(
                data,
                type="content.original",
                mime_type=file.content_type or "application/octet-stream",
                filename=filename,
                project_id=project_id,
            )
        )

    # Exactly one source is accepted until multi-file provenance is supported.
    chosen = next(
        (
            a
            for a in artifacts
            if Path(a.filename).suffix.lower() in _SUPPORTED_CONTENT_SUFFIXES
        ),
        None,
    )
    if chosen is None:
        raise DeckDNAError(
            "invalid_input",
            "no supported content file: expected one of "
            f"{sorted(_SUPPORTED_CONTENT_SUFFIXES)}",
        )
    with tempfile.TemporaryDirectory(prefix="deckdna-ingest-") as tmpdir:
        src_path = Path(tmpdir) / chosen.filename
        src_path.write_bytes(chosen.data)
        pack = parse_file(src_path, artifact_id=chosen.id)
    updates: dict[str, Any] = {}
    if parsed_brief.get("language"):
        updates["language"] = str(parsed_brief["language"])
    if parsed_brief.get("title_hint"):
        updates["title_hint"] = str(parsed_brief["title_hint"])
    if updates:
        pack = pack.model_copy(update=updates)
    # The parser's ID may be a short content hash or user-controlled JSON.
    # API identity belongs to this upload, not to its bytes or supplied ID.
    pack = pack.model_copy(update={"id": _new_id("pack")})
    STORE.pack_artifacts[pack.id] = [chosen.id]
    graph = build_evidence_graph(pack)
    STORE.content_packs[pack.id] = pack
    STORE.evidence_graphs[pack.id] = graph
    STORE.content_pack_projects.setdefault(pack.id, set()).add(project_id)
    STORE.projects[project_id] = project.model_copy(
        update={"content_pack_id": pack.id, "updated_at": _utcnow()}
    )
    job = _complete_job(
        JobKind.content_ingestion,
        project_id,
        {"content_pack_id": pack.id, "evidence_graph_id": graph.id},
    )
    return ContentPackAccepted(content_pack=pack, job=job)


@app.get(f"{API_PREFIX}/content-packs/{{pack_id}}", tags=["content-packs"])
async def get_content_pack(pack_id: str) -> ContentPack:
    return _require(STORE.content_packs, pack_id, "content pack")


@app.get(f"{API_PREFIX}/content-packs/{{pack_id}}/evidence-graph", tags=["content-packs"])
async def get_evidence_graph(pack_id: str) -> EvidenceGraph:
    _require(STORE.content_packs, pack_id, "content pack")
    return _require(STORE.evidence_graphs, pack_id, "evidence graph")


# ---------------------------------------------------------------------------
# §6 Generations (tools: plan_deck + generate_deck)
# ---------------------------------------------------------------------------


def _contract_issue_to_runtime(issue: AuditIssue) -> _RuntimeAuditIssue:
    """Contract ``AuditIssue`` -> runtime ``audit.issues.AuditIssue``.

    Repair tooling (planner/apply) operates on the runtime shape: bbox as a
    plain dict, evidence as list[dict], severity/status as raw strings.
    """
    return _RuntimeAuditIssue(
        rule_code=issue.rule_code,
        severity=issue.severity.value,
        message=issue.message,
        audit_run_id=issue.audit_run_id,
        id=issue.id,
        deterministic=issue.deterministic,
        deck_revision=issue.deck_revision or 0,
        slide_id=issue.slide_id,
        slide_index=issue.slide_index,
        shape_ids=list(issue.shape_ids or []),
        bbox=issue.bbox.model_dump() if issue.bbox else None,
        measured_value=issue.measured_value,
        threshold=issue.threshold,
        evidence=[e.model_dump() for e in (issue.evidence or [])],
        confidence=issue.confidence if issue.confidence is not None else 1.0,
        status=issue.status.value,
        repairable=issue.repairable,
        proposed_actions=list(issue.proposed_actions or []),
        provenance=issue.provenance.model_dump() if issue.provenance else {},
    )


def _clip_bbox(bbox: IssueBbox | None) -> IssueBbox | None:
    """bbox, обрезанный в [0,1] (Q4); None/неполный — как есть."""
    if bbox is None:
        return None
    x, y, w, h = bbox.x, bbox.y, bbox.w, bbox.h
    if x is None or y is None or w is None or h is None:
        return bbox
    x0 = min(max(x, 0.0), 1.0)
    y0 = min(max(y, 0.0), 1.0)
    x1 = min(max(x + w, 0.0), 1.0)
    y1 = min(max(y + h, 0.0), 1.0)
    return IssueBbox(x=x0, y=y0, w=max(x1 - x0, 0.0), h=max(y1 - y0, 0.0))


def _enrich_issue(issue: AuditIssue) -> AuditIssueOut:
    """Контрактная проблема → ответная форма с fingerprint, fix_preview и
    clipped_bbox (ADR-011). Идемпотентна."""
    out = AuditIssueOut.model_validate(issue.model_dump())
    runtime = _contract_issue_to_runtime(out)
    preview = repair_outcomes.fix_preview(runtime)
    return out.model_copy(
        update={
            "fingerprint": repair_outcomes.fingerprint(runtime),
            "fix_preview": FixPreview(**preview) if preview else None,
            "clipped_bbox": _clip_bbox(out.bbox),
        }
    )


def _contract_issue(raw: dict) -> AuditIssueOut:
    # runtime-аудит (audit/contextual.py) пишет evidence kind "vlm_verdict";
    # в замороженной схеме санкционированное имя для вердикта модели —
    # "model_verdict" (переименование kind в схеме требует ADR). detail с
    # исходным check/confidence/rationale сохраняется нетронутым.
    for item in raw.get("evidence") or ():
        if item.get("kind") == "vlm_verdict":
            item["kind"] = "model_verdict"
    return _enrich_issue(AuditIssue.model_validate(raw))


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"o:{offset}".encode()).decode()


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        prefix, _, value = raw.partition(":")
        offset = int(value)
        if prefix != "o" or offset < 0:
            raise ValueError(raw)
        return offset
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise DeckDNAError("invalid_input", f"invalid cursor: {cursor!r}") from None


def _paginate[T](items: list[T], cursor: str | None, limit: int) -> Page[T]:
    """Opaque-cursor страница (API.md §1): курсор — смещение в списке.

    Список стабилен на время листания (issues меняются только repair'ом,
    который заменяет весь список), поэтому смещения достаточно."""
    offset = _decode_cursor(cursor)
    end = offset + limit
    return Page[T](
        items=items[offset:end],
        next_cursor=_encode_cursor(end) if end < len(items) else None,
    )


_VARIANTS_CONFIG = _REPO_ROOT / "configs" / "variants.default.yaml"


def _variant_profile(strategy: str) -> tuple[VariantAxes | None, str | None]:
    """Оси различий и «для кого» варианта — из configs/variants.default.yaml.

    text_density ← weights.text_density, layout_diversity ← novelty,
    visualization ← chart_preference. ``custom`` ведёт себя как balanced."""
    try:
        data = yaml.safe_load(_VARIANTS_CONFIG.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None, None
    wanted = "balanced" if strategy == "custom" else strategy
    for entry in data.get("variants") or []:
        if entry.get("strategy") != wanted:
            continue
        w = entry.get("weights") or {}
        try:
            axes = VariantAxes(
                text_density=float(w["text_density"]),
                layout_diversity=float(w["novelty"]),
                visualization=float(w["chart_preference"]),
            )
        except (KeyError, TypeError, ValueError):
            return None, entry.get("description")
        return axes, entry.get("description")
    return None, None


def _project_view(project: Project) -> Project:
    """Проект с данными последнего прогона (D10) — считается из STORE.runs."""
    runs = [r for r in STORE.runs.values() if r.detail.project_id == project.id]
    if not runs:
        return project
    latest = max(runs, key=lambda r: r.detail.created_at)
    stage = next((v.stage for v in latest.detail.variants if v.stage), None)
    return project.model_copy(
        update={
            "latest_run_id": latest.detail.id,
            "latest_run_state": latest.detail.state,
            "latest_run_stage": stage,
        }
    )


def _issue_summaries(issues: Sequence[AuditIssue]) -> tuple[IssueSummary, dict[str, int]]:
    by_severity = IssueSummary()
    by_type: dict[str, int] = {}
    for issue in issues:
        count = getattr(by_severity, issue.severity.value, 0)
        setattr(by_severity, issue.severity.value, count + 1)
        by_type[issue.rule_code] = by_type.get(issue.rule_code, 0) + 1
    return by_severity, by_type


# ---------------------------------------------------------------------------
# Процесс-локальный асинхронный lifecycle генерации.
#
# До этой версии POST /generations возвращал 202 только ПОСЛЕ полного
# синхронного прогона в threadpool — GET /jobs никогда не видел
# queued/running, а /cancel мог пометить завершённую работу canceled,
# ничего не остановив. Теперь: accept-фаза валидирует входы, создаёт
# run/job/variant_ids в состоянии queued и отвечает 202 немедленно;
# выполнение идёт в _GEN_POOL с честными переходами
# queued→running→completed/failed(/canceled).
#
# Честные границы (процесс-локально, без Redis/PG):
# - backlog живёт в памяти процесса и теряется при рестарте — как и весь
#   STORE; persistence-очередь (Redis/PG + внешний worker, jobs/worker.py
#   пока stub) — отдельная задача;
# - отмена кооперативная: job.state == canceled — сигнал executor'у не
#   начинать следующий вариант; убить поток внутри generate() нельзя —
#   выполняющийся вариант доводится до конца, его результат фиксируется;
# - canceled/failed/completed терминальны и не перезаписываются — поздний
#   переход не затирает отмену (и наоборот отмена терминального — 409).
# ---------------------------------------------------------------------------

_TERMINAL_JOB_STATES = frozenset(
    {JobState.completed, JobState.failed, JobState.canceled}
)
_JOB_LOCK = threading.RLock()
_TEMPLATE_DNA_LOCK = threading.RLock()
# Bounded: число воркеров настраивается через DECKDNA_GEN_WORKERS (по умолчанию 4).
_GEN_POOL = ThreadPoolExecutor(
    max_workers=max(1, int(os.environ.get("DECKDNA_GEN_WORKERS", 4))),
    thread_name_prefix="deckdna-gen",
)


def _patch_variant(run: _InternalRun, variant_id: str, **fields: Any) -> None:
    with _JOB_LOCK:
        variants = [
            v.model_copy(update=fields) if v.id == variant_id else v
            for v in run.detail.variants
        ]
        run.detail = run.detail.model_copy(update={"variants": variants})


def _cascade_delete_project(project_id: str) -> None:
    """DELETE /projects/{id} used to remove only the
    project record -- templates, content packs, runs/variants, audits,
    issues, plans, exports, provider sessions and their artifact bytes
    stayed in STORE, reachable forever through the global (non-project-
    scoped) endpoints by their old IDs.

    Cancels this project's still-active generation jobs first (same
    cooperative mechanism as POST /generations/{id}/cancel — a variant
    already mid-flight finishes and is honestly recorded, but no further
    variant starts), then removes every record this project owns. A
    trailing artifact from a variant that was already running at the
    moment of deletion can still land in STORE.artifacts after this
    returns; that's the same accepted in-memory-store limitation as
    everywhere else cooperative cancellation is used in this file, not a
    new gap this function introduces.
    """
    with _JOB_LOCK:
        for job in list(STORE.jobs.values()):
            if job.project_id == project_id and job.state not in _TERMINAL_JOB_STATES:
                STORE.jobs[job.id] = job.model_copy(
                    update={"state": JobState.canceled, "finished_at": _utcnow()}
                )

    run_ids = [
        r.detail.id for r in STORE.runs.values() if r.detail.project_id == project_id
    ]
    for run_id in run_ids:
        run = STORE.runs.pop(run_id, None)
        if run is None:
            continue
        for variant in run.detail.variants:
            STORE.slides.pop(variant.id, None)
            for audit_id in [
                a.id for a in STORE.audits.values() if a.variant_id == variant.id
            ]:
                STORE.audits.pop(audit_id, None)
                STORE.issues.pop(audit_id, None)
            for export_id in [
                e.id for e in STORE.exports.values() if e.variant_id == variant.id
            ]:
                STORE.exports.pop(export_id, None)
        # Job entries stay (canceled, above) rather than get popped: the
        # background thread for an already-running variant still holds a
        # reference to this job/run and, once unblocked, writes back into
        # STORE.jobs[job_id] on completion regardless -- popping the key
        # here would just have that write resurrect it under the same ID
        # a moment later. Leaving the canceled entry in place means that
        # eventual write lands on a key that was never actually removed.

    for template_id in [
        t.id for t in STORE.templates.values() if t.project_id == project_id
    ]:
        STORE.templates.pop(template_id, None)
        STORE.design_dna.pop(template_id, None)
        STORE.template_slides.pop(template_id, None)
        for analysis_id in [
            a.id for a in STORE.analyses.values() if a.template_id == template_id
        ]:
            STORE.analyses.pop(analysis_id, None)

    # Keep the ownership-map cleanup safe for any legacy shared entries.
    for pack_id, owners in list(STORE.content_pack_projects.items()):
        if project_id not in owners:
            continue
        owners.discard(project_id)
        if owners:
            continue
        STORE.content_pack_projects.pop(pack_id, None)
        STORE.content_packs.pop(pack_id, None)
        STORE.evidence_graphs.pop(pack_id, None)
        STORE.pack_artifacts.pop(pack_id, None)

    for plan_id in [
        p.plan_id for p in STORE.plans.values() if p.project_id == project_id
    ]:
        STORE.plans.pop(plan_id, None)

    for session_id in [
        s.id for s in STORE.provider_sessions.values() if s.project_id == project_id
    ]:
        STORE.provider_sessions.pop(session_id, None)
        STORE.provider_secrets.pop(session_id, None)

    # Belt-and-suspenders sweep: every artifact byte-blob is tagged with
    # its owning project_id at creation (_store_artifact), independent of
    # which of the above collections happens to reference it -- catches
    # anything the per-collection cleanup above missed.
    for artifact_id in [
        a.id for a in STORE.artifacts.values() if a.project_id == project_id
    ]:
        STORE.artifacts.pop(artifact_id, None)


# ---------------------------------------------------------------------------
# Артефакты ревизии: PDF, паспорт, HTML, превью (B2, D5, D6).
#
# Ревизия 1 получает PDF и паспорт от конвейера. Ревизия после repair —
# нет: repair меняет только PPTX. Всё остальное строится лениво, по
# запросу, из ТЕКУЩЕГО PPTX варианта и кешируется в variant_artifacts до
# следующего repair (он кеш сбрасывает). Блокирующая работа (soffice,
# растеризация) идёт в потоке — вызывающие обязаны оборачивать в
# asyncio.to_thread.
# ---------------------------------------------------------------------------

_VARIANT_LOCKS: dict[str, threading.RLock] = {}


def _variant_lock(variant_id: str) -> threading.RLock:
    with _JOB_LOCK:
        return _VARIANT_LOCKS.setdefault(variant_id, threading.RLock())


def _current_deck(run: _InternalRun, variant_id: str) -> _Artifact:
    _, variant = _variant_or_404(variant_id)
    return _require(STORE.artifacts, variant.deck_artifact_id or "", "variant deck bytes")


def _audit_id_of(variant_id: str) -> str | None:
    return next((a.id for a in STORE.audits.values() if a.variant_id == variant_id), None)


def _ensure_pdf(run: _InternalRun, variant_id: str) -> _Artifact:
    with _variant_lock(variant_id):
        arts = run.variant_artifacts.setdefault(variant_id, {})
        if "pdf" in arts:
            return STORE.artifacts[arts["pdf"]]
        deck = _current_deck(run, variant_id)
        revision = run.deck_revision.get(variant_id, 1)
        with tempfile.TemporaryDirectory(prefix="deckdna-lazy-pdf-") as tmp:
            src = Path(tmp) / deck.filename
            src.write_bytes(deck.data)
            pdf = render_pdf(src, Path(tmp) / "deck.pdf")
            art = _store_artifact(
                pdf.read_bytes(),
                type="export.pdf",
                mime_type="application/pdf",
                filename=f"{run.variant_strategy.get(variant_id, 'deck')}_rev{revision}.pdf",
                project_id=run.detail.project_id,
            )
        if run.deck_revision.get(variant_id, 1) == revision:
            arts["pdf"] = art.id
        return art


def _pdf_page_pngs(run: _InternalRun, variant_id: str) -> list[bytes]:
    pdf = _ensure_pdf(run, variant_id)
    with tempfile.TemporaryDirectory(prefix="deckdna-lazy-png-") as tmp:
        path = Path(tmp) / "deck.pdf"
        path.write_bytes(pdf.data)
        return _previews.pdf_page_pngs(path)


def _ensure_previews(run: _InternalRun, variant_id: str) -> None:
    """PNG-превью слайдов и монтаж текущей ревизии (D6)."""
    with _variant_lock(variant_id):
        revision = run.deck_revision.get(variant_id, 1)
        slides = STORE.slides.get(variant_id, [])
        _, variant = _variant_or_404(variant_id)
        if (
            slides
            and all(sl.preview_artifact_id and sl.revision == revision for sl in slides)
            and variant.montage_artifact_id
        ):
            return
        pngs = _pdf_page_pngs(run, variant_id)
        project_id = run.detail.project_id
        strategy = run.variant_strategy.get(variant_id, "deck")
        updated: list[SlideInfo] = []
        for k, slide in enumerate(slides):
            if k < len(pngs):
                art = _store_artifact(
                    pngs[k],
                    type="slide.preview.png",
                    mime_type="image/png",
                    filename=f"{strategy}_rev{revision}_slide{k + 1}.png",
                    project_id=project_id,
                )
                slide = slide.model_copy(
                    update={"preview_artifact_id": art.id, "revision": revision}
                )
            updated.append(slide)
        STORE.slides[variant_id] = updated
        montage = _store_artifact(
            _previews.montage_png(pngs),
            type="montage.png",
            mime_type="image/png",
            filename=f"{strategy}_rev{revision}_montage.png",
            project_id=project_id,
        )
        _patch_variant(run, variant_id, montage_artifact_id=montage.id)


def _ensure_passport(run: _InternalRun, variant_id: str) -> _Artifact:
    """Паспорт качества ТЕКУЩЕЙ ревизии (B2): пересобирается из её PPTX и
    её актуальных проблем, а не отдаёт паспорт ревизии 1."""
    with _variant_lock(variant_id):
        arts = run.variant_artifacts.setdefault(variant_id, {})
        if "quality_passport" in arts:
            return STORE.artifacts[arts["quality_passport"]]
        info = run.variant_reports.get(variant_id)
        audit_id = _audit_id_of(variant_id)
        if info is None or audit_id is None:
            raise DeckDNAError(
                "not_implemented",
                "quality passport cannot be rebuilt: no generation record for this variant",
            )
        deck = _current_deck(run, variant_id)
        template = STORE.artifacts[STORE.templates[run.detail.template_id].artifact_id]
        revision = run.deck_revision.get(variant_id, 1)
        with tempfile.TemporaryDirectory(prefix="deckdna-lazy-passport-") as tmp:
            deck_path = Path(tmp) / deck.filename
            deck_path.write_bytes(deck.data)
            tpl_path = Path(tmp) / template.filename
            tpl_path.write_bytes(template.data)
            compose_report = {**info["compose_report"], "template": str(tpl_path)}
            passport = assemble_quality_passport(
                deck_path,
                compose_report,
                [_contract_issue_to_runtime(i) for i in STORE.issues.get(audit_id, [])],
                content_pack_id=info["content_pack_id"],
                brief=info["brief"],
                variant_id=variant_id,
                duration_seconds=info["duration_seconds"],
                prompt_versions=info["prompt_versions"],
                model_profiles=[ModelProfile(**m) for m in info["model_profiles"]],
                stage_timings=info["stage_timings"],
                usage=info["usage"],
                source_text=info["source_text"],
                auto_fixes=info["auto_fixes"],
                repair_fixes=info.get("repair_fixes") or None,
            )
        art = _store_artifact(
            (json.dumps(to_schema_dict(passport), ensure_ascii=False, indent=2) + "\n").encode(),
            type="quality.passport.json",
            mime_type="application/json",
            filename=(
                f"{run.variant_strategy.get(variant_id, 'deck')}"
                f"_rev{revision}_quality_passport.json"
            ),
            project_id=run.detail.project_id,
        )
        if run.deck_revision.get(variant_id, 1) == revision:
            arts["quality_passport"] = art.id
        return art


def _ensure_html(run: _InternalRun, variant_id: str) -> _Artifact:
    """HTML-просмотрщик (zip: index.html + slide-N.png) текущей ревизии (D5)."""
    with _variant_lock(variant_id):
        arts = run.variant_artifacts.setdefault(variant_id, {})
        if "html" in arts:
            return STORE.artifacts[arts["html"]]
        deck = _current_deck(run, variant_id)
        revision = run.deck_revision.get(variant_id, 1)
        strategy = run.variant_strategy.get(variant_id, "deck")
        pngs = _pdf_page_pngs(run, variant_id)
        with tempfile.TemporaryDirectory(prefix="deckdna-lazy-html-") as tmp:
            deck_path = Path(tmp) / deck.filename
            deck_path.write_bytes(deck.data)
            data = _previews.html_bundle_zip(
                deck_path,
                pngs,
                title=f"DeckDNA — {strategy}, ревизия {revision}",
                slide_titles=[s.title for s in STORE.slides.get(variant_id, [])],
            )
        art = _store_artifact(
            data,
            type="export.html",
            mime_type="application/zip",
            filename=f"{strategy}_rev{revision}.html.zip",
            project_id=run.detail.project_id,
        )
        if run.deck_revision.get(variant_id, 1) == revision:
            arts["html"] = art.id
        return art


def _ensure_artifact(run: _InternalRun, variant_id: str, fmt: str) -> _Artifact:
    """Артефакт формата *fmt* для ТЕКУЩЕЙ ревизии варианта (строит лениво)."""
    if fmt == "pptx":
        return _current_deck(run, variant_id)
    if fmt == "pdf":
        return _ensure_pdf(run, variant_id)
    if fmt == "quality_passport":
        return _ensure_passport(run, variant_id)
    if fmt == "html":
        return _ensure_html(run, variant_id)
    raise DeckDNAError("not_implemented", f"export format {fmt} is not supported")


def _resolve_supplied_plans(
    body: GenerationCreate, project_id: str
) -> tuple[dict[str, DeckPlan], Literal["generated", "plan_set", "user"]]:
    """Готовые планы, по которым собирать варианты (D7): strategy → план.

    ``deck_plan_id`` — набор из POST /projects/{id}/plans (по стратегии на
    вариант); ``deck_plan`` — один план (после правки пользователем) на
    все варианты. Без обоих — планирует сам конвейер."""
    if body.deck_plan_id is not None and body.deck_plan is not None:
        raise DeckDNAError(
            "invalid_input", "pass either deck_plan_id or deck_plan, not both"
        )
    if body.deck_plan_id is not None:
        plan_set = _require(STORE.plans, body.deck_plan_id, "plan set")
        if plan_set.project_id != project_id:
            raise DeckDNAError(
                "invalid_input", f"plan set {plan_set.plan_id} belongs to another project"
            )
        if plan_set.content_pack_id != body.content_pack_id:
            raise DeckDNAError(
                "invalid_input",
                f"plan set {plan_set.plan_id} was made for content pack "
                f"{plan_set.content_pack_id}, not {body.content_pack_id}",
            )
        by_strategy = {e.strategy: e.deck_plan for e in plan_set.plans}
        missing = [v.strategy for v in body.variants if v.strategy not in by_strategy]
        if missing:
            raise DeckDNAError(
                "invalid_input",
                f"plan set {plan_set.plan_id} has no plan for strategies: {missing}",
            )
        return {v.strategy: by_strategy[v.strategy] for v in body.variants}, "plan_set"
    if body.deck_plan is not None:
        if not body.deck_plan.slides:
            raise DeckDNAError("invalid_input", "deck_plan has no slides")
        return {v.strategy: body.deck_plan for v in body.variants}, "user"
    return {}, "generated"


def _create_generation(
    project: Project, body: GenerationCreate, parent_run_id: str | None
) -> _InternalRun:
    """Accept-фаза: валидация входов (та же, что падала синхронно раньше) +
    run/job/variant_ids в queued. Фактическое выполнение — _execute_generation."""
    template = STORE.templates[body.template_id]
    _require(STORE.artifacts, template.artifact_id, "template package bytes")
    source_ids = STORE.pack_artifacts.get(body.content_pack_id, [])
    if not source_ids:
        raise DeckDNAError(
            "invalid_input",
            f"content pack {body.content_pack_id} has no uploaded source file",
        )
    _require(STORE.artifacts, source_ids[0], "content source")

    run_id = _new_id("run")
    job_id = _new_id("job")
    now = _utcnow()
    # variant_ids аллоцируются при приёме — GenerationAccepted их уже
    # несёт, GET /generations сразу видит варианты в queued.
    variants = []
    for v in body.variants:
        axes, rationale = _variant_profile(v.strategy)
        variants.append(
            VariantSummary(
                id=_new_id("var"),
                run_id=run_id,
                strategy=v.strategy,
                status=JobState.queued,
                axes=axes,
                rationale=rationale,
            )
        )
    job = Job(
        id=job_id,
        kind=JobKind.generation,
        state=JobState.queued,
        progress=0.0,
        project_id=project.id,
        created_at=now,
    )
    run = _InternalRun(
        detail=GenerationDetail(
            id=run_id,
            project_id=project.id,
            state=JobState.queued,
            job_id=job_id,
            events_url=f"{API_PREFIX}/jobs/{job_id}/events",
            template_id=body.template_id,
            content_pack_id=body.content_pack_id,
            variants=variants,
            parent_run_id=parent_run_id,
            created_at=now,
        ),
        variant_strategy={v.id: v.strategy for v in variants},
        deck_revision={v.id: 1 for v in variants},
        request=body,
    )
    run.supplied_plans, run.plan_source = _resolve_supplied_plans(body, project.id)
    STORE.jobs[job_id] = job
    STORE.runs[run_id] = run
    return run


def _finish_generation(
    run: _InternalRun,
    *,
    error: JobError | None,
    plans: dict[Strategy, DeckPlan],
) -> None:
    """Финальный атомарный переход job+run (под _JOB_LOCK).

    canceled — терминален и не перезаписывается: отмена во время выполнения
    оставляет завершённые варианты completed, невыполненные помечаются
    canceled."""
    job_id = run.detail.job_id
    now = _utcnow()
    with _JOB_LOCK:
        job = STORE.jobs[job_id]
        detail = run.detail
        if job.state == JobState.canceled:
            variants = [
                v.model_copy(update={"status": JobState.canceled})
                if v.status in {JobState.queued, JobState.running}
                else v
                for v in detail.variants
            ]
            run.detail = detail.model_copy(
                update={"variants": variants, "finished_at": job.finished_at or now}
            )
            return
        if error is not None:
            job = job.model_copy(
                update={
                    "state": JobState.failed,
                    "error": error,
                    "finished_at": now,
                }
            )
            detail_update = {
                "state": JobState.failed,
                "finished_at": now,
                # незавершённые варианты разделяют судьбу прогона — висячий
                # running/queued после failed был бы ложью о состоянии
                "variants": [
                    v.model_copy(
                        update={
                            "status": JobState.failed,
                            "stage": None,
                            "finished_at": now,
                        }
                    )
                    if v.status in {JobState.queued, JobState.running}
                    else v
                    for v in detail.variants
                ],
            }
        else:
            request = cast(GenerationCreate, run.request)  # кладёт _create_generation
            recorded_plan = plans[Strategy(request.variants[0].strategy)]
            job = job.model_copy(
                update={
                    "state": JobState.completed,
                    "progress": 1.0,
                    "finished_at": now,
                    "result_ids": {"deck_plan_id": recorded_plan.id},
                }
            )
            detail_update = {
                "state": JobState.completed,
                "deck_plan_id": recorded_plan.id,
                "deck_plan": recorded_plan,
                "finished_at": now,
            }
        STORE.jobs[job_id] = job
        run.detail = detail.model_copy(update=detail_update)


@contextlib.asynccontextmanager
async def _maybe_gateway_session(gateway: Any) -> AsyncIterator[None]:
    """``async with _maybe_gateway_session(gateway):`` — opt-in HTTP-client
    reuse (Фаза 3) когда *gateway* поддерживает ``session()`` (duck-typed,
    как ``aclose`` — не часть frozen ``ModelGateway``, см.
    ``providers/openai_compat.py::session``), no-op иначе (``None``,
    MockProvider и т.п.). Централизует одну и ту же проверку, повторённую
    в ``_plan_all_strategies``/``_repair_locked``/``create_plans``."""
    session = getattr(gateway, "session", None)
    if session is None:
        yield
        return
    async with session():
        yield


def _plan_all_strategies(
    pack: Any, body: GenerationCreate, gateway: ModelGateway | None, run: _InternalRun
) -> dict[Strategy, DeckPlan]:
    """Планы всех distinct-стратегий этого прогона, каждая ровно один раз.

    Фаза 2: раньше варианты планировались строго последовательно внутри
    ``_execute_generation`` — на живом медленном провайдере (VK Inference
    ~27-30B) каждый ``plan_deck_llm`` round-trip держится в районе
    30-90с, и три варианта, планируемые один за другим, были главным
    источником сквозной задержки. Планы независимых стратегий не зависят
    друг от друга (общий ``pack``/evidence graph, разные ``DeckPlan``),
    поэтому все LLM-планирования уходят одним ``asyncio.gather`` внутри
    одного ``asyncio.run`` — не по одному вызову на вариант.

    Помечает ВСЕ варианты прогона ``running``/``stage="plan"`` перед
    стартом — честно отражает, что они уже не в очереди, даже если
    некоторые получат готовый (``supplied_plans``) или детерминированный
    план мгновенно. compose/audit/render ниже (в ``_execute_generation``)
    остаются последовательными по вариантам — параллельный рендер через
    soffice не был предметом этой фазы (профиль ``UserInstallation`` уже
    изолирован на инвокацию, см. ``pptx/exporting/render.py::_convert``,
    но конкурентная нагрузка на него здесь не проверялась).
    """
    now = _utcnow()
    for variant in run.detail.variants:
        _patch_variant(
            run, variant.id, status=JobState.running, started_at=now, stage="plan"
        )

    strategies = list(dict.fromkeys(Strategy(v.strategy) for v in body.variants))
    plans: dict[Strategy, DeckPlan] = {}
    to_plan_llm: list[Strategy] = []
    for strategy in strategies:
        if strategy.value in run.supplied_plans:
            # готовый план (D7): не планируем заново — колода собирается
            # ровно по нему.
            plans[strategy] = run.supplied_plans[strategy.value]
        elif gateway is None:
            plans[strategy] = plan_deck(pack, body.brief, strategy=strategy)
        else:
            to_plan_llm.append(strategy)

    if to_plan_llm:

        async def _plan_and_style(strategy: Strategy) -> DeckPlan:
            planning_version = pipeline.planning_pipeline_version()
            planner_fn = (
                plan_deck_llm_v2 if planning_version in ("v2", "v3") else plan_deck_llm
            )
            plan = await planner_fn(pack, body.brief, gateway, strategy=strategy)
            # v3 (ADR-016 Stage 3): compose-время layout_fit уже пишет и
            # подгоняет финальный текст — доп. стилизация была бы избыточна
            # (см. тот же guard в generation/pipeline.py::generate()).
            if (
                planning_version != "v3"
                and content_style.content_styling_enabled()
                and content_style.can_rewrite(gateway)
            ):
                plan = await content_style.style_deck_content(
                    plan, gateway, language=plan.language or "ru"
                )
            return plan

        async def _gather_llm_plans() -> list[DeckPlan]:
            # Фаза 3: opt-in session() (см. _maybe_gateway_session) даёт
            # всем LLM-планам этого прогона один httpx.AsyncClient вместо
            # TLS-хендшейка на каждый POST. Клиент закрывается здесь, на
            # выходе из ЭТОГО asyncio.run-скоупа, а не только в самом
            # конце _execute_generation — следующий asyncio.run
            # (compose/audit следующего варианта) это уже новый loop,
            # чужой клиент туда не годится. content_style запускается
            # СРАЗУ после plan_deck_llm той же стратегии (не отдельным
            # gather'ом) — сохраняет тот же session()-скоуп и не требует
            # второго прохода по to_plan_llm.
            try:
                async with _maybe_gateway_session(gateway):
                    return await asyncio.gather(
                        *(_plan_and_style(strategy) for strategy in to_plan_llm)
                    )
            finally:
                close = getattr(gateway, "aclose", None)
                if close is not None:
                    await close()

        for strategy, plan in zip(to_plan_llm, asyncio.run(_gather_llm_plans()), strict=True):
            plans[strategy] = plan
    return plans


def _execute_generation(run_id: str, gateway: ModelGateway | None) -> None:
    """Фоновое выполнение generation job (воркер _GEN_POOL).

    Переходы queued→running→completed/failed атомарны под _JOB_LOCK;
    canceled терминален: отмена до старта пропускает работу, во время —
    останавливает запуск следующих вариантов (кооперативно)."""
    run = STORE.runs[run_id]
    detail = run.detail
    body = cast(GenerationCreate, run.request)  # кладёт _create_generation
    job_id = detail.job_id
    with _JOB_LOCK:
        job = STORE.jobs[job_id]
        if job.state == JobState.canceled:
            return  # отменён ещё в очереди — работа не стартует
        STORE.jobs[job_id] = job.model_copy(
            update={"state": JobState.running, "started_at": _utcnow()}
        )
        run.detail = detail.model_copy(update={"state": JobState.running})

    plans: dict[Strategy, DeckPlan] = {}
    job_error: JobError | None = None
    preparation_usage_before = measures.usage_snapshot(gateway)
    try:
        template = STORE.templates[body.template_id]
        tpl_artifact = STORE.artifacts[template.artifact_id]
        src_artifact = STORE.artifacts[STORE.pack_artifacts[body.content_pack_id][0]]
        # The live UI analyzes on upload without a provider session, so
        # deterministic DNA exists but semantic exemplar descriptions do
        # not. Enrich when generation has a gateway; also handle direct
        # API callers that skipped analysis altogether.
        with _TEMPLATE_DNA_LOCK:
            dna = STORE.design_dna.get(body.template_id)
            needs_enrichment = gateway is not None and (
                dna is None or not any(e.content_description for e in dna.exemplars)
            )
            if dna is None or needs_enrichment:
                asyncio.run(
                    _analyze_template(
                        body.template_id,
                        TemplateAnalysisRequest(
                            provider_session_id=body.provider_session_id,
                            use_llm=body.use_llm if gateway is not None else False,
                        ),
                        gateway,
                    )
                )
                dna = STORE.design_dna[body.template_id]
        with tempfile.TemporaryDirectory(prefix="deckdna-gen-") as tmpdir:
            tmp = Path(tmpdir)
            tpl_path = tmp / tpl_artifact.filename
            tpl_path.write_bytes(tpl_artifact.data)
            src_path = tmp / src_artifact.filename
            src_path.write_bytes(src_artifact.data)
            # Переиспользуем сохранённый при upload ContentPack, а не
            # парсим временную копию заново: у сохранённого пакета верный
            # artifact_id на каждом unit (parse_file по умолчанию
            # подставил бы имя временного файла) и уже применены brief
            # overrides (language/title_hint).
            pack = STORE.content_packs[body.content_pack_id]

            # Фаза 2: варианты раньше планировались
            # строго последовательно — на живом медленном провайдере каждый
            # LLM round-trip держится в районе 30-90с, и три варианта
            # планировались одно за другим. Независимые strategy-планы не
            # зависят друг от друга (общий pack/evidence graph, разные
            # DeckPlan), поэтому все нужные LLM-планирования уходят одним
            # asyncio.gather внутри одного asyncio.run — не по одному на
            # вариант. Кооперативная отмена (ADR-010) уже проверена выше
            # (job.state == canceled → return до старта); отмена ПОСЛЕ этой
            # точки не прерывает уже запущенный concurrent-план-фетч — как и
            # раньше не прерывала уже запущенный вариант, план доводится до
            # конца и лишь следующий вариант не стартует.
            #
            # Осознанное сужение этой итерации: compose/audit/render ниже
            # остаются последовательными по вариантам — параллельный soffice
            # рендер безопасен с per-invocation UserInstallation профилем
            # (см. pptx/exporting/render.py::_convert), но не был предметом
            # этой фазы и не проверялся под конкурентной нагрузкой здесь.
            plans.update(_plan_all_strategies(pack, body, gateway, run))

            total = len(run.detail.variants)
            for index, variant_req in enumerate(body.variants):
                # Кооперативная отмена: следующий вариант не стартует.
                if STORE.jobs[job_id].state == JobState.canceled:
                    break
                variant_id = run.detail.variants[index].id
                strategy = Strategy(variant_req.strategy)
                _patch_variant(
                    run,
                    variant_id,
                    status=JobState.running,
                    started_at=run.detail.variants[index].started_at or _utcnow(),
                    stage="content",
                )
                plan = plans[strategy]
                out_dir = tmp / variant_req.strategy
                report = pipeline.generate(
                    tpl_path,
                    src_path,
                    body.brief,
                    out_dir,
                    gateway=gateway,
                    strategy=strategy,
                    deck_plan=plan,
                    on_stage=lambda stage, _v=variant_id: _patch_variant(
                        run, _v, stage=stage
                    ),
                    # ADR-018: cached template-analysis-time exemplar
                    # descriptions, when this template has been through
                    # /analyze — honest None otherwise (falls back to
                    # layout_fit's own text_preview heuristic).
                    design_dna=dna,
                    content_pack=pack,
                )

                # Preparation (DNA enrichment + concurrent plans) is shared
                # across variants. Charge it once to the first variant so
                # summing passports equals all real generation calls.
                if index == 0:
                    usage = measures.usage_delta(
                        preparation_usage_before, measures.usage_snapshot(gateway)
                    )
                    report["usage"] = usage
                    report["quality_passport"]["metrics"]["usage"] = usage
                    (out_dir / "quality-passport.json").write_text(
                        json.dumps(report["quality_passport"], ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )

                deck_art = _store_artifact(
                    (out_dir / "deck.pptx").read_bytes(),
                    type="deck.pptx.revision",
                    mime_type=_PPTX_MIME,
                    filename=f"deck_{variant_req.strategy}.pptx",
                    project_id=detail.project_id,
                )
                pdf_art = _store_artifact(
                    (out_dir / "deck.pdf").read_bytes(),
                    type="export.pdf",
                    mime_type="application/pdf",
                    filename=f"{variant_req.strategy}.pdf",
                    project_id=detail.project_id,
                )
                passport_art = _store_artifact(
                    (out_dir / "quality-passport.json").read_bytes(),
                    type="quality.passport.json",
                    mime_type="application/json",
                    filename=f"{variant_req.strategy}_quality_passport.json",
                    project_id=detail.project_id,
                )
                run.variant_artifacts[variant_id] = {
                    "pptx": deck_art.id,
                    "pdf": pdf_art.id,
                    "quality_passport": passport_art.id,
                }

                issues = [_contract_issue(i) for i in report["audit_issues"]]
                by_severity, by_type = _issue_summaries(issues)
                passport_dict = report["quality_passport"]
                run.variant_reports[variant_id] = {
                    "compose_report": report["compose_report"],
                    "content_pack_id": report["content_pack_id"],
                    "brief": body.brief,
                    "duration_seconds": report["duration_seconds"],
                    "prompt_versions": passport_dict["provenance"]["prompt_versions"],
                    "model_profiles": passport_dict["provenance"]["model_profiles"],
                    "stage_timings": report["stage_timings"],
                    "usage": report["usage"],
                    "source_text": measures.pack_text(pack),
                    "auto_fixes": report["auto_fixes"],
                    "repair_fixes": {},
                }
                # keep the pipeline's own audit_run_id so issues trace back
                audit_id = issues[0].audit_run_id if issues else _new_id("audit")
                STORE.audits[audit_id] = AuditRun(
                    id=audit_id,
                    variant_id=variant_id,
                    deck_revision=1,
                    status="completed",
                    deterministic_status="completed",
                    contextual_status=(
                        "completed"
                        if report["contextual_audit"].get("complete")
                        else "failed"
                        if report["contextual_audit"]["ran"]
                        else "skipped"
                    ),
                    issue_count=len(issues),
                    summary_by_severity=by_severity,
                    summary_by_type=by_type,
                    created_at=_utcnow(),
                    finished_at=_utcnow(),
                )
                STORE.issues[audit_id] = issues

                export = ExportRecord(
                    id=_new_id("exp"),
                    variant_id=variant_id,
                    deck_revision=1,
                    job_id=job_id,
                    artifacts=[
                        ExportArtifact(
                            format=fmt,
                            artifact_id=art_id,
                            sha256=STORE.artifacts[art_id].sha256,
                            size_bytes=STORE.artifacts[art_id].size_bytes,
                            mime_type=STORE.artifacts[art_id].mime_type,
                            download_url=f"{API_PREFIX}/artifacts/{art_id}/download",
                        )
                        for fmt, art_id in (
                            ("pptx", deck_art.id),
                            ("pdf", pdf_art.id),
                            ("quality_passport", passport_art.id),
                        )
                    ],
                    created_at=_utcnow(),
                )
                STORE.exports[export.id] = export

                qp_metrics = report["quality_passport"]["metrics"]
                _patch_variant(
                    run,
                    variant_id,
                    status=JobState.completed,
                    stage=None,
                    finished_at=_utcnow(),
                    deck_artifact_id=deck_art.id,
                    planner=(
                        f"user_edited:{report['planner']}"
                        if run.plan_source == "user"
                        else report["planner"]
                    ),
                    deck_plan_id=plan.id,
                    contextual_issues=(
                        report["contextual_audit"]["issues"]
                        if report["contextual_audit"]["ran"]
                        else None
                    ),
                    metrics=VariantMetrics(
                        validity=(
                            1.0 if qp_metrics["validity"].get("opens_cleanly") else 0.0
                        ),
                        editability_pei=qp_metrics["editability"].get("pei_level"),
                        issues_total=len(issues),
                        style_fidelity=measures.style_fidelity_score(
                            qp_metrics.get("style_fidelity")
                        ),
                        auto_fixed=sum(report["auto_fixes"].values()),
                    ),
                    audit_status="completed",
                    export_ids=[export.id],
                )
                STORE.slides[variant_id] = [
                    SlideInfo(
                        id=_new_id("sld"),
                        variant_id=variant_id,
                        index=slide.index,
                        slide_plan_id=slide.id,
                        purpose=slide.purpose.value,
                        title=slide.title_intent,
                    )
                    for slide in plan.slides
                ]
                run.variant_plans[variant_id] = plan
                # PNG-превью и монтаж строятся из уже готового PDF. Это
                # удобство, а не часть результата: сбой не должен ронять
                # прогон — превью дособерутся лениво по запросу.
                try:
                    _ensure_previews(run, variant_id)
                except Exception:  # noqa: BLE001 — best-effort
                    logger.warning("preview build failed for %s", variant_id, exc_info=True)
                with _JOB_LOCK:
                    job = STORE.jobs[job_id]
                    if job.state not in _TERMINAL_JOB_STATES:
                        STORE.jobs[job_id] = job.model_copy(
                            update={"progress": (index + 1) / total}
                        )
    except DeckDNAError as exc:
        job_error = JobError(code=exc.code, message=exc.message, stage=exc.stage)
    except Exception as exc:  # noqa: BLE001 — воркер обязан завершаться
        # состоянием, а не потерянным traceback'ом в потоке
        job_error = JobError(
            code="internal_error", message=f"{type(exc).__name__}: {exc}"
        )
    finally:
        # HTTP-клиент gateway закрывается на любом исходе — иначе
        # AsyncClient провайдера утекает вместе с коннекшен-пулом.
        close = getattr(gateway, "aclose", None)
        if close is not None:
            asyncio.run(close())
    _finish_generation(run, error=job_error, plans=plans)


@app.post(
    f"{API_PREFIX}/projects/{{project_id}}/generations",
    status_code=202,
    tags=["generations"],
)
async def create_generation(
    project_id: str,
    body: GenerationCreate,
    idempotency_key: IdempotencyKey = None,
) -> GenerationAccepted:
    project = _require(STORE.projects, project_id, "project")
    _require_owned_template(body.template_id, project_id)
    _require_owned_content_pack(body.content_pack_id, project_id)
    if body.provider_session_id is not None:
        _require_session(body.provider_session_id, project_id)
    # gateway строится синхронно — та же upfront-валидация provider-
    # конфига, что была у синхронной версии (provider_capability_missing
    # → 422 до 202, не silent failure в фоне).
    gateway = _gateway_for(body, project_id)
    run = _create_generation(project, body, parent_run_id=None)
    _GEN_POOL.submit(_execute_generation, run.detail.id, gateway)
    return GenerationAccepted(
        generation_id=run.detail.id,
        job_id=run.detail.job_id,
        variant_ids=[v.id for v in run.detail.variants],
    )


@app.post(f"{API_PREFIX}/projects/{{project_id}}/plans", tags=["plans"])
async def create_plans(project_id: str, body: PlanRequest) -> PlanSet:
    """План до вёрстки (D7): структура колоды по каждой стратегии за
    секунды. Его можно показать, поправить и передать в POST /generations
    (deck_plan_id или deck_plan) — планирование тогда не повторяется."""
    _require(STORE.projects, project_id, "project")
    if body.template_id is not None:
        _require_owned_template(body.template_id, project_id)
    _require_owned_content_pack(body.content_pack_id, project_id)
    if not STORE.pack_artifacts.get(body.content_pack_id):
        raise DeckDNAError(
            "invalid_input",
            f"content pack {body.content_pack_id} has no uploaded source file",
        )
    # Переиспользуем сохранённый при upload ContentPack, а не парсим
    # временную копию заново — так artifact_id на каждом unit остаётся
    # тем же, что и в исходном upload.
    pack = STORE.content_packs[body.content_pack_id]
    if body.provider_session_id is not None:
        _require_session(body.provider_session_id, project_id)
    gateway = _gateway_for(body, project_id)
    entries: list[PlanEntry] = []
    try:
        # Фаза 3: несколько стратегий планируются последовательно в
        # ОДНОМ event loop'е этого ASGI-запроса — opt-in session()
        # (_maybe_gateway_session) даёт им один общий HTTP-клиент
        # вместо хендшейка на каждую стратегию.
        async with _maybe_gateway_session(gateway):
            for strategy_name in dict.fromkeys(body.strategies):
                strategy = Strategy(strategy_name)
                if gateway is None:
                    plan = await asyncio.to_thread(
                        plan_deck, pack, body.brief, strategy=strategy
                    )
                else:
                    plan = await plan_deck_llm(
                        pack, body.brief, gateway, strategy=strategy
                    )
                entries.append(PlanEntry(strategy=strategy_name, deck_plan=plan))
    finally:
        close = getattr(gateway, "aclose", None)
        if close is not None:
            await close()
    plan_set = PlanSet(
        plan_id=_new_id("plan"),
        project_id=project_id,
        content_pack_id=body.content_pack_id,
        plans=entries,
        created_at=_utcnow(),
    )
    STORE.plans[plan_set.plan_id] = plan_set
    return plan_set


@app.get(f"{API_PREFIX}/generations/{{run_id}}", tags=["generations"])
async def get_generation(run_id: str) -> GenerationDetail:
    return _require(STORE.runs, run_id, "generation run").detail


@app.post(f"{API_PREFIX}/generations/{{run_id}}/cancel", tags=["generations"])
async def cancel_generation(run_id: str) -> Job:
    run = _require(STORE.runs, run_id, "generation run")
    job = _require(STORE.jobs, run.detail.job_id, "job")
    now = _utcnow()
    with _JOB_LOCK:
        if job.state in _TERMINAL_JOB_STATES:
            # Терминальный job отменить нельзя — это уже не отмена, а
            # перезапись истории: честный 409 вместо ложного canceled.
            raise DeckDNAError(
                "state_conflict",
                f"generation job is already {job.state.value}; cannot cancel",
            )
        canceled_job = job.model_copy(
            update={"state": JobState.canceled, "finished_at": now}
        )
        STORE.jobs[job.id] = canceled_job
        # Уже выполняющийся вариант доводится до конца (поток внутри
        # generate() убить нельзя) — executor пометит его честным
        # результатом; остальные варианты отменены прямо сейчас.
        variants = [
            v.model_copy(update={"status": JobState.canceled})
            if v.status == JobState.queued
            else v
            for v in run.detail.variants
        ]
        run.detail = run.detail.model_copy(
            update={
                "state": JobState.canceled,
                "variants": variants,
                "finished_at": now,
            }
        )
    return canceled_job


@app.post(f"{API_PREFIX}/generations/{{run_id}}/retry", status_code=202, tags=["generations"])
async def retry_generation(
    run_id: str, idempotency_key: IdempotencyKey = None
) -> GenerationAccepted:
    parent_run = _require(STORE.runs, run_id, "generation run")
    parent = parent_run.detail
    project = _require(STORE.projects, parent.project_id, "project")
    if parent_run.request is not None:
        body = parent_run.request
    elif parent.deck_plan is not None:
        body = GenerationCreate(
            template_id=parent.template_id,
            content_pack_id=parent.content_pack_id,
            brief=parent.deck_plan.brief,
            variants=[VariantRequest(strategy=s.strategy) for s in parent.variants],
        )
    else:
        raise DeckDNAError("internal_error", "generation run has no recorded request")
    if body.provider_session_id is not None:
        _require_session(body.provider_session_id, parent.project_id)
    gateway = _gateway_for(body, parent.project_id)
    # retry — всегда свежий run/job (parent_run_id связывает их),
    # никакого повторного использования состояния старого прогона.
    run = _create_generation(project, body, parent_run_id=run_id)
    _GEN_POOL.submit(_execute_generation, run.detail.id, gateway)
    return GenerationAccepted(
        generation_id=run.detail.id,
        job_id=run.detail.job_id,
        variant_ids=[v.id for v in run.detail.variants],
    )


# ---------------------------------------------------------------------------
# §7 Jobs and events
# ---------------------------------------------------------------------------


@app.get(f"{API_PREFIX}/jobs/{{job_id}}", tags=["jobs"])
async def get_job(job_id: str) -> Job:
    return _require(STORE.jobs, job_id, "job")


@app.get(f"{API_PREFIX}/jobs/{{job_id}}/events", tags=["jobs"])
async def job_events(job_id: str, after: int = Query(default=0, ge=0)) -> EventSourceResponse:
    job = _require(STORE.jobs, job_id, "job")

    async def stream() -> AsyncIterator[dict[str, str]]:
        # Синтез из фактического состояния job (журнала переходов пока
        # нет — процесс-локальный lifecycle; job.completed — только для
        # реально завершённого, canceled/failed идут state_changed).
        if job.state == JobState.queued:
            events = [("job.state_changed", {"state": "queued"})]
        elif job.state == JobState.running:
            events = [
                ("job.state_changed", {"state": "queued"}),
                ("stage.started", {"stage": job.kind.value}),
                ("job.state_changed", {"state": "running"}),
            ]
        else:
            events = [
                ("job.state_changed", {"state": "queued"}),
                ("stage.started", {"stage": job.kind.value}),
                ("job.state_changed", {"state": "running"}),
                (
                    "job.completed"
                    if job.state == JobState.completed
                    else "job.state_changed",
                    {"state": job.state.value},
                ),
            ]
        for seq, (event_type, data) in enumerate(events, start=1):
            if seq <= after:
                continue
            yield {
                "id": str(seq),
                "event": event_type,
                "data": json.dumps(
                    {
                        "sequence": seq,
                        "timestamp": job.created_at.isoformat(),
                        "job_id": job.id,
                        "stage": job.kind.value,
                        "progress": job.progress,
                        **data,
                    }
                ),
            }

    return EventSourceResponse(stream())


# ---------------------------------------------------------------------------
# §8 Variants and slides
# ---------------------------------------------------------------------------


def _variant_or_404(variant_id: str) -> tuple[_InternalRun, VariantSummary]:
    for run in STORE.runs.values():
        for variant in run.detail.variants:
            if variant.id == variant_id:
                return run, variant
    raise DeckDNAError("not_found", f"variant not found: {variant_id}")


def _evidence_graph_for_plan(plan: DeckPlan | None) -> EvidenceGraph | None:
    """Resolve a plan's graph across the two historical ID conventions.

    Stored graphs are keyed by ``content_pack_id`` while newer plans carry
    the graph's ``eg-*`` ID; deterministic legacy plans may carry
    ``pack:<content_pack_id>``.  Contextual audit must use the same graph in
    all three cases instead of silently falling back to an empty excerpt.
    """
    if plan is None:
        return None
    graph_id = plan.evidence_graph_id
    direct = STORE.evidence_graphs.get(graph_id)
    if direct is not None:
        return direct
    pack_id = graph_id.removeprefix("pack:")
    graph = STORE.evidence_graphs.get(pack_id)
    if graph is not None:
        return graph
    return next(
        (candidate for candidate in STORE.evidence_graphs.values() if candidate.id == graph_id),
        None,
    )


@app.get(f"{API_PREFIX}/generations/{{run_id}}/variants", tags=["variants"])
async def list_variants(run_id: str) -> Page[VariantSummary]:
    run = _require(STORE.runs, run_id, "generation run")
    return Page[VariantSummary](items=run.detail.variants)


@app.get(f"{API_PREFIX}/variants/{{variant_id}}", tags=["variants"])
async def get_variant(variant_id: str) -> VariantSummary:
    _, variant = _variant_or_404(variant_id)
    return variant


@app.get(f"{API_PREFIX}/variants/{{variant_id}}/slides", tags=["variants"])
async def list_variant_slides(
    variant_id: str,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> Page[SlideInfo]:
    _variant_or_404(variant_id)
    return _paginate(STORE.slides.get(variant_id, []), cursor, limit)


def _slide_or_404(slide_id: str) -> SlideInfo:
    for slides in STORE.slides.values():
        for slide in slides:
            if slide.id == slide_id:
                return slide
    raise DeckDNAError("not_found", f"slide not found: {slide_id}")


@app.get(f"{API_PREFIX}/slides/{{slide_id}}", tags=["variants"])
async def get_slide(slide_id: str) -> SlideInfo:
    return _slide_or_404(slide_id)


@app.get(f"{API_PREFIX}/slides/{{slide_id}}/preview", tags=["variants"])
async def get_slide_preview(slide_id: str) -> Response:
    slide = _slide_or_404(slide_id)
    run, _ = _variant_or_404(slide.variant_id)
    # после repair превью старой ревизии сброшены — строятся заново из PDF
    # новой ревизии (D6)
    await asyncio.to_thread(_ensure_previews, run, slide.variant_id)
    slide = _slide_or_404(slide_id)
    artifact = _require(STORE.artifacts, slide.preview_artifact_id or "", "slide preview")
    return Response(
        content=artifact.data,
        media_type=artifact.mime_type,
        headers={"Content-Disposition": f'inline; filename="{artifact.filename}"'},
    )


# ---------------------------------------------------------------------------
# §9 Audit (tool: audit_deck) and repair (tool: repair_deck)
# ---------------------------------------------------------------------------


async def _contextual_audit_issues(
    audit: AuditRun,
    run: _InternalRun,
    variant: VariantSummary,
    session: ProviderSession,
) -> list[AuditIssue]:
    """Реальный contextual-прогон по lease-сессии.

    Возвращает contract-issues (runtime → ``to_dict`` → ``_contract_issue``:
    на границе ремапится ``vlm_verdict`` → ``model_verdict``). При отказе
    провайдера статус аудита — ``failed`` и список пуст.
    """
    diagnostics: dict[str, Any] = {}
    try:
        deck_artifact = _require(
            STORE.artifacts, variant.deck_artifact_id, "variant deck bytes"
        )
        with tempfile.TemporaryDirectory(prefix="deckdna-ctx-audit-") as tmpdir:
            deck_path = Path(tmpdir) / deck_artifact.filename
            deck_path.write_bytes(deck_artifact.data)
            gateway = _session_gateway(session)
            # план именно этого варианта — run.detail.deck_plan держит
            # только план ПЕРВОГО варианта; на multi-variant run'е
            # чужой план граундил бы VLM-checks неверно
            plan = run.variant_plans.get(variant.id) or run.detail.deck_plan
            evidence_graph = _evidence_graph_for_plan(plan)
            try:
                ctx_issues = await run_contextual_audit(
                    deck_path,
                    gateway,
                    deck_plan=plan,
                    evidence_graph=evidence_graph,
                    deck_language=plan.language if plan else "ru",
                    audit_run_id=audit.id,
                    deck_revision=audit.deck_revision,
                    diagnostics=diagnostics,
                )
            finally:
                await gateway.aclose()
        audit.contextual_status = "completed" if diagnostics.get("complete") else "failed"
        return [_contract_issue(i.to_dict()) for i in ctx_issues]
    except DeckDNAError:
        audit.contextual_status = "failed"
        return []


@app.get(f"{API_PREFIX}/audit/rules", tags=["audit"])
async def list_audit_rules() -> list[RuleInfoOut]:
    """Каталог правил аудита (D8): название, категория, серьёзность,
    чинится ли, порог — единый источник для интерфейса."""
    cfg = default_audit_config()
    out: list[RuleInfoOut] = []
    for info in audit_catalog.RULES.values():
        threshold = getattr(cfg, info.threshold_key, None) if info.threshold_key else None
        out.append(
            RuleInfoOut(
                code=info.code,
                title_ru=info.title_ru,
                category=info.category,
                deterministic=info.deterministic,
                default_severity=info.default_severity,
                repairable=info.repairable,
                fix_title_ru=info.fix_title_ru,
                threshold=threshold,
            )
        )
    return out


@app.post(f"{API_PREFIX}/variants/{{variant_id}}/audits", status_code=202, tags=["audit"])
async def create_audit(
    variant_id: str,
    body: AuditRequest,
    idempotency_key: IdempotencyKey = None,
) -> AuditAccepted:
    run, variant = _variant_or_404(variant_id)
    session = (
        _require_session(body.provider_session_id, run.detail.project_id)
        if body.provider_session_id is not None
        else None
    )
    existing = next((a for a in STORE.audits.values() if a.variant_id == variant_id), None)
    if existing is not None:
        # the generation pipeline already ran deterministic audit — serve it;
        # но явно переданная живая сессия выполняет upgrade: VLM-прогон
        # поверх существующего audit run, детерминированные issues
        # сохраняются (completed → чистая идемпотентность, повтор не
        # дублирует вызовы; failed → честный повтор попытки).
        if session is not None and existing.contextual_status != "completed":
            ctx_issues = await _contextual_audit_issues(
                existing, run, variant, session
            )
            issues = [
                i for i in STORE.issues.get(existing.id, []) if i.deterministic
            ] + ctx_issues
            STORE.issues[existing.id] = issues
            existing.issue_count = len(issues)
            existing.summary_by_severity, existing.summary_by_type = (
                _issue_summaries(issues)
            )
            existing.finished_at = _utcnow()
            idx = run.detail.variants.index(variant)
            run.detail.variants[idx] = variant.model_copy(
                update={
                    "metrics": variant.metrics.model_copy(
                        update={"issues_total": len(issues)}
                    )
                }
            )
        job = _complete_job(JobKind.audit, run.detail.project_id, {"audit_id": existing.id})
        return AuditAccepted(audit_id=existing.id, job_id=job.id)
    # This branch used to fabricate a fixed set of stub
    # issues and hardcoded validity=1.0/editability_pei=4 for ANY variant
    # with no existing audit record -- including one still queued/running
    # or that failed generation outright, since deck_artifact_id was never
    # checked. Every successful generation already creates a real audit
    # (see _execute_generation's own STORE.audits write above); reaching
    # this branch with a completed deck means that write was somehow
    # skipped, not that stub data is an acceptable substitute.
    if variant.deck_artifact_id is None:
        raise DeckDNAError(
            "state_conflict",
            f"variant {variant_id} has no completed deck yet "
            f"(status={variant.status.value}); audit requires a finished generation",
            http_status=409,
        )
    deck_artifact = _require(STORE.artifacts, variant.deck_artifact_id, "variant deck bytes")
    audit = AuditRun(
        id=_new_id("audit"),
        variant_id=variant_id,
        deck_revision=run.deck_revision.get(variant_id, 1),
        status="completed",
        deterministic_status="completed",
        contextual_status="pending" if session else "skipped",
        issue_count=0,
        summary_by_severity=IssueSummary(),
        summary_by_type={},
        config_version=body.config_version,
        created_at=_utcnow(),
        finished_at=_utcnow(),
    )
    with tempfile.TemporaryDirectory(prefix="deckdna-audit-") as tmpdir:
        deck_path = Path(tmpdir) / deck_artifact.filename
        deck_path.write_bytes(deck_artifact.data)
        # audit_deck() itself opens the package with python-pptx; failing
        # to do so is the same round-trip signal quality_passport.py's own
        # _validity_metrics uses, without a second, separate open here.
        try:
            runtime_issues = audit_deck(
                deck_path, deck_revision=audit.deck_revision, audit_run_id=audit.id
            )
        except Exception as exc:  # noqa: BLE001 — round-trip signal, not a crash
            raise DeckDNAError(
                "package_corrupt",
                f"stored deck artifact for variant {variant_id} failed to open: {exc}",
                stage="audit",
            ) from exc
    issues = [_contract_issue(i.to_dict()) for i in runtime_issues]
    if session is not None:
        issues = [
            *issues,
            *await _contextual_audit_issues(audit, run, variant, session),
        ]
    STORE.issues[audit.id] = issues
    by_severity, by_type = _issue_summaries(issues)
    audit.issue_count = len(issues)
    audit.summary_by_severity = by_severity
    audit.summary_by_type = by_type
    STORE.audits[audit.id] = audit
    idx = run.detail.variants.index(variant)
    run.detail.variants[idx] = variant.model_copy(
        update={
            "audit_status": "completed",
            # audit_deck() above succeeded opening the stored artifact —
            # the same round-trip signal _validity_metrics uses — or this
            # line was never reached (package_corrupt raised instead).
            "metrics": VariantMetrics(validity=1.0, issues_total=len(issues)),
        }
    )
    job = _complete_job(JobKind.audit, run.detail.project_id, {"audit_id": audit.id})
    return AuditAccepted(audit_id=audit.id, job_id=job.id)


@app.get(f"{API_PREFIX}/audits/{{audit_id}}", tags=["audit"])
async def get_audit(audit_id: str) -> AuditRun:
    return _require(STORE.audits, audit_id, "audit run")


@app.get(f"{API_PREFIX}/audits/{{audit_id}}/issues", tags=["audit"])
async def list_audit_issues(
    audit_id: str,
    slide_id: str | None = Query(default=None),
    rule_code: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    deterministic: bool | None = Query(default=None),
    status: Literal["open", "selected", "fixed", "dismissed", "unresolved"] | None = Query(
        default=None
    ),
    repairable: bool | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> Page[AuditIssueOut]:
    _require(STORE.audits, audit_id, "audit run")
    issues = STORE.issues.get(audit_id, [])
    if slide_id is not None:
        issues = [i for i in issues if i.slide_id == slide_id]
    if rule_code is not None:
        issues = [i for i in issues if i.rule_code == rule_code]
    if severity is not None:
        issues = [i for i in issues if i.severity.value == severity]
    if deterministic is not None:
        issues = [i for i in issues if i.deterministic == deterministic]
    if status is not None:
        issues = [i for i in issues if i.status.value == status]
    if repairable is not None:
        issues = [i for i in issues if i.repairable == repairable]
    return _paginate(issues, cursor, limit)


def _repair_sync(
    audit: AuditRun,
    run: _InternalRun,
    variant: VariantSummary,
    selected: list[AuditIssueOut],
    project_id: str | None,
    gateway: ModelGateway | None = None,
) -> RepairAccepted:
    """Применить repair и пересчитать аудит (блокирующая работа — в потоке).

    Под замком варианта: два repair одного варианта и ленивая сборка его
    артефактов сериализуются — иначе PDF старой ревизии мог бы лечь в кеш
    новой, а второй repair — стартовать с уже устаревшей колоды."""
    with _variant_lock(audit.variant_id):
        # колода и ревизия читаются уже под замком, а не при приёме запроса
        current = _variant_or_404(audit.variant_id)[1]
        try:
            return _repair_locked(audit, run, current, selected, project_id, gateway)
        finally:
            close = getattr(gateway, "aclose", None)
            if close is not None:
                asyncio.run(close())


def _repair_locked(
    audit: AuditRun,
    run: _InternalRun,
    variant: VariantSummary,
    selected: list[AuditIssueOut],
    project_id: str | None,
    gateway: ModelGateway | None = None,
) -> RepairAccepted:
    audit_id = audit.id
    variant_id = audit.variant_id
    deck_artifact = _require(
        STORE.artifacts, variant.deck_artifact_id, "variant deck bytes"
    )
    new_revision = run.deck_revision.get(variant_id, 1) + 1
    runtime_selected = [_contract_issue_to_runtime(i) for i in selected]

    with tempfile.TemporaryDirectory(prefix="deckdna-repair-") as tmpdir:
        tmp = Path(tmpdir)
        in_pptx = tmp / deck_artifact.filename
        in_pptx.write_bytes(deck_artifact.data)
        out_pptx = tmp / f"repaired_rev{new_revision}.pptx"

        # pipeline.generate() threads protected_slide_
        # indices through auto-fix/compose, but API repair never loaded
        # this config at all -- a protected slide's issues were fair game
        # for both the model rewrite pass below and apply_repairs.
        protected = frozenset(load_generation_config().protected_slide_indices)

        # С моделью переполненный текст сначала переписывается короче
        # (text_fit): что исчезло из повторного аудита — исправлено моделью;
        # остальное идёт в детерминированный план (рамка, затем обрезка).
        model_outcomes: list[repair_outcomes.IssueOutcome] = []
        remaining = runtime_selected
        fit_candidates = [
            i
            for i in runtime_selected
            if i.rule_code in text_fit.FIT_RULES and i.slide_index not in protected
        ]
        if gateway is not None and fit_candidates and text_fit.can_rewrite(gateway):
            fitted = tmp / "fitted.pptx"

            async def _fit_and_close() -> text_fit.FitReport:
                # Фаза 3: fit_texts гоняет один text_json на слайд через
                # asyncio.gather — opt-in session() (_maybe_gateway_session)
                # даёт им один общий HTTP-клиент вместо хендшейка на каждый
                # слайд. Сессия/клиент создаются и закрываются целиком
                # ВНУТРИ этого loop'а — _repair_sync (вызывающая сторона,
                # другой поток пула) на выходе закрывает gateway ЕЩЁ РАЗ
                # своим отдельным asyncio.run как страховку, но тот — уже
                # другой loop, и клиент, оставшийся живым из ЭТОГО loop'а,
                # там закрыть было бы нельзя («Event loop is closed») —
                # поэтому закрытие обязано случиться здесь же, до выхода
                # из asyncio.run.
                try:
                    async with _maybe_gateway_session(gateway):
                        return await text_fit.fit_texts(
                            in_pptx, fit_candidates, gateway, fitted
                        )
                finally:
                    close = getattr(gateway, "aclose", None)
                    if close is not None:
                        await close()

            fit = asyncio.run(_fit_and_close())
            if fit.rewritten:
                still = {
                    repair_outcomes.fingerprint(i)
                    for i in audit_deck(fitted, deck_revision=new_revision)
                }
                done = {
                    i.id
                    for i in fit_candidates
                    if i.id in fit.rewritten
                    and repair_outcomes.fingerprint(i) not in still
                }
                if done:
                    in_pptx = fitted
                    model_outcomes = [
                        repair_outcomes.IssueOutcome(
                            issue_id=i.id,
                            status=repair_outcomes.FIXED,
                            action="rewrite_text",
                            summary=fit.rewritten[i.id],
                            fingerprint=repair_outcomes.fingerprint(i),
                        )
                        for i in fit_candidates
                        if i.id in done
                    ]
                    remaining = [i for i in runtime_selected if i.id not in done]

        plan = plan_repairs_with_report(remaining)
        apply_report = apply_repairs(in_pptx, plan.actions, out_pptx, protected=protected)

        new_artifact = _store_artifact(
            out_pptx.read_bytes(),
            type="deck.pptx.revision",
            mime_type=_PPTX_MIME,
            filename=f"{variant.strategy}_rev{new_revision}.pptx",
            project_id=project_id,
        )

        # Re-audit the repaired deck: issues that were actually fixed
        # disappear from the list; ones the executor skipped/failed on stay
        # open — nothing is marked "fixed" by hand. Проверенные аудитом
        # исходы и есть отчёт: «применено» = проблема исчезла.
        fresh_runtime = audit_deck(
            out_pptx, deck_revision=new_revision, audit_run_id=audit_id
        )

    outcomes = model_outcomes + repair_outcomes.build_outcomes(
        remaining, plan, apply_report, fresh_runtime
    )
    by_order = {i.id: k for k, i in enumerate(runtime_selected)}
    outcomes.sort(key=lambda o: by_order.get(o.issue_id, 0))
    counts = repair_outcomes.count_outcomes(outcomes, plan)
    dismissed = {
        i.fingerprint
        for i in STORE.issues.get(audit_id, [])
        if i.status == IssueStatus.dismissed and i.fingerprint
    }
    fresh = []
    for raw in fresh_runtime:
        issue = _contract_issue(raw.to_dict())
        if issue.fingerprint in dismissed:
            issue = issue.model_copy(update={"status": IssueStatus.dismissed})
        fresh.append(issue)

    fixed_by_rule: dict[str, int] = {}
    by_id = {i.id: i for i in selected}
    for outcome in outcomes:
        if outcome.status == repair_outcomes.FIXED:
            rule = by_id[outcome.issue_id].rule_code
            fixed_by_rule[rule] = fixed_by_rule.get(rule, 0) + 1

    with _JOB_LOCK:
        STORE.issues[audit_id] = fresh
        by_severity, by_type = _issue_summaries(fresh)
        audit.deck_revision = new_revision
        # Contextual verdicts describe the previous PPTX bytes. The fresh
        # audit above is deterministic only, so the new revision needs its
        # own contextual pass even when the old one was completed.
        audit.contextual_status = "pending"
        audit.issue_count = len(fresh)
        audit.summary_by_severity = by_severity
        audit.summary_by_type = by_type
        audit.finished_at = _utcnow()

        run.deck_revision[variant_id] = new_revision
        # Новая ревизия — новый PPTX. PDF, паспорт, HTML и превью старой
        # ревизии больше её не описывают: кеш сбрасывается, а PDF/паспорт
        # пересобираются лениво из НОВОГО PPTX при запросе экспорта или
        # превью (B2), а не пропадают с 501.
        run.variant_artifacts[variant_id] = {"pptx": new_artifact.id}
        report = run.variant_reports.get(variant_id)
        if report is not None:
            merged = dict(report.get("repair_fixes") or {})
            for rule, n in fixed_by_rule.items():
                merged[rule] = merged.get(rule, 0) + n
            report["repair_fixes"] = merged
        STORE.slides[variant_id] = [
            sl.model_copy(update={"preview_artifact_id": None, "revision": new_revision})
            for sl in STORE.slides.get(variant_id, [])
        ]
        update: dict[str, Any] = {
            "deck_artifact_id": new_artifact.id,
            "montage_artifact_id": None,
        }
        if variant.metrics is not None:
            update["metrics"] = variant.metrics.model_copy(
                update={"issues_total": len(fresh)}
            )
        idx = next(i for i, v in enumerate(run.detail.variants) if v.id == variant_id)
        run.detail.variants[idx] = run.detail.variants[idx].model_copy(update=update)

    now = _utcnow()
    job = Job(
        id=_new_id("job"),
        kind=JobKind.repair,
        state=JobState.completed,
        progress=1.0,
        project_id=project_id,
        created_at=now,
        started_at=now,
        finished_at=now,
        result_ids={
            "audit_id": audit_id,
            "deck_revision": str(new_revision),
            "deck_artifact_id": new_artifact.id,
            "applied": str(counts["applied"]),
            "skipped": str(counts["skipped"]),
            "failed": str(counts["failed"]),
            "not_implemented": str(apply_report.not_implemented),
            "unresolved": str(counts["unresolved"]),
        },
        result=RepairJobResult(
            **counts,
            not_implemented=apply_report.not_implemented,
            audit_id=audit_id,
            deck_revision=new_revision,
            deck_artifact_id=new_artifact.id,
            outcomes=[IssueOutcome(**o.to_dict()) for o in outcomes],
        ),
    )
    STORE.jobs[job.id] = job
    return RepairAccepted(job_id=job.id, audit_id=audit_id, deck_revision=new_revision)


@app.post(
    f"{API_PREFIX}/audits/{{audit_id}}/repairs",
    status_code=202,
    tags=["audit"],
    response_model=RepairAccepted | RepairPreview,
    responses={200: {"model": RepairPreview, "description": "dry_run: план без применения"}},
)
async def create_repair(
    audit_id: str,
    body: RepairRequest,
    dry_run: bool = Query(
        default=False,
        description="Только план: те же outcomes без применения (что будет сделано).",
    ),
    idempotency_key: IdempotencyKey = None,
) -> RepairAccepted | RepairPreview | JSONResponse:
    audit = _require(STORE.audits, audit_id, "audit run")
    run, variant = _variant_or_404(audit.variant_id)
    if body.provider_session_id is not None:
        # lease валидируется до работы: сессия даёт модель для text_fit
        _require_session(body.provider_session_id, run.detail.project_id)
    issues = STORE.issues.get(audit_id, [])
    by_id = {i.id: i for i in issues}
    missing = [i for i in body.selected_issue_ids if i not in by_id]
    if missing:
        raise DeckDNAError(
            "invalid_input", f"unknown issue ids for this audit: {missing}"
        )
    selected = [by_id[i] for i in body.selected_issue_ids]

    if dry_run:
        runtime_selected = [_contract_issue_to_runtime(i) for i in selected]
        plan = plan_repairs_with_report(runtime_selected)
        preview = RepairPreview(
            audit_id=audit_id,
            deck_revision=run.deck_revision.get(audit.variant_id, 1),
            outcomes=[
                IssueOutcome(**o.to_dict())
                for o in repair_outcomes.plan_outcomes(runtime_selected, plan)
            ],
        )
        # ничего не создано и не принято в очередь — 200, а не 202
        return JSONResponse(status_code=200, content=jsonable_encoder(preview))
    # apply + повторный аудит + сборка — секунды блокирующей работы: в поток,
    # чтобы не держать event loop
    gateway = _gateway_for(body, run.detail.project_id)
    return await asyncio.to_thread(
        _repair_sync, audit, run, variant, selected, run.detail.project_id, gateway
    )


@app.post(f"{API_PREFIX}/issues/{{issue_id}}/dismiss", tags=["audit"])
async def dismiss_issue(issue_id: str, body: IssueDismissRequest) -> AuditIssue:
    for issues in STORE.issues.values():
        for issue in issues:
            if issue.id == issue_id:
                updated = issue.model_copy(update={"status": IssueStatus.dismissed})
                issues[issues.index(issue)] = updated
                return updated
    raise DeckDNAError("not_found", f"issue not found: {issue_id}")


# ---------------------------------------------------------------------------
# §10 Exports (tool: export_deck)
# ---------------------------------------------------------------------------


@app.post(f"{API_PREFIX}/variants/{{variant_id}}/exports", status_code=202, tags=["exports"])
async def create_export(
    variant_id: str,
    body: ExportRequest,
    idempotency_key: IdempotencyKey = None,
) -> ExportAccepted:
    run, variant = _variant_or_404(variant_id)
    revision = run.deck_revision.get(variant_id, 1)
    artifacts: list[ExportArtifact] = []
    for fmt in dict.fromkeys(body.formats):
        # PPTX — всегда текущая ревизия; PDF/паспорт/HTML — из кеша
        # ревизии, а после repair пересобираются лениво (B2, D5)
        stored = await asyncio.to_thread(_ensure_artifact, run, variant_id, fmt)
        artifacts.append(
            ExportArtifact(
                format=fmt,
                artifact_id=stored.id,
                sha256=stored.sha256,
                size_bytes=stored.size_bytes,
                mime_type=stored.mime_type,
                download_url=f"{API_PREFIX}/artifacts/{stored.id}/download",
            )
        )
    job = _complete_job(JobKind.export, run.detail.project_id, {})
    export = ExportRecord(
        id=_new_id("exp"),
        variant_id=variant_id,
        deck_revision=revision,
        job_id=job.id,
        artifacts=artifacts,
        created_at=_utcnow(),
    )
    STORE.exports[export.id] = export
    with _JOB_LOCK:
        _patch_variant(
            run, variant_id, export_ids=[*_variant_or_404(variant_id)[1].export_ids, export.id]
        )
    return ExportAccepted(export_id=export.id, job_id=job.id)


@app.get(f"{API_PREFIX}/exports/{{export_id}}", tags=["exports"])
async def get_export(export_id: str) -> ExportRecord:
    return _require(STORE.exports, export_id, "export")


_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


@app.get(f"{API_PREFIX}/artifacts/{{artifact_id}}/download", tags=["exports"])
async def download_artifact(artifact_id: str, request: Request) -> Response:
    """Скачивание артефакта с ``ETag`` и ``Range`` (pdf.js грузит большие
    PDF по диапазонам, браузер кеширует по ETag)."""
    artifact = _require(STORE.artifacts, artifact_id, "artifact")
    safe_name = artifact.filename.replace('"', "").replace("/", "_")
    etag = f'"{artifact.sha256}"'
    headers = {
        "Content-Disposition": f'attachment; filename="{safe_name}"',
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Cache-Control": "private, max-age=0, must-revalidate",
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    data = artifact.data
    total = len(data)
    range_header = request.headers.get("range")
    if range_header:
        match = _RANGE_RE.match(range_header.strip())
        start_s, end_s = match.groups() if match else ("", "")
        if not match or (not start_s and not end_s):
            return Response(
                status_code=416,
                headers={**headers, "Content-Range": f"bytes */{total}"},
            )
        if start_s:
            start = int(start_s)
            end = min(int(end_s), total - 1) if end_s else total - 1
        else:  # суффикс: последние N байт
            start = max(total - int(end_s), 0)
            end = total - 1
        if start >= total or start > end:
            return Response(
                status_code=416,
                headers={**headers, "Content-Range": f"bytes */{total}"},
            )
        return Response(
            content=data[start : end + 1],
            status_code=206,
            media_type=artifact.mime_type,
            headers={**headers, "Content-Range": f"bytes {start}-{end}/{total}"},
        )
    return Response(content=data, media_type=artifact.mime_type, headers=headers)


# ---------------------------------------------------------------------------
# §11 Health and capabilities
# ---------------------------------------------------------------------------


@app.get(f"{API_PREFIX}/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get(f"{API_PREFIX}/health/ready")
async def ready() -> dict[str, str]:
    # Reports process readiness; DB/Redis/artifact-dir probes are not wired yet.
    return {"status": "ok"}


@app.get(f"{API_PREFIX}/version")
async def version() -> dict[str, str]:
    out = {
        "version": app.version,
        "schema_version": SCHEMA_VERSION,
        "official_mode": str(settings.official_mode).lower(),
    }
    try:
        out["skill_version"] = str(_read_skill_manifest().get("version", ""))
    except DeckDNAError:
        out["skill_version"] = "unknown"
    return out


def _read_skill_manifest() -> dict[str, Any]:
    """skill/manifest.yaml parsed to a dict; typed errors, never a bare 500."""
    path = _REPO_ROOT / "skill" / "manifest.yaml"
    if not path.is_file():
        raise DeckDNAError(
            "not_found", f"skill manifest not found at {path}"
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise DeckDNAError(
            "internal_error", f"skill manifest is not valid YAML: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise DeckDNAError(
            "internal_error", "skill manifest is not a YAML mapping"
        )
    return data


@app.get(f"{API_PREFIX}/skill/manifest", tags=["skill"])
async def skill_manifest() -> dict[str, Any]:
    """The skill's manifest.yaml parsed to JSON (OR-006: versioned skill
    identity — name, version, inputs/outputs, tools)."""
    return _read_skill_manifest()


@app.get(f"{API_PREFIX}/capabilities")
async def capabilities() -> dict[str, object]:
    # Aggregated by stage registry once Agents 2/3 register implementations.
    try:
        manifest = _read_skill_manifest()
        skill = {
            "name": str(manifest.get("id", "deckdna")),
            "version": str(manifest.get("version", "")),
        }
    except DeckDNAError:
        skill = {"name": "deckdna", "version": "unknown"}
    # Only real capabilities: parsers resolve to content_parsers._PARSERS
    # suffixes, exporters to formats the API actually serves,
    # audit_rules to the codes in the rule catalog.
    server_provider = (
        not settings.mock_provider
        and bool(settings.provider_base_url and settings.provider_api_key and settings.model_text)
    )
    if STORE.provider_sessions:
        contextual_mode = "session"
    elif server_provider:
        contextual_mode = "server"
    elif settings.mock_provider:
        contextual_mode = "mock"
    else:
        contextual_mode = "none"
    return {
        "parsers": sorted(
            suffix.lstrip(".") for suffix in content_parsers._PARSERS
        ),
        "exporters": ["html", "pdf", "pptx", "quality_passport"],
        # только правила детерминированного аудита; полный каталог (вместе
        # с контекстными) — GET /audit/rules
        "audit_rules": sorted(
            code for code, info in audit_catalog.RULES.items() if info.deterministic
        ),
        "schemas": {"version": SCHEMA_VERSION},
        "skill": skill,
        # Явные флаги функций (D9): интерфейс переключает «скоро» → «есть»
        # по ним. Каждый true — то, что этот процесс реально умеет.
        "features": {
            "html_export": True,
            "plan_only": True,
            "png_previews": True,
            "async_generation": True,
            # SSE отдаёт снимок состояния job, а не потоковый прогресс
            "sse_progress": False,
            # VLM-аудит возможен при живой сессии или настоящем серверном
            # провайдере; mock-провайдер — не считается (вердикты фиктивны)
            "contextual_audit": contextual_mode in {"session", "server"},
            "pdf_after_repair": True,
            "repair_dry_run": True,
            "style_fidelity": True,
            # модель переписывает переполненный текст (text_fit) — в генерации
            # и в repair, когда провайдер доступен
            "model_text_fit": contextual_mode in {"session", "server"},
            # модель включается сама, если на сервере есть провайдер
            "model_auto": _server_provider_ready(),
        },
        # какой провайдер стоит за contextual_audit: session | server | mock | none
        "contextual_audit_mode": contextual_mode,
    }
