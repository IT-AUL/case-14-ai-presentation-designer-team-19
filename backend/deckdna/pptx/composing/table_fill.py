"""Structured table fill: ContentPack.Table → existing `a:tbl` in the slide.

Complements the positional slot fill (text_replace.py): when a plan
slide carries ``Kind.table`` units that resolve to real ``Table`` rows
and the chosen exemplar contains a native table, the table's
headers/rows are poured into the existing `a:tr`/`a:tc` grid — geometry
and cell styles are untouched.

Honest bounds: row growth clones the last `a:tr` and column growth
clones the last `a:gridCol` plus one `a:tc` per row (span attributes
stripped — a cloned `gridSpan`/`hMerge`/`vMerge` would corrupt the
grid); both sync the graphicFrame's declared ext so the audit sees the
true geometry. Units beyond the existing `a:tbl` count create a NEW
native table (`_add_table_grid`) placed in the largest emptied text
body of the slide — the narrow practical case, not full semantic slot
mapping; when no suitable body exists the unit drops honestly.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from lxml import etree

from deckdna.contracts.content_pack import Table

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"


@dataclass
class TableFillReport:
    """What the fill actually wrote — surfaced into the compose report."""

    tables_found: int = 0
    tables_filled: int = 0
    units_dropped: int = 0  # table units that could not be placed
    tables_created: int = 0  # new a:tbl frames built on table-less spots
    cells_written: int = 0
    rows_grown: int = 0  # a:tr rows cloned to fit the data
    cols_grown: int = 0  # a:gridCol+a:tc columns cloned to fit the data
    cols_dropped: int = 0  # cell values beyond row width (span-locked rows)
    table_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "tables_found": self.tables_found,
            "tables_filled": self.tables_filled,
            "units_dropped": self.units_dropped,
            "tables_created": self.tables_created,
            "cells_written": self.cells_written,
            "rows_grown": self.rows_grown,
            "cols_grown": self.cols_grown,
            "cols_dropped": self.cols_dropped,
            "table_ids": self.table_ids,
        }


def _cell_text(value: str | float | None) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _set_cell(tc: etree._Element, text: str) -> None:
    """Write *text* into a cell: first `a:t` of its first paragraph gets
    the value, every other run is emptied. Cells lacking a run entirely
    get a minimal `a:r/a:t` appended so the value is never lost."""
    texts = tc.findall(f".//{{{A}}}t")
    if texts:
        texts[0].text = text
        for t in texts[1:]:
            t.text = ""
        return
    tx_body = tc.find(f"{{{A}}}txBody")
    if tx_body is None:
        return
    para = tx_body.find(f"{{{A}}}p")
    if para is None:
        para = etree.SubElement(tx_body, f"{{{A}}}p")
    run = etree.Element(f"{{{A}}}r")
    t = etree.SubElement(run, f"{{{A}}}t")
    t.text = text
    end = para.find(f"{{{A}}}endParaRPr")
    if end is not None:
        para.insert(list(para).index(end), run)
    else:
        para.append(run)


def _sync_frame_height(grid: etree._Element) -> None:
    """Grow the owning graphicFrame's declared height to the sum of its
    `a:tr@h` — without it a grown table renders past the frame while
    its xfrm still declares the old size, invisible to the geometry
    audit (out_of_bounds/unintended_overlap)."""
    frame = grid.getparent()
    while frame is not None and etree.QName(frame).localname != "graphicFrame":
        frame = frame.getparent()
    if frame is None:
        return
    ext = frame.find(f"{{{P}}}xfrm/{{{A}}}ext")
    if ext is None:
        return
    height = sum(
        int(tr.get("h", "0")) for tr in grid.findall(f"{{{A}}}tr")
    )
    if height > 0:
        ext.set("cy", str(height))


_SPAN_ATTRS = ("gridSpan", "hMerge", "vMerge", "rowSpan")


def _grow_columns(grid: etree._Element, trs: list, target: int) -> int:
    """Widen the grid to *target* columns: clone the last `a:gridCol`
    (its width is reused verbatim), then pad each `a:tr` with clones of
    its last `a:tc` (span attrs stripped). Returns columns added."""
    tbl_grid = grid.find(f"{{{A}}}tblGrid")
    if tbl_grid is None:
        return 0
    grid_cols = tbl_grid.findall(f"{{{A}}}gridCol")
    if not grid_cols:
        return 0
    added = 0
    last_col = grid_cols[-1]
    while len(tbl_grid.findall(f"{{{A}}}gridCol")) < target:
        clone = copy.deepcopy(last_col)
        last_col.addnext(clone)
        last_col = clone
        added += 1
    for tr in trs:
        tcs = tr.findall(f"{{{A}}}tc")
        if not tcs:
            continue
        last_tc = tcs[-1]
        while len(tr.findall(f"{{{A}}}tc")) < target:
            clone = copy.deepcopy(last_tc)
            for attr in _SPAN_ATTRS:
                clone.attrib.pop(attr, None)
            last_tc.addnext(clone)
            last_tc = clone
    return added


_MERGE_ATTRS = ("gridSpan", "rowSpan", "hMerge", "vMerge")


def _shrink_grid(grid: etree._Element, trs: list, n_cols: int, n_rows: int) -> list:
    """Удалить хвостовые колонки/строки донорской таблицы сверх данных.

    Сохраняет общую ширину: освободившаяся ширина делится между
    оставшимися колонками пропорционально. Таблицы с объединёнными
    ячейками не трогаются — удаление столбца разрезало бы объединение."""
    if any(tc.get(a) for tr in trs for tc in tr.findall(f"{{{A}}}tc") for a in _MERGE_ATTRS):
        return trs
    tbl_grid = grid.find(f"{{{A}}}tblGrid")
    cols = tbl_grid.findall(f"{{{A}}}gridCol") if tbl_grid is not None else []
    if n_cols >= 1 and len(cols) > n_cols:
        total = sum(int(c.get("w", "0")) for c in cols)
        for col in cols[n_cols:]:
            tbl_grid.remove(col)
        for tr in trs:
            for tc in tr.findall(f"{{{A}}}tc")[n_cols:]:
                tr.remove(tc)
        kept = cols[:n_cols]
        kept_total = sum(int(c.get("w", "0")) for c in kept) or 1
        for col in kept:
            col.set("w", str(int(int(col.get("w", "0")) * total / kept_total)))
    if n_rows >= 1 and len(trs) > n_rows:
        for tr in trs[n_rows:]:
            tr.getparent().remove(tr)
        trs = trs[:n_rows]
        _sync_frame_height(grid)
    return trs


def _sync_frame_width(grid: etree._Element) -> None:
    """Grow the owning graphicFrame's declared width to the sum of its
    `a:gridCol@w` — the column-growth counterpart of
    `_sync_frame_height`."""
    frame = grid.getparent()
    while frame is not None and etree.QName(frame).localname != "graphicFrame":
        frame = frame.getparent()
    if frame is None:
        return
    ext = frame.find(f"{{{P}}}xfrm/{{{A}}}ext")
    if ext is None:
        return
    width = sum(
        int(col.get("w", "0"))
        for col in grid.findall(f"{{{A}}}tblGrid/{{{A}}}gridCol")
    )
    if width > 0:
        ext.set("cx", str(width))


# Minimal native-table construction (the narrow add_table case):
# a new p:graphicFrame is placed into the bounding box of the largest
# emptied text body — the slot that positional fill left without
# content. Not semantic slot mapping: no layout solving, the table
# simply takes the room the cleared body vacated.
_ROW_H_EMU = 370840  # ~0.4" — python-pptx default row height
_MIN_HOST_W_EMU = 1_500_000  # ~1.6"
_MIN_HOST_H_EMU = 250_000  # ~0.27" — anchor slot; rows may extend past it.
# Found live (29.09) on vk_tech_template: a real cleared body placeholder
# (285750 EMU, ~0.31") legitimately qualified as the only anchor host for
# a title+table slide but was rejected a hair under the previous 300_000
# threshold -- the row height it anchors (_ROW_H_EMU=370840 per row) is
# already far taller than either value, so the threshold only needs to
# filter out degenerate slivers, not approximate the final table size.
_TABLE_STYLE_ID = "{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}"  # Medium Style 2
_NON_HOST_PH = ("title", "ctrTitle", "subTitle", "sldNum", "ftr", "dt", "hdr")


def _next_shape_id(root: etree._Element) -> int:
    ids = [
        int(el.get("id", "0"))
        for el in root.iter(f"{{{P}}}cNvPr")
        if (el.get("id") or "0").isdigit()
    ]
    return max(ids, default=0) + 1


def _filled_content_boxes(root: etree._Element) -> list[tuple[int, int, int, int]]:
    """(x, y, cx, cy) of every shape that still carries real text after
    ``replace_text_runs`` -- by the time ``_add_table_grid`` runs, a
    donor's own small caption shapes (e.g. numbered feature-card labels)
    already hold real content-unit text, not stock copy."""
    boxes = []
    for sp in root.iter(f"{{{P}}}sp"):
        tx = sp.find(f"{{{P}}}txBody")
        if tx is None or not any((t.text or "").strip() for t in tx.iter(f"{{{A}}}t")):
            continue
        xfrm = sp.find(f"{{{P}}}spPr/{{{A}}}xfrm")
        if xfrm is None:
            continue
        off = xfrm.find(f"{{{A}}}off")
        ext = xfrm.find(f"{{{A}}}ext")
        if off is None or ext is None:
            continue
        try:
            boxes.append((
                int(off.get("x", "0")), int(off.get("y", "0")),
                int(ext.get("cx", "0")), int(ext.get("cy", "0")),
            ))
        except ValueError:
            continue
    return boxes


def _overlaps_filled_content(
    box: tuple[int, int, int, int], filled: list[tuple[int, int, int, int]]
) -> bool:
    """True when *box* meaningfully overlaps a shape that already holds
    real text -- more than a third of the SMALLER area, so a large
    backdrop host merely touching a caption's edge still qualifies."""
    bx, by, bw, bh = box
    for fx, fy, fw, fh in filled:
        ix = max(0, min(bx + bw, fx + fw) - max(bx, fx))
        iy = max(0, min(by + bh, fy + fh) - max(by, fy))
        if ix <= 0 or iy <= 0:
            continue
        overlap = ix * iy
        if overlap > min(bw * bh, fw * fh) * 0.33:
            return True
    return False


def _empty_body_hosts(root: etree._Element) -> list[etree._Element]:
    """`p:sp` shapes whose txBody is fully empty (slot cleared by
    replace_text_runs, or stock-empty), with an own xfrm, sorted by
    area descending. Titles/footer placeholders never qualify.

    Found live (28.09): a donor's big empty background panel behind a
    small "N numbered feature cards" group looked like an ideal host by
    size alone -- but a synthesized table filling it collided with the
    cards' own real (filled) caption text sitting on top of it,
    producing an unreadable overlap. A host is disqualified when it
    substantially overlaps a shape that already carries real text --
    that space is visually claimed, size alone does not make it free.
    """
    filled = _filled_content_boxes(root)
    hosts = []
    for sp in root.iter(f"{{{P}}}sp"):
        ph = sp.find(f"{{{P}}}nvSpPr/{{{P}}}nvPr/{{{P}}}ph")
        if ph is not None and (ph.get("type") or "body") in _NON_HOST_PH:
            continue
        tx = sp.find(f"{{{P}}}txBody")
        if tx is None:
            continue
        if any((t.text or "").strip() for t in tx.iter(f"{{{A}}}t")):
            continue
        xfrm = sp.find(f"{{{P}}}spPr/{{{A}}}xfrm")
        if xfrm is None:
            continue
        off = xfrm.find(f"{{{A}}}off")
        ext = xfrm.find(f"{{{A}}}ext")
        if off is None or ext is None:
            continue
        w, h = int(ext.get("cx", "0")), int(ext.get("cy", "0"))
        if w < _MIN_HOST_W_EMU or h < _MIN_HOST_H_EMU:
            continue
        x, y = int(off.get("x", "0")), int(off.get("y", "0"))
        if _overlaps_filled_content((x, y, w, h), filled):
            continue
        hosts.append((w * h, x, y, w, h, sp))
    hosts.sort(key=lambda h: -h[0])
    return hosts


def _build_table_frame(
    shape_id: int, x: int, y: int, cx: int, n_rows: int, n_cols: int
) -> etree._Element:
    """A standalone `p:graphicFrame` carrying a bare `a:tbl` grid —
    same XML python-pptx `add_table` emits (Medium Style 2, bandRow)."""
    col_w = max(1, cx // n_cols)
    cy = n_rows * _ROW_H_EMU
    cell = '<a:tc><a:txBody><a:bodyPr/><a:p/></a:txBody><a:tcPr/></a:tc>'
    row = f'<a:tr h="{_ROW_H_EMU}">' + cell * n_cols + "</a:tr>"
    cols = f'<a:gridCol w="{col_w}"/>' * n_cols
    xml = (
        f'<p:graphicFrame xmlns:p="{P}" xmlns:a="{A}">'
        f'<p:nvGraphicFramePr><p:cNvPr id="{shape_id}" '
        f'name="Table {shape_id}"/><p:cNvGraphicFramePr>'
        f'<a:graphicFrameLocks noGrp="1"/></p:cNvGraphicFramePr>'
        f"<p:nvPr/></p:nvGraphicFramePr>"
        f'<p:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/>'
        f"</p:xfrm>"
        f'<a:graphic><a:graphicData uri="http://schemas.openxmlformats.'
        f'org/drawingml/2006/table"><a:tbl><a:tblPr firstRow="1" '
        f'bandRow="1"><a:tableStyleId>{_TABLE_STYLE_ID}</a:tableStyleId>'
        f"</a:tblPr><a:tblGrid>{cols}</a:tblGrid>{row * n_rows}"
        f"</a:tbl></a:graphicData></a:graphic></p:graphicFrame>"
    )
    return etree.fromstring(xml)


def _add_table_grid(
    root: etree._Element,
    table: Table,
    used_hosts: set[tuple[int, int, int, int]],
    slide_size: tuple[int, int] | None = None,
) -> etree._Element | None:
    """Create a new `a:tbl` for *table* inside the largest emptied text
    body of the slide. Returns the `a:tbl` element, or None when no
    suitable host exists (unit then drops honestly).

    Found live (28.09): a host can pass ``_empty_body_hosts`` (its own
    declared box doesn't overlap filled content) and still collide,
    because the table's actual built height is ``n_rows * _ROW_H_EMU``
    -- almost always taller than a small caption-style host's own
    original height. A host is picked only when the table's REAL,
    post-build box (host's x/y/width, projected height) still clears
    every already-filled sibling -- not the host's own tiny declared
    size, which said nothing about what the table would grow into.
    """
    n_rows = (1 if table.headers else 0) + len(table.rows)
    widths = [len(table.headers)] + [len(r) for r in table.rows]
    n_cols = max(widths, default=0)
    if n_rows < 1 or n_cols < 1:
        return None
    sp_tree = root.find(f"{{{P}}}cSld/{{{P}}}spTree")
    if sp_tree is None:
        return None
    projected_h = n_rows * _ROW_H_EMU
    filled = _filled_content_boxes(root)
    host = None
    for candidate in _empty_body_hosts(root):
        _, cx0, cy0, cw, ch, _sp = candidate
        if candidate[1:5] in used_hosts:
            continue
        if slide_size is not None:
            slide_w, slide_h = slide_size
            if cx0 < 0 or cy0 < 0 or cx0 + cw > slide_w or cy0 + projected_h > slide_h:
                continue
        box = (cx0, cy0, cw, max(ch, projected_h))
        if _overlaps_filled_content(box, filled):
            continue
        host = candidate
        break
    if host is None:
        return None
    _, x, y, cx, _h, sp = host
    # host-ключ — бокс (x, y, cx, cy), не id(sp): координаты переживают
    # клонирование слайда, поэтому набор работает и на post-clone фазе
    used_hosts.add(host[1:5])
    frame = _build_table_frame(
        _next_shape_id(root), x, y, cx, n_rows, n_cols
    )
    sp_tree.append(frame)
    return next(frame.iter(f"{{{A}}}tbl"))


def fill_native_tables(
    slide_xml: bytes,
    tables: list[Table],
    used_hosts: set[tuple[int, int, int, int]] | None = None,
    slide_size: tuple[int, int] | None = None,
) -> tuple[bytes, TableFillReport]:
    """Pour *tables* into the slide's `a:tbl` grids, in document order.

    Row 0 of each grid receives ``headers``; the following rows receive
    ``rows`` in order. Rows/columns beyond the grid grow it by cloning
    the last `a:tr`/`a:gridCol`+`a:tc` (structure/styles preserved) and
    the frame ext is synced. Units beyond the slide's grids create a
    new `a:tbl` in the largest emptied text body (`_add_table_grid`);
    when no host body exists the unit drops.
    """
    report = TableFillReport()
    if not tables:
        return slide_xml, report
    root = etree.fromstring(slide_xml)
    grids = list(root.iter(f"{{{A}}}tbl"))
    report.tables_found = len(grids)

    if used_hosts is None:
        used_hosts = set()
    grid_iter = iter(grids)
    for table in tables:
        grid = next(grid_iter, None)
        if grid is None:
            grid = _add_table_grid(root, table, used_hosts, slide_size)
            if grid is None:
                report.units_dropped += 1
                continue
            report.tables_created += 1
        trs = grid.findall(f"{{{A}}}tr")
        if not trs:
            continue
        lines: list[list[str]] = []
        if table.headers:
            lines.append([_cell_text(h) for h in table.headers])
        lines.extend([_cell_text(v) for v in row] for row in table.rows)
        grown = len(lines) - len(trs)
        for _ in range(grown):
            clone = copy.deepcopy(trs[-1])
            trs[-1].addnext(clone)
            trs.append(clone)
            report.rows_grown += 1
        if grown > 0:
            _sync_frame_height(grid)
        max_cols = max(len(v) for v in lines)
        cur_cols = max(len(tr.findall(f"{{{A}}}tc")) for tr in trs)
        if max_cols > cur_cols:
            report.cols_grown += _grow_columns(grid, trs, max_cols)
            _sync_frame_width(grid)
        elif max_cols < cur_cols or len(lines) < len(trs):
            # донор шире/длиннее данных (расписание на 15 колонок под
            # таблицу из 5): пустые хвостовые колонки и строки убираются,
            # ширина делится между оставшимися колонками
            trs = _shrink_grid(grid, trs, max_cols, len(lines))
        for tr, values in zip(trs, lines[: len(trs)], strict=False):
            tcs = tr.findall(f"{{{A}}}tc")
            report.cols_dropped += max(0, len(values) - len(tcs))
            for tc, text in zip(tcs, values[: len(tcs)], strict=False):
                _set_cell(tc, text)
                report.cells_written += 1 if text else 0
        report.tables_filled += 1
        report.table_ids.append(table.id)

    report.units_dropped = len(tables) - report.tables_filled
    if report.tables_filled == 0:
        return slide_xml, report
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8"), report


def remove_empty_table_frames(slide_xml: bytes) -> tuple[bytes, int]:
    """Remove donor table grids whose stock cell copy was cleared.

    A slide with no matching table unit must not show an empty grid. Filled
    tables stay intact, including intentionally blank individual cells.
    """
    root = etree.fromstring(slide_xml)
    removed = 0
    for frame in list(root.iter(f"{{{P}}}graphicFrame")):
        grid = frame.find(f".//{{{A}}}tbl")
        if grid is None or any((t.text or "").strip() for t in grid.iter(f"{{{A}}}t")):
            continue
        parent = frame.getparent()
        if parent is not None:
            parent.remove(frame)
            removed += 1
    if not removed:
        return slide_xml, 0
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8"), removed
