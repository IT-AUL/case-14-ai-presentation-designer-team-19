"""B02: a requested hard limit survives every change of actual plan length."""

import pytest
from deckdna.assistant.plan_edit import apply_action
from deckdna.contracts.content_pack import Block, ContentPack, Section, SourceRef
from deckdna.contracts.deck_plan import Brief
from deckdna.errors import DeckDNAError
from deckdna.planning import plan_deck, validate_deck_plan
from deckdna.planning.config import DeckBounds, GenerationConfig

CFG = GenerationConfig(deck=DeckBounds(min_slides=3, max_slides=40))


def brief(policy="preferred", target=8, **extra):
    return Brief(purpose="Обзор", audience="команда", language="ru",
                 target_slide_count=target, slide_count_policy=policy, **extra)


def pack(rich=False):
    return ContentPack(schema_version="1", id="constraints", language="ru", sections=[
        Section(id=f"s{i}", heading=f"Раздел {i}", level=2, blocks=[Block(
            kind="list", items=[f"Содержательный пункт {i}.{j} с описанием результатов работы."
                                for j in range(4 if rich else 1)],
            source_ref=SourceRef(artifact_id="source.md"),
        )]) for i in range(6 if rich else 1)
    ], tables=[], assets=[], warnings=[])


def test_hard_count_is_bound_and_survives_copy_and_roundtrip():
    original = brief("exact")
    assert original.requested_slide_count == 8
    changed = original.model_copy(update={"target_slide_count": 3})
    assert Brief.model_validate(changed.model_dump()).requested_slide_count == 8


def test_legacy_and_explicit_preferred_can_shorten_sparse_input():
    for b in (brief(), brief().model_copy(update={"slide_count_policy": None})):
        plan = plan_deck(pack(), b, config=CFG)
        assert len(plan.slides) < 8


def test_exact_shortage_is_a_typed_conflict_instead_of_new_limit():
    with pytest.raises(DeckDNAError) as error:
        plan_deck(pack(), brief("exact"), config=CFG)
    assert error.value.code == "validation_failed"
    assert error.value.details["rule"] == "request.slide_count"
    assert error.value.details["required"] == 8
    assert error.value.details["actual"] < 8


def test_maximum_caps_planning_budget_and_allows_shorter_result():
    b = brief("maximum", target=10, requested_slide_count=6)
    plan = plan_deck(pack(rich=True), b, config=CFG)
    assert len(plan.slides) <= 6
    assert plan.brief.requested_slide_count == 6
    validate_deck_plan(plan, CFG)


def test_explicit_exact_plan_edit_cannot_change_page_count():
    plan = plan_deck(pack(rich=True), brief("exact", target=6), config=CFG)
    before = plan.model_dump_json()
    slide = next(s for s in plan.slides if s.purpose.value not in ("title", "agenda", "thank_you"))
    with pytest.raises(DeckDNAError) as error:
        apply_action(plan, slide.id, "remove", pack(rich=True))
    assert error.value.details["rule"] == "request.slide_count"
    assert plan.model_dump_json() == before


def test_plan_cannot_launder_actual_count_into_the_exact_limit():
    plan = plan_deck(pack(), brief(), config=CFG)
    plan = plan.model_copy(update={"brief": brief("exact").model_copy(update={
        "target_slide_count": len(plan.slides),
    })})
    with pytest.raises(DeckDNAError) as error:
        validate_deck_plan(plan, CFG)
    assert error.value.details["required"] == 8


async def test_structure_budget_uses_hard_cap_before_any_model_call():
    from deckdna.planning.structure import plan_structure_llm
    from deckdna.providers.mock import MockProvider

    result = await plan_structure_llm(
        pack(rich=True), brief("maximum", target=10, requested_slide_count=6),
        MockProvider(), config=CFG,
    )
    assert result.brief.target_slide_count == 6
    assert result.brief.requested_slide_count == 6
    assert result.total_slides <= 6
