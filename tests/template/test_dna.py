"""Design DNA из пакета (contract D1): declared vs observed без «unresolved»."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from deckdna.template import autopsy
from deckdna.template import dna as builder

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pptx"
ORGANIZER = ["vk_tech.pptx", "vk_workspace.pptx", "lct2026_submission.pptx"]
UNSEEN = [
    "synthetic_unseen.pptx",
    "synthetic_unseen_sparse.pptx",
    "synthetic_unseen_dense.pptx",
    "synthetic_unseen_43.pptx",
]


def _build(name: str):
    path = FIXTURES / name
    forensics = autopsy.analyze_template(path)
    ids = {i: f"tsl_{i}" for i in range(forensics.slides)}
    return builder.build_design_dna(
        path.read_bytes(), forensics, "tpl", "ana", ids, datetime.now(UTC), "freeze-1"
    ), forensics


@pytest.fixture(scope="module")
def vk_tech():
    return _build("vk_tech.pptx")


class TestOrganizerTemplate:
    def test_nothing_unresolved(self, vk_tech):
        build, _ = vk_tech
        assert "unresolved" not in build.design_dna.model_dump_json()

    def test_layouts_have_names_types_and_placeholder_boxes(self, vk_tech):
        layouts = vk_tech[0].design_dna.declared.layouts
        assert len(layouts) == vk_tech[1].layouts
        assert all(lay.name and lay.type for lay in layouts)
        boxes = [p.bbox for lay in layouts for p in lay.placeholders if p.bbox]
        assert boxes
        for b in boxes:
            assert -0.01 <= b.x <= 1.01 and -0.01 <= b.y <= 1.01 and b.w > 0 and b.h > 0
        assert {lay.type for lay in layouts} >= {"title", "section", "closing"}

    def test_masters_own_their_layouts(self, vk_tech):
        design = vk_tech[0].design_dna
        owned = [p for m in design.declared.masters for p in m.layout_parts]
        assert sorted(owned) == sorted(lay.part for lay in design.declared.layouts)

    def test_theme_palette_is_filled(self, vk_tech):
        colors = vk_tech[0].design_dna.declared.themes[0].colors
        assert {"dk1", "lt1", "accent1"} <= set(colors)

    def test_font_scale_matches_real_frequency(self, vk_tech):
        sizes = vk_tech[0].design_dna.observed.font_sizes
        assert sizes and sizes == sorted(sizes, key=lambda s: -s.frequency)
        assert all(s.roles for s in sizes)
        assert {"title", "body", "caption"} & {r for s in sizes for r in s.roles}

    def test_fonts_carry_context(self, vk_tech):
        top = vk_tech[0].design_dna.observed.fonts[0]
        assert top.frequency > 0 and top.contexts

    def test_spacing_grid_and_backgrounds(self, vk_tech):
        obs = vk_tech[0].design_dna.observed
        assert obs.spacing.common_margins_emu and obs.spacing.alignment_lines_x
        assert obs.backgrounds and obs.backgrounds[0].kind.value == "solid"
        assert sum(b.frequency for b in obs.backgrounds) == vk_tech[1].slides

    def test_anchor_and_roles(self, vk_tech):
        design = vk_tech[0].design_dna
        assert design.anchors and all(0 < a.slide_coverage <= 1 for a in design.anchors)
        role_ids = {sid for r in design.slide_roles for sid in r.slide_ids}
        assert role_ids == {f"tsl_{i}" for i in range(vk_tech[1].slides)}
        assert all(r.evidence for r in design.slide_roles)
        assert {"title", "content"} <= {r.role for r in design.slide_roles}

    def test_exemplars_are_classified_and_clustered(self, vk_tech):
        ex = vk_tech[0].design_dna.exemplars
        assert len(ex) == vk_tech[1].slides
        assert all(e.role and e.cluster_id and e.layout_part for e in ex)
        assert 1 < len({e.cluster_id for e in ex}) < len(ex)

    def test_capacities_are_observed_not_invented(self, vk_tech):
        cap = vk_tech[0].design_dna.capacities
        assert cap.max_title_chars and cap.max_body_chars and cap.occupancy_range
        assert 0 < cap.occupancy_range.min < cap.occupancy_range.max <= 1

    def test_declared_vs_observed_conflicts_are_reported(self, vk_tech):
        conflicts = vk_tech[0].conflicts
        assert any(c.kind == "font" and "Play" in c.detail for c in conflicts)
        assert all(c.resolution for c in conflicts)
        assert set(vk_tech[0].confidence) == {
            "palette",
            "fonts",
            "sizes",
            "grid",
            "anchors",
            "layouts",
            "roles",
        }
        assert all(0 <= v <= 1 for v in vk_tech[0].confidence.values())


@pytest.mark.parametrize("name", ORGANIZER + UNSEEN)
def test_every_template_builds_without_unresolved_markers(name):
    build, forensics = _build(name)
    dump = build.design_dna.model_dump_json()
    assert "unresolved" not in dump
    assert len(build.design_dna.exemplars) == forensics.slides
    assert build.design_dna.slide_size.width_emu > 0


def test_design_dna_canonical_dump_passes_schema():
    """ADR-018: content_description=None (the common case -- no model at
    analyze time) must be OMITTED by to_schema_dict, not sent as JSON
    null -- the schema declares it "type": "string", not nullable. Same
    class of bug tests/contract/test_schema_gate.py's docstring warns
    about ("model_dump() шлёт null там, где схема ждёт отсутствие
    поля"), just for design-dna.schema.json instead of deck-plan."""
    import json

    from deckdna.contracts.serialize import to_schema_dict
    from jsonschema import validate

    schema_path = (
        Path(__file__).resolve().parents[2] / "schemas" / "design-dna.schema.json"
    )
    schema = json.loads(schema_path.read_text())

    build, _ = _build("vk_tech.pptx")
    validate(instance=to_schema_dict(build.design_dna), schema=schema)

    # and with content_description actually set on one exemplar
    build.design_dna.exemplars[0].content_description = "Карточка члена команды"
    validate(instance=to_schema_dict(build.design_dna), schema=schema)


def test_sparse_template_stays_honest():
    """Шаблон без внятной типографики: пустое остаётся пустым, не выдумывается."""
    build, _ = _build("synthetic_unseen_sparse.pptx")
    design = build.design_dna
    assert design.anchors == []
    assert design.observed.spacing.common_gaps_emu is None
    assert build.confidence["sizes"] < 0.5


def test_charts_are_reported_as_unsupported_features():
    build, forensics = _build("synthetic_unseen_sparse.pptx")
    kinds = {u.feature for u in build.design_dna.unsupported_features}
    assert forensics.charts and "chart" in kinds


def test_title_role_is_inferred_from_slide_features():
    """Роль титула выводится из признаков слайда (позиция + короткая
    подача), а не из имени файла или шаблона."""
    build, _ = _build("synthetic_unseen.pptx")
    roles = {r.role: r for r in build.design_dna.slide_roles}
    assert "title" in roles and roles["title"].slide_ids == ["tsl_0"]
    assert roles["title"].evidence
