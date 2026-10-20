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
import math
import re
from dataclasses import dataclass, field

from lxml import etree

from deckdna.analysis.table_columns import column_indices, column_note
from deckdna.audit.basic import EMU_PER_PT, LINE_HEIGHT_FACTOR, _wrap_metrics
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
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        # крупные числа — с разделителями тысяч по-русски («44 862»),
        # мелкие дроби — без округления до десятых
        from deckdna.analysis.values import fmt_number

        if float(value).is_integer() or abs(value) >= 100:
            return fmt_number(float(value))
        return f"{value:g}".replace(".", ",")
    text = str(value)
    parsed = _numeric_text(text)
    if parsed and len(re.sub(r"\D", "", text.split(",")[0])) >= 5:
        # «44862» из CSV — тоже число: разряды пробелами, как у float.
        # 4-разрядные (годы, артикулы) не трогаем.
        from deckdna.analysis.values import fmt_number, parse_number

        num = parse_number(text)
        if num is not None and not num.percent and not num.currency:
            return fmt_number(num.value)
    return text


def _numeric_text(text: str) -> bool:
    """Ячейка — число (с пробелами тысяч, запятой, %, ₽): её выравнивают
    вправо, чтобы разряды стояли столбцом."""
    if not text.strip():
        return False
    from deckdna.analysis.values import parse_number

    return parse_number(text) is not None


# Standalone grids built by ``_add_table_grid`` carry no donor cell
# style: without an explicit size the text renders at the deck default
# (18pt) and wrapped cells grow rows past the slide bottom — the audit
# flags it ``layout.out_of_bounds`` (ADR-031). Pick the largest size
# whose estimated rendered height still fits the host's vertical room.
_CELL_INSET_LR = 91_440  # a:tcPr lIns/rIns по умолчанию (0.1")
_CELL_INSET_TB = 45_720  # a:tcPr tIns/bIns по умолчанию (0.05")
_TABLE_BOTTOM_GAP = 0.02  # доля высоты слайда — поле под рамкой
_MIN_COL_W_EMU = 1_800_000
_MAX_TABLE_COLS = 4
_TABLE_FONT_LADDER = (18.0, 16.0, 14.0, 13.0, 12.0, 11.0, 10.5)
_TABLE_NOTE_H = 600_000


def _allowed_columns(cx: int, n_cols: int) -> int:
    # В рамке уже двух минимальных колонок формула max(2, ...) невыполнима:
    # оставляем одну колонку; новые таблицы в такие узкие хосты не ставим.
    return min(n_cols, _MAX_TABLE_COLS, max(1, cx // _MIN_COL_W_EMU))


def _estimated_grid_height(grid: etree._Element, size_pt: float) -> float:
    """Высота сетки при кегле *size_pt*: заявленные ``a:tr@h``, раздутые
    переносами текста ячеек — та же модель, что у ``layout.out_of_bounds``."""
    widths = [int(gc.get("w", "0")) for gc in grid.findall(f"{{{A}}}tblGrid/{{{A}}}gridCol")]
    total = 0.0
    for tr in grid.findall(f"{{{A}}}tr"):
        row_h = float(tr.get("h", "0"))
        for tc, col_w in zip(tr.findall(f"{{{A}}}tc"), widths, strict=False):
            text = "".join(t.text or "" for t in tc.iter(f"{{{A}}}t"))
            usable = col_w - 2 * _CELL_INSET_LR
            if not text.strip() or usable <= 0:
                continue
            lines = 0
            for hard in re.split(r"[\v\n]", text):
                wrapped, widest = _wrap_metrics(hard, size_pt, usable)
                lines += max(wrapped, math.ceil(widest / usable))
            row_h = max(
                row_h,
                2 * _CELL_INSET_TB + lines * size_pt * LINE_HEIGHT_FACTOR * EMU_PER_PT,
            )
        total += row_h
    return total


def _stamp_table_font(grid: etree._Element, size_pt: float) -> None:
    """``a:pPr/a:defRPr@sz`` на каждом параграфе ячейки: раны без своего
    ``a:rPr`` наследуют его — и рендер, и кегельный учёт аудита видят
    выбранный размер."""
    sz = str(int(round(size_pt * 100)))
    for para in grid.iter(f"{{{A}}}p"):
        ppr = para.find(f"{{{A}}}pPr")
        if ppr is None:
            ppr = para.makeelement(f"{{{A}}}pPr", {})
            para.insert(0, ppr)
        defrpr = ppr.find(f"{{{A}}}defRPr")
        if defrpr is None:
            defrpr = etree.SubElement(ppr, f"{{{A}}}defRPr")
        defrpr.set("sz", sz)
        ppr.set("latinLnBrk", "0")
        for rpr in para.iter(f"{{{A}}}rPr"):
            rpr.set("sz", sz)
        for rpr in para.iter(f"{{{A}}}endParaRPr"):
            rpr.set("sz", sz)


def _fit_table_font(grid: etree._Element, slide_size: tuple[int, int]) -> None:
    """Кегль новой таблицы — наибольший, при котором раздутая переносами
    сетка не вылезает за низ слайда (фрейм уже стоит в дереве)."""
    frame = next(
        (a for a in grid.iterancestors() if etree.QName(a).localname == "graphicFrame"),
        None,
    )
    off = frame.find(f"{{{P}}}xfrm/{{{A}}}off") if frame is not None else None
    if off is None:
        return
    _slide_w, slide_h = slide_size
    avail = slide_h - int(off.get("y", "0")) - int(slide_h * _TABLE_BOTTOM_GAP)
    if avail <= 0:
        return
    for size in _TABLE_FONT_LADDER:
        widths = [int(c.get("w", "0")) for c in grid.findall(f"{{{A}}}tblGrid/{{{A}}}gridCol")]
        words_fit = all(
            _wrap_metrics("".join(tc.itertext()), size, width - 2 * _CELL_INSET_LR)[1]
            <= width - 2 * _CELL_INSET_LR
            for tr in grid.findall(f"{{{A}}}tr")
            for tc, width in zip(tr.findall(f"{{{A}}}tc"), widths, strict=False)
        )
        if words_fit and _estimated_grid_height(grid, size) <= avail:
            _stamp_table_font(grid, size)
            return
    _stamp_table_font(grid, _TABLE_FONT_LADDER[-1])


def _set_cell(tc: etree._Element, text: str, *, header: bool = False) -> None:
    """Write *text* into a cell: first `a:t` of its first paragraph gets
    the value, every other run is emptied. Cells lacking a run entirely
    get a minimal `a:r/a:t` appended so the value is never lost.

    Числовые ячейки данных выравниваются вправо (``a:pPr/@algn="r"``):
    разряды встают столбцом, финансовые строки читаются. Заголовки и
    текстовые ячейки наследуют выравнивание донора."""
    numeric = not header and _numeric_text(text)
    texts = tc.findall(f".//{{{A}}}t")
    if texts:
        texts[0].text = text
        for t in texts[1:]:
            t.text = ""
        if numeric:
            _align_right(tc)
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
    if numeric:
        _align_right(tc)


def _align_right(tc: etree._Element) -> None:
    """``algn="r"`` на параграфах ячейки с текстом (pPr — первым ребёнком
    ``a:p``, как требует схема)."""
    tx_body = tc.find(f"{{{A}}}txBody")
    if tx_body is None:
        return
    for para in tx_body.findall(f"{{{A}}}p"):
        if not any((t.text or "").strip() for t in para.iter(f"{{{A}}}t")):
            continue
        ppr = para.find(f"{{{A}}}pPr")
        if ppr is None:
            ppr = etree.Element(f"{{{A}}}pPr")
            para.insert(0, ppr)
        ppr.set("algn", "r")


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
    height = sum(int(tr.get("h", "0")) for tr in grid.findall(f"{{{A}}}tr"))
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
    width = sum(int(col.get("w", "0")) for col in grid.findall(f"{{{A}}}tblGrid/{{{A}}}gridCol"))
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
            boxes.append(
                (
                    int(off.get("x", "0")),
                    int(off.get("y", "0")),
                    int(ext.get("cx", "0")),
                    int(ext.get("cy", "0")),
                )
            )
        except ValueError:
            continue
    return boxes


def _overlaps_filled_content(
    box: tuple[int, int, int, int], filled: list[tuple[int, int, int, int]]
) -> bool:
    """True when *box* meaningfully overlaps a shape that already holds
    real text -- more than a quarter of the SMALLER area, matching the
    default overlap audit. A large
    backdrop host merely touching a caption's edge still qualifies."""
    bx, by, bw, bh = box
    for fx, fy, fw, fh in filled:
        ix = max(0, min(bx + bw, fx + fw) - max(bx, fx))
        iy = max(0, min(by + bh, fy + fh) - max(by, fy))
        if ix <= 0 or iy <= 0:
            continue
        overlap = ix * iy
        if overlap > min(bw * bh, fw * fh) * 0.25:
            return True
    return False


_FREE_GRID = (48, 27)
_FREE_MARGIN = 0.04
_VISIBLE_FILLS = ("solidFill", "gradFill", "pattFill", "blipFill")


def _el_box(el: etree._Element) -> tuple[int, int, int, int] | None:
    tag = etree.QName(el).localname
    if tag == "graphicFrame":
        xfrm = el.find(f"{{{P}}}xfrm")
    elif tag == "grpSp":
        xfrm = el.find(f"{{{P}}}grpSpPr/{{{A}}}xfrm")
    else:
        xfrm = el.find(f"{{{P}}}spPr/{{{A}}}xfrm")
    off = xfrm.find(f"{{{A}}}off") if xfrm is not None else None
    ext = xfrm.find(f"{{{A}}}ext") if xfrm is not None else None
    if off is None or ext is None:
        return None
    try:
        return int(off.get("x")), int(off.get("y")), int(ext.get("cx")), int(ext.get("cy"))
    except (TypeError, ValueError):
        return None


def _visible(el: etree._Element) -> bool:
    tag = etree.QName(el).localname
    if tag != "sp":
        return True
    if any((t.text or "").strip() for t in el.iter(f"{{{A}}}t")):
        return True
    sp_pr = el.find(f"{{{P}}}spPr")
    return sp_pr is not None and any(
        sp_pr.find(f"{{{A}}}{fill}") is not None for fill in _VISIBLE_FILLS
    )


def _free_space_host(
    root: etree._Element,
    slide_w: int,
    slide_h: int,
    ignore: frozenset[int] = frozenset(),
    extra_busy: tuple[tuple[int, int, int, int], ...] = (),
    title_band: int | None = None,
) -> tuple[int, int, int, int, int, None] | None:
    """Самый большой свободный прямоугольник слайда ниже заголовка: ни
    одна видимая фигура (текст, заливка, картинка, группа, линия) его не
    касается. Геометрический инвариант для любого шаблона: график или
    таблица встают на пустое место, а не отбрасываются, когда у образца
    нет своей рамки.

    *ignore* — ``id()`` фигур, которые вызывающий готов убрать (ADR-031 §5:
    оформление опустевших карточек под схемой), — они не занимают место."""
    tree = root.find(f"{{{P}}}cSld/{{{P}}}spTree")
    if tree is None or slide_w <= 0 or slide_h <= 0:
        return None
    cols, rows = _FREE_GRID
    cw, rh = slide_w / cols, slide_h / rows
    busy = [[False] * cols for _ in range(rows)]
    mx, my = int(cols * _FREE_MARGIN) + 1, int(rows * _FREE_MARGIN) + 1

    def mark(x: int, y: int, w: int, h: int, value: bool = True) -> None:
        c0, c1 = max(0, int(x // cw)), min(cols, int(-(-(x + w) // cw)))
        r0, r1 = max(0, int(y // rh)), min(rows, int(-(-(y + h) // rh)))
        for r in range(r0, r1):
            for c in range(c0, c1):
                busy[r][c] = value

    def clear(x: int, y: int, w: int, h: int) -> None:
        # только клетки целиком внутри подложки
        mark(x + cw, y + rh, w - 2 * cw, h - 2 * rh, value=False)

    top = 0
    for el in tree:
        if etree.QName(el).localname not in ("sp", "pic", "grpSp", "graphicFrame", "cxnSp"):
            continue
        if id(el) in ignore:
            continue
        box = _el_box(el)
        ph = el.find(f"{{{P}}}nvSpPr/{{{P}}}nvPr/{{{P}}}ph")
        if ph is not None and ph.get("type") in ("title", "ctrTitle"):
            # геометрия заголовка унаследована от макета — берём типичную
            # полосу заголовка, а не считаем верх слайда свободным
            top = max(top, box[1] + box[3] if box else int(slide_h * 0.22))
        if box is None:
            continue
        if box[2] * box[3] >= 0.6 * slide_w * slide_h:
            # непрозрачная подложка закрывает всё, что под ней (z-порядок)
            if etree.QName(el).localname == "sp" and _visible(el):
                clear(*box)
            continue
        if _visible(el):
            mark(*box)
    mark(0, 0, slide_w, top if title_band is None else title_band)
    for box in extra_busy:
        mark(*box)
    best = None
    heights = [0] * cols
    for r in range(my, rows - my):
        for c in range(cols):
            heights[c] = 0 if busy[r][c] or c < mx or c >= cols - mx else heights[c] + 1
        stack: list[int] = []
        for c in range(cols + 1):
            cur = heights[c] if c < cols else 0
            while stack and heights[stack[-1]] >= cur:
                hgt = heights[stack.pop()]
                left = stack[-1] + 1 if stack else 0
                area = hgt * (c - left)
                if hgt and (best is None or area > best[0]):
                    best = (area, left, r - hgt + 1, c - left, hgt)
            stack.append(c)
    if best is None:
        return None
    _, c, r, w, h = best
    x, y, w_emu, h_emu = int(c * cw), int(r * rh), int(w * cw), int(h * rh)
    return (w_emu * h_emu, x, y, w_emu, h_emu, None)


def _empty_body_hosts(
    root: etree._Element,
    free_space_in: tuple[int, int] | None = None,
) -> list[etree._Element]:
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
    if free_space_in is not None:
        # последним — только когда у образца нет своей пустой рамки
        free = _free_space_host(root, *free_space_in)
        if free is not None:
            hosts.append(free)
    return hosts


def _build_table_frame(
    shape_id: int, x: int, y: int, cx: int, n_rows: int, n_cols: int
) -> etree._Element:
    """A standalone `p:graphicFrame` carrying a bare `a:tbl` grid —
    same XML python-pptx `add_table` emits (Medium Style 2, bandRow)."""
    n_cols = _allowed_columns(cx, n_cols)
    col_w = max(1, cx // n_cols)
    cy = n_rows * _ROW_H_EMU
    cell = (
        '<a:tc><a:txBody><a:bodyPr/><a:p><a:pPr latinLnBrk="0">'
        '<a:defRPr sz="1050"/></a:pPr></a:p></a:txBody><a:tcPr/></a:tc>'
    )
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
    candidates = _empty_body_hosts(root, slide_size)
    if table.source_ref.range and n_cols >= 4:
        # ADR-033: широкая таблица из данных — в самую широкую область,
        # иначе колонки переносятся по слогам и строки уходят за слайд
        candidates = sorted(candidates, key=lambda c: -c[3])
    for candidate in candidates:
        _, cx0, cy0, cw, ch, _sp = candidate
        if cw < min(n_cols, 2) * _MIN_COL_W_EMU:
            continue
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
    frame = _build_table_frame(_next_shape_id(root), x, y, cx, n_rows, n_cols)
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
    if all(t.source_ref.range for t in tables) and grids:
        # ADR-033: таблицы из данных строятся с нуля по ширине слайда.
        # Сетка образца бывает декоративной (шире слайда, своё число
        # строк) — её демо-рамка убирается, а не заливается данными.
        for grid in grids:
            frame = next(
                (a for a in grid.iterancestors() if etree.QName(a).localname == "graphicFrame"),
                None,
            )
            if frame is not None and frame.getparent() is not None:
                frame.getparent().remove(frame)
        grids = []

    if used_hosts is None:
        used_hosts = set()
    grid_iter = iter(grids)
    removed_unsafe = False
    for table in tables:
        grid = next(grid_iter, None)
        created = grid is None
        if created:
            grid = _add_table_grid(root, table, used_hosts, slide_size)
            if grid is None:
                report.units_dropped += 1
                continue
            report.tables_created += 1
        frame = next(a for a in grid.iterancestors() if etree.QName(a).localname == "graphicFrame")
        ext = frame.find(f"{{{P}}}xfrm/{{{A}}}ext")
        widths = [int(c.get("w", "0")) for c in grid.findall(f"{{{A}}}tblGrid/{{{A}}}gridCol")]
        # У старых доноров ext бывает рассинхронизирован с реальной сеткой.
        cx = sum(widths) if not created else int(ext.get("cx", "0"))
        if slide_size is not None:
            off = frame.find(f"{{{P}}}xfrm/{{{A}}}off")
            cx = min(cx, slide_size[0] - int(off.get("x", "0")))
        if cx < _MIN_COL_W_EMU:
            frame.getparent().remove(frame)
            removed_unsafe = True
            continue
        total_cols = max([len(table.headers), *(len(r) for r in table.rows)])
        allowed = _allowed_columns(cx, total_cols)
        trimmed = allowed < total_cols
        if trimmed:
            headers = table.headers + [""] * (total_cols - len(table.headers))
            indices = column_indices(headers, table.rows, allowed)
            report.cols_dropped += (total_cols - allowed) * (len(table.rows) + bool(table.headers))
            table = table.model_copy(
                update={
                    "headers": [headers[i] for i in indices] if table.headers else [],
                    "rows": [[r[i] if i < len(r) else None for i in indices] for r in table.rows],
                }
            )
        guarded = (
            trimmed
            or len(widths) > _MAX_TABLE_COLS
            or any(w < _MIN_COL_W_EMU for w in widths)
            or allowed > len(widths)
            or sum(widths) > cx
        )
        if guarded:
            # Свежая сетка убирает также объединения донора, которые нельзя
            # безопасно разрезать удалением отдельных колонок.
            old_rows = len(grid.findall(f"{{{A}}}tr"))
            report.cols_grown += max(0, allowed - len(widths))
            fresh = _build_table_frame(0, 0, 0, cx, max(1, old_rows), allowed)
            ext.set("cx", str(cx))
            new_grid = next(fresh.iter(f"{{{A}}}tbl"))
            grid.getparent().replace(grid, new_grid)
            grid = new_grid
            _sync_frame_height(grid)
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
        header_row = bool(table.headers)
        for row_i, (tr, values) in enumerate(zip(trs, lines[: len(trs)], strict=False)):
            tcs = tr.findall(f"{{{A}}}tc")
            report.cols_dropped += max(0, len(values) - len(tcs))
            for tc, text in zip(tcs, values[: len(tcs)], strict=False):
                _set_cell(tc, text, header=header_row and row_i == 0)
                report.cells_written += 1 if text else 0
        report.tables_filled += 1
        report.table_ids.append(table.id)
        if (created or guarded) and slide_size is not None:
            fit_size = (slide_size[0], slide_size[1] - (_TABLE_NOTE_H if trimmed else 0))
            _fit_table_font(grid, fit_size)
        if trimmed:
            _add_column_note(root, frame, grid, column_note(allowed, total_cols))

    report.units_dropped = len(tables) - report.tables_filled
    if report.tables_filled == 0 and not removed_unsafe:
        return slide_xml, report
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8"), report


def _add_column_note(root, frame, grid, text: str) -> None:
    """Видимая сноска под таблицей, без изменения исходного ContentPack."""
    off = frame.find(f"{{{P}}}xfrm/{{{A}}}off")
    ext = frame.find(f"{{{P}}}xfrm/{{{A}}}ext")
    sizes = [int(r.get("sz", "1050")) / 100 for r in grid.iter(f"{{{A}}}defRPr")]
    height = int(_estimated_grid_height(grid, max(sizes, default=10.5)))
    note = etree.fromstring(
        f'<p:sp xmlns:p="{P}" xmlns:a="{A}">'
        f'<p:nvSpPr><p:cNvPr id="{_next_shape_id(root)}" name="Table column note"/>'
        '<p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
        f'<p:spPr><a:xfrm><a:off x="{off.get("x")}" y="{int(off.get("y")) + height}"/>'
        f'<a:ext cx="{ext.get("cx")}" cy="{_TABLE_NOTE_H}"/></a:xfrm></p:spPr>'
        '<p:txBody><a:bodyPr wrap="square" lIns="0" rIns="0" tIns="45720" bIns="0"/>'
        '<a:lstStyle/><a:p><a:pPr><a:defRPr sz="1050"/></a:pPr>'
        "<a:r><a:t/></a:r></a:p></p:txBody></p:sp>"
    )
    next(note.iter(f"{{{A}}}t")).text = text
    frame.addnext(note)


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
