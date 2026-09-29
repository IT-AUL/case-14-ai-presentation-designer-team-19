"""Превью слайдов, монтаж и HTML-бандл из готового PDF (contract D5/D6)."""

from __future__ import annotations

import io
import zipfile

import pymupdf
import pytest
from deckdna.errors import DeckDNAError
from deckdna.pptx.exporting import previews
from PIL import Image
from pptx import Presentation
from pptx.util import Inches


def _pdf(tmp_path, pages=3):
    doc = pymupdf.open()
    for n in range(pages):
        page = doc.new_page(width=960, height=540)
        page.insert_text((72, 100), f"Page {n + 1}", fontsize=40)
    path = tmp_path / "deck.pdf"
    doc.save(str(path))
    doc.close()
    return path


def _deck(tmp_path, titles):
    prs = Presentation()
    for t in titles:
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(1))
        box.text_frame.text = t
    path = tmp_path / "deck.pptx"
    prs.save(path)
    return path


def test_pdf_page_pngs_one_per_page_at_requested_width(tmp_path):
    pngs = previews.pdf_page_pngs(_pdf(tmp_path, 3), width_px=480)
    assert len(pngs) == 3
    for data in pngs:
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        with Image.open(io.BytesIO(data)) as im:
            assert im.width == 480 and im.height == 270


def test_montage_is_a_grid_of_all_slides(tmp_path):
    pngs = previews.pdf_page_pngs(_pdf(tmp_path, 5), width_px=320)
    montage = previews.montage_png(pngs, columns=3, thumb_width_px=160, gap_px=4)
    sheet = Image.open(io.BytesIO(montage))
    assert sheet.width == 3 * 160 + 4 * 4
    assert sheet.height == 2 * 90 + 3 * 4  # 5 слайдов в 3 колонки = 2 ряда


def test_montage_of_nothing_is_a_typed_error():
    with pytest.raises(DeckDNAError) as err:
        previews.montage_png([])
    assert err.value.code == "invalid_input"


def test_html_bundle_has_markup_captions_and_slide_images(tmp_path):
    pngs = previews.pdf_page_pngs(_pdf(tmp_path, 2), width_px=200)
    data = previews.html_bundle_zip(
        _deck(tmp_path, ["Первый <слайд>", "Второй"]),
        pngs,
        title="Тест & ко",
        slide_titles=["Заголовок 1", None],
    )
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert sorted(zf.namelist()) == ["index.html", "slide-1.png", "slide-2.png"]
        html = zf.read("index.html").decode()
    assert '<html lang="ru">' in html and "<h1>Тест &amp; ко</h1>" in html
    assert "Слайд 1 — Заголовок 1" in html and "Слайд 2</h2>" in html
    assert "Первый &lt;слайд&gt;" in html, "text is markup, escaped"
    assert 'src="slide-1.png"' in html
