"""Безопасные исправления до ревизии 1 (contract Q2) и трекер этапов (D3/D4)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from deckdna.audit.basic import audit_deck
from deckdna.generation import pipeline
from deckdna.generation.pipeline import AUTO_FIX_RULES, PUBLIC_STAGES, _auto_fix

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TEMPLATE = FIXTURES / "pptx" / "vk_tech_template.pptx"
CONTENT = FIXTURES / "content" / "deckdna_pitch_rich.md"
BRIEF = {"purpose": "p", "audience": "a", "language": "ru", "target_slide_count": 12}


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    stages: list[str] = []
    out = tmp_path_factory.mktemp("autofix")
    report = pipeline.generate(TEMPLATE, CONTENT, BRIEF, out, on_stage=stages.append)
    return report, stages, out


def test_stages_are_reported_in_pipeline_order(run):
    report, stages, _ = run
    assert tuple(stages) == PUBLIC_STAGES
    assert [t["stage"] for t in report["stage_timings"]] == list(PUBLIC_STAGES)
    assert all(t["seconds"] >= 0 and t["cached"] is False for t in report["stage_timings"])


def test_revision_one_has_no_fixable_safe_issues(run):
    report, _, out = run
    rules = {i["rule_code"] for i in report["audit_issues"]}
    assert not rules & AUTO_FIX_RULES
    # и это не артефакт отчёта — свежий аудит файла говорит то же самое
    fresh = {i.rule_code for i in audit_deck(out / "deck.pptx", deck_revision=1)}
    assert not fresh & AUTO_FIX_RULES


def test_what_was_fixed_is_recorded(run):
    report, _, _ = run
    assert report["auto_fixes"], "VK Tech deck always needs at least one safe fix"
    assert set(report["auto_fixes"]) <= AUTO_FIX_RULES
    fallbacks = report["quality_passport"]["fallbacks"]
    disclosed = {f["feature"] for f in fallbacks if f["strategy"] == "auto_fix"}
    assert disclosed == {f"repair.{r}" for r in report["auto_fixes"]}
    fixed = report["quality_passport"]["issues_summary"]["fixed"]
    assert fixed == sum(report["auto_fixes"].values())


def test_issues_carry_revision_one(run):
    report, _, _ = run
    assert {i["deck_revision"] for i in report["audit_issues"]} == {1}


def _raw_deck(tmp_path):
    """Колода до автоисправления: собрана без него."""
    from deckdna.contracts.deck_plan import Brief
    from deckdna.ingestion.content_parsers import parse_file
    from deckdna.planning.story_director import plan_deck
    from deckdna.pptx.composing.minimal import generate_deck

    pack = parse_file(CONTENT)
    plan = plan_deck(pack, Brief.model_validate(BRIEF))
    deck = tmp_path / "raw.pptx"
    generate_deck(TEMPLATE, plan, deck, content_path=CONTENT)
    return deck


def test_auto_fix_touches_only_the_safe_rules(tmp_path):
    deck = _raw_deck(tmp_path)
    before = audit_deck(deck, deck_revision=1)
    after, gained, rejected = _auto_fix(deck, before, protected=None)
    assert rejected is None and gained
    assert set(gained) <= AUTO_FIX_RULES
    # правила вне списка не тронуты
    for rule in {i.rule_code for i in before} - AUTO_FIX_RULES:
        assert sum(i.rule_code == rule for i in after) <= sum(i.rule_code == rule for i in before)


def test_auto_fix_rolls_back_when_it_creates_new_hard_errors(tmp_path, monkeypatch):
    deck = _raw_deck(tmp_path)
    before = audit_deck(deck, deck_revision=1)
    original = deck.read_bytes()

    real_audit = pipeline.audit_deck

    def hostile_audit(path, **kw):
        issues = real_audit(path, **kw)
        # моделируем регресс: «после исправления» стало больше error/blocker вне списка
        extra = [i for i in issues if i.severity == "error" and i.rule_code not in AUTO_FIX_RULES]
        return issues + [extra[0]] * 3 if extra else issues

    monkeypatch.setattr(pipeline, "audit_deck", hostile_audit)
    kept, gained, reason = _auto_fix(deck, before, protected=None)
    assert gained == {} and reason and "откат" in reason
    assert kept is before
    assert deck.read_bytes() == original, "a rejected fix must not touch the deck"


def test_auto_fix_without_candidates_is_a_no_op(tmp_path):
    deck = tmp_path / "d.pptx"
    shutil.copy(FIXTURES / "pptx" / "synthetic_unseen_sparse.pptx", deck)
    only_other = [i for i in audit_deck(deck, deck_revision=1) if i.rule_code not in AUTO_FIX_RULES]
    issues, gained, reason = _auto_fix(deck, only_other, protected=None)
    assert issues is only_other and gained == {} and reason is None
