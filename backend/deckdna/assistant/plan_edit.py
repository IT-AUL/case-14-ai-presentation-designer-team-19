"""Шаг «План» поверх готового (ADR-034 §4).

- смена графика на карточке — только среди выбора кода и альтернатив,
  иное → 422 с причиной из ``rejected[]``;
- быстрые действия: ``shorten | split | merge_next | to_chart |
  to_diagram | remove``;
- ``supports_key_message`` — слайд держится за главную мысль колоды
  (эвристика пересечения основ слов с главной мыслью и выводами слайда).

Все правки детерминированные и возвращают новый план с непрерывными
индексами и ``brief.target_slide_count`` = числу слайдов."""

from __future__ import annotations

import re

from deckdna.analysis.pipeline import analyze_pack
from deckdna.analysis.recommend import CHART_RU
from deckdna.analysis.task import intent_of
from deckdna.contracts.content_pack import Chart, ChartSeries, ContentPack, Diagram, SourceRef
from deckdna.contracts.deck_plan import (
    ChartSpec,
    ChartType,
    ContentUnit,
    DeckPlan,
    DesiredVisual,
    Kind,
    Purpose,
    SlidePlan,
)
from deckdna.errors import DeckDNAError
from deckdna.planning.data_story import brief_text, chart_card
from deckdna.planning.locked_copy import validate_locked_transition

ACTIONS = ("shorten", "split", "merge_next", "to_chart", "to_diagram", "remove_chart", "remove")
_BOOKENDS = {Purpose.title, Purpose.thank_you, Purpose.cta, Purpose.agenda}
_TEXT_KINDS = {Kind.bullet, Kind.paragraph, Kind.number, Kind.quote, Kind.label}
_WORD = re.compile(r"[a-zа-яё]{4,}", re.I)
_STOP = {"этот", "этого", "также", "который", "которые", "чтобы", "after", "with", "that"}


def _stems(text: str) -> set[str]:
    return {w.casefold()[:5] for w in _WORD.findall(text or "") if w.casefold() not in _STOP}


def _slide_text(slide: SlidePlan) -> str:
    return " ".join([slide.title_intent, slide.key_message,
                     *(u.text or "" for u in slide.content_units)])


def annotate_key_message(plan: DeckPlan, pack: ContentPack | None = None) -> DeckPlan:
    """``supports_key_message`` на каждом слайде, когда главная мысль
    задана. Слайд держится за неё, если делит с ней основы слов — сам или
    через свои выводы."""
    key = (plan.brief.key_message or "").strip()
    if not key:
        return plan
    key_stems = _stems(key)
    key_task = intent_of(key)
    tasks: dict[str, str] = {}
    if pack is not None and pack.datasets and key_task:
        for analysis in analyze_pack(pack, brief_text(plan.brief)):
            for va in analysis.views:
                tasks[va.view.id] = va.task.task
    insights = {i.id: i for i in (pack.insights if pack is not None else [])}
    slides = []
    for slide in plan.slides:
        view = (slide.chart or {}).get("view_id")
        if slide.purpose in _BOOKENDS or slide.purpose == Purpose.overview:
            supports = True
        elif view in tasks:
            # слайд данных держится за мысль, если решает ту же задачу
            # («выросли вдвое» — динамика, а не доли регионов)
            supports = tasks[view] == key_task
        else:
            text = _slide_text(slide) + " " + " ".join(
                (insights[i].fact + " " + (insights[i].headline or ""))
                for i in slide.insight_ids or [] if i in insights
            )
            supports = bool(key_stems & _stems(text))
        slides.append(slide.model_copy(update={"supports_key_message": supports}))
    return plan.model_copy(update={"slides": slides})


def _renumber(plan: DeckPlan, slides: list[SlidePlan]) -> DeckPlan:
    from deckdna.planning.request_constraints import validate_count_constraint

    validate_count_constraint(plan.brief, len(slides))
    if len(slides) < 3:
        raise DeckDNAError("invalid_input", "a deck needs at least 3 slides", http_status=422)
    slides = [s.model_copy(update={"index": i}) for i, s in enumerate(slides)]
    brief = plan.brief.model_copy(update={"target_slide_count": len(slides)})
    return plan.model_copy(update={"slides": slides, "brief": brief})


def _find(plan: DeckPlan, slide_id: str) -> int:
    for i, slide in enumerate(plan.slides):
        if slide.id == slide_id:
            return i
    raise DeckDNAError("not_found", f"slide not found: {slide_id}")


def _view_analysis(pack: ContentPack, plan: DeckPlan, view_id: str):
    for analysis in analyze_pack(pack, brief_text(plan.brief)):
        for va in analysis.views:
            if va.view.id == view_id:
                return analysis, va
    raise DeckDNAError("not_found", f"data view not found: {view_id}")


def _resolve_slide_view(slide: SlidePlan, plan: DeckPlan, pack: ContentPack) -> str | None:
    if slide.chart and slide.chart.get("view_id"):
        return str(slide.chart["view_id"])
    for u in slide.content_units:
        if u.kind == Kind.table and u.table_ref:
            for t in pack.tables:
                if t.id == u.table_ref and t.source_ref.range:
                    return t.source_ref.range
        if u.kind == Kind.chart and u.chart_ref:
            for c in pack.charts:
                if c.id == u.chart_ref and c.source_ref.range:
                    return c.source_ref.range
    if slide.insight_ids:
        for ins in pack.insights:
            if ins.id in slide.insight_ids and ins.view_id:
                return ins.view_id
    slide_stems = _stems(_slide_text(slide))
    best_view: str | None = None
    best_score = -1
    for analysis in analyze_pack(pack, brief_text(plan.brief)):
        for va in analysis.views:
            view_texts = [
                va.view.title or "",
                analysis.dataset.title or "",
                *(
                    f"{ins.headline or ''} {ins.fact}"
                    for ins in pack.insights
                    if ins.view_id == va.view.id
                ),
            ]
            score = len(slide_stems & _stems(" ".join(view_texts)))
            if score > best_score:
                best_score = score
                best_view = va.view.id
    return best_view


def change_chart(plan: DeckPlan, slide_id: str, chart_type: str, pack: ContentPack) -> DeckPlan:
    i = _find(plan, slide_id)
    slide = plan.slides[i]
    view_id = (slide.chart or {}).get("view_id") or _resolve_slide_view(slide, plan, pack)
    if not view_id:
        raise DeckDNAError("invalid_input", f"slide {slide_id} has no data chart", http_status=422)
    analysis, va = _view_analysis(pack, plan, view_id)
    allowed = {r.chart_type for r in va.rec.ranked}
    if chart_type not in allowed:
        rejected = next((r for r in va.rec.rejected if r.chart_type == chart_type), None)
        reasons = rejected.reasons if rejected else ["тип не из каталога"]
        raise DeckDNAError(
            "invalid_input",
            f"{CHART_RU.get(chart_type, chart_type)}: {reasons[0]}",
            http_status=422,
            details={"chart_type": chart_type, "rejected": reasons,
                     "allowed": sorted(allowed)},
        )
    chart_id = next((c.id for c in pack.charts if c.source_ref.range == va.view.id), None)
    table_id = next((t.id for t in pack.tables if t.source_ref.range == va.view.id), None)
    ds_ev = f"{pack.id}:{analysis.dataset.id}"
    units = [u for u in slide.content_units if u.kind not in (Kind.chart, Kind.table)]
    if chart_type == "table":
        data_unit = ContentUnit(role="table", kind=Kind.table, table_ref=table_id,
                                evidence_ids=[ds_ev])
        visual = DesiredVisual.table
    else:
        data_unit = ContentUnit(
            role="chart", kind=Kind.chart, chart_ref=chart_id, evidence_ids=[ds_ev],
            chart_spec=ChartSpec(
                chart_type=ChartType(chart_type), series_count=min(len(va.view.series) or 1, 5),
                has_axis_labels=True, has_units=bool(va.view.unit),
                has_legend=len(va.view.series) >= 2
                or chart_type in ("pie", "doughnut", "waterfall"),
            ),
        )
        visual = DesiredVisual.chart
    title = [u for u in units if u.kind == Kind.title]
    body = [u for u in units if u.kind != Kind.title]
    updated = slide.model_copy(update={
        "content_units": [*title, data_unit, *body],
        "desired_visual": visual,
        "chart": chart_card(va, chart_type),
        # заметка докладчика — текст выступления (факт, смысл,
        # рекомендация); от смены типа графика он не меняется, а
        # технический разбор выбора живёт в карточке ``chart``
    })
    slides = list(plan.slides)
    slides[i] = updated
    candidate = plan.model_copy(update={"slides": slides})
    validate_locked_transition(plan, candidate)
    return candidate


def _short_step(text: str) -> str:
    assumption = re.match(r"^(Предположение:\s*)(.*)", text.strip(), re.I | re.S)
    if assumption:
        return assumption[1] + _short_step(assumption[2])
    words = re.split(r"\s+", re.split(r"[.;:,(—–-]\s", text.strip(), maxsplit=1)[0])
    return " ".join(words[:6]).rstrip(".,;:")


def apply_action(
    plan: DeckPlan, slide_id: str, action: str, pack: ContentPack,
) -> tuple[DeckPlan, ContentPack | None]:
    """(новый план, новый пак — если действие добавило схему)."""
    if action not in ACTIONS:
        raise DeckDNAError("invalid_input", f"unknown action: {action}", http_status=422)
    i = _find(plan, slide_id)
    slides = list(plan.slides)
    slide = slides[i]
    title = [u for u in slide.content_units if u.kind == Kind.title]
    texts = [u for u in slide.content_units if u.kind in _TEXT_KINDS and u.text]
    other = [u for u in slide.content_units if u not in title and u not in texts]
    new_pack = None
    if action == "remove":
        if slide.purpose == Purpose.title:
            raise DeckDNAError("invalid_input", "the cover cannot be removed", http_status=422)
        slides.pop(i)
    elif action == "shorten":
        if slide.purpose == Purpose.agenda or len(texts) <= 1:
            # один пункт — короче по словам, без потери смысла начала
            units = [u.model_copy(update={"text": _short_step(u.text) if len(u.text) > 60
                                          and not u.locked
                                          else u.text}) for u in texts]
        else:
            unlocked = [u for u in texts if not u.locked]
            keep = {id(u) for u in unlocked[:max(1, len(unlocked) // 2)]}
            units = [u for u in texts if u.locked or id(u) in keep]
        slides[i] = slide.model_copy(update={"content_units": [*title, *other, *units]})
    elif action == "split":
        if len(texts) < 2:
            raise DeckDNAError("invalid_input", "nothing to split: fewer than 2 points",
                               http_status=422)
        half = (len(texts) + 1) // 2
        taken = {s.id for s in slides}
        n = len(slides)
        new_id = f"slide-{n}"
        while new_id in taken:
            n += 1
            new_id = f"slide-{n}"
        cont_title = f"{slide.title_intent} (продолжение)"
        slides[i] = slide.model_copy(update={"content_units": [*title, *other, *texts[:half]]})
        slides.insert(i + 1, slide.model_copy(update={
            "id": new_id,
            "title_intent": cont_title,
            "content_units": [
                ContentUnit(role="title", kind=Kind.title, text=cont_title,
                            evidence_ids=slide.evidence_ids[:1]),
                *texts[half:],
            ],
            "desired_visual": DesiredVisual.none,
            "chart": None,
        }))
    elif action == "merge_next":
        if i + 1 >= len(slides) or slides[i + 1].purpose in _BOOKENDS:
            raise DeckDNAError("invalid_input", "no content slide after this one",
                               http_status=422)
        nxt = slides.pop(i + 1)
        extra = [u for u in nxt.content_units if u.kind != Kind.title]
        slides[i] = slide.model_copy(update={
            "content_units": [*slide.content_units, *extra],
            "evidence_ids": sorted({*slide.evidence_ids, *nxt.evidence_ids}),
            "insight_ids": [*(slide.insight_ids or []), *(nxt.insight_ids or [])] or None,
        })
    elif action == "to_chart":
        view = _resolve_slide_view(slide, plan, pack)
        if view is not None:
            _a, va = _view_analysis(pack, plan, view)
            fake = slide.model_copy(update={"chart": {"view_id": view}})
            slides[i] = fake
            chart_type = next((r.chart_type for r in va.rec.ranked if r.chart_type != "table"),
                              None)
            if chart_type is None:
                raise DeckDNAError("invalid_input", "these data are not chartable",
                                   http_status=422)
            return change_chart(plan.model_copy(update={"slides": slides}), slide_id,
                                chart_type, pack), None
        if pack.charts:
            slide_stems = _stems(_slide_text(slide))
            chosen_chart = max(
                pack.charts,
                key=lambda c: len(
                    slide_stems & _stems(f"{c.title or ''} {' '.join(c.categories)}")
                ),
            )
            ctype = chosen_chart.chart_type or "bar"
            data_unit = ContentUnit(
                role="chart",
                kind=Kind.chart,
                chart_ref=chosen_chart.id,
                evidence_ids=slide.evidence_ids[:1] or ["model:assistant"],
                chart_spec=ChartSpec(
                    chart_type=ChartType(ctype),
                    series_count=max(1, len(chosen_chart.series)),
                    has_axis_labels=True,
                    has_units=bool(chosen_chart.unit),
                    has_legend=len(chosen_chart.series) >= 2,
                ),
            )
            slides[i] = slide.model_copy(update={
                "content_units": [
                    *title,
                    data_unit,
                    *[u for u in other if u.kind not in (Kind.chart, Kind.table)],
                    *texts,
                ],
                "desired_visual": DesiredVisual.chart,
                "chart": {"type": ctype, "alternatives": [{"type": "bar"}, {"type": "line"}]},
            })
        else:
            pairs: list[tuple[str, float]] = []
            for u in texts:
                m = re.search(r"(-?\d+(?:[.,]\d+)?)", (u.text or "").replace("\u00a0", ""))
                if m:
                    val = float(m.group(1).replace(",", "."))
                    label = _short_step(u.text)[:28] or f"Пункт {len(pairs) + 1}"
                    pairs.append((label, val))
            if len(pairs) >= 2:
                chart_obj = Chart(
                    id=f"ch-plan-{slide.id}",
                    categories=[p[0] for p in pairs[:8]],
                    series=[
                        ChartSeries(
                            name=slide.title_intent[:40],
                            values=[p[1] for p in pairs[:8]],
                        )
                    ],
                    chart_type="bar",
                    title=slide.title_intent[:80],
                    source_ref=SourceRef(artifact_id="user:plan"),
                )
                new_pack = pack.model_copy(update={
                    "charts": [*[c for c in pack.charts if c.id != chart_obj.id], chart_obj]
                })
                data_unit = ContentUnit(
                    role="chart",
                    kind=Kind.chart,
                    chart_ref=chart_obj.id,
                    evidence_ids=slide.evidence_ids[:1] or ["model:assistant"],
                    chart_spec=ChartSpec(
                        chart_type=ChartType.bar,
                        series_count=1,
                        has_axis_labels=True,
                    ),
                )
                slides[i] = slide.model_copy(update={
                    "content_units": [
                        *title,
                        data_unit,
                        *[u for u in other if u.kind not in (Kind.chart, Kind.table)],
                        *texts,
                    ],
                    "desired_visual": DesiredVisual.chart,
                    "chart": {"type": "bar", "alternatives": [{"type": "line"}, {"type": "pie"}]},
                })
            else:
                slides[i] = slide.model_copy(update={"desired_visual": DesiredVisual.chart})
    elif action == "to_diagram":
        steps = [_short_step(u.text) for u in texts][:6]
        if len(steps) < 2:
            raise DeckDNAError("invalid_input", "a diagram needs at least 2 points",
                               http_status=422)
        diagram = Diagram(id=f"dg-plan-{slide.id}", steps=steps,
                          source_ref=SourceRef(artifact_id="user:plan"))
        new_pack = pack.model_copy(update={
            "diagrams": [*[d for d in pack.diagrams if d.id != diagram.id], diagram]
        })
        slides[i] = slide.model_copy(update={
            "content_units": [
                *title,
                ContentUnit(role="diagram", kind=Kind.diagram, diagram_ref=diagram.id,
                            evidence_ids=slide.evidence_ids[:1]),
                *[u for u in other if u.kind != Kind.diagram],
            ],
            "desired_visual": DesiredVisual.diagram,
        })
    elif action == "remove_chart":
        visual_kinds = {Kind.chart, Kind.table, Kind.diagram}
        if not any(u.kind in visual_kinds for u in slide.content_units) and not slide.chart:
            raise DeckDNAError("invalid_input", "this slide has no chart, table, or diagram",
                               http_status=422)
        kept_other = [u for u in other if u.kind not in visual_kinds]
        body_units = list(texts)
        if not body_units:
            fallback_text = (slide.key_message or slide.title_intent or "").strip()
            if fallback_text:
                body_units = [
                    ContentUnit(
                        role="bullet",
                        kind=Kind.bullet,
                        text=fallback_text,
                        evidence_ids=slide.evidence_ids[:1] or ["model:assistant"],
                    )
                ]
        slides[i] = slide.model_copy(update={
            "content_units": [*title, *kept_other, *body_units],
            "desired_visual": DesiredVisual.none,
            "chart": None,
        })
    candidate = plan.model_copy(update={"slides": slides})
    validate_locked_transition(plan, candidate, allow_moves=action in {"split", "merge_next"})
    return _renumber(plan, slides), new_pack


__all__ = ["ACTIONS", "annotate_key_message", "apply_action", "change_chart"]
