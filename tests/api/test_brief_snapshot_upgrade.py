"""Additive Brief fields must not break presentations saved before the release."""

from types import SimpleNamespace

from deckdna.api.persistence import load_store, save_store
from deckdna.contracts.deck_plan import Brief
from deckdna.planning.request_constraints import validate_count_constraint


def test_old_pickled_brief_gets_defaults_without_becoming_a_new_author_choice(tmp_path):
    old = Brief(purpose="История", audience="команда", language="ru", target_slide_count=6)
    old.__dict__.pop("slide_count_policy")
    old.__dict__.pop("requested_slide_count")
    fields = set(old.model_fields_set)
    source = SimpleNamespace(plans={"saved": [old, old]}, artifacts={}, runs={}, jobs={},
                             job_events={})
    path = tmp_path / "store.pkl"
    save_store(source, path)
    target = SimpleNamespace(plans={}, artifacts={}, runs={}, jobs={}, job_events={})
    assert load_store(target, path, require_integrity=True)
    brief = target.plans["saved"][0]
    assert target.plans["saved"][1] is brief
    assert brief.slide_count_policy is None and brief.requested_slide_count is None
    assert brief.model_fields_set == fields
    validate_count_constraint(brief, 3)  # legacy shrink remains valid
