"""Перераскладка карточек: пустые карточки сетки удаляются, оставшиеся
растягиваются; «чужие» фигуры и картинки не ломаются."""

from __future__ import annotations

import io
import re
import zipfile

from deckdna.pptx.composing.card_reflow import reflow_cards
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Emu, Pt

SW, SH = 12192000, 6858000


def _grid_deck(
    filled: set[int], numbers: bool = True, foreign: bool = False
) -> tuple[bytes, Presentation]:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(SW), Emu(SH)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    cw, ch, gx, gy = int(SW * 0.30), int(SH * 0.30), int(SW * 0.02), int(SH * 0.03)
    x0, y0 = int(SW * 0.03), int(SH * 0.25)
    for i in range(6):
        r, c = divmod(i, 3)
        x, y = x0 + c * (cw + gx), y0 + r * (ch + gy)
        slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, cw, ch)
        dot = slide.shapes.add_shape(MSO_SHAPE.OVAL, x + 50000, y + 50000, 80000, 80000)
        dot.text_frame.text = ""
        head = slide.shapes.add_textbox(x + 60000, y + 200000, cw - 120000, 250000)
        body = slide.shapes.add_textbox(x + 60000, y + 500000, cw - 120000, ch - 600000)
        if i in filled:
            head.text_frame.text = f"Заголовок {i}"
            run = body.text_frame.paragraphs[0].add_run()
            run.text = "Основной текст карточки с мыслью"
            run.font.size = Pt(10)
        if numbers:
            num = slide.shapes.add_textbox(x + cw - 700000, y + ch - 500000, 600000, 400000)
            num.text_frame.text = f"0{i + 1}"
    if foreign:
        slide.shapes.add_picture(
            io.BytesIO(_png()), x0 + 2 * (cw + gx), y0 + (ch + gy), cw, ch
        )
    buf = io.BytesIO()
    prs.save(buf)
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        return zf.read("ppt/slides/slide1.xml"), prs


def _png() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (40, 40), "red").save(out, format="PNG")
    return out.getvalue()


def _boxes(xml: bytes):
    from lxml import etree

    ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main",
          "p": "http://schemas.openxmlformats.org/presentationml/2006/main"}
    root = etree.fromstring(xml)
    out = []
    for sp in root.iterfind(".//p:sp", ns):
        off, ext = sp.find(".//a:off", ns), sp.find(".//a:ext", ns)
        geom = sp.find(".//a:prstGeom", ns)
        text = "".join(t.text or "" for t in sp.iterfind(".//a:t", ns))
        out.append((geom.get("prst") if geom is not None else "tx", int(off.get("x")),
                    int(off.get("y")), int(ext.get("cx")), int(ext.get("cy")), text))
    return out


def test_empty_cards_are_removed_and_kept_ones_fill_the_row():
    xml, _ = _grid_deck({0, 1})
    new, report = reflow_cards(xml, SW, SH)
    assert report.to_dict()["cards_found"] == 6
    assert report.cards_removed == 4 and report.cards_kept == 2
    cards = [b for b in _boxes(new) if b[0] == "roundRect"]
    assert len(cards) == 2
    # две карточки делят всю ширину исходной сетки и её высоту
    assert cards[0][3] > int(SW * 0.40) and cards[0][4] > int(SH * 0.55)
    assert cards[1][1] > cards[0][1] + cards[0][3]
    texts = [b[5] for b in _boxes(new) if b[5]]
    assert "Заголовок 0" in texts and "Заголовок 1" in texts
    assert "03" not in texts, "numbering of removed cards goes away with them"


def test_body_font_grows_with_the_card():
    xml, _ = _grid_deck({0})
    new, report = reflow_cards(xml, SW, SH)
    assert report.font_grown >= 1
    assert b'sz="1000"' not in new


def test_numbering_alone_does_not_make_a_card_filled():
    xml, _ = _grid_deck(set(range(3)), numbers=True)
    _, report = reflow_cards(xml, SW, SH)
    assert report.cards_kept == 3 and report.cards_removed == 3


def test_numeric_kpi_is_kept_when_replacement_history_marks_its_shape():
    from deckdna.pptx.composing.text_replace import replace_text_runs
    xml, _ = _grid_deck({0}, numbers=True)
    replaced, replacement = replace_text_runs(xml, ["42%"])
    new, report = reflow_cards(
        replaced, SW, SH, filled_shape_ids=set(replacement.filled_shape_ids)
    )
    assert report.cards_kept == 1 and report.cards_removed == 5
    texts = [b[5] for b in _boxes(new) if b[5]]
    assert "42%" in texts
    assert "02" not in texts


def test_all_filled_is_a_no_op_and_all_empty_is_removed():
    xml, _ = _grid_deck(set(range(6)))
    assert reflow_cards(xml, SW, SH)[0] == xml
    xml2, _ = _grid_deck(set(), numbers=False)
    new2, report2 = reflow_cards(xml2, SW, SH)
    assert report2.cards_removed == 6, "a fully empty grid is leftover demo structure"
    assert b"roundRect" not in new2


def test_foreign_shape_in_the_grid_limits_stretching_to_clean_rows():
    xml, _ = _grid_deck({0, 1}, foreign=True)
    new, report = reflow_cards(xml, SW, SH)
    assert report.cards_removed == 4
    cards = [b for b in _boxes(new) if b[0] == "roundRect"]
    # растянуты только в первом ряду — второй ряд занят картинкой
    assert all(c[2] + c[4] <= int(SH * 0.25) + int(SH * 0.30) + 5 for c in cards)
    assert b"<p:pic" in new


def test_clone_cards_adds_styled_copies_and_regrids():
    from deckdna.pptx.composing.card_reflow import card_slots, clone_cards

    xml, _ = _grid_deck(set(range(6)), numbers=False)
    new, added = clone_cards(xml, 8, SW, SH)
    assert added == 2
    cards = [b for b in _boxes(new) if b[0] == "roundRect"]
    assert len(cards) == 8
    # никакие две карточки не пересекаются, все внутри исходной области
    for i, a in enumerate(cards):
        for b in cards[i + 1:]:
            apart_x = a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1]
            apart_y = a[2] + a[4] <= b[2] or b[2] + b[4] <= a[2]
            assert apart_x or apart_y
    assert card_slots(new, SW, SH)[0] == 8
    ids = re.findall(rb'cNvPr id="(\d+)"', new)
    assert len(ids) == len(set(ids)), "cloned shapes get unique ids"


def test_clone_cards_does_not_drop_the_ninth_requested_card():
    from deckdna.pptx.composing.card_reflow import card_slots, clone_cards

    xml, _ = _grid_deck(set(range(6)), numbers=False)
    new, added = clone_cards(xml, 9, SW, SH)
    assert added == 3
    assert card_slots(new, SW, SH)[0] == 9
    assert len([b for b in _boxes(new) if b[0] == "roundRect"]) == 9


def test_clone_cards_is_a_no_op_when_enough_cards():
    from deckdna.pptx.composing.card_reflow import clone_cards

    xml, _ = _grid_deck(set(range(6)))
    assert clone_cards(xml, 4, SW, SH) == (xml, 0)
