"""Единая типографическая шкала шаблона (К3) и баланс заполнения слайда (К4).

Собирает задекларированные ступени кегля (presentation/layout/master ``defRPr@sz``)
и наблюдаемую шкалу шаблона/колоды (``rPr@sz`` на содержательных ранах),
предоставляя детерминированный снаппинг для всех этапов композиции и
финальную балансировку заполненности слайда ``[occupancy_min, occupancy_max]``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from lxml import etree

from deckdna.audit.basic import (
    DEFAULT_FONT_PT,
)
from deckdna.pptx.opc.package import OpcPackage, parse_rels, rels_name_for, resolve_target

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"

SYNTHETIC_FONT_SCALE: tuple[float, ...] = (
    10.0,
    12.0,
    14.0,
    16.0,
    18.0,
    20.0,
    24.0,
    28.0,
    32.0,
    36.0,
)
FONT_SCALE_TOLERANCE = 0.10
DISPLAY_FONT_PT = 40.0
FONT_FLOOR_PT = 8.0

_OCC_GRID_X = 128
_OCC_GRID_Y = 72
_FOOTER_PH_TYPES = frozenset({"sldNum", "dt", "ftr", "hdr"})
_INSET_DEFAULTS_EMU = {"lIns": 91440, "rIns": 91440, "tIns": 45720, "bIns": 45720}


def canonical_font_step(pt: float) -> float:
    """Ступень 0.5pt (совпадает с ``audit.basic._canonical_font_step``)."""
    return round(pt * 2.0) / 2.0


def _presentation_default_pt(parts: dict[str, bytes]) -> float:
    raw = parts.get("ppt/presentation.xml")
    if raw is None:
        return DEFAULT_FONT_PT
    try:
        root = etree.fromstring(raw)
    except etree.XMLSyntaxError:
        return DEFAULT_FONT_PT
    defrpr = root.find(f".//{{{P}}}defaultTextStyle/{{{A}}}lvl1pPr/{{{A}}}defRPr")
    if defrpr is None:
        defrpr = root.find(f".//{{{A}}}defaultTextStyle/{{{A}}}lvl1pPr/{{{A}}}defRPr")
    if defrpr is not None and defrpr.get("sz"):
        try:
            val = int(defrpr.get("sz", "0")) / 100.0
            if val > 0:
                return val
        except ValueError:
            pass
    return DEFAULT_FONT_PT


def xml_declared_font_scale(
    *xml_blobs: bytes | None,
    default_pt: float = DEFAULT_FONT_PT,
) -> tuple[float, ...]:
    """Задекларированная шкала кеглей из переданных XML-частей (layout, master)."""
    scale: set[float] = {default_pt} if default_pt > 0 else set()
    for blob in xml_blobs:
        if not blob:
            continue
        try:
            root = etree.fromstring(blob)
        except etree.XMLSyntaxError:
            continue
        for defrpr in root.findall(f".//{{{A}}}defRPr"):
            sz = defrpr.get("sz")
            if not sz:
                continue
            try:
                val = int(sz) / 100.0
                if val > 0:
                    scale.add(val)
            except ValueError:
                continue
    return tuple(sorted(scale))


def slide_declared_font_scale_from_parts(
    parts: dict[str, bytes],
    slide_part: str,
) -> tuple[float, ...]:
    """Задекларированная шкала слайда (presentation default + layout + master ``defRPr@sz``)."""
    default_pt = _presentation_default_pt(parts)
    layout_blob: bytes | None = None
    master_blob: bytes | None = None
    slide_rels_part = rels_name_for(slide_part)
    if slide_rels_part in parts:
        for rel in parse_rels(parts[slide_rels_part]):
            if not rel.is_external and rel.type.endswith("/slideLayout"):
                layout_part = resolve_target(slide_part, rel.target)
                layout_blob = parts.get(layout_part)
                layout_rels_part = rels_name_for(layout_part)
                if layout_rels_part in parts:
                    for mrel in parse_rels(parts[layout_rels_part]):
                        if not mrel.is_external and mrel.type.endswith("/slideMaster"):
                            master_part = resolve_target(layout_part, mrel.target)
                            master_blob = parts.get(master_part)
                            break
                break
    return xml_declared_font_scale(layout_blob, master_blob, default_pt=default_pt)


def _is_footer_or_decorative_shape(shape: etree._Element) -> bool:
    c_nv = shape.find(f".//{{{P}}}nvSpPr/{{{P}}}cNvPr")
    if c_nv is not None and (c_nv.get("name") or "").startswith("DeckDNA source footnote"):
        return True
    ph = shape.find(f".//{{{P}}}nvSpPr/{{{P}}}nvPr/{{{P}}}ph")
    if ph is not None and ph.get("type") in _FOOTER_PH_TYPES:
        return True
    tx_body = shape.find(f"{{{P}}}txBody")
    if tx_body is None:
        return True
    if any(
        (fld.get("type") or "").lower().startswith(("slidenum", "datetime"))
        for fld in tx_body.iter(f"{{{A}}}fld")
    ):
        return True
    body_pr = tx_body.find(f"{{{A}}}bodyPr")
    if body_pr is not None and body_pr.get("vert") not in (None, "horz"):
        return True
    ext = shape.find(f".//{{{P}}}spPr/{{{A}}}xfrm/{{{A}}}ext")
    if ext is not None:
        try:
            if 0 < int(ext.get("cx", "0")) < 450_000:
                return True
        except ValueError:
            pass
    runs = [(t.text or "").strip() for t in tx_body.findall(f".//{{{A}}}t")]
    nonempty = [t for t in runs if t]
    if len(nonempty) >= 2:
        tiny = sum(1 for t in nonempty if len(t) <= 3)
        if tiny / len(nonempty) > 0.5:
            return True
    return False


def _iter_slide_shapes(root: etree._Element):
    tree = root.find(f".//{{{P}}}cSld/{{{P}}}spTree")
    if tree is None:
        return

    def walk(container: etree._Element):
        for child in container:
            local = etree.QName(child).localname
            if local == "sp":
                yield child
            elif local == "grpSp":
                yield from walk(child)

    yield from walk(tree)


def package_observed_font_scale_from_parts(
    parts: dict[str, bytes],
    *,
    min_uses: int = 3,
) -> tuple[float, ...]:
    """Наблюдаемая шкала явных ``a:rPr@sz`` по всем слайдам пакета."""
    census: dict[float, int] = {}
    slide_names = sorted(
        name for name in parts if name.startswith("ppt/slides/slide") and name.endswith(".xml")
    )
    for name in slide_names:
        try:
            root = etree.fromstring(parts[name])
        except etree.XMLSyntaxError:
            continue
        for shape in _iter_slide_shapes(root):
            if _is_footer_or_decorative_shape(shape):
                continue
            tx_body = shape.find(f"{{{P}}}txBody")
            if tx_body is None:
                continue
            for run in tx_body.iter(f"{{{A}}}r"):
                t_el = run.find(f"{{{A}}}t")
                if t_el is None or not (t_el.text or "").strip():
                    continue
                rpr = run.find(f"{{{A}}}rPr")
                if rpr is None or not rpr.get("sz"):
                    continue
                try:
                    step = canonical_font_step(int(rpr.get("sz", "0")) / 100.0)
                except ValueError:
                    continue
                if step > 0:
                    census[step] = census.get(step, 0) + 1
    return tuple(sorted(step for step, count in census.items() if count >= min_uses))


def effective_font_scale(
    declared: Iterable[float] = (),
    observed: Iterable[float] = (),
    dna_scale: Iterable[float] = (),
) -> tuple[float, ...]:
    """Единая шкала кеглей (declared + observed + DNA); синтетическая, если пусто."""
    combined = sorted(
        {round(float(s), 2) for s in (*declared, *observed, *dna_scale) if s and s > 0}
    )
    text_steps = [s for s in combined if s < DISPLAY_FONT_PT]
    if len(text_steps) < 2:
        combined = sorted(set(combined) | set(SYNTHETIC_FONT_SCALE))
    return tuple(combined)


def slide_font_scale(
    pkg: OpcPackage,
    slide_part: str,
    *,
    observed: Iterable[float] | None = None,
    dna_scale: Iterable[float] = (),
) -> tuple[float, ...]:
    """Шкала кеглей для конкретного слайда пакета."""
    declared = slide_declared_font_scale_from_parts(pkg.parts, slide_part)
    obs = (
        tuple(observed)
        if observed is not None
        else package_observed_font_scale_from_parts(pkg.parts, min_uses=3)
    )
    return effective_font_scale(declared, obs, dna_scale)


def in_font_scale(
    pt: float,
    scale: Iterable[float] | None,
    *,
    tol: float = FONT_SCALE_TOLERANCE,
) -> bool:
    """Попадает ли кегль ``pt`` в шкалу ``scale`` с допуском ``±tol``."""
    if pt >= DISPLAY_FONT_PT:
        return True
    steps = [float(s) for s in (scale or ()) if s and s > 0]
    if not steps:
        return True
    return min(abs(pt - s) / s for s in steps) <= tol


def snap_font_pt(
    pt: float,
    scale: Iterable[float] | None,
    *,
    mode: str = "nearest",
    min_pt: float = FONT_FLOOR_PT,
    max_pt: float | None = None,
    allow_display: bool = True,
) -> float:
    """Привязать кегль ``pt`` к ближайшей/нижней/верхней ступени шкалы."""
    if allow_display and pt >= DISPLAY_FONT_PT and (max_pt is None or pt <= max_pt):
        return canonical_font_step(pt)
    raw_steps = sorted({float(s) for s in (scale or ()) if s and s > 0})
    if not raw_steps:
        raw_steps = list(SYNTHETIC_FONT_SCALE)
    candidates = [
        s for s in raw_steps if s >= min_pt - 1e-6 and (max_pt is None or s <= max_pt + 1e-6)
    ]
    if not candidates:
        # Если в [min_pt, max_pt] нет точных шагов шкалы, проверим допустимую
        # ±9.5% окрестность ближайшего шага шкалы.
        tol_steps = [
            step
            for s in raw_steps
            for step in (
                canonical_font_step(s * 0.91),
                canonical_font_step(s * 0.95),
                canonical_font_step(s),
                canonical_font_step(s * 1.05),
                canonical_font_step(s * 1.09),
            )
            if min_pt - 1e-6 <= step <= (max_pt if max_pt is not None else DISPLAY_FONT_PT) + 1e-6
            and abs(step - s) / s <= 0.095
        ]
        if tol_steps:
            candidates = sorted(set(tol_steps))
        else:
            candidates = raw_steps

    if mode == "down":
        below = [s for s in candidates if s <= pt + 1e-6]
        if below:
            return below[-1]
        # Проверим 9.5%-нижнюю границу самой мелкой ступени
        lowest_ref = min(raw_steps)
        low_tol = canonical_font_step(math.ceil(lowest_ref * 0.905 * 2.0) / 2.0)
        if min_pt <= low_tol <= pt + 1e-6 and abs(low_tol - lowest_ref) / lowest_ref <= 0.098:
            return low_tol
        return candidates[0]
    if mode == "up":
        above = [s for s in candidates if s >= pt - 1e-6]
        return above[0] if above else candidates[-1]
    return min(candidates, key=lambda s: (abs(pt - s) / s, abs(pt - s)))


def scale_candidates(
    scale: Iterable[float] | None,
    low_pt: float,
    high_pt: float,
    *,
    descending: bool = False,
    include_tolerance_steps: bool = True,
) -> list[float]:
    """Кандидаты кегля в диапазоне ``[low_pt, high_pt]``, принадлежащие шкале."""
    raw = sorted({float(s) for s in (scale or ()) if s and s > 0})
    if not raw:
        raw = list(SYNTHETIC_FONT_SCALE)
    lo = max(FONT_FLOOR_PT, float(low_pt))
    hi = float(high_pt)
    if hi < lo:
        lo, hi = hi, lo
    exact = {s for s in raw if lo - 1e-6 <= s <= hi + 1e-6}
    if include_tolerance_steps:
        # Добавляем полушаги 0.5pt, попадающие в ±9.2% от ступеней шкалы,
        # чтобы подбор под рамку не терял плотность между далёкими ступенями.
        n_lo = int(math.ceil(lo * 2.0))
        n_hi = int(math.floor(hi * 2.0))
        for half in range(n_lo, n_hi + 1):
            cand = half / 2.0
            if cand >= DISPLAY_FONT_PT or any(abs(cand - s) / s <= 0.092 for s in raw):
                exact.add(cand)
    out = sorted(exact, reverse=descending)
    if not out:
        snapped = snap_font_pt(
            hi if descending else lo,
            raw,
            mode="down" if descending else "up",
            min_pt=lo,
            max_pt=hi,
        )
        if in_font_scale(snapped, raw):
            out = [snapped]
    return out


def _shape_id(el: etree._Element) -> str | None:
    for path in (
        f".//{{{P}}}nvSpPr/{{{P}}}cNvPr",
        f".//{{{P}}}nvCxnSpPr/{{{P}}}cNvPr",
        f".//{{{P}}}nvGraphicFramePr/{{{P}}}cNvPr",
    ):
        node = el.find(path)
        if node is not None and node.get("id"):
            return node.get("id")
    return None


def _shape_box(
    el: etree._Element, hints: dict[str, dict] | None = None
) -> tuple[int, int, int, int] | None:
    local = etree.QName(el).localname
    if local == "graphicFrame":
        xfrm = el.find(f"{{{P}}}xfrm")
    elif local == "grpSp":
        xfrm = el.find(f"{{{P}}}grpSpPr/{{{A}}}xfrm")
    else:
        xfrm = el.find(f"{{{P}}}spPr/{{{A}}}xfrm")
    if xfrm is not None:
        off, ext = xfrm.find(f"{{{A}}}off"), xfrm.find(f"{{{A}}}ext")
        if off is not None and ext is not None:
            try:
                return (
                    int(off.get("x", "0")),
                    int(off.get("y", "0")),
                    int(ext.get("cx", "0")),
                    int(ext.get("cy", "0")),
                )
            except ValueError:
                pass
    if hints:
        sid = _shape_id(el)
        if sid and sid in hints and "box" in hints[sid]:
            b = hints[sid]["box"]
            return int(b[0]), int(b[1]), int(b[2]), int(b[3])
    return None


def materialize_shape_xfrm(
    shape: etree._Element,
    box: tuple[int, int, int, int] | None,
) -> bool:
    """Записать явный ``p:spPr/a:xfrm`` из унаследованной геометрии макета."""
    if box is None:
        return False
    sp_pr = shape.find(f"{{{P}}}spPr")
    if sp_pr is None:
        return False
    xfrm = sp_pr.find(f"{{{A}}}xfrm")
    if (
        xfrm is not None
        and xfrm.find(f"{{{A}}}off") is not None
        and xfrm.find(f"{{{A}}}ext") is not None
    ):
        return False
    if xfrm is None:
        xfrm = etree.Element(f"{{{A}}}xfrm")
        sp_pr.insert(0, xfrm)
    off = xfrm.find(f"{{{A}}}off")
    if off is None:
        off = etree.SubElement(xfrm, f"{{{A}}}off")
    ext = xfrm.find(f"{{{A}}}ext")
    if ext is None:
        ext = etree.SubElement(xfrm, f"{{{A}}}ext")
    off.set("x", str(int(box[0])))
    off.set("y", str(int(box[1])))
    ext.set("cx", str(max(1, int(box[2]))))
    ext.set("cy", str(max(1, int(box[3]))))
    return True


def _set_shape_box(
    shape: etree._Element,
    x: int,
    y: int,
    w: int,
    h: int,
) -> None:
    sp_pr = shape.find(f"{{{P}}}spPr")
    if sp_pr is None:
        return
    xfrm = sp_pr.find(f"{{{A}}}xfrm")
    if xfrm is None:
        xfrm = etree.Element(f"{{{A}}}xfrm")
        sp_pr.insert(0, xfrm)
    off = xfrm.find(f"{{{A}}}off")
    if off is None:
        off = etree.SubElement(xfrm, f"{{{A}}}off")
    ext = xfrm.find(f"{{{A}}}ext")
    if ext is None:
        ext = etree.SubElement(xfrm, f"{{{A}}}ext")
    off.set("x", str(int(x)))
    off.set("y", str(int(y)))
    ext.set("cx", str(max(1, int(w))))
    ext.set("cy", str(max(1, int(h))))


def snap_deck_font_scale(
    pkg: OpcPackage,
    protected_indices: set[int] | None = None,
    hints_by_part: dict[str, dict[str, dict]] | None = None,
    *,
    observed_scale: Iterable[float] | None = None,
    only_parts: Iterable[str] | None = None,
) -> int:
    """Финальный проход по выходному пакету: гарантирует, что все явные
    ``a:rPr@sz < 40pt`` на непустых содержательных ранах попадают в
    ``declared(slide) | observed(deck)`` с допуском ``±10%``."""
    from deckdna.pptx.composing.text_replace import (
        _fits_in_box,
        _required_height_emu,
        _usable_box_emu,
    )

    protected = protected_indices or set()
    hints_by_part = hints_by_part or {}
    observed = (
        set(observed_scale)
        if observed_scale is not None
        else set(package_observed_font_scale_from_parts(pkg.parts, min_uses=3))
    )
    wanted = set(only_parts) if only_parts is not None else None
    snapped_runs = 0

    slide_parts = sorted(
        (
            name
            for name in pkg.parts
            if name.startswith("ppt/slides/slide") and name.endswith(".xml")
        ),
        key=lambda n: int("".join(ch for ch in n if ch.isdigit()) or "0"),
    )
    sw, sh = 12_192_000, 6_858_000
    raw_pres = pkg.parts.get("ppt/presentation.xml")
    if raw_pres:
        try:
            sld_sz = etree.fromstring(raw_pres).find(f"{{{P}}}sldSz")
            if sld_sz is not None:
                sw = int(sld_sz.get("cx", str(sw)))
                sh = int(sld_sz.get("cy", str(sh)))
        except (etree.XMLSyntaxError, ValueError):
            pass

    for idx, slide_part in enumerate(slide_parts):
        if idx in protected or (wanted is not None and slide_part not in wanted):
            continue
        declared = set(slide_declared_font_scale_from_parts(pkg.parts, slide_part))
        scale = sorted(s for s in (declared | observed) if s > 0)
        if not scale:
            continue
        try:
            root = etree.fromstring(pkg.parts[slide_part])
        except etree.XMLSyntaxError:
            continue
        changed = False
        hints = hints_by_part.get(slide_part, {})
        for shape in _iter_slide_shapes(root):
            if _is_footer_or_decorative_shape(shape):
                continue
            tx_body = shape.find(f"{{{P}}}txBody")
            if tx_body is None:
                continue
            full_text = "\n".join(
                "".join(t.text or "" for t in p.iter(f"{{{A}}}t"))
                for p in tx_body.findall(f"{{{A}}}p")
            ).strip()
            if not full_text:
                continue
            sid = _shape_id(shape) or ""
            hint = hints.get(sid, {})
            usable = _usable_box_emu(tx_body) or (
                (int(hint["w"]), int(hint["h"])) if "w" in hint and "h" in hint else None
            )
            body_pr = tx_body.find(f"{{{A}}}bodyPr")
            wrap_none = (
                body_pr.get("wrap") == "none"
                if body_pr is not None and body_pr.get("wrap")
                else bool(hint.get("wrap_none"))
            )
            for para in tx_body.findall(f"{{{A}}}p"):
                for run in para.findall(f"{{{A}}}r"):
                    t_el = run.find(f"{{{A}}}t")
                    if t_el is None or not (t_el.text or "").strip():
                        continue
                    rpr = run.find(f"{{{A}}}rPr")
                    if rpr is None or not rpr.get("sz"):
                        continue
                    try:
                        pt = int(rpr.get("sz", "0")) / 100.0
                    except ValueError:
                        continue
                    if pt >= DISPLAY_FONT_PT or in_font_scale(pt, scale):
                        continue
                    # Ищем наибольший допустимый шаг шкалы <= pt (чтобы не вызвать overflow)
                    cands_down = scale_candidates(
                        scale, FONT_FLOOR_PT, pt, descending=True, include_tolerance_steps=True
                    )
                    cands_down = [c for c in cands_down if in_font_scale(c, scale)]
                    if cands_down:
                        target_pt = cands_down[0]
                    else:
                        # pt меньше минимальной ступени шкалы: берём минимальную ступень
                        # (или её -9.5% допуск) и при необходимости чуть расширяем высоту рамки
                        lowest_ref = min(scale)
                        low_tol = canonical_font_step(math.ceil(lowest_ref * 0.905 * 2.0) / 2.0)
                        target_pt = low_tol if in_font_scale(low_tol, scale) else lowest_ref
                        if usable is not None and usable[0] > 0:
                            if _fits_in_box(
                                tx_body, full_text, lowest_ref, usable[0], usable[1], wrap_none
                            ):
                                target_pt = lowest_ref
                            else:
                                need_h, widest_w = _required_height_emu(
                                    tx_body, full_text, target_pt, usable[0]
                                )
                                box = _shape_box(shape, hints)
                                if box is not None and widest_w <= usable[0] * 1.05:
                                    x, y, w, h = box
                                    ins_v = max(0, h - usable[1])
                                    req_box_h = int(math.ceil(need_h * 1.08)) + ins_v
                                    if req_box_h > h and y + req_box_h <= int(sh * 0.95):
                                        _set_shape_box(shape, x, y, w, req_box_h)
                                        usable = (usable[0], req_box_h - ins_v)
                    new_sz = str(int(round(target_pt * 100)))
                    rpr.set("sz", new_sz)
                    ppr = para.find(f"{{{A}}}pPr")
                    if ppr is not None:
                        defrpr = ppr.find(f"{{{A}}}defRPr")
                        if defrpr is not None and defrpr.get("sz"):
                            defrpr.set("sz", new_sz)
                    for end_rpr in para.findall(f"{{{A}}}endParaRPr"):
                        if end_rpr.get("sz"):
                            end_rpr.set("sz", new_sz)
                    snapped_runs += 1
                    changed = True
        if changed:
            pkg.parts[slide_part] = etree.tostring(
                root, xml_declaration=True, encoding="UTF-8", standalone=True
            )
    return snapped_runs


def _mark_cells(cells: bytearray, x0: float, y0: float, x1: float, y1: float) -> None:
    x0 = max(x0, 0.0)
    y0 = max(y0, 0.0)
    x1 = min(x1, 1.0)
    y1 = min(y1, 1.0)
    if x1 <= x0 or y1 <= y0:
        return
    for gy in range(int(y0 * _OCC_GRID_Y), min(int(y1 * _OCC_GRID_Y) + 1, _OCC_GRID_Y)):
        row = gy * _OCC_GRID_X
        for gx in range(int(x0 * _OCC_GRID_X), min(int(x1 * _OCC_GRID_X) + 1, _OCC_GRID_X)):
            cells[row + gx] = 1


def measure_xml_occupancy(
    root: etree._Element,
    sw: int,
    sh: int,
    hints: dict[str, dict] | None = None,
) -> tuple[float, float]:
    """(occ_total, occ_content) по XML слайда на сетке 128×72 (зеркало ``_slide_occupancy``)."""
    cells_total = bytearray(_OCC_GRID_X * _OCC_GRID_Y)
    cells_content = bytearray(_OCC_GRID_X * _OCC_GRID_Y)
    tree = root.find(f".//{{{P}}}cSld/{{{P}}}spTree")
    if tree is None or sw <= 0 or sh <= 0:
        return 0.0, 0.0

    for el in tree:
        local = etree.QName(el).localname
        if local not in ("sp", "pic", "graphicFrame", "grpSp", "cxnSp"):
            continue
        box = _shape_box(el, hints)
        if box is None:
            continue
        x, y, w, h = box
        if w <= 0 or h <= 0:
            continue
        nx0, ny0 = x / sw, y / sh
        nx1, ny1 = (x + w) / sw, (y + h) / sh
        _mark_cells(cells_total, nx0, ny0, nx1, ny1)
        area = (w / sw) * (h / sh)
        if area >= 0.80:
            continue
        is_content = False
        if local in ("pic", "graphicFrame"):
            is_content = True
        elif local == "sp":
            txt = "".join(t.text or "" for t in el.iter(f"{{{A}}}t")).strip()
            if txt and not _is_footer_or_decorative_shape(el):
                is_content = True
        elif local == "grpSp":
            txt = "".join(t.text or "" for t in el.iter(f"{{{A}}}t")).strip()
            if txt or el.find(f".//{{{P}}}pic") is not None:
                is_content = True
        if is_content:
            _mark_cells(cells_content, nx0, ny0, nx1, ny1)

    total_cells = _OCC_GRID_X * _OCC_GRID_Y
    return sum(cells_total) / total_cells, sum(cells_content) / total_cells


def fit_oversized_body_before_data(
    slide_xml: bytes,
    sw: int,
    sh: int,
    hints: dict[str, dict] | None = None,
) -> tuple[bytes, bool]:
    """Ужать пустой хвост высокой текстовой рамки-плейсхолдера перед вставкой
    графика/таблицы/схемы, чтобы область под текстом стала свободной."""
    from deckdna.pptx.composing.text_replace import (
        _body_font_size_pt,
        _is_title_run,
        _required_height_emu,
    )

    hints = hints or {}
    root = etree.fromstring(slide_xml)
    tree = root.find(f".//{{{P}}}cSld/{{{P}}}spTree")
    if tree is None:
        return slide_xml, False
    changed = False
    for shape in tree.findall(f"{{{P}}}sp"):
        if _is_footer_or_decorative_shape(shape):
            continue
        tx_body = shape.find(f"{{{P}}}txBody")
        if tx_body is None:
            continue
        runs = [t for t in tx_body.iter(f"{{{A}}}t") if (t.text or "").strip()]
        if not runs or any(_is_title_run(r) for r in runs):
            continue
        box = _shape_box(shape, hints)
        if box is None:
            continue
        x, y, w, h = box
        if h < int(sh * 0.36):
            continue
        sid = _shape_id(shape) or ""
        hint = hints.get(sid, {})
        size_pt = _body_font_size_pt(tx_body) or hint.get("sz", DEFAULT_FONT_PT)
        full_text = "\n".join(
            "".join(t.text or "" for t in p.iter(f"{{{A}}}t")) for p in tx_body.findall(f"{{{A}}}p")
        ).strip()
        usable_w = max(1, w - 2 * 91440)
        need_h, _ = _required_height_emu(tx_body, full_text, size_pt, usable_w)
        fitted_h = max(int(sh * 0.11), int(math.ceil(need_h * 1.25)) + 91440)
        if fitted_h < int(h * 0.55):
            _set_shape_box(shape, x, y, w, fitted_h)
            changed = True
    if not changed:
        return slide_xml, False
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True), True


def balance_slide_occupancy(
    slide_xml: bytes,
    sw: int,
    sh: int,
    hints: dict[str, dict] | None = None,
    font_scale: Iterable[float] | None = None,
    purpose: str = "",
) -> tuple[bytes, bool]:
    """Сбалансировать заполненность слайда в коридор ``[0.25, 0.75]`` (К4).

    * Материализует унаследованный ``a:xfrm`` из макета на непустых текстовых
      плейсхолдерах, чтобы аудит видел их реальную площадь.
    * При ``occ_content > 0.74`` подрезает пустой нижний хвост у непомерно
      высоких текстовых рамок (плейсхолдеров макета) до фактической высоты
      текста с запасом, не создавая overflow и не опуская ``occ_total < 0.28``.
    * При ``occ_total < 0.255`` увеличивает мелкий текст по шкале шаблона и
      распределяет текстовые рамки в свободную область слайда без наложений.
    """
    from deckdna.pptx.composing.text_replace import (
        _body_font_size_pt,
        _fits_in_box,
        _is_title_run,
        _required_height_emu,
        _set_body_sz,
    )

    hints = hints or {}
    root = etree.fromstring(slide_xml)
    tree = root.find(f".//{{{P}}}cSld/{{{P}}}spTree")
    if tree is None or sw <= 0 or sh <= 0:
        return slide_xml, False

    changed = False
    for shape in tree.findall(f"{{{P}}}sp"):
        if _is_footer_or_decorative_shape(shape):
            continue
        tx_body = shape.find(f"{{{P}}}txBody")
        if tx_body is None:
            continue
        txt = "".join(t.text or "" for t in tx_body.iter(f"{{{A}}}t")).strip()
        if not txt:
            continue
        sid = _shape_id(shape) or ""
        hint = hints.get(sid)
        if hint and "box" in hint and materialize_shape_xfrm(shape, hint["box"]):
            changed = True

    occ_total, occ_content = measure_xml_occupancy(root, sw, sh, hints)

    # 1. Over-occupancy (occ_content > 0.74): ужимаем пустой хвост текстовых рамок
    if occ_content > 0.74:
        text_candidates: list[tuple[etree._Element, int, int, int, int, int]] = []
        for shape in tree.findall(f"{{{P}}}sp"):
            if _is_footer_or_decorative_shape(shape):
                continue
            tx_body = shape.find(f"{{{P}}}txBody")
            if tx_body is None:
                continue
            full_text = "\n".join(
                "".join(t.text or "" for t in p.iter(f"{{{A}}}t"))
                for p in tx_body.findall(f"{{{A}}}p")
            ).strip()
            if not full_text:
                continue
            box = _shape_box(shape, hints)
            if box is None:
                continue
            x, y, w, h = box
            if (w / sw) * (h / sh) >= 0.80:
                continue
            sid = _shape_id(shape) or ""
            hint = hints.get(sid, {})
            size_pt = _body_font_size_pt(tx_body) or hint.get("sz", DEFAULT_FONT_PT)
            usable_w = max(1, w - 2 * 91440)
            need_h, _ = _required_height_emu(tx_body, full_text, size_pt, usable_w)
            min_h = max(int(sh * 0.08), int(math.ceil(need_h * 1.18)) + 91440)
            if h > min_h + int(sh * 0.05):
                text_candidates.append((shape, x, y, w, h, min_h))

        # Сортируем по величине пустого хвоста (сначала самые раздутые плейсхолдеры)
        text_candidates.sort(key=lambda item: item[4] - item[5], reverse=True)
        for shape, x, y, w, h, min_h in text_candidates:
            occ_total, occ_content = measure_xml_occupancy(root, sw, sh, hints)
            if occ_content <= 0.71:
                break
            # Подбираем долю урезания, чтобы occ_content <= 0.71, но occ_total >= 0.28
            for frac in (0.0, 0.25, 0.5, 0.75):
                cand_h = int(min_h + frac * (h - min_h))
                _set_shape_box(shape, x, y, w, cand_h)
                nt, nc = measure_xml_occupancy(root, sw, sh, hints)
                if nt >= 0.28:
                    changed = True
                    break
            else:
                _set_shape_box(shape, x, y, w, h)

        # Если после подрезки текста occ_content всё ещё > 0.74 (например, из-за
        # созданного графика/таблицы впритык к тексту), слегка поджимаем высоту graphicFrame
        occ_total, occ_content = measure_xml_occupancy(root, sw, sh, hints)
        if occ_content > 0.74:
            for gf in tree.findall(f"{{{P}}}graphicFrame"):
                box = _shape_box(gf, hints)
                if box is None:
                    continue
                gx, gy, gw, gh = box
                excess_ratio = (occ_content - 0.71) / max(0.1, (gw / sw) * (gh / sh))
                shrink = min(0.22, max(0.06, excess_ratio * 1.1))
                new_gh = max(int(sh * 0.30), int(gh * (1.0 - shrink)))
                if new_gh < gh:
                    xfrm = gf.find(f"{{{P}}}xfrm")
                    if xfrm is not None and xfrm.find(f"{{{A}}}ext") is not None:
                        xfrm.find(f"{{{A}}}ext").set("cy", str(new_gh))
                        changed = True
                occ_total, occ_content = measure_xml_occupancy(root, sw, sh, hints)
                if occ_content <= 0.73:
                    break

    # 2. Under-occupancy (occ_total < 0.255): растим мелкий кегль и расширяем текстовые рамки
    occ_total, occ_content = measure_xml_occupancy(root, sw, sh, hints)
    if occ_total < 0.255:
        content_shapes: list[
            tuple[etree._Element, etree._Element, int, int, int, int, str, bool]
        ] = []
        other_boxes: list[tuple[int, int, int, int]] = []
        for el in tree:
            local = etree.QName(el).localname
            if local not in ("sp", "pic", "graphicFrame", "grpSp", "cxnSp"):
                continue
            box = _shape_box(el, hints)
            if box is None:
                continue
            if local == "sp" and not _is_footer_or_decorative_shape(el):
                tx_body = el.find(f"{{{P}}}txBody")
                if tx_body is not None:
                    txt = "\n".join(
                        "".join(t.text or "" for t in p.iter(f"{{{A}}}t"))
                        for p in tx_body.findall(f"{{{A}}}p")
                    ).strip()
                    if txt:
                        is_title = any(
                            _is_title_run(t)
                            for t in tx_body.iter(f"{{{A}}}t")
                            if (t.text or "").strip()
                        )
                        content_shapes.append(
                            (el, tx_body, box[0], box[1], box[2], box[3], txt, is_title)
                        )
                        continue
            if box[2] * box[3] < 0.80 * sw * sh:
                other_boxes.append(box)

        content_shapes.sort(key=lambda item: (item[3], item[2]))
        margin_x, margin_y = int(sw * 0.05), int(sh * 0.06)
        max_right, max_bottom = int(sw * 0.95), int(sh * 0.90)

        for idx, (shape, tx_body, x, y, w, h, txt, is_title) in enumerate(content_shapes):
            # Определяем доступную ширину и высоту до соседей снизу/справа
            avail_r = max_right
            avail_b = max_bottom
            for j, (_os, _otx, ox, oy, ow, oh, _otxt, _ot) in enumerate(content_shapes):
                if j == idx:
                    continue
                if oy >= y + int(h * 0.5) and min(x + w, ox + ow) > max(x, ox):
                    avail_b = min(avail_b, oy - int(sh * 0.02))
                elif ox >= x + int(w * 0.5) and min(y + h, oy + oh) > max(y, oy):
                    avail_r = min(avail_r, ox - int(sw * 0.02))
            for ox, oy, ow, _oh in other_boxes:
                if oy >= y + int(h * 0.5) and min(x + w, ox + ow) > max(x, ox):
                    avail_b = min(avail_b, oy - int(sh * 0.02))

            body_pr = tx_body.find(f"{{{A}}}bodyPr")
            anchor = body_pr.get("anchor") if body_pr is not None else None
            first_ppr = tx_body.find(f"{{{A}}}p/{{{A}}}pPr")
            algn = first_ppr.get("algn") if first_ppr is not None else None

            target_w = max(w, min(avail_r - x, int(sw * 0.86)))
            if algn == "ctr":
                half_w = min(x + w // 2 - margin_x, max_right - (x + w // 2))
                if 2 * half_w > w:
                    x = (x + w // 2) - half_w
                    target_w = 2 * half_w

            # Сколько высоты нужно добавить, чтобы суммарно превзойти 28% площади
            occ_now, _ = measure_xml_occupancy(root, sw, sh, hints)
            deficit = max(0.0, 0.30 - occ_now)
            need_extra_h = int(deficit * sw * sh / max(1, target_w)) + int(sh * 0.04)
            target_h = max(h, min(avail_b - y, h + need_extra_h))
            if anchor == "ctr" and idx == 0 and len(content_shapes) == 1:
                cy_mid = y + h // 2
                half_h = min(cy_mid - margin_y, avail_b - cy_mid, target_h // 2)
                if 2 * half_h > h:
                    y = cy_mid - half_h
                    target_h = 2 * half_h

            if target_w > w or target_h > h:
                _set_shape_box(shape, x, y, target_w, target_h)
                w, h = target_w, target_h
                changed = True

            # Если кегль на полупустом слайде был мелким (< 20pt), доращиваем по шкале
            sid = _shape_id(shape) or ""
            hint = hints.get(sid, {})
            cur_sz = _body_font_size_pt(tx_body) or hint.get("sz", DEFAULT_FONT_PT)
            cap_pt = 28.0 if (is_title or idx == 0) else 22.0
            if cur_sz < cap_pt and font_scale:
                usable_w = max(1, w - 2 * 91440)
                usable_h = max(1, int((h - 2 * 45720) * 0.85))
                wrap_none = body_pr is not None and body_pr.get("wrap") == "none"
                for step in scale_candidates(
                    font_scale, cur_sz, cap_pt, descending=True, include_tolerance_steps=False
                ):
                    if step > cur_sz and _fits_in_box(
                        tx_body, txt, step, usable_w, usable_h, wrap_none
                    ):
                        _set_body_sz(tx_body, step)
                        changed = True
                        break

    if not changed:
        return slide_xml, False
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True), True
