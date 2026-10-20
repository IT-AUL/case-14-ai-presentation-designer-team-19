"""FastAPI routes and shared pipeline orchestration (docs/contracts/API.md).

Assistant steps, planning, native composition, audit and export call their
production modules. Long-running work uses background jobs and event journals;
provider calls use the configured gateway or an explicitly selected offline mode.
The local metadata/artifact store supports snapshots (ADR-031).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import copy
import hashlib
import ipaddress
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
import zipfile
from collections import Counter
from collections.abc import AsyncIterator, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from io import BytesIO
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from urllib.parse import quote, urlparse
from uuid import uuid4

import yaml
from fastapi import FastAPI, File, Form, Header, Query, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from lxml import etree
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sse_starlette.sse import EventSourceResponse

from deckdna.api.persistence import load_store, save_store
from deckdna.assistant.plan_edit import annotate_key_message, apply_action, change_chart
from deckdna.assistant.questions import build_questions
from deckdna.assistant.understanding import add_notes, apply_block_ops, build_understanding
from deckdna.audit import catalog as audit_catalog
from deckdna.audit.basic import audit_deck
from deckdna.audit.config import default_audit_config
from deckdna.audit.contextual import run_contextual_audit
from deckdna.audit.issues import AuditIssue as _RuntimeAuditIssue
from deckdna.contracts.audit_issue import AuditIssue
from deckdna.contracts.audit_issue import Bbox as IssueBbox
from deckdna.contracts.audit_issue import Status as IssueStatus
from deckdna.contracts.content_pack import ContentPack
from deckdna.contracts.dataset import Insight
from deckdna.contracts.deck_plan import Brief, DeckPlan, SlidePlan
from deckdna.contracts.design_dna import DesignDNA
from deckdna.contracts.evidence_graph import EvidenceGraph
from deckdna.contracts.quality_passport import Fallback, ModelProfile
from deckdna.contracts.serialize import to_schema_dict
from deckdna.contracts.variant_spec import Strategy
from deckdna.errors import DeckDNAError
from deckdna.evaluation import measures
from deckdna.evaluation.quality_passport import assemble_quality_passport
from deckdna.generation import pipeline
from deckdna.ingestion import content_parsers
from deckdna.ingestion.content_parsers import parse_file
from deckdna.ingestion.merge import merge_packs
from deckdna.logging_setup import configure_logging
from deckdna.planning import content_style
from deckdna.planning.complete import complete_hollow_slides
from deckdna.planning.config import load_generation_config
from deckdna.planning.content_fill import annotate_unit_origins, is_sources_heading
from deckdna.planning.evidence import build_evidence_graph
from deckdna.planning.story_director import plan_deck, plan_deck_llm
from deckdna.planning.story_director_v2 import plan_deck_llm_v2
from deckdna.pptx.exporting import previews as _previews
from deckdna.pptx.exporting.render import render_pdf
from deckdna.pptx.fitting import text_fit
from deckdna.pptx.opc.package import OpcPackage
from deckdna.providers.base import ModelGateway
from deckdna.providers.factory import build_gateway
from deckdna.providers.openai_compat import OpenAICompatibleProvider, shared_pacer
from deckdna.repair import outcomes as repair_outcomes
from deckdna.repair.apply import apply_repairs
from deckdna.repair.planner import plan_repairs_with_report
from deckdna.research import fill_content
from deckdna.research.config import load_research_config
from deckdna.research.slide import apply_to_plan, find_slide, research_slide
from deckdna.research.web import source_card
from deckdna.settings import settings
from deckdna.template import autopsy
from deckdna.template import dna as dna_builder
from deckdna.template.synthesize import prepare_template_bytes

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
# Request/response DTOs (entity shapes from docs/contracts/DOMAIN_MODEL.md;
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
    # ADR-030: построение плана с добором контента (POST /plans, run_async)
    planning = "planning"
    # ADR-034 §5: пробные слайды трёх стилей
    style_preview = "style_preview"
    autopilot = "autopilot"
    audit = "audit"
    repair = "repair"
    export = "export"
    # ADR-058: ответ помощника в чате
    assistant = "assistant"


class JobError(BaseModel):
    code: str
    message: str
    stage: str | None = None


class IssueOutcome(BaseModel):
    """Исход repair по одной выбранной проблеме (handoff D2)."""

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
    # VK Inference: заголовок Project-ID провайдера (не id проекта DeckDNA)
    provider_project_id: str | None = Field(default=None, max_length=200)

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
    """Session view — токен и рабочие лимиты хранятся в защищённом lease-сторе
    и никогда не возвращаются API."""

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
    # ADR-034 §5: пробные слайды стиля — колода берёт те же образцы
    preview_id: str | None = None
    # ADR-036: opt-in real per-slide draft renders, before deck audit/export.
    progressive_previews: bool = False
    # ADR-033 §9: брендбук (по умолчанию — брендбук проекта из intake) и
    # чья сторона главнее при расхождении
    brandbook_id: str | None = None
    brand_priority: Literal["brandbook", "template"] | None = None

    @model_validator(mode="before")
    @classmethod
    def _user_slides_need_no_evidence(cls, data: Any) -> Any:
        """ADR-030: слайд, добавленный пользователем на экране плана, ещё
        не опирается на факты (``evidence_ids: []``). Схема DeckPlan
        требует хотя бы один id — подставляем ссылку на сам слайд, тело
        допишется перед вёрсткой (planning/complete.py)."""
        plan = data.get("deck_plan") if isinstance(data, dict) else None
        if isinstance(plan, dict) and isinstance(plan.get("slides"), list):
            for slide in plan["slides"]:
                if isinstance(slide, dict) and not slide.get("evidence_ids"):
                    slide["evidence_ids"] = [f"user:{slide.get('id', 'slide')}"]
        return data


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


PipelineStage = Literal["content", "research", "plan", "compose", "audit", "render", "passport"]


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
    # ADR-030: отчёт стадии добора контента этого прогона
    content_fill: dict[str, Any] | None = None


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
    run_async: bool = Field(
        default=False,
        description=(
            "ADR-030: true = build the plan as a background job (202 "
            "{plan_id, job_id}); progress (research queries, sources, "
            "slides) streams from GET /jobs/{job_id}/events, the result "
            "is GET /plans/{plan_id}. false = synchronous 200 PlanSet."
        ),
    )
    respond_async: bool | None = Field(
        default=None,
        description="ADR-034 §0: the frontend's name for run_async; either one enables 202.",
    )
    exclude_source_ids: list[str] = Field(
        default_factory=list,
        description=(
            "ADR-034 §0: research sources (PlanSet.research.sources[].id or URL) "
            "to drop: their facts leave the pack and research skips them."
        ),
    )

    @property
    def is_async(self) -> bool:
        return bool(self.run_async or self.respond_async)


class SlideResearchRequest(BaseModel):
    """ADR-030: точечный ресёрч одного слайда плана."""

    # план какой стратегии менять; по умолчанию — первый в наборе
    strategy: str | None = None
    # numbers — только факты с цифрами (для графика/таблицы), их нельзя
    # брать из знаний модели; facts — любые факты
    focus: Literal["facts", "numbers"] = "facts"
    provider_session_id: str | None = None
    use_llm: bool | None = None


class PlanAccepted(BaseModel):
    plan_id: str
    job_id: str
    events_url: str


class PlanEntry(BaseModel):
    strategy: str
    deck_plan: DeckPlan


class PlanSet(BaseModel):
    plan_id: str
    project_id: str
    # ADR-030: при доборе контента — id нового (добранного) пака; именно
    # его нужно передать в POST /generations вместе с планом
    content_pack_id: str
    plans: list[PlanEntry]
    created_at: datetime
    updated_at: datetime | None = None
    # ADR-030: оценка достаточности контента и отчёт стадии добора
    # (режим, решение, источники). При content_fill=ask и нехватке/избытке
    # контента plans пуст, а needs_decision=true — фронт показывает выбор
    # и повторяет запрос с expand или shrink.
    content_fill: dict[str, Any] | None = None
    # ADR-034 §0: формы, которые читает фронт (F3 плашка, F6 источники)
    sufficiency: dict[str, Any] | None = None
    research: dict[str, Any] | None = None
    # ADR-034 §4: главная мысль колоды (строка над планом)
    key_message: str | None = None
    # шаблон, под который строился план (пробные слайды стиля)
    template_id: str | None = None


class SlideResearchResult(BaseModel):
    plan_set: PlanSet
    slide: SlidePlan
    added_units: int
    research: dict[str, Any]


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
# Application state store with disk snapshot persistence (/data/artifacts/state).
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
    # ADR-034 §6: PNG слайдов сразу после рендера варианта (slide.ready),
    # variant_id -> [(artifact_id, png)]
    early_previews: dict[str, list[tuple[str, bytes]]] = {}
    # Actual output positions (incl. protected/source continuation slides).
    slide_drafts: dict[str, dict[int, dict[str, Any]]] = {}
    changes: dict[str, list[dict[str, Any]]] = {}
    base_ready: dict[str, bool] = {}
    # ADR-033 §9: отчёт брендбука (для паспорта), если он применялся
    brand: dict[str, Any] | None = None
    template_artifact_id: str | None = None


class _Store:
    def __init__(self) -> None:
        self.projects: dict[str, Project] = {}
        self.provider_sessions: dict[str, ProviderSession] = {}
        # Lease-держатель api_token + рабочих полей ProviderSessionCreate
        # (timeout/concurrency не входят в публичную модель сессии).
        # Токен никогда не сериализуется в ответы; семантика lease: TTL + revoke по DELETE.
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
        # ADR-030: журнал событий job'ов, которые пишут прогресс сами
        # (построение плана: запросы → источники → слайды). Для остальных
        # job'ов события по-прежнему синтезируются из состояния.
        self.job_events: dict[str, list[dict[str, Any]]] = {}
        # ADR-030: добранный пак → исходный пак пользователя (цепочка)
        self.filled_from: dict[str, str] = {}
        # ADR-034 §0: plan_id фонового построения → его job (отмена по
        # POST /projects/{id}/plans/{plan_id}/cancel)
        self.plan_jobs: dict[str, str] = {}
        # ADR-034 §3: бриф проекта, собранный из загрузки, ответов на
        # вопросы помощника и запросов плана
        self.project_briefs: dict[str, dict[str, Any]] = {}
        # ADR-034: minimal UI marks; artifacts remain the session source of truth.
        self.assistant_progress: dict[str, dict[str, Any]] = {}
        # Transient, one current result per project; never persisted in snapshots.
        self.understanding_cache: dict[str, tuple[str, dict[str, Any]]] = {}
        # ADR-034 §5: результаты пробных слайдов стиля
        self.style_previews: dict[str, dict[str, Any]] = {}
        # ADR-034 §1: файлы «Закинь, что есть» по проекту
        self.intakes: dict[str, list[dict[str, Any]]] = {}
        # ADR-033 §9: брендбуки; проект → {brandbook_id, priority}
        self.brandbooks: dict[str, Any] = {}
        self.brand_sources: dict[str, list[str]] = {}
        self.brand_analysis: dict[str, dict[str, Any]] = {}
        self.project_brand: dict[str, dict[str, str]] = {}
        # (template_id, brandbook_id, priority) → (байты шаблона, отчёт)
        self.brand_templates: dict[tuple[str, str, str], tuple[bytes, dict[str, Any]]] = {}
        self.artifacts: dict[str, _Artifact] = {}
        self.idempotency: dict[tuple[str, str, str], dict[str, Any]] = {}
        # ADR-058: переписка с помощником по проекту и стек отмены его правок
        # (variant_id / plan_id -> снимки до правки)
        self.agent_threads: dict[str, list[dict[str, Any]]] = {}
        self.agent_undo: dict[str, list[dict[str, Any]]] = {}


STORE = _Store()

# ADR-031 §8: снапшот состояния на диск — перезапуск контейнера во время
# демо больше не стирает проекты, шаблоны, паки и прогоны
_SNAPSHOT_INTERVAL_S = 15.0
_snapshot_thread: threading.Thread | None = None


def _snapshot_path() -> Path:
    return Path(settings.artifact_root) / "state" / "store.pkl"


def _persist() -> None:
    """Записать снапшот, если он включён. Ошибка записи не роняет запрос."""
    if not settings.state_snapshot:
        return
    try:
        save_store(STORE, _snapshot_path(), _JOB_LOCK)
    except Exception as exc:  # noqa: BLE001 — диск полон/недоступен: работаем дальше
        logger.warning("state snapshot failed: %s", exc)


def _ensure_snapshotter() -> None:
    """Фоновые прогоны меняют STORE вне запросов — раз в
    ``_SNAPSHOT_INTERVAL_S`` снапшот пишется и без мутирующего запроса."""
    global _snapshot_thread
    if not settings.state_snapshot or _snapshot_thread is not None:
        return

    def loop() -> None:
        while True:
            threading.Event().wait(_SNAPSHOT_INTERVAL_S)
            _persist()

    _snapshot_thread = threading.Thread(target=loop, name="deckdna-snapshot", daemon=True)
    _snapshot_thread.start()


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
    """Template lookup scoped to *project_id* — hardening check #6: generation/
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
        max_retries=settings.provider_max_retries,
        min_request_interval_s=settings.provider_min_request_interval_s,
        retry_base_delay_s=settings.provider_retry_base_delay_s,
        pacer=shared_pacer(session.base_url, settings.provider_min_request_interval_s),
        extra_headers=(
            {"Project-ID": secret["provider_project_id"]}
            if secret.get("provider_project_id")
            else None
        ),
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
    body: GenerationCreate
    | PlanRequest
    | RepairRequest
    | TemplateAnalysisRequest
    | SlideResearchRequest,
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


def _write_pack_sources(root: Path, pack_id: str) -> Path:
    """Исходные файлы пака во временный каталог; путь первого — content_path."""
    first: Path | None = None
    for artifact_id in STORE.pack_artifacts.get(pack_id, []):
        artifact = STORE.artifacts[artifact_id]
        path = root / artifact.id / artifact.filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(artifact.data)
        first = first or path
    if first is None:
        raise DeckDNAError("invalid_input", f"content pack {pack_id} has no uploaded source file")
    return first


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
    before checking its size (hardening check #20: ``await file.read()``
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
# Default payload factories and MIME constants.
# ---------------------------------------------------------------------------

_PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

# Mirrors the suffix dispatch in ingestion.content_parsers._PARSERS — the
# first uploaded file with one of these becomes the parsed pack source.
# Derived from the ingestion parser registry — stays in sync automatically.
_SUPPORTED_CONTENT_SUFFIXES = set(content_parsers._PARSERS)
_MAX_CONTENT_FILES = 10
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

    hardening check #19: the record was read before ``call_next`` with no
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
    if settings.state_snapshot:
        _ensure_snapshotter()
        is_write = request.method in {"POST", "PATCH", "PUT", "DELETE"}
        response = await _contract_middleware(request, call_next)
        if is_write and response.status_code < 500:
            await asyncio.to_thread(_persist)
        return response
    return await _contract_middleware(request, call_next)


async def _contract_middleware(request: Request, call_next: Any) -> Response:
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
        "provider_project_id": body.provider_project_id or "",
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
    explicit = {}
    if "target_slide_count" in body.model_fields_set:
        explicit["target_slide_count"] = body.target_slide_count
    if "default_language" in body.model_fields_set:
        explicit["language"] = body.default_language
    _remember_brief(project.id, explicit)
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
    from deckdna.api.dependencies import save_brief

    preferences = {k: v for k, v in update.items() if k == "target_slide_count"}
    if "default_language" in update:
        preferences["language"] = update["default_language"]
    if preferences:
        save_brief(project_id, preferences)
    updated = project.model_copy(update={**update, "updated_at": _utcnow()})
    STORE.projects[project_id] = updated
    return _project_view(updated)


@app.delete(f"{API_PREFIX}/projects/{{project_id}}", status_code=204, tags=["projects"])
async def delete_project(project_id: str) -> Response:
    with _JOB_LOCK:
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
    _require(STORE.projects, project_id, "project")
    filename = _checked_upload_filename(file.filename or "template.pptx")
    package_type = filename.rsplit(".", 1)[-1].lower()
    if package_type not in _TEMPLATE_MIME:
        raise DeckDNAError(
            "invalid_input",
            f"unsupported template type: {filename}; expected .pptx or .potx",
            http_status=415,
        )
    data = await _read_upload_limited(file, settings.max_upload_mb)
    # ADR-031 §2–3: .potx хранится с типом основной части .pptx, шаблон без
    # слайдов — с образцами из макетов: autopsy, Design DNA, вёрстка, рендер
    # и экспорт видят один и тот же пакет
    return _store_template(project_id, filename, package_type, data)


def _store_template(
    project_id: str, filename: str, package_type: str, data: bytes,
) -> TemplateAsset:
    """Проверить и сохранить шаблон проекта (загрузка и ADR-034 §1 intake)."""
    project = _require(STORE.projects, project_id, "project")
    data, _synthesis = prepare_template_bytes(data)
    # hardening check #9: previously any bytes with a .pptx/.potx extension
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
    cached_desc: dict[str, Any] | None = None
    cached_bundle = dna_builder.load_cached_template_analysis(
        artifact.data,
        template_id,
        analysis_id,
        lambda: _new_id("tsl"),
        _utcnow(),
        SCHEMA_VERSION,
    )
    if cached_bundle is not None:
        forensics, result, slide_ids, cached_desc = cached_bundle
        dna, slide_meta, features = result.design_dna, result.slide_meta, result.features
    else:
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
    descriptions = cached_desc
    if descriptions is None and gateway is not None and dna_builder.can_describe(gateway):
        descriptions = await dna_builder.describe_exemplars(
            features, gateway, package_bytes=artifact.data
        )
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
        parser_version=autopsy.ANALYZER_VERSION,
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
    _require(STORE.projects, project_id, "project")
    # ADR-032 §2: несколько файлов — один пак; каждый файл разбирается своим
    # парсером со своим artifact_id, ссылки и source_ref остаются раздельными
    if not files:
        raise DeckDNAError("invalid_input", "content pack upload requires a file", http_status=422)
    if len(files) > _MAX_CONTENT_FILES:
        raise DeckDNAError(
            "invalid_input",
            f"content pack upload accepts at most {_MAX_CONTENT_FILES} files",
            http_status=422,
        )
    parsed_brief: dict[str, Any] = {}
    if brief:
        try:
            parsed_brief = json.loads(brief)
        except json.JSONDecodeError:
            raise DeckDNAError("invalid_input", "brief form field must be valid JSON") from None
    uploads: list[tuple[str, bytes, str]] = []
    for file in files:
        filename = _checked_upload_filename(file.filename or "content.bin")
        if Path(filename).suffix.lower() not in _SUPPORTED_CONTENT_SUFFIXES:
            # раньше лишний файл молча сохранялся — ответ не должен
            # утверждать, что в пак вошло то, что не разобрано
            raise DeckDNAError(
                "invalid_input",
                f"unsupported content file: {filename}; expected one of "
                f"{sorted(_SUPPORTED_CONTENT_SUFFIXES)}",
            )
        # hardening check #20: content-pack upload had no size limit at all
        # (unlike upload_template's max_upload_mb check) -- an arbitrarily
        # large file here would be read fully into memory.
        data = await _read_upload_limited(file, settings.max_upload_mb)
        uploads.append((filename, data, file.content_type or "application/octet-stream"))
    return _ingest_uploads(project_id, uploads, parsed_brief)


def _ingest_uploads(
    project_id: str, uploads: list[tuple[str, bytes, str]], parsed_brief: dict[str, Any],
) -> ContentPackAccepted:
    """Разобрать файлы в один пак (загрузка контента и ADR-034 §1 intake)."""
    project = _require(STORE.projects, project_id, "project")
    from deckdna.assistant.brief import normalize_brief

    parsed_brief = normalize_brief(parsed_brief, title_hint=True)
    # разбор до сохранения: битый файл не оставляет артефактов
    parsed: list[tuple[_Artifact, ContentPack]] = []
    with tempfile.TemporaryDirectory(prefix="deckdna-ingest-") as tmpdir:
        for filename, data, mime in uploads:
            artifact = _Artifact(
                id=_new_id("art"), project_id=project_id, type="content.original",
                mime_type=mime, filename=filename,
                sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data),
                created_at=_utcnow(), data=data,
            )
            src_path = Path(tmpdir) / artifact.id / filename
            src_path.parent.mkdir(parents=True, exist_ok=True)
            src_path.write_bytes(data)
            parsed.append((artifact, parse_file(src_path, artifact_id=artifact.id)))
    for artifact, _pack in parsed:
        STORE.artifacts[artifact.id] = artifact
    pack = merge_packs([p for _, p in parsed])
    chosen_ids = [artifact.id for artifact, _ in parsed]
    updates: dict[str, Any] = {}
    updates["language"] = str(parsed_brief.get("language")
                              or STORE.project_briefs.get(project_id, {}).get("language")
                              or project.default_language)
    if parsed_brief.get("title_hint"):
        updates["title_hint"] = str(parsed_brief["title_hint"])
    _remember_brief(project_id, parsed_brief)
    if updates:
        pack = pack.model_copy(update=updates)
    # The parser's ID may be a short content hash or user-controlled JSON.
    # API identity belongs to this upload, not to its bytes or supplied ID.
    pack = pack.model_copy(update={"id": _new_id("pack")})
    STORE.pack_artifacts[pack.id] = chosen_ids
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
# ADR-034 §2–§3: «Вот что я понял» и «Пара вопросов»
# ---------------------------------------------------------------------------

_BRIEF_KEYS = (*Brief.model_fields, "title_hint")


def _remember_brief(project_id: str, brief: dict[str, Any], *, source: str = "user") -> None:
    from deckdna.assistant.brief import normalize_brief

    brief = normalize_brief(brief, title_hint=True)
    known = STORE.project_briefs.setdefault(project_id, {})
    if ("target_slide_count" in brief and "requested_slide_count" not in brief
            and known.get("slide_count_policy") in ("exact", "maximum")):
        brief["requested_slide_count"] = brief["target_slide_count"]
    marks = STORE.assistant_progress.setdefault(project_id, {})
    origins = marks.setdefault("brief_origins", {})
    for key in _BRIEF_KEYS:
        value = brief.get(key)
        if value not in (None, ""):
            was_known = key in known
            known[key] = value
            if source != "default" or (key not in origins and not was_known):
                origins[key] = {"source": source,
                                "context_revision": marks.get("context_revision", 1)}


def _pack_project(pack_id: str) -> str:
    owners = sorted(STORE.content_pack_projects.get(pack_id) or [])
    if not owners:
        raise DeckDNAError("not_found", f"content pack not found: {pack_id}")
    return owners[0]


def _brief_model(project_id: str, pack: ContentPack | None) -> Brief:
    known = STORE.project_briefs.get(project_id, {})
    return Brief.model_validate({
        "purpose": "", "audience": "", "language": (pack.language if pack else "ru") or "ru",
        "target_slide_count": 8, "content_fill": "expand",
        **{k: v for k, v in known.items() if k in Brief.model_fields and v is not None},
    })


def _apply_local_context(project_id: str, pack: ContentPack) -> list[dict[str, Any]]:
    """Apply grounded rules without caching or spending a provider call."""
    from deckdna.assistant.context import context_sources, extract_context_rules

    if STORE.projects[project_id].content_pack_id != pack.id:
        return []
    known = STORE.project_briefs.setdefault(project_id, {})
    marks = STORE.assistant_progress.setdefault(project_id, {})
    evidence = extract_context_rules(context_sources(pack, known), known)
    for entry in evidence:
        known[entry["field"]] = entry["value"]
        marks.setdefault("brief_origins", {})[entry["field"]] = {
            "source": "inferred", "method": "rule", "source_id": entry["source_id"],
            "quote": entry["quote"], "context_revision": marks.get("context_revision", 1),
        }
    return evidence


class BlockOps(BaseModel):
    ops: list[dict[str, Any]] = Field(min_length=1)


class InsightPatch(BaseModel):
    confirmed: bool | None = None
    # правка формулировки: заголовок-вывод (числа пользователь меняет сам)
    text: str | None = None
    meaning: str | None = None
    implication: str | None = None


class NotesBody(BaseModel):
    items: list[str] = Field(min_length=1, max_length=10)


class AnswersBody(BaseModel):
    model_config = {"extra": "forbid"}

    answers: dict[str, Any]
    expected_context_revision: int | None = Field(default=None, ge=1)


def _new_pack_version(pack_id: str, pack: ContentPack) -> dict[str, Any]:
    project_id = _pack_project(pack_id)
    new_id = _register_filled_pack(pack_id, pack, project_id)
    project = STORE.projects.get(project_id)
    if project is not None:
        STORE.projects[project_id] = project.model_copy(
            update={"content_pack_id": new_id, "updated_at": _utcnow()}
        )
    new_pack = STORE.content_packs[new_id]
    return {
        "content_pack_id": new_id,
        "filled_from": pack_id,
        "understanding": build_understanding(new_pack, _brief_model(project_id, new_pack)),
    }


class _UnderstandingOptions(BaseModel):
    use_llm: bool | None = None
    provider_session_id: str | None = None


_LISTENING_ACTIVE: set[tuple[int, str]] = set()
_INTERPRETATION_ACTIVE: set[tuple[int, str]] = set()


async def _listen_context(
    project_id: str, pack: ContentPack, options: _UnderstandingOptions,
) -> dict[str, Any]:
    from deckdna.assistant.context import (
        context_key,
        context_sources,
        extract_context,
    )

    known = dict(STORE.project_briefs.get(project_id, {}))
    marks = STORE.assistant_progress.setdefault(project_id, {})
    if STORE.projects[project_id].content_pack_id != pack.id:
        return {"status": "not_current", "fields": []}
    sources = context_sources(pack, known)
    route = (options.provider_session_id, settings.provider_base_url, settings.model_text,
             settings.mock_provider, _server_provider_ready())

    def key_for(brief: dict[str, Any]) -> str:
        return hashlib.sha256((context_key(context_sources(pack, brief), brief)
                              + repr(route)).encode()).hexdigest()

    key = key_for(known)
    previous = marks.get("listening", {})
    if previous.get("key") == key:
        return previous
    # Сначала правила без модели: явное «для совета директоров» в словах
    # автора читается детерминированно — вопрос об аудитории не нужен
    # и не зависит от доступности провайдера (ADR-035).
    rule_fields = _apply_local_context(project_id, pack)
    known = dict(STORE.project_briefs.get(project_id, {}))
    explicit_length_in_text = bool(re.search(
        r"\b\d+[\s-]*(?:слайд\w*|slides?)\b", " ".join(sources.values()), re.I,
    ))
    if (known.get("audience") and known.get("purpose")
            and (known.get("target_slide_count") or not explicit_length_in_text)):
        result = {"status": "known", "fields": rule_fields, "key": key_for(known)}
        marks["listening"] = result
        return result
    if not sources or options.use_llm is False:
        if rule_fields:
            result = {"status": "reviewed", "fields": rule_fields,
                      "key": key_for(known)}
            marks["listening"] = result
            return result
        return {"status": "not_requested", "fields": []}
    token = (id(STORE), project_id)
    with _JOB_LOCK:
        if token in _LISTENING_ACTIVE:
            return {"status": "pending", "fields": []}
        _LISTENING_ACTIVE.add(token)
    gateway = None
    try:
        gateway = _gateway_for(options, project_id)
        found, evidence, status = await extract_context(sources, known, gateway)
        if STORE.projects[project_id].content_pack_id != pack.id:
            return {"status": "not_current", "fields": []}
        # Другой запрос мог сохранить явный ответ, пока модель работала.
        latest = STORE.project_briefs.setdefault(project_id, {})
        accepted = [e for e in evidence if latest.get(e["field"]) in (None, "")]
        for entry in accepted:
            latest[entry["field"]] = found[entry["field"]]
            marks.setdefault("brief_origins", {})[entry["field"]] = {
                "source": "inferred", "method": "model", "source_id": entry["source_id"],
                "quote": entry["quote"], "context_revision": marks.get("context_revision", 1),
            }
        if rule_fields and status == "unavailable":
            status = "reviewed"
        result = {"status": status, "fields": [*rule_fields, *accepted],
                  "key": key_for(latest)}
        marks["listening"] = result
        return result
    finally:
        with _JOB_LOCK:
            _LISTENING_ACTIVE.discard(token)
        close = getattr(gateway, "aclose", None)
        if close is not None:
            await close()


async def _interpret_understanding(
    pack_id: str, pack: ContentPack, brief: Brief, options: _UnderstandingOptions,
) -> tuple[ContentPack, dict[str, Any]]:
    from deckdna.analysis.interpret import INTERPRET_PROMPT, interpret_pack
    from deckdna.providers.prompts import load_prompt

    project_id = _pack_project(pack_id)
    marks = STORE.assistant_progress.setdefault(project_id, {})
    payload = {
        "pack": pack_id,
        "brief": {k: getattr(brief, k) for k in ("purpose", "audience", "key_message", "language")},
        "facts": [(i.id, i.fact, i.numbers, i.headline) for i in pack.insights],
        "prompt": load_prompt(INTERPRET_PROMPT).version,
        "provider": (options.provider_session_id, settings.provider_base_url, settings.model_text,
                     settings.mock_provider, _server_provider_ready()),
    }
    key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    previous = marks.get("interpretation", {})
    if previous.get("key") == key:
        return pack, {"status": "cached", "report": previous["report"]}
    token = (id(STORE), pack_id)
    with _JOB_LOCK:
        if token in _INTERPRETATION_ACTIVE:
            return pack, {"status": "pending"}
        _INTERPRETATION_ACTIVE.add(token)
    gateway = None
    try:
        gateway = _gateway_for(options, project_id)
        changed, report = await interpret_pack(pack, brief, gateway)
        result = report.to_dict()
        with _JOB_LOCK:
            latest = STORE.content_packs[pack_id]
            current_brief = _brief_model(project_id, latest)
            if any(getattr(current_brief, k) != getattr(brief, k)
                   for k in ("purpose", "audience", "key_message", "language")):
                return latest, {"status": "superseded"}
            original = {i.id: i for i in pack.insights}
            generated = {i.id: i for i in changed.insights}
            # Автор мог подтвердить/поправить вывод, пока шёл запрос модели.
            # Более поздняя правка всегда выигрывает целиком.
            merged = [generated.get(i.id, i) if i == original.get(i.id) else i
                      for i in latest.insights]
            changed = latest.model_copy(update={"insights": merged})
            STORE.content_packs[pack_id] = changed
            records = marks.setdefault("interpretation_outputs", {}).setdefault(pack_id, {})
            for insight in merged:
                before = original.get(insight.id)
                if before is not None and before != insight:
                    existing = records.get(insight.id)
                    records[insight.id] = {
                        "before": (existing["before"] if existing
                                   else before.model_dump(mode="json")),
                        "after": insight.model_dump(mode="json"),
                    }
            marks["interpretation"] = {"key": key, "report": result}
            return changed, {"status": "processed", "report": result}
    finally:
        with _JOB_LOCK:
            _INTERPRETATION_ACTIVE.discard(token)
        close = getattr(gateway, "aclose", None)
        if close is not None:
            await close()


def _understanding_key(project_id: str, pack: ContentPack, options: dict) -> str:
    project = STORE.projects[project_id]
    brand = STORE.project_brand.get(project_id, {})
    book = STORE.brandbooks.get(brand.get("brandbook_id"))
    marks = STORE.assistant_progress.get(project_id, {})
    payload = {
        "content_pack_id": pack.id, "pack": pack.model_dump(mode="json"),
        "template_id": project.template_id, "brand": brand,
        "brandbook": book.model_dump(mode="json") if book is not None else None,
        "brief": STORE.project_briefs.get(project_id, {}),
        "origins": marks.get("brief_origins", {}),
        "deferred": marks.get("deferred_questions_pack_id"), "options": options,
        "context": marks.get("listening"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@app.get(f"{API_PREFIX}/projects/{{project_id}}/understanding", tags=["assistant"])
async def get_project_understanding(
    project_id: str, use_llm: bool | None = None, provider_session_id: str | None = None,
) -> dict[str, Any]:
    project = _require(STORE.projects, project_id, "project")
    if not project.content_pack_id:
        raise DeckDNAError("invalid_input", "upload content first", http_status=422)
    return await get_understanding(project.content_pack_id, use_llm, provider_session_id)


@app.get(f"{API_PREFIX}/content-packs/{{pack_id}}/understanding", tags=["assistant"])
async def get_understanding(
    pack_id: str, use_llm: bool | None = None, provider_session_id: str | None = None,
) -> dict[str, Any]:
    """ADR-034 §2: смысловые блоки, сводка и датасеты с выводами.

    С моделью (``use_llm`` / ``provider_session_id``, как у /plans) выводы
    данных получают «что значит» и «что следует» (ADR-033 §5, числа
    сверены с фактом); результат сохраняется в паке."""
    pack = _require(STORE.content_packs, pack_id, "content pack")
    project_id = _pack_project(pack_id)
    if provider_session_id is not None:
        _require_session(provider_session_id, project_id)
    context = await _listen_context(
        project_id, pack, _UnderstandingOptions(
            use_llm=use_llm, provider_session_id=provider_session_id,
        ),
    )
    cache_options = {"use_llm": use_llm, "provider_session_id": provider_session_id}
    cache_key = _understanding_key(project_id, pack, cache_options)
    cached = STORE.understanding_cache.get(project_id)
    if cached and cached[0] == cache_key:
        STORE.assistant_progress.setdefault(project_id, {})["understood_pack_id"] = pack_id
        _persist()
        result = copy.deepcopy(cached[1])
        if result["interpretation"]["status"] == "processed":
            result["interpretation"]["status"] = "cached"
        return result
    brief = _brief_model(project_id, pack)
    marks = STORE.assistant_progress.setdefault(project_id, {})
    question_policy = build_questions(
        pack, STORE.project_briefs.get(project_id, {}),
        defer_questions=marks.get("deferred_questions_pack_id") == pack_id,
    )
    interpretation = {"status": "not_requested"}
    if pack.datasets and (use_llm or provider_session_id) and question_policy["questions"]:
        interpretation = {"status": "deferred", "reason": "Уточню задачу перед интерпретацией."}
    elif pack.datasets and (use_llm or provider_session_id):
        pack, interpretation = await _interpret_understanding(
            pack_id, pack, brief,
            _UnderstandingOptions(use_llm=use_llm, provider_session_id=provider_session_id),
        )
    api_marks = STORE.assistant_progress.setdefault(project_id, {})
    api_marks["understood_pack_id"] = pack_id
    _persist()
    result = build_understanding(pack, brief)
    result["context"] = {k: v for k, v in context.items() if k != "key"}
    result["brief"] = dict(STORE.project_briefs.get(project_id, {}))
    result["brief_origins"] = dict(api_marks.get("brief_origins", {}))
    result["interpretation"] = interpretation
    if (interpretation["status"] not in ("pending", "superseded")
            and context.get("status") != "pending"):
        key = _understanding_key(project_id, pack, cache_options)
        STORE.understanding_cache[project_id] = (key, copy.deepcopy(result))
    return result


@app.post(f"{API_PREFIX}/projects/{{project_id}}/research", tags=["assistant"])
async def research_project(
    project_id: str, body: _UnderstandingOptions | None = None,
) -> dict[str, Any]:
    """Explicit web research; zero additions never claim successful enrichment."""
    from deckdna.research.fill import merge_into_pack
    from deckdna.research.web import WebResearch, research_web

    body = body or _UnderstandingOptions()
    project = _require(STORE.projects, project_id, "project")
    pack = _require(STORE.content_packs, project.content_pack_id, "content pack")
    if body.provider_session_id is not None:
        _require_session(body.provider_session_id, project_id)
    cfg = load_research_config()
    gateway = None
    try:
        if not cfg.web.available:
            found = WebResearch(degraded="web_disabled")
        else:
            gateway = _gateway_for(body, project_id)
            found = await research_web(
                pack, _brief_model(project_id, pack), gateway, cfg.web,
                missing_slides=1, covered_topics=[s.heading for s in pack.sections if s.heading],
                deadline=time.monotonic() + cfg.budget_s,
            )
        result = found.to_dict()
        result["content_pack_id"] = pack.id
        if found.facts_kept and found.sections:
            if STORE.projects[project_id].content_pack_id != pack.id:
                raise DeckDNAError(
                    "invalid_input", "content changed during research", http_status=409,
                )
            sections = [s.model_copy(update={"id": _new_id("sec-web")}) for s in found.sections]
            changed = merge_into_pack(pack, sections, found.sources, pack.language or "ru")
            result.update(_new_pack_version(pack.id, changed))
        return result
    finally:
        close = getattr(gateway, "aclose", None)
        if close is not None:
            await close()


@app.patch(f"{API_PREFIX}/content-packs/{{pack_id}}/blocks", tags=["assistant"])
async def patch_blocks(pack_id: str, body: BlockOps) -> dict[str, Any]:
    """ADR-034 §2: правка блоков → новая версия пака (цепочка ``filled_from``)."""
    pack = _require(STORE.content_packs, pack_id, "content pack")
    return _new_pack_version(pack_id, apply_block_ops(pack, body.ops))


@app.patch(f"{API_PREFIX}/content-packs/{{pack_id}}/insights/{{insight_id}}", tags=["assistant"])
async def patch_insight(pack_id: str, insight_id: str, body: InsightPatch) -> Insight:
    """ADR-033 §5: подтверждение или правка вывода. Подтверждённый вывод
    поднимается выше в «Ключевых выводах», его следствие идёт в «Что
    предлагаем»."""
    pack = _require(STORE.content_packs, pack_id, "content pack")
    insight = next((i for i in pack.insights if i.id == insight_id), None)
    if insight is None:
        raise DeckDNAError("not_found", f"insight not found: {insight_id}")
    update: dict[str, Any] = {}
    if body.confirmed is not None:
        update["confirmed"] = body.confirmed
        if insight.implication:
            update["needs_confirmation"] = not body.confirmed
    if body.text is not None and body.text.strip():
        update["headline"] = body.text.strip()
        update["confirmed"] = True
    for key in ("meaning", "implication"):
        value = getattr(body, key)
        if value is not None:
            update[key] = value.strip() or None
    changed = insight.model_copy(update=update)
    STORE.content_packs[pack_id] = pack.model_copy(update={
        "insights": [changed if i.id == insight_id else i for i in pack.insights]
    })
    return changed


@app.post(f"{API_PREFIX}/content-packs/{{pack_id}}/notes", tags=["assistant"])
async def add_pack_notes(pack_id: str, body: NotesBody) -> dict[str, Any]:
    """ADR-034 §3: «Я добавлю пару тезисов» → новая версия пака."""
    pack = _require(STORE.content_packs, pack_id, "content pack")
    return _new_pack_version(pack_id, add_notes(pack, body.items))


@app.get(f"{API_PREFIX}/projects/{{project_id}}/questions", tags=["assistant"])
async def get_questions(project_id: str) -> dict[str, Any]:
    """ADR-034 §3: вопросы только по причине; пустой список — шаг пропускается."""
    project = _require(STORE.projects, project_id, "project")
    pack = STORE.content_packs.get(project.content_pack_id) if project.content_pack_id else None
    if pack is not None:
        _apply_local_context(project_id, pack)
    brief = dict(STORE.project_briefs.get(project_id, {}))
    marks = STORE.assistant_progress.get(project_id, {})
    return build_questions(
        pack, brief, web_available=load_research_config().web.available,
        defer_questions=marks.get("deferred_questions_pack_id") == project.content_pack_id,
    )


@app.post(f"{API_PREFIX}/projects/{{project_id}}/answers", tags=["assistant"])
async def post_answers(project_id: str, body: AnswersBody) -> dict[str, Any]:
    """ADR-034 §3: ответы → бриф проекта (``audience``, ``tone``,
    ``target_slide_count``, ``content_fill``, ``key_message``,
    ``call_to_action``). Бриф отдаётся целиком — фронт шлёт его в /plans."""
    project = _require(STORE.projects, project_id, "project")
    from deckdna.assistant.brief import answered_question_ids, normalize_answers

    values, follow_up = normalize_answers(body.answers)
    revision = STORE.assistant_progress.get(project_id, {}).get("context_revision", 1)
    if body.expected_context_revision is not None and body.expected_context_revision != revision:
        raise DeckDNAError("state_conflict", "project context changed", http_status=409,
                           details={"actual_revision": revision})
    pack = STORE.content_packs.get(project.content_pack_id) if project.content_pack_id else None
    if pack is not None:
        _apply_local_context(project_id, pack)
    known = STORE.project_briefs.get(project_id, {})
    policy = build_questions(pack, known)
    brief = {**known, **values}
    full = {
        **policy["suggested_brief"],
        **brief,
        "purpose": brief.get("purpose") or "",
        "audience": brief.get("audience") or "",
        "language": brief.get("language") or (pack.language if pack else "ru") or "ru",
        "target_slide_count": int(brief.get("target_slide_count")
                                  or policy["suggested_brief"]["target_slide_count"]),
        **{k: brief[k] for k in ("tone", "content_fill", "key_message", "call_to_action")
           if brief.get(k)},
    }
    from deckdna.api.dependencies import save_brief

    if "target_slide_count" in values and "requested_slide_count" not in values:
        full.pop("requested_slide_count", None)
    saved = save_brief(project_id, full, expected=body.expected_context_revision,
                       origins={k: "user" if k in values else "default" for k in full
                                if k in values or k not in known})
    full = saved["brief"]
    STORE.assistant_progress.setdefault(project_id, {})["answered_pack_id"] = (
        project.content_pack_id
    )
    visible = {q["id"] for q in policy["questions"]}
    provided = answered_question_ids(values)
    if not body.answers or visible <= provided:
        STORE.assistant_progress[project_id]["deferred_questions_pack_id"] = project.content_pack_id
        if full.get("content_fill") == "ask":
            full["content_fill"] = "expand"
    _persist()
    remaining = build_questions(
        pack, full,
        defer_questions=STORE.assistant_progress[project_id].get("deferred_questions_pack_id")
        == project.content_pack_id,
    )["questions"]
    note = (
        "Добавьте тезисы, учту их в плане." if follow_up.get("notes")
        else "Учёл ответ. Осталось существенное уточнение." if remaining
        else "Учёл. Можно составлять план."
    )
    return {**saved, "brief": full, "follow_up": follow_up, "assistant_note": note}


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


_STAGE_NOTES = {
    "research": "Ищу недостающие факты…",
    "content": "Готовлю текст слайдов…",
    "planning": "Раскладываю материал по слайдам…",
    "composing.deck": "Собираю {n} {slides}…",
    "audit.basic": "Проверяю читаемость…",
    "audit.contextual": "Сверяю смысл слайдов с материалом…",
    "exporting.render": "Рисую слайды…",
    "evaluation.quality_passport": "Готовлю паспорт качества…",
}


def _stage_note(stage: str, n: int | None) -> str | None:
    from deckdna.analysis.values import plural

    note = _STAGE_NOTES.get(stage)
    if note and "{n}" in note:
        if not n:
            return "Собираю слайды…"
        return note.format(n=n, slides=plural(n, ("слайд", "слайда", "слайдов")))
    return note


def _patch_variant(run: _InternalRun, variant_id: str, **fields: Any) -> None:
    with _JOB_LOCK:
        before = next((v for v in run.detail.variants if v.id == variant_id), None)
        variants = [
            v.model_copy(update=fields) if v.id == variant_id else v
            for v in run.detail.variants
        ]
        run.detail = run.detail.model_copy(update={"variants": variants})
    stage = fields.get("stage")
    if (stage and (before is None or before.stage != stage)
            and run.detail.job_id in STORE.job_events):
        plan = run.variant_plans.get(variant_id) or run.supplied_plans.get(
            run.variant_strategy.get(variant_id, "")
        )
        _journal(run.detail.job_id, "stage.started", {
            "stage": stage, "variant_id": variant_id,
            "assistant_note": _stage_note(stage, len(plan.slides) if plan else None),
        })


def _cascade_delete_project(project_id: str) -> None:
    """hardening check #18: DELETE /projects/{id} used to remove only the
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
    STORE.assistant_progress.pop(project_id, None)
    STORE.understanding_cache.pop(project_id, None)
    STORE.agent_threads.pop(project_id, None)
    STORE.project_brand.pop(project_id, None)
    for bid in [b.id for b in STORE.brandbooks.values() if b.project_id == project_id]:
        STORE.brandbooks.pop(bid, None)
        STORE.brand_sources.pop(bid, None)
        STORE.brand_analysis.pop(bid, None)
        STORE.brand_templates = {k: v for k, v in STORE.brand_templates.items() if k[1] != bid}
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
            from deckdna.planning.request_constraints import validate_deck_count, validate_pdf_count

            plan = run.variant_plans.get(variant_id) or run.detail.deck_plan
            briefs = [run.request.brief, *([plan.brief] if plan is not None else [])]
            physical_count = None
            for brief in briefs:
                physical_count = validate_deck_count(brief, src) or physical_count
            pdf = render_pdf(src, Path(tmp) / "deck.pdf")
            for brief in briefs:
                validate_pdf_count(brief, pdf, physical_count)
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
            tpl_path.write_bytes(_run_template_bytes(run))
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
                content_fill=info.get("content_fill"),
                speaker_notes=info.get("speaker_notes"),
            )
            if info.get("analysis"):
                passport.analysis = info["analysis"]
            if run.brand:
                passport.brand = run.brand
            passport.deck_revision = revision
            contextual_status = STORE.audits[audit_id].contextual_status
            if contextual_status in {"pending", "running", "failed"}:
                passport.fallbacks = [*(passport.fallbacks or []), Fallback(
                    feature="audit.contextual.coverage", strategy=contextual_status,
                    disclosure=(f"Contextual audit status for deck revision {revision}: "
                                f"{contextual_status}; complete coverage is not verified."),
                )]
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
    """HTML-просмотрщик (zip: index.html + assets/) текущей ревизии (D5)."""
    with _variant_lock(variant_id):
        arts = run.variant_artifacts.setdefault(variant_id, {})
        if "html" in arts:
            return STORE.artifacts[arts["html"]]
        deck = _current_deck(run, variant_id)
        revision = run.deck_revision.get(variant_id, 1)
        strategy = run.variant_strategy.get(variant_id, "deck")
        with tempfile.TemporaryDirectory(prefix="deckdna-lazy-html-") as tmp:
            deck_path = Path(tmp) / deck.filename
            deck_path.write_bytes(deck.data)
            data = _previews.html_bundle_zip(
                deck_path,
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
        from deckdna.api.dependencies import is_current

        if not is_current(project_id, plan_set.created_at):
            raise DeckDNAError("state_conflict", "plan belongs to an older project brief",
                               http_status=409)
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
        from deckdna.planning.mandatory import protect_mandatory_sections

        pack = STORE.content_packs[body.content_pack_id]
        required = body.brief.mandatory_sections
        return {v.strategy: protect_mandatory_sections(by_strategy[v.strategy], pack,
                                                       required=required)
                for v in body.variants}, "plan_set"
    if body.deck_plan is not None:
        known = STORE.project_briefs.get(project_id, {})
        if known.get("slide_count_policy") in ("exact", "maximum"):
            for field in ("slide_count_policy", "requested_slide_count"):
                if known.get(field) != getattr(body.deck_plan.brief, field):
                    raise DeckDNAError(
                        "state_conflict", "inline plan changes the count requirement",
                        http_status=409,
                    )
        for field in ("purpose", "audience", "language", "key_message", "call_to_action",
                      "mandatory_sections"):
            if known.get(field) and known[field] != getattr(body.deck_plan.brief, field):
                raise DeckDNAError("state_conflict", "inline plan contradicts the current brief",
                                   http_status=409)
        if not body.deck_plan.slides:
            raise DeckDNAError("invalid_input", "deck_plan has no slides")
        from deckdna.planning.mandatory import protect_mandatory_sections

        required = [*(known.get("mandatory_sections") or []),
                    *(body.brief.mandatory_sections or [])]
        plan = protect_mandatory_sections(body.deck_plan, STORE.content_packs[body.content_pack_id],
                                          required=required)
        return {v.strategy: plan for v in body.variants}, "user"
    return {}, "generated"


def _create_generation(
    project: Project, body: GenerationCreate, parent_run_id: str | None,
    *, job_id: str | None = None,
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
    job_id = job_id or _new_id("job")
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
    existing_job = STORE.jobs.get(job_id)
    if existing_job is None:
        job = Job(
            id=job_id,
            kind=JobKind.generation,
            state=JobState.queued,
            progress=0.0,
            project_id=project.id,
            created_at=now,
            # ADR-032: агент (MCP get_job) находит прогон по job id
            result_ids={"generation_id": run_id},
        )
    else:
        job = existing_job.model_copy(update={"result_ids": {
            **existing_job.result_ids, "generation_id": run_id,
        }})
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
    # ADR-034 §6: генерация пишет журнал событий (stage.started со строкой
    # помощника, slide.ready по мере рендера, variant.completed)
    if existing_job is None:
        _journal(job_id, "job.state_changed", {"state": "queued", "generation_id": run_id})
    else:
        _journal(job_id, "generation.started", {"generation_id": run_id})
    return run


def _progressive_slide_event(
    run: _InternalRun, variant_id: str, event_type: str, payload: dict[str, Any],
) -> None:
    with _JOB_LOCK:
        job = STORE.jobs.get(run.detail.job_id)
        if job is None or job.state in _TERMINAL_JOB_STATES:
            return
        data = {k: v for k, v in payload.items() if k != "png"}
        data.update(variant_id=variant_id, slide_key=f"{variant_id}:{data['index']}")
        if event_type == "slide.preview_failed":
            data["status"] = "preview_failed"
        if "png" in payload:
            artifact = _store_artifact(
                payload["png"], type="slide.preview.png", mime_type="image/png",
                filename=f"{variant_id}_draft_slide{data['index']}.png",
                project_id=run.detail.project_id,
            )
            data.update(preview_artifact_id=artifact.id, preview_revision=1)
        previous = run.slide_drafts.setdefault(variant_id, {}).get(data["index"], {})
        run.slide_drafts[variant_id][data["index"]] = {**previous, **data}
        if any(c["state"] in {"queued", "running"}
               for c in run.changes.get(variant_id, [])):
            data["status"] = "superseded"
            run.slide_drafts[variant_id][data["index"]]["status"] = "superseded"
        # Existing event name, with explicit draft/final status for new clients.
        _journal(run.detail.job_id,
                 "slide.ready" if event_type == "slide.draft_ready" else event_type, data)


def _slides_ready(run: _InternalRun, variant_id: str, plan: DeckPlan, pdf: Path) -> None:
    """ADR-034 §6: PNG каждого слайда — сразу после рендера варианта, до
    паспорта и экспорта; событие ``slide.ready`` на слайд."""
    if STORE.jobs[run.detail.job_id].state == JobState.canceled:
        return
    pngs = _previews.pdf_page_pngs(pdf)
    strategy = run.variant_strategy.get(variant_id, "deck")
    early: list[tuple[str, bytes]] = []
    total = len(pngs)
    for k, png in enumerate(pngs):
        art = _store_artifact(
            png, type="slide.preview.png", mime_type="image/png",
            filename=f"{strategy}_rev1_slide{k + 1}.png", project_id=run.detail.project_id,
        )
        early.append((art.id, png))
        draft = run.slide_drafts.get(variant_id, {}).get(k + 1)
        slide_id = draft["slide_id"] if draft else (
            plan.slides[k].id if k < len(plan.slides) else None
        )
        event = {
            "variant_id": variant_id, "index": k + 1, "slide_id": slide_id,
            "slide_key": f"{variant_id}:{k + 1}", "revision": 1,
            "status": "rendered", "preview_revision": 2 if draft else 1,
            "preview_artifact_id": art.id,
            "assistant_note": f"Готово {k + 1} из {total}…",
        }
        with _JOB_LOCK:
            if STORE.jobs[run.detail.job_id].state == JobState.canceled:
                return
            if any(c["state"] in {"queued", "running"}
                   for c in run.changes.get(variant_id, [])):
                event["status"] = "superseded"
            if draft:
                run.slide_drafts[variant_id][k + 1] = event
            _journal(run.detail.job_id, "slide.ready", event)
    run.early_previews[variant_id] = early


def _attach_early_previews(run: _InternalRun, variant_id: str) -> None:
    """Превью из ``slide.ready`` — слайдам варианта и монтажу (второй раз
    PDF в PNG не переводится)."""
    early = run.early_previews.pop(variant_id, None)
    slides = STORE.slides.get(variant_id) or []
    if not early or len(early) < len(slides):
        return
    STORE.slides[variant_id] = [
        sl.model_copy(update={"preview_artifact_id": early[k][0], "revision": 1})
        for k, sl in enumerate(slides)
    ]
    strategy = run.variant_strategy.get(variant_id, "deck")
    montage = _store_artifact(
        _previews.montage_png([png for _, png in early]),
        type="montage.png", mime_type="image/png",
        filename=f"{strategy}_rev1_montage.png", project_id=run.detail.project_id,
    )
    _patch_variant(run, variant_id, montage_artifact_id=montage.id)


def _preview_overrides(body: GenerationCreate, strategy: str) -> dict[str, str] | None:
    """Образцы пробных слайдов этого стиля (ADR-034 §5)."""
    if not body.preview_id:
        return None
    record = STORE.style_previews.get(body.preview_id)
    if record is None:
        return None
    style = next((s for s in record["styles"] if s["strategy"] == strategy), None)
    return dict(style["exemplars"]) if style else None


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
            recorded_plan = run.variant_plans.get(detail.variants[0].id, recorded_plan)
            job = job.model_copy(
                update={
                    "state": JobState.completed,
                    "progress": 1.0,
                    "finished_at": now,
                    "result_ids": {
                        **job.result_ids,
                        "deck_plan_id": recorded_plan.id, "generation_id": run.detail.id,
                    },
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


def _llm_planner():
    """LLM-планировщик той же версии, что у генерации (``planning.
    pipeline_version``). ADR-030: ``POST /plans`` раньше всегда звал v1, и
    показанный пользователю план не совпадал со сверстанным."""
    version = pipeline.planning_pipeline_version()
    return plan_deck_llm_v2 if version in ("v2", "v3") else plan_deck_llm


def _register_filled_pack(source_pack_id: str, filled: ContentPack, project_id: str) -> str:
    """Добранный пак (ADR-030) — отдельная запись: исходный пак пользователя
    не меняется, а план и генерация ссылаются на факты добранного."""
    pack = filled.model_copy(update={"id": _new_id("pack")})
    STORE.content_packs[pack.id] = pack
    STORE.pack_artifacts[pack.id] = list(STORE.pack_artifacts.get(source_pack_id, []))
    STORE.evidence_graphs[pack.id] = build_evidence_graph(pack)
    STORE.content_pack_projects.setdefault(pack.id, set()).add(project_id)
    STORE.filled_from[pack.id] = source_pack_id
    return pack.id


def _derived_from(pack_id: str, source_pack_id: str) -> bool:
    """``pack_id`` — добранная (возможно, повторно) версия ``source_pack_id``."""
    seen: set[str] = set()
    while pack_id in STORE.filled_from and pack_id not in seen:
        seen.add(pack_id)
        pack_id = STORE.filled_from[pack_id]
        if pack_id == source_pack_id:
            return True
    return False


def _plan_pack_id(body: GenerationCreate) -> str | None:
    """Пак, на котором построен присланный план, если это добранная версия
    пака из запроса (ADR-030). Фронт, не знающий про добор, присылает
    исходный ``content_pack_id`` — генерация всё равно должна идти по
    добранному паку, иначе факты плана не найдутся в графе."""
    candidate: str | None = None
    if body.deck_plan_id is not None:
        plan_set = STORE.plans.get(body.deck_plan_id)
        candidate = plan_set.content_pack_id if plan_set is not None else None
    elif body.deck_plan is not None:
        graph_id = body.deck_plan.evidence_graph_id
        candidate = graph_id.removeprefix("pack:").removeprefix("eg-")
    if candidate and candidate != body.content_pack_id and _derived_from(
        candidate, body.content_pack_id
    ):
        return candidate
    return None


def _plan_all_strategies(
    pack: Any,
    body: GenerationCreate,
    gateway: ModelGateway | None,
    run: _InternalRun,
    brief: Brief | None = None,
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
    brief = brief or body.brief
    now = _utcnow()
    for variant in run.detail.variants:
        _patch_variant(
            run, variant.id, status=JobState.running, started_at=now, stage="plan"
        )

    strategies = list(dict.fromkeys(Strategy(v.strategy) for v in body.variants))
    plans: dict[Strategy, DeckPlan] = {}
    to_plan: list[Strategy] = []
    for strategy in strategies:
        if strategy.value in run.supplied_plans:
            # готовый план (D7): не планируем заново — колода собирается
            # ровно по нему.
            plans[strategy] = annotate_unit_origins(
                run.supplied_plans[strategy.value], pack
            )
        else:
            to_plan.append(strategy)

    if to_plan:
        primary = Strategy.balanced if Strategy.balanced in to_plan else to_plan[0]
        if gateway is None:
            shared_plan = plan_deck(pack, brief, strategy=primary)
        else:

            async def _plan_and_style_once(strategy: Strategy) -> DeckPlan:
                planning_version = pipeline.planning_pipeline_version()
                try:
                    plan = await _llm_planner()(pack, brief, gateway, strategy=strategy)
                except DeckDNAError:
                    plan = plan_deck(pack, brief, strategy=strategy)
                # v3 (ADR-016 Stage 3): compose-время layout_fit уже пишет и
                # подгоняет финальный текст — доп. стилизация была бы избыточна
                # (см. тот же guard в generation/pipeline.py::generate()).
                if (
                    planning_version != "v3"
                    and content_style.content_styling_enabled()
                    and content_style.can_rewrite(gateway)
                ):
                    with contextlib.suppress(DeckDNAError):
                        plan = await content_style.style_deck_content(
                            plan, gateway, language=plan.language or "ru"
                        )
                return plan

            async def _run_shared_llm_plan() -> DeckPlan:
                try:
                    async with _maybe_gateway_session(gateway):
                        return await _plan_and_style_once(primary)
                finally:
                    close = getattr(gateway, "aclose", None)
                    if close is not None:
                        await close()

            shared_plan = asyncio.run(_run_shared_llm_plan())

        shared_plan = annotate_unit_origins(shared_plan, pack)
        for strategy in to_plan:
            plans[strategy] = shared_plan
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
            update={"state": JobState.running, "started_at": job.started_at or _utcnow()}
        )
        run.detail = detail.model_copy(update={"state": JobState.running})
    _journal(job_id, "job.state_changed", {"state": "running"})

    plans: dict[Strategy, DeckPlan] = {}
    job_error: JobError | None = None
    preparation_usage_before = measures.usage_snapshot(gateway)
    try:
        template = STORE.templates[body.template_id]
        tpl_artifact = STORE.artifacts[template.artifact_id]
        # The live UI analyzes on upload without a provider session, so
        # deterministic DNA exists but semantic exemplar descriptions do
        # not. Enrich when generation has a gateway; also handle direct
        # API callers that skipped analysis altogether.
        with _TEMPLATE_DNA_LOCK:
            dna = STORE.design_dna.get(body.template_id)
            needs_enrichment = (
                gateway is not None
                and dna_builder.can_describe(gateway)
                and (dna is None or not any(e.content_description for e in dna.exemplars))
            )
            if dna is None or needs_enrichment:
                _journal(job_id, "stage.started", {
                    "stage": "template_analysis", "template_id": body.template_id,
                    "assistant_note": "Разбираю оформление шаблона…",
                })
                analysis_body = TemplateAnalysisRequest(
                    provider_session_id=body.provider_session_id,
                    use_llm=body.use_llm if gateway is not None else False,
                )
                if STORE.jobs[job_id].kind == JobKind.autopilot:
                    # Internal autopsy shares the autopilot job, without a child job.
                    asyncio.run(_run_template_analysis(template, analysis_body, gateway))
                else:
                    asyncio.run(_analyze_template(body.template_id, analysis_body, gateway))
                dna = STORE.design_dna[body.template_id]
                _journal(job_id, "stage.completed", {
                    "stage": "template_analysis", "template_id": body.template_id,
                })
        with tempfile.TemporaryDirectory(prefix="deckdna-gen-") as tmpdir:
            tmp = Path(tmpdir)
            tpl_path = tmp / tpl_artifact.filename
            # ADR-033 §9: шаблон в цветах и шрифтах брендбука проекта
            tpl_bytes, run.brand = _template_bytes(
                body.template_id, detail.project_id, body.brandbook_id, body.brand_priority
            )
            if run.brand and run.brand.get("applied") and dna is not None:
                dna = dna_builder.restyle_design_dna(dna, tpl_bytes)
            if run.brand:
                run.template_artifact_id = _store_artifact(
                    tpl_bytes, type="template.branded", mime_type=_PPTX_MIME,
                    filename=tpl_artifact.filename, project_id=detail.project_id).id
            tpl_path.write_bytes(tpl_bytes)
            # ADR-032: все исходники пака, каждый в своём каталоге
            # <src>/<artifact_id>/<имя> — шаблон и контент с одинаковым
            # именем (deck.pptx) не затирают друг друга, а картинки
            # «<artifact_id>#ppt/media/…» находят свой файл
            src_path = _write_pack_sources(tmp / "src", body.content_pack_id)
            # Переиспользуем сохранённый при upload ContentPack, а не
            # парсим временную копию заново: у сохранённого пакета верный
            # artifact_id на каждом unit (parse_file по умолчанию
            # подставил бы имя временного файла) и уже применены brief
            # overrides (language/title_hint) — см. hardening check #15.
            pack = STORE.content_packs[body.content_pack_id]

            # Фаза 2 : варианты раньше планировались
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
            # Фаза 5 : compose/audit/render теперь
            # ТОЖЕ параллельны между вариантами, не только планирование
            # (Фаза 2). На живом провайдере rerank+VLM-аудит одного
            # варианта уже сам по себе занимал больше 5-минутного бюджета
            # колоды (подтверждено дважды: 6:31 и 7:09 при трёх
            # последовательных вариантах) — это и было главным оставшимся
            # источником задержки сверх бюджета, planning параллелился уже
            # в Фазе 2.
            #
            # Критично: каждый вариант получает СОБСТВЕННЫЙ gateway
            # (``_gateway_for`` — фабрика, не синглтон), а не общий
            # ``gateway``, использованный выше для DNA/планирования.
            # ``OpenAICompatibleProvider.session()``/``aclose()`` держат
            # ``_session_client``/``_pace_lock`` как обычные атрибуты
            # экземпляра без блокировки — это безопасно для НЕСКОЛЬКИХ
            # ПОСЛЕДОВАТЕЛЬНЫХ ``asyncio.run`` (ровно то, что уже
            # проверяют test_provider_survives_multiple_event_loops и
            # test_pacing_survives_multiple_event_loops), но НЕ для
            # ОДНОВРЕМЕННОГО обращения из нескольких ОС-потоков: два
            # потока, открывающие ``session()`` в один момент, гонялись бы
            # за одним ``_session_client`` — один поток мог бы закрыть
            # HTTP-клиент, которым в этот момент пользуется другой
            # («Event loop is closed» или чтение чужого пула соединений).
            # Отдельный gateway на поток полностью убирает эту гонку ценой
            # того, что pacing (``_min_request_interval_s`` — защита от
            # TPM-барстов, см. openai_compat.py) координируется только
            # ВНУТРИ каждого варианта, не между тремя одновременно; ретраи
            # с backoff (тот же файл) уже смягчают случайный burst-429.
            # ``out_dir`` теперь именован по index, не только по strategy,
            # — защита от коллизии файлов, если когда-нибудь два варианта
            # запросят одну стратегию (сегодня API это не валидирует).
            # ADR-030: добор контента — один раз на прогон, общий для всех
            # вариантов (три варианта вёрстки одной презентации). Если все
            # варианты пришли с готовым планом из POST /plans, добор уже
            # сделан там (content_pack_id указывает на добранный пак).
            brief = body.brief
            fill_report: dict[str, Any] | None = None
            if any(v.strategy not in run.supplied_plans for v in body.variants):
                for variant in run.detail.variants:
                    _patch_variant(run, variant.id, stage="research")
                fill = pipeline._run_async_gw(
                    gateway, fill_content(pack, body.brief, gateway)
                )
                brief = fill.brief
                fill_report = fill.to_dict()
                if fill.changed:
                    # свой id → свой граф фактов: повторный contextual-аудит
                    # и паспорт видят добранные узлы с их origin
                    pack = STORE.content_packs[
                        _register_filled_pack(body.content_pack_id, fill.pack, detail.project_id)
                    ]
                with _JOB_LOCK:
                    run.detail = run.detail.model_copy(update={"content_fill": fill_report})
            else:
                # ADR-034 §0: добор был в POST /plans — его отчёт идёт в
                # паспорт (metrics.content_origin / research) и в прогон
                fill_report = _plan_fill_report(body, detail.project_id)
                if fill_report is not None:
                    with _JOB_LOCK:
                        run.detail = run.detail.model_copy(update={"content_fill": fill_report})
            if run.plan_source == "user":
                # ADR-030: слайды, добавленные пользователем одним
                # заголовком, получают тело до вёрстки — иначе заглушка
                user_plan = next(iter(run.supplied_plans.values()))
                completed_plan, completion = pipeline._run_async_gw(
                    gateway, complete_hollow_slides(user_plan, brief, gateway, pack)
                )
                run.supplied_plans = {s: completed_plan for s in run.supplied_plans}
                if any(completion.values()):
                    fill_report = {**(fill_report or {}), "user_slides": completion}
                    with _JOB_LOCK:
                        run.detail = run.detail.model_copy(
                            update={"content_fill": fill_report}
                        )
            plans.update(_plan_all_strategies(pack, body, gateway, run, brief))

            total = len(run.detail.variants)

            def _run_one_variant(index: int, variant_req: VariantRequest) -> None:
                variant_id = run.detail.variants[index].id
                strategy = Strategy(variant_req.strategy)
                variant_gateway = _gateway_for(body, detail.project_id)
                try:
                    _patch_variant(
                        run,
                        variant_id,
                        status=JobState.running,
                        started_at=run.detail.variants[index].started_at or _utcnow(),
                        stage="content",
                    )
                    plan = plans[strategy]
                    with _JOB_LOCK:
                        run.variant_plans[variant_id] = plan
                    out_dir = tmp / f"{index}-{variant_req.strategy}"
                    report = pipeline.generate(
                        tpl_path,
                        src_path,
                        brief,
                        out_dir,
                        gateway=variant_gateway,
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
                        content_fill=fill_report or {},
                        exemplar_overrides=_preview_overrides(body, variant_req.strategy),
                        on_slide_event=(
                            lambda kind, data, _v=variant_id: _progressive_slide_event(
                                run, _v, kind, data
                            )
                        ) if body.progressive_previews else None,
                        is_canceled=lambda: (job_id not in STORE.jobs
                                             or STORE.jobs[job_id].state == JobState.canceled),
                        on_render=lambda pdf, _v=variant_id, _p=plan: _slides_ready(
                            run, _v, _p, pdf
                        ),
                    )

                    # Layout fitting may change text. Local edits must retain
                    # the actual compiled plan, not its pre-fit ancestor.
                    plan = DeckPlan.model_validate(report["deck_plan"])
                    plans[strategy] = plan
                    # Preparation (DNA enrichment + concurrent plans) is
                    # shared across variants and measured on the ORIGINAL
                    # gateway (unused by any variant thread from this point
                    # on, so this snapshot is stable regardless of thread
                    # timing). Charge it once to the first requested variant
                    # so summing passports equals all real generation calls.
                    if index == 0:
                        preparation_usage = measures.usage_delta(
                            preparation_usage_before, measures.usage_snapshot(gateway)
                        )
                        usage = measures.merge_usage(report.get('usage') or {}, preparation_usage)
                        report["usage"] = usage
                        report["quality_passport"]["metrics"]["usage"] = usage
                        (out_dir / "quality-passport.json").write_text(
                            json.dumps(
                                report["quality_passport"], ensure_ascii=False, indent=2
                            ),
                            encoding="utf-8",
                        )

                    if run.brand:
                        # ADR-033 §9: применённый брендбук и конфликты
                        report["quality_passport"]["brand"] = run.brand
                        (out_dir / "quality-passport.json").write_text(
                            json.dumps(
                                report["quality_passport"], ensure_ascii=False, indent=2
                            ),
                            encoding="utf-8",
                        )
                    with _JOB_LOCK:
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
                            "brief": brief,
                            "duration_seconds": report["duration_seconds"],
                            "prompt_versions": passport_dict["provenance"]["prompt_versions"],
                            "model_profiles": passport_dict["provenance"]["model_profiles"],
                            "stage_timings": report["stage_timings"],
                            "usage": report["usage"],
                            "source_text": measures.pack_text(pack),
                            "auto_fixes": report["auto_fixes"],
                            "repair_fixes": {},
                            # паспорт новой ревизии (после ремонта) не теряет
                            # добор (ADR-034 §0), заметки и выбор графиков
                            "content_fill": fill_report,
                            "speaker_notes": (passport_dict.get("metrics") or {}).get(
                                "speaker_notes"
                            ),
                            "analysis": passport_dict.get("analysis"),
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
                    # The composer can add bibliography continuation pages (and
                    # protected template slides). Describe the delivered PPTX,
                    # not merely the shorter input DeckPlan, so every page gets
                    # its own preview and the UI keeps slide numbers aligned.
                    plan_by_id = {slide.id: slide for slide in plan.slides}
                    with _JOB_LOCK:
                        STORE.slides[variant_id] = [
                            SlideInfo(
                                id=_new_id("sld"),
                                variant_id=variant_id,
                                index=position,
                                slide_plan_id=(
                                    source.id
                                    if (source := plan_by_id.get(item["id"]))
                                    else None
                                ),
                                purpose=item.get("purpose"),
                                title=item.get("title") or (
                                    source.title_intent if source else None
                                ),
                            )
                            for position, item in enumerate(
                                report["compose_report"]["slides"], 1
                            )
                        ]
                        run.variant_plans[variant_id] = plan
                    _attach_early_previews(run, variant_id)
                    from deckdna.api.changes import drain

                    with _JOB_LOCK:
                        run.base_ready[variant_id] = True
                    drain(run.detail.id, variant_id)
                    _journal(job_id, "variant.completed", {
                        "variant_id": variant_id, "strategy": variant_req.strategy,
                    })
                    # PNG-превью и монтаж строятся из уже готового PDF. Это
                    # удобство, а не часть результата: сбой не должен ронять
                    # прогон — превью дособерутся лениво по запросу.
                    try:
                        _ensure_previews(run, variant_id)
                    except Exception:  # noqa: BLE001 — best-effort
                        logger.warning(
                            "preview build failed for %s", variant_id, exc_info=True
                        )
                finally:
                    close = getattr(variant_gateway, "aclose", None)
                    if close is not None:
                        asyncio.run(close())

            gen_cfg_raw = pipeline.load_generation_config().raw
            max_parallel = int(
                (gen_cfg_raw.get("runtime") or {}).get("max_parallel_variants") or 3
            )
            max_workers = max(
                1,
                min(
                    len(body.variants),
                    os.cpu_count() or 1,
                    max_parallel,
                ),
            )
            with ThreadPoolExecutor(
                max_workers=max_workers, thread_name_prefix="deckdna-variant"
            ) as variant_pool:
                futures: dict[Any, int] = {}
                for index, variant_req in enumerate(body.variants):
                    # Кооперативная отмена: следующий вариант не запускается.
                    if STORE.jobs[job_id].state == JobState.canceled:
                        break
                    futures[variant_pool.submit(_run_one_variant, index, variant_req)] = (
                        index
                    )

                done = 0
                errors: dict[int, BaseException] = {}
                for future in as_completed(futures):
                    idx = futures[future]
                    exc = future.exception()
                    if exc is not None:
                        errors[idx] = exc
                    else:
                        done += 1
                    with _JOB_LOCK:
                        job = STORE.jobs[job_id]
                        if job.state not in _TERMINAL_JOB_STATES:
                            STORE.jobs[job_id] = job.model_copy(
                                update={"progress": (done + len(errors)) / total}
                            )
                if errors:
                    # Deterministic by requested variant order, not by
                    # which thread happened to fail first (wall-clock
                    # completion order is not reproducible) -- matches the
                    # old sequential loop, which always surfaced the
                    # earliest variant's error. Siblings that already
                    # succeeded keep their completed status/artifacts
                    # (_finish_generation only downgrades queued/running
                    # variants on job failure) -- an improvement over the
                    # old strictly-sequential loop, which never even
                    # attempted variants after the first failure.
                    raise errors[min(errors)]
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
    from deckdna.api.changes import settle_unavailable

    settle_unavailable(run)
    final = STORE.jobs[job_id]
    if final.state == JobState.completed:
        _journal(job_id, "job.completed", {"state": "completed", "generation_id": run_id,
                                           "assistant_note": "Готово."})
    elif final.state == JobState.failed:
        _journal(job_id, "job.failed", {
            "state": "failed", "error": final.error.model_dump() if final.error else None,
        })


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
    filled = _plan_pack_id(body)
    if filled is not None:
        body = body.model_copy(update={"content_pack_id": filled})
    from deckdna.planning.mandatory import mandatory_anchors

    required = body.brief.mandatory_sections
    if required is None:
        required = STORE.project_briefs.get(project_id, {}).get("mandatory_sections")
    mandatory_anchors(STORE.content_packs[body.content_pack_id], required)
    if body.provider_session_id is not None:
        _require_session(body.provider_session_id, project_id)
    # gateway строится синхронно — та же upfront-валидация provider-
    # конфига, что была у синхронной версии (provider_capability_missing
    # → 422 до 202, не silent failure в фоне).
    gateway = _gateway_for(body, project_id)
    from deckdna.api.dependencies import save_brief

    if not body.deck_plan_id and body.deck_plan is None:
        save_brief(project_id, body.brief.model_dump(mode="json", exclude_none=True))
        body = body.model_copy(update={
            "brief": _brief_model(project_id, STORE.content_packs[body.content_pack_id]),
        })
    run = _create_generation(project, body, parent_run_id=None)
    from deckdna.api.dispatch import schedule_generation

    await schedule_generation(run, gateway)
    return GenerationAccepted(
        generation_id=run.detail.id,
        job_id=run.detail.job_id,
        variant_ids=[v.id for v in run.detail.variants],
    )


def _journal(job_id: str, event_type: str, data: dict[str, Any]) -> None:
    """Событие в журнал job'а (ADR-030): seq растёт монотонно."""
    with _JOB_LOCK:
        events = STORE.job_events.setdefault(job_id, [])
        job = STORE.jobs.get(job_id)
        events.append({
            "sequence": len(events) + 1,
            "event": event_type,
            "timestamp": _utcnow().isoformat(),
            "job_id": job_id,
            "stage": job.stage if job is not None else None,
            "progress": job.progress if job is not None else 0.0,
            **data,
        })


def _plan_inputs(project_id: str, body: PlanRequest) -> ContentPack:
    _require(STORE.projects, project_id, "project")
    if body.template_id is not None:
        _require_owned_template(body.template_id, project_id)
    _require_owned_content_pack(body.content_pack_id, project_id)
    if not STORE.pack_artifacts.get(body.content_pack_id):
        raise DeckDNAError(
            "invalid_input",
            f"content pack {body.content_pack_id} has no uploaded source file",
        )
    if body.provider_session_id is not None:
        _require_session(body.provider_session_id, project_id)
    # Переиспользуем сохранённый при upload ContentPack, а не парсим
    # временную копию заново — так artifact_id на каждом unit остаётся
    # тем же, что и в исходном upload (см. hardening check #15).
    pack = STORE.content_packs[body.content_pack_id]
    from deckdna.planning.mandatory import mandatory_anchors

    required = body.brief.mandatory_sections
    if required is None:
        required = STORE.project_briefs.get(project_id, {}).get("mandatory_sections")
    mandatory_anchors(pack, required)
    return pack


async def _build_plan_set(
    project_id: str,
    body: PlanRequest,
    pack: ContentPack,
    gateway: ModelGateway | None,
    *,
    plan_id: str | None = None,
    on_event: Any = None,
    is_canceled: Any = None,
) -> PlanSet:
    """Добор контента (ADR-030) + план по каждой стратегии — общее ядро
    синхронного и фонового режимов ``POST /plans``.

    ``is_canceled()`` — кооперативная отмена (ADR-034 §0): проверяется
    после добора и перед каждой стратегией."""
    emit = on_event or (lambda _type, _data: None)
    context_revision = STORE.assistant_progress.get(project_id, {}).get("context_revision", 1)

    def check_canceled() -> None:
        current_revision = STORE.assistant_progress.get(project_id, {}).get("context_revision", 1)
        if current_revision != context_revision:
            raise DeckDNAError("state_conflict", "project brief changed during planning",
                               http_status=409)
        if is_canceled is not None and is_canceled():
            raise _PlanCanceled()
    entries: list[PlanEntry] = []
    plan_pack_id = body.content_pack_id
    try:
        # Фаза 3: несколько стратегий планируются последовательно в
        # ОДНОМ event loop'е — opt-in session() (_maybe_gateway_session)
        # даёт им один общий HTTP-клиент вместо хендшейка на каждую.
        async with _maybe_gateway_session(gateway):
            # ADR-030: добор контента до планирования. Здесь пользователь
            # может выбрать сам (content_fill=ask): при нехватке/избытке
            # планы не строятся, ответ несёт оценку и needs_decision.
            emit("stage.started", {"stage": "research"})
            fill = await fill_content(
                pack, body.brief, gateway, ask_allowed=True, on_event=emit,
                exclude_source_ids=body.exclude_source_ids,
            )
            check_canceled()
            if not fill.needs_decision:
                emit("stage.started", {"stage": "plan"})
                if fill.changed:
                    plan_pack_id = _register_filled_pack(
                        body.content_pack_id, fill.pack, project_id
                    )
                plan_pack = STORE.content_packs[plan_pack_id]
                unique_names = list(dict.fromkeys(body.strategies))
                if unique_names:
                    check_canceled()
                    strategy_objs = [Strategy(s) for s in unique_names]
                    primary = (
                        Strategy.balanced
                        if Strategy.balanced in strategy_objs
                        else strategy_objs[0]
                    )
                    if gateway is None:
                        shared_plan = await asyncio.to_thread(
                            plan_deck, plan_pack, fill.brief, strategy=primary
                        )
                    else:
                        try:
                            shared_plan = await _llm_planner()(
                                plan_pack, fill.brief, gateway, strategy=primary
                            )
                        except DeckDNAError:
                            shared_plan = await asyncio.to_thread(
                                plan_deck, plan_pack, fill.brief, strategy=primary
                            )
                    shared_plan = annotate_key_message(
                        annotate_unit_origins(shared_plan, plan_pack), plan_pack
                    )
                    for strategy_name in unique_names:
                        check_canceled()
                        entries.append(
                            PlanEntry(strategy=strategy_name, deck_plan=shared_plan)
                        )
                        for slide in shared_plan.slides:
                            emit("plan.slide", {
                                "strategy": strategy_name,
                                "index": slide.index,
                                "title": slide.title_intent,
                                "purpose": slide.purpose.value,
                                # ADR-034 §0: карточка слайда целиком (форма фронта)
                                "slide": slide.model_dump(mode="json", exclude_none=True),
                            })
    finally:
        close = getattr(gateway, "aclose", None)
        if close is not None:
            await close()
    plan_set = PlanSet(
        plan_id=plan_id or _new_id("plan"),
        project_id=project_id,
        content_pack_id=plan_pack_id,
        plans=entries,
        created_at=_utcnow(),
        content_fill=fill.to_dict(),
        sufficiency=_sufficiency(fill),
        research=_research_summary(fill),
        key_message=fill.brief.key_message,
        template_id=body.template_id,
    )
    check_canceled()
    STORE.plans[plan_set.plan_id] = plan_set
    return plan_set


def _plan_fill_report(body: Any, project_id: str) -> dict[str, Any] | None:
    """Отчёт добора из набора планов, по которому идёт генерация:
    ``deck_plan_id`` или последний набор проекта на этом паке."""
    plan_set = STORE.plans.get(body.deck_plan_id) if body.deck_plan_id else None
    if plan_set is None:
        candidates = [
            p for p in STORE.plans.values()
            if p.project_id == project_id and p.content_pack_id == body.content_pack_id
        ]
        plan_set = max(candidates, key=lambda p: p.created_at, default=None)
    if plan_set is None or not plan_set.content_fill:
        return None
    return dict(plan_set.content_fill)


class _PlanCanceled(Exception):
    """Построение плана отменено (POST /projects/{id}/plans/{plan_id}/cancel)."""


def _sufficiency(fill: Any) -> dict[str, Any]:
    """ADR-034 §0 (F3 плашка): ``{status, content_slides, target_slides}``.
    ``content_slides`` — на сколько слайдов хватает контента после добора,
    ``target_slides`` — итоговое число слайдов брифа."""
    return {
        "status": fill.after.status.value,
        "content_slides": fill.after.capacity,
        "target_slides": fill.brief.target_slide_count,
    }


def _research_summary(fill: Any) -> dict[str, Any]:
    """ADR-034 §0 (F6): ``{queries[], sources[{id, title, url, site,
    accessed_at, facts_count}], duration_s}``."""
    web = fill.web
    if web is None:
        return {"queries": [], "sources": [], "duration_s": round(fill.elapsed_s, 2),
                "added_facts": 0, "status": "skipped", "reason": fill.resolved}
    counts: dict[str, int] = {}
    for section in fill.pack.sections:
        if is_sources_heading(section.heading):
            continue
        for block in section.blocks:
            key = block.source_ref.artifact_id
            texts = [t for t in [block.text, *(block.items or [])] if t]
            counts[key] = counts.get(key, 0) + len(texts)
    return {
        "added_facts": web.facts_kept,
        "status": web.to_dict()["status"], "reason": web.to_dict()["reason"],
        "queries": list(web.queries),
        "sources": [source_card(src, counts.get(src.id, 0)) for src in web.sources],
        "duration_s": round(fill.elapsed_s, 2),
    }


_PLAN_POOL = ThreadPoolExecutor(
    max_workers=max(1, int(os.environ.get("DECKDNA_PLAN_WORKERS", 2))),
    thread_name_prefix="deckdna-plan",
)


def _set_job(job_id: str, **fields: Any) -> None:
    with _JOB_LOCK:
        STORE.jobs[job_id] = STORE.jobs[job_id].model_copy(update=fields)


def _execute_plan_job(
    job_id: str,
    plan_id: str,
    project_id: str,
    body: PlanRequest,
    pack: ContentPack,
    gateway: ModelGateway | None,
) -> None:
    """Фоновое построение плана (ADR-030 §6): прогресс — в журнал job'а."""
    _set_job(job_id, state=JobState.running, started_at=_utcnow(), stage="research")
    _journal(job_id, "job.state_changed", {"state": "running"})

    def on_event(event_type: str, data: dict[str, Any]) -> None:
        if event_type == "research.finished":
            _set_job(job_id, stage="plan", progress=0.5)
        _journal(job_id, event_type, data)

    def is_canceled() -> bool:
        return STORE.jobs[job_id].state == JobState.canceled

    if is_canceled():
        return
    try:
        plan_set = asyncio.run(
            _build_plan_set(project_id, body, pack, gateway, plan_id=plan_id,
                            on_event=on_event, is_canceled=is_canceled)
        )
    except _PlanCanceled:
        return
    except Exception as exc:  # noqa: BLE001 — job завершается typed-ошибкой
        if STORE.jobs[job_id].state == JobState.canceled:
            return
        error = (
            JobError(code=exc.code, message=exc.message, stage=exc.stage)
            if isinstance(exc, DeckDNAError)
            else JobError(code="internal_error", message=f"{type(exc).__name__}: {exc}",
                          stage="planning")
        )
        _set_job(job_id, state=JobState.failed, finished_at=_utcnow(), error=error)
        _journal(job_id, "job.state_changed", {"state": "failed", "error": error.model_dump()})
        return
    _set_job(
        job_id,
        state=JobState.completed,
        progress=1.0,
        finished_at=_utcnow(),
        result_ids={"plan_id": plan_set.plan_id, "content_pack_id": plan_set.content_pack_id},
    )
    _journal(job_id, "job.completed", {
        "state": "completed",
        "plan_id": plan_set.plan_id,
        "needs_decision": bool((plan_set.content_fill or {}).get("needs_decision")),
    })


@app.post(f"{API_PREFIX}/projects/{{project_id}}/plans", tags=["plans"])
async def create_plans(
    project_id: str, body: PlanRequest, response: Response
) -> PlanSet | PlanAccepted:
    """План до вёрстки (D7): структура колоды по каждой стратегии. Его можно
    показать, поправить и передать в POST /generations (deck_plan_id или
    deck_plan) — планирование тогда не повторяется.

    ADR-030: до планирования — добор контента по ``brief.content_fill``;
    ``run_async=true`` строит план фоновым job'ом с живыми событиями."""
    pack = _plan_inputs(project_id, body)
    from deckdna.api.dependencies import save_brief

    save_brief(project_id, body.brief.model_dump(mode="json", exclude_none=True))
    # Intent invalidation may have reverted unconfirmed model interpretations.
    pack = STORE.content_packs[pack.id]
    body = body.model_copy(update={"brief": _brief_model(project_id, pack)})
    gateway = _gateway_for(body, project_id)
    if not body.is_async:
        return await _build_plan_set(project_id, body, pack, gateway)

    now = _utcnow()
    job = Job(
        id=_new_id("job"),
        kind=JobKind.planning,
        state=JobState.queued,
        progress=0.0,
        project_id=project_id,
        created_at=now,
    )
    plan_id = _new_id("plan")
    with _JOB_LOCK:
        STORE.jobs[job.id] = job
        STORE.plan_jobs[plan_id] = job.id
    _journal(job.id, "job.state_changed", {"state": "queued", "plan_id": plan_id})
    _PLAN_POOL.submit(_execute_plan_job, job.id, plan_id, project_id, body, pack, gateway)
    response.status_code = 202
    return PlanAccepted(
        plan_id=plan_id, job_id=job.id, events_url=f"{API_PREFIX}/jobs/{job.id}/events"
    )


@app.post(
    f"{API_PREFIX}/plans/{{plan_id}}/slides/{{slide_id}}/research", tags=["plans"]
)
async def research_plan_slide(
    plan_id: str, slide_id: str, body: SlideResearchRequest
) -> SlideResearchResult:
    """ADR-030: «найти факты / цифры» для одного слайда плана. Найденное
    (с цитатами и источниками) добавляется в слайд и в новый пак; набор
    планов обновляется — передайте его ``content_pack_id`` в генерацию."""
    plan_set = _require(STORE.plans, plan_id, "plan set")
    if not plan_set.plans:
        raise DeckDNAError("invalid_input", f"plan set {plan_id} has no plans")
    entry = next(
        (e for e in plan_set.plans if body.strategy in (None, e.strategy)), None
    )
    if entry is None:
        raise DeckDNAError(
            "invalid_input", f"plan set {plan_id} has no plan for strategy {body.strategy}"
        )
    slide = find_slide(entry.deck_plan, slide_id)
    if slide is None:
        raise DeckDNAError("not_found", f"slide not found: {slide_id}")
    if body.provider_session_id is not None:
        _require_session(body.provider_session_id, plan_set.project_id)
    gateway = _gateway_for(body, plan_set.project_id)
    pack = STORE.content_packs[plan_set.content_pack_id]
    try:
        async with _maybe_gateway_session(gateway):
            found = await research_slide(
                entry.deck_plan, slide, pack, entry.deck_plan.brief, gateway,
                load_research_config(), focus=body.focus,
            )
    finally:
        close = getattr(gateway, "aclose", None)
        if close is not None:
            await close()
    if not found.changed:
        return SlideResearchResult(
            plan_set=plan_set, slide=slide, added_units=0, research=found.to_dict()
        )
    pack_id = plan_set.content_pack_id
    if found.facts:
        pack_id = _register_filled_pack(plan_set.content_pack_id, found.pack, plan_set.project_id)
    updated = annotate_unit_origins(
        apply_to_plan(entry.deck_plan, slide_id, found, pack_id), STORE.content_packs[pack_id]
    )
    fill = dict(plan_set.content_fill or {})
    fill["slide_research"] = [
        *fill.get("slide_research", []),
        {"slide_id": slide_id, "focus": body.focus, **found.to_dict()},
    ]
    plan_set = plan_set.model_copy(update={
        "content_pack_id": pack_id,
        "plans": [
            PlanEntry(strategy=e.strategy, deck_plan=updated) if e is entry else e
            for e in plan_set.plans
        ],
        "content_fill": fill,
    })
    STORE.plans[plan_id] = plan_set
    return SlideResearchResult(
        plan_set=plan_set,
        slide=find_slide(updated, slide_id) or slide,
        added_units=len(found.facts) + len(found.model_bullets),
        research=found.to_dict(),
    )


class SlideChartPatch(BaseModel):
    chart_type: str
    strategy: str | None = None


class SlideActionBody(BaseModel):
    action: Literal["shorten", "split", "merge_next", "to_chart", "to_diagram", "remove"]
    strategy: str | None = None


def _plan_entry(plan_set: PlanSet, strategy: str | None) -> PlanEntry:
    from deckdna.api.dependencies import is_current

    if not is_current(plan_set.project_id, plan_set.created_at):
        raise DeckDNAError("state_conflict", "plan belongs to an older project brief",
                           http_status=409)
    entry = next((e for e in plan_set.plans if strategy in (None, e.strategy)), None)
    if entry is None:
        raise DeckDNAError("not_found", f"plan set {plan_set.plan_id} has no plan for {strategy}")
    return entry


def _store_plan(plan_set: PlanSet, entry: PlanEntry, plan: DeckPlan,
                pack_id: str | None = None) -> PlanSet:
    pack = STORE.content_packs[pack_id or plan_set.content_pack_id]
    plan = annotate_key_message(plan, pack)
    updated = plan_set.model_copy(update={
        "updated_at": _utcnow(),
        "plans": [PlanEntry(strategy=e.strategy, deck_plan=plan) if e is entry else e
                  for e in plan_set.plans],
        **({"content_pack_id": pack_id} if pack_id else {}),
    })
    STORE.plans[plan_set.plan_id] = updated
    return updated


@app.patch(f"{API_PREFIX}/plans/{{plan_id}}/slides/{{slide_id}}", tags=["plans"])
async def patch_plan_slide(plan_id: str, slide_id: str, body: SlideChartPatch) -> PlanSet:
    """ADR-034 §4: другой график на карточке — только среди альтернатив;
    иное → 422 с причиной из ``rejected[]``."""
    plan_set = _require(STORE.plans, plan_id, "plan set")
    entry = _plan_entry(plan_set, body.strategy)
    pack = STORE.content_packs[plan_set.content_pack_id]
    plan = change_chart(entry.deck_plan, slide_id, body.chart_type, pack)
    return _store_plan(plan_set, entry, plan)


@app.post(f"{API_PREFIX}/plans/{{plan_id}}/slides/{{slide_id}}/actions", tags=["plans"])
async def plan_slide_action(plan_id: str, slide_id: str, body: SlideActionBody) -> PlanSet:
    """ADR-034 §4: быстрые действия на карточке слайда."""
    plan_set = _require(STORE.plans, plan_id, "plan set")
    entry = _plan_entry(plan_set, body.strategy)
    pack = STORE.content_packs[plan_set.content_pack_id]
    plan, new_pack = apply_action(entry.deck_plan, slide_id, body.action, pack)
    pack_id = None
    if new_pack is not None:
        pack_id = _register_filled_pack(plan_set.content_pack_id, new_pack, plan_set.project_id)
    return _store_plan(plan_set, entry, plan, pack_id)


class StylePreviewRequest(BaseModel):
    """ADR-034 §5: пробные слайды трёх стилей."""

    slide_ids: list[str] | None = Field(default=None, max_length=4)
    strategy: str | None = None  # чей план из набора (по умолчанию первый)
    template_id: str | None = None  # по умолчанию — шаблон плана или проекта
    respond_async: bool = True


class StylePreviewAccepted(BaseModel):
    preview_id: str
    job_id: str
    events_url: str


def _run_style_previews(
    preview_id: str, job_id: str | None, plan_set: PlanSet, plan: DeckPlan,
    template_id: str, slide_ids: list[str] | None,
) -> dict[str, Any]:
    from deckdna.generation.preview import render_style_previews

    template = STORE.templates[template_id]
    tpl_artifact = STORE.artifacts[template.artifact_id]
    pack = STORE.content_packs[plan_set.content_pack_id]
    dna = STORE.design_dna.get(template_id)
    images: dict[tuple[str, str], str] = {}

    def on_ready(strategy: str, slide: dict[str, Any]) -> None:
        png = slide.get("png")
        if not png:
            return
        art = _store_artifact(
            png, type="style.preview.png", mime_type="image/png",
            filename=f"{strategy}_{slide['slide_id']}.png", project_id=plan_set.project_id,
        )
        images[(strategy, slide["slide_id"])] = art.id
        if job_id is not None:
            _journal(job_id, "preview.ready", {
                "strategy": strategy, "slide_id": slide["slide_id"],
                "image_artifact_id": art.id,
            })

    with tempfile.TemporaryDirectory(prefix="deckdna-style-") as tmp:
        tpl_path = Path(tmp) / tpl_artifact.filename
        template_bytes, brand = _template_bytes(template_id, plan_set.project_id)
        if brand and brand.get("applied") and dna is not None:
            dna = dna_builder.restyle_design_dna(dna, template_bytes)
        tpl_path.write_bytes(template_bytes)
        result = render_style_previews(
            tpl_path, pack, plan, Path(tmp) / "out", slide_ids=slide_ids,
            design_dna=dna, on_ready=on_ready,
        )
    for style in result["styles"]:
        style["previews"] = [
            {"slide_id": p["slide_id"], "index": p["index"], "exemplar": p["exemplar"],
             "image_artifact_id": images.get((style["strategy"], p["slide_id"]))}
            for p in style["previews"]
        ]
    record = {
        "preview_id": preview_id, "plan_id": plan_set.plan_id,
        "project_id": plan_set.project_id, "template_id": template_id,
        "deck_plan_id": plan.id,
        "plan_digest": hashlib.sha256(plan.model_dump_json().encode()).hexdigest(),
        "template_digest": hashlib.sha256(template_bytes).hexdigest(),
        "created_at": _utcnow().isoformat(), **result,
    }
    STORE.style_previews[preview_id] = record
    return record


@app.post(f"{API_PREFIX}/plans/{{plan_id}}/style-previews", tags=["plans"])
async def create_style_previews(
    plan_id: str, body: StylePreviewRequest, response: Response,
) -> dict[str, Any]:
    """ADR-034 §5: 2–3 ключевых слайда в трёх стратегиях, параллельно.
    Полная сборка с ``preview_id`` берёт те же образцы."""
    plan_set = _require(STORE.plans, plan_id, "plan set")
    entry = _plan_entry(plan_set, body.strategy)
    project = _require(STORE.projects, plan_set.project_id, "project")
    template_id = body.template_id or plan_set.template_id or project.template_id
    if template_id is None:
        raise DeckDNAError("invalid_input", "style previews need a template", http_status=422)
    _require_owned_template(template_id, plan_set.project_id)
    known = {s.id for s in entry.deck_plan.slides}
    if body.slide_ids and not set(body.slide_ids) <= known:
        raise DeckDNAError("not_found", f"slides not in plan: {set(body.slide_ids) - known}")
    preview_id = _new_id("prev")
    if not body.respond_async:
        return await asyncio.to_thread(
            _run_style_previews, preview_id, None, plan_set, entry.deck_plan, template_id,
            body.slide_ids,
        )
    job = Job(id=_new_id("job"), kind=JobKind.style_preview, state=JobState.queued,
              progress=0.0, project_id=plan_set.project_id, created_at=_utcnow(),
              result_ids={"preview_id": preview_id})
    with _JOB_LOCK:
        STORE.jobs[job.id] = job
    _journal(job.id, "job.state_changed", {"state": "queued", "preview_id": preview_id})

    def work() -> None:
        _set_job(job.id, state=JobState.running, started_at=_utcnow(), stage="style_preview")
        _journal(job.id, "job.state_changed", {"state": "running"})
        try:
            record = _run_style_previews(preview_id, job.id, plan_set, entry.deck_plan,
                                         template_id, body.slide_ids)
        except Exception as exc:  # noqa: BLE001
            error = JobError(code="internal_error", message=f"{type(exc).__name__}: {exc}",
                             stage="style_preview")
            _set_job(job.id, state=JobState.failed, finished_at=_utcnow(), error=error)
            _journal(job.id, "job.state_changed", {"state": "failed",
                                                   "error": error.model_dump()})
            return
        _set_job(job.id, state=JobState.completed, progress=1.0, finished_at=_utcnow())
        _journal(job.id, "job.completed", {
            "state": "completed", "preview_id": preview_id,
            "recommended": record["recommended"], "assistant_note": record["assistant_note"],
        })

    _PLAN_POOL.submit(work)
    response.status_code = 202
    return StylePreviewAccepted(
        preview_id=preview_id, job_id=job.id, events_url=f"{API_PREFIX}/jobs/{job.id}/events",
    ).model_dump()


@app.get(f"{API_PREFIX}/style-previews/{{preview_id}}", tags=["plans"])
async def get_style_previews(preview_id: str) -> dict[str, Any]:
    """Итог пробных слайдов: ``{styles:[{strategy, title, difference,
    recommended, previews:[{slide_id, image_artifact_id}]}], assistant_note}``."""
    return _require(STORE.style_previews, preview_id, "style preview")


@app.get(f"{API_PREFIX}/plans/{{plan_id}}", tags=["plans"])
async def get_plan_set(plan_id: str) -> PlanSet:
    """Готовый набор планов (в т. ч. построенный фоновым job'ом)."""
    return _require(STORE.plans, plan_id, "plan set")


@app.get(f"{API_PREFIX}/projects/{{project_id}}/plans/{{plan_id}}", tags=["plans"])
async def get_project_plan_set(project_id: str, plan_id: str) -> PlanSet:
    """ADR-034 §0: путь фронта к тому же PlanSet."""
    _require(STORE.projects, project_id, "project")
    plan_set = _require(STORE.plans, plan_id, "plan set")
    if plan_set.project_id != project_id:
        raise DeckDNAError("not_found", f"plan set not found: {plan_id}")
    return plan_set


@app.post(f"{API_PREFIX}/projects/{{project_id}}/plans/{{plan_id}}/cancel", tags=["plans"])
async def cancel_plan(project_id: str, plan_id: str) -> Job:
    """ADR-034 §0: кооперативная отмена фонового построения плана.
    Добор контента дорабатывает текущий шаг, план не сохраняется."""
    _require(STORE.projects, project_id, "project")
    job_id = STORE.plan_jobs.get(plan_id)
    if job_id is None:
        if plan_id in STORE.plans:
            raise DeckDNAError("state_conflict", f"plan {plan_id} is already built; cannot cancel")
        raise DeckDNAError("not_found", f"plan not found: {plan_id}")
    with _JOB_LOCK:
        job = STORE.jobs[job_id]
        if job.project_id != project_id:
            raise DeckDNAError("not_found", f"plan not found: {plan_id}")
        if job.state in _TERMINAL_JOB_STATES:
            if job.state == JobState.canceled:
                return job
            raise DeckDNAError(
                "state_conflict", f"plan job is already {job.state.value}; cannot cancel"
            )
        job = job.model_copy(update={"state": JobState.canceled, "finished_at": _utcnow()})
        STORE.jobs[job_id] = job
    _journal(job_id, "job.state_changed", {"state": "canceled", "plan_id": plan_id})
    return job


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
    _journal(canceled_job.id, "job.state_changed", {"state": "canceled"})
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
    from deckdna.api.dispatch import schedule_generation

    await schedule_generation(run, gateway)
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
async def job_events(
    job_id: str,
    after: int = Query(default=0, ge=0),
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> EventSourceResponse:
    job = _require(STORE.jobs, job_id, "job")
    if last_event_id is not None:
        if not re.fullmatch(r"[0-9]{1,18}", last_event_id):
            raise DeckDNAError("invalid_input", "Last-Event-ID must be a nonnegative integer",
                               http_status=422)
        after = max(after, int(last_event_id))

    if job_id in STORE.job_events:
        async def stream_journal() -> AsyncIterator[dict[str, str]]:
            cursor = after
            while True:
                # Worker threads append under this same lock. Never hold it
                # while awaiting a slow/disconnected subscriber.
                with _JOB_LOCK:
                    current = STORE.jobs.get(job_id)
                    journal = STORE.job_events.get(job_id, [])
                    recorded = [dict(e) for e in journal if e["sequence"] > cursor]
                    # State and final event are written separately by legacy
                    # workers: wait for BOTH, so the final payload is not lost.
                    settled = current is not None and any(
                        e.get("state") == current.state.value
                        and e.get("event") in {
                            "job.completed", "job.failed", "job.state_changed",
                        }
                        for e in journal
                    ) and current.state in _TERMINAL_JOB_STATES | {JobState.awaiting_user}
                if current is None:  # project deleted while subscribed
                    return
                for event in recorded:
                    cursor = event["sequence"]
                    yield {
                        "id": str(cursor), "event": event["event"],
                        "data": json.dumps(event, ensure_ascii=False),
                    }
                if settled:
                    return
                # No new provider calls, rendering or worker thread per client.
                # EventSourceResponse cancels this task on client disconnect.
                await asyncio.sleep(0.1)

        return EventSourceResponse(
            stream_journal(), ping=15, send_timeout=15,
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
        )

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
    graph = STORE.evidence_graphs.get(graph_id)
    pack_id = graph_id.removeprefix("pack:")
    if graph is None:
        graph = STORE.evidence_graphs.get(pack_id)
    if graph is None:
        graph = next((candidate for candidate in STORE.evidence_graphs.values()
                      if candidate.id == graph_id), None)
    if graph is not None:
        pack = STORE.content_packs.get(graph.content_pack_id)
        if pack is not None and pack.datasets:
            expected = {f"{pack.id}:{dataset.id}" for dataset in pack.datasets}
            if not expected <= {node.id for node in graph.nodes}:
                # ADR-044: saved pre-upgrade graphs lack the data-story IDs.
                # Rebuild from the saved raw pack, without inference/replanning.
                old_graph = graph
                graph = build_evidence_graph(pack)
                for key, candidate in list(STORE.evidence_graphs.items()):
                    if candidate is old_graph:
                        STORE.evidence_graphs[key] = graph
    return graph


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
# ADR-034 §1 intake, ADR-033 §9 brandbook
# ---------------------------------------------------------------------------


def _brand_choice(
    project_id: str, brandbook_id: str | None = None, priority: str | None = None,
) -> tuple[Any, str] | None:
    """Брендбук и приоритет: явные из запроса, иначе проекта; нет — None."""
    current = STORE.project_brand.get(project_id) or {}
    book_id = brandbook_id or current.get("brandbook_id")
    if not book_id:
        return None
    book = _require(STORE.brandbooks, book_id, "brandbook")
    return book, priority or current.get("priority") or "brandbook"


def _template_bytes(
    template_id: str, project_id: str, brandbook_id: str | None = None,
    priority: str | None = None,
) -> tuple[bytes, dict[str, Any] | None]:
    """Байты шаблона для вёрстки: в цветах и шрифтах бренда, если у
    проекта есть брендбук (ADR-033 §9), и отчёт для паспорта."""
    from deckdna.template.brandbook import apply_brandbook, brandbook_cache_key

    template = STORE.templates[template_id]
    data = STORE.artifacts[template.artifact_id].data
    choice = _brand_choice(project_id, brandbook_id, priority)
    if choice is None:
        return data, None
    book, prio = choice
    b_key = brandbook_cache_key(book, prio)
    key = (template_id, book.id, prio, b_key)
    cached = STORE.brand_templates.get(key) or STORE.brand_templates.get(
        (template_id, book.id, prio)
    )
    if cached is None:
        branded, report = apply_brandbook(data, book, prio)
        cached = (branded, report.to_dict())
        STORE.brand_templates[key] = cached
    return cached


def _run_template_bytes(run: _InternalRun) -> bytes:
    """Шаблон, по которому собран прогон (с брендом, если он был)."""
    from deckdna.template.brandbook import brandbook_cache_key

    pinned = getattr(run, 'template_artifact_id', None)
    if pinned and pinned in STORE.artifacts:
        return STORE.artifacts[pinned].data
    tpl = STORE.artifacts[STORE.templates[run.detail.template_id].artifact_id].data
    if not run.brand:
        return tpl
    book_id = run.brand["brandbook_id"]
    prio = run.brand["priority"]
    book = STORE.brandbooks.get(book_id)
    cached = None
    if book is not None:
        cached = STORE.brand_templates.get(
            (run.detail.template_id, book_id, prio, brandbook_cache_key(book, prio))
        )
    if cached is None:
        cached = STORE.brand_templates.get((run.detail.template_id, book_id, prio))
    return cached[0] if cached else tpl


def _store_brandbook(project_id: str, files: list[tuple[str, bytes]]) -> Any:
    from deckdna.template.brandbook import build_brandbook

    def store_asset(name: str, data: bytes, media_type: str) -> str:
        return _store_artifact(data, type="brandbook.asset", mime_type=media_type,
                               filename=name, project_id=project_id).id

    book = build_brandbook(files, _new_id("brand"), project_id=project_id,
                           store_asset=store_asset)
    STORE.brandbooks[book.id] = book
    STORE.brand_sources[book.id] = [
        _store_artifact(data, type="brandbook.source", mime_type="application/octet-stream",
                        filename=name, project_id=project_id).id for name, data in files
    ]
    prio = (STORE.project_brand.get(project_id) or {}).get("priority", "brandbook")
    STORE.project_brand[project_id] = {"brandbook_id": book.id, "priority": prio}
    return book


def _intake_view(project_id: str, result: dict[str, Any]) -> dict[str, Any]:
    files = STORE.intakes.get(project_id, [])
    kinds = Counter(f["kind"] for f in files if f["accepted"])
    parts = []
    for kind, label in (("template", "шаблон"), ("content", "текст"), ("data", "данные"),
                        ("brandbook", "брендбук")):
        if kinds.get(kind):
            parts.append(f"{label} ({kinds[kind]})" if kinds[kind] > 1 else label)
    skipped = sum(1 for f in files if not f["accepted"])
    note = ("Принял: " + ", ".join(parts) + "." if parts else "Пока ничего не принял.")
    if skipped:
        note += (f" Не взял {skipped} {_plural(skipped, ('файл', 'файла', 'файлов'))}, "
                 "тип можно поправить.")
    return {
        **result,
        "project_id": project_id,
        "files": [
            {k: f[k] for k in ("name", "detected_kind", "kind", "accepted", "reason", "size_bytes")}
            for f in files
        ],
        "assistant_note": note,
    }


def _plural(n: int, forms: tuple[str, str, str]) -> str:
    from deckdna.analysis.values import plural

    return plural(n, forms)


def _parse_probe(name: str, data: bytes) -> None:
    with tempfile.TemporaryDirectory(prefix="deckdna-intake-") as tmp:
        path = Path(tmp) / name
        path.write_bytes(data)
        parse_file(path, artifact_id="probe")


def _process_intake(project_id: str, idea: str | None) -> dict[str, Any]:
    """Разложить файлы проекта по типам: шаблон → анализ, контент и данные
    → один пак, брендбук → брендбук проекта."""
    files = STORE.intakes.get(project_id, [])
    result: dict[str, Any] = {"template_id": None, "content_pack_id": None,
                              "brandbook_id": None}
    templates = [f for f in files if f["kind"] == "template"]
    for extra in templates[1:]:
        extra.update(accepted=False, reason="шаблон уже есть; можно отметить как контент")
    if templates:
        first = templates[0]
        first["accepted"] = True
        ext = first["name"].rsplit(".", 1)[-1].lower()
        if first.get("template_id") and first["template_id"] in STORE.templates:
            template = STORE.templates[first["template_id"]]
            STORE.projects[project_id] = STORE.projects[project_id].model_copy(
                update={"template_id": template.id, "updated_at": _utcnow()})
        else:
            template = _store_template(project_id, first["name"], ext, first["data"])
            first["template_id"] = template.id
        result["template_id"] = template.id
    content = [f for f in files if f["kind"] in ("content", "data")]
    supported = [f for f in content
                 if Path(f["name"]).suffix.lower() in _SUPPORTED_CONTENT_SUFFIXES]
    for f in content:
        f["accepted"] = f in supported
        if not f["accepted"]:
            f["reason"] = "этот формат не читается как контент"
    uploads = [(f["name"], f["data"], f.get("mime") or "application/octet-stream")
               for f in supported[:_MAX_CONTENT_FILES]]
    if not uploads and idea and idea.strip():
        uploads = [("idea.md", f"# Идея\n\n{idea.strip()}\n".encode(), "text/markdown")]
    if uploads:
        brief = {"purpose": idea.strip()} if idea else {}
        try:
            accepted = _ingest_uploads(project_id, uploads, brief)
        except DeckDNAError:
            # файл, который не читается в выбранном типе (тип мог поправить
            # пользователь), отмечается и не валит остальные
            good = []
            for upload in uploads:
                try:
                    _parse_probe(upload[0], upload[1])
                    good.append(upload)
                except DeckDNAError as exc:
                    f = next(x for x in content if x["name"] == upload[0])
                    f.update(accepted=False,
                             reason=f"не читается как {f['kind']}: {exc.message}"[:200])
            accepted = _ingest_uploads(project_id, good, brief) if good else None
        if accepted is not None:
            result["content_pack_id"] = accepted.content_pack.id
    brand = [f for f in files if f["kind"] == "brandbook"]
    if brand:
        book = _store_brandbook(project_id, [(f["name"], f["data"]) for f in brand])
        parsed = {src.file_name: src for src in book.sources}
        for f in brand:
            src = parsed.get(f["name"])
            f["accepted"] = bool(src and src.parsed)
            if src and not src.parsed:
                f["reason"] = src.note or "не удалось прочитать"
        result["brandbook_id"] = book.id
    elif project_id in STORE.project_brand:
        STORE.project_brand.pop(project_id)
    for f in files:
        if f["kind"] == "unknown":
            f["accepted"] = False
    return result


@app.post(f"{API_PREFIX}/projects/{{project_id}}/intake", status_code=202, tags=["intake"])
async def intake(
    project_id: str,
    files: Annotated[list[UploadFile] | None, File()] = None,
    idea: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    """ADR-034 §1: «Закинь, что есть» — все файлы разом и/или одна фраза.
    Тип каждого файла определяется сам; ответ — что куда ушло."""
    from deckdna.ingestion.intake import detect

    _require(STORE.projects, project_id, "project")
    files = files or []
    if not files and not (idea and idea.strip()):
        raise DeckDNAError("invalid_input", "intake needs files or an idea", http_status=422)
    if len(files) > _MAX_CONTENT_FILES + 10:
        raise DeckDNAError("invalid_input", "too many files", http_status=422)
    records: list[dict[str, Any]] = []
    taken = False
    for file in files:
        name = _checked_upload_filename(file.filename or "file.bin")
        data = await _read_upload_limited(file, settings.max_upload_mb)
        found = detect(name, data, template_taken=taken)
        taken = taken or found.kind == "template"
        records.append({
            "name": name, "data": data, "mime": file.content_type,
            "size_bytes": len(data), "detected_kind": found.kind, "kind": found.kind,
            "reason": found.reason, "accepted": False,
        })
    STORE.intakes[project_id] = records
    result = await asyncio.to_thread(_process_intake, project_id, idea)
    job = _complete_job(JobKind.content_ingestion, project_id,
                        {k: v for k, v in result.items() if v})
    return _intake_view(project_id, {"job_id": job.id, **result})


class IntakeFilePatch(BaseModel):
    kind: Literal["template", "content", "data", "brandbook", "unknown"]


@app.patch(f"{API_PREFIX}/projects/{{project_id}}/intake/files/{{name}}", tags=["intake"])
async def patch_intake_file(project_id: str, name: str, body: IntakeFilePatch) -> dict[str, Any]:
    """Чип типа файла: пользователь поправил тип — файлы проекта
    раскладываются заново."""
    from deckdna.ingestion.intake import allowed

    _require(STORE.projects, project_id, "project")
    files = STORE.intakes.get(project_id) or []
    record = next((f for f in files if f["name"] == name), None)
    if record is None:
        raise DeckDNAError("not_found", f"intake file not found: {name}")
    if not allowed(name, body.kind):
        raise DeckDNAError("invalid_input", f"{name} cannot be read as {body.kind}",
                           http_status=422)
    record.update(kind=body.kind, reason="тип выбран вручную")
    if body.kind == "template":
        for other in files:
            if other is not record and other["kind"] == "template":
                other.update(kind="content", reason="шаблон выбран другой")
        files.remove(record)
        files.insert(0, record)
    result = await asyncio.to_thread(_process_intake, project_id, None)
    return _intake_view(project_id, result)


@app.get(f"{API_PREFIX}/templates/{{template_id}}/summary", tags=["templates"])
async def template_summary(template_id: str) -> dict[str, Any]:
    """Карточка шаблона: слайды, палитра, шрифты (ADR-034 §1)."""
    from deckdna.template.brandbook import template_look

    template = _require(STORE.templates, template_id, "template")
    if template_id not in STORE.design_dna:
        await _analyze_template(template_id, TemplateAnalysisRequest(use_llm=False), None)
    look = template_look(STORE.artifacts[template.artifact_id].data)
    slides = STORE.template_slides.get(template_id, [])
    roles = Counter(s.role for s in slides if s.role)
    fonts = [f for f in (look.heading_font, look.body_font) if f]
    note = (f"Шаблон: {len(slides)} {_plural(len(slides), ('слайд', 'слайда', 'слайдов'))}, "
            f"главный цвет {look.palette[0]}" if look.palette else
            f"Шаблон: {len(slides)} {_plural(len(slides), ('слайд', 'слайда', 'слайдов'))}")
    if fonts:
        note += f", шрифт {fonts[0]}"
    return {
        "template_id": template_id,
        "filename": template.filename,
        "slides": len(slides),
        "roles": dict(roles),
        "palette": look.palette[:6],
        "fonts": list(dict.fromkeys(fonts + look.fonts))[:4],
        "heading_font": look.heading_font,
        "body_font": look.body_font,
        "preview_artifact_ids": [],
        "assistant_note": note + ".",
    }


def _brandbook_view(book: Any) -> dict[str, Any]:
    from deckdna.template.brandbook import plan_brand, summary, template_look

    project_id = book.project_id
    current = STORE.project_brand.get(project_id or "") or {}
    priority = current.get("priority", "brandbook") if current.get(
        "brandbook_id") == book.id else "brandbook"
    project = STORE.projects.get(project_id or "")
    conflicts: list[dict[str, Any]] = []
    if project is not None and project.template_id in STORE.templates:
        tpl = STORE.artifacts[STORE.templates[project.template_id].artifact_id].data
        conflicts = [c.model_dump(mode="json")
                     for c in plan_brand(template_look(tpl), book, priority).conflicts]
    color_conflicts = [c for c in conflicts if c["kind"] in ("color", "font")]
    if not book.colors or not book.fonts:
        note = ("Брендбук разобран частично: "
                + ("цвета не извлечены; " if not book.colors else "")
                + ("шрифты не извлечены; " if not book.fonts else "")
                + "полное соответствие шаблону пока не проверено.")
    elif project is None or project.template_id not in STORE.templates:
        note = "Правила извлечены; загрузите шаблон для проверки расхождений."
    elif color_conflicts and priority == "brandbook":
        note = (f"Брендбук расходится с шаблоном в {len(color_conflicts)} "
                f"{_plural(len(color_conflicts), ('месте', 'местах', 'местах'))}; "
                "беру цвета и шрифты брендбука.")
    elif color_conflicts:
        note = "Брендбук расходится с шаблоном; оставляю оформление шаблона."
    else:
        note = "Брендбук с шаблоном не спорит."
    return {**summary(book), "priority": priority, "conflicts": conflicts,
            "assistant_note": note, "analysis": STORE.brand_analysis.get(book.id)}


@app.get(f"{API_PREFIX}/brandbooks/{{brandbook_id}}", tags=["brandbooks"])
async def get_brandbook(brandbook_id: str) -> dict[str, Any]:
    return _require(STORE.brandbooks, brandbook_id, "brandbook").model_dump(mode="json")


@app.get(f"{API_PREFIX}/brandbooks/{{brandbook_id}}/summary", tags=["brandbooks"])
async def brandbook_summary(brandbook_id: str) -> dict[str, Any]:
    """Карточка брендбука: палитра, шрифты, конфликты с шаблоном проекта,
    приоритет (ADR-034 §1, ADR-033 §9)."""
    return _brandbook_view(_require(STORE.brandbooks, brandbook_id, "brandbook"))


class BrandbookPatch(BaseModel):
    priority: Literal["brandbook", "template"] | None = None
    analyze: bool = False
    force: bool = False
    provider_session_id: str | None = None
    use_llm: bool | None = None


_BRAND_ANALYZING: set[str] = set()


@app.patch(f"{API_PREFIX}/brandbooks/{{brandbook_id}}", tags=["brandbooks"])
async def patch_brandbook(brandbook_id: str, body: BrandbookPatch) -> dict[str, Any]:
    """Переключатель «чьё оформление главнее» для проекта брендбука."""
    book = _require(STORE.brandbooks, brandbook_id, "brandbook")
    if body.provider_session_id:
        _require_session(body.provider_session_id, book.project_id)
    if body.analyze and (body.force or book.id not in STORE.brand_analysis):
        if book.project_id not in STORE.projects:
            raise DeckDNAError("not_found", "brandbook project not found")
        from deckdna.template.brand_vision import analyze_brandbook

        with _JOB_LOCK:
            if book.id in _BRAND_ANALYZING:
                raise DeckDNAError("state_conflict", "brandbook analysis already running",
                                   http_status=409)
            _BRAND_ANALYZING.add(book.id)
        gateway = None
        current = (STORE.project_brand.get(book.project_id) or {}).get("brandbook_id")
        try:
            gateway = _gateway_for(body, book.project_id)
            if gateway is None:
                raise DeckDNAError("provider_capability_missing", "vision provider is unavailable",
                                   http_status=422)
            files = [(STORE.artifacts[aid].filename, STORE.artifacts[aid].data)
                     for aid in STORE.brand_sources.get(book.id, [])]
            source_book = book.model_copy(deep=True)
            if body.force:
                source_book.colors = [c for c in book.colors if ':vision:' not in (c.source or '')]
                source_book.fonts = [f for f in book.fonts if ':vision:' not in (f.source or '')]
                source_book.warnings = [w for w in book.warnings
                                        if not w.startswith('Vision-разбор:')]
            try:
                updated, labels = await analyze_brandbook(source_book, files, gateway)
                analysis_status = "completed"
            except Exception as exc:
                # мягкий фоллбэк: vision-разбор не обязателен — в книге уже
                # лежат детерминированно разобранные токены файла
                logger.warning(
                    "brandbook vision analysis failed (%s); keeping parsed tokens", exc
                )
                updated = source_book
                labels = []
                analysis_status = "fallback"
            with _JOB_LOCK:
                if (book.project_id not in STORE.projects
                        or current != (STORE.project_brand.get(book.project_id) or {}).get(
                            "brandbook_id")):
                    raise DeckDNAError("state_conflict", "active brandbook changed",
                                       http_status=409)
                book = updated
                for run in STORE.runs.values():
                    if (run.brand and run.brand.get('brandbook_id') == book.id
                            and not getattr(run, 'template_artifact_id', None)):
                        run.template_artifact_id = _store_artifact(
                            _run_template_bytes(run), type="template.branded", mime_type=_PPTX_MIME,
                            filename="template.pptx", project_id=book.project_id).id
                STORE.brandbooks[book.id] = book
                STORE.brand_analysis[book.id] = {"pages": labels, "status": analysis_status}
                STORE.brand_templates = {k: v for k, v in STORE.brand_templates.items()
                                         if k[1] != book.id}
        finally:
            with _JOB_LOCK:
                _BRAND_ANALYZING.discard(book.id)
            close = getattr(gateway, "aclose", None)
            if close:
                await close()
    if book.project_id and body.priority is not None:
        STORE.project_brand[book.project_id] = {"brandbook_id": book.id,
                                                "priority": body.priority}
    return _brandbook_view(book)


class RegenerateRequest(BaseModel):
    """ADR-034 §7: «Переделать слайд»."""

    action: str = Field(description="other_layout | shorter | other_chart | style:<strategy>")
    chart_type: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)
    text: str | None = Field(default=None, max_length=6000)


def _variant_slide(variant_id: str, slide_id: str) -> SlideInfo:
    """Слайд варианта по id SlideInfo (``sld_…``) или id слайда плана."""
    slides = STORE.slides.get(variant_id) or []
    found = next((s for s in slides if s.id == slide_id or s.slide_plan_id == slide_id), None)
    if found is None:
        raise DeckDNAError("not_found", f"slide not found in variant: {slide_id}")
    return found


def _run_pack(run: _InternalRun) -> ContentPack:
    """Пак, по которому собран прогон (добранная версия, если была)."""
    plan_pack = run.detail.content_pack_id
    candidates = [pid for pid in STORE.content_packs if _derived_from(pid, plan_pack)]
    return STORE.content_packs[candidates[-1] if candidates else plan_pack]


@app.post(f"{API_PREFIX}/variants/{{variant_id}}/slides/{{slide_id}}/regenerate",
          tags=["variants"])
async def regenerate_variant_slide(
    variant_id: str, slide_id: str, body: RegenerateRequest,
) -> dict[str, Any]:
    """ADR-034 §7: пересборка **одного** слайда — новая ревизия колоды, как
    repair; остальные слайды байт-в-байт те же. Аудит и превью слайда."""
    return await asyncio.to_thread(_regenerate_variant_slide, variant_id, slide_id, body)


def _regenerate_variant_slide(
    variant_id: str, slide_id: str, body: RegenerateRequest, *, queued: bool = False,
) -> dict[str, Any]:
    """Read, compute and publish under one OS-thread lock, with no await."""
    with _variant_lock(variant_id):
        from deckdna.generation.regenerate import regenerate_slide

        run, variant = _variant_or_404(variant_id)
        from deckdna.api.dependencies import is_current

        if not is_current(run.detail.project_id, run.detail.created_at):
            raise DeckDNAError("state_conflict", "variant belongs to an older project brief",
                               http_status=409)
        context_revision = STORE.assistant_progress.get(run.detail.project_id, {}).get(
            "context_revision", 1)
        if not queued and any(i["state"] in {"queued", "running"}
                              for i in getattr(run, "changes", {}).get(variant_id, [])):
            raise DeckDNAError("state_conflict", "use the active change queue", http_status=409)
        actual = run.deck_revision.get(variant_id, 1)
        if body.expected_revision is not None and body.expected_revision != actual:
            raise DeckDNAError("state_conflict", "deck revision changed", http_status=409,
                               details={"expected_revision": body.expected_revision,
                                        "actual_revision": actual})
        slide = _variant_slide(variant_id, slide_id)
        plan = run.variant_plans.get(variant_id)
        if plan is None or slide.slide_plan_id is None:
            raise DeckDNAError("not_implemented", "this slide has no plan to rebuild from")
        template = STORE.templates[run.detail.template_id]
        tpl_artifact = STORE.artifacts[template.artifact_id]
        pack = _run_pack(run)
        info = run.variant_reports.get(variant_id) or {}
        compose_slides = (info.get("compose_report") or {}).get("slides") or []
        current = next(
            ((c.get("exemplar") or {}).get("slide_part") for c in compose_slides
             if c.get("id") == slide.slide_plan_id),
            None,
        )
        deck = _current_deck(run, variant_id)

        def work() -> dict[str, Any]:
            with tempfile.TemporaryDirectory(prefix="deckdna-regen-") as tmp:
                tpl_path = Path(tmp) / tpl_artifact.filename
                tpl_bytes = _run_template_bytes(run)
                tpl_path.write_bytes(tpl_bytes)
                dna = STORE.design_dna.get(run.detail.template_id)
                if run.brand and run.brand.get("applied") and dna is not None:
                    dna = dna_builder.restyle_design_dna(dna, tpl_bytes)
                result = regenerate_slide(
                    tpl_path, deck.data, plan.model_copy(deep=True), pack,
                    slide.slide_plan_id, slide.index,
                    body.action, strategy=variant.strategy, chart_type=body.chart_type,
                    text=body.text,
                    current_exemplar=current,
                    design_dna=dna,
                )
                new_path = Path(tmp) / "deck.pptx"
                new_path.write_bytes(result.deck)
                from deckdna.planning.request_constraints import validate_deck_count

                validate_deck_count(plan.brief, new_path)
                validate_deck_count(run.request.brief, new_path)
                from deckdna.planning.locked_copy import (
                    replacement_kinds,
                    validate_locked_deck,
                    validate_locked_transition,
                )

                validate_locked_transition(
                    plan, result.plan, replacement_slide=slide.slide_plan_id,
                    replaced_kinds=replacement_kinds(body.action),
                )
                from deckdna.planning.mandatory import protect_mandatory_sections

                result.plan = protect_mandatory_sections(
                    result.plan, pack, required=run.request.brief.mandatory_sections,
                )
                validate_locked_deck(result.plan, new_path, compose_slides or None)
                new_revision = run.deck_revision.get(variant_id, 1) + 1
                audit_id = _audit_id_of(variant_id)
                fresh_runtime = audit_deck(new_path, deck_revision=new_revision,
                                           audit_run_id=audit_id or _new_id("audit"))
            return {"result": result, "revision": new_revision, "audit_id": audit_id,
                    "fresh": fresh_runtime}

        done = work()
        if (STORE.jobs[run.detail.job_id].state in {JobState.canceled, JobState.failed}
                or not is_current(run.detail.project_id, run.detail.created_at)
                or STORE.assistant_progress.get(run.detail.project_id, {}).get(
                    "context_revision", 1) != context_revision):
            raise DeckDNAError("state_conflict", "generation was superseded or canceled",
                               http_status=409)
        result, new_revision, audit_id = done["result"], done["revision"], done["audit_id"]
        fresh = [_contract_issue(raw.to_dict()) for raw in done["fresh"]]
        with _JOB_LOCK:
            if (STORE.jobs[run.detail.job_id].state in {JobState.canceled, JobState.failed}
                    or not is_current(run.detail.project_id, run.detail.created_at)
                    or STORE.assistant_progress.get(run.detail.project_id, {}).get(
                        "context_revision", 1) != context_revision):
                raise DeckDNAError("state_conflict", "generation was superseded or canceled",
                                   http_status=409)
            new_artifact = _store_artifact(
                result.deck, type="deck.pptx.revision", mime_type=_PPTX_MIME,
                filename=f"{variant.strategy}_rev{new_revision}.pptx",
                project_id=run.detail.project_id,
            )
            preview_id = None
            if result.preview_png:
                preview_id = _store_artifact(
                    result.preview_png, type="slide.preview.png", mime_type="image/png",
                    filename=f"{variant.strategy}_rev{new_revision}_slide{slide.index}.png",
                    project_id=run.detail.project_id,
                ).id
            if audit_id is not None:
                STORE.issues[audit_id] = fresh
                audit = STORE.audits[audit_id]
                by_severity, by_type = _issue_summaries(fresh)
                audit.deck_revision = new_revision
                audit.contextual_status = "pending"
                audit.issue_count = len(fresh)
                audit.summary_by_severity = by_severity
                audit.summary_by_type = by_type
            run.deck_revision[variant_id] = new_revision
            run.variant_artifacts[variant_id] = {"pptx": new_artifact.id}
            run.variant_plans[variant_id] = result.plan
            edited_slide = next(s for s in result.plan.slides if s.id == slide.slide_plan_id)
            if run.detail.variants[0].id == variant_id:
                run.detail = run.detail.model_copy(update={'deck_plan': result.plan})
            if body.text:
                info["source_text"] = (info.get("source_text") or "") + "\n" + body.text
            for c in compose_slides:
                if c.get("id") == slide.slide_plan_id:
                    c.setdefault("exemplar", {})["slide_part"] = result.exemplar
                    c['title'] = edited_slide.title_intent
            # остальные слайды байт-в-байт те же — их превью прежние
            STORE.slides[variant_id] = [
                sl.model_copy(update={
                    "revision": new_revision,
                    **({"preview_artifact_id": preview_id, 'title': edited_slide.title_intent}
                       if sl.id == slide.id else {}),
                })
                for sl in STORE.slides.get(variant_id, [])
            ]
            idx = next(i for i, v in enumerate(run.detail.variants) if v.id == variant_id)
            run.detail.variants[idx] = run.detail.variants[idx].model_copy(update={
                "deck_artifact_id": new_artifact.id, "montage_artifact_id": None,
                "export_ids": [], "contextual_issues": None,
                "metrics": run.detail.variants[idx].metrics.model_copy(
                    update={"issues_total": len(fresh)}),
            })
        slide_issues = [i for i in fresh if i.slide_index == slide.index - 1]
        notes = {
            "other_layout": "Поменял раскладку слайда.",
            "shorter": "Сделал слайд короче.",
            "other_chart": "Поменял график.",
        }
        return {
            "variant_id": variant_id,
            "slide": _slide_or_404(slide.id).model_dump(mode="json"),
            "deck_revision": new_revision,
            "deck_artifact_id": new_artifact.id,
            "exemplar": result.exemplar,
            "preview_artifact_id": preview_id,
            "issues": [i.model_dump(mode="json") for i in slide_issues],
            "assistant_note": notes.get(body.action, "Пересобрал слайд в другом стиле."),
        }


@app.get(f"{API_PREFIX}/variants/{{variant_id}}/slide-drafts", tags=["variants"])
async def get_slide_drafts(variant_id: str) -> dict[str, Any]:
    """ADR-036: read-only recovery of cards on refresh; never starts rendering."""
    run, variant = _variant_or_404(variant_id)
    with _JOB_LOCK:
        drafts = getattr(run, "slide_drafts", {}).get(variant_id, {})
        return {"variant_id": variant_id, "state": variant.status.value,
                "slides": [dict(drafts[i]) for i in sorted(drafts)]}


@app.get(f"{API_PREFIX}/slides/{{slide_id}}/explain", tags=["variants"])
async def explain_slide(slide_id: str) -> dict[str, Any]:
    """ADR-034 §7 / ADR-033 §10: панель «Почему так» — данные → профиль →
    задача → почему этот тип → альтернативы → выводы."""
    from deckdna.analysis.explain import view_explanation
    from deckdna.analysis.pipeline import analyze_pack
    from deckdna.planning.data_story import brief_text

    slide = _slide_or_404(slide_id)
    run, _variant = _variant_or_404(slide.variant_id)
    plan = run.variant_plans.get(slide.variant_id)
    plan_slide = (next((s for s in plan.slides if s.id == slide.slide_plan_id), None)
                  if plan else None)
    card = (plan_slide.chart or {}) if plan_slide is not None else {}
    base = {"slide_id": slide.id, "slide_plan_id": slide.slide_plan_id,
            "title": slide.title}
    if not card.get("view_id"):
        return {**base, "dataset": None,
                "assistant_note": "На этом слайде нет графика из данных."}
    pack = _run_pack(run)
    for analysis in analyze_pack(pack, brief_text(plan.brief)):
        for va in analysis.views:
            if va.view.id == card["view_id"]:
                exp = view_explanation(analysis, va, card.get("type"))
                return {
                    **base,
                    "dataset": {"id": analysis.dataset.id, "title": analysis.dataset.title,
                                "file": analysis.dataset.file_name,
                                "rows": analysis.dataset.row_count},
                    "profile_summary": analysis.profile.summary_ru(),
                    **exp,
                    "assistant_note": va.why,
                }
    return {**base, "dataset": None, "assistant_note": "Данные слайда не найдены в паке."}


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
            var_report = (run.variant_reports.get(variant.id) or {}).get("compose_report")
            design_dna = STORE.design_dna.get(run.detail.template_id)
            det_issues = STORE.issues.get(audit.id)
            if det_issues is None:
                det_issues = audit_deck(
                    deck_path, deck_revision=audit.deck_revision, audit_run_id=audit.id
                )
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
                    deterministic_issues=det_issues,
                    compose_report=var_report,
                    design_dna=design_dna,
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
    from deckdna.api.audit_runs import create_audit_snapshot

    return await create_audit_snapshot(variant_id, body)


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

        # hardening check #14: pipeline.generate() threads protected_slide_
        # indices through auto-fix/compose, but API repair never loaded
        # this config at all -- a protected slide's issues were fair game
        # for both the model rewrite pass below and apply_repairs.
        protected = frozenset(load_generation_config().protected_slide_indices)
        copy_plan = run.variant_plans.get(variant_id) or run.detail.deck_plan
        if copy_plan is not None:
            from deckdna.planning.locked_copy import locked_slide_indices

            copy_reports = ((run.variant_reports.get(variant_id) or {})
                            .get("compose_report") or {}).get("slides")
            protected |= locked_slide_indices(copy_plan, copy_reports)

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

        from deckdna.planning.request_constraints import validate_deck_count

        count_brief = run.variant_plans.get(variant_id) or run.detail.deck_plan
        validate_deck_count(count_brief.brief if count_brief else run.request.brief, out_pptx)
        validate_deck_count(run.request.brief, out_pptx)

        if count_brief is not None:
            from deckdna.planning.locked_copy import validate_locked_deck
            from deckdna.planning.mandatory import protect_mandatory_sections

            compose_slides = ((run.variant_reports.get(variant_id) or {})
                              .get("compose_report") or {}).get("slides")
            protected_plan = protect_mandatory_sections(
                count_brief, _run_pack(run), required=run.request.brief.mandatory_sections,
            )
            validate_locked_deck(protected_plan, out_pptx, compose_slides)

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
    return await asyncio.to_thread(_create_export_sync, variant_id, body)


def _create_export_sync(variant_id: str, body: ExportRequest) -> ExportAccepted:
    # All formats and the export record must describe one committed revision.
    with _variant_lock(variant_id):
        run, variant = _variant_or_404(variant_id)
        if any(i["state"] in {"queued", "running"}
               for i in getattr(run, "changes", {}).get(variant_id, [])):
            raise DeckDNAError("state_conflict", "wait for pending changes before exporting",
                               http_status=409)
        revision = run.deck_revision.get(variant_id, 1)
        artifacts: list[ExportArtifact] = []
        for fmt in dict.fromkeys(body.formats):
            # PPTX — всегда текущая ревизия; PDF/паспорт/HTML — из кеша
            # ревизии, а после repair пересобираются лениво (B2, D5)
            stored = _ensure_artifact(run, variant_id, fmt)
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
    ascii_name = safe_name.encode("ascii", "ignore").decode().strip() or "download"
    etag = f'"{artifact.sha256}"'
    headers = {
        "Content-Disposition": (
            f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(safe_name)}'
        ),
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
async def ready() -> Any:
    if settings.durable_queue:
        from deckdna.api.dispatch import queue

        try:
            async with queue().connection() as connection:
                await connection.fetchval('SELECT 1')
        except Exception:
            return JSONResponse(status_code=503, content={
                'status': 'not_ready', 'component': 'queue_database',
            })
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
            # ADR-036: journal events are delivered while the job runs.
            "sse_progress": True,
            "progressive_previews": True,
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
            # ADR-030/034: добор контента (чипы режима текста на брифе и плане)
            "content_fill": True,
        },
        # какой провайдер стоит за contextual_audit: session | server | mock | none
        "contextual_audit_mode": contextual_mode,
    }


# ADR-034 §8: loaded after the shared pipeline helpers, avoiding import cycles.
from deckdna.api.assistant_session import router as _session_router  # noqa: E402

app.include_router(_session_router)
from deckdna.api.changes import router as _changes_router  # noqa: E402

app.include_router(_changes_router)
from deckdna.api.commands import router as _commands_router  # noqa: E402

app.include_router(_commands_router)
from deckdna.api.dispatch import router as _dispatch_router  # noqa: E402

app.include_router(_dispatch_router)
from deckdna.api.agent import router as _agent_router  # noqa: E402

app.include_router(_agent_router)


if settings.durable_queue and (not settings.state_snapshot or len(settings.worker_token) < 16):
    raise ValueError('Durable queue requires state_snapshot and worker_token (at least 16 chars)')
if settings.state_snapshot and load_store(
    STORE, _snapshot_path(), preserve_queued=settings.durable_queue,
    require_integrity=settings.durable_queue,
):
    logger.info("state restored from %s", _snapshot_path())
