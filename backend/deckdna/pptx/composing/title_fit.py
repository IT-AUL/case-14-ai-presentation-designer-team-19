"""Заголовок не заходит на содержимое слайда — после того, как оно на месте.

Кегль заголовка подгоняется при заливке текста (``text_replace``), но
таблицы, схемы и созданные рамки ставятся позже. Живой дефект 29.09
(незнакомый шаблон Slidesgo, крупный дисплейный шрифт): заголовок в три
строки ложился поверх таблицы, вставленной под ним.

Правило общее, без знания шаблона: ближайшая фигура ПОД заголовком
(перекрывает его по горизонтали, верх ниже верха заголовка) — это пол.
Если текст заголовка при текущем кегле ниже пола, кегль уменьшается
шагами до ``_MIN_RATIO`` исходного (не мельче ``_MIN_PT``). Не хватило и
этого — заголовок остаётся, аудит сообщит о пересечении честно.
"""

from __future__ import annotations

from lxml import etree

from deckdna.pptx.composing.text_replace import (
    DEFAULT_FONT_PT,
    _body_font_size_pt,
    _required_height_emu,
    _set_body_sz,
    _shape_id,
)

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_MIN_RATIO = 0.6
_MIN_PT = 16.0
_STEP_PT = 1.0
_GAP_EMU = int(0.06 * 914400)
_TITLE_TYPES = ("title", "ctrTitle")


def _xfrm_box(el: etree._Element) -> tuple[int, int, int, int] | None:
    tag = etree.QName(el).localname
    if tag == "graphicFrame":
        xfrm = el.find(f"{{{P}}}xfrm")
    elif tag == "grpSp":
        xfrm = el.find(f"{{{P}}}grpSpPr/{{{A}}}xfrm")
    else:
        xfrm = el.find(f"{{{P}}}spPr/{{{A}}}xfrm")
    if xfrm is None:
        return None
    off, ext = xfrm.find(f"{{{A}}}off"), xfrm.find(f"{{{A}}}ext")
    if off is None or ext is None:
        return None
    try:
        return int(off.get("x")), int(off.get("y")), int(ext.get("cx")), int(ext.get("cy"))
    except (TypeError, ValueError):
        return None


def _is_title(el: etree._Element) -> bool:
    ph = el.find(f"{{{P}}}nvSpPr/{{{P}}}nvPr/{{{P}}}ph")
    return ph is not None and ph.get("type") in _TITLE_TYPES


def _has_content(el: etree._Element) -> bool:
    tag = etree.QName(el).localname
    if tag in ("graphicFrame", "pic", "grpSp"):
        return True
    return any((t.text or "").strip() for t in el.iter(f"{{{A}}}t"))


def _dump_fitted(root: etree._Element, title: etree._Element) -> bytes:
    """Keep the compiler's heading mask within its fitted title frame.

    Donor shapes are never changed here. Only add_missing_title's own backdrop
    follows its heading when fitting shrinks that heading above real content.
    """
    name = title.find(f"{{{P}}}nvSpPr/{{{P}}}cNvPr")
    box = _xfrm_box(title)
    if name is not None and name.get("name") == "DeckDNA heading" and box is not None:
        tree = title.getparent()
        for shape in tree:
            identity = shape.find(f"{{{P}}}nvSpPr/{{{P}}}cNvPr")
            backdrop = _xfrm_box(shape)
            if (identity is None or identity.get("name") != "DeckDNA heading backdrop"
                    or backdrop is None):
                continue
            height = max(1, min(backdrop[3], box[1] + box[3] - backdrop[1]))
            shape.find(f"{{{P}}}spPr/{{{A}}}xfrm/{{{A}}}ext").set("cy", str(height))
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


_SIDE_TAGS = ("pic", "grpSp", "graphicFrame", "cxnSp")
_MIN_WIDTH_RATIO = 0.4


def _set_width(title: etree._Element, box: tuple[int, int, int, int], width: int) -> None:
    sp_pr = title.find(f"{{{P}}}spPr")
    xfrm = sp_pr.find(f"{{{A}}}xfrm") if sp_pr is not None else None
    if sp_pr is None:
        return
    if xfrm is None:  # геометрия унаследована от макета — делаем явной
        xfrm = etree.Element(f"{{{A}}}xfrm")
        etree.SubElement(xfrm, f"{{{A}}}off", x=str(box[0]), y=str(box[1]))
        etree.SubElement(xfrm, f"{{{A}}}ext", cx=str(box[2]), cy=str(box[3]))
        sp_pr.insert(0, xfrm)
    xfrm.find(f"{{{A}}}ext").set("cx", str(width))


def _narrow_to_side_art(
    tree: etree._Element, title: etree._Element, box: tuple[int, int, int, int],
    slide_w: int, slide_h: int,
) -> int:
    """Картинка, линия или группа, заходящая в полосу заголовка справа,
    — край рамки заголовка (живой дефект 29.09: заголовок на «кольцах»
    KPI). Не уже ``_MIN_WIDTH_RATIO`` исходной ширины."""
    x, y, w, h = box
    edge = x + w
    for el in tree:
        if el is title or etree.QName(el).localname not in _SIDE_TAGS:
            continue
        ob = _xfrm_box(el)
        if ob is None:
            continue
        ox, oy, ow, oh = ob
        if ow * oh >= 0.6 * slide_w * slide_h:
            continue
        if oy >= y + h or oy + oh <= y:
            continue  # не в полосе заголовка
        if x + _MIN_WIDTH_RATIO * w <= ox < edge:
            edge = ox
    if edge >= x + w:
        return w
    width = max(1, edge - x - _GAP_EMU)
    _set_width(title, box, width)
    return width


def fit_title_above_content(
    slide_xml: bytes, slide_w: int, slide_h: int, hints: dict[str, dict] | None = None,
    *, title_text: str | None = None,
    font_scale: list[float] | tuple[float, ...] | None = None,
) -> tuple[bytes, bool]:
    """(xml, подогнана ли рамка/типографика заголовка)."""
    hints = hints or {}
    root = etree.fromstring(slide_xml)
    tree = root.find(f"{{{P}}}cSld/{{{P}}}spTree")
    if tree is None:
        return slide_xml, False
    title = next((el for el in tree.iter(f"{{{P}}}sp") if _is_title(el)), None)
    if title is None and title_text:
        expected = ' '.join(title_text.split())
        matches = [el for el in tree.iter(f"{{{P}}}sp") if ' '.join(
            ' '.join(t.text or '' for t in el.iter(f'{{{A}}}t')).split()) == expected]
        if matches:
            def title_size(el):
                body = el.find(f'{{{P}}}txBody')
                return _body_font_size_pt(body) or hints.get(_shape_id(body) or '', {}).get('sz', 0)

            title = max(matches, key=title_size)
    if title is None:
        return slide_xml, False
    tx_body = title.find(f"{{{P}}}txBody")
    if tx_body is None:
        return slide_xml, False
    text = " ".join(t.text or "" for t in tx_body.iter(f"{{{A}}}t")).strip()
    if not text:
        return slide_xml, False
    body_pr = tx_body.find(f"{{{A}}}bodyPr")
    if body_pr is not None and body_pr.get("anchor") not in (None, "t"):
        return slide_xml, False  # текст растёт не вниз
    hint = hints.get(_shape_id(tx_body) or "", {})
    box = _xfrm_box(title) or hint.get("box")
    if box is None:
        return slide_xml, False
    x, y, w, h = box
    l_ins, t_ins = hint.get("insets", (91440, 45720))
    w = _narrow_to_side_art(tree, title, (x, y, w, h), slide_w, slide_h)
    usable_w = max(1, w - 2 * l_ins)

    floor = None
    for el in tree:
        if el is title or etree.QName(el).localname not in ("sp", "pic", "graphicFrame", "grpSp"):
            continue
        ob = _xfrm_box(el)
        if ob is None or not _has_content(el):
            continue
        ox, oy, ow, oh = ob
        if ow * oh >= 0.6 * slide_w * slide_h:
            continue  # фон/подложка во весь слайд
        if ox >= x + w:
            continue  # справа от (суженного) заголовка
        if oy <= y + 0.25 * h or oy >= slide_h:
            continue  # рядом с заголовком сбоку/сверху, а не под ним
        overlap = min(x + w, ox + ow) - max(x, ox)
        if overlap < 0.3 * min(w, ow):
            continue
        floor = oy if floor is None else min(floor, oy)
    if floor is None:
        narrowed = w != box[2]
        if narrowed:
            floor = y + h + _GAP_EMU  # сузили — держим прежнюю высоту рамки
        else:
            return slide_xml, False
    b_ins = int(body_pr.get('bIns', '45720')) if body_pr is not None else 45720
    available = floor - _GAP_EMU - y - t_ins - b_ins
    size = _body_font_size_pt(tx_body) or hint.get("sz", DEFAULT_FONT_PT)
    need, _ = _required_height_emu(tx_body, text, size, usable_w)
    if need <= available:
        height = min(h, floor - _GAP_EMU - y)
        if height < h or w != box[2]:
            _set_width(title, box, w)
            title.find(f'{{{P}}}spPr/{{{A}}}xfrm/{{{A}}}ext').set('cy', str(height))
            return _dump_fitted(root, title), True
        return slide_xml, False
    lowest = max(_MIN_PT, size * _MIN_RATIO)
    fitted = size
    if font_scale:
        from deckdna.pptx.composing.font_scale import scale_candidates, snap_font_pt

        cands = [
            c
            for c in reversed(
                scale_candidates(font_scale, lowest, size, include_tolerance_steps=False)
            )
            if c < size
        ]
        low_snap = snap_font_pt(lowest, font_scale, mode="up", min_pt=_MIN_PT, max_pt=size)
        if low_snap < size and low_snap not in cands:
            cands.append(low_snap)
        for cand in cands:
            fitted = cand
            need, _ = _required_height_emu(tx_body, text, fitted, usable_w)
            if need <= available:
                break
    else:
        while fitted - _STEP_PT >= lowest:
            fitted -= _STEP_PT
            need, _ = _required_height_emu(tx_body, text, fitted, usable_w)
            if need <= available:
                break
    if fitted >= size:
        return _dump_fitted(root, title), w != box[2]
    _set_body_sz(tx_body, fitted)
    if need <= available:
        _set_width(title, box, w)
        title.find(f'{{{P}}}spPr/{{{A}}}xfrm/{{{A}}}ext').set(
            'cy', str(min(h, floor - _GAP_EMU - y)))
    return _dump_fitted(root, title), True
