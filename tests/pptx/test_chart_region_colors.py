"""Chart ink follows the panel under its frame, independently of template names."""

from io import BytesIO
from zipfile import ZipFile

import pytest
from deckdna.contracts.content_pack import Chart, ChartSeries, SourceRef
from deckdna.pptx.composing.chart_colors import chart_colors, contrast
from deckdna.pptx.composing.chart_fill import fill_slide_charts
from deckdna.pptx.composing.chart_style import DonorStyle
from deckdna.pptx.opc.package import OpcPackage, parse_rels, rels_name_for
from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
SLIDE = "ppt/slides/slide1.xml"


def _package(existing_chart):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = RGBColor(255, 255, 255)
    panel = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(5), 0, Inches(5), Inches(7.5),
    )
    panel.fill.solid()
    panel.fill.fore_color.rgb = RGBColor(0, 0, 0)
    if existing_chart:
        data = CategoryChartData()
        data.categories = ["A", "B"]
        data.add_series("Donor", [1, 2])
        slide.shapes.add_chart(
            XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(5.5), Inches(1),
            Inches(4), Inches(5), data,
        )
    buf = BytesIO()
    prs.save(buf)
    with ZipFile(buf) as archive:
        return OpcPackage({name: archive.read(name) for name in archive.namelist()})


@pytest.mark.parametrize("existing_chart", [False, True])
def test_added_and_replaced_charts_use_panel_contrast(existing_chart):
    pkg = _package(existing_chart)
    chart = Chart(
        id="sample", chart_type="scatter", categories=["1", "2"],
        series=[ChartSeries(name="Value", values=[3, 5])],
        source_ref=SourceRef(artifact_id="sample.csv"),
        category_axis_title="Spend", value_axis_title="Revenue",
    )
    report = fill_slide_charts(
        pkg.parts, SLIDE, [chart], parse_rels(pkg.parts[rels_name_for(SLIDE)]), set(),
        used_hosts=set(), donor=DonorStyle(series=(("srgb", "000000"),)),
    )
    assert report.charts_filled == 1 and report.units_dropped == 0
    xml = next(v for k, v in pkg.parts.items() if k.startswith("ppt/charts/chart"))
    root = etree.fromstring(xml)
    for tx in root.iter(f"{{{C}}}txPr"):
        ink = tx.find(f".//{{{A}}}srgbClr")
        assert ink is not None and contrast(ink.get("val"), "000000") >= 4.5
    assert root.find(f".//{{{C}}}scatterChart") is not None
    # Black donor series would disappear on the local panel too.
    marker = root.find(f".//{{{C}}}marker/{{{C}}}spPr/{{{A}}}solidFill/{{{A}}}srgbClr")
    assert marker is not None and contrast(marker.get("val"), "000000") >= 1.6


def test_panel_color_is_local_and_requires_full_coverage():
    pkg = _package(False)
    assert contrast(chart_colors(pkg.parts, SLIDE).ink, "FFFFFF") >= 4.5
    dark = (int(Inches(6)), int(Inches(1)), int(Inches(3)), int(Inches(4)))
    assert contrast(chart_colors(pkg.parts, SLIDE, dark).ink, "000000") >= 4.5
    crossing = (int(Inches(4)), int(Inches(1)), int(Inches(3)), int(Inches(4)))
    assert chart_colors(pkg.parts, SLIDE, crossing) == chart_colors(pkg.parts, SLIDE)
