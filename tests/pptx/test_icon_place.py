"""Иконки шаблона встают на маркеры карточек по выбору модели."""

from __future__ import annotations

import asyncio

from deckdna.pptx.composing.icon_place import PROMPT_NAME, find_targets, place_icons
from deckdna.pptx.opc.package import OpcPackage
from deckdna.providers.mock import MockProvider
from deckdna.template.icons import IconAsset
from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches, Pt

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"


def _deck(path):
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(10), Inches(5.625)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    for i, text in enumerate(["Безопасность данных", "Рост выручки"]):
        x = Inches(0.5 + i * 4.5)
        card = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE, x, Inches(1), Inches(4), Inches(3)
        )
        card.fill.solid()
        card.fill.fore_color.rgb = RGBColor(0xEE, 0xEE, 0xEE)
        dot = slide.shapes.add_shape(
            MSO_SHAPE.OVAL, x + Inches(0.2), Inches(1.2), Inches(0.1), Inches(0.1)
        )
        dot.fill.solid()
        dot.fill.fore_color.rgb = RGBColor(0x00, 0x77, 0xFF)
        tb = slide.shapes.add_textbox(x + Inches(0.2), Inches(2), Inches(3.6), Inches(1))
        tb.text_frame.text = text
        tb.text_frame.paragraphs[0].runs[0].font.size = Pt(14)
    prs.save(path)


def _icon(n: int, label: str) -> IconAsset:
    xml = (
        f'<p:sp xmlns:p="{P}" xmlns:a="{A}"><p:nvSpPr><p:cNvPr id="{n}" name="i"/>'
        '<p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="0" y="0"/>'
        '<a:ext cx="100" cy="100"/></a:xfrm><a:custGeom><a:pathLst><a:path w="1" h="1">'
        '<a:moveTo><a:pt x="0" y="0"/></a:moveTo></a:path></a:pathLst></a:custGeom>'
        '<a:solidFill><a:srgbClr val="FF0000"/></a:solidFill></p:spPr></p:sp>'
    )
    return IconAsset(
        id=f"i{n}", part="x", slide_index=0, box=(0, 0, 100, 100), xml=xml, label=label
    )


def test_icons_replace_card_markers_in_accent_color(tmp_path):
    path = tmp_path / "deck.pptx"
    _deck(path)
    pkg = OpcPackage.open(path)
    targets = find_targets(pkg, ["ppt/slides/slide1.xml"], Inches(10), Inches(5.625))
    assert len(targets) == 1 and len(targets[0].items) == 2
    gw = MockProvider(fixtures={PROMPT_NAME: {"slides": [{"slide": 0, "icons": [1, 0]}]}})
    icons = [_icon(90, "chart growth"), _icon(91, "shield")]
    report = asyncio.run(place_icons(str(path), icons, gw))
    assert report["icons_placed"] == 2
    root = etree.fromstring(OpcPackage.open(path).parts["ppt/slides/slide1.xml"])
    custom = [el for el in root.iter(f"{{{P}}}sp") if el.find(f".//{{{A}}}custGeom") is not None]
    assert len(custom) == 2
    assert all(el.find(f".//{{{A}}}schemeClr").get("val") == "accent1" for el in custom)


def test_no_icons_when_model_has_no_fit_for_every_card(tmp_path):
    path = tmp_path / "deck.pptx"
    _deck(path)
    gw = MockProvider(fixtures={PROMPT_NAME: {"slides": [{"slide": 0, "icons": [0, None]}]}})
    report = asyncio.run(place_icons(str(path), [_icon(90, "shield")], gw))
    assert report["icons_placed"] == 0
