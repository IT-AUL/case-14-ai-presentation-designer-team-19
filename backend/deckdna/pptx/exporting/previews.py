"""Превью и монтаж из PDF; HTML-бандл из объектов PPTX.

Тяжёлая часть рендера — ``soffice`` (pptx → pdf) — делается один раз;
всё остальное строится из PDF без повторного запуска LibreOffice и без
``pdftoppm``: страницы растеризует PyMuPDF (уже в зависимостях).

- ``pdf_page_pngs``   — по PNG на страницу (превью слайдов, D6);
- ``montage_png``     — сетка миниатюр одним файлом (D6, ``montage_artifact_id``);
- ``html_bundle_zip`` — zip с ``index.html`` и ``assets/``: текст и таблицы
  разметкой, графики inline SVG, картинки из медиа PPTX.

"""

from __future__ import annotations

import io
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
            thumbs.append(im.resize((thumb_width_px, max(1, round(im.height * ratio)))))
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


def html_bundle_zip(
    pptx_path: str | Path,
    pngs: Sequence[bytes] = (),
    *,
    title: str | None = None,
    slide_titles: Sequence[str | None] | None = None,
) -> bytes:
    """Standalone HTML and assets from PPTX objects, shared with CLI/skill.

    ``pngs`` remains accepted for callers of the previous internal helper;
    slide previews are never included in the HTML bundle.
    """
    from deckdna.pptx.exporting.html import html_zip

    return html_zip(pptx_path, title=title, slide_titles=slide_titles)
