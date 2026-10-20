"""Безопасные исправления до ревизии 1 (handoff Q2) и трекер этапов (D3/D4)."""

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
    import asyncio

    from deckdna.contracts.deck_plan import Brief
    from deckdna.ingestion.content_parsers import parse_file
    from deckdna.planning.agenda import sync_agenda
    from deckdna.planning.story_director import plan_deck
    from deckdna.pptx.composing.minimal import generate_deck
    from deckdna.research import fill_content

    fill = asyncio.run(fill_content(parse_file(CONTENT), Brief.model_validate(BRIEF), None))
    pack = fill.pack
    plan = sync_agenda(plan_deck(pack, fill.brief), pack)
    deck = tmp_path / "raw.pptx"
    generate_deck(
        TEMPLATE,
        plan,
        deck,
        tables=pack.tables,
        charts=pack.charts,
        diagrams=pack.diagrams,
        assets=pack.assets,
        content_path=CONTENT,
    )
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

    real_apply = pipeline.apply_repairs

    def hostile_repair(source, actions, target, **kw):
        from pptx import Presentation
        from pptx.util import Inches

        result = real_apply(source, actions, target, **kw)
        # Inject an actual regression into the repaired copy. The real audit
        # must catch it even when the original generated deck has no errors.
        prs = Presentation(target)
        shape = prs.slides[0].shapes.add_textbox(
            -Inches(2), Inches(2), Inches(3), Inches(1),
        )
        shape.text = "Deliberately broken repair output"
        prs.save(target)
        assert any(i.rule_code == "layout.out_of_bounds" for i in audit_deck(target))
        return result

    monkeypatch.setattr(pipeline, "apply_repairs", hostile_repair)
    kept, gained, reason = _auto_fix(deck, before, protected=None)
    assert gained == {} and reason and "откат" in reason
    assert kept is before
    assert deck.read_bytes() == original, "a rejected fix must not touch the deck"


def test_auto_fix_rolls_back_when_deterministic_warnings_worsen(tmp_path, monkeypatch):
    """Vision audit gate п.2: патч принимается, только если детерминированный аудит
    не ухудшился (включая предупреждения warning), иначе откат."""
    from deckdna.audit.issues import AuditIssue

    deck = _raw_deck(tmp_path)
    before = audit_deck(deck, deck_revision=1)
    original = deck.read_bytes()

    def fake_audit_after(path, *, deck_revision=1):
        # Целевое правило исправлено, но появилось 2 новых детерминированных warning
        cleaned = [i for i in before if i.rule_code not in AUTO_FIX_RULES]
        extra = [
            AuditIssue(
                id=f"iss_warn_{k}",
                audit_run_id="aud_after",
                deck_revision=deck_revision,
                slide_index=0,
                rule_code="text.overflow",
                severity="warning",
                deterministic=True,
                message="new warning after patch",
            )
            for k in range(len(before) + 1)
        ]
        return cleaned + extra

    monkeypatch.setattr(pipeline, "audit_deck", fake_audit_after)
    kept, gained, reason = _auto_fix(deck, before, protected=None)
    assert gained == {} and reason and "откат" in reason
    assert kept is before
    assert deck.read_bytes() == original


def test_sync_auto_repair_capped_at_one_cycle():
    """Vision audit gate п.2: не больше одного цикла автоисправления в синхронном запросе."""
    from deckdna.generation.pipeline import _max_auto_repair_cycles

    assert _max_auto_repair_cycles() == 1


def test_auto_fix_without_candidates_is_a_no_op(tmp_path):
    deck = tmp_path / "d.pptx"
    shutil.copy(FIXTURES / "pptx" / "synthetic_unseen_sparse.pptx", deck)
    only_other = [i for i in audit_deck(deck, deck_revision=1) if i.rule_code not in AUTO_FIX_RULES]
    issues, gained, reason = _auto_fix(deck, only_other, protected=None)
    assert issues is only_other and gained == {} and reason is None

