"""Pre-attentive focus и читаемость нативных графиков:

- одиночный bar-ряд: столбец-лидер — акцент, остальные приглушены
  (``c:dPt``), сводная категория «Прочие/Остальные» — нейтральная;
- pie/doughnut: «Прочие» — приглушённый сектор;
- scatter: ``c:trendline`` только при |r| ≥ 0.5;
- line с пропусками: маркеры точек остаются даже при >12 категориях;
- цвет серий фильтруется по контрасту с фоном слайда.
"""

from __future__ import annotations

from deckdna.contracts.content_pack import Chart, ChartSeries, SourceRef
from deckdna.pptx.composing.chart_build import build_typed_chart_xml
from deckdna.pptx.composing.chart_colors import ChartColors

COLORS = ChartColors(
    ink="FFFFFF", grid="444444", series=("0B74FF", "FF5A00"), muted="3E5F86"
)


def _chart(kind, categories, series):
    return Chart(
        id="ch",
        categories=list(categories),
        series=[ChartSeries(name=n, values=list(v)) for n, v in series],
        source_ref=SourceRef(artifact_id="d.csv"),
        chart_type=kind,
    )


def _xml(chart) -> str:
    return build_typed_chart_xml(chart, None, colors=COLORS).decode()


def test_bar_leader_is_accented_others_muted():
    chart = _chart(
        "bar",
        ["A", "B", "C", "D"],
        [("Продажи", [10, 90, 30, 20])],
    )
    xml = _xml(chart)
    assert xml.count("<c:dPt>") == 4
    # лидер (индекс 1) — акцентная заливка, остальные — приглушённые
    leader = xml.split('<c:idx val="1"/>')[1][:400]
    assert 'srgbClr val="0B74FF"' in leader
    third = xml.split('<c:idx val="2"/>')[1][:400]
    assert 'srgbClr val="3E5F86"' in third


def test_bar_without_focus_colors_when_no_muted():
    chart = _chart("bar", ["A", "B"], [("S", [5, 9])])
    xml = _xml(chart)  # muted задан в COLORS — dPt ожидаем
    assert "<c:dPt>" in xml
    plain = ChartColors(ink="111111", series=("0B74FF",))
    xml_plain = build_typed_chart_xml(chart, None, colors=plain).decode()
    # без палитры фоллбэк — schemeClr-приглушение, dPt всё равно есть
    assert "<c:dPt>" in xml_plain


def test_others_category_is_muted_in_bar():
    chart = _chart(
        "bar",
        ["A", "B", "Прочие (7)"],
        [("S", [50, 40, 10])],
    )
    xml = _xml(chart)
    tail = xml.split('<c:idx val="2"/>')[1][:400]
    assert 'srgbClr val="3E5F86"' in tail


def test_pie_others_sector_muted():
    chart = _chart(
        "pie",
        ["A", "B", "C", "Прочие"],
        [("Доля", [40, 30, 20, 10])],
    )
    xml = _xml(chart)
    idx3 = xml.split('<c:idx val="3"/>')[1][:400]
    assert 'srgbClr val="3E5F86"' in idx3


def test_scatter_trendline_only_for_strong_correlation():
    # scatter: X — категории (числа строкой), Y — единственная серия
    strong = _chart(
        "scatter",
        ["1", "2", "3", "4", "5", "6", "7", "8"],
        [("y", [2.1, 4.0, 5.9, 8.2, 10.1, 11.9, 14.2, 16.0])],
    )
    assert "<c:trendline>" in _xml(strong)
    weak = _chart(
        "scatter",
        ["1", "2", "3", "4", "5", "6", "7", "8"],
        [("y", [5, 1, 8, 2, 7, 3, 6, 4])],
    )
    assert "<c:trendline>" not in _xml(weak)


def test_line_with_gaps_keeps_markers():
    cats = [f"2025-{m:02d}" for m in range(1, 15)]
    gaps = [10, None, 12, 13, None, 15, 16, 17, None, 19, 20, 21, 22, 23]
    xml = _xml(_chart("line", cats, [("S", gaps)]))
    assert '<c:symbol val="circle"/>' in xml
    # без пропусков и при >12 точках маркеры не рисуются — линия чистая
    dense = _xml(_chart("line", cats, [("S", list(range(14)))]))
    assert '<c:symbol val="none"/>' in dense


def test_series_colors_filtered_by_contrast():
    """Бледные акценты темы на белом фоне не должны попасть в серии."""
    from io import BytesIO
    from zipfile import ZipFile

    from deckdna.pptx.composing.chart_colors import chart_colors
    from lxml import etree
    from pptx import Presentation
    from pptx.dml.color import RGBColor

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = RGBColor.from_string("FFFFFF")
    stream = BytesIO()
    prs.save(stream)
    from deckdna.pptx.opc.package import OpcPackage

    with ZipFile(stream) as z:
        pkg = OpcPackage({n: z.read(n) for n in z.namelist()})
    A = "http://schemas.openxmlformats.org/drawingml/2006/main"
    theme_name = next(
        n for n in pkg.parts if n.startswith("ppt/theme/") and n.endswith(".xml")
    )
    theme = etree.fromstring(pkg.parts[theme_name])
    # accent1 делаем почти белым — он обязан отфильтроваться из серий
    accent1 = theme.find(f".//{{{A}}}accent1")
    for child in list(accent1):
        accent1.remove(child)
    etree.SubElement(accent1, f"{{{A}}}srgbClr").set("val", "F8F8F8")
    pkg.parts[theme_name] = etree.tostring(theme, xml_declaration=True)

    colors = chart_colors(pkg.parts, "ppt/slides/slide1.xml")
    assert colors.ink and colors.ink != "FFFFFF"
    assert "F8F8F8" not in (colors.series or ())
    if colors.series:
        assert colors.muted and colors.muted != colors.series[0]
