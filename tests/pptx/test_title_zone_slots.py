"""Слоты в зоне заголовка не получают тезисы тела.

QA (agenda43): у донора без плейсхолдера заголовка текст «Проблема» был
залит в кикер-рамку внутри шапочной полосы, а «План презентации» встал
отдельной плашкой — два заголовка на одном слайде. Слот, пересекающийся
с зоной заголовка (плейсхолдер или дорисованная плашка), исключается из
заливки — его стоковый текст всё равно очищается.
"""

from deckdna.pptx.composing.text_replace import replace_text_runs
from lxml import etree
from pptx import Presentation
from pptx.util import Inches

SW, SH = 9_144_000, 6_858_000  # 10" × 7.5" (4:3)


def _slide_with_kicker_and_bodies():
    prs = Presentation()
    prs.slide_width = SW
    prs.slide_height = SH
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    # кикер внутри шапочной полосы (0.15" — накрывается плашкой заголовка)
    slide.shapes.add_textbox(Inches(0.5), Inches(0.15), Inches(9), Inches(0.6))
    slide.shapes[-1].text_frame.text = "Подраздел"
    # два тела ниже
    for i, y in enumerate((2.0, 4.0)):
        tb = slide.shapes.add_textbox(Inches(0.5), Inches(y), Inches(9), Inches(1.5))
        tb.text_frame.text = f"Тело {i}"
    return slide


def _texts_by_pos(xml: bytes):
    root = etree.fromstring(xml)
    P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
    A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    out = []
    for sp in root.iter(f"{P}sp"):
        off = sp.find(f"{P}spPr/{A}xfrm/{A}off")
        y = int(off.get("y")) if off is not None else 0
        text = "".join(t.text or "" for t in sp.iter(f"{A}t")).strip()
        out.append((y, text))
    return sorted(out)


def test_kicker_in_title_zone_is_cleared_not_filled():
    xml = _slide_with_kicker_and_bodies()._element.xml.encode()
    out, report = replace_text_runs(
        xml,
        ["Пункт один", "Пункт два"],
        title_text="План презентации",
        slide_size=(SW, SH),
    )
    texts = _texts_by_pos(out)
    kicker = texts[0][1]
    assert "Пункт" not in kicker and "Подраздел" not in kicker
    bodies = [t for _, t in texts[1:]]
    assert bodies == ["Пункт один", "Пункт два"]
    assert report.texts_dropped == 0


def test_title_placeholder_zone_protects_overlapping_slot():
    prs = Presentation()
    prs.slide_width = SW
    prs.slide_height = SH
    slide = prs.slides.add_slide(prs.slide_layouts[5])  # Title Only
    slide.shapes.title.text_frame.text = "Заголовок"
    # рамка, наполовину залезшая на полосу заголовка
    tb = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(9), Inches(0.6))
    tb.text_frame.text = "Кикер"
    body = slide.shapes.add_textbox(Inches(0.5), Inches(3.0), Inches(9), Inches(2))
    body.text_frame.text = "Тело"
    out, _report = replace_text_runs(
        slide._element.xml.encode(),
        ["Тезис"],
        title_text="Новый заголовок",
        slide_size=(SW, SH),
    )
    texts = _texts_by_pos(out)
    joined = " | ".join(t for _, t in texts)
    assert "Кикер" not in joined
    assert "Тезис" in joined and "Новый заголовок" in joined
