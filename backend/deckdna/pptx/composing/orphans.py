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

import re

from lxml import etree

from deckdna.pptx.composing.card_reflow import _Box, _box, _is_visible, _text
from deckdna.pptx.composing.text_replace import _TEMPLATE_CREDIT_RE

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_SHAPES = {f"{{{P}}}sp", f"{{{P}}}cxnSp"}
_MAX_DECOR_AREA = 0.02  # доля площади слайда
_NEAR = 0.04  # «вплотную»: доля меньшей стороны слайда
_SOCIAL_META_RE = re.compile(
    r"\b(?:facebook|twitter|instagram|linkedin|youtube|tiktok|telegram|whatsapp|"
    r"vk\.com|t\.me|x\.com|social|соцсет\w*|иконк\w*)\b",
    re.I,
)
_SOCIAL_BADGE_RE = re.compile(r"^(?:fb|vk|tg|in|ig|yt|tt|ok|tw|wa|@[\w.-]+)$", re.I)


def _has_social_meta(el: etree._Element) -> bool:
    for cnv in el.iter(f"{{{P}}}cNvPr"):
        meta = f"{cnv.get('name') or ''} {cnv.get('descr') or ''} {cnv.get('title') or ''}"
        if _SOCIAL_META_RE.search(meta):
            return True
    return el.find(f".//{{{A}}}hlinkClick") is not None


def _is_title_shape(el: etree._Element) -> bool:
    ph = el.find(f".//{{{P}}}nvSpPr/{{{P}}}nvPr/{{{P}}}ph")
    return ph is not None and ph.get("type") in {"title", "ctrTitle"}


def _content_placeholder(el: etree._Element) -> bool:
    ph = el.find(f"{{{P}}}nvSpPr/{{{P}}}nvPr/{{{P}}}ph")
    return ph is not None and ph.get("type", "obj") in {"body", "obj", "subTitle"}


def _shape_id(el: etree._Element) -> str | None:
    c = el.find(f".//{{{P}}}cNvPr")
    return c.get("id") if c is not None else None


def _generated(el: etree._Element) -> bool:
    """Фигура создана нами (compose_*): id у новых фигур может совпасть
    с id уже удалённого донорского текста — по id её нельзя считать
    «очищенным слотом» эталона (живой дефект: панель раскладки
    data-split уходила вместе со слотом, а её чернила уже посчитаны
    под панель)."""
    c = el.find(f".//{{{P}}}cNvPr")
    return c is not None and (c.get("name") or "").startswith("DeckDNA ")


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
    before_xml: bytes,
    after_xml: bytes,
    slide_w: int,
    slide_h: int,
    keep_pictures: bool = False,
    *,
    closing: bool = False,
) -> tuple[bytes, int]:
    """(xml, число удалённых фигур). *before_xml* — эталон до заливки.
    *keep_pictures* — на слайде будет картинка колоды: рамки-картинки не
    трогаем (одна из них — хост), мелкие значки у пустых слотов — да."""
    before = etree.fromstring(before_xml)
    had_text = {
        sid for el in before.iter(f"{{{P}}}sp") if _text(el) and (sid := _shape_id(el)) is not None
    }
    credit_ids = {
        sid
        for el in before.iter(f"{{{P}}}sp")
        if (sid := _shape_id(el)) is not None
        and _TEMPLATE_CREDIT_RE.search("".join(t.text or "" for t in el.iter(f"{{{A}}}t")).strip())
    }
    root = etree.fromstring(after_xml)
    tree = root.find(f"{{{P}}}cSld/{{{P}}}spTree")
    if tree is None:
        return after_xml, 0
    slide_area = float(slide_w * slide_h)
    if closing:
        has_user_social = any(
            ("@" in _text(el) or "http" in _text(el).lower())
            and not _SOCIAL_BADGE_RE.fullmatch(_text(el).strip())
            for el in tree
            if el.tag in _SHAPES and _text(el) and not _is_title_shape(el)
        )
        if not has_user_social:
            for el in tree:
                if el.tag == f"{{{P}}}sp" and not _generated(el) and not _is_title_shape(el):
                    b = _box(el)
                    txt = _text(el).strip()
                    if (
                        b is not None
                        and 0 < b.w * b.h <= _MAX_DECOR_AREA * slide_area
                        and 0.4 <= b.w / max(b.h, 1) <= 2.5
                        and _SOCIAL_BADGE_RE.fullmatch(txt)
                    ):
                        for t in el.iter(f"{{{A}}}t"):
                            t.text = ""
    shapes = [(el, b) for el in tree if el.tag in _SHAPES if (b := _box(el)) is not None]
    # серая заглушка или демо-фото спикера — у колоды фото нет
    stubs = [
        el
        for el in tree
        if el.tag in (f"{{{P}}}sp", f"{{{P}}}pic")
        and (b := _box(el)) is not None
        and _is_photo_stub(el, b, slide_w)
        and not (keep_pictures and el.tag == f"{{{P}}}pic")
    ]
    cleared = [
        (el, b)
        for el, b in shapes
        if (_shape_id(el) in had_text or _content_placeholder(el))
        and not _text(el)
        and not _generated(el)
    ]
    texts = [b for el, b in shapes if _text(el)]
    near = int(_NEAR * min(slide_w, slide_h))
    doomed: list[etree._Element] = stubs + [
        el
        for el, _ in cleared
        if (_is_visible(el) or _shape_id(el) in credit_ids)
        and el not in stubs
        and not _content_placeholder(el)
    ]
    # Пустой ph остаётся: он подавляет подсказку макета в LibreOffice.
    # Рамка карточки при этом не должна рисоваться.
    hidden = False
    for el, _ in cleared:
        if _content_placeholder(el) and _is_visible(el):
            props = el.find(f"{{{P}}}spPr")
            if props is not None:
                for child in list(props):
                    if etree.QName(child).localname in {
                        "solidFill",
                        "gradFill",
                        "blipFill",
                        "pattFill",
                        "grpFill",
                        "noFill",
                        "ln",
                    }:
                        props.remove(child)
                etree.SubElement(props, f"{{{A}}}noFill")
                etree.SubElement(etree.SubElement(props, f"{{{A}}}ln"), f"{{{A}}}noFill")
                hidden = True
    # мелкие значки-картинки и векторные группы-иконки у очищенного слота —
    # тоже оформление пункта, которого у колоды нет (иконки без подписей,
    # иконки соцсетей на финальном слайде).
    # Логотип в УГЛУ слайда (обе координаты центра в краевой полосе, как
    # в slide_frame._near_edge) — брендинг, а не иконка пункта: очищенная
    # шапочная рамка рядом не делает его сиротой.
    corner = 0.12

    def _in_corner(b: _Box) -> bool:
        return (b.x + b.w / 2 < corner * slide_w or b.x + b.w / 2 > (1 - corner) * slide_w) and (
            b.y + b.h / 2 < corner * slide_h or b.y + b.h / 2 > (1 - corner) * slide_h
        )

    icons = [
        (el, b)
        for el in tree
        if el.tag in (f"{{{P}}}pic", f"{{{P}}}grpSp")
        and not _text(el)
        and not _generated(el)
        and (b := _box(el)) is not None
        and 0 < b.w * b.h <= _MAX_DECOR_AREA * slide_w * slide_h
        and not _in_corner(b)
    ]
    for el, b in shapes + icons:
        if el in doomed or _text(el) or _shape_id(el) in had_text or _generated(el):
            continue
        if b.w * b.h > _MAX_DECOR_AREA * slide_area:
            continue
        if el.tag not in (f"{{{P}}}pic", f"{{{P}}}grpSp") and not _is_visible(el):
            continue
        if any(_gap(b, cb) <= near for _, cb in cleared) and not any(
            _gap(b, tb) <= near for tb in texts
        ):
            doomed.append(el)
    if cleared or closing:
        small_icons = [
            (el, b)
            for el in tree
            if el.tag in (f"{{{P}}}pic", f"{{{P}}}grpSp", f"{{{P}}}sp")
            and not _text(el)
            and not _generated(el)
            and not _content_placeholder(el)
            and not _is_title_shape(el)
            and (b := _box(el)) is not None
            and 0 < b.w * b.h <= _MAX_DECOR_AREA * slide_area
            and 0.4 <= b.w / max(b.h, 1) <= 2.5
            and not _in_corner(b)
            and not any(_gap(b, tb) <= near for tb in texts)
        ]
        no_body_text = closing and not any(_text(el) for el, _ in shapes if not _is_title_shape(el))
        for el, b in small_icons:
            if el in doomed:
                continue
            aligned_peers = any(
                other is not el
                and (abs(b.y - ob.y) <= max(b.h, ob.h) or abs(b.x - ob.x) <= max(b.w, ob.w))
                and 0.4 <= (b.w * b.h) / max(ob.w * ob.h, 1) <= 2.5
                for other, ob in small_icons
            )
            is_vector_icon = el.tag == f"{{{P}}}grpSp" or el.find(f".//{{{A}}}custGeom") is not None
            if _has_social_meta(el) or aligned_peers or (no_body_text and is_vector_icon):
                doomed.append(el)
    # Картинка внутри удаляемой подложки принадлежит тому же пустому
    # пункту, даже если от самой подписи она чуть дальше порога near.
    boxes = [_box(el) for el in doomed]
    for el, b in icons:
        if el not in doomed and any(
            cb is not None and cb.x <= b.x and cb.y <= b.y and cb.r >= b.r and cb.b >= b.b
            for cb in boxes
        ):
            doomed.append(el)

    # Опустевшая панель-контейнер: видимая фигура без своего текста,
    # в эталоне державшая контентные фигуры (тексты, картинки, рамки
    # данных), а после заливки — ни одной живой внутри. Пустая колонка-
    # подложка или слот картинки остаются цветным прямоугольником,
    # «съедающим» место под график (живой дефект: тёмная панель эталона
    # с очищенной колонкой). Фон (≥60% площади) и мелкий декор вне
    # диапазона; центры считаются, чтобы заголовок, заходящий на край
    # панели, её не «спасал» и не губил.
    def _inside(box: _Box, inner: _Box) -> bool:
        cx, cy = inner.x + inner.w / 2, inner.y + inner.h / 2
        return box.x <= cx <= box.r and box.y <= cy <= box.b

    before_content = []
    for el in before.iter():
        tag = etree.QName(el).localname
        if tag not in ("sp", "pic", "graphicFrame", "grpSp"):
            continue
        if tag == "sp" and not _text(el):
            continue
        if (b := _box(el)) is not None:
            before_content.append(b)
    doomed_ids = {id(el) for el in doomed}
    live = [
        (el, b)
        for el in tree
        if (b := _box(el)) is not None and _is_visible(el) and id(el) not in doomed_ids
    ]
    # панель — фигура без собственного txBody: очищенные текстовые боксы
    # в панели не считаются, они ещё могут стать хостом таблицы/графика
    panels = [
        (el, b)
        for el, b in live
        if el.tag == f"{{{P}}}sp"
        and el.find(f"{{{P}}}txBody") is None
        and _MAX_DECOR_AREA * slide_area < b.w * b.h < 0.6 * slide_area
        and not _generated(el)
    ]
    panel_ids = {id(el) for el, _ in panels}

    # панель держит «в живых» только контент: очищенный текстовый бокс
    # со style/заливкой всё ещё «видим», но контента в нём нет
    def _content_bearing(el: etree._Element) -> bool:
        tag = etree.QName(el).localname
        if tag in ("pic", "graphicFrame", "cxnSp"):
            return True
        if tag == "sp":
            return bool(_text(el))
        if tag == "grpSp":
            return any(
                _text(d) or etree.QName(d).localname in ("pic", "graphicFrame")
                for d in el.iterdescendants()
            )
        return False

    live_content = [(el, b) for el, b in live if _content_bearing(el)]
    for el, b in panels:
        if not any(_inside(b, bb) for bb in before_content):
            continue
        if any(id(other) not in panel_ids and _inside(b, ob) for other, ob in live_content):
            continue
        doomed.append(el)
    for el in doomed:
        tree.remove(el)
    if not doomed and not hidden:
        return after_xml, 0
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True), len(
        doomed
    )
