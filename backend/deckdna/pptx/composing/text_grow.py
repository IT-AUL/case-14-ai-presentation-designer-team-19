"""Дорастить кегль текста до размера, который рамка реально вмещает.

Демо-текст шаблонов часто набран подписями 7–9 pt: при заливке настоящего
текста в большую карточку он остаётся мелким и нечитаемым, а карточка —
полупустой. Обратная сторона ``text_replace._fit_body`` (который только
уменьшает): здесь кегль растёт ступенями 0.5 pt, пока текст помещается с
запасом по той же оценке, что у аудита (``_fits_in_box``).

* заголовок слайда не трогается — его кегль задан шаблоном;
* потолок: 14 pt для основного текста, 16 pt для коротких однострочных
  заголовков карточек — деловая типографика, а не плакат;
* рамки одного размера на слайде получают общий кегль (минимум из
  вмещаемых) — карточки сетки выглядят одинаково;
* кегль только растёт, никогда не уменьшается.
"""

from __future__ import annotations

from lxml import etree

from deckdna.pptx.composing.text_replace import (
    _body_font_size_pt,
    _body_is_decorative,
    _fits_in_box,
    _is_title_run,
    _set_body_sz,
    _shape_id,
    _usable_box_emu,
)

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"

BODY_CAP_PT = 14.0
LABEL_CAP_PT = 16.0
_STEP_PT = 0.5
_HEIGHT_RESERVE = 0.85  # текст занимает не больше 85% высоты рамки
_LABEL_MAX_CHARS = 40


def _text(tx_body: etree._Element) -> str:
    return "\n".join(
        "".join(t.text or "" for t in p.iter(f"{{{A}}}t")) for p in tx_body.findall(f"{{{A}}}p")
    ).strip()


def _in_table(tx_body: etree._Element) -> bool:
    return any(etree.QName(a).localname == "tbl" for a in tx_body.iterancestors())


def grow_text(slide_xml: bytes, hints: dict[str, dict] | None = None) -> tuple[bytes, int]:
    """Увеличить мелкий текст слайда; возвращает (xml, число выросших рамок).

    *hints* — метрики фигур эталона с наследованием плейсхолдеров
    (``minimal._exemplar_shape_metrics``): у рамки-плейсхолдера кегль и
    размер часто заданы только в макете — без них она не росла (повестка
    VK Education оставалась 7 pt)."""
    hints = hints or {}
    root = etree.fromstring(slide_xml)
    candidates: list[tuple[etree._Element, str, float, int, int, bool, float]] = []
    for tx_body in root.iter(f"{{{P}}}txBody"):
        if _in_table(tx_body) or _body_is_decorative(tx_body):
            continue
        runs = [t for t in tx_body.iter(f"{{{A}}}t") if (t.text or "").strip()]
        if not runs or any(_is_title_run(t) for t in runs):
            continue
        text = _text(tx_body)
        hint = hints.get(_shape_id(tx_body) or "", {})
        size = _body_font_size_pt(tx_body) or hint.get("sz")
        box = _usable_box_emu(tx_body) or (
            (int(hint["w"]), int(hint["h"])) if "w" in hint and "h" in hint else None
        )
        if size is None or box is None or box[0] <= 0 or box[1] <= 0:
            continue
        body_pr = tx_body.find(f"{{{A}}}bodyPr")
        wrap_none = body_pr is not None and body_pr.get("wrap") == "none"
        is_label = len(text) <= _LABEL_MAX_CHARS and "\n" not in text
        cap = LABEL_CAP_PT if is_label else BODY_CAP_PT
        if size >= cap:
            continue
        best = size
        probe = size + _STEP_PT
        usable_w, usable_h = box
        while probe <= cap and _fits_in_box(
            tx_body, text, probe, usable_w, int(usable_h * _HEIGHT_RESERVE), wrap_none
        ):
            best = probe
            probe += _STEP_PT
        if best > size:
            candidates.append((tx_body, text, size, usable_w, usable_h, is_label, best))

    # рамки одного размера — общий кегль (минимум из вмещаемых): пункты
    # списка и карточки одной сетки не должны прыгать 14 → 16 pt
    groups: dict[tuple[int, int], float] = {}
    for _tx, _t, _s, w, h, _is_label, best in candidates:
        key = (round(w / 50000), round(h / 50000))
        groups[key] = min(groups.get(key, best), best)
    grown = 0
    for tx_body, _t, size, w, h, _is_label, _best in candidates:
        target = groups[(round(w / 50000), round(h / 50000))]
        if target > size:
            _set_body_sz(tx_body, target)
            grown += 1
    if not grown:
        return slide_xml, 0
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True), grown
