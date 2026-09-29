"""Table/chart контент: честная видимость, не молчаливая потеря.

Два инварианта:
1. Стоковый текст ячеек a:tbl в exemplar-слайде очищается — иначе он
   утечёт в вывод (та же дыра, что у card-бодий до slot-clear).
2. Нетекстовые units плана (table/chart/image/...) считаются и видны в
   compose_report.dropped_units + warnings — не проглатываются.
"""

import copy
import json
from pathlib import Path

import pytest
from deckdna.pptx.composing.minimal import generate_deck
from deckdna.pptx.composing.table_fill import fill_native_tables, remove_empty_table_frames
from deckdna.pptx.composing.text_replace import replace_text_runs
from deckdna.pptx.opc.package import OpcPackage
from lxml import etree

VK_WORKSPACE = Path("tests/fixtures/pptx/vk_workspace.pptx")
VK_TEMPLATE = Path("tests/fixtures/pptx/vk_tech_template.pptx")
DECK_PLAN = Path("tests/fixtures/content/poc_deck_plan.json")
A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def test_table_cells_cleared_not_leaked(tmp_path):
    """vk_workspace slide14 содержит a:tbl со стоковым 'Заголовок/Текст' —
    после заливки ячейки пусты, stock не выживает."""
    if not VK_WORKSPACE.exists():
        pytest.skip("vk_workspace fixture not present")
    pkg = OpcPackage.open(VK_WORKSPACE)
    slide_xml = pkg.parts["ppt/slides/slide14.xml"]
    xml, rep = replace_text_runs(slide_xml, ["новый текст"], title_text="Титул")
    root = etree.fromstring(xml)
    cell_texts = {
        (t.text or "").strip()
        for t in root.iter(f"{{{A}}}t")
        if any(etree.QName(a).localname == "tbl" for a in t.iterancestors())
    }
    assert cell_texts == {""}
    assert rep.table_cells_cleared >= 10
    # сам a:tbl на месте — геометрия таблицы не тронута
    assert b"<a:tbl>" in xml


def test_cleared_donor_table_removed_but_filled_table_kept():
    """An exemplar table without a table unit must not leave an empty grid."""
    if not VK_WORKSPACE.exists():
        pytest.skip("vk_workspace fixture not present")
    xml = OpcPackage.open(VK_WORKSPACE).parts["ppt/slides/slide14.xml"]
    cleared, _ = replace_text_runs(xml, [], title_text="Спасибо за внимание")
    cleaned, removed = remove_empty_table_frames(cleared)
    assert removed == 1
    assert b"<a:tbl>" not in cleaned
    assert "Спасибо за внимание" in cleaned.decode()

    filled, report = fill_native_tables(cleared, [_mk_table()])
    assert report.tables_filled == 1
    kept, removed = remove_empty_table_frames(filled)
    assert removed == 0
    assert kept == filled
    assert b"<a:tbl>" in kept


def test_non_text_units_reported_dropped(tmp_path):
    """План с table/chart-unit → compose_report.warnings + dropped_units,
    а не молчаливая потеря."""
    if not VK_TEMPLATE.exists():
        pytest.skip("organizer fixture not present")
    plan = json.loads(DECK_PLAN.read_text(encoding="utf-8"))
    slide = copy.deepcopy(plan["slides"][0])
    slide["content_units"].extend(
        [
            {"role": "body", "kind": "table", "table_ref": "tbl-1"},
            {"role": "body", "kind": "chart", "chart_spec": {}},
            {"role": "body", "kind": "image", "asset_ref": "img-1"},
        ]
    )
    plan["slides"] = [slide]
    report = generate_deck(VK_TEMPLATE, plan, tmp_path / "out.pptx")
    dropped = report["dropped_units"]
    # chart/image/table are structural kinds this exemplar has no slot for
    # at all — always dropped, deterministically. text_unplaced (bullets
    # that lost their slot once exemplar selection also had to weigh the
    # new table/chart/image needs) is a budget-fit side effect of *which*
    # exemplar gets picked, not what this test exercises.
    assert dropped["chart"] == 1
    assert dropped["image"] == 1
    assert dropped["table"] == 1
    assert any("table" in w for w in report["warnings"])


# ---------- structured fill: Table -> a:tbl ----------


def _mk_table(
    headers=("Метрика", "Значение"),
    rows=(("Рост", 12.0), ("Доля", 0.5)),
    tid="tbl-1",
):
    from deckdna.contracts.content_pack import SourceRef, Table

    return Table(
        id=tid,
        headers=list(headers),
        rows=[list(r) for r in rows],
        source_ref=SourceRef(artifact_id="t.md"),
    )


def _cell_grid(xml: bytes) -> list[list[str]]:
    root = etree.fromstring(xml)
    grid = next(root.iter(f"{{{A}}}tbl"))
    return [
        ["".join((t.text or "") for t in tc.iter(f"{{{A}}}t")) for tc in tr.findall(f"{{{A}}}tc")]
        for tr in grid.findall(f"{{{A}}}tr")
    ]


def test_fill_native_table_into_exemplar_grid():
    """vk_workspace slide14: реальная a:tbl (11x2) получает данные из
    ContentPack.Table — шапка в первую строку, rows дальше; пустые строки
    донора сверх данных удаляются (раньше оставались 6 пустых строк)."""
    if not VK_WORKSPACE.exists():
        pytest.skip("vk_workspace fixture not present")
    pkg = OpcPackage.open(VK_WORKSPACE)
    xml = pkg.parts["ppt/slides/slide14.xml"]
    xml, rep = fill_native_tables(xml, [_mk_table(rows=[(f"r{i}", i) for i in range(4)])])
    grid = _cell_grid(xml)
    assert len(grid) == 5 and all(len(r) == 2 for r in grid)
    assert grid[0] == ["Метрика", "Значение"]
    assert grid[1] == ["r0", "0"] and grid[4] == ["r3", "3"]
    assert rep.tables_filled == 1 and rep.units_dropped == 0


def test_fill_native_table_grows_extra_rows():
    """Данных больше, чем строк сетки — последняя a:tr клонируется,
    ВСЕ строки реально попадают в вывод, сетка выросла, не урезана."""
    if not VK_WORKSPACE.exists():
        pytest.skip("vk_workspace fixture not present")
    pkg = OpcPackage.open(VK_WORKSPACE)
    xml = pkg.parts["ppt/slides/slide14.xml"]
    xml, rep = fill_native_tables(
        xml, [_mk_table(rows=[(f"r{i}", i) for i in range(20)])]
    )
    grid = _cell_grid(xml)
    assert rep.rows_grown == 10  # 21 строка данных при 11 a:tr
    assert len(grid) == 21
    assert grid[0] == ["Метрика", "Значение"]
    assert grid[-1] == ["r19", "19"]  # последняя строка данных дошла


def test_grown_table_visible_to_audit():
    """Рост сетки синкает высоту фрейма — таблица, вылезшая за слайд,
    флагается layout.out_of_bounds, а не проходит молча."""
    if not VK_WORKSPACE.exists():
        pytest.skip("vk_workspace fixture not present")
    pkg = OpcPackage.open(VK_WORKSPACE)
    xml = pkg.parts["ppt/slides/slide14.xml"]
    before_h = _frame_cy(xml)
    xml, rep = fill_native_tables(
        xml, [_mk_table(rows=[(f"r{i}", i) for i in range(30)])]
    )
    after_h = _frame_cy(xml)
    assert rep.rows_grown == 20 and after_h > before_h


def _frame_cy(xml: bytes) -> int:
    """Declared height (cy) of the graphicFrame owning the a:tbl."""
    root = etree.fromstring(xml)
    tbl = next(root.iter(f"{{{A}}}tbl"))
    frame = tbl.getparent()
    while frame is not None and etree.QName(frame).localname != "graphicFrame":
        frame = frame.getparent()
    P = "http://schemas.openxmlformats.org/presentationml/2006/main"
    ext = frame.find(f"{{{P}}}xfrm/{{{A}}}ext")
    return int(ext.get("cy"))


def test_fill_native_table_no_grid_is_dropped():
    """Слайд без a:tbl — unit честно дропается."""
    if not VK_TEMPLATE.exists():
        pytest.skip("organizer fixture not present")
    pkg = OpcPackage.open(VK_TEMPLATE)
    slide = next(p for p in pkg.parts if p.startswith("ppt/slides/slide"))
    xml, rep = fill_native_tables(pkg.parts[slide], [_mk_table()])
    assert rep.tables_found == 0 and rep.units_dropped == 1
    assert xml == pkg.parts[slide]


def _template_with_native_table(path: Path) -> Path:
    """Минимальный шаблон: один content-like слайд с нативной a:tbl
    (python-pptx add_table) — экземпляр гарантированно попадёт в пул."""
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    for i, text in enumerate(
        [
            "Заголовок слайда с достаточно длинным текстом",
            "Первый абзац содержательного текста слайда",
            "Второй абзац содержательного текста слайда",
            "Третий абзац содержательного текста слайда",
        ]
    ):
        box = slide.shapes.add_textbox(
            Inches(0.5), Inches(0.3 + i * 0.5), Inches(8), Inches(0.4)
        )
        box.text_frame.text = text
    frame = slide.shapes.add_table(
        3, 2, Inches(0.5), Inches(3.0), Inches(8), Inches(2)
    )
    frame.table.cell(0, 0).text = "Сток"
    prs.save(path)
    return path


def _plan_with_table_unit() -> dict:
    plan = json.loads(DECK_PLAN.read_text(encoding="utf-8"))
    slide = copy.deepcopy(plan["slides"][0])
    slide["content_units"] = [
        {"role": "title", "kind": "title", "text": "Показатели"},
        {"role": "body", "kind": "table", "table_ref": "tbl-1"},
    ]
    plan["slides"] = [slide]
    return plan


def test_compose_fills_exemplar_table(tmp_path):
    """Сквозная заливка: plan.table_ref -> Table -> a:tbl exemplar'а.
    Ячейки выходной колоды содержат реальные данные, не сток."""
    tpl = _template_with_native_table(tmp_path / "tpl.pptx")
    out = tmp_path / "out.pptx"
    report = generate_deck(tpl, _plan_with_table_unit(), out, tables=[_mk_table()])
    slide_rep = report["slides"][0]
    assert slide_rep["tables"]["tables_filled"] == 1
    assert report["dropped_units"] == {}

    pkg = OpcPackage.open(out)
    slide_xml = next(
        v
        for p, v in pkg.parts.items()
        if p.startswith("ppt/slides/slide") and p.endswith(".xml")
    )
    grid = _cell_grid(slide_xml)
    assert grid[0] == ["Метрика", "Значение"]
    assert grid[1] == ["Рост", "12"]
    assert grid[2] == ["Доля", "0.5"]


def test_compose_table_ref_without_grid_dropped(tmp_path):
    """table_ref есть, a:tbl в exemplar нет — второй unit создаёт НОВУЮ
    таблицу в крупнейшем опустевшем текстовом слоте (add_table)."""
    tpl = _template_with_native_table(tmp_path / "tpl.pptx")
    # два table-unit при одной a:tbl — второй уходит в add_table
    plan = _plan_with_table_unit()
    plan["slides"][0]["content_units"].append(
        {"role": "body", "kind": "table", "table_ref": "tbl-2"}
    )
    report = generate_deck(
        tpl,
        plan,
        tmp_path / "o.pptx",
        tables=[_mk_table(), _mk_table(tid="tbl-2")],
    )
    tables = report["slides"][0]["tables"]
    assert tables["tables_filled"] == 2 and tables["tables_created"] == 1


def test_new_table_that_cannot_fit_slide_bounds_is_dropped_honestly(tmp_path):
    tpl = _template_with_native_table(tmp_path / "tpl.pptx")
    plan = _plan_with_table_unit()
    plan["slides"][0]["content_units"].append(
        {"role": "body", "kind": "table", "table_ref": "tbl-2"}
    )
    too_tall = _mk_table(rows=[(f"Строка {i}", i) for i in range(30)])
    too_tall = too_tall.model_copy(update={"id": "tbl-2"})
    report = generate_deck(
        tpl,
        plan,
        tmp_path / "too-tall.pptx",
        tables=[_mk_table(), too_tall],
    )
    tables = report["slides"][0]["tables"]
    assert tables["tables_created"] == 0
    assert tables["units_dropped"] == 1
    assert report["dropped_units"]["table"] == 1


def test_markdown_table_reaches_plan():
    """GFM-таблица в markdown -> pack.tables + table_ref-блок в потоке,
    иначе Table никогда не доходит до DeckPlan."""
    from deckdna.ingestion.content_parsers import parse_markdown

    pack = parse_markdown(
        "# Показатели\n\n| Метрика | Значение |\n|---|---|\n| Рост | 12 |\n"
    )
    assert len(pack.tables) == 1
    refs = [b.text for s in pack.sections for b in s.blocks if b.kind.value == "table_ref"]
    assert refs == ["tbl-0"]


def test_compose_grown_table_audited(tmp_path):
    """e2e: Table с 12 строками при сетке 3 — все строки в выходной
    колоде; выросший фрейм за границей слайда ловится аудитом."""
    tpl = _template_with_native_table(tmp_path / "tpl.pptx")
    out = tmp_path / "out.pptx"
    big = _mk_table(rows=[(f"Строка {i}", i) for i in range(12)])
    report = generate_deck(tpl, _plan_with_table_unit(), out, tables=[big])
    assert report["slides"][0]["tables"]["rows_grown"] == 10

    pkg = OpcPackage.open(out)
    slide_xml = next(
        v
        for p, v in pkg.parts.items()
        if p.startswith("ppt/slides/slide") and p.endswith(".xml")
    )
    grid = _cell_grid(slide_xml)
    assert len(grid) == 13 and grid[-1] == ["Строка 11", "11"]

    from deckdna.audit.basic import audit_deck

    issues = audit_deck(out)
    assert any(
        i.rule_code == "layout.out_of_bounds" and i.slide_index == 0
        for i in issues
    )


def _grid_col_count(xml: bytes) -> int:
    root = etree.fromstring(xml)
    tbl = next(root.iter(f"{{{A}}}tbl"))
    return len(tbl.findall(f"{{{A}}}tblGrid/{{{A}}}gridCol"))


def test_fill_native_table_grows_extra_cols():
    """5-колоночная Table в сетку 2 колонки — gridCol и a:tc клонируются,
    все 5 значений строки реально в выводе, cx фрейма синканут."""
    if not VK_WORKSPACE.exists():
        pytest.skip("vk_workspace fixture not present")
    pkg = OpcPackage.open(VK_WORKSPACE)
    xml = pkg.parts["ppt/slides/slide14.xml"]
    before = _frame_cy(xml)
    wide = _mk_table(
        headers=("A", "B", "C", "D", "E"),
        rows=[("1", "2", "3", "4", "5"), ("6", "7", "8", "9", "10")],
    )
    xml, rep = fill_native_tables(xml, [wide])
    assert rep.cols_grown == 3 and rep.cols_dropped == 0
    assert _grid_col_count(xml) == 5
    grid = _cell_grid(xml)
    assert grid[0] == ["A", "B", "C", "D", "E"]
    assert grid[1] == ["1", "2", "3", "4", "5"]
    assert grid[2] == ["6", "7", "8", "9", "10"]
    # оставшиеся строки тоже дополнены a:tc до 5
    assert all(len(r) == 5 for r in grid)
    assert before == _frame_cy(xml)  # высоту рост колонок не трогает
    # cx синканут под сумму gridCol@w — аудит видит реальную ширину
    root = etree.fromstring(xml)
    tbl = next(root.iter(f"{{{A}}}tbl"))
    total_w = sum(int(c.get("w")) for c in tbl.findall(f"{{{A}}}tblGrid/{{{A}}}gridCol"))
    assert _frame_cx(xml) == total_w


def _frame_cx(xml: bytes) -> int:
    root = etree.fromstring(xml)
    tbl = next(root.iter(f"{{{A}}}tbl"))
    frame = tbl.getparent()
    while frame is not None and etree.QName(frame).localname != "graphicFrame":
        frame = frame.getparent()
    P = "http://schemas.openxmlformats.org/presentationml/2006/main"
    ext = frame.find(f"{{{P}}}xfrm/{{{A}}}ext")
    return int(ext.get("cx"))


def test_add_table_on_tableless_slide(tmp_path):
    """add_table: exemplar без a:tbl + table unit -> НОВАЯ нативная
    таблица создана в bbox опустевшего слота, ячейки содержат данные."""
    if not VK_TEMPLATE.exists():
        pytest.skip("organizer fixture not present")
    report = generate_deck(
        VK_TEMPLATE,
        _plan_with_table_unit(),
        tmp_path / "out.pptx",
        tables=[_mk_table()],
    )
    slide_rep = report["slides"][0]
    assert slide_rep["tables"]["tables_created"] == 1
    assert slide_rep["tables"]["tables_filled"] == 1

    pkg = OpcPackage.open(tmp_path / "out.pptx")
    slide_xml = next(
        v
        for p, v in pkg.parts.items()
        if p.startswith("ppt/slides/slide") and p.endswith(".xml")
    )
    grid = _cell_grid(slide_xml)
    assert grid[0] == ["Метрика", "Значение"]
    assert grid[1] == ["Рост", "12"]


def test_add_table_no_host_drops():
    """Сырой slide XML (без предварительной очистки слотов) — все тела
    заняты стоковым текстом, хоста нет -> unit честно дропается."""
    if not VK_TEMPLATE.exists():
        pytest.skip("organizer fixture not present")
    pkg = OpcPackage.open(VK_TEMPLATE)
    slide = next(p for p in pkg.parts if p.startswith("ppt/slides/slide"))
    xml, rep = fill_native_tables(pkg.parts[slide], [_mk_table()])
    assert rep.tables_created == 0 and rep.units_dropped == 1
