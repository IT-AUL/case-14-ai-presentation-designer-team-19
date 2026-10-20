"""Legacy donor caches and labels cannot contribute facts to a new chart."""

from io import BytesIO
from zipfile import ZipFile

import pytest
from deckdna.contracts.content_pack import Chart, ChartSeries, SourceRef
from deckdna.pptx.composing.chart_fill import fill_slide_charts
from deckdna.pptx.opc.package import OpcPackage, parse_rels, rels_name_for
from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches

C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
S = "ppt/slides/slide1.xml"


def donor():
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    data = CategoryChartData()
    data.categories = ["Donor A", "Donor B"]
    data.add_series("Donor revenue", [9001, 9002])
    data.add_series("Donor margin", [99998, 99999])
    chart = slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(1), Inches(8), Inches(4), data
    ).chart
    chart.has_title = True
    chart.chart_title.text_frame.text = "Donor claim 99999"
    for axis in [chart.category_axis, chart.value_axis]:
        axis.has_title = True
        axis.axis_title.text_frame.text = "Donor axis"
    stream = BytesIO()
    prs.save(stream)
    with ZipFile(stream) as z:
        return OpcPackage({n: z.read(n) for n in z.namelist()})


@pytest.mark.parametrize("name", [None, "Actual revenue"])
def test_unused_donor_series_and_labels_disappear(name):
    pkg = donor()
    chart = Chart(
        id="new",
        categories=["A", "B"],
        series=[ChartSeries(name=name, values=[10, 20])],
        source_ref=SourceRef(artifact_id="data.md"),
    )
    report = fill_slide_charts(
        pkg.parts, S, [chart], parse_rels(pkg.parts[rels_name_for(S)]), set(), used_hosts=set()
    )
    xml = next(v for n, v in pkg.parts.items() if n.startswith("ppt/charts/chart"))
    root = etree.fromstring(xml)
    assert len(root.findall(f".//{{{C}}}ser")) == 1
    assert "Donor" not in "".join(root.itertext())
    assert not any(v.text in ["9001", "9002", "99998", "99999"] for v in root.iter(f"{{{C}}}v"))
    assert report.series_dropped == 0


def test_missing_legacy_values_are_gaps_not_invalid_numeric_text():
    pkg = donor()
    chart = Chart(
        id="gap",
        categories=["A", "B"],
        series=[ChartSeries(name="Actual", values=[10, None])],
        source_ref=SourceRef(artifact_id="data.md"),
    )
    fill_slide_charts(
        pkg.parts, S, [chart], parse_rels(pkg.parts[rels_name_for(S)]), set(), used_hosts=set()
    )
    raw = next(v for n, v in pkg.parts.items() if n.startswith("ppt/charts/chart"))
    root = etree.fromstring(raw)
    pts = root.findall(f".//{{{C}}}val/{{{C}}}numRef/{{{C}}}numCache/{{{C}}}pt")
    assert [(p.get("idx"), p.findtext(f"{{{C}}}v")) for p in pts] == [("0", "10")]
    assert b">None<" not in raw
    assert root.find(f"{{{C}}}chart/{{{C}}}dispBlanksAs").get("val") == "gap"


def test_explicit_chart_title_replaces_donor_claim():
    pkg = donor()
    chart = Chart(
        id="title",
        title="Actual chart title",
        categories=["A", "B"],
        series=[ChartSeries(name="Actual", values=[10, 20])],
        source_ref=SourceRef(artifact_id="data.md"),
    )
    fill_slide_charts(
        pkg.parts, S, [chart], parse_rels(pkg.parts[rels_name_for(S)]), set(), used_hosts=set()
    )
    raw = next(v for n, v in pkg.parts.items() if n.startswith("ppt/charts/chart"))
    root = etree.fromstring(raw)
    assert root.find(f"{{{C}}}chart/{{{C}}}title") is not None
    assert "Actual chart title" in "".join(root.itertext())
    assert "Donor" not in "".join(root.itertext())
