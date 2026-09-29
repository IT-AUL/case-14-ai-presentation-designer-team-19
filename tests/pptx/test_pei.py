"""PEI assessment on real .pptx packages (python-pptx generated fixtures)."""

from deckdna.pptx.validation.pei import assess_pptx
from pptx import Presentation
from pptx.util import Inches


def _native_deck(path):
    """Deck with native text, vector shapes and a table -> expect L4+."""
    prs = Presentation()
    s1 = prs.slides.add_slide(prs.slide_layouts[5])  # title only
    s1.shapes.title.text = "Native title"
    box = s1.shapes.add_textbox(Inches(1), Inches(2), Inches(4), Inches(1))
    box.text_frame.text = "editable text"

    s2 = prs.slides.add_slide(prs.slide_layouts[5])
    s2.shapes.title.text = "Data"
    shape = s2.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(4), Inches(1))
    shape.table.cell(0, 0).text = "a"
    prs.save(path)
    return path


def _raster_only_deck(path, png):
    """Every slide is a single picture, no text -> L0."""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
    slide.shapes.add_picture(
        str(png), 0, 0, width=prs.slide_width, height=prs.slide_height
    )
    prs.save(path)
    return path


def _png(tmp_path):
    from PIL import Image

    p = tmp_path / "img.png"
    Image.new("RGB", (64, 64), "navy").save(p)
    return p


def test_native_deck_reaches_l4_or_higher(tmp_path):
    report = assess_pptx(_native_deck(tmp_path / "deck.pptx"))
    assert report.openable
    assert report.level >= 4, report.to_dict()
    assert report.raster_only_slides == []


def test_raster_only_deck_is_l0(tmp_path):
    report = assess_pptx(_raster_only_deck(tmp_path / "flat.pptx", _png(tmp_path)))
    assert report.openable
    assert report.level == 0
    assert "raster-only" in " ".join(report.reasons)


def test_mixed_deck_capped_below_l3(tmp_path):
    """A raster-only slide anywhere must block L3 even if other slides are native."""
    prs = Presentation()
    s1 = prs.slides.add_slide(prs.slide_layouts[5])
    s1.shapes.title.text = "native"
    box = s1.shapes.add_textbox(Inches(1), Inches(2), Inches(4), Inches(1))
    box.text_frame.text = "text"
    s2 = prs.slides.add_slide(prs.slide_layouts[6])
    s2.shapes.add_picture(
        str(_png(tmp_path)), 0, 0, width=prs.slide_width, height=prs.slide_height
    )
    path = tmp_path / "mixed.pptx"
    prs.save(path)
    report = assess_pptx(path)
    assert report.level < 3
    assert report.raster_only_slides


def test_unopenable_is_l0(tmp_path):
    bad = tmp_path / "bad.pptx"
    bad.write_bytes(b"not a zip")
    report = assess_pptx(bad)
    assert not report.openable
    assert report.level == 0


def test_report_serializes(tmp_path):
    report = assess_pptx(_native_deck(tmp_path / "d.pptx"))
    d = report.to_dict()
    assert d["pei_level"] == report.level and d["slide_count"] == 2
