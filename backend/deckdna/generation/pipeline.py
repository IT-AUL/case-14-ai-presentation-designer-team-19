"""End-to-end generation pipeline (Wave 1, synchronous, single process).

Wires the independently-tested stages into one call:

  content file ──parse──▶ ContentPack ──plan──▶ DeckPlan ──compose──▶ out.pptx
      │                                                            │
      └── brief (CLI/API) ────────────────────────────────────────┤
                                                                    ▼
QualityPassport ◀── assemble ── audit issues ◀── audit_deck ── render_pdf → .pdf

Each stage is wrapped in a typed ``DeckDNAError`` with ``stage`` set —
failures surface with the stage that produced them, never swallowed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import tempfile
import threading
import time
import zipfile
from collections import Counter
from collections.abc import Callable, Coroutine, Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from deckdna.audit.basic import audit_deck, audit_filler_slides
from deckdna.audit.config import default_audit_config
from deckdna.audit.contextual import DEFAULT_CALL_TIMEOUT_S as _CTX_DEFAULT_CALL_TIMEOUT_S
from deckdna.audit.contextual import DEFAULT_STAGE_BUDGET_S as _CTX_DEFAULT_BUDGET_S
from deckdna.audit.contextual import PROMPT_NAME as _CTX_PROMPT_NAME
from deckdna.audit.contextual import run_contextual_audit
from deckdna.audit.issues import AuditIssue
from deckdna.contracts.content_pack import ContentPack
from deckdna.contracts.deck_plan import Brief, DeckPlan, Kind
from deckdna.contracts.design_dna import DesignDNA
from deckdna.contracts.quality_passport import Fallback, ModelProfile, QualityPassport
from deckdna.contracts.serialize import to_schema_dict
from deckdna.contracts.variant_spec import Strategy
from deckdna.errors import DeckDNAError
from deckdna.evaluation import measures
from deckdna.evaluation.model_licenses import load_manifest
from deckdna.evaluation.quality_passport import assemble_quality_passport
from deckdna.ingestion.content_parsers import parse_file
from deckdna.ingestion.restructure import (
    needs_restructure,
    restructure_pack,
    restructure_pack_sync,
)
from deckdna.planning import content_style, image_brief
from deckdna.planning.agenda import sync_agenda
from deckdna.planning.config import load_generation_config
from deckdna.planning.content_fill import annotate_unit_origins
from deckdna.planning.evidence import build_evidence_graph
from deckdna.planning.story_director import (
    LLM_PLANNER_VERSION,
    STORYLINE_PROMPT,
    plan_deck,
    plan_deck_llm,
)
from deckdna.planning.story_director_v2 import plan_deck_llm_v2
from deckdna.planning.talk_track import PROMPT_NAME as TALK_TRACK_PROMPT
from deckdna.planning.talk_track import TalkTrackReport, write_notes_to_pptx, write_talk_tracks
from deckdna.pptx.cloning import layout_fit
from deckdna.pptx.cloning.rerank import (
    EXEMPLAR_RERANK_PROMPT,
    llm_exemplar_choices,
)
from deckdna.pptx.composing.icon_place import place_icons
from deckdna.pptx.composing.minimal import generate_deck, slide_needs
from deckdna.pptx.composing.text_budget import template_budgets
from deckdna.pptx.exporting.render import render_pdf
from deckdna.pptx.fitting import budget_fit, text_fit
from deckdna.pptx.opc.package import OpcPackage
from deckdna.providers.base import ModelGateway
from deckdna.providers.openai_compat import provider_deadline
from deckdna.providers.profiles import ModelProfileSource
from deckdna.providers.prompts import load_prompt
from deckdna.repair.apply import apply_repairs
from deckdna.repair.planner import plan_repairs_with_report
from deckdna.research import fill_content
from deckdna.template.icons import can_label, has_template_icons, icon_library
from deckdna.template.synthesize import prepared_template_path

STAGE_INGESTION = "content_ingestion"
STAGE_PLANNING = "planning"
STAGE_COMPOSING = "composing.deck"
STAGE_AUDIT = "audit.basic"
STAGE_AUDIT_CTX = "audit.contextual"
STAGE_RENDER = "exporting.render"
STAGE_PASSPORT = "evaluation.quality_passport"
STAGE_RESEARCH = "research.fill"

# Публичные названия этапов для интерфейса (VariantSummary.stage,
# passport.metrics.timings.per_stage): content → plan → compose → audit
# → render → passport.
PUBLIC_STAGES = ("content", "research", "plan", "compose", "audit", "render", "passport")

# Правила, которые конвейер чинит САМ до сохранения ревизии 1 (Q2):
# исправление детерминированно, не теряет содержимое и проверяется
# повторным аудитом. Остальное — выбор пользователя.
AUTO_FIX_RULES = frozenset(
    {"template.color_palette", "image.aspect_ratio", "accessibility.contrast"}
)


def _stage[T](stage: str, fn: Callable[[], T]) -> T:
    """Run one pipeline stage; unexpected errors become typed with the
    stage attached. DeckDNAError already carries its own stage."""
    try:
        return fn()
    except DeckDNAError as exc:
        if exc.stage is None:
            exc.stage = stage
        raise
    except Exception as exc:  # noqa: BLE001 — boundary: retype for callers
        raise DeckDNAError(
            code="internal_error",
            message=f"{type(exc).__name__}: {exc}",
            stage=stage,
        ) from exc


def _skip_without_vision[T](skipped: dict[str, str], fn: Callable[[], T]) -> T | None:
    """ADR-031 §4: стадия на зрении модели при текстовой модели пропускается.

    ``provider_capability_missing`` (у провайдера нет vision-модели, например
    пустой ``DECKDNA_MODEL_VISION``) — не отказ генерации: аудит и прочие
    VLM-стадии проверяют качество, колода выходит и без них. Причина пишется
    в *skipped* и раскрывается в отчёте и паспорте. Остальные ошибки
    провайдера идут как раньше."""
    try:
        return fn()
    except DeckDNAError as exc:
        if exc.code != "provider_capability_missing":
            raise
        skipped["reason"] = f"provider_capability_missing: {exc.message}"
        return None


def _run_async_gw[T](gateway: Any, coro: Coroutine[Any, Any, T]) -> T:
    """Синхронный мост для async-вызовов провайдера (LLM-план, VLM-аудит),
    опционально переиспользующий один HTTP-клиент *gateway* на все вызовы
    внутри этого asyncio.run и закрывающий его на выходе.

    generate() остаётся синхронным по стратегии Wave-1; вызов внутри уже
    запущенного event loop'а честно упадёт RuntimeError'ом (sync
    FastAPI-эндпоинты идут через threadpool — там loop'а нет).

    Фаза 3: если *gateway* поддерживает opt-in ``session()`` (см.
    ``providers/openai_compat.py`` — duck-typed, как ``aclose``, не часть
    frozen ``ModelGateway``), все HTTP-вызовы *coro* делят один
    ``httpx.AsyncClient`` с keep-alive пулом вместо TLS-хендшейка на
    каждый POST — с батчингом Фазы 1/ADR-012 таких вызовов внутри одной
    стадии заметно больше одного. Без ``session`` (MockProvider и
    прочие) — поведение не меняется. Один provider-объект переживает
    НЕСКОЛЬКО таких asyncio.run в рамках одного ``generate()``
    (план/rerank/fit/аудит — разные циклы) — сессия/клиент каждого
    закрывается явно здесь, а не один раз в самом конце: иначе пул
    соединений раннего этапа утёк бы, когда его loop закрылся, и
    вызывающая сторона (CLI, тесты, вызывающие ``generate()`` напрямую)
    вообще не обязана знать про lifecycle клиента gateway.
    """

    async def _scoped() -> T:
        session = getattr(gateway, "session", None)
        try:
            if session is None:
                return await coro
            async with session():
                return await coro
        finally:
            close = getattr(gateway, "aclose", None)
            if close is not None:
                await close()

    return asyncio.run(_scoped())


class _StageClock:
    """Тайминги публичных этапов + колбэк «сейчас идёт этап N»."""

    def __init__(self, on_stage: Callable[[str], None] | None) -> None:
        self._on_stage = on_stage
        self.items: list[dict[str, Any]] = []

    @contextmanager
    def track(self, stage: str):
        if self._on_stage is not None:
            self._on_stage(stage)
        started = time.monotonic()
        try:
            yield
        finally:
            self.items.append(
                {
                    "stage": stage,
                    "seconds": round(time.monotonic() - started, 3),
                    "cached": False,
                }
            )


def _restore_protected_verbatim(
    original: Path, fixed: Path, protected: Iterable[int] | None
) -> None:
    """Вернуть защищённым слайдам (OR-031) исходные байты после
    пересохранения пакета python-pptx: ``prs.save`` заново
    сериализует все части (иной формат XML-декларации), а обязательные
    слайды обязаны остаться байт-в-байт. Действия на них и так
    отклонены гардом, поэтому подмена возвращает ровно то, что было."""
    indices = sorted(set(protected or ()))
    if not indices:
        return
    from pptx import Presentation

    slides = list(Presentation(str(original)).slides)
    names: set[str] = set()
    for i in indices:
        if 0 <= i < len(slides):
            part = str(slides[i].part.partname).lstrip("/")
            names.add(part)
            directory, _, base = part.rpartition("/")
            names.add(f"{directory}/_rels/{base}.rels")
    tmp = fixed.with_suffix(".restored")
    with zipfile.ZipFile(original) as zo, zipfile.ZipFile(fixed) as zf, zipfile.ZipFile(
        tmp, "w", zipfile.ZIP_DEFLATED
    ) as out:
        original_names = set(zo.namelist())
        for info in zf.infolist():
            if info.filename in names and info.filename in original_names:
                out.writestr(info.filename, zo.read(info.filename))
            else:
                out.writestr(info, zf.read(info.filename))
    tmp.replace(fixed)


def _max_auto_repair_cycles() -> int:
    """configs/audit.default.yaml → repair.max_iterations (в синхронном запросе не больше 1)."""
    rep = default_audit_config().raw.get("repair") or {}
    return min(1, max(0, int(rep.get("max_iterations", 1))))


def deterministic_audit_worsened(
    before: Iterable[AuditIssue],
    after: Iterable[AuditIssue],
    *,
    target_rules: Iterable[str] | None = None,
) -> bool:
    """Проверить, ухудшился ли детерминированный аудит после патча.

    Патч отклоняется (возвращает True), если:
    - выросло число blocker, error или warning в детерминированном аудите;
    - выросло общее число детерминированных проблем;
    - для любого нецелевого правила выросло число проблем.
    """
    before_det = [i for i in before if getattr(i, "deterministic", True)]
    after_det = [i for i in after if getattr(i, "deterministic", True)]
    if len(after_det) > len(before_det):
        return True
    for sev in ("blocker", "error", "warning"):
        if sum(1 for i in after_det if i.severity == sev) > sum(
            1 for i in before_det if i.severity == sev
        ):
            return True
    targets = set(target_rules or ())
    if targets:
        before_other = Counter(
            i.rule_code for i in before_det if i.rule_code not in targets
        )
        after_other = Counter(
            i.rule_code for i in after_det if i.rule_code not in targets
        )
        for rule, cnt in after_other.items():
            if cnt > before_other.get(rule, 0):
                return True
        before_target = Counter(
            i.rule_code for i in before_det if i.rule_code in targets
        )
        after_target = Counter(
            i.rule_code for i in after_det if i.rule_code in targets
        )
        for rule, cnt in after_target.items():
            if cnt > before_target.get(rule, 0):
                return True
    return False


def _auto_fix(
    deck: Path,
    issues: list[AuditIssue],
    protected: Iterable[int] | None,
    *,
    max_cycles: int | None = None,
) -> tuple[list[AuditIssue], dict[str, int], str | None]:
    """Безопасные автоисправления до ревизии 1 (Q2).

    В синхронном запросе выполняется не больше 1 цикла автоисправления.
    Планирует действия только для ``AUTO_FIX_RULES``, применяет их к
    копии, перемеряет аудитом и принимает результат лишь если (а) число
    проблем целевых правил упало и (б) детерминированный аудит не
    ухудшился (число blocker/error/warning и прочих правил не выросло).
    Иначе колода остаётся как была — откат.

    Возвращает (issues итоговой колоды, {rule: исправлено}, причина
    отказа либо None).
    """
    cycles_cap = (
        _max_auto_repair_cycles()
        if max_cycles is None
        else min(1, max(0, int(max_cycles)))
    )
    if cycles_cap <= 0:
        return issues, {}, None
    candidates = [
        i for i in issues if i.rule_code in AUTO_FIX_RULES and i.repairable
    ]
    if not candidates:
        return issues, {}, None
    plan = plan_repairs_with_report(candidates)
    if not plan.actions:
        return issues, {}, None
    with tempfile.TemporaryDirectory(prefix="deckdna-autofix-") as tmp:
        fixed_path = Path(tmp) / "fixed.pptx"
        apply_repairs(deck, plan.actions, fixed_path, protected=protected)
        _restore_protected_verbatim(deck, fixed_path, protected)
        after = audit_deck(fixed_path, deck_revision=1)
        before_by_rule = Counter(
            i.rule_code for i in issues if i.rule_code in AUTO_FIX_RULES
        )
        after_by_rule = Counter(
            i.rule_code for i in after if i.rule_code in AUTO_FIX_RULES
        )
        gained = {
            rule: before_by_rule[rule] - after_by_rule[rule]
            for rule in before_by_rule
            if before_by_rule[rule] > after_by_rule[rule]
        }
        if not gained:
            return issues, {}, "auto-fix не уменьшил число проблем — откат"
        if deterministic_audit_worsened(issues, after, target_rules=AUTO_FIX_RULES):
            return (
                issues,
                {},
                "auto-fix ухудшил детерминированный аудит (новые error/blocker/warning) — откат",
            )
        shutil.copyfile(fixed_path, deck)
    return after, gained, None


def _provenance_inputs(
    gateway: ModelGateway | None,
    plan: DeckPlan,
    contextual_ran: bool,
    rerank_accepted: bool = False,
    fit_accepted: bool = False,
    style_accepted: bool = False,
    layout_fit_accepted: bool = False,
    images_generated: bool = False,
    notes_accepted: bool = False,
) -> tuple[dict[str, str], list[ModelProfile]]:
    """Фактически использованные промпты и модели прогона для паспорта.

    Промпты — только те, что реально ушли в gateway И были приняты:
    storyline при LLM-планировщике (версия уже честно записана plan'ом
    из реестра), slide_checks при отработавшем contextual audit,
    exemplar_rerank при принятом rerank'е (маркер 'llm'), content_style
    при принятой стилизации (хотя бы один content_unit переписан),
    layout_fit (ADR-016 Stage 3, v3-пайплайн) — отдельным флагом, не тем
    же rerank_accepted: exemplar_rerank и layout_fit — разные промпты,
    хоть оба и решают «какой эталон» (rerank_accepted остаётся True
    только для v1/v2-пути через llm_exemplar_choices).
    Модели — только успешно завершённые вызовы, о которых провайдер
    отчитался через опциональный ModelProfileSource (frozen-протокол
    его не требует → gateway без интроспекции даёт честный пустой
    список, а не гадание) И чья стадия принята в результат: роль 'text'
    засчитывается при принятом LLM-плане, rerank'е ИЛИ стилизации
    (text_json мог вернуть схему-валидный, но семантически отвергнутый
    ответ → fallback — тогда text-модель не использована), 'vision' —
    только при contextual_ran, 'image' (ADR-017) — только когда
    ``images_generated`` (хотя бы один слайд реально получил
    сгенерированную картинку — см. image_brief.generate_images'а
    непустой возврат); роли без стадии в этом пайплайне (embed и пр.) не
    засчитываются. Нет отдельного prompt_versions-элемента для image —
    промпт строится детерминированно (image_brief.build_prompt), не из
    versioned YAML-файла реестра, так что там нечего версионировать;
    модель всё равно попадает в model_profiles — этого достаточно, чтобы
    жюри видело, что картинка реально сгенерирована, а не хардкод.
    license/size_b обогащаются из configs/model_licenses.yaml; модель вне
    manifest-а остаётся с None-полями.
    """
    prompt_versions: dict[str, str] = {}
    if plan.provenance.planner == LLM_PLANNER_VERSION:
        prompt_versions[load_prompt(STORYLINE_PROMPT).name] = (
            plan.provenance.prompt_version
        )
    if contextual_ran:
        spec = load_prompt(_CTX_PROMPT_NAME)
        prompt_versions[spec.name] = spec.version
    if rerank_accepted:
        spec = load_prompt(EXEMPLAR_RERANK_PROMPT)
        prompt_versions[spec.name] = spec.version
    if fit_accepted:
        spec = load_prompt(text_fit.PROMPT_NAME)
        prompt_versions[spec.name] = spec.version
    if style_accepted:
        spec = load_prompt(content_style.PROMPT_NAME)
        prompt_versions[spec.name] = spec.version
    if layout_fit_accepted:
        spec = load_prompt(layout_fit.LAYOUT_FIT_PROMPT)
        prompt_versions[spec.name] = spec.version
    if notes_accepted:
        spec = load_prompt(TALK_TRACK_PROMPT)
        prompt_versions[spec.name] = spec.version

    # Роль -> приняла ли её вывод соответствующая стадия пайплайна.
    stage_accepted = {
        "text": plan.provenance.planner == LLM_PLANNER_VERSION
        or rerank_accepted
        or fit_accepted
        or layout_fit_accepted
        or style_accepted
        or notes_accepted,
        "vision": contextual_ran,
        "image": images_generated,
    }
    profiles: list[ModelProfile] = []
    if isinstance(gateway, ModelProfileSource):
        manifest = {
            m.get("model_id"): m for m in load_manifest().get("models") or []
        }
        used = {
            role: model_id
            for role, model_id in gateway.used_model_ids().items()
            if stage_accepted.get(role, False)
        }
        for model_id in dict.fromkeys(used.values()):
            entry = manifest.get(model_id) or {}
            profiles.append(
                ModelProfile(
                    model_id=model_id,
                    provider=gateway.provider_name,
                    size_b=entry.get("size_b"),
                    license=entry.get("license"),
                )
            )
    return prompt_versions, profiles


def _contextual_enabled() -> bool:
    """configs/audit.default.yaml → contextual.enabled (по умолчанию true)."""
    ctx = default_audit_config().raw.get("contextual") or {}
    return bool(ctx.get("enabled", True))


def _contextual_deadlines() -> tuple[float, float]:
    """configs/audit.default.yaml → (call_timeout_seconds, budget_seconds).

    Фаза 4 страховочные дедлайны (см. audit/contextual.py) — падают на
    модульные дефолты, если ключи не заданы в конфиге."""
    ctx = default_audit_config().raw.get("contextual") or {}
    return (
        float(ctx.get("call_timeout_seconds", _CTX_DEFAULT_CALL_TIMEOUT_S)),
        float(ctx.get("budget_seconds", _CTX_DEFAULT_BUDGET_S)),
    )


def _contextual_max_concurrent() -> int:
    """configs/audit.default.yaml → contextual.max_concurrent_calls (default 3)."""
    ctx = default_audit_config().raw.get("contextual") or {}
    return max(1, int(ctx.get("max_concurrent_calls", 3)))


def _text_fit_enabled() -> bool:
    """configs/generation.default.yaml → fitting.llm_rewrite (default true)."""
    fit = load_generation_config().raw.get("fitting") or {}
    return bool(fit.get("llm_rewrite", True))


def _theme_palette(design_dna: DesignDNA | None) -> list[str]:
    """Акценты и тёмный цвет темы шаблона — для иллюстраций в его палитре."""
    if design_dna is None or not design_dna.declared.themes:
        return []
    colors = design_dna.declared.themes[0].colors
    out = []
    for name in ("accent1", "accent2", "accent3", "dk2", "accent4"):
        value = colors.get(name)
        hexv = getattr(value, "root", value)
        if isinstance(hexv, str) and hexv.lstrip("#") and hexv.upper() not in out:
            out.append(hexv.upper())
    return out


def _icons_enabled() -> bool:
    """configs/generation.default.yaml → composition.icons (default true)."""
    comp = load_generation_config().raw.get("composition") or {}
    return bool(comp.get("icons", True))


_ICON_BRIEF_RE = re.compile(r"(иконк|пиктограмм|значк|icon|pictogram)", re.IGNORECASE)


def brief_requests_icons(brief: Brief, plan: DeckPlan | None = None) -> bool:
    """Просит ли бриф или план размещения иконок/пиктограмм."""
    for value in brief.model_dump().values():
        if isinstance(value, str) and _ICON_BRIEF_RE.search(value):
            return True
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and _ICON_BRIEF_RE.search(item):
                    return True
    if plan is not None:
        for slide in plan.slides:
            if any(u.kind == Kind.icon for u in slide.content_units):
                return True
    return False


def _should_run_icons(
    template_path: Path, brief: Brief, plan: DeckPlan, gateway: Any
) -> bool:
    """Пропуск этапа icons, если в шаблоне нет иконок/пиктограмм и бриф их не просит."""
    if gateway is None or not _icons_enabled() or not can_label(gateway):
        return False
    return has_template_icons(template_path) or brief_requests_icons(brief, plan)


async def _place_template_icons(template_path: Path, deck: Path, gateway: Any) -> dict:
    icons = await icon_library(template_path, gateway)
    if not icons:
        return {"library": 0}
    report = await place_icons(str(deck), icons, gateway)
    return {"library": len(icons), **report}


def _budget_fit_enabled() -> bool:
    """configs/generation.default.yaml → fitting.budget_fit (default true)."""
    fit = load_generation_config().raw.get("fitting") or {}
    return bool(fit.get("budget_fit", True))


@dataclass
class _SharedVariantPrep:
    event: threading.Event = field(default_factory=threading.Event)
    ready: bool = False
    error: BaseException | None = None
    plan: DeckPlan | None = None
    primary_strategy: Strategy | None = None
    primary_choices: list[Any] | None = None
    exemplar_rerank: str = "deterministic"
    generated_images: dict[str, bytes] = field(default_factory=dict)
    budget_report: budget_fit.BudgetFitReport | None = None
    notes_report: TalkTrackReport | None = None
    used_models: dict[str, str] = field(default_factory=dict)
    fallbacks: list[Fallback] = field(default_factory=list)


_SHARED_PREP_LOCK = threading.Lock()
_SHARED_PREP_CACHE: dict[tuple[Any, ...], _SharedVariantPrep] = {}
_MAX_SHARED_PREP_ENTRIES = 16


def _gateway_share_key(gateway: Any) -> tuple[Any, ...]:
    base_url = getattr(gateway, "_base_url", None)
    model_text = getattr(gateway, "_model_text", None)
    if base_url and model_text:
        return ("openai", str(base_url), str(model_text))
    return ("gw", id(gateway))


_PROVIDER_DEGRADABLE_CODES = frozenset(
    {
        "provider_unavailable",
        "provider_timeout",
        "structured_output_invalid",
        "provider_rate_limited",
    }
)


def _model_fit(
    deck: Path,
    issues: list[AuditIssue],
    gateway: ModelGateway,
    protected: Iterable[int] | None,
    language: str,
) -> tuple[list[AuditIssue], int, str | None]:
    """Переписать моделью переполненный текст (навык text_fit) до ревизии 1.

    Как и детерминированный auto-fix: результат принимается, только если
    повторный аудит показал меньше проблем переполнения и детерминированный
    аудит не ухудшился. Защищённые слайды не трогаются.
    Возвращает (issues колоды, сколько проблем исправлено, причина отказа).
    """
    if _max_auto_repair_cycles() <= 0:
        return issues, 0, None
    guard = set(protected or ())
    candidates = [
        i
        for i in issues
        if i.rule_code in text_fit.FIT_RULES
        and i.repairable
        and (i.slide_index is None or i.slide_index not in guard)
    ]
    if not candidates:
        return issues, 0, None
    with tempfile.TemporaryDirectory(prefix="deckdna-fit-") as tmp:
        fitted = Path(tmp) / "fitted.pptx"
        report = _run_async_gw(
            gateway,
            text_fit.fit_texts(deck, candidates, gateway, fitted, language=language),
        )
        if not report.rewritten:
            return issues, 0, "модель не дала пригодного текста"
        _restore_protected_verbatim(deck, fitted, protected)
        after = audit_deck(fitted, deck_revision=1)

        def n_fit(items: list[AuditIssue]) -> int:
            return sum(1 for i in items if i.rule_code in text_fit.FIT_RULES)

        gained = n_fit(issues) - n_fit(after)
        if gained <= 0:
            return issues, 0, "переписанный текст всё ещё не помещается — откат"
        if deterministic_audit_worsened(issues, after, target_rules=text_fit.FIT_RULES):
            return issues, 0, "переписывание породило новые error/blocker/warning — откат"
        shutil.copyfile(fitted, deck)
    return after, gained, None


def _rerank_enabled() -> bool:
    """configs/generation.default.yaml → retrieval.vlm_rerank (default true)."""
    ret = load_generation_config().raw.get("retrieval") or {}
    return bool(ret.get("vlm_rerank", True))


def planning_pipeline_version() -> str:
    """configs/generation.default.yaml → planning.pipeline_version.

    "v3" (default since 27.09, live-validated) — ADR-016 Stage 1
    (structure.py) + Stage 2 Writer (content_writer.py) + Stage 3
    Layout-fit (layout_fit.py): exemplar picked FROM the real written
    text, content_style skipped (Stage 3 already adapts the final text).
    "v2" — Stage 1+2 only, exemplar still picked the old way (rerank.py).
    "v1" — legacy plan_deck_llm (ADR-012), content_units verbatim from a
    single evidence_ids selection. Both kept as instant, no-code-change
    rollback levers if v3 misbehaves on a live run — same pattern as
    content_styling.enabled/fitting.llm_rewrite."""
    planning = load_generation_config().raw.get("planning") or {}
    return str(planning.get("pipeline_version", "v3"))


def generate(
    template_path: str | Path,
    content_path: str | Path,
    brief: Brief | dict[str, Any],
    out_dir: str | Path,
    protected_slide_indices: Iterable[int] | None = None,
    gateway: ModelGateway | None = None,
    strategy: Strategy | str = Strategy.balanced,
    deck_plan: DeckPlan | None = None,
    on_stage: Callable[[str], None] | None = None,
    design_dna: DesignDNA | None = None,
    content_pack: ContentPack | None = None,
    content_fill: dict[str, Any] | None = None,
    exemplar_overrides: dict[str, str] | None = None,
    on_render: Callable[[Path], None] | None = None,
    on_slide_event: Callable[[str, dict[str, Any]], None] | None = None,
    is_canceled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Run the full DeckDNA pipeline and return a JSON-serialisable report.

    ADR-034 §5–§6: *exemplar_overrides* (id слайда → слайд шаблона) — образцы
    пробных слайдов стиля, полная колода берёт те же; *on_render(pdf)* —
    вызывается сразу после рендера, до паспорта и экспорта (``slide.ready``).
    ADR-036: ``on_slide_event`` opts into real draft renders at compiler
    boundaries (extra render cost, no extra planning); ``is_canceled`` stops
    starting/publishing those previews without terminating the final compiler.

    ``design_dna`` (ADR-018, optional): the template's cached analysis —
    when its exemplars carry real LLM-written content_description (see
    Design DNA §exemplars), Stage 3 (layout_fit, v3) uses them for
    semantic candidate matching instead of a cruder text heuristic.
    Model descriptions are computed at template analyze time; this
    function never calls a describing model itself. With ``None`` (the
    CLI/skill path), deterministic DNA is derived locally to select
    template roles such as the cover before fitting the written text.

    Returns artifact paths (pptx/pdf/passport json), the DeckPlan id,
    the composition report, audit issues (as dicts) and the assembled
    QualityPassport (schema dict).

    ``gateway=None`` (дефолт) — полностью детерминированный путь,
    идентичный предыдущему поведению. Переданный gateway включает
    LLM/VLM-режим: планирование через ``plan_deck_llm`` (промпт
    ``storyline``; при отказе провайдера сам честно откатывается на
    детерминированный план — отличие видно по ``report["planner"]``) и,
    если ``contextual.enabled`` в audit-конфиге, VLM-аудит каждого
    слайда через ``run_contextual_audit`` — его issues
    (``deterministic=False``) добавляются к issues ``audit_deck``.
    Ошибки VLM-стадии НЕ проглатываются: всплывают typed-ошибкой со
    stage ``audit.contextual``, как у остальных стадий.

    ``strategy`` — дифференциация вариантов (OR-007): прокидывается в
    plan_deck (``faithful`` — без agenda и recap-инъекций), в
    exemplar selection и в composing (``visual`` — приоритет
    визуально-насыщенных exemplar + кап плотности текста).
    ``custom`` ведёт себя как ``balanced``. Принимает и имя
    стратегии строкой (``"faithful"`` и т.п.) — коэрсится в
    ``Strategy`` по аналогии с ``brief: Brief | dict``; неизвестное
    значение — обычный ``ValueError``, как ``ValidationError`` у
    кривого brief. Заметка (roadmap): детерминированный candidate
    filtering уже есть; LLM top-k reranking pool'а вариантов —
    отдельная задача, здесь не делалась.

    ``deck_plan`` — заранее вычисленный план: стадия планирования
    пропускается, колода собирается ровно по переданному плану
    (API использует это, чтобы сохранённый ``deck_plan`` был тем же
    объектом, по которому собрана колода, — иначе LLM-планировщик
    вызывался бы дважды и записанный план мог отличаться от
    итоговой колоды). ``gateway`` при этом всё равно нужен для
    contextual-аудита.

    ``content_pack`` (опционально) — уже распарсенный пакет: парсинг и
    restructure пропускаются, ``content_path`` используется только как
    путь для разрешения относительных ассетов в compose. Нужен, когда
    вызывающий код (API) уже сохранил ContentPack с корректным
    ``artifact_id`` на каждом unit — повторный ``parse_file`` по копии
    файла во временной директории подставил бы вместо него имя файла
    (см. hardening check #15), и каждый SourceRef плана перестал бы
    резолвиться в исходный артефакт API.

    ``content_fill`` (ADR-030) — отчёт стадии добора контента, если её уже
    выполнил вызывающий код (API делает это один раз на прогон для всех
    вариантов и передаёт сюда добранный ``content_pack``). Без него и без
    ``deck_plan`` стадия выполняется здесь: оценка достаточности контента
    → веб-ресёрч/знания модели или честное сокращение колоды.
    """
    started = time.monotonic()
    cfg_raw = load_generation_config().raw
    time_budget_s = float(
        (cfg_raw.get("runtime") or {}).get("time_budget_seconds")
        or cfg_raw.get("time_budget_seconds", 180)
    )
    pipeline_deadline = started + time_budget_s
    stage_fallbacks: list[Fallback] = []
    clock = _StageClock(on_stage)
    usage_before = measures.usage_snapshot(gateway)
    telemetry_before = measures.telemetry_snapshot(gateway)

    def _run_model_stage_with_fallback[T](
        feature: str,
        stage_cap_s: float,
        fn: Callable[[], T],
        fallback_fn: Callable[[], T],
        *,
        reserve_s: float = 5.0,
        fallback_strategy: str = "fallback",
        target_fallbacks: list[Fallback] | None = None,
    ) -> T:
        fb_list = target_fallbacks if target_fallbacks is not None else stage_fallbacks
        remaining = pipeline_deadline - time.monotonic() - reserve_s
        if remaining <= 0.5:
            fb_list.append(
                Fallback(
                    feature=feature,
                    strategy=fallback_strategy,
                    disclosure="time_budget_exhausted",
                )
            )
            return fallback_fn()
        try:
            with provider_deadline(min(stage_cap_s, remaining)):
                return fn()
        except (DeckDNAError, TimeoutError) as exc:
            if (
                isinstance(exc, DeckDNAError)
                and exc.code not in _PROVIDER_DEGRADABLE_CODES
            ):
                raise
            logging.getLogger(__name__).warning(
                "stage %s degraded to %s: %s", feature, fallback_strategy, exc
            )
            fb_list.append(
                Fallback(
                    feature=feature,
                    strategy=fallback_strategy,
                    disclosure=f"{getattr(exc, 'code', type(exc).__name__)}: {exc}",
                )
            )
            return fallback_fn()

    content_path = Path(content_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # ADR-031 §2–3: .potx → копия с типом основной части .pptx (иначе
    # python-pptx отказывает пакету на composing.deck); шаблон без слайдов
    # или с одним слайдом получает образцы из своих макетов
    template_path, template_synthesis = prepared_template_path(
        Path(template_path), out_dir / ".work"
    )

    if isinstance(brief, dict):
        # A raw dict brief (dict target_slide_count out of the ge=3/le=40
        # contract, wrong types, etc.) used to crash generate() with a bare
        # pydantic ValidationError before any stage-typed error existed to
        # catch it -- found live when widening the supported slide-count
        # range (29.09) stopped a previously-"valid" value from failing a
        # later check instead, exposing this gap.
        try:
            brief = Brief.model_validate(brief)
        except ValidationError as exc:
            raise DeckDNAError(
                code="invalid_input",
                message=f"invalid brief: {exc}",
                stage=STAGE_PLANNING,
            ) from exc

    input_count_brief = brief
    if isinstance(strategy, str):
        strategy = Strategy(strategy)

    if content_pack is None and not content_path.exists():
        raise DeckDNAError(
            code="invalid_input",
            message=f"content file not found: {content_path}",
            stage=STAGE_INGESTION,
        )
    with clock.track("content"):
        pack = (
            content_pack
            if content_pack is not None
            else _stage(STAGE_INGESTION, lambda: parse_file(content_path))
        )
        from deckdna.planning.mandatory import mandatory_anchors, protect_mandatory_sections

        mandatory_anchors(pack, brief.mandatory_sections)
        # структуру любого материала строит модель (только группирует
        # предложения источника); без модели — сплошной текст режется по
        # структуре парсера; готовый deck_plan ссылается на исходные id
        if (deck_plan is None and not brief.mandatory_sections
                and (gateway is not None or needs_restructure(pack))):
            if gateway is not None:
                pack = _stage(
                    STAGE_INGESTION,
                    lambda: _run_model_stage_with_fallback(
                        STAGE_INGESTION,
                        35.0,
                        lambda: _run_async_gw(gateway, restructure_pack(pack, gateway)),
                        lambda: restructure_pack_sync(pack, None),
                    ),
                )
            else:
                pack = _stage(STAGE_INGESTION, lambda: restructure_pack_sync(pack, None))
    if deck_plan is None and content_fill is None:
        with clock.track("research"):
            fill = _stage(
                STAGE_RESEARCH,
                lambda: _run_model_stage_with_fallback(
                    STAGE_RESEARCH,
                    40.0,
                    lambda: _run_async_gw(gateway, fill_content(pack, brief, gateway)),
                    lambda: asyncio.run(fill_content(pack, brief, None)),
                )
                if gateway is not None
                else asyncio.run(fill_content(pack, brief, None)),
            )
        pack, brief = fill.pack, fill.brief
        content_fill = fill.to_dict()
    evidence_graph = None
    style_accepted = False
    planning_version = planning_pipeline_version()
    with clock.track("plan"):
        if deck_plan is not None:
            plan = deck_plan.model_copy(deep=True)
            if gateway is not None:
                evidence_graph = _stage(
                    STAGE_PLANNING, lambda: build_evidence_graph(pack)
                )
        elif gateway is None:
            plan = _stage(
                STAGE_PLANNING, lambda: plan_deck(pack, brief, strategy=strategy)
            )
        else:
            evidence_graph = _stage(
                STAGE_PLANNING, lambda: build_evidence_graph(pack)
            )
            planner_fn = (
                plan_deck_llm_v2 if planning_version in ("v2", "v3") else plan_deck_llm
            )
            plan = _stage(
                STAGE_PLANNING,
                lambda: _run_model_stage_with_fallback(
                    STAGE_PLANNING,
                    60.0,
                    lambda: _run_async_gw(
                        gateway, planner_fn(
                            pack, brief, gateway, design_dna=design_dna,
                            strategy=strategy,
                        )
                    ),
                    lambda: plan_deck(pack, brief, strategy=strategy),
                ),
            )
            # v3 (ADR-016 Stage 3, см. compose ниже): layout_fit уже пишет
            # и подгоняет финальный текст под выбранный эталон — ещё один
            # стилистический проход поверх был бы избыточен.
            if (
                planning_version != "v3"
                and content_style.content_styling_enabled()
                and content_style.can_rewrite(gateway)
            ):
                styled_plan = _stage(
                    STAGE_PLANNING,
                    lambda: _run_model_stage_with_fallback(
                        "planning.content_style",
                        30.0,
                        lambda: _run_async_gw(
                            gateway,
                            content_style.style_deck_content(
                                plan, gateway, language=plan.language or "ru"
                            ),
                        ),
                        lambda: plan,
                    ),
                )
                style_accepted = styled_plan is not plan
                plan = styled_plan

    from deckdna.planning.request_constraints import validate_count_constraint, validate_deck_count

    validate_count_constraint(plan.brief, len(plan.slides))
    validate_count_constraint(input_count_brief, len(plan.slides))
    from deckdna.planning.locked_copy import (
        locked_slide_indices,
        validate_locked_deck,
        validate_locked_transition,
    )

    plan = protect_mandatory_sections(plan, pack, required=input_count_brief.mandatory_sections)
    locked_baseline = plan.model_copy(deep=True)
    if deck_plan is None:
        # ADR-030: метки происхождения пунктов (ваш контент / интернет / модель)
        plan = annotate_unit_origins(plan, pack)

    # Защищённые позиции слайдов (OR-031): явный аргумент побеждает,
    # иначе — protected_slide_indices из generation config.
    if protected_slide_indices is None:
        protected_slide_indices = load_generation_config().protected_slide_indices

    def _run_compose_model_prep(
        base_plan: DeckPlan,
        local_dna: DesignDNA | None,
        target_fallbacks: list[Fallback],
    ) -> tuple[
        DeckPlan,
        dict[str, bytes],
        list[Any] | None,
        str,
        budget_fit.BudgetFitReport | None,
        TalkTrackReport,
    ]:
        cur_plan = base_plan
        gen_images: dict[str, bytes] = {}
        if gateway is not None:
            cur_plan, gen_images = _stage(
                STAGE_COMPOSING,
                lambda: _run_model_stage_with_fallback(
                    "composing.image_generation",
                    45.0,
                    lambda: _run_async_gw(
                        gateway,
                        image_brief.generate_images(
                            cur_plan, gateway, strategy, palette=_theme_palette(local_dna)
                        ),
                    ),
                    lambda: (cur_plan, {}),
                    target_fallbacks=target_fallbacks,
                ),
            )
        gen_refs = frozenset(gen_images)

        choices = None
        rerank_status = "deterministic"
        b_report: budget_fit.BudgetFitReport | None = None
        if gateway is not None and planning_version == "v3" and _rerank_enabled():
            if local_dna is None:
                from deckdna.pptx.composing.minimal import _local_design_dna

                local_dna = _local_design_dna(OpcPackage.open(template_path))
            needs = slide_needs(
                cur_plan,
                {t.id: t for t in pack.tables},
                {c.id: c for c in pack.charts},
                list(pack.assets),
                gen_refs,
            )
            pre_fit_plan = cur_plan
            budgets = template_budgets(template_path)
            choices, cur_plan = _stage(
                STAGE_COMPOSING,
                lambda: _run_model_stage_with_fallback(
                    "composing.layout_fit",
                    60.0,
                    lambda: _run_async_gw(
                        gateway,
                        layout_fit.layout_fit_deck(
                            OpcPackage.open(template_path),
                            cur_plan,
                            needs,
                            gateway,
                            strategy=strategy,
                            design_dna=local_dna,
                            budgets=budgets,
                        ),
                    ),
                    lambda: (None, pre_fit_plan),
                    target_fallbacks=target_fallbacks,
                ),
            )
            rerank_status = "llm" if cur_plan is not pre_fit_plan else "fallback"
            if (
                rerank_status == "fallback"
                and layout_fit.can_rewrite(gateway)
                and not any(f.feature == "composing.layout_fit" for f in target_fallbacks)
            ):
                target_fallbacks.append(
                    Fallback(
                        feature="composing.layout_fit",
                        strategy="fallback",
                        disclosure="deterministic_layout_fit_fallback",
                    )
                )
            cur_plan = sync_agenda(cur_plan, pack)
            if (
                choices
                and _budget_fit_enabled()
                and budget_fit.can_rewrite(gateway)
            ):
                parts = [c.slide_part for c in choices]
                fitted_input = cur_plan
                cur_plan, b_report = _stage(
                    STAGE_COMPOSING,
                    lambda: _run_model_stage_with_fallback(
                        "composing.budget_fit",
                        35.0,
                        lambda: _run_async_gw(
                            gateway,
                            budget_fit.fit_plan_to_budgets(
                                fitted_input,
                                parts,
                                budgets,
                                gateway,
                                language=fitted_input.language or "ru",
                                protected_indices=frozenset(protected_slide_indices or ()),
                            ),
                        ),
                        lambda: (fitted_input, None),
                        target_fallbacks=target_fallbacks,
                    ),
                )
        elif gateway is not None and _rerank_enabled():
            needs = slide_needs(
                cur_plan,
                {t.id: t for t in pack.tables},
                {c.id: c for c in pack.charts},
                list(pack.assets),
                gen_refs,
            )
            choices = _stage(
                STAGE_COMPOSING,
                lambda: _run_model_stage_with_fallback(
                    "composing.exemplar_rerank",
                    40.0,
                    lambda: _run_async_gw(
                        gateway,
                        llm_exemplar_choices(
                            OpcPackage.open(template_path),
                            cur_plan,
                            needs,
                            gateway,
                            strategy=strategy,
                        ),
                    ),
                    lambda: None,
                    target_fallbacks=target_fallbacks,
                ),
            )
            rerank_status = "llm" if choices is not None else "fallback"

        cur_plan = sync_agenda(cur_plan, pack)
        n_report = _stage(
            STAGE_COMPOSING,
            lambda: _run_model_stage_with_fallback(
                "composing.speaker_notes",
                35.0,
                lambda: _run_async_gw(gateway, write_talk_tracks(cur_plan, gateway)),
                lambda: asyncio.run(write_talk_tracks(cur_plan, None)),
                target_fallbacks=target_fallbacks,
            )
            if gateway is not None
            else asyncio.run(write_talk_tracks(cur_plan, None)),
        )
        if (
            gateway is not None
            and n_report.model_written == 0
            and n_report.extractive > 0
            and not any(f.feature == "composing.speaker_notes" for f in target_fallbacks)
        ):
            from deckdna.planning.talk_track import can_write as _can_write_notes

            if _can_write_notes(gateway):
                target_fallbacks.append(
                    Fallback(
                        feature="composing.speaker_notes",
                        strategy="fallback",
                        disclosure="extractive_notes_fallback",
                    )
                )
        return cur_plan, gen_images, choices, rerank_status, b_report, n_report

    out_pptx = out_dir / "deck.pptx"
    notes_report: TalkTrackReport | None = None
    with clock.track("compose"):
        if deck_plan is not None and gateway is not None:
            # Анализ, факты, план и тексты считаются один раз на три варианта;
            # варианты отличаются только раскладкой (strategy) и компилируются параллельно.
            prep_key = (
                hashlib.sha256(template_path.read_bytes()).hexdigest(),
                id(deck_plan),
                deck_plan.id,
                planning_version,
                _gateway_share_key(gateway),
            )
            with _SHARED_PREP_LOCK:
                existing_entry = _SHARED_PREP_CACHE.get(prep_key)
                is_owner = existing_entry is None
                if existing_entry is None:
                    if len(_SHARED_PREP_CACHE) >= _MAX_SHARED_PREP_ENTRIES:
                        _SHARED_PREP_CACHE.pop(next(iter(_SHARED_PREP_CACHE)))
                    prep_entry = _SharedVariantPrep()
                    _SHARED_PREP_CACHE[prep_key] = prep_entry
                else:
                    prep_entry = existing_entry
            if is_owner:
                try:
                    prep_fbs: list[Fallback] = []
                    (
                        plan,
                        generated_images,
                        exemplar_choices,
                        exemplar_rerank,
                        budget_report,
                        notes_report,
                    ) = _run_compose_model_prep(plan, design_dna, prep_fbs)
                    prep_entry.plan = plan.model_copy(deep=True)
                    prep_entry.primary_strategy = strategy
                    prep_entry.primary_choices = (
                        list(exemplar_choices) if exemplar_choices is not None else None
                    )
                    prep_entry.exemplar_rerank = exemplar_rerank
                    prep_entry.generated_images = dict(generated_images)
                    prep_entry.budget_report = budget_report
                    prep_entry.notes_report = notes_report
                    prep_entry.used_models = (
                        dict(gateway.used_model_ids())
                        if isinstance(gateway, ModelProfileSource)
                        else {}
                    )
                    prep_entry.fallbacks = list(prep_fbs)
                    prep_entry.ready = True
                    stage_fallbacks.extend(prep_fbs)
                except BaseException as exc:
                    prep_entry.error = exc
                    with _SHARED_PREP_LOCK:
                        _SHARED_PREP_CACHE.pop(prep_key, None)
                    raise
                finally:
                    prep_entry.event.set()
            else:
                prep_entry.event.wait()
                if prep_entry.error is not None or prep_entry.plan is None:
                    (
                        plan,
                        generated_images,
                        exemplar_choices,
                        exemplar_rerank,
                        budget_report,
                        notes_report,
                    ) = _run_compose_model_prep(plan, design_dna, stage_fallbacks)
                else:
                    plan = prep_entry.plan.model_copy(deep=True)
                    generated_images = dict(prep_entry.generated_images)
                    budget_report = prep_entry.budget_report
                    notes_report = prep_entry.notes_report
                    exemplar_rerank = prep_entry.exemplar_rerank
                    stage_fallbacks.extend(prep_entry.fallbacks)
                    if hasattr(gateway, "_used") and isinstance(gateway._used, dict):
                        gateway._used.update(prep_entry.used_models)
                    if strategy == prep_entry.primary_strategy:
                        exemplar_choices = (
                            list(prep_entry.primary_choices)
                            if prep_entry.primary_choices is not None
                            else None
                        )
                    else:
                        # Для остальных стратегий (faithful / visual) тексты и план
                        # те же, а раскладка/эталоны подбираются детерминированно под strategy
                        exemplar_choices = None
        else:
            (
                plan,
                generated_images,
                exemplar_choices,
                exemplar_rerank,
                budget_report,
                notes_report,
            ) = _run_compose_model_prep(plan, design_dna, stage_fallbacks)

        progressive = None
        if on_slide_event is not None:
            from deckdna.generation.progressive import ProgressiveRenderer

            progressive = ProgressiveRenderer(out_dir / ".work" / "drafts", on_slide_event,
                                               is_canceled=is_canceled)
        compose_report = _stage(
            STAGE_COMPOSING,
            lambda: generate_deck(
                template_path,
                plan,
                out_pptx,
                protected_slide_indices=protected_slide_indices,
                tables=pack.tables,
                charts=pack.charts,
                diagrams=pack.diagrams,
                assets=pack.assets,
                content_path=content_path,
                strategy=strategy,
                exemplar_choices=exemplar_choices,
                generated_images=generated_images,
                design_dna=design_dna,
                exemplar_overrides=exemplar_overrides,
                on_slide_compiled=progressive.compiled if progressive else None,
            ),
        )

    validate_deck_count(plan.brief, out_pptx)
    validate_deck_count(input_count_brief, out_pptx)
    validate_locked_transition(locked_baseline, plan)
    validate_locked_deck(locked_baseline, out_pptx, compose_report.get("slides"))
    repair_protected = sorted(set(protected_slide_indices or ()) | locked_slide_indices(
        plan, compose_report.get("slides"),
    ))

    # иконки шаблона на карточки — по смыслу пунктов (template/icons.py,
    # pptx/composing/icon_place.py); пропуск этапа, если в шаблоне нет
    # иконок/пиктограмм и бриф их не просит
    icon_report: dict[str, int] = {}
    if _should_run_icons(template_path, brief, plan, gateway):
        with clock.track("icons"):
            icon_report = _stage(
                STAGE_COMPOSING,
                lambda: _run_model_stage_with_fallback(
                    "composing.icons",
                    20.0,
                    lambda: _run_async_gw(gateway, _place_template_icons(
                        template_path, out_pptx, gateway
                    )),
                    lambda: {"library": 0},
                    fallback_strategy="skipped",
                ),
            )

    # ADR-032 §3: заметки докладчика — talk track в notesSlide каждого слайда.
    if notes_report is None:
        notes_report = _stage(
            STAGE_COMPOSING,
            lambda: asyncio.run(write_talk_tracks(plan, None)),
        )
    if notes_report.notes:
        before_notes = out_dir / ".work" / "deck.before-notes.pptx"
        before_notes.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(out_pptx, before_notes)
        _stage(
            STAGE_COMPOSING,
            lambda: write_notes_to_pptx(out_pptx, compose_report, notes_report.notes),
        )
        # python-pptx пересериализует все части: обязательные слайды (OR-031)
        # возвращаются байт-в-байт
        _restore_protected_verbatim(before_notes, out_pptx, protected_slide_indices)

    contextual_ran = False
    contextual_diagnostics: dict[str, Any] = {}
    # ADR-031 §4: причина пропуска контекстного аудита (модель без зрения / бюджет)
    contextual_skipped: dict[str, str] = {}
    auto_fixes: dict[str, int] = {}
    auto_fix_rejected: str | None = None
    with clock.track("audit"):
        audit_issues: list[AuditIssue] = _stage(
            STAGE_AUDIT, lambda: audit_deck(out_pptx, deck_revision=1)
        )
        # Q2: безопасные исправления делаются ДО ревизии 1
        audit_issues, auto_fixes, auto_fix_rejected = _stage(
            STAGE_AUDIT,
            lambda: _auto_fix(out_pptx, audit_issues, repair_protected),
        )
        # Модель переписывает переполненный текст короче (text_fit) вместо
        # обрезки с «…» — тоже до ревизии 1 и под тем же guard'ом аудита
        fit_gained = 0
        fit_rejected: str | None = None
        if gateway is not None and _text_fit_enabled() and text_fit.can_rewrite(gateway):
            pre_fit_issues = list(audit_issues)
            audit_issues, fit_gained, fit_rejected = _stage(
                STAGE_AUDIT,
                lambda: _run_model_stage_with_fallback(
                    "audit.text_fit",
                    25.0,
                    lambda: _model_fit(
                        out_pptx,
                        pre_fit_issues,
                        gateway,
                        repair_protected,
                        plan.language or "ru",
                    ),
                    lambda: (pre_fit_issues, 0, "time_budget_or_provider_fallback"),
                ),
            )
            if fit_gained:
                auto_fixes = {**auto_fixes, "text.overflow": fit_gained}

        # ADR-030: заглушки ищутся по финальному файлу ревизии 1 — после
        # авто-исправлений, которые пересобирают список issues заново
        audit_issues = [
            *audit_issues,
            *_stage(
                STAGE_AUDIT,
                lambda: audit_filler_slides(out_pptx, compose_report, deck_revision=1),
            ),
        ]

        if gateway is not None and _contextual_enabled():
            _ctx_call_timeout_s, _ctx_budget_s = _contextual_deadlines()
            _ctx_max_conc = _contextual_max_concurrent()
            rem_for_ctx = pipeline_deadline - time.monotonic() - 2.0
            if rem_for_ctx <= 0.5:
                contextual_skipped["reason"] = "time_budget_exhausted"
            else:
                effective_ctx_budget = min(_ctx_budget_s, rem_for_ctx)
                effective_call_timeout = min(_ctx_call_timeout_s, effective_ctx_budget)
                try:
                    with provider_deadline(effective_ctx_budget):
                        contextual_issues = _skip_without_vision(
                            contextual_skipped,
                            lambda: _stage(
                                STAGE_AUDIT_CTX,
                                lambda: _run_async_gw(
                                    gateway,
                                    run_contextual_audit(
                                        out_pptx,
                                        gateway,
                                        deck_plan=plan,
                                        evidence_graph=evidence_graph,
                                        deck_language=plan.language or "ru",
                                        audit_run_id=f"audit-ctx-{plan.id}",
                                        deck_revision=1,
                                        call_timeout_s=effective_call_timeout,
                                        budget_s=effective_ctx_budget,
                                        diagnostics=contextual_diagnostics,
                                        deterministic_issues=audit_issues,
                                        max_concurrent=_ctx_max_conc,
                                        compose_report=compose_report,
                                        design_dna=design_dna,
                                    ),
                                ),
                            ),
                        )
                    if contextual_issues is not None:
                        audit_issues = [*audit_issues, *contextual_issues]
                        contextual_ran = True
                except (DeckDNAError, TimeoutError) as exc:
                    if (
                        isinstance(exc, DeckDNAError)
                        and exc.code not in _PROVIDER_DEGRADABLE_CODES
                    ):
                        raise
                    contextual_skipped["reason"] = (
                        f"{getattr(exc, 'code', type(exc).__name__)}: {exc}"
                    )

    with clock.track("render"):
        from deckdna.planning.request_constraints import validate_pdf_count

        validate_locked_transition(locked_baseline, plan)
        validate_locked_deck(locked_baseline, out_pptx, compose_report.get("slides"))
        input_count = validate_deck_count(input_count_brief, out_pptx)
        plan_count = validate_deck_count(plan.brief, out_pptx)
        physical_count = input_count or plan_count
        out_pdf = _stage(
            STAGE_RENDER, lambda: render_pdf(out_pptx, out_dir / "deck.pdf")
        )
        validate_pdf_count(input_count_brief, Path(out_pdf), physical_count)
        validate_pdf_count(plan.brief, Path(out_pdf), physical_count)
    if on_render is not None:
        try:
            on_render(Path(out_pdf))
        except Exception:  # noqa: BLE001 — превью для ленты не важнее колоды
            logging.getLogger(__name__).exception("on_render callback failed")

    _pv, _mp = _provenance_inputs(
        gateway,
        plan,
        contextual_ran,
        rerank_accepted=exemplar_rerank == "llm" and planning_version != "v3",
        fit_accepted=fit_gained > 0,
        style_accepted=style_accepted,
        layout_fit_accepted=exemplar_rerank == "llm" and planning_version == "v3",
        images_generated=bool(generated_images),
        notes_accepted=notes_report.model_written > 0,
    )
    usage = measures.usage_delta(usage_before, measures.usage_snapshot(gateway))
    telemetry = measures.telemetry_delta(
        telemetry_before, measures.telemetry_snapshot(gateway)
    )
    source_text = measures.pack_text(pack)
    with clock.track("passport"):
        # duration/тайминги паспорта — до самого паспорта; сам этап
        # passport в per_stage попадает уже после сборки (своё время
        # известно только по её окончании), поэтому здесь его нет
        duration = time.monotonic() - started
        passport: QualityPassport = _stage(
            STAGE_PASSPORT,
            lambda: assemble_quality_passport(
                out_pptx,
                compose_report,
                audit_issues,
                content_pack_id=pack.id,
                brief=brief,
                duration_seconds=duration,
                prompt_versions=_pv,
                model_profiles=_mp,
                stage_timings=list(clock.items),
                usage=usage,
                source_text=source_text,
                auto_fixes=auto_fixes,
                content_fill=content_fill,
                speaker_notes=notes_report.to_dict(),
                telemetry=telemetry,
                repair_count=sum(auto_fixes.values()),
            ),
        )
        if pack.datasets:
            # ADR-033 §10: выбор графиков и причины — в паспорт
            from deckdna.analysis.explain import analysis_report

            passport.analysis = analysis_report(pack, plan)
        if stage_fallbacks:
            passport.fallbacks = [*(passport.fallbacks or []), *stage_fallbacks]
        if contextual_skipped:
            passport.fallbacks = [*(passport.fallbacks or []), Fallback(
                feature="audit.contextual", strategy="skipped",
                disclosure=contextual_skipped["reason"],
            )]
        if contextual_ran and (
            contextual_diagnostics.get("unverified_slides")
            or contextual_diagnostics.get("unverified_critical_slides")
            or contextual_diagnostics.get("missing_checks")
            or contextual_diagnostics.get("duplicate_checks")
            or contextual_diagnostics.get("invalid_checks")
            or contextual_diagnostics.get("uncertain_checks")
        ):
            passport.fallbacks = [*(passport.fallbacks or []), Fallback(
                feature="audit.contextual.coverage", strategy="partial",
                disclosure=json.dumps(contextual_diagnostics, ensure_ascii=False),
            )]
        passport_path = out_dir / "quality-passport.json"
        passport_path.write_text(
            json.dumps(to_schema_dict(passport), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    duration = time.monotonic() - started

    return {
        "artifacts": {
            "pptx": str(out_pptx),
            "pdf": str(out_pdf),
            "quality_passport": str(passport_path),
        },
        "content_pack_id": pack.id,
        "deck_plan_id": plan.id,
        # итоговый план (с выбором графиков ADR-033) — для отчёта и тестов
        "deck_plan": to_schema_dict(plan),
        "slides_out": compose_report["slides_out"],
        "compose_report": compose_report,
        "audit_issues": [issue.to_dict() for issue in audit_issues],
        "quality_passport": to_schema_dict(passport),
        "planner": plan.provenance.planner,
        "strategy": strategy.value,
        "content_fill": content_fill,
        "template_synthesis": template_synthesis.to_dict() if template_synthesis else None,
        "speaker_notes": notes_report.to_dict(),
        "exemplar_rerank": exemplar_rerank,
        "budget_fit": budget_report.to_dict() if budget_report else None,
        "icons": icon_report,
        "contextual_audit": (
            {
                "ran": True,
                **contextual_diagnostics,
                "issues": sum(1 for i in audit_issues if not i.deterministic),
            }
            if contextual_ran
            else {"ran": False, "issues": 0, **contextual_skipped}
        ),
        "duration_seconds": round(duration, 2),
        "stage_timings": list(clock.items),
        "usage": usage,
        "numbers": {
            "verified": passport.metrics.content_support.numbers_verified,
            "failed": passport.metrics.content_support.numbers_failed,
        }
        if passport.metrics.content_support
        else None,
        "auto_fixes": auto_fixes,
        "auto_fix_rejected": auto_fix_rejected,
        "model_text_fit": {"fixed": fit_gained, "rejected": fit_rejected},
    }
