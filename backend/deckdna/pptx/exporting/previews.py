"""Превью слайдов, монтаж и HTML-бандл из уже готового PDF.

Тяжёлая часть рендера — ``soffice`` (pptx → pdf) — делается один раз;
всё остальное строится из PDF без повторного запуска LibreOffice и без
``pdftoppm``: страницы растеризует PyMuPDF (уже в зависимостях).

- ``pdf_page_pngs``   — по PNG на страницу (превью слайдов, D6);
- ``montage_png``     — сетка миниатюр одним файлом (D6, ``montage_artifact_id``);
- ``html_bundle_zip`` — zip с ``index.html`` и ``slide-N.png`` (D5): HTML —
  просмотрщик колоды (ARCHITECTURE §12: OR-010 — viewer, не редактируемый
  формат), подписи и заголовки слайдов — разметкой, а не картинкой.
"""

from __future__ import annotations

import html as _html
import io
import zipfile
from collections.abc import Sequence
from pathlib import Path

from deckdna.errors import DeckDNAError

PREVIEW_WIDTH_PX = 960
_MONTAGE_COLUMNS = 4
_MONTAGE_THUMB_WIDTH_PX = 480
_MONTAGE_GAP_PX = 12


def pdf_page_pngs(pdf_path: str | Path, width_px: int = PREVIEW_WIDTH_PX) -> list[bytes]:
    """PNG каждой страницы PDF шириной *width_px* (пропорции сохраняются)."""
    import pymupdf

    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception as exc:  # noqa: BLE001 — битый PDF → typed error
        raise DeckDNAError(
            code="render_failed",
            message=f"не удалось открыть PDF для превью: {exc}",
            stage="exporting.render",
        ) from exc
    pages: list[bytes] = []
    try:
        for page in doc:
            zoom = width_px / page.rect.width
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            pages.append(pix.tobytes("png"))
    finally:
        doc.close()
    return pages


def montage_png(
    pngs: Sequence[bytes],
    columns: int = _MONTAGE_COLUMNS,
    thumb_width_px: int = _MONTAGE_THUMB_WIDTH_PX,
    gap_px: int = _MONTAGE_GAP_PX,
) -> bytes:
    """Сетка миниатюр слайдов одним PNG (слева направо, сверху вниз)."""
    from PIL import Image

    if not pngs:
        raise DeckDNAError(
            code="invalid_input",
            message="нечего собирать в монтаж: нет ни одного слайда",
            stage="exporting.render",
        )
    thumbs = []
    for data in pngs:
        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("RGB")
            ratio = thumb_width_px / im.width
            thumbs.append(
                im.resize((thumb_width_px, max(1, round(im.height * ratio))))
            )
    cols = max(1, min(columns, len(thumbs)))
    rows = -(-len(thumbs) // cols)
    cell_h = max(t.height for t in thumbs)
    sheet = Image.new(
        "RGB",
        (
            cols * thumb_width_px + (cols + 1) * gap_px,
            rows * cell_h + (rows + 1) * gap_px,
        ),
        (243, 244, 246),
    )
    for i, thumb in enumerate(thumbs):
        r, c = divmod(i, cols)
        sheet.paste(
            thumb,
            (gap_px + c * (thumb_width_px + gap_px), gap_px + r * (cell_h + gap_px)),
        )
    out = io.BytesIO()
    sheet.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _slide_texts(pptx_path: Path) -> list[str]:
    from deckdna.pptx.exporting.render import _slide_texts as texts

    return texts(pptx_path)


def html_bundle_zip(
    pptx_path: str | Path,
    pngs: Sequence[bytes],
    *,
    title: str | None = None,
    slide_titles: Sequence[str | None] | None = None,
) -> bytes:
    """Zip: ``index.html`` (просмотрщик) + ``slide-N.png``.

    Подписи берутся из самой колоды (текст слайдов), поэтому HTML остаётся
    читаемым и индексируемым; картинка — визуальный слепок слайда."""
    pptx_path = Path(pptx_path)
    texts = _slide_texts(pptx_path)
    heading = _html.escape(title or pptx_path.stem)
    items: list[str] = []
    for i in range(len(pngs)):
        caption = texts[i] if i < len(texts) else ""
        slide_title = (
            slide_titles[i] if slide_titles and i < len(slide_titles) else None
        )
        label = f"Слайд {i + 1}" + (f" — {slide_title}" if slide_title else "")
        items.append(
            '<section class="slide">\n'
            f"  <h2>{_html.escape(label)}</h2>\n"
            f'  <img src="slide-{i + 1}.png" alt="{_html.escape(label)}">\n'
            + (
                f'  <p class="caption">{_html.escape(caption)}</p>\n'
                if caption
                else ""
            )
            + "</section>"
        )
    index = (
        "<!doctype html>\n"
        '<html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{heading}</title>\n"
        "<style>"
        "body{font-family:system-ui,sans-serif;margin:2em auto;max-width:1100px;"
        "padding:0 1em;background:#f7f7f7;color:#1a1a1a}"
        ".slide{margin-bottom:2.5em}"
        "img{max-width:100%;border:1px solid #ccc;background:#fff}"
        ".caption{color:#555;font-size:.9em;white-space:pre-line;margin-top:.4em}"
        "</style></head><body>\n"
        f"<h1>{heading}</h1>\n" + "\n".join(items) + "\n</body></html>\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.html", index)
        for i, data in enumerate(pngs):
            zf.writestr(f"slide-{i + 1}.png", data)
    return buf.getvalue()
