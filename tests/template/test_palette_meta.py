"""Recognize actual palette swatches without excluding technical source code."""

from pathlib import Path

from deckdna.pptx.opc.package import OpcPackage
from deckdna.template.meta import is_meta_xml, meta_slides
from lxml import etree

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"


def _slide(labels):
    root = etree.Element(f"{{{P}}}sld")
    tree = etree.SubElement(etree.SubElement(root, f"{{{P}}}cSld"), f"{{{P}}}spTree")
    for i, label in enumerate(labels):
        sh = etree.SubElement(tree, f"{{{P}}}sp")
        props = etree.SubElement(sh, f"{{{P}}}spPr")
        etree.SubElement(
            etree.SubElement(props, f"{{{A}}}solidFill"),
            f"{{{A}}}schemeClr",
            val=f"accent{i % 6 + 1}",
        )
        text = etree.SubElement(etree.SubElement(sh, f"{{{P}}}txBody"), f"{{{A}}}p")
        etree.SubElement(etree.SubElement(text, f"{{{A}}}r"), f"{{{A}}}t").text = label
    return etree.tostring(root)


def test_filled_hex_swatch_grid_is_template_metadata():
    assert is_meta_xml(_slide(["HEX #112233", "HEX #445566", "Hex #778899", "HEX # AABBCC"]))


def test_css_code_with_color_literals_remains_content():
    assert not is_meta_xml(
        _slide(
            [
                "body { color: #112233; background: #445566; }",
                ".card { color: #778899; border-color: #AABBCC; }",
            ]
        )
    )


def test_two_hex_labels_are_not_enough_to_classify_a_palette():
    assert not is_meta_xml(_slide(["HEX #112233", "HEX #445566"]))


def test_reported_palette_reproduction_is_filtered():
    path = Path(__file__).parents[1] / "fixtures/pptx/lct2026_submission.pptx"
    pkg = OpcPackage.open(path)
    assert "ppt/slides/slide5.xml" in meta_slides(pkg, ["ppt/slides/slide5.xml"])
