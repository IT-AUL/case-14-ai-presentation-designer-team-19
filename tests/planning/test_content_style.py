"""Навык content_style: модель переписывает content_units деловым языком
после планирования, ДО exemplar rerank/compose (см. докстринг модуля
content_style.py). В отличие от text_fit — не про переполнение рамки, про
качество текста; заземление (никаких выдуманных фактов) проверяется тем же
способом, что и у text_fit."""

from __future__ import annotations

import asyncio

import pytest
from deckdna.contracts.deck_plan import (
    Brief,
    ContentUnit,
    DeckPlan,
    DensityBudget,
    Kind,
    Level,
    Provenance,
    Purpose,
    SlidePlan,
)
from deckdna.planning import content_style
from deckdna.providers.mock import MockProvider

LONG = (
    "Ручная адаптация контента под фирменный шаблон занимает у команды до 6 часов "
    "на каждую презентацию и требует участия дизайнера на каждом шаге работы"
)
SHORT = "12%"


class FakeGateway:
    """Отвечает заданной функцией от payload; считает вызовы."""

    provider_name = "fake"

    def __init__(self, answer):
        self.answer = answer
        self.calls: list[dict] = []

    async def text_json(self, prompt_name, payload, schema):
        assert prompt_name == content_style.PROMPT_NAME
        self.calls.append(payload)
        value = self.answer(payload)
        if isinstance(value, Exception):
            raise value
        return schema.model_validate(value)


def _unit(text: str, kind: Kind = Kind.bullet) -> ContentUnit:
    return ContentUnit(role="bullet", kind=kind, text=text, evidence_ids=["e1"])


def _slide(units: list[ContentUnit], index: int = 0) -> SlidePlan:
    return SlidePlan(
        id=f"s{index}",
        index=index,
        purpose=Purpose.problem,
        title_intent="Заголовок слайда",
        key_message="ключевая мысль",
        evidence_ids=["e1"],
        content_units=units,
        density_budget=DensityBudget(level=Level.medium, max_chars=200),
    )


def _plan(slides: list[SlidePlan]) -> DeckPlan:
    return DeckPlan(
        schema_version="1.0.0",
        id="p1",
        brief=Brief(purpose="x", audience="y", language="ru", target_slide_count=3),
        evidence_graph_id="g1",
        objective="o",
        audience="y",
        language="ru",
        slides=slides,
        provenance=Provenance(planner="p", prompt_version="v1", schema_version="1.0.0"),
    )


def _style(plan, gw):
    return asyncio.run(content_style.style_deck_content(plan, gw))


def test_model_rewrite_replaces_bullet_text(tmp_path=None):
    plan = _plan([_slide([_unit(LONG)])])
    gw = FakeGateway(
        lambda p: {
            "items": [
                {"index": it["index"], "text": "Адаптация шаблона под бренд занимает до 6 часов."}
                for it in p["items"]
            ]
        }
    )
    new_plan = _style(plan, gw)
    expected = "Адаптация шаблона под бренд занимает до 6 часов."
    assert new_plan.slides[0].content_units[0].text == expected
    assert gw.calls[0]["items"][0]["text"] == LONG


def test_short_units_are_never_sent_to_the_model():
    plan = _plan([_slide([_unit(SHORT)])])
    gw = FakeGateway(lambda p: {"items": []})
    new_plan = _style(plan, gw)
    assert new_plan.slides[0].content_units[0].text == SHORT
    assert gw.calls == []


def test_non_bullet_units_are_left_alone():
    plan = _plan([_slide([_unit(LONG, kind=Kind.number)])])
    gw = FakeGateway(lambda p: {"items": []})
    new_plan = _style(plan, gw)
    assert new_plan.slides[0].content_units[0].text == LONG
    assert gw.calls == []


def test_invented_numbers_are_rejected_and_source_kept():
    plan = _plan([_slide([_unit(LONG)])])
    gw = FakeGateway(
        lambda p: {
            "items": [
                {"index": it["index"], "text": "Адаптация занимает 40 часов."}
                for it in p["items"]
            ]
        }
    )
    new_plan = _style(plan, gw)
    assert new_plan.slides[0].content_units[0].text == LONG


def test_runaway_growth_is_rejected():
    plan = _plan([_slide([_unit(LONG)])])
    bloated = "слово " * 100
    gw = FakeGateway(
        lambda p: {"items": [{"index": it["index"], "text": bloated} for it in p["items"]]}
    )
    new_plan = _style(plan, gw)
    assert new_plan.slides[0].content_units[0].text == LONG


def test_provider_failure_keeps_source_text_not_raises():
    plan = _plan([_slide([_unit(LONG)])])
    gw = FakeGateway(lambda p: RuntimeError("provider down"))
    new_plan = _style(plan, gw)
    assert new_plan.slides[0].content_units[0].text == LONG


def test_empty_answer_text_is_rejected():
    plan = _plan([_slide([_unit(LONG)])])
    gw = FakeGateway(
        lambda p: {"items": [{"index": it["index"], "text": "   "} for it in p["items"]]}
    )
    new_plan = _style(plan, gw)
    assert new_plan.slides[0].content_units[0].text == LONG


def test_multiple_slides_are_batched_and_matched_by_index():
    plan = _plan(
        [
            _slide([_unit(LONG)], index=0),
            _slide([_unit(LONG.replace("6 часов", "8 часов"))], index=1),
        ]
    )
    gw = FakeGateway(
        lambda p: {
            "items": [
                {"index": it["index"], "text": f"Переписано #{it['index']}: {it['text'][:20]}"}
                for it in p["items"]
            ]
        }
    )
    new_plan = _style(plan, gw)
    assert new_plan.slides[0].content_units[0].text.startswith("Переписано #0")
    assert new_plan.slides[1].content_units[0].text.startswith("Переписано #1")


def test_plan_is_not_mutated_in_place():
    plan = _plan([_slide([_unit(LONG)])])
    gw = FakeGateway(
        lambda p: {
            "items": [
                {"index": it["index"], "text": "Коротко и по делу."} for it in p["items"]
            ]
        }
    )
    new_plan = _style(plan, gw)
    assert plan.slides[0].content_units[0].text == LONG
    assert new_plan is not plan
    assert new_plan.slides[0].content_units[0].text != LONG


def test_no_eligible_units_returns_plan_unchanged_without_calling_model():
    plan = _plan([_slide([_unit(SHORT, kind=Kind.number)])])
    gw = FakeGateway(lambda p: {"items": []})
    new_plan = _style(plan, gw)
    assert new_plan is plan
    assert gw.calls == []


def test_offline_mock_never_writes_synthetic_text():
    assert not content_style.can_rewrite(MockProvider())
    assert content_style.can_rewrite(MockProvider(fixtures={"content_style": {"items": []}}))
    assert content_style.can_rewrite(FakeGateway(lambda p: {}))


@pytest.mark.parametrize(
    "source,text,expected_bad",
    [
        ("Занимает 6 часов", "", True),
        ("Занимает 6 часов", "Занимает 6 часов, экономит 40%", True),
        (LONG, "Адаптация шаблона занимает у команды до 6 часов на презентацию.", False),
    ],
)
def test_validate_rules(source, text, expected_bad):
    assert (content_style.validate(source, text) is not None) is expected_bad
