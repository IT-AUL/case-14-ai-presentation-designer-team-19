"""Точечный ресёрч для одного слайда плана (ADR-030 §6).

Экран плана: пользователь выбрал «график» для слайда без чисел или просто
просит «найти факты» — ищем в интернете по заголовку и мысли слайда, в
контексте темы колоды. Факты проходят ту же проверку дословной цитатой,
что и обычный добор, и попадают в пак (новая запись) и в слайд с
``origin=web``. Если веб недоступен, а нужны просто факты, пункты пишет
модель (``origin=model``); числа из знаний модели не берутся — для
графика нужны подтверждённые цифры.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from deckdna.contracts.content_pack import Chart, ChartSeries, ContentPack
from deckdna.contracts.deck_plan import (
    Brief,
    ContentUnit,
    DeckPlan,
    DensityBudget,
    DesiredVisual,
    Kind,
    Level,
    Origin,
    Purpose,
    SlidePlan,
)
from deckdna.planning.complete import SLIDE_FILL_PROMPT, SlideFill, can_fill
from deckdna.planning.content_fill import is_sources_heading
from deckdna.providers.base import ModelGateway
from deckdna.research.config import ResearchConfig
from deckdna.research.expand import MODEL_SOURCE_ID
from deckdna.research.fill import _bibliography_entry, merge_into_pack
from deckdna.research.numbers_chart import chart_or_table
from deckdna.research.web import SearchProvider, WebResearch, research_web

_MAX_NEW_UNITS = 5


@dataclass
class SlideResearch:
    pack: ContentPack
    facts: list[tuple[str, str, int]] = field(default_factory=list)  # text, section id, block
    model_bullets: list[str] = field(default_factory=list)
    web: WebResearch | None = None
    degraded: str | None = None
    # ADR-032 §5: из однородных цифр — график, из разнородных — таблица
    chart_id: str | None = None
    table_id: str | None = None

    @property
    def changed(self) -> bool:
        return bool(self.facts or self.model_bullets)

    def to_dict(self) -> dict:
        return {
            "facts_from_web": len(self.facts),
            "facts_from_model": len(self.model_bullets),
            "web": self.web.to_dict() if self.web is not None else None,
            "degraded": self.degraded,
            "chart_id": self.chart_id,
            "table_id": self.table_id,
        }


def find_slide(plan: DeckPlan, slide_id: str) -> SlidePlan | None:
    return next((s for s in plan.slides if s.id == slide_id), None)


async def research_slide(
    plan: DeckPlan,
    slide: SlidePlan,
    pack: ContentPack,
    deck_brief: Brief,
    gateway: ModelGateway | None,
    rc: ResearchConfig,
    *,
    focus: str = "facts",
    search: SearchProvider | None = None,
    fetch=None,
) -> SlideResearch:
    purpose = f"{deck_brief.purpose}. Слайд: {slide.title_intent}. {slide.key_message}"
    brief = deck_brief.model_copy(update={"purpose": purpose.strip()})
    result = SlideResearch(pack=pack)
    if rc.web.available or search is not None:
        web = await research_web(
            pack,
            brief,
            gateway,
            rc.web,
            missing_slides=1,
            covered_topics=[s.title_intent for s in plan.slides if s.id != slide.id],
            deadline=time.monotonic() + rc.budget_s,
            search=search,
            fetch=fetch,
            focus=focus,
            min_facts_per_topic=1,
        )
        result.web = web
        if web.sections:
            language = deck_brief.language or pack.language or "ru"
            result.pack = merge_into_pack(pack, web.sections, web.sources, language)
            if web.datasets:
                known = {d.id for d in result.pack.datasets}
                result.pack = result.pack.model_copy(
                    update={
                        "datasets": [
                            *result.pack.datasets,
                            *(d for d in web.datasets if d.id not in known),
                        ]
                    }
                )
            # merge_into_pack сохраняет id разделов и порядок блоков —
            # ссылки <pack>:<section>:b<i> указывают на эти факты
            for section in web.sections:
                for bi, block in enumerate(section.blocks):
                    for text in block.items or []:
                        result.facts.append((text, section.id, bi))
            result.facts = result.facts[:_MAX_NEW_UNITS]
            if focus == "numbers":
                _attach_numbers_visual(result, slide)
            return result
        result.degraded = web.degraded or "no_verified_facts"
    else:
        result.degraded = "web_disabled"
    if focus == "numbers" or not can_fill(gateway):
        return result  # цифры из знаний модели не берём
    payload = {
        "brief": {
            "purpose": deck_brief.purpose,
            "audience": deck_brief.audience,
            "language": deck_brief.language,
            "tone": deck_brief.tone,
            "forbidden_claims": deck_brief.forbidden_claims or [],
        },
        "slide": {"title": slide.title_intent, "desired_visual": None},
        "deck_outline": [s.title_intent for s in plan.slides if s.id != slide.id],
        "facts": [u.text for u in slide.content_units if u.text and u.kind != Kind.title],
        "bullets": 3,
    }
    try:
        answer = await gateway.text_json(SLIDE_FILL_PROMPT, payload, SlideFill)
    except Exception:  # noqa: BLE001 — без добора, слайд не меняется
        return result
    if isinstance(answer, SlideFill):
        result.model_bullets = [b.strip() for b in answer.bullets if 20 <= len(b.strip()) <= 240]
        result.model_bullets = result.model_bullets[:_MAX_NEW_UNITS]
    return result


def _attach_numbers_visual(result: SlideResearch, slide: SlidePlan) -> None:
    """Однородные цифры → ``Chart`` в паке, разнородные → ``Table``."""
    facts = [text for text, _sec, _bi in result.facts]
    section_ids = {sec for _t, sec, _bi in result.facts}
    sections = [s for s in result.pack.sections if s.id in section_ids]
    source = next((b.source_ref for s in sections for b in s.blocks), None)
    if source is None:
        return
    item_id = f"{slide.id}-numbers"
    if result.web and result.web.datasets:
        dataset = result.web.datasets[0]
        chart = Chart(
            id=item_id,
            categories=[str(v) for v in dataset.columns[0].values],
            series=[ChartSeries(name=dataset.columns[1].name, values=dataset.columns[1].values)],
            source_ref=dataset.source_ref,
            source_note=dataset.context_text,
            chart_type="line" if dataset.columns[0].name == "Год" else "bar",
        )
        table = None
    else:
        chart, table = chart_or_table(facts, item_id, source)
    if chart is not None:
        if result.web and result.web.footnote and not chart.source_note:
            chart = chart.model_copy(update={"source_note": result.web.footnote})
        result.pack = result.pack.model_copy(update={"charts": [*result.pack.charts, chart]})
        result.chart_id = chart.id
    elif table is not None:
        result.pack = result.pack.model_copy(update={"tables": [*result.pack.tables, table]})
        result.table_id = table.id


def apply_to_plan(plan: DeckPlan, slide_id: str, research: SlideResearch, pack_id: str) -> DeckPlan:
    """Новые пункты — в слайд; веб-источники — в слайд источников (или
    новый перед финальным); граф фактов плана — от нового пака."""
    new_units = [
        ContentUnit(
            role="bullet",
            kind=Kind.bullet,
            text=text,
            origin=Origin.web,
            evidence_ids=[f"{pack_id}:{section_id}:b{bi}"],
        )
        for text, section_id, bi in research.facts
    ] + [
        ContentUnit(
            role="bullet",
            kind=Kind.bullet,
            text=text,
            origin=Origin.model,
            evidence_ids=[MODEL_SOURCE_ID],
        )
        for text in research.model_bullets
    ]
    visual_update: dict = {}
    fact_evidence = [f"{pack_id}:{sec}:b{bi}" for _t, sec, bi in research.facts]
    if research.chart_id:
        new_units.insert(
            0,
            ContentUnit(
                role="body",
                kind=Kind.chart,
                chart_ref=research.chart_id,
                origin=Origin.web,
                evidence_ids=fact_evidence,
            ),
        )
        visual_update = {"desired_visual": DesiredVisual.chart}
    elif research.table_id:
        new_units.insert(
            0,
            ContentUnit(
                role="body",
                kind=Kind.table,
                table_ref=research.table_id,
                origin=Origin.web,
                evidence_ids=fact_evidence,
            ),
        )
        visual_update = {"desired_visual": DesiredVisual.table}
    slides = [
        s.model_copy(
            update={
                "content_units": [*s.content_units, *new_units],
                **visual_update,
            }
        )
        if s.id == slide_id
        else s
        for s in plan.slides
    ]
    sources = research.web.sources if research.web is not None and research.facts else []
    if sources:
        language = plan.language or "ru"
        entries = [
            ContentUnit(
                role="bullet",
                kind=Kind.bullet,
                text=_bibliography_entry(src, language),
                origin=Origin.web,
                evidence_ids=[src.id],
            )
            for src in sources
        ]
        target = next((s for s in slides if is_sources_heading(s.title_intent)), None)
        if target is not None:
            slides = [
                s.model_copy(update={"content_units": [*s.content_units, *entries]})
                if s.id == target.id
                else s
                for s in slides
            ]
        else:
            title = "Источники" if language.startswith("ru") else "Sources"
            sources_slide = SlidePlan(
                id=f"slide-sources-{len(slides)}",
                index=0,
                purpose=Purpose.custom,
                title_intent=title,
                key_message=title,
                evidence_ids=[src.id for src in sources],
                content_units=[
                    ContentUnit(
                        role="title", kind=Kind.title, text=title, evidence_ids=[sources[0].id]
                    ),
                    *entries,
                ],
                density_budget=DensityBudget(level=Level.medium),
            )
            slides = [*slides[:-1], sources_slide, slides[-1]]
    slides = [s.model_copy(update={"index": i}) for i, s in enumerate(slides)]
    return plan.model_copy(
        update={
            "slides": slides,
            "evidence_graph_id": f"pack:{pack_id}",
            "brief": plan.brief.model_copy(update={"target_slide_count": len(slides)}),
        }
    )


__all__ = ["SlideResearch", "apply_to_plan", "find_slide", "research_slide"]
