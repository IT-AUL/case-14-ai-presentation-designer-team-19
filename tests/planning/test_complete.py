"""ADR-030: слайд, добавленный пользователем одним заголовком, получает
тело до вёрстки — из его ключевой мысли или от модели."""

from __future__ import annotations

import asyncio

from deckdna.contracts.content_pack import Block, ContentPack, Kind, Section, SourceRef
from deckdna.contracts.deck_plan import Brief, ContentUnit, DensityBudget, Level, Purpose, SlidePlan
from deckdna.contracts.deck_plan import Kind as UnitKind
from deckdna.planning import plan_deck, validate_deck_plan
from deckdna.planning.complete import SLIDE_FILL_PROMPT, complete_hollow_slides, hollow_slides
from deckdna.planning.config import DeckBounds, GenerationConfig
from deckdna.providers.mock import MockProvider

CFG = GenerationConfig(deck=DeckBounds(min_slides=3, max_slides=40))
BRIEF = Brief(purpose="Цифровой дизайнер презентаций", audience="руководство",
              language="ru", target_slide_count=4)
POINT = "Содержательный пункт про шаблоны компании и единый стиль документов"


def _pack() -> ContentPack:
    return ContentPack(
        schema_version="1.0", id="pack-c", language="ru",
        sections=[Section(id="s0", heading="Стиль", level=2, blocks=[
            Block(kind=Kind.list, items=[f"{POINT} {i}." for i in range(4)],
                  source_ref=SourceRef(artifact_id="a.md"))
        ])],
        tables=[], assets=[], warnings=[],
    )


def _with_user_slides(*slides: tuple[str, str]):
    plan = plan_deck(_pack(), BRIEF, config=CFG)
    closing = plan.slides[-1]
    added = [
        SlidePlan(
            id=f"slide-user-{i}", index=0, purpose=Purpose.custom, title_intent=title,
            key_message=message, evidence_ids=[f"user:slide-user-{i}"],
            content_units=[ContentUnit(role="title", kind=UnitKind.title, text=title,
                                       evidence_ids=[f"user:slide-user-{i}"])],
            density_budget=DensityBudget(level=Level.medium),
        )
        for i, (title, message) in enumerate(slides)
    ]
    ordered = [*plan.slides[:-1], *added, closing]
    ordered = [s.model_copy(update={"index": i}) for i, s in enumerate(ordered)]
    return plan.model_copy(update={
        "slides": ordered,
        "brief": plan.brief.model_copy(update={"target_slide_count": len(ordered)}),
    })


def test_key_message_becomes_the_locked_body():
    plan = _with_user_slides(("Планы на квартал", "Запустить пилот в двух отделах до декабря"))
    done, report = asyncio.run(complete_hollow_slides(plan, BRIEF, None, _pack()))
    slide = next(s for s in done.slides if s.id == "slide-user-0")
    body = [u for u in slide.content_units if u.kind != UnitKind.title]
    assert [(u.text, u.locked, u.origin.value) for u in body] == [
        ("Запустить пилот в двух отделах до декабря", True, "user")
    ]
    assert report["from_key_message"] == ["slide-user-0"]
    assert not hollow_slides(done)
    validate_deck_plan(done, CFG)


def test_model_writes_bullets_for_a_bare_title():
    seen = {}

    def fill(payload):
        seen.update(payload)
        return {"bullets": [
            "Первый содержательный пункт про риски внедрения сервиса",
            "Второй содержательный пункт про сроки и ответственных",
            "Подробнее: https://example.com/risks — ссылка без опоры",
        ]}

    plan = _with_user_slides(("Риски", ""))
    gw = MockProvider(fixtures={SLIDE_FILL_PROMPT: fill})
    done, report = asyncio.run(complete_hollow_slides(plan, BRIEF, gw, _pack()))
    slide = next(s for s in done.slides if s.id == "slide-user-0")
    body = [u for u in slide.content_units if u.kind != UnitKind.title]
    assert [u.origin.value for u in body] == ["model", "model"]  # ссылка отброшена
    assert report["from_model"] == ["slide-user-0"]
    assert seen["slide"]["title"] == "Риски"
    assert "Риски" not in seen["deck_outline"]
    assert seen["facts"]  # факты пака переданы модели


def test_without_model_or_message_the_slide_is_reported_unfilled():
    plan = _with_user_slides(("Риски", ""))
    done, report = asyncio.run(complete_hollow_slides(plan, BRIEF, MockProvider(), _pack()))
    assert done is plan
    assert report["unfilled"] == ["slide-user-0"]
