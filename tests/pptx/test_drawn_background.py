"""Final native compositions retain drawn backgrounds and readable card ink."""

import io
import json
from pathlib import Path

import pytest
from deckdna.contracts.variant_spec import Strategy
from deckdna.pptx.composing.background import background_layers
from deckdna.pptx.composing.chart_colors import contrast, slide_background
from deckdna.pptx.composing.minimal import generate_deck
from deckdna.pptx.composing.table_fill import _el_box
from deckdna.pptx.opc.package import OpcPackage
from lxml import etree
from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches, Pt

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"


def _template(path, kind):
    prs = Presentation()
    for _ in range(3):
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        shapes = slide.shapes.add_group_shape().shapes if kind == "group" else slide.shapes
        if kind == "image":
            buf = io.BytesIO()
            Image.new("RGB", (20, 15), "#172C46").save(buf, format="PNG")
            shapes.add_picture(io.BytesIO(buf.getvalue()), 0, 0, prs.slide_width, prs.slide_height)
        else:
            rect = shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, prs.slide_height)
            rect.fill.solid()
            rect.fill.fore_color.rgb = RGBColor.from_string("172C46")
        for y, text in [
            (1, "Authored heading"),
            (3, "Authored content for a slide with drawn artwork"),
        ]:
            shape = slide.shapes.add_textbox(Inches(0.5), Inches(y), Inches(8), Inches(1))
            shape.text = text
            run = shape.text_frame.paragraphs[0].runs[0]
            run.font.color.rgb = RGBColor.from_string("F0F3F8")
            run.font.size = Pt(18)
    prs.save(path)
    if kind == "image":
        # A theme may assign a LIGHT color to bg2/dk2; slot names are not colors.
        pkg = OpcPackage.open(path)
        root = etree.fromstring(pkg.parts["ppt/theme/theme1.xml"])
        root.find(f".//{{{A}}}clrScheme/{{{A}}}dk2/{{{A}}}srgbClr").set("val", "EEE8DD")
        pkg.parts["ppt/theme/theme1.xml"] = etree.tostring(root)
        pkg.save(path)


@pytest.mark.parametrize("kind", ["shape", "group", "image"])
@pytest.mark.parametrize("layout", ["cards", "sources"])
def test_final_compositions_keep_full_canvas_artwork(tmp_path, kind, layout):
    template = tmp_path / "template.pptx"
    _template(template, kind)
    plan = json.loads(Path("tests/fixtures/content/poc_deck_plan.json").read_text())
    plan["slides"] = plan["slides"][:1]
    slide = plan["slides"][0]
    slide["title_intent"] = "Источники" if layout == "sources" else "Результаты"
    slide["content_units"] = [
        {"kind": "title", "role": "title", "text": slide["title_intent"]},
        {"kind": "bullet", "role": "body", "text": "Первый подтверждённый результат"},
        {"kind": "bullet", "role": "body", "text": "Второй подтверждённый результат"},
    ]
    output = tmp_path / "deck.pptx"
    generate_deck(template, plan, output, strategy=Strategy.visual)
    pkg = OpcPackage.open(output)
    part = "ppt/slides/slide1.xml"
    root = etree.fromstring(pkg.parts[part])
    deck = Presentation(output)
    assert len(background_layers(root, deck.slide_width, deck.slide_height)) == 1
    # Artwork remains first in z-order and all authored content is editable.
    first = deck.slides[0].shapes[0]
    if kind == "image":
        assert first.image.blob
    else:
        rect = first.shapes[0] if kind == "group" else first
        assert rect.fill.fore_color.rgb == RGBColor.from_string("172C46")
    composed_texts = []
    for shape in root.iter(f"{{{P}}}sp"):
        props = shape.find(f"{{{P}}}nvSpPr/{{{P}}}cNvPr")
        texts = [t.text for t in shape.iter(f"{{{A}}}t") if t.text]
        if not texts or props is None:
            continue
        composed_texts.extend(texts)
        # Compare real resolved colors, rather than assuming bg1/bg2 polarity.
        bg, _, _ = slide_background(pkg.parts, part, _el_box(shape))
        for fill in shape.findall(f".//{{{A}}}rPr/{{{A}}}solidFill/{{{A}}}srgbClr"):
            # A picture background has no single fill colour to compare with.
            if bg is not None and kind != "image":
                assert contrast(fill.get("val"), bg) >= 4.5
    assert any("Первый подтверждённый результат" in text for text in composed_texts)
