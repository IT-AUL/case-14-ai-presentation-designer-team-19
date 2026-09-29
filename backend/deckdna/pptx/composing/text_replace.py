"""Static text substitution into a cloned slide (`replace_text` op).

Minimal version of the op: distribute content over *text bodies*, not
individual runs — each content-eligible `p:txBody` is one slot. A slot
either gets a text (first non-empty run ← text, its sibling runs cleared)
or, when the plan's texts run out, is *cleared entirely*: no original
template copy ("Безопасность", Lorem ipsum) may leak into the output —
a real-user defect found on Story-Director plans where sparse
`content_units` left stock card text in the deck.

Runs that belong to decorative typography (per-letter layouts, KPI
micro-labels, vertical captions, ultra-narrow boxes) are left
alone, as are *empty* runs — an empty run in an exemplar is styled
structure (card backgrounds, accents), not a content slot. Pouring prose
into them produces the unreadable overlay this fix addresses.

Table cells (`a:tc` runs inside `a:tbl`) are not text slots — a native
table needs structured rows/cells, which the plan does not yet carry.
Their stock text is still *cleared* (`table_cells_cleared`): leaving it
would leak template copy the same way unfilled card bodies did.

Later stages replace positional mapping with semantic slot mapping; this op
exists to prove the end-to-end path exemplar → clone → content → valid deck.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lxml import etree

# Measurement logic is borrowed, not duplicated: wrap estimation and text
# width live in audit/basic.py (PIL-backed DejaVu metrics). The fitting
# heuristic here is deliberately light — the audit remains the source of
# truth for residual overflow issues.
from deckdna.audit.basic import (
    DEFAULT_FONT_PT,
    EMU_PER_PT,
    LINE_HEIGHT_FACTOR,
    _text_width_emu,
    _wrap_metrics,
)
from deckdna.audit.config import default_audit_config

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"

# A text box narrower than ~0.5" (≈460k EMU) is a decorative micro-label,
# not a prose slot — long text wraps into a vertical letter column there.
_MIN_TEXT_CX_EMU = 450_000
# A body whose majority of non-empty runs are 1-3 chars is fragmented
# decorative typography (per-letter layout), not substitutable content.
_TINY_RUN_MAX = 3
_MAX_TINY_RATIO = 0.5
_STOCK_PLACEHOLDERS = frozenset({
    "product", "product name", "company", "company name", "your title",
    "your text", "placeholder", "sample text", "lorem ipsum",
    "текст", "заголовок", "название продукта", "название компании",
})

# What happens to content slots the plan had no text for: they are
# cleared (runs emptied). Deleting the shapes was the alternative —
# rejected for now: a cleared card keeps the template's composition
# intact, and removing `p:sp` from `spTree` is a riskier XML surgery
# better left to the semantic-mapping stage.
EMPTY_SLOT_POLICY = "clear"

# ── shrink-to-fit ─────────────────────────────────────────────────────
# Positional pour has no real text fitting (that's the Constraint
# Compiler's job). Until it exists, a slot whose text overflows its box
# is shrunk in 1pt steps down to FONT_FLOOR_PT. The floor is a hard
# limit: below it text becomes unreadably small, so residual overflow at
# the floor stays a legitimate audit issue — not hidden.
FONT_FLOOR_PT = 8.0
_SHRINK_STEP_PT = 1.0
# OOXML default text-inset margins when a:bodyPr omits them (0.1"/0.05").
_INSET_DEFAULTS_EMU = {"lIns": 91440, "rIns": 91440, "tIns": 45720, "bIns": 45720}


@dataclass(frozen=True)
class TextReplaceReport:
    runs_total: int
    runs_replaced: int
    runs_skipped_decorative: int
    runs_skipped_empty: int
    bodies_eligible: int
    bodies_filled: int
    bodies_cleared: int
    bodies_shrunk: int
    bodies_truncated: int
    table_cells_cleared: int
    texts_supplied: int
    texts_dropped: int
    runs_title: int
    title_placed: bool
    bodies_overflowing: int = 0
    empty_slot_policy: str = EMPTY_SLOT_POLICY
    font_floor_pt: float = FONT_FLOOR_PT
    # IDs of text shapes that received a non-empty content unit.  This is an
    # internal hand-off to card reflow: a valid KPI can be purely numeric,
    # while decorative donor labels such as ``01`` must remain ignorable.
    filled_shape_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "runs_total": self.runs_total,
            "runs_replaced": self.runs_replaced,
            "runs_skipped_decorative": self.runs_skipped_decorative,
            "runs_skipped_empty": self.runs_skipped_empty,
            "bodies_eligible": self.bodies_eligible,
            "bodies_filled": self.bodies_filled,
            "bodies_cleared": self.bodies_cleared,
            "bodies_shrunk": self.bodies_shrunk,
            "bodies_truncated": self.bodies_truncated,
            "bodies_overflowing": self.bodies_overflowing,
            "table_cells_cleared": self.table_cells_cleared,
            "font_floor_pt": self.font_floor_pt,
            "texts_supplied": self.texts_supplied,
            "texts_dropped": self.texts_dropped,
            "runs_title": self.runs_title,
            "title_placed": self.title_placed,
            "empty_slot_policy": self.empty_slot_policy,
        }


def _is_tiny_run_body(tx_body: etree._Element) -> bool:
    """Фрагментированная типографика: НЕСКОЛЬКО коротких runs в одном
    теле (побуквенная раскладка). Тело с единственным коротким run —
    легитимная короткая метка или число ("12", "91%"), не фрагмент:
    оно должно быть слотом, иначе стоковый текст утечёт в вывод.
    Узкие однобуквенные боксы отсекаются _is_narrow_shape."""
    runs = [(t.text or "").strip() for t in tx_body.findall(f".//{{{A}}}t")]
    nonempty = [t for t in runs if t]
    if len(nonempty) < 2:
        return False
    tiny = sum(1 for t in nonempty if len(t) <= _TINY_RUN_MAX)
    return tiny / len(nonempty) > _MAX_TINY_RATIO


def _owning_shape(tx_body: etree._Element) -> etree._Element | None:
    shape = tx_body.getparent()
    while shape is not None and etree.QName(shape).localname not in ("sp", "cxnSp"):
        shape = shape.getparent()
    return shape


def _shape_ext_emu(tx_body: etree._Element) -> tuple[int, int] | None:
    shape = _owning_shape(tx_body)
    if shape is None:
        return None
    ext = shape.find(f".//{{{P}}}spPr/{{{A}}}xfrm/{{{A}}}ext")
    if ext is None:
        return None
    try:
        return int(ext.get("cx", "0")), int(ext.get("cy", "0"))
    except ValueError:
        return None


def _is_narrow_shape(tx_body: etree._Element) -> bool:
    """Body lives in a shape whose own frame is too narrow for prose."""
    ext = _shape_ext_emu(tx_body)
    return ext is not None and ext[0] < _MIN_TEXT_CX_EMU


def _body_is_decorative(tx_body: etree._Element) -> bool:
    body_pr = tx_body.find(f"{{{A}}}bodyPr")
    if body_pr is not None and body_pr.get("vert"):
        return True  # vertical/caption typography
    return _is_tiny_run_body(tx_body) or _is_narrow_shape(tx_body)


def _is_offslide_body(
    tx_body: etree._Element, slide_size: tuple[int, int] | None
) -> bool:
    """A top-level box crossing the page edge cannot hold authored prose.

    Some donor slides intentionally crop decorative number labels at the
    edges. They may remain as artwork, but counting them as content slots
    pours sentences into invisible/clipped frames. Group-local coordinates
    need a group transform, so those are left to the existing group audit.
    """
    if slide_size is None:
        return False
    shape = _owning_shape(tx_body)
    if shape is None or shape.getparent() is None:
        return False
    if etree.QName(shape.getparent()).localname != "spTree":
        return False
    box = _xy(shape)
    if box is None:
        return False
    x, y, w, h = box
    sw, sh = slide_size
    return x < 0 or y < 0 or x + w > sw or y + h > sh


def _usable_box_emu(tx_body: etree._Element) -> tuple[int, int] | None:
    """(width, height) of the slot's box minus text insets, in EMU."""
    ext = _shape_ext_emu(tx_body)
    if ext is None:
        return None
    cx, cy = ext
    ins = dict(_INSET_DEFAULTS_EMU)
    body_pr = tx_body.find(f"{{{A}}}bodyPr")
    if body_pr is not None:
        for key in ins:
            if body_pr.get(key):
                try:
                    ins[key] = int(body_pr.get(key))
                except ValueError:
                    pass
    height = cy
    room = _room_below(tx_body)
    if room is not None:
        height = min(height, room)
    return (
        max(cx - ins["lIns"] - ins["rIns"], 0),
        max(height - ins["tIns"] - ins["bIns"], 0),
    )


def _xy(shape: etree._Element) -> tuple[int, int, int, int] | None:
    xfrm = shape.find(f"{{{P}}}spPr/{{{A}}}xfrm")
    if xfrm is None:
        return None
    off, ext = xfrm.find(f"{{{A}}}off"), xfrm.find(f"{{{A}}}ext")
    if off is None or ext is None:
        return None
    try:
        return (int(off.get("x")), int(off.get("y")), int(ext.get("cx")), int(ext.get("cy")))
    except (TypeError, ValueError):
        return None


def _room_below(tx_body: etree._Element) -> int | None:
    """Высота до ближайшей другой текстовой рамки, лежащей внутри этой
    ниже её верха (подпись внизу карточки VK WorkSpace).

    Текст рамки сверху растёт вниз и на подпись наезжает, хотя в своей
    рамке «помещается». Только для рамок с якорем сверху; None — никто
    не мешает."""
    shape = _owning_shape(tx_body)
    if shape is None or shape.getparent() is None:
        return None
    body_pr = tx_body.find(f"{{{A}}}bodyPr")
    if body_pr is not None and body_pr.get("anchor") not in (None, "t"):
        return None
    own = _xy(shape)
    if own is None:
        return None
    x, y, w, h = own
    room: int | None = None
    for other in shape.getparent().iterchildren(f"{{{P}}}sp"):
        if other is shape or other.find(f"{{{P}}}txBody") is None:
            continue
        if not any((t.text or "").strip() for t in other.iter(f"{{{A}}}t")):
            continue
        box = _xy(other)
        if box is None:
            continue
        ox, oy, ow, _oh = box
        if not (y + 0.1 * h < oy < y + h):
            continue  # не внутри по вертикали
        overlap = min(x + w, ox + ow) - max(x, ox)
        if overlap < 0.5 * min(w, ow):
            continue
        gap = int(0.04 * 914400)
        room = oy - y - gap if room is None else min(room, oy - y - gap)
    return room


def _attr_sz_pt(el: etree._Element | None) -> float | None:
    if el is None or not el.get("sz"):
        return None
    try:
        return int(el.get("sz")) / 100
    except ValueError:
        return None


def _body_font_size_pt(tx_body: etree._Element) -> float | None:
    """Largest declared sz across the body's rPr/defRPr/endParaRPr, or
    None when the body declares none — the effective size then comes
    from the placeholder chain (layout → master), which the caller
    supplies as a fallback measured on the exemplar."""
    sizes = [
        sz
        for tag in (f"{{{A}}}rPr", f"{{{A}}}defRPr", f"{{{A}}}endParaRPr")
        for el in tx_body.iter(tag)
        if (sz := _attr_sz_pt(el)) is not None
    ]
    return max(sizes) if sizes else None


def _spacing_val_emu(el: etree._Element | None, size_pt: float) -> float:
    """a:spcBef/spcAft/lnSpc value in EMU (spcPts absolute or spcPct of
    the line height)."""
    if el is None:
        return 0.0
    pts = el.find(f"{{{A}}}spcPts")
    if pts is not None and pts.get("val"):
        return int(pts.get("val")) / 100 * EMU_PER_PT
    pct = el.find(f"{{{A}}}spcPct")
    if pct is not None and pct.get("val"):
        return int(pct.get("val")) / 100000 * size_pt * LINE_HEIGHT_FACTOR * EMU_PER_PT
    return 0.0


def _para_line_height_emu(para: etree._Element, size_pt: float) -> float:
    ppr = para.find(f"{{{A}}}pPr")
    if ppr is not None:
        lnspc = ppr.find(f"{{{A}}}lnSpc")
        if lnspc is not None:
            pts = lnspc.find(f"{{{A}}}spcPts")
            if pts is not None and pts.get("val"):
                return int(pts.get("val")) / 100 * EMU_PER_PT
            pct = lnspc.find(f"{{{A}}}spcPct")
            if pct is not None and pct.get("val"):
                return (
                    size_pt
                    * LINE_HEIGHT_FACTOR
                    * int(pct.get("val"))
                    / 100000
                    * EMU_PER_PT
                )
    return size_pt * LINE_HEIGHT_FACTOR * EMU_PER_PT


def _required_height_emu(
    tx_body: etree._Element, text: str, size_pt: float, usable_w: int
) -> tuple[float, float]:
    """(required height, widest word) in EMU — mirrors the audit's
    per-paragraph estimate: every paragraph contributes at least one
    line (empty paragraphs too) plus its spcBef/spcAft."""
    paras = tx_body.findall(f"{{{A}}}p")
    text_idx = next(
        (
            i
            for i, para in enumerate(paras)
            if any((t.text or "").strip() for t in para.iter(f"{{{A}}}t"))
        ),
        0,
    )
    total = 0.0
    widest = 0.0
    for i, para in enumerate(paras):
        content = text if i == text_idx else ""
        n_lines, w = _wrap_metrics(content, size_pt, usable_w)
        total += n_lines * _para_line_height_emu(para, size_pt)
        ppr = para.find(f"{{{A}}}pPr")
        if ppr is not None:
            total += _spacing_val_emu(ppr.find(f"{{{A}}}spcBef"), size_pt)
            total += _spacing_val_emu(ppr.find(f"{{{A}}}spcAft"), size_pt)
        widest = max(widest, w)
    return total, widest


def _fits_in_box(
    tx_body: etree._Element,
    text: str,
    size_pt: float,
    usable_w: int,
    usable_h: int,
    wrap_none: bool,
) -> bool:
    """Same fit predicate the audit applies: wrapped height and widest
    unbreakable word against the usable box with overflow_tolerance
    (the same configs/audit.default.yaml value the audit uses)."""
    tol = default_audit_config().overflow_tolerance
    if wrap_none:
        widest_line = max(
            (_text_width_emu(line, size_pt) for line in re.split(r"[\v\n]", text)),
            default=0,
        )
        return widest_line <= usable_w * (1 + tol)
    required_h, widest_word = _required_height_emu(tx_body, text, size_pt, usable_w)
    return (
        required_h <= usable_h * (1 + tol)
        and widest_word <= usable_w * (1 + tol)
    )


def _set_body_sz(tx_body: etree._Element, size_pt: float) -> None:
    """Set the body's effective size on every layer the audit's
    effective-size lookup reads: run rPr, paragraph defRPr (created
    under pPr when absent), and endParaRPr. Setting only run sz would
    lose to a bigger paragraph defRPr in the audit's max()."""
    value = str(int(round(size_pt * 100)))
    for para in tx_body.iter(f"{{{A}}}p"):
        ppr = para.find(f"{{{A}}}pPr")
        if ppr is None:
            ppr = etree.Element(f"{{{A}}}pPr")
            para.insert(0, ppr)
        defrpr = ppr.find(f"{{{A}}}defRPr")
        if defrpr is None:
            defrpr = etree.SubElement(ppr, f"{{{A}}}defRPr")
        defrpr.set("sz", value)
    for r in tx_body.iter(f"{{{A}}}r"):
        rpr = r.find(f"{{{A}}}rPr")
        if rpr is None:
            rpr = etree.Element(f"{{{A}}}rPr")
            r.insert(0, rpr)
        rpr.set("sz", value)
    for el in tx_body.iter(f"{{{A}}}endParaRPr"):
        el.set("sz", value)


def _shape_id(tx_body: etree._Element) -> str | None:
    """cNvPr@id of the owning shape — the key audit/python-pptx callers
    use to correlate sizes measured on the exemplar."""
    shape = _owning_shape(tx_body)
    if shape is None:
        return None
    for path in (
        f".//{{{P}}}nvSpPr/{{{P}}}cNvPr",
        f".//{{{P}}}nvCxnSpPr/{{{P}}}cNvPr",
    ):
        el = shape.find(path)
        if el is not None and el.get("id"):
            return el.get("id")
    return None


def _fit_body(
    tx_body: etree._Element,
    text: str,
    hint: dict | None = None,
) -> bool:
    """Shrink the body's font in 1pt steps until the text fits its box.

    *hint* carries exemplar-measured fallbacks ({sz, w, h, wrap_none})
    for values the slide XML doesn't declare (placeholder inheritance).
    Bounded at FONT_FLOOR_PT — if it still doesn't fit there, the size
    stays at the floor and the audit keeps its legitimate overflow issue.
    Returns True when the declared size was actually reduced.
    """
    hint = hint or {}
    box = _usable_box_emu(tx_body) or (
        (hint["w"], hint["h"]) if "w" in hint and "h" in hint else None
    )
    if box is None:
        return False
    usable_w, usable_h = box
    if usable_w <= 0 or usable_h <= 0:
        return False
    body_pr = tx_body.find(f"{{{A}}}bodyPr")
    if body_pr is not None and body_pr.get("wrap"):
        wrap_none = body_pr.get("wrap") == "none"
    else:
        wrap_none = bool(hint.get("wrap_none"))
    size_pt = _body_font_size_pt(tx_body) or hint.get("sz", DEFAULT_FONT_PT)
    fitted = size_pt
    while fitted > FONT_FLOOR_PT and not _fits_in_box(
        tx_body, text, fitted, usable_w, usable_h, wrap_none
    ):
        fitted = max(FONT_FLOOR_PT, fitted - _SHRINK_STEP_PT)
    wrap_switched = False
    if wrap_none and not _fits_in_box(tx_body, text, fitted, usable_w, usable_h, wrap_none):
        # Found live (28.09): a donor's single-line label (wrap="none",
        # e.g. a small pill/badge shape) given a real sentence instead
        # of a short tag still doesn't fit at the font floor -- with no
        # wrap, that overflow runs sideways past the shape, straight
        # into whatever sits next to it (a neighboring card/column),
        # not just past its own bottom edge. Allowing wrap here trades
        # that for vertical growth within/below the same shape, which
        # the audit already treats as an honest, expected overflow
        # class (text.overflow) instead of an unrelated shape's text
        # being stepped on.
        if body_pr is not None:
            body_pr.set("wrap", "square")
        wrap_switched = True
        wrap_none = False
        fitted = size_pt
        while fitted > FONT_FLOOR_PT and not _fits_in_box(
            tx_body, text, fitted, usable_w, usable_h, wrap_none
        ):
            fitted = max(FONT_FLOOR_PT, fitted - _SHRINK_STEP_PT)
    if fitted >= size_pt and not wrap_switched:
        return False
    _set_body_sz(tx_body, fitted)
    return True


def _xfrm_box(shape: etree._Element) -> tuple[etree._Element, int, int, int, int] | None:
    xfrm = shape.find(f"{{{P}}}spPr/{{{A}}}xfrm")
    if xfrm is None:
        return None
    off, ext = xfrm.find(f"{{{A}}}off"), xfrm.find(f"{{{A}}}ext")
    if off is None or ext is None:
        return None
    try:
        return ext, int(off.get("x")), int(off.get("y")), int(ext.get("cx")), int(ext.get("cy"))
    except (TypeError, ValueError):
        return None


_TITLE_GROW_MAX_PT = 40.0


def _grow_title_box(
    root: etree._Element, tx_body: etree._Element, text: str, hint: dict | None = None
) -> bool:
    """Рамка заголовка растёт вниз до высоты, нужной тексту при его кегле.

    Шаблоны рисуют рамку заголовка под одну строку, а заголовок-вывод
    занимает две; без этого ``_fit_body`` ужимал его с 24 до 15 pt.
    Рост ограничен ближайшей фигурой под рамкой, перекрывающей её по
    горизонтали (с зазором); рамки без своего ``xfrm`` не трогаются."""
    shape = _owning_shape(tx_body)
    if shape is None:
        return False
    own = _xfrm_box(shape)
    box = _usable_box_emu(tx_body)
    if own is None or box is None:
        return False
    ext, x, y, w, h = own
    body_pr = tx_body.find(f"{{{A}}}bodyPr")
    if body_pr is not None and body_pr.get("wrap") == "none":
        return False
    size = _body_font_size_pt(tx_body) or (hint or {}).get("sz", DEFAULT_FONT_PT)
    if size > _TITLE_GROW_MAX_PT:
        return False  # дисплейный заголовок («Спасибо!» 80 pt) — только ужимать
    need, _ = _required_height_emu(tx_body, text, size, box[0])
    extra = int(need) - box[1]
    if extra <= 0:
        return False
    gap = int(0.08 * 914400)
    floor = None
    for other in root.iter(f"{{{P}}}sp", f"{{{P}}}pic", f"{{{P}}}graphicFrame", f"{{{P}}}grpSp"):
        if other is shape or any(a is other for a in shape.iterancestors()):
            continue
        if etree.QName(other.getparent()).localname != "spTree":
            continue
        xfrm = other.find(f"{{{P}}}spPr/{{{A}}}xfrm")
        if xfrm is None:
            xfrm = other.find(f"{{{P}}}grpSpPr/{{{A}}}xfrm")
        if xfrm is None:
            xfrm = other.find(f"{{{P}}}xfrm")
        if xfrm is None or xfrm.find(f"{{{A}}}off") is None or xfrm.find(f"{{{A}}}ext") is None:
            continue
        ox = int(xfrm.find(f"{{{A}}}off").get("x", 0))
        oy = int(xfrm.find(f"{{{A}}}off").get("y", 0))
        ow = int(xfrm.find(f"{{{A}}}ext").get("cx", 0))
        oh = int(xfrm.find(f"{{{A}}}ext").get("cy", 0))
        if oy < y + h or min(x + w, ox + ow) <= max(x, ox) or not oh:
            continue  # не под рамкой (фон во весь слайд начинается выше)
        floor = oy if floor is None else min(floor, oy)
    # без фигуры под рамкой — не больше исходной высоты (1 → 2 строки):
    # размер слайда здесь неизвестен, а за край уходить нельзя
    two_lines = int(2 * size * LINE_HEIGHT_FACTOR * EMU_PER_PT) - box[1]
    cap = max(h, two_lines)  # до двух строк заголовка, не дальше
    room = (floor - gap - (y + h)) if floor is not None else cap
    grow = max(0, min(extra, room, cap))
    if grow < int(0.05 * 914400):
        return False
    ext.set("cy", str(h + grow))
    return True


def _strip_hard_breaks(tx_body: etree._Element) -> None:
    """Remove exemplar `a:br` hard breaks from a filled body: they were
    line structure for the *original* text; left in place they render a
    phantom empty line after the poured text (and the audit counts it).
    """
    for br in tx_body.iter(f"{{{A}}}br"):
        parent = br.getparent()
        if parent is not None:
            parent.remove(br)


def _strip_empty_paragraphs(tx_body: etree._Element) -> None:
    paragraphs = tx_body.findall(f"{{{A}}}p")
    for para in paragraphs:
        if len(paragraphs) <= 1:
            break
        if not any((t.text or "").strip() for t in para.iter(f"{{{A}}}t")):
            tx_body.remove(para)
            paragraphs.remove(para)


def _body_overflows(tx_body: etree._Element, text: str, hint: dict | None = None) -> bool:
    """Keep authored content intact; report residual overflow for layout repair."""
    hint = hint or {}
    box = _usable_box_emu(tx_body) or (
        (hint["w"], hint["h"]) if "w" in hint and "h" in hint else None
    )
    if box is None or min(box) <= 0:
        return False
    body_pr = tx_body.find(f"{{{A}}}bodyPr")
    wrap_none = (
        body_pr.get("wrap") == "none"
        if body_pr is not None and body_pr.get("wrap")
        else bool(hint.get("wrap_none"))
    )
    size = _body_font_size_pt(tx_body) or hint.get("sz", DEFAULT_FONT_PT)
    return not _fits_in_box(tx_body, text, size, *box, wrap_none)


def _tx_body_of(el: etree._Element) -> etree._Element | None:
    node = el.getparent()
    while node is not None and etree.QName(node).localname != "txBody":
        node = node.getparent()
    return node


def _is_title_run(el: etree._Element) -> bool:
    """Run lives in a shape declared as title/ctrTitle placeholder."""
    for ancestor in el.iterancestors():
        if etree.QName(ancestor).localname != "sp":
            continue
        ph = ancestor.find(f".//{{{P}}}nvSpPr/{{{P}}}nvPr/{{{P}}}ph")
        return ph is not None and ph.get("type") in ("title", "ctrTitle")
    return False


def count_content_slots(
    slide_xml: bytes, slide_size: tuple[int, int] | None = None
) -> int:
    """How many content slots ``replace_text_runs`` would actually fill
    on this slide -- the real, authoritative capacity signal for
    exemplar selection, not an approximation of it.

    Mirrors ``replace_text_runs``'s own slot-eligibility predicates
    exactly (table cells, decorative bodies, title runs excluded; a slot
    is one distinct ``p:txBody`` holding >=1 non-empty, non-title run) so
    "how many slots does this candidate have" and "how many slots will
    actually get filled" can never disagree -- callers that need a
    capacity NUMBER (cloning/exemplar.py's exemplar ranking) get it from
    here instead of maintaining a second, looser definition of "slot"
    (the prior signal, ``RunProfile.long_runs``, just counted any
    non-empty run >=15 chars anywhere on the slide, including ones
    inside a single multi-paragraph body or a decorative/table run --
    read-only, no mutation, safe to call on every ranking candidate.
    """
    root = etree.fromstring(slide_xml)
    # id(tx_body) as the dict KEY, but the dict also holds the element
    # itself as the VALUE -- without a live Python reference, lxml's
    # proxy object for an ancestor visited only via a transient
    # .getparent() walk can be garbage-collected before the loop ends,
    # and a later run's unrelated txBody can then be allocated at that
    # same freed address, making two genuinely different bodies collide
    # under id() (found live: a 6-vs-16 mismatch against
    # replace_text_runs's own bodies_eligible on the same slide, which
    # avoids this by keeping body_elems[key] = tx_body alive the same
    # way). dict discards the value once returned, but that's fine --
    # counting only needs the size, which is now correct because the
    # references stayed alive for the whole loop.
    slots: dict[int, object] = {}
    for el in root.findall(f".//{{{A}}}t"):
        if not (el.text or "").strip():
            continue
        if any(etree.QName(a).localname == "tbl" for a in el.iterancestors()):
            continue
        tx_body = _tx_body_of(el)
        if (
            tx_body is None
            or _body_is_decorative(tx_body)
            or _is_offslide_body(tx_body, slide_size)
        ):
            continue
        if _is_title_run(el):
            continue
        slots[id(tx_body)] = tx_body
    return len(slots)


def replace_text_runs(
    slide_xml: bytes,
    texts: list[str],
    title_text: str | None = None,
    shape_hints: dict[str, dict] | None = None,
    slide_size: tuple[int, int] | None = None,
) -> tuple[bytes, TextReplaceReport]:
    """Distribute *texts* over the slide's content slots.

    A slot is a content-eligible `p:txBody`: not in `a:tbl`, not a
    decorative body (vertical, per-letter fragments, ultra-narrow), and
    holding at least one non-empty run. Non-empty `a:tc` cell runs are
    emptied — a native table is not prose slot, but its stock copy must
    not survive either. Slots are filled in document
    order — the first non-empty run gets the text, its siblings are
    cleared so a longer replacement can never overlay leftover original
    copy in the same body. Slots beyond `len(texts)` are emptied
    (`empty_slot_policy="clear"`): stock template text must not survive
    into a generated deck.

    *title_text* goes to the title/ctrTitle placeholder runs directly —
    title runs sit at unpredictable spots in document order, so a
    positional pour rarely reaches them; the first run gets the text,
    the rest are cleared.
    """
    root = etree.fromstring(slide_xml)
    all_runs = root.findall(f".//{{{A}}}t")
    skipped = 0
    skipped_empty = 0
    table_cells_cleared = 0
    title_runs: list[etree._Element] = []
    # eligible bodies in first-seen document order -> their non-empty runs
    body_runs: dict[int, list[etree._Element]] = {}
    body_order: list[int] = []
    body_elems: dict[int, etree._Element] = {}
    intended = {t.strip().casefold() for t in texts}
    if title_text:
        intended.add(title_text.strip().casefold())
    for el in all_runs:
        tx_body = el.getparent()
        while tx_body is not None and etree.QName(tx_body).localname != "txBody":
            tx_body = tx_body.getparent()
        in_table = any(
            etree.QName(a).localname == "tbl" for a in el.iterancestors()
        )
        if in_table:
            # Ячейки таблицы — не текстовый слот: позиционная заливка
            # прозы в a:tc невалидна семантически. Но стоковый текст
            # ячеек ("Заголовок"/"Текст") обязан быть очищен — иначе
            # он утечёт в вывод при клонировании exemplar с a:tbl.
            skipped += 1
            if (el.text or "").strip():
                el.text = ""
                table_cells_cleared += 1
            continue
        if (
            tx_body is None
            or _body_is_decorative(tx_body)
            or _is_offslide_body(tx_body, slide_size)
        ):
            skipped += 1
            # Decorative typography is normally preserved, but generic
            # template placeholders are never authored presentation copy.
            # This also catches placeholders in ultra-narrow boxes that
            # are intentionally ineligible as content slots.
            original = (el.text or "").strip()
            if original.casefold() in _STOCK_PLACEHOLDERS and original.casefold() not in intended:
                el.text = ""
            continue
        if not (el.text or "").strip():
            skipped_empty += 1
            continue
        if _is_title_run(el):
            title_runs.append(el)
            continue
        key = id(tx_body)
        if key not in body_runs:
            body_runs[key] = []
            body_elems[key] = tx_body
            body_order.append(key)
        body_runs[key].append(el)

    shape_hints = shape_hints or {}

    def hint_for(tx_body: etree._Element) -> dict:
        return shape_hints.get(_shape_id(tx_body), {})

    replaced = 0
    title_placed = False
    bodies_shrunk = 0
    bodies_truncated = 0
    bodies_overflowing = 0
    for i, el in enumerate(title_runs):
        if title_text:
            el.text = title_text if i == 0 else ""
            replaced += 1
            title_placed = True
    if title_placed:
        title_body = _tx_body_of(title_runs[0])
        if title_body is not None:
            _strip_hard_breaks(title_body)
            _grow_title_box(root, title_body, title_text, hint_for(title_body))
            if _fit_body(title_body, title_text, hint_for(title_body)):
                bodies_shrunk += 1

    bodies_eligible = len(body_order)
    bodies_filled = 0
    bodies_cleared = 0
    filled_shape_ids: list[str] = []
    for key in body_order:
        runs = body_runs[key]
        text = texts[bodies_filled] if bodies_filled < len(texts) else None
        if text is not None:
            runs[0].text = text
            for el in runs[1:]:
                el.text = ""
            _strip_hard_breaks(body_elems[key])
            _strip_empty_paragraphs(body_elems[key])
            if _fit_body(body_elems[key], text, hint_for(body_elems[key])):
                bodies_shrunk += 1
            if _body_overflows(body_elems[key], text, hint_for(body_elems[key])):
                bodies_overflowing += 1
            if text.strip():
                shape_id = _shape_id(body_elems[key])
                if shape_id is not None:
                    filled_shape_ids.append(shape_id)
            bodies_filled += 1
        else:
            bodies_cleared += 1
            for el in runs:
                el.text = ""
        replaced += len(runs)

    xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    return xml, TextReplaceReport(
        runs_total=len(all_runs),
        runs_replaced=replaced,
        runs_skipped_decorative=skipped,
        runs_skipped_empty=skipped_empty,
        bodies_eligible=bodies_eligible,
        bodies_filled=bodies_filled,
        bodies_cleared=bodies_cleared,
        bodies_shrunk=bodies_shrunk,
        bodies_truncated=bodies_truncated,
        bodies_overflowing=bodies_overflowing,
        table_cells_cleared=table_cells_cleared,
        texts_supplied=len(texts),
        texts_dropped=max(0, len(texts) - bodies_eligible),
        runs_title=len(title_runs) if title_placed else 0,
        title_placed=title_placed,
        filled_shape_ids=tuple(filled_shape_ids),
    )
