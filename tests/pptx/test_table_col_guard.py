"""Широкие таблицы не превращаются в узкие столбики букв."""

from deckdna.audit.basic import _wrap_metrics
from deckdna.contracts.content_pack import SourceRef, Table
from deckdna.pptx.composing.table_fill import (
    A,
    _build_table_frame,
    fill_native_tables,
)
from lxml import etree
from pptx import Presentation


def wide_table():
    return Table(
        id="wide",
        headers=["Человек, компания, портрет", *[f"Метрика {i}" for i in range(10)]],
        rows=[["Рекомендуемая литература", *range(10)]],
        source_ref=SourceRef(artifact_id="synthetic.csv"),
    )


def filled_table(*, donor=False, width=6_000_000):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    if donor:
        slide.shapes.add_table(2, 11, 200_000, 300_000, width, 800_000)
    else:
        slide.shapes.add_textbox(200_000, 300_000, width, 1_000_000)
    xml, report = fill_native_tables(
        slide._element.xml.encode(), [wide_table()], slide_size=(12_192_000, 6_858_000)
    )
    assert report.tables_filled == 1
    return etree.fromstring(xml)


def test_frame_limits_columns_before_filling():
    frame = _build_table_frame(1, 0, 0, 6_000_000, 2, 11)
    assert len(list(frame.iter(f"{{{A}}}gridCol"))) == 3


def test_wide_table_columns_and_width():
    for donor in (False, True):
        root = filled_table(donor=donor)
        widths = [int(c.get("w")) for c in root.iter(f"{{{A}}}gridCol")]
        assert len(widths) == 3
        assert min(widths) >= 1_800_000
        assert sum(widths) <= 6_000_000
        assert "Отображены первые 3 из 11 колонок" in etree.tostring(root, encoding="unicode")


def test_cell_words_fit_without_letter_wrapping():
    root = filled_table()
    grid = next(root.iter(f"{{{A}}}tbl"))
    widths = [int(c.get("w")) for c in grid.iter(f"{{{A}}}gridCol")]
    for row in grid.findall(f"{{{A}}}tr"):
        for cell, width in zip(row.findall(f"{{{A}}}tc"), widths, strict=True):
            size = int(cell.find(f".//{{{A}}}defRPr").get("sz")) / 100
            for text in cell.iter(f"{{{A}}}t"):
                _, widest_word = _wrap_metrics(text.text or "", size, width - 182_880)
                assert widest_word <= width - 182_880


def test_table_font_never_below_readable_floor():
    from deckdna.pptx.composing.table_fill import _fit_table_font

    frame = _build_table_frame(1, 0, 0, 6_000_000, 30, 2)
    grid = next(frame.iter(f"{{{A}}}tbl"))
    _fit_table_font(grid, (12_192_000, 1_000_000))
    assert all(int(r.get("sz")) >= 1050 for r in grid.iter(f"{{{A}}}defRPr"))


def test_guarded_donor_without_slide_size_has_explicit_readable_font():
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_table(2, 11, 0, 0, 6_000_000, 800_000)
    xml, _ = fill_native_tables(slide._element.xml.encode(), [wide_table()])
    grid = next(etree.fromstring(xml).iter(f"{{{A}}}tbl"))
    sizes = [int(r.get("sz")) for r in grid.iter(f"{{{A}}}defRPr")]
    assert sizes and min(sizes) >= 1050


def test_donor_narrower_than_one_readable_column_is_reported_dropped():
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_table(2, 11, 0, 0, 1_000_000, 800_000)
    xml, report = fill_native_tables(slide._element.xml.encode(), [wide_table()])
    assert report.units_dropped == 1
    assert not list(etree.fromstring(xml).iter(f"{{{A}}}gridCol"))
