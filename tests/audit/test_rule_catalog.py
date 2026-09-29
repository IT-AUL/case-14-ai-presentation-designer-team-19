"""Каталог правил аудита не расходится ни с аудитом, ни с repair-планировщиком.

Handoff B5: ``repairable=true`` допустим только у правил, для которых
планировщик умеет построить действие, а исполнитель — его применить.
"""

from __future__ import annotations

from deckdna.audit import basic as audit_basic
from deckdna.audit import contextual
from deckdna.audit.catalog import (
    CATEGORIES,
    NON_EXECUTABLE_RULES,
    RULES,
    is_repairable,
    repairable_rules,
)
from deckdna.repair import apply as repair_apply
from deckdna.repair import planner


def test_every_emitted_rule_is_cataloged():
    deterministic = {
        v for k, v in vars(audit_basic).items() if k.startswith("RULE_")
    }
    contextual_codes = {code for code, _ in contextual._CHECK_RULES.values()}
    assert deterministic | contextual_codes == set(RULES)


def test_catalog_categories_are_the_documented_six():
    assert {r.category for r in RULES.values()} <= set(CATEGORIES)


def test_deterministic_flag_matches_layer():
    contextual_codes = {code for code, _ in contextual._CHECK_RULES.values()}
    for code, info in RULES.items():
        assert info.deterministic == (code not in contextual_codes)


def test_default_severity_matches_contextual_registry():
    for code, severity in contextual._CHECK_RULES.values():
        assert RULES[code].default_severity == severity


def test_repairable_equals_planner_handlers():
    assert repairable_rules() == set(planner._RULE_HANDLERS) - NON_EXECUTABLE_RULES


def test_repairable_rules_have_a_fix_title():
    for code in repairable_rules():
        assert RULES[code].fix_title_ru, code


def test_non_repairable_rules_are_flagged_false_by_audit():
    for code in ("text.font_floor", "template.font_scale", "density.occupancy"):
        assert not is_repairable(code)
    assert not is_repairable("no.such.rule")


def test_every_planned_action_type_is_executable_or_excluded():
    """Правило из repairable_rules() не должно планировать action_type,
    которого нет в исполнителе (иначе not_implemented вместо fix)."""
    assert "native_rebuild" not in repair_apply._IMPLEMENTED
    assert planner.RULE_RASTER_ONLY in NON_EXECUTABLE_RULES
