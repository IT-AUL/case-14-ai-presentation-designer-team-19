"""Build simple flow diagrams as SmartArt-like grouped shapes (OR-004).

«SmartArt-like grouped editable shapes» — NOT real
OOXML SmartArt (dgm parts, a separate heavy format). Each `Diagram`
from ContentPack.diagrams becomes one `p:grpSp` holding a row of
`roundRect` step boxes with `rightArrow` connectors between them —
fully native and editable in PowerPoint.

Placement (ADR-031 §5). A flow reads left→right, so the host is chosen by
WIDTH, not by area: the widest of the emptied text bodies and the largest
free rectangle under the title. When the slide's emptied card grid (icons,
markers, row plates without text) blocks a much wider region, those orphaned
decorations are removed and the flow takes the whole area under the title —
otherwise the flow was squeezed into one narrow card row and its words broke
letter by letter while empty card markers stayed beside it.

Step text gets an explicit size: the largest one at which the longest word
fits the box without breaking and all lines fit its height (the same width
metric the audit uses), never below the readability floor.
When no host is wide enough the unit honestly drops.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from lxml import etree

from deckdna.audit.basic import EMU_PER_PT, LINE_HEIGHT_FACTOR, _wrap_metrics
from deckdna.contracts.content_pack import Diagram
from deckdna.pptx.composing.table_fill import (
    _el_box,
    _empty_body_hosts,
    _free_space_host,
    _next_shape_id,
    _visible,
)

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"

_MAX_STEPS = 8  # beyond that step text doesn't fit a row anyway
_STEP_H_EMU = 640_080  # ~0.7"
_ARROW_W_EMU = 274_320  # ~0.3"
_MIN_HOST_W_EMU = 1_800_000  # ~2" — a flow needs real width
_MAX_STEP_H_EMU = 1_280_160  # ~1.4" — taller boxes look like cards, not a flow
_INSET_EMU = 91_440  # default bodyPr lIns/rIns/tIns/bIns
_MAX_PT = 20.0
_MIN_PT = 9.0  # above the 8pt audit floor, readable on a projector
# footer band (logos, page numbers) is never cleared
_FOOTER_BAND = 0.12
_TITLE_TYPES = ("title", "ctrTitle")
_ROW_PREFERRED_PT = 12.0  # строка читается — столбец не нужен
_MIN_COLUMN_STEP_H_EMU = 320_040  # ~0.35" на шаг в столбце
_MAX_COLUMN_W_EMU = 4_572_000  # ~5": шире столбец выглядит плашкой
_NEAR = 0.04  # «вплотную»: доля меньшей стороны слайда (как orphans.py)
_HEADING_ZONE = 0.12  # текст, начинающийся в верхних 12% слайда, — заголовок


@dataclass
class DiagramFillReport:
    """What the fill actually built — surfaced into the compose report."""

    diagrams_created: int = 0
    steps_written: int = 0
    units_dropped: int = 0
    decorations_removed: int = 0
    diagram_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "diagrams_created": self.diagrams_created,
            "steps_written": self.steps_written,
            "units_dropped": self.units_dropped,
            "decorations_removed": self.decorations_removed,
            "diagram_ids": self.diagram_ids,
        }


def _step_sp(
    sid: int, name: str, x: int, y: int, cx: int, cy: int, text: str, size_pt: float
) -> str:
    sz = int(round(size_pt * 100))
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{escape(name)}"/>'
        f"<p:cNvSpPr/><p:nvPr/></p:nvSpPr>"
        f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/>'
        f'<a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        f'<a:prstGeom prst="roundRect"><a:avLst/></a:prstGeom>'
        f'<a:solidFill><a:schemeClr val="accent1"/></a:solidFill></p:spPr>'
        f'<p:txBody><a:bodyPr anchor="ctr" wrap="square" lIns="{_INSET_EMU}" '
        f'tIns="{_INSET_EMU}" rIns="{_INSET_EMU}" bIns="{_INSET_EMU}"><a:noAutofit/></a:bodyPr>'
        f'<a:p><a:pPr algn="ctr"/><a:r>'
        f'<a:rPr lang="ru-RU" sz="{sz}"><a:solidFill><a:schemeClr val="lt1"/>'
        f"</a:solidFill></a:rPr>"
        f"<a:t>{escape(text)}</a:t></a:r></a:p></p:txBody></p:sp>"
    )


def _arrow_sp(
    sid: int, name: str, x: int, y: int, cx: int, cy: int, prst: str = "rightArrow"
) -> str:
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{escape(name)}"/>'
        f"<p:cNvSpPr/><p:nvPr/></p:nvSpPr>"
        f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/>'
        f'<a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        f'<a:prstGeom prst="{prst}"><a:avLst/></a:prstGeom>'
        f'<a:solidFill><a:schemeClr val="accent1"/></a:solidFill></p:spPr>'
        f"<p:txBody><a:bodyPr/><a:p/></p:txBody></p:sp>"
    )


def step_font(steps: list[str], box_w: int, max_h: int) -> tuple[float, int] | None:
    """(кегль, высота коробки) для шагов схемы или None, если даже на
    ``_MIN_PT`` самое длинное слово не помещается в ширину коробки.

    Кегль — наибольший, при котором ни одно слово не рвётся и все строки
    влезают в *max_h*; ширина слова считается той же метрикой, что у
    аудита ``text.overflow`` (``_wrap_metrics``)."""
    usable_w = box_w - 2 * _INSET_EMU
    pt = _MAX_PT
    while pt >= _MIN_PT:
        lines = 0
        fits = True
        for step in steps:
            n, widest = _wrap_metrics(step, pt, usable_w)
            if widest > usable_w:
                fits = False
                break
            lines = max(lines, n)
        if fits:
            need = int(lines * pt * LINE_HEIGHT_FACTOR * EMU_PER_PT) + 2 * _INSET_EMU
            if need <= max_h:
                return pt, max(need, min(_STEP_H_EMU, max_h))
        pt -= 0.5
    return None


@dataclass(frozen=True)
class _Flow:
    """Геометрия потока.

    * ``row`` — строка слева направо;
    * ``snake`` — две строки «змейкой»: первая слева направо, стрелка вниз
      под последним шагом, вторая справа налево (порядок читается без
      нумерации, как SmartArt «процесс с изгибом»);
    * ``column`` — столбец сверху вниз."""

    mode: str
    arrow: int  # зазор под стрелку между соседними шагами
    box_w: int
    box_h: int
    size_pt: float
    per_row: int = 0


def _row(n: int, steps: list[str], cx: int, cy: int) -> _Flow | None:
    arrow = min(_ARROW_W_EMU, max(1, int(cx * 0.05)))
    box_w = max(1, (cx - (n - 1) * arrow) // n)
    font = step_font(steps, box_w, min(cy, _MAX_STEP_H_EMU))
    if font is None:
        return None
    return _Flow("row", arrow, box_w, font[1], font[0], per_row=n)


def _snake(n: int, steps: list[str], cx: int, cy: int) -> _Flow | None:
    if n < 4:
        return None
    per_row = -(-n // 2)
    arrow = min(_ARROW_W_EMU, max(1, int(cx * 0.05)))
    box_w = max(1, (cx - (per_row - 1) * arrow) // per_row)
    row_h = (cy - arrow) // 2
    if row_h < _MIN_COLUMN_STEP_H_EMU:
        return None
    font = step_font(steps, box_w, min(row_h, _MAX_STEP_H_EMU))
    if font is None:
        return None
    return _Flow("snake", arrow, box_w, font[1], font[0], per_row=per_row)


def _column(n: int, steps: list[str], cx: int, cy: int) -> _Flow | None:
    arrow = min(_ARROW_W_EMU, max(1, int(cy * 0.08)))
    slot_h = (cy - (n - 1) * arrow) // n
    if slot_h < _MIN_COLUMN_STEP_H_EMU:
        return None
    box_w = min(cx, _MAX_COLUMN_W_EMU)
    font = step_font(steps, box_w, min(slot_h, _MAX_STEP_H_EMU))
    if font is None:
        return None
    return _Flow("column", arrow, box_w, font[1], font[0], per_row=1)


def _layout(diagram: Diagram, cx: int, cy: int) -> _Flow | None:
    """Строка, если в ней шаги читаются (кегль ≥ ``_ROW_PREFERRED_PT``);
    иначе — вариант с наибольшим кеглем из строки, «змейки» и столбца
    (при равенстве — в этом порядке). Длинные русские слова в шестишаговом
    процессе не рвутся, а уходят во вторую строку; узкая высокая область
    рядом с оформлением макета получает столбец."""
    steps = list(diagram.steps)
    n = len(steps)
    row = _row(n, steps, cx, cy)
    if row is not None and row.size_pt >= _ROW_PREFERRED_PT:
        return row
    order = {"row": 2, "snake": 1, "column": 0}
    candidates = [
        f for f in (row, _snake(n, steps, cx, cy), _column(n, steps, cx, cy)) if f is not None
    ]
    return max(candidates, key=lambda f: (f.size_pt, order[f.mode]), default=None)


def _positions(flow: _Flow, n: int, x0: int, y0: int) -> list[tuple[int, int]]:
    if flow.mode == "column":
        return [(x0, y0 + i * (flow.box_h + flow.arrow)) for i in range(n)]
    step_x = flow.box_w + flow.arrow
    out = []
    for i in range(n):
        if flow.mode == "snake" and i >= flow.per_row:
            col = flow.per_row - 1 - (i - flow.per_row)
            out.append((x0 + col * step_x, y0 + flow.box_h + flow.arrow))
        else:
            out.append((x0 + i * step_x, y0))
    return out


def _build_diagram_group(
    gid: int, diagram: Diagram, x: int, y: int, cx: int, cy: int, flow: _Flow,
) -> etree._Element:
    """One `p:grpSp`: step boxes with arrows between them, centred in the
    host box (see ``_Flow`` for the three arrangements)."""
    steps = diagram.steps
    n = len(steps)
    if flow.mode == "column":
        gw, gh = flow.box_w, n * flow.box_h + (n - 1) * flow.arrow
    elif flow.mode == "snake":
        gw = flow.per_row * flow.box_w + (flow.per_row - 1) * flow.arrow
        gh = 2 * flow.box_h + flow.arrow
    else:
        gw, gh = n * flow.box_w + (n - 1) * flow.arrow, flow.box_h
    x0 = x + (cx - gw) // 2
    y0 = y + (cy - gh) // 2
    pos = _positions(flow, n, x0, y0)

    children: list[str] = []
    sid = gid + 1
    for i, step in enumerate(steps):
        bx, by = pos[i]
        children.append(
            _step_sp(sid, f"Step {i + 1}", bx, by, flow.box_w, flow.box_h, step, flow.size_pt)
        )
        sid += 1
        if i == n - 1:
            continue
        nx, ny = pos[i + 1]
        if ny > by:  # вниз: столбец или поворот «змейки»
            aw = int(flow.box_w * 0.12)
            children.append(_arrow_sp(
                sid, f"Arrow {i + 1}", bx + (flow.box_w - aw) // 2, by + flow.box_h,
                aw, flow.arrow, prst="downArrow",
            ))
        else:
            ah = int(flow.box_h * 0.35)
            ay = by + (flow.box_h - ah) // 2
            if nx > bx:
                children.append(_arrow_sp(
                    sid, f"Arrow {i + 1}", bx + flow.box_w, ay, flow.arrow, ah,
                ))
            else:
                children.append(_arrow_sp(
                    sid, f"Arrow {i + 1}", nx + flow.box_w, ay, flow.arrow, ah,
                    prst="leftArrow",
                ))
        sid += 1

    xml = (
        f'<p:grpSp xmlns:p="{P}" xmlns:a="{A}">'
        f'<p:nvGrpSpPr><p:cNvPr id="{gid}" name="Diagram {escape(diagram.id)}"/>'
        f"<p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>"
        f'<p:grpSpPr><a:xfrm><a:off x="{x0}" y="{y0}"/>'
        f'<a:ext cx="{gw}" cy="{gh}"/>'
        f'<a:chOff x="{x0}" y="{y0}"/>'
        f'<a:chExt cx="{gw}" cy="{gh}"/></a:xfrm></p:grpSpPr>'
        + "".join(children)
        + "</p:grpSp>"
    )
    return etree.fromstring(xml)


def _has_text(el: etree._Element) -> bool:
    return any((t.text or "").strip() for t in el.iter(f"{{{A}}}t"))


def _title_bottom(tree: etree._Element, slide_h: int | None = None) -> int:
    """Нижняя граница заголовка, который на слайде действительно виден.

    Пустой плейсхолдер заголовка не в счёт: когда заголовок нарисован своей
    плашкой (``add_missing_title``, «DeckDNA heading»), пустой плейсхолдер
    титульного макета внизу слайда иначе «съедал» всю область над собой.
    Без видимого заголовка — граница любых плейсхолдеров заголовка, как
    раньше."""
    bottom = 0
    fallback = 0
    for el in tree:
        box = _el_box(el)
        if box is None:
            continue
        ph = el.find(f"{{{P}}}nvSpPr/{{{P}}}nvPr/{{{P}}}ph")
        is_title = ph is not None and ph.get("type") in _TITLE_TYPES
        if is_title:
            fallback = max(fallback, box[1] + box[3])
        heading = (
            slide_h is not None and not is_title and etree.QName(el).localname == "sp"
            and box[1] < slide_h * _HEADING_ZONE and box[1] + box[3] < slide_h * 0.4
        )
        if (is_title or heading) and _has_text(el):
            bottom = max(bottom, box[1] + box[3])
    return bottom or fallback


def _empty_slots(tree: etree._Element) -> list[tuple[int, int, int, int]]:
    """Рамки текста без текста (очищенные слоты карточек), кроме заголовка."""
    out = []
    for el in tree:
        if etree.QName(el).localname != "sp" or el.find(f"{{{P}}}txBody") is None:
            continue
        ph = el.find(f"{{{P}}}nvSpPr/{{{P}}}nvPr/{{{P}}}ph")
        if ph is not None and ph.get("type") in _TITLE_TYPES:
            continue
        if _has_text(el):
            continue
        box = _el_box(el)
        if box is not None:
            out.append(box)
    return out


def _gap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
    dx = max(0, max(a[0], b[0]) - min(a[0] + a[2], b[0] + b[2]))
    dy = max(0, max(a[1], b[1]) - min(a[1] + a[3], b[1] + b[3]))
    return max(dx, dy)


def _decor(tree: etree._Element, slide_w: int, slide_h: int) -> list[etree._Element]:
    """Фигуры без текста, которые в принципе можно убрать: не плейсхолдер,
    не фоновая подложка (≥60% слайда), не колонтитульная полоса."""
    footer = slide_h * (1 - _FOOTER_BAND)
    out = []
    for el in tree:
        if etree.QName(el).localname not in ("sp", "pic", "grpSp", "cxnSp", "graphicFrame"):
            continue
        if _has_text(el):
            continue
        # плейсхолдеры не удаляются никогда: без них LibreOffice рисует
        # подсказку макета («Проблема», «Описание проблемы» у шаблона LCT,
        # hasCustomPrompt), а пустой плейсхолдер на слайде невидим
        if el.find(f".//{{{P}}}nvPr/{{{P}}}ph") is not None:
            continue
        box = _el_box(el)
        if box is None:
            continue
        if box[2] * box[3] >= 0.6 * slide_w * slide_h or box[1] >= footer:
            continue
        out.append(el)
    return out


def _clearable(tree: etree._Element, slide_w: int, slide_h: int) -> list[etree._Element]:
    """Оформление опустевших карточек: фигуры без текста под заголовком,
    стоящие вплотную к очищенному слоту (маркер, иконка, подложка, сама
    пустая рамка). Декор шаблона вдали от слотов (точки пагинации, узоры)
    сюда не попадает и остаётся занятым местом."""
    top = _title_bottom(tree, slide_h)
    slots = _empty_slots(tree)
    near = int(_NEAR * min(slide_w, slide_h))
    out = []
    for el in _decor(tree, slide_w, slide_h):
        box = _el_box(el)
        # центр под полосой заголовка: фигура может слегка заходить на неё
        if box[1] + box[3] // 2 < top:
            continue
        if any(_gap(box, slot) <= near for slot in slots):
            out.append(el)
    return out


def _siblings(
    tree: etree._Element, doomed: list[etree._Element], slide_w: int, slide_h: int
) -> list[etree._Element]:
    """Элементы той же сетки карточек, что уже уходят под схему: тот же
    размер (±5%) и вплотную к удаляемой фигуре или пустому слоту. Ловит
    маркер первой карточки, который стоит на уровне заголовка."""
    sizes = [(b[2], b[3]) for el in doomed if (b := _el_box(el)) is not None]
    anchors = [b for el in doomed if (b := _el_box(el)) is not None] + _empty_slots(tree)
    near = int(_NEAR * min(slide_w, slide_h))
    out = []
    for el in _decor(tree, slide_w, slide_h):
        if el in doomed:
            continue
        box = _el_box(el)
        same = any(
            abs(box[2] - w) <= 0.05 * w and abs(box[3] - h) <= 0.05 * h for w, h in sizes
        )
        if same and any(_gap(box, a) <= near for a in anchors):
            out.append(el)
    return out


def _overlap_share(
    box: tuple[int, int, int, int], other: tuple[int, int, int, int]
) -> float:
    """Доля площади *box*, накрытая *other*."""
    ix = max(0, min(box[0] + box[2], other[0] + other[2]) - max(box[0], other[0]))
    iy = max(0, min(box[1] + box[3], other[1] + other[3]) - max(box[1], other[1]))
    return ix * iy / max(1, box[2] * box[3])


def _intersects(box: tuple[int, int, int, int], region: tuple[int, int, int, int]) -> bool:
    bx, by, bw, bh = box
    rx, ry, rw, rh = region
    return bx < rx + rw and rx < bx + bw and by < ry + rh and ry < by + bh


def _add_diagram(
    root: etree._Element,
    diagram: Diagram,
    used_hosts: set[tuple[int, int, int, int]],
    slide_size: tuple[int, int] | None = None,
    layout_busy: tuple[tuple[int, int, int, int], ...] = (),
    allow_clear: bool = True,
) -> tuple[bool, int]:
    """Place the flow where it reads best. (placed, decorations removed).

    Candidates: emptied text bodies, the free rectangle under the title and
    the same rectangle with the emptied card grid's decoration cleared. Each
    is scored by its best arrangement (``_layout``): a readable left→right
    row first, then the step font, then width."""
    # A partial flow silently changes the process. Leave the entire unit
    # unplaced when its steps cannot fit this one-row native layout.
    if not 1 <= len(diagram.steps) <= _MAX_STEPS:
        return False, 0
    sp_tree = root.find(f".//{{{P}}}cSld/{{{P}}}spTree")
    if sp_tree is None:
        return False, 0
    # (хост, что убрать ради него)
    candidates: list[tuple[tuple, list[etree._Element]]] = [
        (h, []) for h in _empty_body_hosts(root)
        if h[3] >= _MIN_HOST_W_EMU and h[1:5] not in used_hosts
        # рамка шаблона поверх картинки макета (подпись на титуле) — не место
        # для схемы: шаги легли бы на оформление
        and not any(_overlap_share(h[1:5], b) > 0.2 for b in layout_busy)
    ]
    if slide_size is not None:
        # свободное место как есть — с учётом оформления макета под слайдом
        band = _title_bottom(sp_tree, slide_size[1])
        free = _free_space_host(
            root, *slide_size, extra_busy=layout_busy, title_band=band
        )
        if free is not None and free[1:5] not in used_hosts:
            candidates.append((free, []))
        # область под заголовком без оформления опустевших карточек; когда
        # карточки «запечены» в картинку фона/макета (allow_clear=False),
        # убранная фигура обнажила бы такую же карточку с демо-текстом
        clearable = _clearable(sp_tree, *slide_size) if allow_clear else []
        wide = _free_space_host(
            root, *slide_size, ignore=frozenset(id(el) for el in clearable),
            extra_busy=layout_busy, title_band=band,
        )
        if wide is not None and wide[1:5] not in used_hosts:
            region = wide[1:5]
            doomed = [
                el for el in clearable
                if (box := _el_box(el)) is not None and _intersects(box, region)
            ]
            doomed += _siblings(sp_tree, doomed, *slide_size)
            candidates.append((wide, doomed))
    scored = []
    for host, doomed in candidates:
        if host[3] < _MIN_HOST_W_EMU:
            continue
        flow = _layout(diagram, host[3], host[4])
        if flow is not None:
            # читаемая строка (как у ``_layout``), затем кегль, затем ширина.
            # Убираемое оформление — маркеры и подложки опустевших карточек
            # (``_clearable``): без пунктов они и так «дырявый грид», поэтому
            # область без них не штрафуется
            readable_row = flow.mode == "row" and flow.size_pt >= _ROW_PREFERRED_PT
            scored.append(
                ((readable_row, flow.size_pt, host[3], host[0]), host, doomed, flow)
            )
    if not scored:
        return False, 0
    _, best, doomed, flow = max(scored, key=lambda item: item[0])
    _, x, y, cx, cy, _sp = best
    for el in doomed:
        sp_tree.remove(el)
    used_hosts.add((x, y, cx, cy))
    sp_tree.append(
        _build_diagram_group(_next_shape_id(root), diagram, x, y, cx, cy, flow)
    )
    return True, len(doomed)


def fill_slide_diagrams(
    slide_xml: bytes,
    diagrams: list[Diagram],
    used_hosts: set[tuple[int, int, int, int]] | None = None,
    slide_size: tuple[int, int] | None = None,
    layout_busy: tuple[tuple[int, int, int, int], ...] = (),
    allow_clear: bool = True,
) -> tuple[bytes, DiagramFillReport]:
    """Build one SmartArt-like group per resolved Diagram, in plan
    order. Units beyond available hosts honestly drop.

    *layout_busy* — видимое оформление макета слайда (см.
    ``layout_decor_boxes``): схема не ложится поверх него. *allow_clear* —
    можно ли убирать оформление опустевших карточек (нельзя, когда сетка
    «запечена» в картинку макета)."""
    report = DiagramFillReport()
    if not diagrams:
        return slide_xml, report
    root = etree.fromstring(slide_xml)
    hosts = used_hosts if used_hosts is not None else set()
    for diagram in diagrams:
        placed, removed = _add_diagram(
            root, diagram, hosts, slide_size, layout_busy, allow_clear
        )
        report.decorations_removed += removed
        if placed:
            report.diagrams_created += 1
            report.steps_written += len(diagram.steps)
            report.diagram_ids.append(diagram.id)
        else:
            report.units_dropped += 1
    if not report.diagrams_created:
        return slide_xml, report
    return etree.tostring(root, xml_declaration=True, standalone=True), report


_NON_CONTENT_PH_TYPES = ("title", "ctrTitle", "dt", "ftr", "sldNum", "hdr")


def layout_busy_boxes(pkg, slide_part: str) -> tuple[tuple[int, int, int, int], ...]:
    """Что макет слайда рисует под ним в области контента (EMU-рамки):

    * видимое оформление макета (картинки, фигуры с заливкой) — его нет в
      ``spTree`` слайда;
    * плейсхолдеры макета с собственной подсказкой (``hasCustomPrompt``):
      LibreOffice рисует такую подсказку на слайде, если плейсхолдер слайда
      пуст или отсутствует («Проблема», «Описание проблемы» у шаблона питча
      LCT — их прятали непрозрачные карточки образца, и после их удаления
      подсказки проступили сквозь схему). PowerPoint их не показывает, но
      рендер-арена и PDF собираются через LibreOffice.

    Фоновые подложки (≥60% слайда) не в счёт — на них и так всё стоит."""
    from deckdna.pptx.cloning.exemplar import _presentation_slide_size

    size = _presentation_slide_size(pkg)
    layout = next(
        (t for rel, t in pkg.internal_dependencies(slide_part) if rel.type_name == "slideLayout"),
        None,
    )
    if size is None or layout is None or layout not in pkg.parts:
        return ()
    sw, sh = size
    tree = etree.fromstring(pkg.parts[layout]).find(f"{{{P}}}cSld/{{{P}}}spTree")
    if tree is None:
        return ()
    boxes = []
    for el in tree:
        box = _el_box(el)
        if box is None or box[2] * box[3] == 0 or box[2] * box[3] >= 0.6 * sw * sh:
            continue
        ph = el.find(f".//{{{P}}}nvPr/{{{P}}}ph")
        if ph is not None:
            if (
                ph.get("hasCustomPrompt") == "1"
                and (ph.get("type") or "body") not in _NON_CONTENT_PH_TYPES
                and _has_text(el)
            ):
                boxes.append(box)
            continue
        if _visible(el):
            boxes.append(box)
    return tuple(boxes)
