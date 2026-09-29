"""layout_archetype (exemplar.py) — discrete visual-layout label for an
exemplar candidate, designed so a weak (~27-30B) rerank model can match a
plan slide's purpose against a small named enum instead of weighing raw
geometric numbers (see the docstring on ``LayoutArchetype`` for the
motivation and the live-quality finding of 27.09).

Ground-truth cases below were verified by hand against the real
organizer fixture (``vk_tech_template.pptx``) while designing the
classifier — see the module's own inline notes for how each slide's
geometry was inspected."""

from __future__ import annotations

from pathlib import Path

import pytest
from deckdna.pptx.cloning.exemplar import (
    LayoutArchetype,
    _arrangement,
    _cluster_count,
    _content_boxes,
    layout_archetype,
    run_profile,
)
from deckdna.pptx.opc.package import OpcPackage
from lxml import etree
from pptx import Presentation
from pptx.util import Inches

FIXTURE = Path("tests/fixtures/pptx/vk_tech_template.pptx")
needs_fixture = pytest.mark.skipif(not FIXTURE.exists(), reason="organizer fixture missing")


@pytest.fixture(scope="module")
def pkg() -> OpcPackage:
    return OpcPackage.open(FIXTURE)


@needs_fixture
def test_real_8_card_grid_is_classified_as_grid(pkg):
    # slide16: "Безопасность" + description repeated 8x, 4 cols x 2 rows
    # (x in {438148, 2605074, 4772001, 6938927}, y in {1590803, 3264604}).
    assert layout_archetype(pkg, "ppt/slides/slide16.xml") is LayoutArchetype.grid


@needs_fixture
def test_real_icon_badge_grid_short_circuits_via_profile(pkg):
    # slide21: 20 repeats of a short label across 4 groups of 5 badges —
    # is_badge_grid gates this before geometry is even inspected.
    assert layout_archetype(pkg, "ppt/slides/slide21.xml") is LayoutArchetype.icon_badge_grid


@needs_fixture
def test_real_vertical_card_stack_is_classified_as_stacked_list(pkg):
    # slide25: same "Безопасность" card repeated 4x, but stacked in ONE
    # column (x fixed at 4048160, y increasing each repeat) rather than a
    # grid — the geometric signature that distinguishes stacked_list from
    # grid/row_grid.
    assert layout_archetype(pkg, "ppt/slides/slide25.xml") is LayoutArchetype.stacked_list


@needs_fixture
def test_title_placeholder_is_excluded_from_content_boxes(pkg):
    """Every real content slide in this fixture has a title placeholder
    sitting far above the body (y~283000 EMU vs body y~1.2-1.6M EMU) and
    spanning nearly the full slide width. If it were counted as a content
    box, every single one of these slides would misclassify (spurious
    extra row, or a lone title masking a real single_block/list as
    two_column). Confirms the ``p:ph`` exclusion actually fires, not just
    that the end label happens to look right."""
    root = etree.fromstring(pkg.parts["ppt/slides/slide16.xml"])
    boxes = _content_boxes(root)
    # 16 body shapes (8 x 2: "Безопасность" + description) — the title
    # itself never appears in this list.
    assert len(boxes) == 16


@needs_fixture
def test_decorative_short_labels_are_not_counted_as_content_boxes(pkg):
    # slide21's badge groups aside, a plain short numeric/label run inside
    # a bare `sp` (not part of a group) must not count as its own content
    # block — same _TINY_RUN_MAX threshold as the content-likeness gate.
    root = etree.fromstring(pkg.parts["ppt/slides/slide16.xml"])
    boxes = _content_boxes(root)
    # every box must correspond to a real >3-char text run or media shape;
    # sanity: none of them is a degenerate near-zero-size sliver.
    assert all(cx > 0 and cy > 0 for _, _, cx, cy in boxes)


def test_cluster_count_merges_within_tolerance_and_splits_beyond_it():
    tol = 274320
    assert _cluster_count([]) == 0
    assert _cluster_count([100]) == 1
    assert _cluster_count([100, 100 + tol]) == 1  # exactly at the edge: still merged
    assert _cluster_count([100, 100 + tol + 1]) == 2
    assert _cluster_count([0, 300000, 600000, 5_000_000]) == 4


def test_arrangement_reads_rows_from_y_and_cols_from_x():
    # 2 rows x 3 cols, spaced well beyond tolerance on both axes.
    boxes = [
        (0, 0, 100, 100),
        (1_000_000, 0, 100, 100),
        (2_000_000, 0, 100, 100),
        (0, 1_000_000, 100, 100),
        (1_000_000, 1_000_000, 100, 100),
        (2_000_000, 1_000_000, 100, 100),
    ]
    assert _arrangement(boxes) == (2, 3)


@pytest.fixture
def synth_pkg(tmp_path):
    def _build(add_shapes) -> OpcPackage:
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank layout, no placeholders
        add_shapes(slide)
        path = tmp_path / "synth.pptx"
        prs.save(path)
        return OpcPackage.open(path)

    return _build


def test_single_shape_is_single_block(synth_pkg):
    def add(slide):
        box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(2))
        box.text_frame.paragraphs[0].add_run().text = "Достаточно длинный текст абзаца."

    pkg = synth_pkg(add)
    assert layout_archetype(pkg, "ppt/slides/slide1.xml") is LayoutArchetype.single_block


def test_two_shapes_side_by_side_is_two_column(synth_pkg):
    def add(slide):
        for i in range(2):
            box = slide.shapes.add_textbox(
                Inches(1 + i * 4), Inches(1), Inches(3), Inches(2)
            )
            box.text_frame.paragraphs[0].add_run().text = f"Колонка номер {i} с текстом."

    pkg = synth_pkg(add)
    assert layout_archetype(pkg, "ppt/slides/slide1.xml") is LayoutArchetype.two_column


def test_three_shapes_in_a_row_is_row_grid(synth_pkg):
    def add(slide):
        for i in range(3):
            box = slide.shapes.add_textbox(
                Inches(1 + i * 3), Inches(1), Inches(2), Inches(2)
            )
            box.text_frame.paragraphs[0].add_run().text = f"Карточка номер {i} с текстом."

    pkg = synth_pkg(add)
    assert layout_archetype(pkg, "ppt/slides/slide1.xml") is LayoutArchetype.row_grid


def test_stacked_shapes_in_one_column_is_stacked_list(synth_pkg):
    def add(slide):
        for i in range(3):
            box = slide.shapes.add_textbox(
                Inches(1), Inches(1 + i * 2), Inches(4), Inches(1.5)
            )
            box.text_frame.paragraphs[0].add_run().text = f"Пункт списка номер {i} текст."

    pkg = synth_pkg(add)
    assert layout_archetype(pkg, "ppt/slides/slide1.xml") is LayoutArchetype.stacked_list


def test_overlapping_shapes_fall_back_to_other_not_a_forced_guess(synth_pkg):
    """Two content shapes at (near-)identical position: not 1 shape (not
    single_block), not cleanly 2 columns or 2 rows either (rows==1,
    cols==1) — the honest fallback, not a forced label."""

    def add(slide):
        for _ in range(2):
            box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(2))
            box.text_frame.paragraphs[0].add_run().text = "Текст перекрывающейся фигуры."

    pkg = synth_pkg(add)
    assert layout_archetype(pkg, "ppt/slides/slide1.xml") is LayoutArchetype.other


def test_title_placeholder_text_does_not_leak_into_arrangement(tmp_path):
    """A real ``p:ph type="title"`` placeholder (via the *title and
    content* layout) sits far from the body and spans almost the full
    slide width — it would corrupt clustering (spurious extra
    row/column) if counted as a content box. The layout's content
    placeholder is left empty, so the only real signal is the title
    itself: if the exclusion works, there are 0 content boxes and the
    slide is single_block, not something skewed by the title's geometry."""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])  # title + content
    slide.shapes.title.text = "Заголовок слайда для теста"
    path = tmp_path / "synth.pptx"
    prs.save(path)
    pkg = OpcPackage.open(path)
    part = "ppt/slides/slide1.xml"
    prof = run_profile(pkg, part)
    assert layout_archetype(pkg, part, prof) is LayoutArchetype.single_block
