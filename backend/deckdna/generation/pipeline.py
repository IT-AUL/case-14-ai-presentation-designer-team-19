"""End-to-end generation pipeline (synchronous, single process).

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
import json
import shutil
import tempfile
import time
import zipfile
from collections import Counter
from collections.abc import Callable, Coroutine, Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from deckdna.audit.basic import audit_deck
from deckdna.audit.config import default_audit_config
from deckdna.audit.contextual import DEFAULT_CALL_TIMEOUT_S as _CTX_DEFAULT_CALL_TIMEOUT_S
from deckdna.audit.contextual import DEFAULT_STAGE_BUDGET_S as _CTX_DEFAULT_BUDGET_S
from deckdna.audit.contextual import PROMPT_NAME as _CTX_PROMPT_NAME
from deckdna.audit.contextual import run_contextual_audit
from deckdna.audit.issues import AuditIssue
from deckdna.contracts.content_pack import ContentPack
from deckdna.contracts.deck_plan import Brief, DeckPlan
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
from deckdna.planning.evidence import build_evidence_graph
from deckdna.planning.story_director import (
    LLM_PLANNER_VERSION,
    STORYLINE_PROMPT,
    plan_deck,
    plan_deck_llm,
)
from deckdna.planning.story_director_v2 import plan_deck_llm_v2
from deckdna.pptx.cloning import layout_fit
from deckdna.pptx.cloning.rerank import (
    EXEMPLAR_RERANK_PROMPT,
    llm_exemplar_choices,
)
from deckdna.pptx.composing.minimal import generate_deck, slide_needs
from deckdna.pptx.composing.text_budget import template_budgets
from deckdna.pptx.exporting.render import render_pdf
from deckdna.pptx.fitting import budget_fit, text_fit
from deckdna.pptx.opc.package import OpcPackage
from deckdna.providers.base import ModelGateway
from deckdna.providers.profiles import ModelProfileSource
from deckdna.providers.prompts import load_prompt
from deckdna.repair.apply import apply_repairs
from deckdna.repair.planner import plan_repairs_with_report

STAGE_INGESTION = "content_ingestion"
STAGE_PLANNING = "planning"
STAGE_COMPOSING = "composing.deck"
STAGE_AUDIT = "audit.basic"
STAGE_AUDIT_CTX = "audit.contextual"
STAGE_RENDER = "exporting.render"
STAGE_PASSPORT = "evaluation.quality_passport"

# Публичные названия этапов для интерфейса (VariantSummary.stage,
# passport.metrics.timings.per_stage): content → plan → compose → audit
# → render → passport.
PUBLIC_STAGES = ("content", "plan", "compose", "audit", "render", "passport")

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


def _auto_fix(
    deck: Path,
    issues: list[AuditIssue],
    protected: Iterable[int] | None,
) -> tuple[list[AuditIssue], dict[str, int], str | None]:
    """Безопасные автоисправления до ревизии 1 (Q2).

    Планирует действия только для ``AUTO_FIX_RULES``, применяет их к
    копии, перемеряет аудитом и принимает результат лишь если (а) число
    проблем целевых правил упало и (б) число error/blocker прочих правил
    не выросло. Иначе колода остаётся как была — молча портить нельзя.

    Возвращает (issues итоговой колоды, {rule: исправлено}, причина
    отказа либо None).
    """
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
            return issues, {}, "auto-fix не уменьшил число проблем"

        def hard(items: list[AuditIssue]) -> int:
            return sum(
                1
                for i in items
                if i.rule_code not in AUTO_FIX_RULES
                and i.severity in {"error", "blocker"}
            )

        if hard(after) > hard(issues):
            return issues, {}, "auto-fix породил новые error/blocker — откат"
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

    # Роль -> приняла ли её вывод соответствующая стадия пайплайна.
    stage_accepted = {
        "text": plan.provenance.planner == LLM_PLANNER_VERSION
        or rerank_accepted
        or fit_accepted
        or layout_fit_accepted
        or style_accepted,
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


def _text_fit_enabled() -> bool:
    """configs/generation.default.yaml → fitting.llm_rewrite (default true)."""
    fit = load_generation_config().raw.get("fitting") or {}
    return bool(fit.get("llm_rewrite", True))


def _budget_fit_enabled() -> bool:
    """configs/generation.default.yaml → fitting.budget_fit (default true)."""
    fit = load_generation_config().raw.get("fitting") or {}
    return bool(fit.get("budget_fit", True))


def _model_fit(
    deck: Path,
    issues: list[AuditIssue],
    gateway: ModelGateway,
    protected: Iterable[int] | None,
    language: str,
) -> tuple[list[AuditIssue], int, str | None]:
    """Переписать моделью переполненный текст (навык text_fit) до ревизии 1.

    Как и детерминированный auto-fix: результат принимается, только если
    повторный аудит показал меньше проблем переполнения и не добавил
    error/blocker в других правилах. Защищённые слайды не трогаются.
    Возвращает (issues колоды, сколько проблем исправлено, причина отказа).
    """
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

        def hard(items: list[AuditIssue]) -> int:
            return sum(
                1
                for i in items
                if i.rule_code not in text_fit.FIT_RULES
                and i.severity in {"error", "blocker"}
            )

        gained = n_fit(issues) - n_fit(after)
        if gained <= 0:
            return issues, 0, "переписанный текст всё ещё не помещается"
        if hard(after) > hard(issues):
            return issues, 0, "переписывание породило новые error/blocker — откат"
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
) -> dict[str, Any]:
    """Run the full DeckDNA pipeline and return a JSON-serialisable report.

    ``design_dna`` (ADR-018, optional): the template's cached analysis —
    when its exemplars carry real LLM-written content_description (see
    Design DNA §exemplars), Stage 3 (layout_fit, v3) uses them for
    semantic candidate matching instead of a cruder text heuristic.
    Computed once at template analyze time, never here — this function
    never calls a describing model itself. ``None`` (the CLI/skill path,
    which has no template_id/Design DNA store) falls back to today's
    behavior unchanged.

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
    файла во временной директории подставил бы вместо него имя файла,
    и каждый SourceRef плана перестал бы
    резолвиться в исходный артефакт API.
    """
    started = time.monotonic()
    clock = _StageClock(on_stage)
    usage_before = measures.usage_snapshot(gateway)
    template_path = Path(template_path)
    content_path = Path(content_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

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
        # структуру любого материала строит модель (только группирует
        # предложения источника); без модели — сплошной текст режется по
        # структуре парсера; готовый deck_plan ссылается на исходные id
        if deck_plan is None and (gateway is not None or needs_restructure(pack)):
            pack = _stage(
                STAGE_INGESTION,
                lambda: _run_async_gw(gateway, restructure_pack(pack, gateway))
                if gateway is not None
                else restructure_pack_sync(pack, None),
            )
    evidence_graph = None
    style_accepted = False
    planning_version = planning_pipeline_version()
    with clock.track("plan"):
        if deck_plan is not None:
            plan = deck_plan
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
                lambda: _run_async_gw(
                    gateway, planner_fn(
                        pack, brief, gateway, design_dna=design_dna,
                        strategy=strategy,
                    )
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
                    lambda: _run_async_gw(
                        gateway,
                        content_style.style_deck_content(
                            plan, gateway, language=plan.language or "ru"
                        ),
                    ),
                )
                style_accepted = styled_plan is not plan
                plan = styled_plan

    # Защищённые позиции слайдов (OR-031): явный аргумент побеждает,
    # иначе — protected_slide_indices из generation config.
    if protected_slide_indices is None:
        protected_slide_indices = load_generation_config().protected_slide_indices

    out_pptx = out_dir / "deck.pptx"
    with clock.track("compose"):
        # ADR-017 (off by default, content.image_generation.enabled):
        # deterministic candidate selection + generation happens FIRST,
        # before exemplar selection below -- a slide that gets a
        # generated image needs its Kind.image unit in the plan BEFORE
        # slide_needs() computes capability requirements, or the exemplar
        # picked for it won't have anywhere to put the picture.
        generated_images: dict[str, bytes] = {}
        if gateway is not None:
            plan, generated_images = _stage(
                STAGE_COMPOSING,
                lambda: _run_async_gw(
                    gateway, image_brief.generate_images(plan, gateway, strategy)
                ),
            )
        generated_refs = frozenset(generated_images)

        # Опциональный LLM top-k rerank exemplar-пула (deterministic
        # candidate filtering уже сделан внутри llm_exemplar_choices —
        # ranked-пул тот же что у select_exemplar_slides). Бюджет: один
        # text_json вызов; None → честный откат на детерминированный выбор.
        exemplar_choices = None
        exemplar_rerank = "deterministic"
        budget_report: budget_fit.BudgetFitReport | None = None
        if gateway is not None and planning_version == "v3" and _rerank_enabled():
            # ADR-016 Stage 3: exemplar-выбор ИЗ РЕАЛЬНОГО написанного
            # текста (Stage 2), не из оценки объёма (в отличие от
            # llm_exemplar_choices ниже) -- также адаптирует текст под
            # выбранный эталон, поэтому возвращает и новый plan.
            needs = slide_needs(
                plan,
                {t.id: t for t in pack.tables},
                {c.id: c for c in pack.charts},
                list(pack.assets),
                generated_refs,
            )
            pre_fit_plan = plan
            budgets = template_budgets(template_path)
            exemplar_choices, plan = _stage(
                STAGE_COMPOSING,
                lambda: _run_async_gw(
                    gateway,
                    layout_fit.layout_fit_deck(
                        OpcPackage.open(template_path),
                        plan,
                        needs,
                        gateway,
                        strategy=strategy,
                        design_dna=design_dna,
                        budgets=budgets,
                    ),
                ),
            )
            exemplar_rerank = "llm" if plan is not pre_fit_plan else "fallback"
            # повестка — по составу колоды, до вписывания в бюджет
            plan = sync_agenda(plan, pack)
            if (
                exemplar_choices
                and _budget_fit_enabled()
                and budget_fit.can_rewrite(gateway)
            ):
                parts = [c.slide_part for c in exemplar_choices]
                plan, budget_report = _stage(
                    STAGE_COMPOSING,
                    lambda: _run_async_gw(
                        gateway,
                        budget_fit.fit_plan_to_budgets(
                            plan,
                            parts,
                            budgets,
                            gateway,
                            language=plan.language or "ru",
                            protected_indices=frozenset(protected_slide_indices or ()),
                        ),
                    ),
                )
        elif gateway is not None and _rerank_enabled():
            needs = slide_needs(
                plan,
                {t.id: t for t in pack.tables},
                {c.id: c for c in pack.charts},
                list(pack.assets),
                generated_refs,
            )
            exemplar_choices = _stage(
                STAGE_COMPOSING,
                lambda: _run_async_gw(
                    gateway,
                    llm_exemplar_choices(
                        OpcPackage.open(template_path),
                        plan,
                        needs,
                        gateway,
                        strategy=strategy,
                    ),
                ),
            )
            exemplar_rerank = "llm" if exemplar_choices is not None else "fallback"

        plan = sync_agenda(plan, pack)
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
            ),
        )

    contextual_ran = False
    contextual_diagnostics: dict[str, Any] = {}
    auto_fixes: dict[str, int] = {}
    auto_fix_rejected: str | None = None
    with clock.track("audit"):
        audit_issues: list[AuditIssue] = _stage(
            STAGE_AUDIT, lambda: audit_deck(out_pptx, deck_revision=1)
        )
        # Q2: безопасные исправления делаются ДО ревизии 1
        audit_issues, auto_fixes, auto_fix_rejected = _stage(
            STAGE_AUDIT,
            lambda: _auto_fix(out_pptx, audit_issues, protected_slide_indices),
        )
        # Модель переписывает переполненный текст короче (text_fit) вместо
        # обрезки с «…» — тоже до ревизии 1 и под тем же guard'ом аудита
        fit_gained = 0
        fit_rejected: str | None = None
        if gateway is not None and _text_fit_enabled() and text_fit.can_rewrite(gateway):
            audit_issues, fit_gained, fit_rejected = _stage(
                STAGE_AUDIT,
                lambda: _model_fit(
                    out_pptx,
                    audit_issues,
                    gateway,
                    protected_slide_indices,
                    plan.language or "ru",
                ),
            )
            if fit_gained:
                auto_fixes = {**auto_fixes, "text.overflow": fit_gained}

        if gateway is not None and _contextual_enabled():
            _ctx_call_timeout_s, _ctx_budget_s = _contextual_deadlines()
            contextual_issues = _stage(
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
                        call_timeout_s=_ctx_call_timeout_s,
                        budget_s=_ctx_budget_s,
                        diagnostics=contextual_diagnostics,
                    ),
                ),
            )
            audit_issues = [*audit_issues, *contextual_issues]
            contextual_ran = True

    with clock.track("render"):
        out_pdf = _stage(
            STAGE_RENDER, lambda: render_pdf(out_pptx, out_dir / "deck.pdf")
        )

    _pv, _mp = _provenance_inputs(
        gateway,
        plan,
        contextual_ran,
        rerank_accepted=exemplar_rerank == "llm" and planning_version != "v3",
        fit_accepted=fit_gained > 0,
        style_accepted=style_accepted,
        layout_fit_accepted=exemplar_rerank == "llm" and planning_version == "v3",
        images_generated=bool(generated_images),
    )
    usage = measures.usage_delta(usage_before, measures.usage_snapshot(gateway))
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
            ),
        )
        if contextual_ran and (
            contextual_diagnostics.get("unverified_slides")
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
        "slides_out": compose_report["slides_out"],
        "compose_report": compose_report,
        "audit_issues": [issue.to_dict() for issue in audit_issues],
        "quality_passport": to_schema_dict(passport),
        "planner": plan.provenance.planner,
        "strategy": strategy.value,
        "exemplar_rerank": exemplar_rerank,
        "budget_fit": budget_report.to_dict() if budget_report else None,
        "contextual_audit": (
            {
                "ran": True,
                **contextual_diagnostics,
                "issues": sum(1 for i in audit_issues if not i.deterministic),
            }
            if contextual_ran
            else {"ran": False, "issues": 0}
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
