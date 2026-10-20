"""Финансовые таблицы: разделители тысяч, правое выравнивание чисел,
Top-N + «Прочие» + «Итого» для длинных разрезов."""

from __future__ import annotations

from deckdna.analysis import analyze_dataset
from deckdna.analysis.pipeline import view_table
from deckdna.ingestion.tabular import read_csv
from deckdna.pptx.composing.table_fill import _cell_text, _set_cell
from lxml import etree

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NBSP = " "


def _tc() -> etree._Element:
    return etree.fromstring(
        f'<a:tc xmlns:a="{A}"><a:txBody><a:p><a:r><a:t/></a:r></a:p>'
        f'</a:txBody><a:tcPr/></a:tc>'
    )


def _para(tc):
    return tc.find(f".//{{{A}}}p")


def test_cell_text_formats_thousands():
    assert _cell_text(44862) == f"44{NBSP}862"
    assert _cell_text(1_234_567) == f"1{NBSP}234{NBSP}567"
    assert _cell_text(0.35) == "0,35"
    assert _cell_text("текст") == "текст"
    assert _cell_text(None) == ""


def test_numeric_cells_align_right_headers_and_text_do_not():
    tc = _tc()
    _set_cell(tc, "44 862")
    assert _para(tc).find(f"{{{A}}}pPr").get("algn") == "r"
    tc = _tc()
    _set_cell(tc, "Выручка")
    assert _para(tc).find(f"{{{A}}}pPr") is None
    tc = _tc()
    _set_cell(tc, "1 234 567", header=True)
    assert _para(tc).find(f"{{{A}}}pPr") is None, "шапка хранит выравнивание донора"


def test_view_table_summarizes_long_slices():
    rows = "".join(f"Город {i:02d};{1000 + i}\n" for i in range(20))
    ds = read_csv(("Город;Выручка\n" + rows).encode(), "cities.csv")
    analysis = analyze_dataset(ds)
    va = next(v for v in analysis.views if v.view.shape == "categorical")
    table = view_table(ds, va)
    assert len(table.rows) == 16  # 14 строк + «Прочие» + «Итого»
    assert table.rows[-2][0].startswith("Прочие (6 позиций)")
    assert table.rows[-1][0] == "Итого"
    expected = sum(1000 + i for i in range(20))
    assert table.rows[-1][1] == expected
    head_sum = sum(r[1] for r in table.rows[:-2])
    assert table.rows[-2][1] == expected - head_sum


def test_view_table_short_slice_unchanged():
    ds = read_csv("Город;Выручка\nA;10\nB;20\nC;30\n".encode(), "s.csv")
    analysis = analyze_dataset(ds)
    va = next(v for v in analysis.views if v.view.shape == "categorical")
    table = view_table(ds, va)
    # короткий разрез — без сводных строк; порядок — по убыванию значения
    assert sorted(r[0] for r in table.rows) == ["A", "B", "C"]
    assert not any("Прочие" in str(r[0]) or r[0] == "Итого" for r in table.rows)
