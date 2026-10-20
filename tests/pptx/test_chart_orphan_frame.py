"""A donor chart whose frame was cleared by a custom layout is not a host."""

from io import BytesIO
from zipfile import ZipFile

from deckdna.contracts.content_pack import Chart, ChartSeries, SourceRef
from deckdna.pptx.composing.chart_fill import fill_slide_charts
from deckdna.pptx.opc.package import OpcPackage, parse_rels, rels_name_for
from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches

S = "ppt/slides/slide1.xml"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _package_with_orphan_chart_rel() -> OpcPackage:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(10), Inches(6)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    data = CategoryChartData()
    data.categories = ["X", "Y"]
    data.add_series("Demo", (42, 32))
    frame = slide.shapes.add_chart(
        XL_CHART_TYPE.PIE, Inches(1), Inches(1), Inches(4), Inches(4), data
    )
    slide.shapes._spTree.remove(frame._element)
    stream = BytesIO()
    prs.save(stream)
    with ZipFile(stream) as z:
        return OpcPackage({n: z.read(n) for n in z.namelist()})


def test_orphan_chart_rel_gets_a_visible_native_chart():
    pkg = _package_with_orphan_chart_rel()
    assert not etree.fromstring(pkg.parts[S]).findall(f".//{{{P}}}graphicFrame")
    chart = Chart(
        id="revenue",
        chart_type="line",
        categories=["янв", "фев", "мар"],
        series=[ChartSeries(name="Выручка", values=[11.4, 12.0, 13.1])],
        source_ref=SourceRef(artifact_id="sales.csv"),
    )
    rels = parse_rels(pkg.parts[rels_name_for(S)])
    report = fill_slide_charts(pkg.parts, S, [chart], rels, set(), used_hosts=set())

    assert report.charts_found == 0
    assert report.charts_created == 1 and report.units_dropped == 0
    root = etree.fromstring(pkg.parts[S])
    frames = root.findall(f".//{{{P}}}graphicFrame")
    assert len(frames) == 1
    rid = frames[0].find(f".//{{{C}}}chart").get(f"{{{R}}}id")
    rels = parse_rels(pkg.parts[rels_name_for(S)])
    assert any(rel.id == rid for rel in rels)


def test_orphan_chart_rel_without_host_is_dropped_not_reported_filled():
    pkg = _package_with_orphan_chart_rel()
    chart = Chart(
        id="revenue",
        chart_type="line",
        categories=["янв", "фев"],
        series=[ChartSeries(name="Выручка", values=[1.0, 2.0])],
        source_ref=SourceRef(artifact_id="sales.csv"),
    )
    rels = parse_rels(pkg.parts[rels_name_for(S)])
    report = fill_slide_charts(pkg.parts, S, [chart], rels, set())

    assert report.charts_filled == 0
    assert report.units_dropped == 1
