"""Readable native PowerPoint layout for bibliography slides."""

from __future__ import annotations

import math
import re

from lxml import etree

from deckdna.audit.basic import EMU_PER_PT, LINE_HEIGHT_FACTOR, _wrap_metrics
from deckdna.errors import DeckDNAError

P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def is_sources_slide(title: str) -> bool:
    return (
        title.casefold()
        .strip()
        .startswith(("references", "bibliography", "источники", "список источников", "литература"))
    )


def _source_entry(text: str) -> str:
    """Keep the bibliographic item, omit planner-style lead-ins."""
    cleaned = re.sub(
        r"^(?:источники\s+включают|использован\s+источник:|источник\s+доступен\s+по\s+ссылке:|sources\s+include)\s*",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    )
    # Some weak-model source summaries leak conversational markers into the
    # bibliography ("См domain.org", "Title: есть domain.org").  They are
    # not evidence and make the rendered source list look like parser debug
    # output.  Keep the title/URL while removing only the marker forms.
    cleaned = re.sub(r"^см\.?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r":\s*(?:есть|is)\s+", ": ", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _is_fullcanvas_artwork(shape, width: int, height: int) -> bool:
    """Текст-пустая фигура, накрывающая ≥80% слайда, — донорский фон.

    Полноэкранная плашка (тёмная заливка, градиент) часто несёт пустой
    txBody: удалять её вместе с демо-текстом нельзя — сгенерированный
    контент окажется на чужом (или белом) фоне."""
    if "".join(t.text or "" for t in shape.iter(f"{A}t")).strip():
        return False
    xfrm = shape.find(f"{P}spPr/{A}xfrm")
    if xfrm is None:
        return False
    off, ext = xfrm.find(f"{A}off"), xfrm.find(f"{A}ext")
    if ext is None:
        return False
    try:
        w, h = int(ext.get("cx", "0")), int(ext.get("cy", "0"))
        x, y = (
            int(off.get("x", "0")) if off is not None else 0,
            (int(off.get("y", "0")) if off is not None else 0),
        )
    except (TypeError, ValueError):
        return False
    return (
        w * h >= 0.8 * width * height
        and x <= 0.05 * width
        and y <= 0.05 * height
        and x + w >= 0.95 * width
        and y + h >= 0.95 * height
    )


def _is_interior_donor_card(shape, width: int, height: int) -> bool:
    if _is_fullcanvas_artwork(shape, width, height):
        return False
    xfrm = shape.find(f"{P}spPr/{A}xfrm")
    if xfrm is None:
        return False
    off, ext = xfrm.find(f"{A}off"), xfrm.find(f"{A}ext")
    if off is None or ext is None:
        return False
    try:
        w, h = int(ext.get("cx", "0")), int(ext.get("cy", "0"))
        y = int(off.get("y", "0"))
    except (TypeError, ValueError):
        return False
    area_ratio = (w * h) / max(1, width * height)
    if not (0.015 <= area_ratio < 0.75):
        return False
    return y >= int(height * 0.10) and (y + h) <= int(height * 0.94)


def _small_content_pic(shape, width: int, height: int) -> bool:
    """Мелкий значок-картинка внутри контентной зоны (≤2% площади,
    центр не в краевой полосе): демо-оформление карточек эталона.
    Логотип в углу и фон на весь слайд сюда не попадают."""
    xfrm = shape.find(f"{P}spPr/{A}xfrm")
    if xfrm is None:
        return False
    off, ext = xfrm.find(f"{A}off"), xfrm.find(f"{A}ext")
    if off is None or ext is None:
        return False
    try:
        w, h = int(ext.get("cx", "0")), int(ext.get("cy", "0"))
        x, y = int(off.get("x", "0")), int(off.get("y", "0"))
    except (TypeError, ValueError):
        return False
    if w * h > 0.02 * width * height:
        return False
    corner = 0.12
    cx, cy = x + w / 2, y + h / 2
    return not (
        (cx < corner * width or cx > (1 - corner) * width)
        and (cy < corner * height or cy > (1 - corner) * height)
    )


def _shape(
    tree,
    sid: int,
    name: str,
    x: int,
    y: int,
    w: int,
    h: int,
    text: str | None = None,
    size: int = 1450,
    fill: str | None = None,
    theme_fill: str | None = None,
    geometry: str = "rect",
    tint: int | None = None,
    text_theme: str = "tx1",
):
    shape = etree.SubElement(tree, P + "sp")
    nv = etree.SubElement(shape, P + "nvSpPr")
    etree.SubElement(nv, P + "cNvPr", id=str(sid), name=name)
    etree.SubElement(nv, P + "cNvSpPr")
    etree.SubElement(nv, P + "nvPr")
    props = etree.SubElement(shape, P + "spPr")
    transform = etree.SubElement(props, A + "xfrm")
    etree.SubElement(transform, A + "off", x=str(x), y=str(y))
    etree.SubElement(transform, A + "ext", cx=str(w), cy=str(h))
    geom = etree.SubElement(props, A + "prstGeom", prst=geometry)
    etree.SubElement(geom, A + "avLst")
    if fill:
        color = etree.SubElement(etree.SubElement(props, A + "solidFill"), A + "srgbClr")
        color.set("val", fill)
    elif theme_fill:
        color = etree.SubElement(etree.SubElement(props, A + "solidFill"), A + "schemeClr")
        color.set("val", theme_fill)
        if tint is not None:
            etree.SubElement(color, A + "tint", val=str(tint))
    else:
        etree.SubElement(props, A + "noFill")
    etree.SubElement(etree.SubElement(props, A + "ln"), A + "noFill")
    if text is not None:
        body = etree.SubElement(shape, P + "txBody")
        etree.SubElement(
            body, A + "bodyPr", wrap="square", anchor="t", lIns="0", rIns="0", tIns="0", bIns="0"
        )
        etree.SubElement(body, A + "lstStyle")
        paragraph = etree.SubElement(body, A + "p")
        ppr = etree.SubElement(paragraph, A + "pPr", algn="l")
        etree.SubElement(ppr, A + "buNone")
        run = etree.SubElement(paragraph, A + "r")
        rpr = etree.SubElement(run, A + "rPr", lang="ru-RU", sz=str(size))
        clr = etree.SubElement(etree.SubElement(rpr, A + "solidFill"), A + "schemeClr")
        clr.set("val", text_theme)
        etree.SubElement(rpr, A + "latin", typeface="+mn-lt")
        etree.SubElement(rpr, A + "ea", typeface="+mn-ea")
        etree.SubElement(rpr, A + "cs", typeface="+mn-cs")
        etree.SubElement(run, A + "t").text = text


def _fitted_size(
    text: str, width: int, height: int, preferred: float, floor: float = 10.0
) -> float:
    """Fit the whole text to physical geometry, never to a character quota.

    At the readability floor retain the complete text; a genuine excess
    remains visible to the normal overflow audit, rather than being hidden
    by a fabricated ellipsis. Measure hard line breaks separately.
    """
    size = preferred
    while size >= floor:
        metrics = [_wrap_metrics(line, size, width) for line in text.splitlines() or [text]]
        lines = sum(m[0] for m in metrics)
        if (
            lines * size * LINE_HEIGHT_FACTOR * EMU_PER_PT <= height * 0.94
            and max(m[1] for m in metrics) <= width
        ):
            return size
        size -= 0.5
    return floor


def _fits_at_floor(text: str, width: int, height: int, floor: float = 8.0) -> bool:
    """Whether every wrapped line fits at the minimum readable source size."""
    metrics = [_wrap_metrics(line, floor, width) for line in text.splitlines() or [text]]
    lines = sum(m[0] for m in metrics)
    return (
        lines * floor * LINE_HEIGHT_FACTOR * EMU_PER_PT <= height * 0.94
        and max(m[1] for m in metrics) <= width
    )


def _title_style(title: str, width: int, height: int) -> tuple[str, int, int]:
    """Fit a heading in up to three lines before shortening it."""
    max_h = int(height * 0.19)
    for size in range(28, 17, -1):
        lines = _wrap_metrics(title, size, width)[0]
        required = lines * size * LINE_HEIGHT_FACTOR * EMU_PER_PT
        if lines <= 3 and required <= max_h * 0.94:
            return title, size * 100, max(int(height * 0.1), int(required * 1.06))
    return title, int(_fitted_size(title, width, max_h, 18.0, 14.0) * 100), max_h


def _entry_height(text: str, width: int, font_pt: float) -> int | None:
    metrics = [_wrap_metrics(line, font_pt, width) for line in text.splitlines() or [text]]
    if max(measured_width for _, measured_width in metrics) > width:
        return None
    lines = sum(line_count for line_count, _ in metrics)
    return math.ceil(lines * font_pt * LINE_HEIGHT_FACTOR * EMU_PER_PT / 0.94)


def _source_column_plan(
    displays: list[str],
    text_width: int,
    available: int,
    gap: int,
) -> tuple[float, int, list[int]] | None:
    """Choose one uniform readable font and balance contiguous entries by height."""
    for step in range(10):  # 12.5 pt down to the 8 pt hard floor
        font_pt = 12.5 - 0.5 * step
        measured = [_entry_height(text, text_width, font_pt) for text in displays]
        if any(height is None for height in measured):
            continue
        heights = [int(height) for height in measured if height is not None]
        if len(heights) == 1:
            if heights[0] <= available:
                return font_pt, 1, heights
            continue
        splits = range(1, len(heights))
        split = min(
            splits,
            key=lambda at: max(
                sum(heights[:at]) + gap * (at - 1),
                sum(heights[at:]) + gap * (len(heights) - at - 1),
            ),
        )
        tallest = max(
            sum(heights[:split]) + gap * (split - 1),
            sum(heights[split:]) + gap * (len(heights) - split - 1),
        )
        if tallest <= available:
            return font_pt, split, heights
    return None


def compose_sources(
    slide_xml: bytes,
    title: str,
    entries: list[str],
    width: int,
    height: int,
    start_number: int = 1,
    numbered: bool = True,
) -> bytes:
    """Replace donor content with an editable, evenly spaced source list.

    A white content panel makes citation contrast independent of the
    arbitrary template's background; footer branding remains visible.
    """
    root = etree.fromstring(slide_xml)
    tree = root.find(f".//{P}spTree")
    if tree is None:
        return slide_xml
    for shape in list(tree.iter(P + "sp")):
        if _is_fullcanvas_artwork(shape, width, height):
            continue
        if (
            shape.find(f"{P}txBody") is not None
            or _is_interior_donor_card(shape, width, height)
        ) and shape.getparent() is not None:
            shape.getparent().remove(shape)
    for pic in list(tree.iter(P + "pic")):
        # значки без подписей — демо-оформление: без текста карточки
        # они висят чужим декором поверх источников
        if _small_content_pic(pic, width, height) and pic.getparent() is not None:
            pic.getparent().remove(pic)
    for frame in list(tree.iter(P + "graphicFrame")):
        if frame.getparent() is not None:
            frame.getparent().remove(frame)
    ids = [int(n.get("id")) for n in root.iter(P + "cNvPr") if (n.get("id") or "").isdigit()]
    sid = max(ids, default=1) + 1
    _shape(
        tree,
        sid,
        "DeckDNA bibliography panel",
        int(width * 0.025),
        int(height * 0.055),
        int(width * 0.95),
        int(height * 0.85),
        theme_fill="bg1",
    )
    sid += 1
    title_text, title_size, title_h = _title_style(title, int(width * 0.9), height)
    _shape(
        tree,
        sid,
        "DeckDNA bibliography title",
        int(width * 0.05),
        int(height * 0.09),
        int(width * 0.9),
        title_h,
        text=title_text,
        size=title_size,
    )
    sid += 1
    top = max(int(height * 0.205), int(height * 0.09) + title_h + int(height * 0.02))
    displays = [
        f"{start_number + i}. {_source_entry(entry)}" if numbered else f"• {_source_entry(entry)}"
        for i, entry in enumerate(entries)
    ]
    if not displays:
        return etree.tostring(root, encoding="UTF-8", xml_declaration=True)
    text_width = int(width * (0.9 if len(displays) == 1 else 0.425))
    gap = int(height * 0.006)
    plan = _source_column_plan(displays, text_width, int(height * 0.905) - top, gap)
    if plan is None:
        # не влезло ни в какую раскладку даже на 8 pt: одна колода важнее
        # идеальной вёрстки одного слайда — общий блок на 8 pt, текст цел,
        # переполнение честно отметит аудит (раньше падала вся генерация)
        box_w = int(width * 0.9)
        _shape(
            tree,
            sid,
            "DeckDNA source 1",
            int(width * 0.05),
            top,
            box_w,
            int(height * 0.905) - top,
            text="\n".join(displays),
            size=800,
        )
        return etree.tostring(root, encoding="UTF-8", xml_declaration=True)
    font_pt, split, heights = plan
    offsets = [top, top]
    for index, (display, text_h) in enumerate(zip(displays, heights, strict=True)):
        col = int(index >= split)
        x = int(width * (0.05 if col == 0 else 0.525))
        _shape(
            tree,
            sid,
            f"DeckDNA source {index + 1}",
            x,
            offsets[col],
            text_width,
            text_h,
            text=display,
            size=int(font_pt * 100),
        )
        offsets[col] += text_h + gap
        sid += 1
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True)


def paginate_sources(
    slide_xml: bytes,
    title: str,
    entries: list[str],
    width: int,
    height: int,
    start_number: int = 1,
    continuation_title: str | None = None,
) -> list[tuple[bytes, int]]:
    """Place every source on readable native slides, preserving order and numbering.

    Each page uses the same geometry check as ``compose_sources``. A single
    entry that cannot fit on its own still fails with the typed error; it is
    never clipped or silently removed.
    """
    if not entries:
        return [(compose_sources(slide_xml, title, [], width, height, start_number), 0)]

    pages: list[tuple[bytes, int]] = []
    offset = 0
    while offset < len(entries):
        page_title = title if not pages else (continuation_title or title)
        top_title_h = _title_style(page_title, int(width * 0.9), height)[2]
        top = max(int(height * 0.205), int(height * 0.09) + top_title_h + int(height * 0.02))
        available = int(height * 0.905) - top
        gap = int(height * 0.006)

        def fits(
            count: int,
            *,
            page_offset: int = offset,
            page_available: int = available,
            page_gap: int = gap,
        ) -> bool:
            displays = [
                f"{start_number + page_offset + i}. {_source_entry(entry)}"
                for i, entry in enumerate(entries[page_offset : page_offset + count])
            ]
            text_width = int(width * (0.9 if count == 1 else 0.425))
            return _source_column_plan(displays, text_width, page_available, page_gap) is not None

        if not fits(1):
            raise DeckDNAError(
                "composition_failed",
                "source entry does not fit at the 8pt readability floor",
                stage="composing.sources",
                details={"entry": start_number + offset},
            )
        low, high = 1, len(entries) - offset
        while low < high:
            middle = (low + high + 1) // 2
            if fits(middle):
                low = middle
            else:
                high = middle - 1
        count = low
        pages.append(
            (
                compose_sources(
                    slide_xml,
                    page_title,
                    entries[offset : offset + count],
                    width,
                    height,
                    start_number + offset,
                ),
                count,
            )
        )
        offset += count
    return pages


def compose_dense_prose(
    slide_xml: bytes, title: str, entries: list[str], width: int, height: int
) -> tuple[bytes, int]:
    """Readable fallback when a dense deterministic plan cannot fit cards."""
    return compose_sources(slide_xml, title, entries, width, height, numbered=False), 0


def compose_data_split(
    slide_xml: bytes,
    title: str,
    entries: list[str],
    width: int,
    height: int,
    *,
    mirror: bool = False,
) -> bytes:
    """Слайд с графиком/таблицей, у образца которого нет ни своей рамки
    данных, ни свободного места: заголовок, текст колонкой слева, правая
    половина пуста — туда встаёт нативный график/таблица (поиск свободного
    места в chart_fill/table_fill). Стиль — токены темы донора.

    *mirror* — зеркальная раскладка (ось варианта visual): текст справа,
    данные встают в свободную левую половину и читаются первыми."""
    # Без сплошной панели: текст ложится на собственный фон слайда
    # (панель bg1 на тёмной теме давала белое поле, а при восстановлении
    # донорского оформления снималась — текст оставался с чернилами,
    # посчитанными под панель).
    xml, _ = compose_visual_cards(slide_xml, title, [], width, height, panel=False)
    if not entries:
        return xml
    root = etree.fromstring(xml)
    tree = root.find(f".//{P}spTree")
    ids = [int(n.get("id")) for n in root.iter(P + "cNvPr") if (n.get("id") or "").isdigit()]
    sid = max(ids, default=1) + 1
    title_h = _title_style(title, int(width * 0.9), height)[2]
    top = max(int(height * 0.21), int(height * 0.09) + title_h + int(height * 0.02))
    box_w, box_h = int(width * 0.36), int(height * 0.875) - top
    text = "\n".join(f"• {e}" for e in entries)
    font_pt = _fitted_size(text, int(box_w * 0.95), int(box_h * 0.95), 16.0)
    text_x = int(width * (0.59 if mirror else 0.05))
    for pic in list(tree.iter(P + "pic")):
        xfrm = pic.find(f"{P}spPr/{A}xfrm")
        if xfrm is None:
            continue
        off, ext = xfrm.find(f"{A}off"), xfrm.find(f"{A}ext")
        if off is None or ext is None:
            continue
        try:
            px = int(off.get("x", "0"))
            pw, ph = int(ext.get("cx", "0")), int(ext.get("cy", "0"))
        except (TypeError, ValueError):
            continue
        area_ratio = (pw * ph) / max(1, width * height)
        if not (0.03 <= area_ratio < 0.75):
            continue
        if px < text_x + box_w + int(width * 0.02) and px + pw > text_x - int(width * 0.02):
            vis_x = int(width * (0.06 if mirror else 0.48))
            vis_w = int(width * 0.44)
            target_h = min(box_h, max(ph, int(height * 0.56)))
            target_w = min(vis_w, max(pw, int(target_h * 0.85)))
            off.set("x", str(vis_x + (vis_w - target_w) // 2))
            off.set("y", str(top + (box_h - target_h) // 2))
            ext.set("cx", str(target_w))
            ext.set("cy", str(target_h))
    _shape(
        tree,
        sid,
        "DeckDNA data text",
        text_x,
        top,
        box_w,
        box_h,
        text=text,
        size=int(font_pt * 100),
    )
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True)


def add_missing_title(slide_xml: bytes, title: str, width: int, height: int) -> bytes:
    """Ensure an editable heading when the donor has no title placeholder."""
    root = etree.fromstring(slide_xml)
    tree = root.find(f".//{P}spTree")
    if tree is None:
        return slide_xml
    ids = [int(n.get("id")) for n in root.iter(P + "cNvPr") if (n.get("id") or "").isdigit()]
    sid = max(ids, default=1) + 1
    _shape(
        tree,
        sid,
        "DeckDNA heading backdrop",
        int(width * 0.025),
        int(height * 0.045),
        int(width * 0.95),
        int(height * 0.12),
        theme_fill="bg1",
    )
    title_text, title_size, title_h = _title_style(title, int(width * 0.9), height)
    _shape(
        tree,
        sid + 1,
        "DeckDNA heading",
        int(width * 0.05),
        int(height * 0.065),
        int(width * 0.9),
        title_h,
        text=title_text,
        size=title_size,
    )
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True)


def compose_cover_slide(
    slide_xml: bytes,
    title: str,
    subtitle: str | None,
    kicker: str | None,
    width: int,
    height: int,
    *,
    has_image: bool = False,
) -> bytes:
    """Native cover composition when a cover slide lands on a non-title exemplar
    whose header is tiny (< 22pt)."""
    root = etree.fromstring(slide_xml)
    tree = root.find(f".//{P}spTree")
    if tree is None:
        return slide_xml
    for shape in list(tree.iter(P + "sp")):
        if _is_fullcanvas_artwork(shape, width, height):
            continue
        if (
            shape.find(f"{P}txBody") is not None
            or _is_interior_donor_card(shape, width, height)
        ) and shape.getparent() is not None:
            shape.getparent().remove(shape)
    for pic in list(tree.iter(P + "pic")):
        if _small_content_pic(pic, width, height) and pic.getparent() is not None:
            pic.getparent().remove(pic)
    for frame in list(tree.iter(P + "graphicFrame")):
        if frame.getparent() is not None:
            frame.getparent().remove(frame)
    ids = [int(n.get("id")) for n in root.iter(P + "cNvPr") if (n.get("id") or "").isdigit()]
    sid = max(ids, default=1) + 1
    left_x = int(width * 0.065)
    col_w = int(width * (0.50 if has_image else 0.84))
    cur_y = int(height * 0.22)
    if kicker and kicker.strip():
        kicker_h = int(height * 0.06)
        _shape(
            tree,
            sid,
            "DeckDNA cover kicker",
            left_x,
            cur_y,
            col_w,
            kicker_h,
            text=kicker.strip(),
            size=1400,
            text_theme="accent1",
        )
        sid += 1
        cur_y += kicker_h + int(height * 0.02)
    else:
        cur_y = int(height * 0.27)
    title_h = int(height * 0.34)
    title_pt = _fitted_size(title, int(col_w * 0.90), int(title_h * 0.88), 42.0, floor=26.0)
    _shape(
        tree,
        sid,
        "DeckDNA cover title",
        left_x,
        cur_y,
        col_w,
        title_h,
        text=title,
        size=int(round(title_pt * 100)),
        text_theme="tx1",
    )
    title_sp = tree[-1]
    for rpr in title_sp.iter(A + "rPr"):
        rpr.set("b", "1")
        for lat in rpr.findall(A + "latin"):
            lat.set("typeface", "+mj-lt")
    sid += 1
    accent_y = cur_y + title_h + int(height * 0.02)
    accent_h = max(38100, int(height * 0.008))
    _shape(
        tree,
        sid,
        "DeckDNA cover accent",
        left_x,
        accent_y,
        int(width * 0.12),
        accent_h,
        theme_fill="accent1",
    )
    sid += 1
    if subtitle and subtitle.strip():
        sub_y = accent_y + accent_h + int(height * 0.03)
        sub_h = max(int(height * 0.14), int(height * 0.86) - sub_y)
        sub_pt = _fitted_size(
            subtitle.strip(), int(col_w * 0.94), int(sub_h * 0.88), 18.0, floor=13.0
        )
        _shape(
            tree,
            sid,
            "DeckDNA cover subtitle",
            left_x,
            sub_y,
            col_w,
            sub_h,
            text=subtitle.strip(),
            size=int(round(sub_pt * 100)),
            text_theme="tx1",
        )
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True)


def compose_visual_cards(
    slide_xml: bytes,
    title: str,
    entries: list[str],
    width: int,
    height: int,
    *,
    cards: bool = True,
    compact: bool = False,
    panel: bool = True,
) -> tuple[bytes, int]:
    """Native theme-accent cards with exactly one card per content unit.

    *panel* — сплошная bg1-подложка под контент; раскладка «текст +
    данные» (compose_data_split) её не ставит: текст там лежит на
    собственном фоне слайда."""
    root = etree.fromstring(slide_xml)
    tree = root.find(f".//{P}spTree")
    if tree is None:
        return slide_xml, 0
    for shape in list(tree.iter(P + "sp")):
        if _is_fullcanvas_artwork(shape, width, height):
            continue
        if (
            shape.find(f"{P}txBody") is not None
            or _is_interior_donor_card(shape, width, height)
        ) and shape.getparent() is not None:
            shape.getparent().remove(shape)
    for pic in list(tree.iter(P + "pic")):
        # мелкие значки эталона — без подписей карточек это бессмысленный
        # декор поверх сгенерированного содержимого
        if _small_content_pic(pic, width, height) and pic.getparent() is not None:
            pic.getparent().remove(pic)
    for frame in list(tree.iter(P + "graphicFrame")):
        if frame.getparent() is not None:
            frame.getparent().remove(frame)
    ids = [int(n.get("id")) for n in root.iter(P + "cNvPr") if (n.get("id") or "").isdigit()]
    sid = max(ids, default=1) + 1
    if panel:
        _shape(
            tree,
            sid,
            "DeckDNA visual panel",
            int(width * 0.025),
            int(height * 0.055),
            int(width * 0.95),
            int(height * 0.85),
            theme_fill="bg1",
        )
        sid += 1
    title_text, title_size, title_h = _title_style(title, int(width * 0.9), height)
    _shape(
        tree,
        sid,
        "DeckDNA visual title",
        int(width * 0.05),
        int(height * 0.09),
        int(width * 0.9),
        title_h,
        text=title_text,
        size=title_size,
    )
    sid += 1
    count = len(entries)
    if not count:
        return etree.tostring(root, encoding="UTF-8", xml_declaration=True), 0
    # Long prose needs broad columns. Single statements use the full
    # canvas; a list fallback uses rows instead of manufacturing empty cards.
    cols = (
        3
        if cards and count in (3, 6) and max(map(len, entries)) <= 110
        else 2
        if count > 1 and cards
        else 1
    )
    rows = (count + cols - 1) // cols
    card_w = int(width * (0.9 if cols == 1 else 0.425 if cols == 2 else 0.28))
    start_y = max(int(height * 0.21), int(height * 0.09) + title_h + int(height * 0.02))
    gap_y = int(height * 0.035)
    card_h = (int(height * 0.875) - start_y - (rows - 1) * gap_y) // rows
    if compact:
        card_h = min(card_h, int(height * 0.13))
    clipped = 0
    for i, entry in enumerate(entries):
        col, row = i % cols, i // cols
        row_offset = 0
        x = int(width * (0.05 + row_offset + col * (0.475 if cols == 2 else 0.31)))
        y = start_y + row * (card_h + gap_y)
        if cards and count > 1:
            _shape(
                tree,
                sid,
                f"DeckDNA visual card {i + 1}",
                x,
                y,
                card_w,
                card_h,
                theme_fill="bg2",
                geometry="roundRect",
            )
            sid += 1
        _shape(
            tree,
            sid,
            f"DeckDNA visual accent {i + 1}",
            x,
            y,
            int(card_w * (0.008 if compact else 0.045)),
            card_h,
            theme_fill="accent1",
        )
        sid += 1
        display = entry
        preferred = 24.0 if compact or count == 1 else 18.0 if rows == 1 else 16.0
        font_pt = _fitted_size(display, int(card_w * 0.86), int(card_h * 0.82), preferred)
        # Use the theme's paired background/text slots. bg2 can be light
        # or dark; bg1 is another background and is not its text counterpart.
        _shape(
            tree,
            sid,
            f"DeckDNA visual text {i + 1}",
            x + int(card_w * 0.085),
            y + int(card_h * 0.09),
            int(card_w * 0.86),
            int(card_h * 0.82),
            text=display,
            size=int(font_pt * 100),
            text_theme="tx2" if (cards and count > 1) else "tx1",
        )
        sid += 1
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True), clipped


def add_slide_footnote(
    slide_xml: bytes,
    text: str,
    width: int,
    height: int,
    font_scale: tuple[float, ...] | list[float] | None = None,
) -> bytes:
    """Добавляет компактную сноску с источником внизу слайда без наложения на контент."""
    cleaned = (text or "").strip()
    if not cleaned:
        return slide_xml
    root = etree.fromstring(slide_xml)
    tree = root.find(f"{P}cSld/{P}spTree")
    if tree is None:
        return slide_xml
    for el in tree.iter(f"{P}cNvPr"):
        if (el.get("name") or "").startswith("DeckDNA source footnote"):
            return slide_xml

    fn_x = int(width * 0.05)
    fn_r = int(width * 0.82)
    fn_y = int(height * 0.93)
    for child in tree:
        xfrm = child.find(f".//{A}xfrm")
        if xfrm is None:
            continue
        off, ext = xfrm.find(f"{A}off"), xfrm.find(f"{A}ext")
        if off is None or ext is None:
            continue
        try:
            sx, sy = int(off.get("x", "0")), int(off.get("y", "0"))
            sw, sh = int(ext.get("cx", "0")), int(ext.get("cy", "0"))
        except (TypeError, ValueError):
            continue
        if sw <= 0 or sh <= 0 or sw * sh >= 0.5 * width * height:
            continue
        sb = sy + sh
        if sb < int(height * 0.88):
            continue
        if sw * sh <= 0.04 * width * height and sy >= int(height * 0.85):
            if sx + sw / 2 < width * 0.3:
                fn_x = max(fn_x, sx + sw + int(width * 0.015))
            elif sx + sw / 2 > width * 0.65:
                fn_r = min(fn_r, sx - int(width * 0.015))
        elif sb > fn_y and sb < int(height * 0.955):
            fn_y = max(fn_y, sb + int(height * 0.006))

    fn_w = max(int(width * 0.45), fn_r - fn_x)
    fn_h = max(int(height * 0.04), height - fn_y - int(height * 0.008))
    pt = 10.5
    if font_scale:
        from deckdna.pptx.composing.font_scale import snap_font_pt

        pt = snap_font_pt(10.5, font_scale, mode="up", min_pt=10.0, max_pt=12.0)
    sid = (
        max(
            (
                int(c.get("id", "1"))
                for c in root.iter(f"{P}cNvPr")
                if (c.get("id") or "").isdigit()
            ),
            default=10,
        )
        + 1
    )
    _shape(
        tree,
        sid,
        "DeckDNA source footnote",
        fn_x,
        fn_y,
        fn_w,
        fn_h,
        text=cleaned,
        size=int(round(pt * 100)),
        text_theme="tx1",
    )
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True)
