"""Legacy chart cache fills must retain labels supplied in the Chart contract."""

import pytest
from deckdna.audit.basic import audit_deck
from deckdna.contracts.content_pack import Chart, ChartSeries, SourceRef
from deckdna.pptx.composing.chart_colors import contrast
from deckdna.pptx.composing.chart_fill import fill_slide_charts
from deckdna.pptx.opc.package import parse_rels, rels_name_for
from lxml import etree
from test_chart_region_colors import SLIDE, A, C, _package


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("explicit", [False, True])
def test_untyped_chart_keeps_readable_axis_metadata(tmp_path, existing, explicit):
    pkg = _package(existing)
    chart = Chart(
        id="sales",
        categories=["2023", "2024"],
        series=[ChartSeries(name="Выручка", values=[10, 20])],
        category_axis_title="Год" if explicit else None,
        value_axis_title="Выручка, млн ₽" if explicit else None,
        source_ref=SourceRef(artifact_id="data.md"),
    )
    report = fill_slide_charts(
        pkg.parts,
        SLIDE,
        [chart],
        parse_rels(pkg.parts[rels_name_for(SLIDE)]),
        set(),
        used_hosts=set(),
    )
    assert report.charts_filled == 1
    path = tmp_path / "chart.pptx"
    pkg.save(path)
    assert not any(i.rule_code == "chart.metadata" for i in audit_deck(path))
    raw = next(data for name, data in pkg.parts.items() if name.startswith("ppt/charts/chart"))
    root = etree.fromstring(raw)
    titles = root.findall(f".//{{{C}}}catAx/{{{C}}}title") + root.findall(
        f".//{{{C}}}valAx/{{{C}}}title"
    )
    assert len(titles) == 2
    labels = ["".join(n.itertext()) for n in titles]
    if explicit:
        assert "Год" in labels and "Выручка, млн ₽" in labels
    for n in titles:
        ink = n.find(f".//{{{A}}}srgbClr")
        assert ink is not None and contrast(ink.get("val"), "000000") >= 4.5


def test_axis_names_replace_donor_copy_and_remain_idempotent():
    from deckdna.pptx.composing.chart_metadata import complete_chart_metadata

    xml = (
        f'<c:chartSpace xmlns:c="{C}" xmlns:a="{A}"><c:chart><c:plotArea>'
        "<c:catAx><c:title><c:tx><c:rich><a:p><a:r><a:t>Donor category</a:t>"
        "</a:r></a:p></c:rich></c:tx></c:title></c:catAx>"
        "<c:valAx><c:title><c:tx><c:rich><a:p><a:r><a:t>Donor value</a:t>"
        "</a:r></a:p></c:rich></c:tx></c:title></c:valAx>"
        "</c:plotArea></c:chart></c:chartSpace>"
    ).encode()
    base = Chart(
        id="x",
        categories=["A"],
        series=[ChartSeries(values=[1])],
        source_ref=SourceRef(artifact_id="x"),
    )
    neutral = complete_chart_metadata(xml, base, "FFFFFF")
    assert b"Donor" not in neutral
    assert complete_chart_metadata(neutral, base, "FFFFFF") == neutral
    explicit = base.model_copy(
        update={"category_axis_title": "Регион", "value_axis_title": "Продажи, млн ₽"}
    )
    changed = complete_chart_metadata(xml, explicit, "FFFFFF")
    assert "Регион" in changed.decode() and "Продажи, млн ₽" in changed.decode()
    assert b"Donor" not in changed
    assert complete_chart_metadata(changed, explicit, "FFFFFF") == changed


def test_axis_ink_follows_explicit_chart_background_not_only_slide_panel():
    pkg = _package(True)
    part = next(n for n in pkg.parts if n.startswith("ppt/charts/chart"))
    root = etree.fromstring(pkg.parts[part])
    props = root.find(f"{{{C}}}spPr")
    if props is None:
        props = etree.SubElement(root, f"{{{C}}}spPr")
    for fill in list(props):
        if etree.QName(fill).localname.endswith("Fill"):
            props.remove(fill)
    etree.SubElement(etree.SubElement(props, f"{{{A}}}solidFill"), f"{{{A}}}srgbClr", val="FFFFFF")
    pkg.parts[part] = etree.tostring(root)
    chart = Chart(
        id="x",
        categories=["A", "B"],
        series=[ChartSeries(name="Actual", values=[1, 2])],
        source_ref=SourceRef(artifact_id="data.md"),
    )
    fill_slide_charts(
        pkg.parts,
        SLIDE,
        [chart],
        parse_rels(pkg.parts[rels_name_for(SLIDE)]),
        set(),
        used_hosts=set(),
    )
    root = etree.fromstring(pkg.parts[part])
    for title in root.findall(f".//{{{C}}}catAx/{{{C}}}title"):
        ink = title.find(f".//{{{A}}}srgbClr")
        assert ink is not None and contrast(ink.get("val"), "FFFFFF") >= 4.5
