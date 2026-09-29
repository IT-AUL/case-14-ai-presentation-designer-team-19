"""Повестка перечисляет разделы, которые реально есть в колоде."""

from __future__ import annotations

from pathlib import Path

from deckdna.contracts.deck_plan import Brief, Kind, Purpose
from deckdna.ingestion.content_parsers import parse_file
from deckdna.planning.agenda import MAX_AGENDA_ITEMS, sync_agenda
from deckdna.planning.story_director import plan_deck

CONTENT = Path("tests/fixtures/content/deckdna_pitch_rich.md")


def _agenda(plan):
    slide = next(s for s in plan.slides if s.purpose == Purpose.agenda)
    return [u.text for u in slide.content_units if u.kind == Kind.bullet]


def test_agenda_lists_only_covered_sections_without_repeats():
    pack = parse_file(CONTENT)
    plan = plan_deck(pack, Brief(purpose="t", audience="q", language="ru", target_slide_count=6))
    items = _agenda(sync_agenda(plan, pack))
    assert 2 <= len(items) <= MAX_AGENDA_ITEMS
    assert len(items) == len(set(items))
    content_titles = {
        s.title_intent for s in plan.slides
        if s.purpose not in (Purpose.title, Purpose.agenda, Purpose.thank_you)
    }
    assert set(items) <= content_titles  # маршрут доклада, а не оглавление источника
    title = next(s.title_intent for s in plan.slides if s.purpose == Purpose.title)
    assert title not in items


def test_sync_agenda_is_idempotent():
    pack = parse_file(CONTENT)
    plan = plan_deck(pack, Brief(purpose="t", audience="q", language="ru", target_slide_count=10))
    once = sync_agenda(plan, pack)
    assert sync_agenda(once, pack) == once
