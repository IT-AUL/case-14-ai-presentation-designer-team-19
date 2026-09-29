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
    return title.casefold().strip().startswith((
        "references", "bibliography", "источники", "список источников", "литература"
    ))


def _source_entry(text: str) -> str:
    """Keep the bibliographic item, omit planner-style lead-ins."""
    cleaned = re.sub(
        r"^(?:источники\s+включают|использован\s+источник:|источник\s+доступен\s+по\s+ссылке:|sources\s+include)\s*",
        "", text.strip(), flags=re.IGNORECASE,
    )
    # Some weak-model source summaries leak conversational markers into the
    # bibliography ("См domain.org", "Title: есть domain.org").  They are
    # not evidence and make the rendered source list look like parser debug
    # output.  Keep the title/URL while removing only the marker forms.
    cleaned = re.sub(r"^см\.?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r":\s*(?:есть|is)\s+", ": ", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _shape(tree, sid: int, name: str, x: int, y: int, w: int, h: int,
           text: str | None = None, size: int = 1450, fill: str | None = None,
           theme_fill: str | None = None, geometry: str = "rect",
           tint: int | None = None, text_theme: str = "tx1"):
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
        etree.SubElement(body, A + "bodyPr", wrap="square", anchor="t",
                         lIns="0", rIns="0", tIns="0", bIns="0")
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


def _fitted_size(text: str, width: int, height: int, preferred: float,
                 floor: float = 10.0) -> float:
    """Fit the whole text to physical geometry, never to a character quota.

    At the readability floor retain the complete text; a genuine excess
    remains visible to the normal overflow audit, rather than being hidden
    by a fabricated ellipsis. Measure hard line breaks separately.
    """
    size = preferred
    while size >= floor:
        metrics = [_wrap_metrics(line, size, width) for line in text.splitlines() or [text]]
        lines = sum(m[0] for m in metrics)
        if (lines * size * LINE_HEIGHT_FACTOR * EMU_PER_PT <= height * .94
                and max(m[1] for m in metrics) <= width):
            return size
        size -= .5
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
    max_h = int(height * .19)
    for size in range(28, 17, -1):
        lines = _wrap_metrics(title, size, width)[0]
        required = lines * size * LINE_HEIGHT_FACTOR * EMU_PER_PT
        if lines <= 3 and required <= max_h * .94:
            return title, size * 100, max(int(height*.1), int(required*1.06))
    return title, int(_fitted_size(title, width, max_h, 18.0, 14.0)*100), max_h


def _entry_height(text: str, width: int, font_pt: float) -> int | None:
    metrics = [_wrap_metrics(line, font_pt, width) for line in text.splitlines() or [text]]
    if max(measured_width for _, measured_width in metrics) > width:
        return None
    lines = sum(line_count for line_count, _ in metrics)
    return math.ceil(lines * font_pt * LINE_HEIGHT_FACTOR * EMU_PER_PT / .94)


def _source_column_plan(
    displays: list[str], text_width: int, available: int, gap: int,
) -> tuple[float, int, list[int]] | None:
    """Choose one uniform readable font and balance contiguous entries by height."""
    for step in range(10):  # 12.5 pt down to the 8 pt hard floor
        font_pt = 12.5 - .5 * step
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


def compose_sources(slide_xml: bytes, title: str, entries: list[str],
                    width: int, height: int, start_number: int = 1,
                    numbered: bool = True) -> bytes:
    """Replace donor content with an editable, evenly spaced source list.

    A white content panel makes citation contrast independent of the
    arbitrary template's background; footer branding remains visible.
    """
    root = etree.fromstring(slide_xml)
    tree = root.find(f".//{P}spTree")
    if tree is None:
        return slide_xml
    for shape in list(tree.iter(P + "sp")):
        if shape.find(f"{P}txBody") is not None and shape.getparent() is not None:
            shape.getparent().remove(shape)
    for frame in list(tree.iter(P + "graphicFrame")):
        if frame.getparent() is not None:
            frame.getparent().remove(frame)
    ids = [int(n.get("id")) for n in root.iter(P + "cNvPr") if (n.get("id") or "").isdigit()]
    sid = max(ids, default=1) + 1
    _shape(tree, sid, "DeckDNA bibliography panel", int(width*.025), int(height*.055),
           int(width*.95), int(height*.85), theme_fill="bg1")
    sid += 1
    title_text, title_size, title_h = _title_style(title, int(width*.9), height)
    _shape(tree, sid, "DeckDNA bibliography title", int(width*.05), int(height*.09),
           int(width*.9), title_h, text=title_text, size=title_size)
    sid += 1
    top = max(int(height*.205), int(height*.09) + title_h + int(height*.02))
    displays = [
        f"{start_number+i}. {_source_entry(entry)}" if numbered
        else f"• {_source_entry(entry)}"
        for i, entry in enumerate(entries)
    ]
    if not displays:
        return etree.tostring(root, encoding="UTF-8", xml_declaration=True)
    text_width = int(width * (.9 if len(displays) == 1 else .425))
    gap = int(height * .006)
    plan = _source_column_plan(displays, text_width, int(height*.905) - top, gap)
    if plan is None:
        raise DeckDNAError(
            "composition_failed",
            "source entries do not fit at the 8pt readability floor; "
            "paginate the sources slide",
            stage="composing.sources",
            details={"entries": len(entries)},
        )
    font_pt, split, heights = plan
    offsets = [top, top]
    for index, (display, text_h) in enumerate(zip(displays, heights, strict=True)):
        col = int(index >= split)
        x = int(width * (.05 if col == 0 else .525))
        _shape(tree, sid, f"DeckDNA source {index+1}", x,
               offsets[col], text_width, text_h,
               text=display, size=int(font_pt*100))
        offsets[col] += text_h + gap
        sid += 1
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True)


def compose_dense_prose(slide_xml: bytes, title: str, entries: list[str],
                        width: int, height: int) -> tuple[bytes, int]:
    """Readable fallback when a dense deterministic plan cannot fit cards."""
    return compose_sources(
        slide_xml, title, entries, width, height, numbered=False
    ), 0


def add_missing_title(slide_xml: bytes, title: str, width: int, height: int) -> bytes:
    """Ensure an editable heading when the donor has no title placeholder."""
    root = etree.fromstring(slide_xml)
    tree = root.find(f".//{P}spTree")
    if tree is None:
        return slide_xml
    ids = [int(n.get("id")) for n in root.iter(P + "cNvPr") if (n.get("id") or "").isdigit()]
    sid = max(ids, default=1) + 1
    _shape(tree, sid, "DeckDNA heading backdrop", int(width*.025), int(height*.045),
           int(width*.95), int(height*.12), theme_fill="bg1")
    title_text, title_size, title_h = _title_style(title, int(width*.9), height)
    _shape(tree, sid+1, "DeckDNA heading", int(width*.05), int(height*.065),
           int(width*.9), title_h, text=title_text, size=title_size)
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True)


def compose_visual_cards(slide_xml: bytes, title: str, entries: list[str],
                         width: int, height: int, *, cards: bool = True) -> tuple[bytes, int]:
    """Native theme-accent cards with exactly one card per content unit."""
    root = etree.fromstring(slide_xml)
    tree = root.find(f".//{P}spTree")
    if tree is None:
        return slide_xml, 0
    for shape in list(tree.iter(P + "sp")):
        if shape.find(f"{P}txBody") is not None and shape.getparent() is not None:
            shape.getparent().remove(shape)
    for frame in list(tree.iter(P + "graphicFrame")):
        if frame.getparent() is not None:
            frame.getparent().remove(frame)
    ids = [int(n.get("id")) for n in root.iter(P + "cNvPr") if (n.get("id") or "").isdigit()]
    sid = max(ids, default=1) + 1
    _shape(tree, sid, "DeckDNA visual panel", int(width*.025), int(height*.055),
           int(width*.95), int(height*.85), theme_fill="bg1")
    sid += 1
    title_text, title_size, title_h = _title_style(title, int(width*.9), height)
    _shape(tree, sid, "DeckDNA visual title", int(width*.05), int(height*.09),
           int(width*.9), title_h, text=title_text, size=title_size)
    sid += 1
    count = len(entries)
    if not count:
        return etree.tostring(root, encoding="UTF-8", xml_declaration=True), 0
    # Long prose needs broad columns. Single statements use the full
    # canvas; a list fallback uses rows instead of manufacturing empty cards.
    cols = (3 if count in (3, 6) and max(map(len, entries)) <= 110
            else 2 if count > 1 and cards else 1)
    rows = (count + cols - 1) // cols
    card_w = int(width * (.9 if cols == 1 else .425 if cols == 2 else .28))
    start_y = max(int(height*.21), int(height*.09) + title_h + int(height*.02))
    gap_y = int(height * .035)
    card_h = (int(height*.875) - start_y - (rows-1)*gap_y) // rows
    clipped = 0
    for i, entry in enumerate(entries):
        col, row = i % cols, i // cols
        row_offset = 0
        x = int(width * (.05 + row_offset + col * (.475 if cols == 2 else .31)))
        y = start_y + row * (card_h + gap_y)
        if cards and count > 1:
            _shape(tree, sid, f"DeckDNA visual card {i+1}", x, y, card_w, card_h,
                   theme_fill="bg2", geometry="roundRect")
            sid += 1
        _shape(tree, sid, f"DeckDNA visual accent {i+1}", x, y,
               int(card_w*.045), card_h, theme_fill="accent1")
        sid += 1
        display = entry
        preferred = 24.0 if count == 1 else 18.0 if rows == 1 else 16.0
        font_pt = _fitted_size(display, int(card_w*.86), int(card_h*.82), preferred)
        # Found live (28.09): the card itself fills with "bg2" -- a dark
        # accent tone in most themes (clrMap usually routes both bg2 and
        # tx1 to the theme's two DARK slots, dk2/dk1) -- while this text
        # always used "tx1". Dark card + dark text is unreadable
        # regardless of which template supplied the theme; the card's
        # own light theme counterpart ("bg1") reads correctly on it.
        _shape(tree, sid, f"DeckDNA visual text {i+1}", x+int(card_w*.085),
               y+int(card_h*.09), int(card_w*.86), int(card_h*.82),
               text=display, size=int(font_pt*100),
               text_theme="bg1" if (cards and count > 1) else "tx1")
        sid += 1
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True), clipped
