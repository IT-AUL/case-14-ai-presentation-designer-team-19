"""Slide-count bounds must be config-driven, not a hardcoded 10–15.

TZ baseline: 10–15 slides OR user-specified. The official profile ships
10–15; a wider profile must allow shorter and longer decks.
"""

import pytest
from deckdna.errors import DeckDNAError
from deckdna.planning.config import (
    ABSOLUTE_MAX_SLIDES,
    ABSOLUTE_MIN_SLIDES,
    DeckBounds,
    GenerationConfig,
)
from deckdna.planning.validation import validate_deck_plan, validate_slide_count


def _plan(n: int) -> dict:
    return {
        "schema_version": "1",
        "id": "plan-test",
        "brief": {
            "purpose": "test",
            "audience": "jury",
            "language": "ru",
            "target_slide_count": n,
        },
        "evidence_graph_id": "eg-1",
        "objective": "demo",
        "audience": "jury",
        "language": "ru",
        "slides": [
            {
                "id": f"s{i}",
                "index": i,
                "purpose": "overview",
                "title_intent": f"Conclusion {i}",
                "key_message": "msg",
                "evidence_ids": ["e1"],
                "content_units": [{"role": "body", "kind": "bullet", "text": "x"}],
                "density_budget": {"level": "sparse"},
            }
            for i in range(n)
        ],
        "provenance": {"planner": "test", "prompt_version": "0", "schema_version": "1"},
    }


EXTENDED = GenerationConfig(deck=DeckBounds(min_slides=3, max_slides=40, default_slide_count=12))
OFFICIAL = GenerationConfig(deck=DeckBounds(min_slides=10, max_slides=15, default_slide_count=12))


def test_six_slides_allowed_with_extended_profile():
    validate_slide_count(6, EXTENDED)
    validate_deck_plan(_plan(6), EXTENDED)


def test_twenty_slides_allowed_with_extended_profile():
    validate_slide_count(20, EXTENDED)
    validate_deck_plan(_plan(20), EXTENDED)


def test_official_profile_rejects_out_of_range():
    with pytest.raises(DeckDNAError) as exc:
        validate_slide_count(6, OFFICIAL)
    assert exc.value.code == "invalid_input"
    with pytest.raises(DeckDNAError):
        validate_slide_count(20, OFFICIAL)


def test_bounds_come_from_config_not_constants():
    tight = GenerationConfig(deck=DeckBounds(min_slides=5, max_slides=7, default_slide_count=6))
    validate_slide_count(5, tight)
    with pytest.raises(DeckDNAError):
        validate_slide_count(10, tight)  # inside TZ range, outside this profile


def test_absolute_guard_rails_still_apply():
    with pytest.raises(DeckDNAError):
        validate_slide_count(ABSOLUTE_MIN_SLIDES - 1, EXTENDED)
    with pytest.raises(DeckDNAError):
        validate_slide_count(ABSOLUTE_MAX_SLIDES + 1, EXTENDED)


def test_slide_count_mismatch_rejected():
    plan = _plan(12)
    plan["slides"] = plan["slides"][:11]
    with pytest.raises(DeckDNAError) as exc:
        validate_deck_plan(plan, OFFICIAL)
    assert exc.value.code == "validation_failed"
