from deckdna.pptx.exporting.html import html_files
from pptx import Presentation


def test_html_viewport_preserves_aspect_ratio_and_keyboard_navigation(tmp_path):
    prs = Presentation()
    prs.slides.add_slide(prs.slide_layouts[6])
    path = tmp_path / "deck.pptx"
    prs.save(path)
    page = html_files(path)["index.html"].decode()
    assert "max-height:calc(100vh - 96px)" in page
    assert "max-width:100%;object-fit:contain" in page
    assert "width:min(100%,calc((100vh - 96px)*var(--slide-ratio)))" in page
    assert f"--slide-ratio:{prs.slide_width / prs.slide_height}" in page
    assert "ArrowLeft" in page and "ArrowRight" in page and "scrollIntoView" in page
