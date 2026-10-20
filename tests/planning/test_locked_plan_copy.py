"""Locked author copy survives rewriting and destructive plan operations."""
import asyncio
from pathlib import Path

import pytest
from deckdna.assistant.plan_edit import apply_action
from deckdna.contracts.deck_plan import Brief, ContentUnit, Kind
from deckdna.errors import DeckDNAError
from deckdna.ingestion.content_parsers import parse_file
from deckdna.planning import content_style
from deckdna.planning.story_director import plan_deck
from deckdna.planning.story_director_v2 import _slide_task
from deckdna.pptx.fitting.budget_fit import fit_plan_to_budgets
from deckdna.providers.mock import MockProvider

COPY = "Один проверенный результат без гарантий повторения. " * 3


@pytest.fixture
def inputs():
    pack = parse_file(Path("tests/fixtures/content/poc_article.md"))
    plan = plan_deck(pack, Brief(purpose="Обзор", audience="команда", language="ru",
                                 target_slide_count=10))
    slide = next(s for s in plan.slides if s.purpose.value == "problem"
                 or any(u.kind == Kind.bullet for u in s.content_units))
    units = [u.model_copy(update={"text": COPY, "locked": True})
             if u.kind != Kind.title else u.model_copy(update={"locked": True})
             for u in slide.content_units]
    if not any(u.kind == Kind.title for u in units):
        units.insert(0, ContentUnit(role="title", kind=Kind.title, text=slide.title_intent,
                                    locked=True))
    locked = slide.model_copy(update={"content_units": units})
    plan = plan.model_copy(update={"slides": [locked if s.id == locked.id else s
                                             for s in plan.slides]})
    return plan, pack, locked


@pytest.mark.asyncio
async def test_style_does_not_send_locked_copy_to_rewriter(inputs):
    plan, _, slide = inputs
    one = plan.model_copy(update={"slides": [slide]})
    gateway = MockProvider(fixtures={content_style.PROMPT_NAME: {"items": []}})
    result = await content_style.style_deck_content(one, gateway)
    assert result == one and gateway.calls == []


@pytest.mark.asyncio
async def test_budget_does_not_rewrite_or_request_a_locked_slide(inputs):
    plan, _, slide = inputs
    one = plan.model_copy(update={"slides": [slide]})
    gateway = MockProvider(fixtures={"slide_budget_fit": {"title": "Новое", "bullets": []}})
    result, report = await fit_plan_to_budgets(one, ["donor"], {
        "donor": {"title_max_chars": 3, "bullet_max_chars": 4, "card_headings": True},
    }, gateway)
    assert result == one and report.model_calls == 0 and gateway.calls == []


@pytest.mark.parametrize("action", ["remove", "to_diagram"])
def test_destructive_plan_action_cannot_drop_locked_copy(inputs, action):
    plan, pack, slide = inputs
    with pytest.raises(DeckDNAError) as exc:
        apply_action(plan, slide.id, action, pack)
    assert exc.value.details["rule"] == "request.locked_copy"


def test_shorten_leaves_locked_body_verbatim(inputs):
    plan, pack, slide = inputs
    result, _ = apply_action(plan, slide.id, "shorten", pack)
    target = next(s for s in result.slides if s.id == slide.id)
    assert target.content_units == slide.content_units


@pytest.mark.asyncio
async def test_stage_two_does_not_rewrite_locked_title_or_body(inputs):
    _, _, slide = inputs
    gateway = MockProvider(fixtures={"content_writer": {}})
    result = await _slide_task(slide.index, {slide.index: slide}, {}, gateway, {}, None,
                               "ru", True, asyncio.Semaphore(1))
    assert result[1:4] == (slide.title_intent, slide.key_message, slide.content_units)
    assert not result[4] and gateway.calls == []
