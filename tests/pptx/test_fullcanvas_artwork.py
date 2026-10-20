"""Полноэкранная текст-пустая плашка донора — фон, а не контент-слот.

QA (dark theme): ``compose_visual_cards``/``compose_sources`` удаляли
все ``p:sp`` с txBody — вместе с тёмной фоновой плашкой; сгенерированный
текст оказывался на чужом фоне и терял контраст.
"""

from deckdna.pptx.composing.sources_layout import compose_visual_cards
from lxml import etree
from pptx import Presentation
from pptx.util import Inches

SW, SH = 12_192_000, 6_858_000

P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def test_fullcanvas_textless_shape_survives_visual_cards():
    prs = Presentation()
    prs.slide_width = SW
    prs.slide_height = SH
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = slide.shapes.add_shape(
        1,  # MSO_SHAPE.RECTANGLE
        Inches(0),
        Inches(0),
        prs.slide_width,
        prs.slide_height,
    )
    bg.name = "Dark background"
    tb = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(1))
    tb.text_frame.text = "Демо-текст донора"
    out, _ = compose_visual_cards(
        slide._element.xml.encode(),
        "Заголовок",
        ["Пункт"],
        SW,
        SH,
    )
    root = etree.fromstring(out)
    names = [sp.find(f"{P}nvSpPr/{P}cNvPr").get("name") for sp in root.iter(f"{P}sp")]
    assert "Dark background" in names
    assert "Демо-текст донора" not in "".join(t.text or "" for t in root.iter(f"{A}t"))


def test_partial_panel_without_text_still_removed():
    prs = Presentation()
    prs.slide_width = SW
    prs.slide_height = SH
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    panel = slide.shapes.add_shape(1, Inches(1), Inches(1), Inches(4), Inches(3))
    panel.name = "Side panel"
    out, _ = compose_visual_cards(
        slide._element.xml.encode(),
        "Заголовок",
        ["Пункт"],
        SW,
        SH,
    )
    root = etree.fromstring(out)
    names = [sp.find(f"{P}nvSpPr/{P}cNvPr").get("name") for sp in root.iter(f"{P}sp")]
    assert "Side panel" not in names
