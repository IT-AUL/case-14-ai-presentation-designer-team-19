"""Регрессии волны 5 (LEAD-BE), сторона вёрстки:

- waterfall: ось значений начинается с нуля (``<c:min val="0"/>``) —
  срезанная база завышала вклад факторов (F-79);
- ``compose_visual_cards``/``compose_sources`` снимают мелкие значки-
  картинки донора без подписей (F-80), логотип в углу не трогают;
- числовые строки ≥5 разрядов в таблицах — с разделителями («44 862»),
  годы/артикулы в 4 разряда не трогаются.
"""

import base64
import io

from deckdna.contracts.content_pack import Chart, ChartSeries, SourceRef
from deckdna.pptx.composing.chart_build import build_typed_chart_xml
from deckdna.pptx.composing.sources_layout import compose_visual_cards
from deckdna.pptx.composing.table_fill import _cell_text
from lxml import etree
from pptx import Presentation
from pptx.util import Emu

SW, SH = 12_192_000, 6_858_000
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_waterfall_axis_starts_at_zero():
    chart = Chart(
        id="w",
        categories=["Было", "Цена", "Объём", "Стало"],
        series=[ChartSeries(name="Итог", values=[100, 30, 15, 120])],
        source_ref=SourceRef(artifact_id="d.csv"),
        chart_type="waterfall",
    )
    xml = build_typed_chart_xml(chart, None).decode()
    assert '<c:min val="0"/>' in xml


def test_bar_axis_has_no_forced_zero():
    chart = Chart(
        id="b",
        categories=["A", "B"],
        series=[ChartSeries(name="S", values=[5, 9])],
        source_ref=SourceRef(artifact_id="d.csv"),
        chart_type="bar",
    )
    assert '<c:min val="0"/>' not in build_typed_chart_xml(chart, None).decode()


def _pic(slide, x_emu, y_emu, w_emu=500_000, h_emu=500_000):
    return slide.shapes.add_picture(
        io.BytesIO(_PNG), Emu(x_emu), Emu(y_emu), Emu(w_emu), Emu(h_emu)
    )


def test_small_icons_removed_corner_logo_kept():
    prs = Presentation()
    prs.slide_width, prs.slide_height = SW, SH
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    # значок в центре контентной зоны (как у карточки эталона)
    icon = _pic(slide, int(SW * 0.45), int(SH * 0.45))
    icon.name = "Card gear icon"
    # логотип в левом верхнем углу — брендинг, остаётся
    logo = _pic(slide, int(SW * 0.01), int(SH * 0.01))
    logo.name = "Corner logo"
    out, _ = compose_visual_cards(
        slide._element.xml.encode(), "Заголовок", ["Пункт"], SW, SH
    )
    root = etree.fromstring(out)
    names = [
        pic.find(f"{P}nvPicPr/{P}cNvPr").get("name")
        for pic in root.iter(f"{P}pic")
    ]
    assert "Card gear icon" not in names
    assert "Corner logo" in names


def test_full_bleed_picture_survives():
    prs = Presentation()
    prs.slide_width, prs.slide_height = SW, SH
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    art = _pic(slide, 0, 0, SW, SH)
    art.name = "Full bleed art"
    out, _ = compose_visual_cards(
        slide._element.xml.encode(), "Заголовок", ["Пункт"], SW, SH
    )
    root = etree.fromstring(out)
    names = [
        pic.find(f"{P}nvPicPr/{P}cNvPr").get("name")
        for pic in root.iter(f"{P}pic")
    ]
    assert "Full bleed art" in names


def test_cell_text_reformats_long_numbers_not_years():
    assert _cell_text("44862") == "44\xa0862"
    assert _cell_text("2024") == "2024"
    assert _cell_text("12,5%") == "12,5%"
    assert _cell_text(44862.0) == "44\xa0862"
