"""Убрать с заполненного слайда то, что осталось без своего текста.

Слот шаблона, для которого у колоды нет текста, ``text_replace`` очищает
(демо-текст не должен утечь). Но рядом с ним в шаблоне часто стоит
оформление этого текста: кружок-аватар у «Имя Фамилия / Должность»,
плашка-кнопка, маркер, иконка. После очистки оно висит на слайде пустой
белой фигурой — живой дефект финального слайда VK Tech (28.09).

Правило общее, без знания шаблона:

* очищенная фигура (в эталоне был текст, после заливки нет) удаляется,
  если она что-то рисует (заливка, контур) — пустая плашка выглядит
  сломанной; невидимая пустая рамка просто остаётся;
* мелкая декоративная фигура без текста (до ``_MAX_DECOR_AREA`` площади
  слайда, не картинка), стоящая вплотную к очищенному слоту и не
  вплотную ни к одному тексту, который остался, — удаляется.

Работает по фигурам верхнего уровня ``spTree``; группы (карточки) —
забота ``card_reflow``.
"""

from __future__ import annotations

from lxml import etree

from deckdna.pptx.composing.card_reflow import _Box, _box, _is_visible, _text

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_SHAPES = {f"{{{P}}}sp", f"{{{P}}}cxnSp"}
_MAX_DECOR_AREA = 0.02  # доля площади слайда
_NEAR = 0.04  # «вплотную»: доля меньшей стороны слайда


def _shape_id(el: etree._Element) -> str | None:
    c = el.find(f".//{{{P}}}cNvPr")
    return c.get("id") if c is not None else None


def _gap(a: _Box, b: _Box) -> int:
    dx = max(0, max(a.x, b.x) - min(a.r, b.r))
    dy = max(0, max(a.y, b.y) - min(a.b, b.b))
    return max(dx, dy)


_STUB_GEOMS = frozenset({"ellipse", "roundRect", "rect", "flowChartConnector"})


def _is_photo_stub(el: etree._Element, box: _Box, slide_w: int) -> bool:
    """Круг/квадрат без текста, залитый нейтральным серым, размером с
    аватар (5–25% ширины слайда): место под фото спикера (VK WorkSpace,
    обложка). Цветной декор и мелкие маркеры сюда не попадают."""
    import colorsys

    if el.tag not in (f"{{{P}}}sp", f"{{{P}}}pic") or _text(el) or not box.h:
        return False
    if not 0.85 <= box.w / box.h <= 1.18 or not 0.05 <= box.w / slide_w <= 0.25:
        return False
    sp_pr = el.find(f"{{{P}}}spPr")
    geom = sp_pr.find(f"{{{A}}}prstGeom") if sp_pr is not None else None
    if el.tag == f"{{{P}}}pic":
        # демо-фото человека в круглой обрезке (обложка VK Education):
        # чужое лицо в сгенерированной колоде — всегда ошибка
        if geom is not None:
            return geom.get("prst") in ("ellipse", "flowChartConnector")
        # круг, нарисованный произвольной кривой (экспорт Google Slides)
        return sp_pr is not None and sp_pr.find(f"{{{A}}}custGeom") is not None
    fill = sp_pr.find(f"{{{A}}}solidFill/{{{A}}}srgbClr") if sp_pr is not None else None
    if geom is None or geom.get("prst") not in _STUB_GEOMS or fill is None:
        return False
    if len(fill):  # трансформы цвета — не угадываем
        return False
    try:
        r, g, b = (int(fill.get("val")[i : i + 2], 16) / 255 for i in (0, 2, 4))
    except (TypeError, ValueError):
        return False
    _h, sat, val = colorsys.rgb_to_hsv(r, g, b)
    return sat < 0.3 and 0.3 <= val <= 0.9  # серый и сине-серый (5A6775)


def remove_orphans(
    before_xml: bytes, after_xml: bytes, slide_w: int, slide_h: int
) -> tuple[bytes, int]:
    """(xml, число удалённых фигур). *before_xml* — эталон до заливки."""
    before = etree.fromstring(before_xml)
    had_text = {
        sid
        for el in before.iter(f"{{{P}}}sp")
        if _text(el) and (sid := _shape_id(el)) is not None
    }
    root = etree.fromstring(after_xml)
    tree = root.find(f"{{{P}}}cSld/{{{P}}}spTree")
    if tree is None:
        return after_xml, 0
    shapes = [(el, b) for el in tree if el.tag in _SHAPES if (b := _box(el)) is not None]
    # серая заглушка или демо-фото спикера — у колоды фото нет
    stubs = [
        el for el in tree
        if el.tag in (f"{{{P}}}sp", f"{{{P}}}pic")
        and (b := _box(el)) is not None
        and _is_photo_stub(el, b, slide_w)
    ]
    cleared = [
        (el, b) for el, b in shapes
        if _shape_id(el) in had_text and not _text(el)
    ]
    texts = [b for el, b in shapes if _text(el)]
    near = int(_NEAR * min(slide_w, slide_h))
    slide_area = float(slide_w * slide_h)
    doomed: list[etree._Element] = stubs + [
        el for el, _ in cleared if _is_visible(el) and el not in stubs
    ]
    for el, b in shapes:
        if el in doomed or _text(el) or _shape_id(el) in had_text:
            continue
        if b.w * b.h > _MAX_DECOR_AREA * slide_area or not _is_visible(el):
            continue
        if any(_gap(b, cb) <= near for _, cb in cleared) and not any(
            _gap(b, tb) <= near for tb in texts
        ):
            doomed.append(el)
    for el in doomed:
        tree.remove(el)
    if not doomed:
        return after_xml, 0
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True), len(
        doomed
    )
