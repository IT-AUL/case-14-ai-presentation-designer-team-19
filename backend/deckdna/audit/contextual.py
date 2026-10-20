"""Contextual VLM-аудит — 11 официальных вопросов к каждому слайду.

Реализует contextual-слой Render Arena (docs/pipeline/AUDIT_AND_REPAIR.md §3,
промпт ``prompts/contextual_audit/slide_checks.v1.yaml``): каждый слайд
рендерится в PNG (тот же soffice→pdftoppm путь, что у HTML-экспорта), VLM
получает картинку + извлечённый текст слайда + title_intent из DeckPlan +
evidence_excerpt из EvidenceGraph + язык колоды и отвечает JSON-verdict'ами
по 10 проверкам (вопрос 10 объединяет language consistency и logical flow —
мапится на ``deck.logical_flow``; ``content.language_consistency`` покрывается
этим же вердиктом и не заводится отдельно).

Маппинг вердиктов в ``AuditIssue`` (severity по frozen Appendix):

- ``fail`` + ``confidence >= confidence_threshold`` → issue
  (``deterministic=False``, verdict+confidence в evidence);
- ``uncertain`` → НЕ issue: честная неопределённость, не false positive.
  Понижение uncertain→warning (политика ``uncertain_downgrade_to`` из
  configs/audit.default.yaml) — осознанно отложено: выдавать warning за
  "не знаю" — это искусственный сигнал; disclosure реализуем, когда у
  issue появится поле для него;
- ``pass`` или ``fail`` ниже порога уверенности → не issue.

Работает против любого OpenAI-совместимого vision endpoint через
``gateway.vision_json`` — включая VK inference, когда его спецификация
будет опубликована. В текущем контуре VK endpoint'а нет; тесты идут
через MockProvider с фикстурными ответами.

Фаза 4 (надёжность, регрессионная проверка): живой инцидент показал
вызов ``vision_json``, зависший на 24 минуты без единой ошибки в
логах — ``httpx``-таймаут per-phase (connect/read/write), а не общий
дедлайн на запрос, бесконечно сбрасывается любыми keep-alive байтами
от сервера/прокси. Это НЕ решение проблемы латентности по существу
(её решают Фазы 1-3: наблюдаемость + параллельность + переиспользование
соединения) — это страховочный ремень поверх них, единственное место
в пайплайне, где нужен настоящий hard deadline: (1) ``asyncio.wait_for``
вокруг ОДНОГО вызова ``gateway.vision_json`` — таймаут не ретраится
(это не «модель прислала мусор», это «модель/сеть не ответила вовсе»),
и (2) общий бюджет времени на всю стадию — слайды, до которых бюджет
не дожил, честно остаются непроверенными (семантика ``uncertain`` —
не issue, не выдуманный fail), а не блокируют вариант навечно.
Недоступность провайдера после его собственных retry также оставляет
слайд непроверенным: отказ необязательной проверки не теряет готовую
колоду. Паспорт раскрывает неполное покрытие. Ошибки компилятора,
рендера и программные ошибки по-прежнему прерывают выполнение.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import tempfile
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from deckdna.audit.config import default_audit_config
from deckdna.audit.issues import AuditIssue
from deckdna.contracts.deck_plan import DeckPlan
from deckdna.contracts.design_dna import DesignDNA
from deckdna.contracts.evidence_graph import EvidenceGraph
from deckdna.errors import DeckDNAError
from deckdna.pptx.exporting.render import render_slides_png
from deckdna.providers.base import ModelGateway
from deckdna.providers.prompts import load_prompt

logger = logging.getLogger(__name__)

RULE_VERSION = "audit-contextual/0.1.0"
PROMPT_NAME = "slide_checks"
PROMPT_REF = f"contextual_slide_audit@{load_prompt(PROMPT_NAME).version}"
DEFAULT_CONFIDENCE_THRESHOLD = 0.5

# Слайды в колоде независимы — гоняем VLM-вызовы конкурентно, а не по
# одному: с reasoning-моделью round-trip держится в районе 30-90с, и
# последовательный цикл на 12 слайдах давал ~10+ минут только на аудит
# одного варианта (замерено вживую на qwen3.8-27b/OpenRouter). Потолок
# ограничивает нагрузку на endpoint, не даёт бюджету расти — тот же
# набор вызовов, просто не в очередь.
DEFAULT_MAX_CONCURRENT_VLM_CALLS = 3
DEFAULT_MAX_SAMPLE_SLIDES = 3
DEFAULT_RISK_THRESHOLD = 0.45
DEFAULT_RISK_WEIGHTS: dict[str, float] = {
    "overflow": 0.30,
    "font_substitution": 0.20,
    "novel_layout": 0.20,
    "dense_chart": 0.15,
    "low_style_confidence": 0.15,
}

# Единичный отказ модели ("provider returned invalid structured output")
# не должен ронять весь ~10-минутный VLM-аудит варианта целиком — один
# повтор того же запроса. Сетевые retry принадлежат gateway; на стадии
# аудита их не добавляем. Отказ проверки раскрывается как partial coverage.
_RETRY_ON_ERROR_CODE = "structured_output_invalid"
_SLIDE_LEVEL_ERRORS = frozenset({"structured_output_invalid", "provider_unavailable"})
_FLAGGED_SEVERITIES = frozenset({"warning", "error", "blocker"})

_OVERFLOW_RULES = frozenset(
    {
        "text.overflow",
        "layout.out_of_bounds",
        "layout.unintended_overlap",
        "density.bullet_count",
        "density.bullet_length",
        "density.occupancy",
    }
)
_FONT_RULES = frozenset({"template.font_family", "template.font_scale", "text.font_floor"})
_CHART_RULES = frozenset({"density.chart_series", "chart.readability", "density.table_size"})
_STYLE_RULES = frozenset(
    {"template.color_palette", "template.anchor_position", "accessibility.contrast"}
)

# Страховочные дедлайны (Фаза 4) — не основное решение проблемы
# латентности (см. докстринг модуля), а гарантия, что зависший вызов не
# держит вариант вечно. Оба настраиваются через configs/audit.default.yaml
# (contextual.call_timeout_seconds / contextual.budget_seconds).
DEFAULT_CALL_TIMEOUT_S = 30.0
DEFAULT_STAGE_BUDGET_S = 45.0


def load_risk_config() -> tuple[dict[str, float], float]:
    """Веса и порог оценки риска слайда из ``configs/audit.default.yaml``."""
    ctx_cfg = default_audit_config().raw.get("contextual") or {}
    raw_weights = ctx_cfg.get("risk_weights") or {}
    weights = {
        key: float(raw_weights.get(key, default_val))
        for key, default_val in DEFAULT_RISK_WEIGHTS.items()
    }
    threshold = float(ctx_cfg.get("risk_threshold", DEFAULT_RISK_THRESHOLD))
    return weights, threshold


def _clamp01(value: Any) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, v))


def compute_slide_risk(
    factors: Mapping[str, Any] | None = None,
    *,
    overflow: float | bool | None = None,
    font_substitution: float | bool | None = None,
    novel_layout: float | bool | None = None,
    dense_chart: float | bool | None = None,
    low_style_confidence: float | bool | None = None,
    weights: Mapping[str, float] | None = None,
) -> float:
    """Оценка риска слайда:
    ``risk = 0.30*overflow + 0.20*font_substitution + 0.20*novel_layout``
    ``+ 0.15*dense_chart + 0.15*low_style_confidence``.
    """
    cfg_weights, _ = load_risk_config()
    w = {**cfg_weights, **(dict(weights) if weights else {})}
    src = dict(factors or {})
    if overflow is not None:
        src["overflow"] = overflow
    if font_substitution is not None:
        src["font_substitution"] = font_substitution
    if novel_layout is not None:
        src["novel_layout"] = novel_layout
    if dense_chart is not None:
        src["dense_chart"] = dense_chart
    if low_style_confidence is not None:
        src["low_style_confidence"] = low_style_confidence
    score = (
        w["overflow"] * _clamp01(src.get("overflow", 0.0))
        + w["font_substitution"] * _clamp01(src.get("font_substitution", 0.0))
        + w["novel_layout"] * _clamp01(src.get("novel_layout", 0.0))
        + w["dense_chart"] * _clamp01(src.get("dense_chart", 0.0))
        + w["low_style_confidence"] * _clamp01(src.get("low_style_confidence", 0.0))
    )
    return round(score, 6)


def _issue_slide_no(issue: Any) -> int | None:
    """1-based номер слайда из runtime AuditIssue или contract AuditIssueOut."""
    raw_idx = (
        issue.get("slide_index")
        if isinstance(issue, dict)
        else getattr(issue, "slide_index", None)
    )
    if raw_idx is not None:
        return int(raw_idx) + 1
    raw_id = (
        issue.get("slide_id")
        if isinstance(issue, dict)
        else getattr(issue, "slide_id", None)
    )
    if isinstance(raw_id, str) and raw_id.startswith("slide-"):
        tail = raw_id.removeprefix("slide-")
        if tail.isdigit():
            return int(tail)
    return None


def _issue_severity(issue: Any) -> str:
    raw_sev = (
        issue.get("severity")
        if isinstance(issue, dict)
        else getattr(issue, "severity", None)
    )
    return str(getattr(raw_sev, "value", raw_sev) or "")


def _issue_rule_code(issue: Any) -> str:
    raw_rule = (
        issue.get("rule_code")
        if isinstance(issue, dict)
        else getattr(issue, "rule_code", None)
    )
    return str(raw_rule or "")


def slide_risk_factors(
    slide_no: int,
    *,
    deterministic_issues: Sequence[Any] | None = None,
    deck_plan: DeckPlan | None = None,
    compose_report: Mapping[str, Any] | None = None,
    design_dna: DesignDNA | None = None,
) -> dict[str, float]:
    """Признаки риска слайда (1-based ``slide_no``) из компиляции и аудита."""
    idx = slide_no - 1
    slide_issues = [
        iss for iss in (deterministic_issues or ()) if _issue_slide_no(iss) == slide_no
    ]
    rules = {_issue_rule_code(iss) for iss in slide_issues}
    slides_rep = (compose_report or {}).get("slides") or []
    s_rep = (
        slides_rep[idx]
        if 0 <= idx < len(slides_rep) and isinstance(slides_rep[idx], Mapping)
        else {}
    )
    text_rep = s_rep.get("text") if isinstance(s_rep.get("text"), Mapping) else {}
    charts_rep = s_rep.get("charts") if isinstance(s_rep.get("charts"), Mapping) else {}
    tables_rep = s_rep.get("tables") if isinstance(s_rep.get("tables"), Mapping) else {}
    diagrams_rep = s_rep.get("diagrams") if isinstance(s_rep.get("diagrams"), Mapping) else {}
    exemplar_rep = s_rep.get("exemplar") if isinstance(s_rep.get("exemplar"), Mapping) else {}
    sp = _slide_plan_at(deck_plan, slide_no) if deck_plan is not None else None

    overflow = bool(
        s_rep.get("overflow")
        or (rules & _OVERFLOW_RULES)
        or (text_rep.get("bodies_overflowing") or 0) > 0
        or (text_rep.get("texts_dropped") or 0) > 0
    )
    font_substitution = bool(
        s_rep.get("font_substitution")
        or (rules & _FONT_RULES)
        or (
            design_dna is not None
            and s_rep.get("font_fallback")
            and any(
                "font" in str(getattr(u, "feature", u)).lower()
                for u in (design_dna.unsupported_features or [])
            )
        )
    )
    novel_layout = bool(
        s_rep.get("novel_layout")
        or str(s_rep.get("mode") or "") in {"synthesized", "native"}
        or s_rep.get("native_composition")
        or s_rep.get("adaptive_layout")
        or (sp is not None and sp.purpose.value == "custom")
    )
    dense_chart = bool(
        s_rep.get("dense_chart")
        or (rules & _CHART_RULES)
        or (charts_rep.get("series_dropped") or 0) > 0
        or (charts_rep.get("units_dropped") or 0) > 0
        or (tables_rep.get("rows_dropped") or 0) > 0
        or (tables_rep.get("cols_dropped") or 0) > 0
        or (diagrams_rep.get("units_dropped") or 0) > 0
    )
    low_style_confidence = bool(
        s_rep.get("low_style_confidence")
        or (rules & _STYLE_RULES)
        or bool(s_rep.get("dropped_units"))
        or (
            exemplar_rep.get("score") is not None
            and float(exemplar_rep.get("score", 1.0)) < 0.5
        )
    )
    return {
        "overflow": 1.0 if overflow else 0.0,
        "font_substitution": 1.0 if font_substitution else 0.0,
        "novel_layout": 1.0 if novel_layout else 0.0,
        "dense_chart": 1.0 if dense_chart else 0.0,
        "low_style_confidence": 1.0 if low_style_confidence else 0.0,
    }


def _slide_layout_family(
    slide_no: int,
    *,
    layout_families: Sequence[str] | Mapping[int, str] | None = None,
    slide_risks: Sequence[Any] | Mapping[int, Any] | None = None,
    compose_report: Mapping[str, Any] | None = None,
    deck_plan: DeckPlan | None = None,
) -> str | None:
    idx = slide_no - 1
    if layout_families is not None:
        if isinstance(layout_families, Mapping):
            fam = layout_families.get(slide_no)
            if fam is None:
                fam = layout_families.get(idx)
            if fam is not None:
                return str(fam)
        elif 0 <= idx < len(layout_families) and layout_families[idx] is not None:
            return str(layout_families[idx])
    if slide_risks is not None:
        entry = (
            slide_risks.get(slide_no, slide_risks.get(idx))
            if isinstance(slide_risks, Mapping)
            else (slide_risks[idx] if 0 <= idx < len(slide_risks) else None)
        )
        if isinstance(entry, Mapping):
            for key in ("layout_family", "family", "layout"):
                if entry.get(key):
                    return str(entry[key])
    slides_rep = (compose_report or {}).get("slides") or []
    s_rep = (
        slides_rep[idx]
        if 0 <= idx < len(slides_rep) and isinstance(slides_rep[idx], Mapping)
        else {}
    )
    for key in ("layout_family", "family", "layout"):
        if s_rep.get(key):
            return str(s_rep[key])
    sp = _slide_plan_at(deck_plan, slide_no) if deck_plan is not None else None
    purpose = str(s_rep.get("purpose") or (sp.purpose.value if sp is not None else "") or "")
    if purpose == "title":
        return "title"
    if purpose == "section_divider":
        return "section"
    if purpose in {"thank_you", "qa", "cta"}:
        return "closing"
    exemplar = s_rep.get("exemplar") if isinstance(s_rep.get("exemplar"), Mapping) else {}
    if exemplar.get("layout_part"):
        return f"content:{exemplar['layout_part']}"
    if sp is not None and sp.selected_layout_id:
        return f"content:{sp.selected_layout_id}"
    if purpose:
        return "content"
    return None


def slide_audit_passed(
    slide_index: int,
    deterministic_issues: Sequence[Any] | None = None,
    contextual_issues: Sequence[Any] | None = None,
    *,
    verified_slides: Iterable[int] | None = None,
    unverified_slides: Iterable[int] | None = None,
) -> bool:
    """Слайд считается прошедшим аудит только если на нём нет блокеров/ошибок,
    нет проваленных VLM-проверок и нет непроверенных критических предупреждений."""
    if (
        unverified_slides is None
        and contextual_issues is not None
        and all(isinstance(x, int) and not isinstance(x, bool) for x in contextual_issues)
    ):
        unverified_slides = contextual_issues
        contextual_issues = ()
    slide_no = slide_index + 1
    det_on_slide = [
        iss for iss in (deterministic_issues or ()) if _issue_slide_no(iss) == slide_no
    ]
    ctx_on_slide = [
        iss for iss in (contextual_issues or ()) if _issue_slide_no(iss) == slide_no
    ]
    if any(_issue_severity(iss) in {"error", "blocker"} for iss in det_on_slide):
        return False
    if ctx_on_slide:
        return False
    has_critical_warning = any(
        _issue_severity(iss) in _FLAGGED_SEVERITIES for iss in det_on_slide
    )
    if has_critical_warning:
        unverified_set = set(unverified_slides or ())
        if slide_index in unverified_set:
            return False
        if verified_slides is not None and slide_index not in set(verified_slides):
            return False
    return True


def select_contextual_audit_slides(
    total_slides: int,
    deterministic_issues: Sequence[Any] | None = None,
    *,
    max_sample_slides: int | None = None,
    slide_risks: Sequence[Any] | Mapping[int, Any] | None = None,
    layout_families: Sequence[str] | Mapping[int, str] | None = None,
    deck_plan: DeckPlan | None = None,
    compose_report: Mapping[str, Any] | None = None,
    design_dna: DesignDNA | None = None,
    risk_threshold: float | None = None,
    risk_weights: Mapping[str, float] | None = None,
) -> list[int]:
    """Выбрать 1-based номера слайдов для контекстного VLM-аудита.

    Зрением проверяются:
    1) слайды с ``risk >= risk_threshold`` (по умолчанию 0.45);
    2) первый слайд каждого семейства макета;
    3) слайды с детерминированным предупреждением (``warning``/``error``/``blocker``).
    Остальные слайды проходят только детерминированный аудит.
    """
    if total_slides <= 0:
        return []
    weights, default_threshold = load_risk_config()
    if risk_weights is not None:
        weights = {**weights, **dict(risk_weights)}
    threshold = float(risk_threshold if risk_threshold is not None else default_threshold)

    severity_rank = {"blocker": 0, "error": 1, "warning": 2}
    flagged_by_slide: dict[int, int] = {}
    for issue in deterministic_issues or ():
        slide_no = _issue_slide_no(issue)
        if slide_no is None or not (1 <= slide_no <= total_slides):
            continue
        sev = _issue_severity(issue)
        if sev not in _FLAGGED_SEVERITIES:
            continue
        rank = severity_rank.get(sev, 3)
        if slide_no not in flagged_by_slide or rank < flagged_by_slide[slide_no]:
            flagged_by_slide[slide_no] = rank

    has_structural_inputs = any(
        x is not None
        for x in (slide_risks, layout_families, deck_plan, compose_report, design_dna)
    )
    if has_structural_inputs:
        selected: set[int] = set(flagged_by_slide)
        seen_families: set[str] = set()
        for slide_no in range(1, total_slides + 1):
            idx = slide_no - 1
            fam = _slide_layout_family(
                slide_no,
                layout_families=layout_families,
                slide_risks=slide_risks,
                compose_report=compose_report,
                deck_plan=deck_plan,
            )
            if fam is not None and fam not in seen_families:
                seen_families.add(fam)
                selected.add(slide_no)

            risk_val: float | None = None
            if slide_risks is not None:
                entry = (
                    slide_risks.get(slide_no, slide_risks.get(idx))
                    if isinstance(slide_risks, Mapping)
                    else (slide_risks[idx] if 0 <= idx < len(slide_risks) else None)
                )
                if isinstance(entry, (int, float)) and not isinstance(entry, bool):
                    risk_val = float(entry)
                elif isinstance(entry, Mapping):
                    if "risk" in entry and isinstance(entry["risk"], (int, float)):
                        risk_val = float(entry["risk"])
                    else:
                        risk_val = compute_slide_risk(entry, weights=weights)
            if risk_val is None:
                factors = slide_risk_factors(
                    slide_no,
                    deterministic_issues=deterministic_issues,
                    deck_plan=deck_plan,
                    compose_report=compose_report,
                    design_dna=design_dna,
                )
                risk_val = compute_slide_risk(factors, weights=weights)
            if risk_val >= threshold:
                selected.add(slide_no)
        return sorted(selected)

    sample_cap = (
        max_sample_slides if max_sample_slides is not None else DEFAULT_MAX_SAMPLE_SLIDES
    )
    limit = max(1, min(sample_cap, total_slides))
    if flagged_by_slide:
        prioritized = sorted(
            flagged_by_slide, key=lambda s: (flagged_by_slide[s], s)
        )[:limit]
        return sorted(prioritized)
    if total_slides <= limit:
        return list(range(1, total_slides + 1))
    if limit == 1:
        return [1]
    if limit == 2:
        return [1, total_slides]
    picked = {
        1 + round(i * (total_slides - 1) / (limit - 1))
        for i in range(limit)
    }
    return sorted(picked)


async def _vision_json_with_retry(
    gateway: ModelGateway,
    prompt_name: str,
    images: list[str],
    payload: dict,
    schema: type[BaseModel],
    *,
    retries: int = 1,
    timeout_s: float = DEFAULT_CALL_TIMEOUT_S,
) -> BaseModel:
    attempt = 0
    while True:
        try:
            return await asyncio.wait_for(
                gateway.vision_json(prompt_name, images, payload, schema),
                timeout=timeout_s,
            )
        except DeckDNAError as exc:
            if exc.code != _RETRY_ON_ERROR_CODE or attempt >= retries:
                raise
            attempt += 1
        # TimeoutError (включая asyncio.TimeoutError) НЕ ретраится и НЕ
        # ловится здесь — это не "модель ответила мусором" (транзиентный
        # повтор мог бы помочь), это "модель/сеть не ответила вовсе за
        # timeout_s"; вызывающая сторона (run_contextual_audit) трактует
        # его как честную неопределённость для этого слайда, а не как
        # повод повторить настолько же долгий запрос ещё раз.


class SlideCheckVerdict(BaseModel):
    """Один verdict из ответа VLM по slide_checks.v1.yaml."""

    check: str
    verdict: Literal["pass", "fail", "uncertain"]
    rationale: str | None = None
    confidence: float = 0.0


class SlideChecksResult(BaseModel):
    """Ответ VLM: verdict'ы по всем проверкам одного слайда."""

    verdicts: list[SlideCheckVerdict]


# check N (нумерация вопросов в slide_checks.v1.yaml) → frozen rule_code
# и severity из OFFICIAL_TRACEABILITY.md Appendix. Вопрос 10 объединяет
# language consistency и logical flow — мапим на deck.logical_flow.
_CHECK_RULES: dict[int, tuple[str, str]] = {
    1: ("content.conclusion_title", "warning"),
    2: ("content.title_alignment", "error"),
    3: ("content.single_message", "warning"),
    4: ("content.source_support", "blocker"),
    5: ("content.nonempty", "error"),
    6: ("content.visual_relevance", "warning"),
    7: ("content.prompt_leakage", "error"),
    8: ("content.spelling", "warning"),
    9: ("content.data_relevance", "warning"),
    10: ("deck.logical_flow", "warning"),
}


def _check_index(raw: str) -> int | None:
    """check приходит как "1"/"check_1"/"check 1" — извлекаем номер."""
    digits = "".join(c for c in raw if c.isdigit())
    return int(digits) if digits else None


def _slide_texts(pptx_path: Path) -> list[str]:
    """Текст каждого слайда (зеркало render._slide_texts, тот же код)."""
    from pptx import Presentation

    # Native grouped shapes, tables and charts also carry auditable content.
    texts: list[str] = []
    for slide in Presentation(str(pptx_path)).slides:
        parts = [node.text for node in slide._element.xpath(".//a:t") if node.text]
        texts.append("\n".join(parts))
    return texts


def _evidence_excerpt(
    slide_index: int,
    deck_plan: DeckPlan | None,
    graph: EvidenceGraph | None,
    *,
    max_chars: int = 12000,
) -> str:
    """Тексты evidence-узлов, релевантных слайду (по evidence_ids плана)."""
    if graph is None:
        return ""
    texts: dict[str, str] = {}
    for node in graph.nodes:
        if node.text:
            texts[node.id] = node.text
    ids: list[str] | None = None
    if deck_plan is not None:
        sp = _slide_plan_at(deck_plan, slide_index)
        if sp is not None:
            ids = list(sp.evidence_ids)
            # Authored revisions may fork the content-pack id while retaining
            # source-derived insight identities. Resolve those explicit ids
            # against this graph; never use rewritten slide copy as evidence.
            ids.extend(f"{graph.content_pack_id}:{iid}" for iid in (sp.insight_ids or []))
            # Data-story chart caches have their own raw-derived graph nodes.
            # Include exactly the referenced chart, never unrelated datasets.
            ids.extend(f"{graph.content_pack_id}:{unit.chart_ref}"
                       for unit in sp.content_units if unit.chart_ref)
            ids = list(dict.fromkeys(ids))
    chosen = [texts[i] for i in ids if i in texts] if ids else list(texts.values())
    if not chosen and ids and deck_plan is not None:
        # Legacy deterministic DeckPlan IDs are pack:section:block, while
        # build_evidence_graph() uses ev_blk_*/ev_lst_* IDs.  Match only
        # verbatim content units to graph nodes; never treat rewritten slide
        # copy as independent source evidence.
        sp = _slide_plan_at(deck_plan, slide_index)
        if sp is not None:
            exact = {text for text in texts.values() if text}
            chosen = [
                unit.text
                for unit in sp.content_units
                if unit.text and unit.evidence_ids and unit.text in exact
            ]
    return "\n".join(chosen)[:max_chars]


def _png_data_url(png: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode()


async def run_contextual_audit(
    deck_path: str | Path,
    gateway: ModelGateway,
    *,
    deck_plan: DeckPlan | None = None,
    evidence_graph: EvidenceGraph | None = None,
    deck_language: str = "ru",
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    first: int = 1,
    last: int | None = None,
    audit_run_id: str | None = None,
    deck_revision: int = 0,
    keep_images: str | Path | None = None,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT_VLM_CALLS,
    call_timeout_s: float = DEFAULT_CALL_TIMEOUT_S,
    budget_s: float = DEFAULT_STAGE_BUDGET_S,
    diagnostics: dict | None = None,
    deterministic_issues: Sequence[Any] | None = None,
    slide_numbers: Sequence[int] | None = None,
    max_sample_slides: int | None = None,
    compose_report: Mapping[str, Any] | None = None,
    design_dna: DesignDNA | None = None,
    slide_risks: Sequence[Any] | Mapping[int, Any] | None = None,
    layout_families: Sequence[str] | Mapping[int, str] | None = None,
    risk_threshold: float | None = None,
    risk_weights: Mapping[str, float] | None = None,
) -> list[AuditIssue]:
    """VLM-аудит слайдов колоды; возвращает contextual AuditIssue'ы.

    ``deck_plan``/``evidence_graph`` опциональны: без них title_intent и
    evidence_excerpt пустые — проверки на фактах/выводах честно вернут
    uncertain, а не выдуманный fail. ``keep_images`` оставляет PNG-рендеры.
    Слайды проверяются конкурентно (до ``max_concurrent`` одновременных
    vision_json-вызовов) — порядок итоговых issues и нумерация ``seq``
    остаются позиционными по слайду, не по порядку завершения запроса.

    ``call_timeout_s``/``budget_s`` — страховочные дедлайны Фазы 4 (см.
    докстринг модуля), НЕ основной способ уложиться в бюджет времени:
    ``call_timeout_s`` — hard timeout ОДНОГО vision_json-вызова;
    ``budget_s`` — общий бюджет на ВСЮ стадию. Слайд, для которого budget
    уже исчерпан к моменту его очереди в семафоре, или чей вызов упёрся в
    call_timeout_s, остаётся честно непроверенным (как ``uncertain`` —
    не issue, не выдуманный fail) — стадия завершается частично, а не
    блокирует вариант навечно. Недоступный провайдер также оставляет слайд
    непроверенным; прочие ошибки продолжают всплывать.
    """
    pptx_path = Path(deck_path)
    run_id = audit_run_id or f"audit-ctx-{uuid.uuid4().hex[:8]}"
    texts = _slide_texts(pptx_path)
    issues: list[AuditIssue] = []
    seq = 0
    deadline = time.monotonic() + budget_s

    ctx_cfg = default_audit_config().raw.get("contextual") or {}
    sample_limit = (
        max_sample_slides
        if max_sample_slides is not None
        else int(ctx_cfg.get("max_sample_slides", DEFAULT_MAX_SAMPLE_SLIDES))
    )
    flagged_only = bool(ctx_cfg.get("flagged_only", True))

    selected_slides: list[int] | None = None
    if slide_numbers is not None:
        selected_slides = sorted(
            {int(s) for s in slide_numbers if 1 <= int(s) <= len(texts)}
        )
    elif (
        deterministic_issues is not None
        or (first == 1 and last is None and flagged_only)
        or any(
            x is not None
            for x in (slide_risks, layout_families, compose_report, design_dna)
        )
    ):
        selected_slides = select_contextual_audit_slides(
            len(texts),
            deterministic_issues,
            max_sample_slides=sample_limit,
            slide_risks=slide_risks,
            layout_families=layout_families,
            deck_plan=deck_plan,
            compose_report=compose_report,
            design_dna=design_dna,
            risk_threshold=risk_threshold,
            risk_weights=risk_weights,
        )

    with tempfile.TemporaryDirectory(prefix="deckdna-ctx-") as tmp:
        out_dir = Path(keep_images) if keep_images else Path(tmp)
        if selected_slides is not None:
            if selected_slides:
                first_render = min(selected_slides)
                last_render = max(selected_slides)
                pngs = render_slides_png(
                    pptx_path, out_dir, first=first_render, last=last_render
                )
                slide_items = [
                    (s, pngs[s - first_render])
                    for s in selected_slides
                    if 0 <= s - first_render < len(pngs)
                ]
            else:
                slide_items = []
        else:
            pngs = render_slides_png(pptx_path, out_dir, first=first, last=last)
            slide_items = [(first + pos, png) for pos, png in enumerate(pngs)]

        stats = diagnostics if diagnostics is not None else {}
        stats.update(
            expected_slides=len(slide_items),
            requested_slides=len(slide_items),
            completed_slides=0,
            unverified_slides=[],
            uncertain_checks=0,
            missing_checks=0,
            duplicate_checks=0,
            invalid_checks=0,
        )
        semaphore = asyncio.Semaphore(max(1, max_concurrent))

        async def _check_slide(
            slide_index: int, png: Path
        ) -> tuple[int, SlideChecksResult | None]:
            sp = _slide_plan_at(deck_plan, slide_index) if deck_plan else None
            payload = {
                # input_fields промпта: slide_image — идентификатор рендера;
                # сама картинка идёт в сообщении как image_url (data URL).
                "slide_image": slide_index,
                "slide_text": texts[slide_index - 1] if slide_index - 1 < len(texts) else "",
                "title_intent": _title_intent(deck_plan, slide_index),
                "evidence_excerpt": _evidence_excerpt(
                    slide_index, deck_plan, evidence_graph
                ),
                "deck_language": deck_language,
                "slide_purpose": sp.purpose.value if sp else "unknown",
                "neighbor_titles": [
                    _title_intent(deck_plan, n)
                    for n in (slide_index - 1, slide_index + 1)
                    if deck_plan is not None and 1 <= n <= len(deck_plan.slides)
                ],
            }
            async with semaphore:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning(
                        "contextual audit: stage budget (%.0fs) exhausted before "
                        "slide %d — leaving it unverified (uncertain)",
                        budget_s,
                        slide_index,
                    )
                    return slide_index, None
                try:
                    result = await _vision_json_with_retry(
                        gateway,
                        PROMPT_NAME,
                        [_png_data_url(png)],
                        payload,
                        SlideChecksResult,
                        timeout_s=min(call_timeout_s, remaining),
                    )
                except TimeoutError:
                    logger.warning(
                        "contextual audit: vision_json timed out (%.0fs) on "
                        "slide %d — leaving it unverified (uncertain)",
                        call_timeout_s,
                        slide_index,
                    )
                    return slide_index, None
                except DeckDNAError as exc:
                    # Невалидный ответ модели на одном слайде (после повтора) —
                    # непроверенный слайд, а не упавшая генерация: аудит —
                    # проверка качества (найдено на VK Inference 17.10).
                    # Отказ провайдера после его retry тоже не теряет колоду.
                    # Отсутствие vision-модели обрабатывается выше.
                    if exc.code not in _SLIDE_LEVEL_ERRORS:
                        raise
                    logger.warning(
                        "contextual audit: %s on slide %d — leaving it unverified",
                        exc.code,
                        slide_index,
                    )
                    return slide_index, None
            return slide_index, result

        per_slide = await asyncio.gather(
            *(_check_slide(slide_index, png) for slide_index, png in slide_items)
        )

        verified_slide_indices: set[int] = set()
        for slide_index, result in per_slide:
            if result is None:
                stats["unverified_slides"].append(slide_index - 1)
                continue
            stats["completed_slides"] += 1
            check_indices = [_check_index(v.check) for v in result.verdicts]
            # Модель без навязанной схемы (VK Qwen, 17.10) пишет в `check`
            # текст вопроса вместо номера, но в порядке 1–10: при полном
            # ответе без номеров сопоставляем по позиции.
            if (
                all(index is None for index in check_indices)
                and len(check_indices) == len(_CHECK_RULES)
            ):
                check_indices = sorted(_CHECK_RULES)
            valid_checks = [index for index in check_indices if index in _CHECK_RULES]
            slide_invalid = len(check_indices) - len(valid_checks)
            slide_duplicate = len(valid_checks) - len(set(valid_checks))
            answered = set(valid_checks)
            slide_missing = len(set(_CHECK_RULES) - answered)
            slide_uncertain = sum(v.verdict == "uncertain" for v in result.verdicts)
            stats["invalid_checks"] += slide_invalid
            stats["duplicate_checks"] += slide_duplicate
            stats["missing_checks"] += slide_missing
            stats["uncertain_checks"] += slide_uncertain
            if (
                slide_invalid == 0
                and slide_duplicate == 0
                and slide_missing == 0
                and slide_uncertain == 0
            ):
                verified_slide_indices.add(slide_index - 1)
            seen_checks: set[int] = set()
            for verdict, check_index in zip(result.verdicts, check_indices, strict=True):
                if check_index not in _CHECK_RULES or check_index in seen_checks:
                    continue
                seen_checks.add(check_index)
                if verdict.verdict != "fail":
                    continue
                if verdict.confidence < confidence_threshold:
                    continue
                rule_code, severity = _CHECK_RULES[check_index]
                seq += 1
                issues.append(
                    AuditIssue(
                        id=f"{run_id}-{rule_code}-s{slide_index}-{seq}",
                        audit_run_id=run_id,
                        deck_revision=deck_revision,
                        rule_code=rule_code,
                        severity=severity,
                        message=(
                            verdict.rationale
                            or f"VLM: проверка {verdict.check} = fail"
                        ),
                        deterministic=False,
                        # VLM-вердикт — суждение, не геометрия: у content.*
                        # нет repair-обработчика (см. audit/catalog.py)
                        repairable=False,
                        slide_index=slide_index - 1,
                        measured_value=f"confidence {verdict.confidence:.2f}",
                        threshold=confidence_threshold,
                        confidence=verdict.confidence,
                        evidence=[
                            {
                                "kind": "model_verdict",
                                "ref": f"slide[{slide_index - 1}]",
                                "detail": (
                                    f"{verdict.check}: fail "
                                    f"(confidence {verdict.confidence:.2f}); "
                                    f"{verdict.rationale or 'no rationale'}"
                                ),
                            }
                        ],
                        provenance={
                            "rule_version": RULE_VERSION,
                            "prompt": PROMPT_REF,
                        },
                    )
                )

        # Слайд с непроверенным критическим предупреждением не считается прошедшим.
        unverified_critical: list[int] = []
        if deterministic_issues:
            critical_slides = sorted(
                {
                    s_no - 1
                    for iss in deterministic_issues
                    if (s_no := _issue_slide_no(iss)) is not None
                    and 1 <= s_no <= len(texts)
                    and _issue_severity(iss) in _FLAGGED_SEVERITIES
                }
            )
            checked_slide_indices = {s - 1 for s, _ in slide_items}
            for s_idx in critical_slides:
                if s_idx not in verified_slide_indices:
                    unverified_critical.append(s_idx)
                    if (
                        s_idx not in checked_slide_indices
                        and s_idx not in stats["unverified_slides"]
                    ):
                        stats["unverified_slides"].append(s_idx)
            if unverified_critical:
                stats["unverified_critical_slides"] = unverified_critical

        stats["complete"] = (
            stats["requested_slides"] > 0
            and stats["requested_slides"] == stats["completed_slides"]
            and not stats["unverified_slides"]
            and not unverified_critical
            and stats["missing_checks"] == 0
            and stats["duplicate_checks"] == 0
            and stats["invalid_checks"] == 0
        )
    return issues


def _slide_plan_at(deck_plan: DeckPlan, slide_index: int):
    """SlidePlan для N-го слайда колоды — позиционно, не по sp.index.

    База sp.index не специфицирована контрактом: plan_deck выдаёт
    0-based, сторонний/LLM-план может быть 1-based — сверять index с
    1-based slide_index смещало бы граундинг на соседний слайд.
    """
    if not 1 <= slide_index <= len(deck_plan.slides):
        return None
    return deck_plan.slides[slide_index - 1]


def _title_intent(deck_plan: DeckPlan | None, slide_index: int) -> str:
    if deck_plan is None:
        return ""
    sp = _slide_plan_at(deck_plan, slide_index)
    return sp.title_intent if sp is not None else ""
