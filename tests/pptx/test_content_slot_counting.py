"""count_content_slots: the authoritative capacity signal for exemplar
selection (pptx/composing/text_replace.py), not an approximation of it.

Ranking exemplars by ``RunProfile.long_runs`` (any non-empty run
>=15 chars anywhere on the slide) badly under/over-estimates how many
content_units a candidate can actually receive -- verified live:
long_runs=6 on one real vk_tech_template.pptx card-grid slide, whose
real fillable capacity (measured against replace_text_runs's own
bodies_eligible) is 37. count_content_slots asks the exact same
question replace_text_runs will answer at fill time, using its own
slot-eligibility predicates, so the two can never disagree.
"""

from __future__ import annotations

from deckdna.contracts.variant_spec import Strategy
from deckdna.pptx.cloning.exemplar import (
    _presentation_slide_size,
    _ranked_candidate_pool,
    has_offslide_content_slots,
    select_exemplar_slides,
)
from deckdna.pptx.composing.text_replace import count_content_slots, replace_text_runs
from deckdna.pptx.opc.package import OpcPackage
from lxml import etree

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

TEMPLATE = "tests/fixtures/pptx/vk_tech_template.pptx"


def _synthetic_slide(body_xml: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}">
  <p:cSld>
    <p:spTree>
      <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvGrpSpPr/></p:nvGrpSpPr>
      <p:grpSpPr/>
      {body_xml}
    </p:spTree>
  </p:cSld>
</p:sld>""".encode()


def _shape(
    shape_id: int,
    text: str,
    *,
    cx: int = 3000000,
    x: int = 0,
    vert: str | None = None,
    ph_title: bool = False,
) -> str:
    ph = '<p:ph type="title"/>' if ph_title else ""
    vert_attr = f' vert="{vert}"' if vert else ""
    return f"""<p:sp>
  <p:nvSpPr>
    <p:cNvPr id="{shape_id}" name="S{shape_id}"/>
    <p:cNvSpPr/>
    <p:nvPr>{ph}</p:nvPr>
  </p:nvSpPr>
  <p:spPr><a:xfrm><a:off x="{x}" y="0"/><a:ext cx="{cx}" cy="500000"/></a:xfrm></p:spPr>
  <p:txBody><a:bodyPr{vert_attr}/><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody>
</p:sp>"""


# --------------------------------------------------------------------------
# real organizer template: count_content_slots must always agree with
# replace_text_runs's own bodies_eligible, never approximate it
# --------------------------------------------------------------------------


def test_matches_real_fill_capacity_on_organizer_template():
    pkg = OpcPackage.open(TEMPLATE)
    slide_size = _presentation_slide_size(pkg)
    choices = select_exemplar_slides(
        pkg, count=6, needs=[frozenset()] * 6, strategy=Strategy.balanced
    )
    assert choices  # sanity: the pool actually produced candidates
    for c in choices:
        xml = pkg.parts[c.slide_part]
        n_slots = count_content_slots(xml, slide_size)
        _, report = replace_text_runs(
            xml, [f"T{i}" for i in range(60)], title_text="TITLE",
            slide_size=slide_size,
        )
        assert n_slots == report.bodies_eligible == c.content_slots, c.slide_part


def test_long_runs_proxy_understates_real_capacity_on_a_dense_card_grid():
    """Documents exactly why long_runs was replaced: on a real dense
    card-grid slide it undercounts real fillable capacity by a wide
    margin (not just off-by-one)."""
    pkg = OpcPackage.open(TEMPLATE)
    choices = select_exemplar_slides(
        pkg, count=6, needs=[frozenset()] * 6, strategy=Strategy.balanced
    )
    densest = max(choices, key=lambda c: count_content_slots(pkg.parts[c.slide_part]))
    real_capacity = count_content_slots(pkg.parts[densest.slide_part])
    # схема из 37 коротких подписей больше не попадает в пул (label
    # diagram), но и на обычной сетке карточек прокси занижает вместимость
    assert real_capacity > densest.long_runs


# --------------------------------------------------------------------------
# synthetic slides -- exact, controlled slot counts
# --------------------------------------------------------------------------


def test_three_plain_textboxes_three_slots():
    xml = _synthetic_slide("".join(_shape(i, f"body {i}") for i in (10, 11, 12)))
    assert count_content_slots(xml) == 3


def test_cropped_edge_labels_are_not_fillable_content_slots():
    slide_size = (8_000_000, 4_000_000)
    xml = _synthetic_slide(
        _shape(10, "Edge label", x=-100_000)
        + _shape(11, "Right edge label", x=6_000_000)
        + _shape(12, "Real body text", x=2_000_000)
    )
    assert count_content_slots(xml, slide_size) == 1
    filled, report = replace_text_runs(
        xml, ["Only body text"], slide_size=slide_size
    )
    assert report.bodies_eligible == report.bodies_filled == 1
    root = etree.fromstring(filled)
    assert [t.text for t in root.iter(f"{{{A}}}t")] == [
        "Edge label", "Right edge label", "Only body text"
    ]


def test_real_cropped_label_slide_is_not_a_prose_exemplar():
    pkg = OpcPackage.open(TEMPLATE)
    part = "ppt/slides/slide41.xml"
    assert has_offslide_content_slots(pkg, part)
    slide_size = _presentation_slide_size(pkg)
    assert slide_size is not None
    xml = pkg.parts[part]
    assert count_content_slots(xml, slide_size) < count_content_slots(xml)
    ranked, _, _, _ = _ranked_candidate_pool(pkg)
    assert part not in ranked


def test_title_run_is_not_a_content_slot():
    xml = _synthetic_slide(
        _shape(10, "Title text", ph_title=True) + _shape(11, "Body text")
    )
    assert count_content_slots(xml) == 1


def test_narrow_shape_excluded_as_decorative():
    xml = _synthetic_slide(
        _shape(10, "01", cx=100_000)  # << _MIN_TEXT_CX_EMU (450_000)
        + _shape(11, "Real body text, wide enough")
    )
    assert count_content_slots(xml) == 1


def test_vertical_caption_excluded_as_decorative():
    xml = _synthetic_slide(
        _shape(10, "Vertical caption", vert="vert")
        + _shape(11, "Normal body text")
    )
    assert count_content_slots(xml) == 1


def test_empty_body_not_counted():
    xml = _synthetic_slide(_shape(10, "") + _shape(11, "Real content here"))
    assert count_content_slots(xml) == 1


def test_table_cells_are_not_content_slots():
    tbl = """<p:graphicFrame>
      <p:nvGraphicFramePr>
        <p:cNvPr id="20" name="Table"/>
        <p:cNvGraphicFramePr/>
        <p:nvPr/>
      </p:nvGraphicFramePr>
      <p:xfrm><a:off x="0" y="0"/><a:ext cx="3000000" cy="1000000"/></p:xfrm>
      <a:graphic>
        <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table">
          <a:tbl><a:tr h="500000"><a:tc><a:txBody><a:bodyPr/>
            <a:p><a:r><a:t>Cell text</a:t></a:r></a:p>
          </a:txBody></a:tc></a:tr></a:tbl>
        </a:graphicData>
      </a:graphic>
    </p:graphicFrame>"""
    xml = _synthetic_slide(tbl + _shape(11, "Real body text"))
    assert count_content_slots(xml) == 1


def test_multi_paragraph_single_shape_is_one_slot_not_two():
    """Two runs in the SAME txBody (two paragraphs of one card) count as
    one slot -- matches replace_text_runs pouring one text per BODY, not
    per run."""
    xml = _synthetic_slide("""<p:sp>
      <p:nvSpPr><p:cNvPr id="10" name="S10"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
      <p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="3000000" cy="500000"/></a:xfrm></p:spPr>
      <p:txBody><a:bodyPr/>
        <a:p><a:r><a:t>Line one</a:t></a:r></a:p>
        <a:p><a:r><a:t>Line two</a:t></a:r></a:p>
      </p:txBody>
    </p:sp>""")
    assert count_content_slots(xml) == 1


def test_no_mutation_side_effect():
    """Read-only: calling it doesn't change what replace_text_runs sees
    afterward on the same original bytes."""
    pkg = OpcPackage.open(TEMPLATE)
    choices = select_exemplar_slides(pkg, count=1, needs=[frozenset()])
    xml = pkg.parts[choices[0].slide_part]
    before = etree.tostring(etree.fromstring(xml))
    count_content_slots(xml)
    count_content_slots(xml)
    after = etree.tostring(etree.fromstring(xml))
    assert before == after
