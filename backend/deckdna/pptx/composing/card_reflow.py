"""Перераскладка карточек под реальный объём контента.

Проблема (живые колоды 28.09): эталонный слайд шаблона клонируется целиком,
текст заливается в первые слоты, а остальные карточки сетки остаются
пустыми — «дырявые гриды» на большинстве контентных слайдов.

Решение — тот же приём, что у PPTAgent (edit-based генерация: удалять
неиспользованные элементы эталона, а не оставлять их) плюс перераскладка:

1. найти повторяющиеся «карточки» — фигуры-подложки одинакового размера,
   не пересекающиеся между собой, каждая с текстовой фигурой внутри;
2. карточка = подложка + всё, что лежит внутри её рамки (маркер, заголовок,
   текст, иконка, картинка);
3. незаполненные карточки удаляются целиком;
4. оставшиеся растягиваются на освободившееся место сетки — текст получает
   нормальную площадь, композиция остаётся композицией шаблона.

Всё определяется геометрией и повторяемостью, без имён и индексов
конкретного шаблона. Слайды без распознанной сетки не меняются.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from lxml import etree

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_SHAPE_TAGS = {f"{{{P}}}{t}" for t in ("sp", "grpSp", "pic", "graphicFrame", "cxnSp")}

_SIZE_TOL = 0.04  # доля размера: «одинаковые» подложки
_MIN_CARD_AREA = 0.012  # доля площади слайда
_MAX_CARD_AREA = 0.45
_EDGE_TOL = 0.004  # допуск «лежит внутри» (доля стороны слайда)
_FONT_GROWTH_CAP = 1.6
# «содержательный» текст карточки — хотя бы одно слово из букв; крупная
# нумерация «01», «03» и маркеры — оформление шаблона, а не контент
_WORD_RE = re.compile(r"[^\W\d_]{2,}")


@dataclass
class _Box:
    x: int
    y: int
    w: int
    h: int

    @property
    def r(self) -> int:
        return self.x + self.w

    @property
    def b(self) -> int:
        return self.y + self.h

    def contains(self, o: _Box, tol_x: int, tol_y: int) -> bool:
        return (
            o.x >= self.x - tol_x
            and o.y >= self.y - tol_y
            and o.r <= self.r + tol_x
            and o.b <= self.b + tol_y
        )

    def overlaps(self, o: _Box) -> bool:
        return min(self.r, o.r) > max(self.x, o.x) and min(self.b, o.b) > max(self.y, o.y)


@dataclass
class _Card:
    container: etree._Element
    box: _Box
    members: list[tuple[etree._Element, _Box]] = field(default_factory=list)
    filled: bool = False


@dataclass
class ReflowReport:
    cards_found: int = 0
    cards_removed: int = 0
    cards_kept: int = 0
    font_grown: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "cards_found": self.cards_found,
            "cards_removed": self.cards_removed,
            "cards_kept": self.cards_kept,
            "font_grown": self.font_grown,
        }


def _xfrm(el: etree._Element) -> etree._Element | None:
    tag = etree.QName(el).localname
    if tag == "grpSp":
        return el.find(f"{{{P}}}grpSpPr/{{{A}}}xfrm")
    if tag == "graphicFrame":
        return el.find(f"{{{P}}}xfrm")
    return el.find(f"{{{P}}}spPr/{{{A}}}xfrm")


def _box(el: etree._Element) -> _Box | None:
    x = _xfrm(el)
    if x is None:
        return None
    off, ext = x.find(f"{{{A}}}off"), x.find(f"{{{A}}}ext")
    if off is None or ext is None:
        return None
    try:
        return _Box(int(off.get("x")), int(off.get("y")), int(ext.get("cx")), int(ext.get("cy")))
    except (TypeError, ValueError):
        return None


def _set_box(el: etree._Element, box: _Box) -> None:
    x = _xfrm(el)
    if x is None:
        return
    off, ext = x.find(f"{{{A}}}off"), x.find(f"{{{A}}}ext")
    off.set("x", str(int(box.x)))
    off.set("y", str(int(box.y)))
    ext.set("cx", str(max(1, int(box.w))))
    ext.set("cy", str(max(1, int(box.h))))


def _text(el: etree._Element) -> str:
    return "".join(t.text or "" for t in el.iter(f"{{{A}}}t")).strip()


def _has_tx_body(el: etree._Element) -> bool:
    return el.find(f".//{{{P}}}txBody") is not None


def _is_placeholder(el: etree._Element) -> bool:
    return el.find(f".//{{{P}}}nvPr/{{{P}}}ph") is not None


def _same_size(a: _Box, b: _Box) -> bool:
    return abs(a.w - b.w) <= _SIZE_TOL * max(a.w, b.w) and abs(a.h - b.h) <= _SIZE_TOL * max(
        a.h, b.h
    )


def _text_slot(card: _Card) -> int:
    """Сколько текстовых мест у карточки: собственный текст (фигура с
    txBody или группа) плюс текстовые фигуры внутри шириной ≥40%."""
    own = 1 if _has_tx_body(card.container) else 0
    inner = sum(
        1 for m, mb in card.members if _has_tx_body(m) and mb.w >= 0.4 * card.box.w
    )
    return own + inner


def _find_cards(
    shapes: list[tuple[etree._Element, _Box]], sw: int, sh: int
) -> list[_Card]:
    """Самый многочисленный кластер одинаковых непересекающихся подложек,
    каждая из которых содержит текстовую фигуру."""
    area = sw * sh
    # подложка карточки — фигура без текста; но карточкой может быть и сама
    # текстовая фигура с заливкой/рамкой, и группа (текст внутри неё)
    candidates = [
        (el, b)
        for el, b in shapes
        if not _is_placeholder(el)
        and _MIN_CARD_AREA <= (b.w * b.h) / area <= _MAX_CARD_AREA
    ]
    clusters: list[list[tuple[etree._Element, _Box]]] = []
    for el, b in candidates:
        for cluster in clusters:
            if _same_size(cluster[0][1], b) and not any(b.overlaps(o) for _, o in cluster):
                cluster.append((el, b))
                break
        else:
            clusters.append([(el, b)])

    best: list[_Card] = []
    valid: list[list[_Card]] = []
    for cluster in clusters:
        if len(cluster) < 2:
            continue
        containers = {id(el) for el, _ in cluster}
        cards: list[_Card] = []
        for el, b in cluster:
            card = _Card(el, b)
            for other, ob in shapes:
                if id(other) in containers or other is el:
                    continue
                if ob.w * ob.h >= b.w * b.h:
                    continue
                # центр внутри карточки: крупная нумерация «01» и иконки
                # часто выступают за её рамку, но принадлежат ей
                cx, cy = ob.x + ob.w / 2, ob.y + ob.h / 2
                if b.x <= cx <= b.r and b.y <= cy <= b.b:
                    card.members.append((other, ob))
            cards.append(card)
        # в каждой карточке — текстовая рамка заметной ширины (точка-маркер
        # с пустым txBody не делает плашку карточкой)
        if not all(_text_slot(c) for c in cards):
            continue
        # один элемент не может принадлежать двум карточкам
        owners: dict[int, int] = {}
        for c in cards:
            for m, _ in c.members:
                owners[id(m)] = owners.get(id(m), 0) + 1
        if any(n > 1 for n in owners.values()):
            continue
        valid.append(cards)
    # Выигрывает кластер, покрывающий больше текстовых фигур; при равенстве —
    # с большим числом карточек. Полосы рядов покрывают те же тексты, что и
    # карточки внутри них, но их меньше; плашки-заголовки внутри карточек
    # покрывают только заголовки, без основного текста.
    def score(cards: list[_Card]) -> tuple[int, int]:
        covered = sum(_text_slot(c) for c in cards)
        return covered, len(cards)

    for cards in valid:
        if not best or score(cards) > score(best):
            best = cards
    return best


def _axis_positions(values: list[int], tol: int) -> list[int]:
    out: list[int] = []
    for v in sorted(values):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


def _grid(cards: list[_Card], sw: int, sh: int) -> tuple[list[int], list[int]]:
    cols = _axis_positions([c.box.x for c in cards], int(0.02 * sw))
    rows = _axis_positions([c.box.y for c in cards], int(0.02 * sh))
    return cols, rows


def _min_gap(starts: list[int], size: int) -> int:
    gaps = [b - (a + size) for a, b in zip(starts, starts[1:], strict=False)]
    gaps = [g for g in gaps if g > 0]
    return min(gaps) if gaps else 0


def _move_member(
    el: etree._Element, mb: _Box, old: _Box, new: _Box
) -> _Box:
    """Новое место элемента карточки при смене рамки карточки ``old → new``.

    Отступы от краёв сохраняются. Широкие элементы (≥ половины ширины
    карточки) растягиваются по ширине; элемент, доходящий почти до низа и
    занимающий заметную высоту (основной текст), — по высоте. Мелкие
    элементы в правой/нижней половине держат расстояние до правого/нижнего
    края (иконки в углу)."""
    dx, dy = mb.x - old.x, mb.y - old.y
    right_gap, bottom_gap = old.r - mb.r, old.b - mb.b
    rigid = etree.QName(el).localname in {"pic", "graphicFrame"}  # пропорции картинки
    wide = mb.w >= 0.5 * old.w and not rigid
    tall_bottom = mb.h >= 0.3 * old.h and bottom_gap <= 0.2 * old.h and not rigid
    if wide:
        x, w = new.x + dx, max(1, new.w - dx - right_gap)
    elif dx > old.w / 2:
        x, w = new.r - right_gap - mb.w, mb.w
    else:
        x, w = new.x + dx, mb.w
    if tall_bottom:
        y, h = new.y + dy, max(1, new.h - dy - bottom_gap)
    elif (dy > old.h / 2 or rigid) and not _text(el):
        y, h = new.b - bottom_gap - mb.h, mb.h
    else:
        y, h = new.y + dy, mb.h
    return _Box(int(x), int(y), int(w), int(h))


def _grow_fonts(el: etree._Element, factor: float) -> int:
    """Увеличить явные кегли текста фигуры (карточка стала заметно больше).
    Кегли округляются до 0.5pt; рост ограничен ``_FONT_GROWTH_CAP``."""
    factor = min(factor, _FONT_GROWTH_CAP)
    if factor < 1.15:
        return 0
    grown = 0
    for tag in ("rPr", "defRPr", "endParaRPr"):
        for rpr in el.iter(f"{{{A}}}{tag}"):
            sz = rpr.get("sz")
            if not sz:
                continue
            new = int(round(int(sz) * factor / 50.0) * 50)
            if new > int(sz):
                rpr.set("sz", str(new))
                grown += 1
    return grown


def _expand_bodies(cards: list[_Card]) -> int:
    """Нижний текст карточки (основной) дотягивается до низа карточки.

    В эталонах шаблонов текстовая рамка часто занимает верхнюю треть
    карточки, а ниже — пустота: настоящему тексту некуда расти, кегль
    остаётся мелким. Рамка растёт вниз до ближайшего элемента под ней
    (иконка, нумерация) или до низа карточки с тем же отступом, что сверху,
    и вверх — в место пустых невидимых рамок (незаполненный заголовок
    карточки), до ближайшей видимой фигуры над ней."""
    grown = 0
    for c in cards:
        box = _box(c.container) or c.box
        members = [(m, _box(m)) for m, _ in c.members if m.getparent() is not None]
        texts = [
            (m, b) for m, b in members if b is not None and _text(m) and b.w >= 0.4 * box.w
        ]
        if not texts:
            continue
        m, b = max(texts, key=lambda mb: mb[1].b)
        top_pad = min(bb.y for _, bb in members if bb is not None) - box.y
        limit = box.b - max(top_pad, int(0.06 * box.h))
        for other, ob in members:
            if other is m or ob is None or ob.y < b.b:
                continue
            if min(ob.r, b.r) > max(ob.x, b.x):  # под рамкой по горизонтали
                limit = min(limit, ob.y - int(0.02 * box.h))
        top = b.y
        # вверх — в место пустых невидимых рамок (незаполненная строка
        # заголовка карточки): до низа ближайшей видимой фигуры над текстом
        ceiling = box.y + max(top_pad, int(0.06 * box.h))
        for other, ob in members:
            if other is m or ob is None or ob.y >= b.y or not _is_visible(other):
                continue
            if min(ob.r, b.r) > max(ob.x, b.x):
                ceiling = max(ceiling, ob.b + int(0.03 * box.h))
        if ceiling < b.y - int(0.1 * box.h):
            top = ceiling
        bottom = limit if limit > b.b + int(0.1 * box.h) else b.b
        if top < b.y or bottom > b.b:
            _set_box(m, _Box(b.x, top, b.w, bottom - top))
            grown += 1
    return grown


_FILL_TAGS = {f"{{{A}}}{t}" for t in ("solidFill", "gradFill", "blipFill", "pattFill")}


def _is_visible(el: etree._Element) -> bool:
    """Фигура что-то рисует: текст, картинка, заливка или контур."""
    if _text(el) or etree.QName(el).localname in ("pic", "graphicFrame", "grpSp", "cxnSp"):
        return True
    sp_pr = el.find(f"{{{P}}}spPr")
    if sp_pr is None:
        return False
    if any(child.tag in _FILL_TAGS for child in sp_pr):
        return True
    ln = sp_pr.find(f"{{{A}}}ln")
    if ln is not None and ln.find(f"{{{A}}}noFill") is None and len(ln):
        return True
    # стиль темы (p:style) без явного noFill тоже рисует заливку
    style = el.find(f"{{{P}}}style")
    no_fill = sp_pr.find(f"{{{A}}}noFill") is not None
    return style is not None and not no_fill


def _dump(root: etree._Element) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _shape_id(el: etree._Element) -> str | None:
    """Return the OOXML id of a shape, when one is declared."""
    for path in (
        f".//{{{P}}}nvSpPr/{{{P}}}cNvPr",
        f".//{{{P}}}nvCxnSpPr/{{{P}}}cNvPr",
        f".//{{{P}}}nvGraphicFramePr/{{{P}}}cNvPr",
    ):
        node = el.find(path)
        if node is not None and node.get("id"):
            return node.get("id")
    return None


def reflow_cards(
    slide_xml: bytes,
    slide_w: int,
    slide_h: int,
    *,
    filled_shape_ids: set[str] | frozenset[str] | None = None,
) -> tuple[bytes, ReflowReport]:
    """Удалить незаполненные карточки сетки и растянуть оставшиеся.

    ``filled_shape_ids`` is supplied by ``replace_text_runs`` when the
    compiler has just filled the slide.  It preserves numeric content such as
    ``42%`` without mistaking donor numbering (``01``/``02``) for content.
    Direct callers that do not have replacement history retain the historical
    letter-based fallback.
    """
    report = ReflowReport()
    root = etree.fromstring(slide_xml)
    tree = root.find(f"{{{P}}}cSld/{{{P}}}spTree")
    if tree is None:
        return slide_xml, report
    shapes = [(el, b) for el in tree if el.tag in _SHAPE_TAGS if (b := _box(el)) is not None]
    cards = _find_cards(shapes, slide_w, slide_h)
    report.cards_found = len(cards)
    if not cards:
        return slide_xml, report
    for c in cards:
        if filled_shape_ids is not None:
            ids = {_shape_id(c.container)} | {_shape_id(m) for m, _ in c.members}
            c.filled = bool(ids & filled_shape_ids)
        else:
            c.filled = bool(_WORD_RE.search(_text(c.container))) or any(
                _WORD_RE.search(_text(m)) or etree.QName(m).localname == "graphicFrame"
                for m, _ in c.members
            )
    kept = [c for c in cards if c.filled]
    removed = [c for c in cards if not c.filled]
    if not kept:
        # сетка целиком пустая (весь текст слайда стоит в другом месте) —
        # пустые рамки демо-структуры шаблона не должны остаться в колоде
        for c in removed:
            for el in [c.container, *(m for m, _ in c.members)]:
                parent = el.getparent()
                if parent is not None:
                    parent.remove(el)
        report.cards_removed = len(removed)
        return _dump(root), report
    if not removed:
        report.cards_kept = len(kept)
        if kept and _expand_bodies(kept):
            return _dump(root), report
        return slide_xml, report

    cols, rows = _grid(cards, slide_w, slide_h)
    size = cards[0].box
    gap_x = _min_gap(cols, size.w) or int(0.012 * slide_w)
    gap_y = _min_gap(rows, size.h) or int(0.02 * slide_h)
    region = _Box(
        min(c.box.x for c in cards),
        min(c.box.y for c in cards),
        max(c.box.r for c in cards) - min(c.box.x for c in cards),
        max(c.box.b for c in cards) - min(c.box.y for c in cards),
    )

    ids_in_cards = {id(c.container) for c in cards} | {
        id(m) for c in cards for m, _ in c.members
    }
    tol_x, tol_y = int(_EDGE_TOL * slide_w), int(_EDGE_TOL * slide_h)
    # полосы-подложки рядов: содержат ≥2 карточек, но не все
    strips = [
        (el, b)
        for el, b in shapes
        if id(el) not in ids_in_cards
        and not _text(el)
        and 2 <= sum(1 for c in cards if b.contains(c.box, tol_x, tol_y)) < len(cards)
    ]
    strip_ids = {id(el) for el, _ in strips}
    slide_area = slide_w * slide_h

    def foreign_in(area: _Box) -> bool:
        """Чужая фигура (не карточка, не полоса, не общая подложка) в области."""
        def inter(b: _Box) -> int:
            w = min(b.r, area.r) - max(b.x, area.x)
            h = min(b.b, area.b) - max(b.y, area.y)
            return max(0, w) * max(0, h)

        # заметное пересечение (≥15% площади фигуры): заголовок, чуть
        # заходящий в верх сетки, чужой фигурой не считается
        return any(
            id(el) not in ids_in_cards
            and id(el) not in strip_ids
            and not b.contains(area, tol_x, tol_y)
            and b.w * b.h > 0.005 * slide_area
            and inter(b) >= 0.15 * b.w * b.h
            for el, b in shapes
        )

    # удалить незаполненные карточки целиком
    for c in removed:
        for el in [c.container, *(m for m, _ in c.members)]:
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)

    target = region
    n_cols = len(cols)
    if foreign_in(region):
        # растягиваем только по рядам без чужих фигур; иначе — только удаление
        bands = []
        for y in rows:
            row_cards = [c for c in cards if abs(c.box.y - y) <= int(0.02 * slide_h)]
            band = _Box(region.x, y, region.w, max(c.box.h for c in row_cards))
            if not foreign_in(band):
                bands.append((band, row_cards))
        kept_ids = {id(c) for c in kept}
        clean_ids = {id(c) for _, rc in bands for c in rc}
        if not kept_ids <= clean_ids:
            report.cards_removed = len(removed)
            report.cards_kept = len(kept)
            _expand_bodies(kept)
            return _dump(root), report
        used = [b for b, rc in bands if any(id(c) in kept_ids for c in rc)]
        target = _Box(
            region.x,
            min(b.y for b in used),
            region.w,
            max(b.b for b in used) - min(b.y for b in used),
        )
        if foreign_in(target):
            report.cards_removed = len(removed)
            report.cards_kept = len(kept)
            _expand_bodies(kept)
            return _dump(root), report

    k = len(kept)
    n_cols = max(1, min(k, n_cols))
    n_rows = math.ceil(k / n_cols)
    cell_w = (target.w - (n_cols - 1) * gap_x) / n_cols
    cell_h = (target.h - (n_rows - 1) * gap_y) / n_rows
    kept.sort(key=lambda c: (c.box.y, c.box.x))
    new_boxes: list[_Box] = []
    for i, c in enumerate(kept):
        r, col = divmod(i, n_cols)
        new = _Box(
            int(target.x + col * (cell_w + gap_x)),
            int(target.y + r * (cell_h + gap_y)),
            int(cell_w),
            int(cell_h),
        )
        new_boxes.append(new)
        old = c.box
        _set_box(c.container, new)
        for m, mb in c.members:
            nb = _move_member(m, mb, old, new)
            _set_box(m, nb)
            # кегль растёт у основного текста карточки (заметная высота),
            # когда его рамка заметно выросла; однострочный заголовок не
            # трогаем — он переполнится
            if _text(m) and mb.h >= 0.25 * old.h:
                growth = math.sqrt((nb.w * nb.h) / max(1, mb.w * mb.h))
                if growth >= 1.2:
                    report.font_grown += _grow_fonts(m, growth)
    # полосы рядов, в которых не осталось карточек, удаляются
    for el, b in strips:
        if not any(b.contains(nb, tol_x, tol_y) for nb in new_boxes):
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)
    _expand_bodies(kept)
    report.cards_removed = len(removed)
    report.cards_kept = k
    return _dump(root), report


# --------------------------------------------------------------------------
# Пары «заголовок + фраза» для карточек эталона
# --------------------------------------------------------------------------

_LABEL_SPLIT = re.compile(r"^(?P<label>[^:—–]{2,48}?)\s*(?::|\s[—–])\s+(?P<body>\S.*)$", re.S)


def split_label(text: str) -> tuple[str | None, str]:
    """«Короткий заголовок: развёрнутая фраза» → (заголовок, фраза).

    Заголовок — до 6 слов и 48 символов, не число; иначе (None, текст)."""
    m = _LABEL_SPLIT.match(text.strip())
    if not m:
        return None, text.strip()
    label, body = m.group("label").strip(), m.group("body").strip()
    if len(label.split()) > 6 or not _WORD_RE.search(label) or not _WORD_RE.search(body):
        return None, text.strip()
    return label, body


def card_slots(slide_xml: bytes, slide_w: int, slide_h: int) -> tuple[int, int]:
    """(число карточек, текстовых слотов на карточку) эталона; (0, 0) — не сетка.

    Слот — текстовая фигура внутри карточки с демо-текстом шаблона (именно
    такие заполняет text_replace) и шириной ≥40% карточки."""
    root = etree.fromstring(slide_xml)
    tree = root.find(f"{{{P}}}cSld/{{{P}}}spTree")
    if tree is None:
        return 0, 0
    shapes = [(el, b) for el in tree if el.tag in _SHAPE_TAGS if (b := _box(el)) is not None]
    cards = _find_cards(shapes, slide_w, slide_h)
    if not cards:
        return 0, 0
    per_card = {
        sum(1 for m, mb in c.members if _text(m) and mb.w >= 0.4 * c.box.w) for c in cards
    }
    if len(per_card) != 1:
        return len(cards), 0
    return len(cards), per_card.pop()


def pair_texts_for_cards(texts: list[str], slots_per_card: int) -> list[str]:
    """Тексты по слотам карточек: при двух слотах на карточку каждый пункт
    «Заголовок: фраза» занимает заголовок и текст одной карточки; пункт без
    заголовка идёт в текст, заголовок карточки остаётся пустым — иначе
    предложение попадало в мелкий однострочный заголовок, а карточка
    делилась между двумя пунктами."""
    if slots_per_card != 2:
        return texts
    out: list[str] = []
    for t in texts:
        label, body = split_label(t)
        if label and body:
            body = body[0].upper() + body[1:]
        out.extend([label or "", body])
    return out


# --------------------------------------------------------------------------
# Клонирование карточек (пунктов больше, чем карточек в эталоне)
# --------------------------------------------------------------------------

def _max_shape_id(root: etree._Element) -> int:
    ids = [
        int(el.get("id"))
        for el in root.iter(f"{{{P}}}cNvPr")
        if (el.get("id") or "").isdigit()
    ]
    return max(ids, default=1)


def _renumber(el: etree._Element, next_id: int) -> int:
    for c in el.iter(f"{{{P}}}cNvPr"):
        c.set("id", str(next_id))
        next_id += 1
    return next_id


def clone_cards(
    slide_xml: bytes, need: int, slide_w: int, slide_h: int
) -> tuple[bytes, int]:
    """Добавить в сетку эталона карточки-копии, пока их не станет ``need``.

    Копируется последняя карточка сетки целиком (подложка и всё внутри —
    маркер, заголовок, текст, иконка): стиль шаблона сохраняется, меняется
    только число блоков. Копии встают в документ сразу за последней
    карточкой — текст ложится в них в порядке чтения. Затем все карточки
    раскладываются сеткой в исходной области. Возвращает (xml, добавлено)."""
    root = etree.fromstring(slide_xml)
    tree = root.find(f"{{{P}}}cSld/{{{P}}}spTree")
    if tree is None:
        return slide_xml, 0
    shapes = [(el, b) for el in tree if el.tag in _SHAPE_TAGS if (b := _box(el)) is not None]
    cards = _find_cards(shapes, slide_w, slide_h)
    if len(cards) < 2 or need <= len(cards):
        return slide_xml, 0
    cards.sort(key=lambda c: (c.box.y, c.box.x))
    cols, rows = _grid(cards, slide_w, slide_h)
    size = cards[0].box
    gap_x = _min_gap(cols, size.w) or int(0.012 * slide_w)
    gap_y = _min_gap(rows, size.h) or int(0.02 * slide_h)
    region = _Box(
        min(c.box.x for c in cards),
        min(c.box.y for c in cards),
        max(c.box.r for c in cards) - min(c.box.x for c in cards),
        max(c.box.b for c in cards) - min(c.box.y for c in cards),
    )
    source = cards[-1]
    group = [source.container, *(m for m, _ in source.members)]
    anchor = max(group, key=lambda el: list(tree).index(el))
    next_id = _max_shape_id(root) + 1
    added: list[_Card] = []
    for _ in range(need - len(cards)):
        copies = []
        for el in group:
            dup = etree.fromstring(etree.tostring(el))
            next_id = _renumber(dup, next_id)
            copies.append(dup)
        for dup in copies:
            anchor.addnext(dup)
            anchor = dup
        members = [(d, _box(d)) for d in copies[1:]]
        added.append(_Card(copies[0], source.box, [(m, b) for m, b in members if b]))

    all_cards = cards + added
    k = len(all_cards)
    if len(rows) == 1:
        n_cols = k if k <= 5 else math.ceil(k / 2)
    else:
        n_cols = max(len(cols), math.ceil(k / max(1, len(rows))))
    n_rows = math.ceil(k / n_cols)
    cell_w = (region.w - (n_cols - 1) * gap_x) / n_cols
    cell_h = (region.h - (n_rows - 1) * gap_y) / n_rows
    for i, c in enumerate(all_cards):
        r, col = divmod(i, n_cols)
        new = _Box(
            int(region.x + col * (cell_w + gap_x)),
            int(region.y + r * (cell_h + gap_y)),
            int(cell_w),
            int(cell_h),
        )
        old = c.box
        _set_box(c.container, new)
        for m, mb in c.members:
            _set_box(m, _move_member(m, mb, old, new))
    return _dump(root), len(added)
