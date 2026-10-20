"""Native chart data must agree with caches after arbitrary donor workbooks."""

from io import BytesIO

import openpyxl
import pytest
from deckdna.contracts.content_pack import Chart, ChartSeries, SourceRef
from deckdna.pptx.composing.chart_fill import fill_slide_charts
from deckdna.pptx.opc.package import parse_rels, rels_name_for, resolve_target
from lxml import etree
from test_legacy_chart_data_integrity import C, S, donor


def _parts(pkg):
    part = next(n for n in pkg.parts if n.startswith("ppt/charts/chart"))
    rel = next(r for r in parse_rels(pkg.parts[rels_name_for(part)]) if r.type_name == "package")
    return part, resolve_target(part, rel.target)


def _chart():
    return Chart(
        id="actual",
        categories=["A", "B"],
        series=[ChartSeries(name="Actual", values=[10, None])],
        source_ref=SourceRef(artifact_id="input.md"),
    )


def _fill(pkg):
    fill_slide_charts(
        pkg.parts, S, [_chart()], parse_rels(pkg.parts[rels_name_for(S)]), set(), used_hosts=set()
    )


@pytest.mark.parametrize("sheet_name", ["Sheet1", "Данные '2026'"])
def test_edit_data_has_no_remote_donor_cells_or_sheets(sheet_name):
    pkg = donor()
    part, xlsx = _parts(pkg)
    wb = openpyxl.load_workbook(BytesIO(pkg.parts[xlsx]))
    wb.active.title = sheet_name
    wb.active["J600"] = "DONOR 99111"
    wb.create_sheet("Unrelated")["A1"] = "DONOR 99112"
    out = BytesIO()
    wb.save(out)
    pkg.parts[xlsx] = out.getvalue()
    _fill(pkg)
    part, xlsx = _parts(pkg)
    wb = openpyxl.load_workbook(BytesIO(pkg.parts[xlsx]))
    assert len(wb.sheetnames) == 1
    values = [cell.value for ws in wb for row in ws for cell in row if cell.value is not None]
    assert set(values) == {"A", "B", "Actual", 10}


def test_nonstandard_donor_formula_ranges_resolve_to_current_source_values():
    pkg = donor()
    part, xlsx = _parts(pkg)
    root = etree.fromstring(pkg.parts[part])
    for node in root.iter(f"{{{C}}}f"):
        node.text = node.text.replace("$A$2:$A$3", "$D$12:$D$13").replace(
            "$B$2:$B$3", "$E$12:$E$13"
        )
    pkg.parts[part] = etree.tostring(root)
    _fill(pkg)
    part, xlsx = _parts(pkg)
    root = etree.fromstring(pkg.parts[part])
    wb = openpyxl.load_workbook(BytesIO(pkg.parts[xlsx]))
    series = root.find(f".//{{{C}}}ser")
    for field, expected in [("cat", ["A", "B"]), ("val", [10, None])]:
        formula = series.find(f"{{{C}}}{field}").find(f".//{{{C}}}f").text
        sheet, area = formula.rsplit("!", 1)
        ws = wb[sheet.strip("'").replace("''", "'")]
        assert [cell.value for row in ws[area] for cell in row] == expected
