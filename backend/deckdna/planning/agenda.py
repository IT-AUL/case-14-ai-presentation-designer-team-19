"""Повестка по реальной колоде, а не по оглавлению источника.

Детерминированный планировщик пишет в слайд «План презентации» заголовки
ВСЕХ разделов контент-пакета. Живой прогон 29.09 (PDF на 16 разделов,
колода из 8 слайдов): 16 пунктов повестки, из них половины в колоде нет,
плюс повторы. Повестка обещает то, что будет показано, — поэтому пункты
пересобираются после того, как состав колоды окончательный:

* если в колоде есть слайды-разделители (не меньше двух) — их заголовки;
* иначе заголовки контентных слайдов в порядке колоды (``budget_fit``
  сокращает их до коротких тем);
* без заголовков — разделы-источники слайдов колоды, без повторов;
* не больше ``MAX_AGENDA_ITEMS`` пунктов; при избытке — равномерная
  выборка с сохранением первого и последнего.
"""

from __future__ import annotations

import re

from deckdna.contracts.content_pack import ContentPack
from deckdna.contracts.deck_plan import ContentUnit, DeckPlan, Kind, Purpose

MAX_AGENDA_ITEMS = 7
_SKIP = frozenset({
    Purpose.title, Purpose.agenda, Purpose.thank_you, Purpose.qa, Purpose.cta,
})
_EV_RE = re.compile(r"^ev_(?:sec|blk|lst)_(\d+)")


def _section_of(evidence_id: str, pack: ContentPack) -> int | None:
    m = _EV_RE.match(evidence_id)
    if m:
        si = int(m.group(1))
        return si if 0 <= si < len(pack.sections) else None
    for si, section in enumerate(pack.sections):
        if evidence_id == section.id or evidence_id.startswith(f"{pack.id}:{section.id}:"):
            return si
    return None


def _sample(items: list[str], limit: int) -> list[str]:
    if len(items) <= limit:
        return items
    step = (len(items) - 1) / (limit - 1)
    return [items[round(i * step)] for i in range(limit)]


def agenda_items(plan: DeckPlan, pack: ContentPack) -> list[tuple[str, list[str]]]:
    """[(текст пункта, evidence_ids)] для повестки этой колоды."""
    dividers = [
        (s.title_intent, list(s.evidence_ids))
        for s in plan.slides
        if s.purpose == Purpose.section_divider and s.title_intent
    ]
    if len(dividers) >= 2:
        return dividers[:MAX_AGENDA_ITEMS]
    # заголовки контентных слайдов — это и есть маршрут доклада; разделы
    # источника («Неделя 3», «2. Аргументов должно быть…») читаются хуже.
    # Короткие темы из них делает budget_fit (agenda=True).
    titled = [
        (s.title_intent.strip(), list(s.evidence_ids))
        for s in plan.slides
        if s.purpose not in _SKIP and (s.title_intent or "").strip()
    ]
    if len(titled) >= 2:
        unique_titles: dict[str, list[str]] = {}
        for text, ev in titled:
            unique_titles.setdefault(text, ev)
        pairs_t = list(unique_titles.items())
        keep_t = set(_sample([t for t, _ in pairs_t], MAX_AGENDA_ITEMS))
        return [(t, ev) for t, ev in pairs_t if t in keep_t]
    seen: dict[int, None] = {}
    for slide in plan.slides:
        if slide.purpose in _SKIP:
            continue
        sections = [
            si for eid in slide.evidence_ids
            if (si := _section_of(eid, pack)) is not None
        ]
        if sections:
            seen.setdefault(min(sections), None)
    items = [
        (pack.sections[si].heading, [pack.sections[si].id])
        for si in seen
        if (pack.sections[si].heading or "").strip()
    ]
    # заголовок всей колоды (раздел-обложка источника) — не пункт повестки
    deck_titles = {
        (s.title_intent or "").strip().casefold()
        for s in plan.slides if s.purpose == Purpose.title
    } | {(getattr(pack, "title", None) or "").strip().casefold()}
    unique: dict[str, list[str]] = {}
    for text, ev in items:
        if text.strip().casefold() not in deck_titles:
            unique.setdefault(text.strip(), ev)
    pairs = list(unique.items())
    keep = set(_sample([t for t, _ in pairs], MAX_AGENDA_ITEMS))
    return [(t, ev) for t, ev in pairs if t in keep]


def sync_agenda(plan: DeckPlan, pack: ContentPack) -> DeckPlan:
    """План с повесткой, собранной по составу колоды (идемпотентно)."""
    idx = next((i for i, s in enumerate(plan.slides) if s.purpose == Purpose.agenda), None)
    if idx is None:
        return plan
    items = agenda_items(plan, pack)
    if len(items) < 2:
        return plan
    slide = plan.slides[idx]
    kept = [u for u in slide.content_units if u.kind != Kind.bullet]
    bullets = [
        ContentUnit(role="agenda-item", kind=Kind.bullet, text=text, evidence_ids=ev)
        for text, ev in items
    ]
    new_slide = slide.model_copy(update={"content_units": kept + bullets})
    if slide.density_budget is not None:
        new_slide = new_slide.model_copy(
            update={
                "density_budget": slide.density_budget.model_copy(
                    update={"max_items": len(bullets)}
                )
            }
        )
    slides = list(plan.slides)
    slides[idx] = new_slide
    return plan.model_copy(update={"slides": slides})
