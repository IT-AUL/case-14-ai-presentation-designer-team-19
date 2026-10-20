"""The XML part number is not the author's slide position."""

import pytest
from deckdna.contracts.deck_plan import Brief
from deckdna.errors import DeckDNAError
from deckdna.generation.pipeline import generate
from deckdna.ingestion.content_parsers import parse_file
from deckdna.planning.story_director import plan_deck
from deckdna.pptx.composing.minimal import _local_design_dna, generate_deck
from deckdna.pptx.opc.package import OpcPackage
from lxml import etree
from pptx import Presentation


def _inputs(tmp_path):
    template = tmp_path / "reordered.pptx"
    prs = Presentation()
    for text in ["Original first slide", "Original second slide", "Credits must survive"]:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = text
        slide.placeholders[1].text = "Original body with enough text to serve as a donor."
    # Reorder sldId elements while retaining physical ZIP part names.
    ids = prs.slides._sldIdLst
    ids.insert(0, ids[-1])
    prs.save(template)
    source = tmp_path / "source.md"
    source.write_text("# Обзор\n\n## Результат\n\nКоманда подготовила обзор результатов.\n")
    brief = Brief(purpose="Обзор", audience="команда", language="ru", target_slide_count=3)
    pack = parse_file(source)
    plan = plan_deck(pack, brief)
    return template, source, brief, pack, plan


def test_protected_position_uses_presentation_relationships(tmp_path):
    template, source, _, _, plan = _inputs(tmp_path)
    original = OpcPackage.open(template)
    # Validate the fixture before python-pptx can rename parts in memory.
    assert b"Credits must survive" in original.parts["ppt/slides/slide3.xml"]
    output = tmp_path / "out.pptx"
    report = generate_deck(template, plan, output, protected_slide_indices=[0], content_path=source)
    actual = OpcPackage.open(output)
    assert actual.parts["ppt/slides/slide1.xml"] == original.parts["ppt/slides/slide3.xml"]
    assert report["slides"][0]["exemplar"]["slide_part"] == "ppt/slides/slide3.xml"


def test_reordered_protected_slide_survives_final_pipeline(tmp_path):
    template, source, brief, pack, plan = _inputs(tmp_path)
    original = OpcPackage.open(template)
    report = generate(template, source, brief, tmp_path / "out", content_pack=pack,
                      deck_plan=plan, protected_slide_indices=[0])
    actual = OpcPackage.open(report["artifacts"]["pptx"])
    assert actual.parts["ppt/slides/slide1.xml"] == original.parts["ppt/slides/slide3.xml"]


def test_ordered_parts_reject_missing_slide_relationship(tmp_path):
    template, *_ = _inputs(tmp_path)
    package = OpcPackage.open(template)
    root = etree.fromstring(package.parts["ppt/presentation.xml"])
    root.find('.//{*}sldId').set(
        '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id', 'missing'
    )
    package.parts["ppt/presentation.xml"] = etree.tostring(root)
    with pytest.raises(DeckDNAError) as exc:
        package.ordered_slide_parts()
    assert exc.value.code == "package_corrupt"


def test_dna_exemplar_identity_uses_raw_package_names(tmp_path):
    template, *_ = _inputs(tmp_path)
    design = _local_design_dna(OpcPackage.open(template))
    assert [ex.part for ex in design.exemplars] == [
        "ppt/slides/slide3.xml", "ppt/slides/slide1.xml", "ppt/slides/slide2.xml",
    ]
