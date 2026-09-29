"""Repair Apply — применение RepairAction к .pptx (мутация пакета).

Пара к planner.py: plan_repairs() только предлагает действия,
apply_repairs() исполняет их. Реализованы типы, имеющие реальное
покрытие на organizer-фикстурах:

- ``resize_shape`` — записывает ``a:xfrm`` (off/ext) фигур из
  ``target.shape_ids`` по ``params.bbox`` в нормализованных [0,1]
  координатах от размера слайда (та же система, что производит
  ``audit._shape_bbox`` и читает ``planner._overflow_action``).
- ``recrop_image`` — при ``keep_aspect=True`` правит ``a:srcRect``
  так, чтобы отображаемая область источника имела aspect рамки:
  избыточная сторона дожимается симметричным кропом поверх
  существующего. Рамка не двигается — «показать то же, но без
  искажения».
- ``remove_placeholder`` — очистка текстовых runs фигуры-заглушки
  (a:t → ""); геометрия и стиль фрейма остаются.
- ``merge_slide`` — удаление слайда-дубля: запись выкидывается из
  ``sldIdLst`` презентации, relationship на slide-парт снимается
  (``drop_rel``); сиротский парт и уникальные для него медиа не
  попадают в сохраняемый zip. Для почти-идентичного дубля
  (jaccard ≥0.9) удаление ≡ merge в оригинал; ``params.new_index``
  (индекс оригинала) сохранён planner'ом только для отчётности.
- ``shorten_text`` — цепочечный fallback за resize_shape для
  text.overflow: исполнитель перемеряет overflow теми же оценками, что
  и ``audit.check_text_overflow``, и пропускает фигуру, если текст уже
  помещается (resize справился) — контент не режется зря. Иначе
  урезает: неразбиваемые слова шире usable width обрезаются с '…',
  хвост текста снимается словами до вписывания по высоте.

Остальные action_type (native_rebuild, split_slide...)
честно репортятся как ``not_implemented`` — планировщик их может
выдать, но исполнитель фейк-применений не делает.

Per-action статусы: applied / skipped (нет цели/параметров) /
failed (исключение или shape не найден) / not_implemented.
Пакет сохраняется в ``out_path`` всегда — даже при 0 applied —
чтобы пайплайн мог продолжать работать с файлом.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lxml import etree
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE_TYPE

from deckdna.audit.basic import (  # noqa: SLF001 — общий расчёт
    _UNRESOLVED,
    _contrast_ratio,
    _Ctx,
    _delta_e,
    _effective_font_size_pt,
    _estimate_text_height_emu,
    _group_map,
    _is_decorative_audit_body,
    _is_footerish_placeholder,
    _is_neutral_rgb,
    _max_line_width_emu,
    _resolve_color_element,
    _run_text_rgb,
    _text_width_emu,
    _theme_palette,
    effective_fill_rgb,
)
from deckdna.audit.config import default_audit_config
from deckdna.contracts.repair_action import RepairAction
from deckdna.errors import DeckDNAError
from deckdna.pptx.composing.protection import ProtectedSlides

A = "http://schemas.openxmlformats.org/drawingml/2006/main"

logger = logging.getLogger(__name__)

STAGE = "repair.apply"

_STATUS_APPLIED = "applied"
_STATUS_SKIPPED = "skipped"
_STATUS_FAILED = "failed"
_STATUS_NOT_IMPLEMENTED = "not_implemented"

# Типы, которые исполнитель реально умеет применять.
_IMPLEMENTED = {
    "resize_shape",
    "recrop_image",
    "remove_placeholder",
    "merge_slide",
    "move_shape",
    "map_font",
    "map_color",
    "shorten_text",
}


@dataclass
class ActionResult:
    """Исход одного RepairAction."""

    index: int
    action_type: str
    status: str
    detail: str = ""
    shapes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "action_type": self.action_type,
            "status": self.status,
            "detail": self.detail,
            "shapes": self.shapes,
        }


@dataclass
class RepairApplyReport:
    """Сводный отчёт применения: счётчики + per-action результаты."""

    out_path: str
    results: list[ActionResult] = field(default_factory=list)

    @property
    def applied(self) -> int:
        return sum(1 for r in self.results if r.status == _STATUS_APPLIED)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status == _STATUS_SKIPPED)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == _STATUS_FAILED)

    @property
    def not_implemented(self) -> int:
        return sum(1 for r in self.results if r.status == _STATUS_NOT_IMPLEMENTED)

    @property
    def by_type(self) -> dict[str, dict[str, int]]:
        stats: dict[str, dict[str, int]] = {}
        for r in self.results:
            stats.setdefault(r.action_type, {}).setdefault(r.status, 0)
            stats[r.action_type][r.status] += 1
        return stats

    def to_dict(self) -> dict[str, Any]:
        return {
            "out_path": self.out_path,
            "applied": self.applied,
            "skipped": self.skipped,
            "failed": self.failed,
            "not_implemented": self.not_implemented,
            "by_type": self.by_type,
            "results": [r.to_dict() for r in self.results],
        }


def _iter_shapes(shapes) -> Iterable:
    """Рекурсивный обход — шейпы внутри групп тоже адресуемы."""
    for shape in shapes:
        yield shape
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _iter_shapes(shape.shapes)


def _find_slide(prs, slide_id: str):
    for slide in prs.slides:
        if str(slide.slide_id) == str(slide_id):
            return slide
    return None


def _find_shape(slide, shape_id: str):
    for shape in _iter_shapes(slide.shapes):
        if str(shape.shape_id) == str(shape_id):
            return shape
    return None


# Аффинная карта local→slide EMU по осям: slide = t + s·local. У top-level
# фигур это (0, 1); у детей масштабированной группы (chOff/chExt ≠ off/ext)
# — нет. Аудит меряет bbox и вместимость в slide-space, поэтому исполнитель
# обязан писать геометрию через ту же карту, иначе действие «применено», а
# проблема остаётся (contract B6: фигуры внутри группы масштаба ≈0.38).
_AxisMap = tuple[float, float]
_IDENTITY: tuple[_AxisMap, _AxisMap] = ((0.0, 1.0), (0.0, 1.0))


def _slide_maps(slide) -> dict[str, tuple[_AxisMap, _AxisMap]]:
    """shape_id → (map_x, map_y) для каждой фигуры слайда, включая вложенные."""
    maps: dict[str, tuple[_AxisMap, _AxisMap]] = {}

    def walk(shapes, map_x: _AxisMap, map_y: _AxisMap) -> None:
        for shape in shapes:
            maps[str(shape.shape_id)] = (map_x, map_y)
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                walk(shape.shapes, *_group_map(shape, map_x, map_y))

    walk(slide.shapes, *_IDENTITY)
    return maps


def _slide_box(shape, maps) -> tuple[int, int, int, int]:
    """Текущая геометрия фигуры в slide-space EMU (left, top, w, h)."""
    (tx, sx), (ty, sy) = maps.get(str(shape.shape_id), _IDENTITY)
    return (
        int(round(tx + sx * (shape.left or 0))),
        int(round(ty + sy * (shape.top or 0))),
        int(round(sx * (shape.width or 0))),
        int(round(sy * (shape.height or 0))),
    )


def _set_slide_box(shape, maps, box: tuple[int, int, int, int]) -> None:
    """Записать slide-space бокс в xfrm фигуры (в её собственных координатах)."""
    (tx, sx), (ty, sy) = maps.get(str(shape.shape_id), _IDENTITY)
    left, top, width, height = box
    if sx == 0 or sy == 0:
        raise ValueError("вырожденная карта группы: масштаб 0")
    shape.left = int(round((left - tx) / sx))
    shape.top = int(round((top - ty) / sy))
    shape.width = int(round(width / sx))
    shape.height = int(round(height / sy))


def _shape_eligible_for_overlap(shape, box: tuple[int, int, int, int], slide_area: float) -> bool:
    """Гейты check_unintended_overlap для ОДНОЙ стороны пары:

    текст есть, площадь bbox в [1%, 80%) слайда. Аудит флагает только
    пары, где ОБЕ фигуры проходят гейты — поэтому apply избегает
    пересечений только с такими соседями (и только если целевая фигура
    сама проходит): любое прочее пересечение правило всё равно не флагает.
    """
    w, h = box[2], box[3]
    if w <= 0 or h <= 0:
        return False
    area = w * h
    cfg = default_audit_config()
    if (
        area < slide_area * cfg.overlap_min_shape_area
        or area >= slide_area * cfg.overlap_bg_area
    ):
        return False
    return bool(shape.has_text_frame and shape.text_frame.text.strip())


def _neighbor_boxes(slide, exclude_ids: set[str], sw: float, sh: float, maps=None):
    """Соседи-препятствия на слайде в slide-space (включая детей групп —
    аудит проверяет пересечения по всем вложенным фигурам)."""
    slide_area = sw * sh
    maps = maps if maps is not None else _slide_maps(slide)
    boxes: list[tuple[int, int, int, int]] = []
    for other in _iter_shapes(slide.shapes):
        if str(other.shape_id) in exclude_ids:
            continue
        if other.left is None or other.top is None:
            continue
        box = _slide_box(other, maps)
        if _shape_eligible_for_overlap(other, box, slide_area):
            boxes.append(box)
    return boxes


def _cap_at_neighbors(
    cur: tuple[int, int, int, int],
    cand: tuple[int, int, int, int],
    obstacles: list[tuple[int, int, int, int]],
) -> tuple[int, int, int, int]:
    """Зажать расширение по ближайшему краю текстового соседа.

    Каждая сторона, которая РАСТЁТ относительно текущей геометрии,
    останавливается у края ближайшего препятствия в этом направлении.
    Уже существующее пересечение не уменьшаем (сторона не двигается
    дальше текущей), не создаём и новое. Лево/верх учтены для
    adversarial-bbox со сдвигом — planner двигает только w/h.
    """
    cl, ct, cw, ch = cur
    nl, nt, nw, nh = cand
    cr, cb = cl + cw, ct + ch
    nr, nb = nl + nw, nt + nh
    for ol, ot, ow, oh in obstacles:
        obr, obb = ol + ow, ot + oh
        v_overlap = nt < obb and nb > ot
        h_overlap = nl < obr and nr > ol
        if v_overlap and obr > cr and ol < nr:
            nr = min(nr, max(cr, ol))
        if v_overlap and ol < cl and obr > nl:
            nl = max(nl, min(cl, obr))
        if h_overlap and obb > cb and ot < nb:
            nb = min(nb, max(cb, ot))
        if h_overlap and ot < ct and obb > nt:
            nt = max(nt, min(ct, obb))
    return nl, nt, nr - nl, nb - nt


def _flaggable_overlap_count(
    cand: tuple[int, int, int, int],
    obstacles: list[tuple[int, int, int, int]],
) -> int:
    """Сколько соседей кандидат пересекает так, как флагает аудит
    (пересечение ∈ [overlap_min_cover, overlap_containment) меньшей —
    пороги из configs/audit.default.yaml, как у детектора)."""
    nl, nt, nw, nh = cand
    n_area = nw * nh
    count = 0
    for ol, ot, ow, oh in obstacles:
        iw = min(nl + nw, ol + ow) - max(nl, ol)
        ih = min(nt + nh, ot + oh) - max(nt, ot)
        inter = max(0, iw) * max(0, ih)
        if inter <= 0:
            continue
        cover = inter / min(n_area, ow * oh)
        cfg = default_audit_config()
        if cfg.overlap_min_cover <= cover < cfg.overlap_containment:
            count += 1
    return count


def _apply_resize_shape(prs, action: RepairAction, slide) -> ActionResult:
    """resize_shape: params.bbox [0,1] → off/ext фигуры в EMU."""
    res = ActionResult(index=-1, action_type="resize_shape", status=_STATUS_APPLIED)
    bbox = action.params.bbox if action.params else None
    if bbox is None or all(
        getattr(bbox, f) is None for f in ("x", "y", "w", "h")
    ):
        res.status = _STATUS_SKIPPED
        res.detail = "params.bbox отсутствует — нечего записать в xfrm"
        return res

    sw, sh = float(prs.slide_width), float(prs.slide_height)
    if sw <= 0 or sh <= 0:
        res.status = _STATUS_FAILED
        res.detail = "нулевая геометрия слайда — нормализация невозможна"
        return res

    ids = action.target.shape_ids or []
    if not ids:
        res.status = _STATUS_SKIPPED
        res.detail = "target.shape_ids пуст — нет адресата"
        return res

    errors: list[str] = []
    degenerate: list[str] = []
    clamped: list[str] = []
    neighbor_capped: list[str] = []
    colliding: list[tuple[str, int]] = []
    maps = _slide_maps(slide)
    neighbors = _neighbor_boxes(slide, set(ids), sw, sh, maps)
    for sid in ids:
        shape = _find_shape(slide, sid)
        if shape is None:
            errors.append(f"shape {sid} не найден")
            continue
        try:
            # Вся арифметика — в slide-space (как bbox аудита); для детей
            # масштабированной группы запись в xfrm идёт через обратную
            # карту группы (_set_slide_box).
            cur_l, cur_t, cur_w, cur_h = _slide_box(shape, maps)
            # None-поля оставляют текущее значение; итог зажимается в
            # границы слайда: apply не доверяет bbox слепо — расширенная
            # рамка не должна создавать layout.out_of_bounds взамен
            # закрываемого text.overflow. Остаточный overflow при этом
            # законно остаётся issue (planner помечает это precondition'ом).
            new_left = int(round(bbox.x * sw)) if bbox.x is not None else cur_l
            new_top = int(round(bbox.y * sh)) if bbox.y is not None else cur_t
            new_w = int(round(bbox.w * sw)) if bbox.w is not None else cur_w
            new_h = int(round(bbox.h * sh)) if bbox.h is not None else cur_h
            left = min(max(new_left or 0, 0), int(sw))
            top = min(max(new_top or 0, 0), int(sh))
            width = min(max(new_w or 0, 0), int(sw) - left)
            height = min(max(new_h or 0, 0), int(sh) - top)
            if (left, top, width, height) != (new_left, new_top, new_w, new_h):
                clamped.append(str(sid))
            # Второй зажим — по текстовым соседям на тех же гейтах, что у
            # check_unintended_overlap: расширение не должно закрывать
            # text.overflow ценой новой коллизии. Остаточный overflow при
            # этом законно остаётся issue.
            if _shape_eligible_for_overlap(
                shape, (left, top, width, height), sw * sh
            ):
                capped = _cap_at_neighbors(
                    (cur_l, cur_t, cur_w, cur_h),
                    (left, top, width, height),
                    neighbors,
                )
                if capped != (left, top, width, height):
                    left, top, width, height = capped
                    neighbor_capped.append(str(sid))
                residual = _flaggable_overlap_count(
                    (left, top, width, height), neighbors
                )
                if residual:
                    colliding.append((str(sid), residual))
            if width <= 0 or height <= 0:
                # Слайд-границы зажали top/left настолько, что для width/
                # height просто не осталось места (типично: фигура уже
                # стояла частично/полностью за пределами слайда — то же
                # clamp-в-[0,sh], что защищает от out_of_bounds, здесь
                # схлопывает высоту/ширину до 0). Мутировать shape в
                # нулевой размер хуже, чем оставить как есть: цепочечный
                # shorten_text больше не сможет измерить вместимость
                # (найдено live-репро — сгенерированная колода, shape с
                # исходным bbox y≈1.087, за пределами слайда: clamp дал
                # height=0, дальше shorten_text падал с «нет геометрии»,
                # хотя ДО этого шага у shape была настоящая, измеримая
                # geometry). Оставляем геометрию нетронутой — честный
                # skip, а не молчаливая порча под видом "applied".
                degenerate.append(
                    f"shape {sid}: расширение в границах слайда даёт "
                    f"нулевые width/height ({width}x{height}) — geometry не изменена"
                )
                continue
            _set_slide_box(shape, maps, (left, top, width, height))
            res.shapes.append(str(sid))
        except Exception as exc:  # noqa: BLE001 — per-shape ошибка не роняет action
            errors.append(f"shape {sid}: {exc}")

    if not res.shapes:
        if errors:
            res.status = _STATUS_FAILED
            res.detail = "; ".join(errors)
        else:
            # Every targeted shape hit the degenerate-clamp guard, not a
            # real error (not-found/exception) -- an honest no-op, same
            # as the "nothing to write" skip above, not a failure.
            res.status = _STATUS_SKIPPED
            res.detail = "; ".join(degenerate) or "ни один shape не изменён"
    else:
        notes: list[str] = []
        if clamped:
            notes.append(f"bbox зажат в границы у {len(clamped)} фигур")
        if neighbor_capped:
            notes.append(
                f"расширение остановлено у соседей у {len(neighbor_capped)} фигур"
            )
        if colliding:
            notes.append(
                "остались пересечения с соседями у "
                + ", ".join(f"{sid} ({n})" for sid, n in colliding)
            )
        if errors:
            notes.append("частично применено: " + "; ".join(errors))
        if degenerate:
            notes.append("нулевая geometry после зажима: " + "; ".join(degenerate))
        if not notes:
            notes.append(f"xfrm обновлён у {len(res.shapes)} фигур")
        res.detail = "; ".join(notes)
    return res


def _apply_recrop_image(prs, action: RepairAction, slide) -> ActionResult:
    """recrop_image(keep_aspect=True): симметричный a:srcRect так, чтобы
    отображаемая область источника имела aspect рамки (без изменения ext).

    Считает относительно УЖЕ наложенного кропа — выбранная автором
    область сохраняется по центру, дожимается только избыточная ось.
    """
    res = ActionResult(index=-1, action_type="recrop_image", status=_STATUS_APPLIED)
    if action.params and action.params.keep_aspect is False:
        res.status = _STATUS_SKIPPED
        res.detail = "keep_aspect=False — растяжение вместо кропа не поддерживается"
        return res

    ids = action.target.shape_ids or []
    if not ids:
        res.status = _STATUS_SKIPPED
        res.detail = "target.shape_ids пуст — нет адресата"
        return res

    errors: list[str] = []
    for sid in ids:
        shape = _find_shape(slide, sid)
        if shape is None:
            errors.append(f"shape {sid} не найден")
            continue
        try:
            px_w, px_h = shape.image.size
            if not shape.width or not shape.height or px_w <= 0 or px_h <= 0:
                errors.append(f"shape {sid}: нет размеров кадра или источника")
                continue
            src_w = px_w * (1.0 - shape.crop_left - shape.crop_right)
            src_h = px_h * (1.0 - shape.crop_top - shape.crop_bottom)
            if src_w <= 0 or src_h <= 0:
                errors.append(f"shape {sid}: существующий кроп занулил источник")
                continue
            target = float(shape.width) / float(shape.height)
            src_ratio = src_w / src_h
            if abs(src_ratio / target - 1.0) <= 0.005:
                res.shapes.append(str(sid))  # уже в допуске — идемпотентно applied
                continue
            if src_ratio > target:
                # источник шире кадра — дожимаем по бокам
                new_src_w = src_h * target
                extra = (src_w - new_src_w) / 2.0 / px_w
                shape.crop_left = shape.crop_left + extra
                shape.crop_right = shape.crop_right + extra
            else:
                new_src_h = src_w / target
                extra = (src_h - new_src_h) / 2.0 / px_h
                shape.crop_top = shape.crop_top + extra
                shape.crop_bottom = shape.crop_bottom + extra
            res.shapes.append(str(sid))
        except (KeyError, OSError, AttributeError) as exc:
            errors.append(f"shape {sid}: {exc}")

    if not res.shapes:
        res.status = _STATUS_FAILED
        res.detail = "; ".join(errors) or "ни одна картинка не обработана"
    elif errors:
        res.detail = "частично применено: " + "; ".join(errors)
    else:
        res.detail = f"srcRect пересчитан у {len(res.shapes)} картинок"
    return res


def _apply_remove_placeholder(prs, action: RepairAction, slide) -> ActionResult:
    """remove_placeholder: очистить текстовые runs фигуры (a:t → "").

    Shape НЕ удаляется из XML-дерева — та же политика, что у очистки
    card-слотов в composing: заглушка уходит, геометрия/стиль остаются.
    STRONG-маркер внутри длинного текста planner помечает риском в
    preconditions — исполнитель действует по решению пользователя.
    """
    res = ActionResult(
        index=-1, action_type="remove_placeholder", status=_STATUS_APPLIED
    )
    ids = action.target.shape_ids or []
    if not ids:
        res.status = _STATUS_SKIPPED
        res.detail = "target.shape_ids пуст — нет адресата"
        return res

    errors: list[str] = []
    for sid in ids:
        shape = _find_shape(slide, sid)
        if shape is None:
            errors.append(f"shape {sid} не найден")
            continue
        runs = shape.element.findall(f".//{{{A}}}t")
        nonempty = [t for t in runs if (t.text or "").strip()]
        if not nonempty:
            errors.append(f"shape {sid}: нет текстовых runs")
            continue
        for t in nonempty:
            t.text = ""
        res.shapes.append(str(sid))

    if not res.shapes:
        res.status = _STATUS_FAILED
        res.detail = "; ".join(errors) or "ни одна фигура не обработана"
    elif errors:
        res.detail = "частично применено: " + "; ".join(errors)
    else:
        res.detail = f"очищены runs у {len(res.shapes)} фигур"
    return res


def _apply_move_shape(prs, action: RepairAction, slide) -> ActionResult:
    """move_shape: params.bbox x/y [0,1] → off фигуры; ext не меняется.

    Только позиция — отличие от resize_shape, который пишет и размер.
    Новая позиция зажимается так, чтобы фигура осталась в границах
    слайда: move не должен породить layout.out_of_bounds взамен
    закрываемого edge_margin.
    """
    res = ActionResult(index=-1, action_type="move_shape", status=_STATUS_APPLIED)
    bbox = action.params.bbox if action.params else None
    if bbox is None or (bbox.x is None and bbox.y is None):
        res.status = _STATUS_SKIPPED
        res.detail = "params.bbox x/y отсутствует — нечего записать в off"
        return res

    sw, sh = float(prs.slide_width), float(prs.slide_height)
    if sw <= 0 or sh <= 0:
        res.status = _STATUS_FAILED
        res.detail = "нулевая геометрия слайда — нормализация невозможна"
        return res

    ids = action.target.shape_ids or []
    if not ids:
        res.status = _STATUS_SKIPPED
        res.detail = "target.shape_ids пуст — нет адресата"
        return res

    errors: list[str] = []
    clamped: list[str] = []
    maps = _slide_maps(slide)
    for sid in ids:
        shape = _find_shape(slide, sid)
        if shape is None:
            errors.append(f"shape {sid} не найден")
            continue
        try:
            # slide-space, как у аудита; запись — через карту группы
            cur_l, cur_t, cur_w, cur_h = _slide_box(shape, maps)
            new_left = int(round(bbox.x * sw)) if bbox.x is not None else cur_l
            new_top = int(round(bbox.y * sh)) if bbox.y is not None else cur_t
            # зажим в [0, extent−size]: сдвинутая фигура не вылетает за край
            left = min(max(new_left, 0), max(int(sw) - cur_w, 0))
            top = min(max(new_top, 0), max(int(sh) - cur_h, 0))
            if (left, top) != (new_left, new_top):
                clamped.append(str(sid))
            _set_slide_box(shape, maps, (left, top, cur_w, cur_h))
            res.shapes.append(str(sid))
        except Exception as exc:  # noqa: BLE001 — per-shape ошибка не роняет action
            errors.append(f"shape {sid}: {exc}")

    if not res.shapes:
        res.status = _STATUS_FAILED
        res.detail = "; ".join(errors) or "ни один shape не сдвинут"
    else:
        notes: list[str] = []
        if clamped:
            notes.append(f"позиция зажата в границы у {len(clamped)} фигур")
        if errors:
            notes.append("частично применено: " + "; ".join(errors))
        if not notes:
            notes.append(f"off обновлён у {len(res.shapes)} фигур")
        res.detail = "; ".join(notes)
    return res


_SHORTEN_MAX_STEPS = 500


@dataclass(frozen=True)
class _MeasureCtx:
    """Минимальный ctx для аудит-оценок текстовой высоты/ширины.

    _Ctx из audit.basic нельзя: его __init__ гоняет
    _find_slide_duplicates по ВСЕМ слайдам, а _slide_text_tokens
    обращается к shape.text_frame — python-pptx при этом создаёт
    txBody даже на шейпах без текста (side effect), что ломает
    XML-идентичность protected-слайдов без единого applied-action.
    Оценочные хелперы читают только default_font_pt (+ cfg у нас)."""

    default_font_pt: float
    cfg: Any


def _fitted_prefix(text: str, size_pt: float, limit_emu: float) -> str:
    """Максимальный префикс, котор��й вместе с '…' влезает в limit_emu."""
    ell = _text_width_emu("…", size_pt)
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _text_width_emu(text[:mid], size_pt) + ell <= limit_emu:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo]


def _replace_token(para, needle: str, replacement: str) -> None:
    """Заменить первое вхождение needle в тексте абзаца, даже если оно
    разорвано по нескольким a:t: замена пишется в a:t, где needle
    начинается, остальной охват стирается."""
    start = para.text.find(needle)
    if start < 0:
        return
    end = start + len(needle)
    pos = 0
    for t in para._p.findall(f".//{{{A}}}t"):  # noqa: SLF001
        txt = t.text or ""
        seg_end = pos + len(txt)
        if seg_end <= start or pos >= end:
            pos = seg_end
            continue
        lo = max(start - pos, 0)
        hi = min(end - pos, len(txt))
        head = replacement if pos <= start < seg_end else ""
        t.text = txt[:lo] + head + txt[hi:]
        pos = seg_end


def _truncate_wide_words(shape, tf, slide, ctx, usable_w: int) -> bool:
    """Урезать слова, которые сами шире usable width (аудит считает их
    неразбиваемыми — перенос по словам их не спасает)."""
    changed = False
    limit = usable_w * (1 + ctx.cfg.overflow_tolerance)
    for para in tf.paragraphs:
        size_pt = _effective_font_size_pt(shape, para, slide, ctx)
        for word in set(para.text.split()):
            if _text_width_emu(word, size_pt) > limit:
                _replace_token(para, word, _fitted_prefix(word, size_pt, usable_w) + "…")
                changed = True
    return changed


def _drop_last_text_unit(tf) -> bool:
    """Срезать одну единицу с хвоста текста.

    Последнее слово последнего непустого a:t; если a:t держал ровно
    одно слово — он очищается; хвостовой абзац без текста удаляется из
    txBody (пустой a:p в оценке аудита стоит целую строку высоты).
    Последний a:p в txBody не удаляется — пустой абзац обязателен.
    """
    tx = tf._txBody  # noqa: SLF001 — публичного API для a:p-уровня нет
    paras = tx.findall(f"{{{A}}}p")
    for p in reversed(paras):
        nonempty = [t for t in p.findall(f".//{{{A}}}t") if (t.text or "").strip()]
        if not nonempty:
            if len(paras) > 1:
                tx.remove(p)
                return True
            continue
        t = nonempty[-1]
        words = (t.text or "").split()
        if len(words) > 1:
            t.text = (t.text or "").rsplit(words[-1], 1)[0].rstrip()
        else:
            t.text = ""
        return True
    return False


def _append_ellipsis(tf) -> bool:
    """'…' в конец последнего непустого a:t — маркер усечения.

    False, если текст уже заканчивается на '…' (свой маркер от
    _fitted_prefix/_truncate_wide_words) — второй многоточий ломал бы
    ширину последнего слова и сносил всю строку.
    """
    for para in reversed(list(tf.paragraphs)):
        nonempty = [t for t in para._p.findall(f".//{{{A}}}t") if (t.text or "").strip()]  # noqa: SLF001
        if nonempty:
            t = nonempty[-1]
            text = (t.text or "").rstrip()
            if text.endswith("…"):
                return False
            t.text = text + "…"
            return True
    return False


def _shorten_wrapped(shape, tf, slide, ctx, usable_w: int, usable_h: int) -> str:
    """Урезать wrap-фрейм до вместимости: ширина — через неразбиваемые
    слова, высота — срезкой хвоста словами. '…' ставится после срезки;
    если он сам разошёл границу — цикл режет ещё одно слово."""
    tol_w = usable_w * (1 + ctx.cfg.overflow_tolerance)
    tol_h = usable_h * (1 + ctx.cfg.overflow_tolerance)

    def _fits() -> bool:
        required, widest = _estimate_text_height_emu(shape, tf, slide, ctx, usable_w)
        return required <= tol_h and widest <= tol_w

    if _fits():
        return "fits"
    changed = _truncate_wide_words(shape, tf, slide, ctx, usable_w)
    removed = False
    ellipsed = False
    for _ in range(_SHORTEN_MAX_STEPS):
        if not _fits():
            if not _drop_last_text_unit(tf):
                break
            removed = changed = True
            continue
        if removed and not ellipsed:
            # '…' может сам разойти границу — тогда цикл снимет ещё слово
            ellipsed = True
            if _append_ellipsis(tf):
                continue
            break
        break
    if not changed:
        return "unchanged"
    return "shortened" if _fits() else "partial"


def _shorten_nowrap(shape, tf, slide, ctx, usable_w: int) -> str:
    """wrap=none: overflow горизонтальный — урезаем слишком широкие
    строки (жёсткие переносы \v/\n могут лежать внутри одного a:t)."""
    tol = usable_w * (1 + ctx.cfg.overflow_tolerance)
    changed = False
    for para in tf.paragraphs:
        size_pt = _effective_font_size_pt(shape, para, slide, ctx)
        for t in para._p.findall(f".//{{{A}}}t"):  # noqa: SLF001
            parts = (t.text or "").split("\v")
            t.text = "\v".join(
                _fitted_prefix(seg, size_pt, usable_w) + "…"
                if _text_width_emu(seg, size_pt) > tol
                else seg
                for seg in parts
            )
            if t.text != "\v".join(parts):
                changed = True
    return "shortened" if changed else "unchanged"


def _shorten_one_shape(prs, slide, shape, ctx, maps=None) -> str | None:
    """Один шейп → 'fits' / 'shortened' / 'partial' / 'unchanged' /
    None (нет текста или геометрии для оценки).

    Вместимость — в slide-space, как у аудита: рамка и её поля лежат в
    координатах группы и масштабируются её (chExt/ext) картой; кегль —
    нет (глиф не масштабируется группой). Без этого фигура внутри
    группы ×0.38 «помещалась» по child-координатам, а аудит видел
    переполнение (contract B6)."""
    tf = shape.text_frame
    if not shape.width or not shape.height:
        return None
    (_, sx), (_, sy) = (maps or {}).get(str(shape.shape_id), _IDENTITY)
    usable_w = int(sx * (int(shape.width) - int(tf.margin_left) - int(tf.margin_right)))
    usable_h = int(sy * (int(shape.height) - int(tf.margin_top) - int(tf.margin_bottom)))
    if usable_w <= 0 or usable_h <= 0:
        return None
    if tf.word_wrap is False:
        widest, _ = _max_line_width_emu(shape, tf, slide, ctx)
        if widest <= usable_w * (1 + ctx.cfg.overflow_tolerance):
            return "fits"
        return _shorten_nowrap(shape, tf, slide, ctx, usable_w)
    return _shorten_wrapped(shape, tf, slide, ctx, usable_w, usable_h)


def _apply_shorten_text(prs, action: RepairAction, slide) -> ActionResult:
    """shorten_text: сократить текст до фактической вместимости рамки.

    Цепочечный fallback за resize_shape: сам перемеряет overflow теми
    же оценками, что и аудит — если рамка после resize уже вмещает
    текст, фигура идёт в skipped и контент не теряется. Мутации только
    на уровне a:t — форматирование уцелевшего текста сохраняется.
    """
    res = ActionResult(index=-1, action_type="shorten_text", status=_STATUS_APPLIED)
    ids = action.target.shape_ids or []
    if not ids:
        res.status = _STATUS_SKIPPED
        res.detail = "target.shape_ids пуст — нет адресата"
        return res

    ctx = _MeasureCtx(
        default_font_pt=_Ctx._default_font_pt(prs),  # noqa: SLF001
        cfg=default_audit_config(),
    )
    errors: list[str] = []
    notes: list[str] = []
    maps = _slide_maps(slide)
    for sid in ids:
        shape = _find_shape(slide, sid)
        if shape is None:
            errors.append(f"shape {sid} не найден")
            continue
        if not shape.has_text_frame or not shape.text_frame.text.strip():
            notes.append(f"shape {sid}: пустой текстовый фрейм")
            continue
        try:
            out = _shorten_one_shape(prs, slide, shape, ctx, maps)
        except Exception as exc:  # noqa: BLE001 — per-shape ошибка не роняет action
            errors.append(f"shape {sid}: {exc}")
            continue
        if out is None:
            errors.append(f"shape {sid}: нет геометрии для оценки вместимости")
        elif out == "fits":
            notes.append(f"shape {sid}: текст уже помещается")
        elif out == "unchanged":
            errors.append(f"shape {sid}: overflow есть, но сократить нечего")
        elif out == "partial":
            res.shapes.append(str(sid))
            notes.append(f"shape {sid}: сокращён до предела — остаточный overflow")
        else:
            res.shapes.append(str(sid))
            notes.append(f"shape {sid}: текст усечён до вместимости рамки")

    if not res.shapes:
        if errors:
            res.status = _STATUS_FAILED
            res.detail = "; ".join(errors)
        else:
            res.status = _STATUS_SKIPPED
            res.detail = "; ".join(notes) or "текст уже помещается"
    else:
        detail = "; ".join(notes)
        if errors:
            detail += "; частично: " + "; ".join(errors)
        res.detail = detail
    return res


def _action_shapes(action: RepairAction, slide) -> list:
    """Адресаты действия: shape_ids target'а или все фигуры слайда."""
    ids = action.target.shape_ids
    if ids:
        return [
            s
            for s in (_find_shape(slide, sid) for sid in ids)
            if s is not None
        ]
    return list(_iter_shapes(slide.shapes))


def _apply_map_font(prs, action: RepairAction, slide) -> ActionResult:
    """map_font: свести семейства шрифтов на слайде к лимиту аудита.

    Заменяет ``a:latin/@typeface`` лишних семейств (все сверх
    ``max_font_families`` самых частотных) на ``params.font_family``
    или на самое частотное семейство адресатов. Точно то же множество,
    что считает check_font_family: только ``a:latin`` в ``a:rPr``
    непустых ``a:r`` — повторный аудит видит результат сразу.
    """
    res = ActionResult(index=-1, action_type="map_font", status=_STATUS_APPLIED)
    keep = default_audit_config().max_font_families
    shapes = _action_shapes(action, slide)
    if not shapes:
        res.status = _STATUS_SKIPPED
        res.detail = "target.shape_ids пуст и на слайде нет адресатов"
        return res

    latin = f"{{{A}}}latin"
    counts: dict[str, int] = {}
    lats: list = []
    for shape in shapes:
        if not shape.has_text_frame:
            continue
        for run in shape.text_frame._txBody.findall(f".//{{{A}}}r"):
            text_el = run.find(f"{{{A}}}t")
            if text_el is None or not (text_el.text or "").strip():
                continue
            rpr = run.find(f"{{{A}}}rPr")
            if rpr is None:
                continue
            lat = rpr.find(latin)
            if lat is not None and lat.get("typeface"):
                counts[lat.get("typeface")] = counts.get(lat.get("typeface"), 0) + 1
                lats.append(lat)
    if len(counts) <= keep:
        res.status = _STATUS_SKIPPED
        res.detail = (
            f"семейств {len(counts)} ≤ {keep} — переназначение не нужно"
        )
        return res

    ordered = sorted(counts, key=lambda f: (-counts[f], f))
    keepers = set(ordered[:keep])
    replacement = (
        action.params.font_family
        if action.params and action.params.font_family
        else ordered[0]
    )
    changed = 0
    for lat in lats:
        if lat.get("typeface") not in keepers:
            lat.set("typeface", replacement)
            changed += 1
    if not changed:
        res.status = _STATUS_SKIPPED
        res.detail = "переписываемых typeface не нашлось"
        return res
    res.detail = (
        f"{changed} run(s): typeface → '{replacement}'; "
        f"keepers={sorted(keepers)}"
    )
    return res


def _measure_ctx(prs) -> Any:
    """Мини-ctx для разрешения цветов аудита: палитры-кеш + размеры
    слайда (_shape_bbox/_backdrop_rgb/_run_text_rgb их читают)."""

    @dataclass
    class _Pal:
        theme_palettes: dict = field(default_factory=dict)
        slide_width: int = 0
        slide_height: int = 0

    return _Pal(slide_width=prs.slide_width, slide_height=prs.slide_height)


def _audited_text_shapes(shapes):
    """Фигуры, чей текст аудит реально оценивает (как в check_*): есть
    непустой текст, не footerish placeholder, не decorative body."""
    for shape in shapes:
        if not shape.has_text_frame or not shape.text_frame.text.strip():
            continue
        if _is_footerish_placeholder(shape) or _is_decorative_audit_body(
            shape
        ):
            continue
        yield shape


def _shape_bg(shape, slide, measure, palette):
    """Подложка под текст фигуры: собственный solid fill, иначе фон по
    z-order под центром (тот же разрешитель, что у check_contrast).
    Возвращает _UNRESOLVED, когда цвет честно не определить."""
    # тот же разрешитель, что у check_contrast, включая полупрозрачные заливки
    return effective_fill_rgb(shape, slide, measure, palette)


def _apply_map_color(prs, action: RepairAction, slide) -> ActionResult:
    """map_color: off-palette явные цвета → ближайший слот палитры темы.

    Мутация того множества, что флагает check_color_palette:
    ``a:solidFill`` в ``p:spPr`` фигуры и в ``a:rPr`` непустых ранов
    (footerish/decorative тела для ранов пропускаются как в правиле).
    ``params.scope == "text"`` сужает действие до текстовых ранов —
    заливки/фон не трогаются (ADR-0001). Каждый offending color element
    заменяется на ``a:srgbClr`` ближайшего по ΔE (CIE76) слота clrScheme
    — либо на ``params.color``, если planner задал цель явно.

    Без пина ``params.color`` действие также чинит низкий контраст
    ранов (accessibility.contrast): перекраска идёт в слот с макс. ratio
    к разрешённой подложке, и только когда это строго улучшает текущий
    ratio — ни palette-remap, ни contrast-pass не ухудшают читаемость.
    """
    res = ActionResult(index=-1, action_type="map_color", status=_STATUS_APPLIED)
    cfg = default_audit_config()
    measure = _measure_ctx(prs)
    palette = _theme_palette(slide, measure)
    if not palette:
        res.status = _STATUS_SKIPPED
        res.detail = "у темы слайда нет разрешимой clrScheme-палитры"
        return res
    slots = list(palette.values())
    tol = cfg.color_tolerance_delta_e
    min_ratio = cfg.contrast_min_ratio
    p_ns = "http://schemas.openxmlformats.org/presentationml/2006/main"
    pinned = (
        tuple(int(action.params.color[i : i + 2], 16) for i in (0, 2, 4))
        if action.params and action.params.color
        else None
    )
    scope = action.params.scope if action.params else None
    text_only = scope is not None and scope.value == "text"

    shapes = _action_shapes(action, slide)
    if not shapes:
        res.status = _STATUS_SKIPPED
        res.detail = "target.shape_ids пуст и на слайде нет адресатов"
        return res

    # Пред-замер до любых записей: исходный ratio каждого рана и его
    # подложка. Guard против ухудшения — ни palette-remap, ни contrast-
    # pass не опускают читаемость ниже исходной (review #134).
    pre: dict[Any, tuple[float, Any]] = {}
    if pinned is None:
        for shape in _audited_text_shapes(shapes):
            bg = _shape_bg(shape, slide, measure, palette)
            if bg is _UNRESOLVED:
                continue
            for paragraph in shape.text_frame.paragraphs:
                for run in paragraph.runs:
                    if not run.text.strip():
                        continue
                    fg = _run_text_rgb(
                        run, paragraph, shape, slide, measure, palette
                    )
                    if fg is _UNRESOLVED:
                        continue
                    pre[run._r] = (_contrast_ratio(fg, bg), bg)

    changed: list[str] = []
    kept_readable = 0
    for shape in shapes:
        containers: list[tuple[Any, Any]] = []
        if not text_only:
            sp_pr = shape.element.find(f"{{{p_ns}}}spPr")
            if sp_pr is not None:
                containers.append((sp_pr, None))
        if shape.has_text_frame and not (
            _is_footerish_placeholder(shape) or _is_decorative_audit_body(shape)
        ):
            for rpr in shape.text_frame._txBody.findall(f".//{{{A}}}rPr"):
                run_el = rpr.getparent()
                text_el = (
                    run_el.find(f"{{{A}}}t") if run_el is not None else None
                )
                if text_el is None or not (text_el.text or "").strip():
                    continue
                containers.append((rpr, run_el))
        for parent, run_el in containers:
            for sf in list(parent.iter(f"{{{A}}}solidFill")):
                for clr in list(sf):
                    tag = etree.QName(clr).localname
                    if tag == "schemeClr":
                        continue
                    rgb = _resolve_color_element(clr, palette)
                    if rgb is _UNRESOLVED or _is_neutral_rgb(rgb):
                        continue
                    if min(_delta_e(rgb, slot) for slot in slots) <= tol:
                        continue
                    best = pinned or min(slots, key=lambda s: _delta_e(rgb, s))
                    info = run_el is not None and pre.get(run_el)
                    if info:
                        orig_ratio, bg = info
                        if _contrast_ratio(best, bg) < orig_ratio:
                            kept_readable += 1
                            continue
                    hexv = f"{best[0]:02X}{best[1]:02X}{best[2]:02X}"
                    if tag == "srgbClr" and len(clr) == 0:
                        clr.set("val", hexv)
                    else:
                        new = etree.SubElement(sf, f"{{{A}}}srgbClr")
                        new.set("val", hexv)
                        sf.replace(clr, new)
                    changed.append(
                        f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}→#{hexv}"
                    )
    # accessibility.contrast: тот же проход чинит низкий контраст ранов —
    # непроходящие перекрашиваются в слот clrScheme с наибольшим ratio
    # против разрешённой подложки (DNA-цвет темы, не хардкод), и только
    # если слот строго лучше текущего ratio. Пинned params.color —
    # явная воля пользователя, contrast-pass не трогаем.
    partial = False
    unimproved = 0
    if pinned is None:
        for shape in _audited_text_shapes(shapes):
            bg = _shape_bg(shape, slide, measure, palette)
            if bg is _UNRESOLVED:
                continue
            best_slot = max(slots, key=lambda c: _contrast_ratio(c, bg))
            best_ratio = _contrast_ratio(best_slot, bg)
            for paragraph in shape.text_frame.paragraphs:
                for run in paragraph.runs:
                    if not run.text.strip():
                        continue
                    fg = _run_text_rgb(
                        run, paragraph, shape, slide, measure, palette
                    )
                    if fg is _UNRESOLVED:
                        continue
                    cur = _contrast_ratio(fg, bg)
                    if cur >= min_ratio:
                        continue
                    if best_ratio <= cur:
                        unimproved += 1
                        continue
                    run.font.color.rgb = RGBColor(*best_slot)
                    if best_ratio < min_ratio:
                        partial = True
                    changed.append(
                        "contrast "
                        f"{shape.shape_id}:{cur:.2f}→{best_ratio:.2f}"
                    )
    if not changed:
        res.status = _STATUS_SKIPPED
        notes = []
        if kept_readable:
            notes.append(
                f"{kept_readable} цветов не перенесены — палитра ухудшила "
                "бы читаемость"
            )
        if unimproved:
            notes.append(
                f"{unimproved} ранов без слота лучше текущего ratio"
            )
        res.detail = (
            "off-palette явных цветов и нарушений контраста не нашлось"
            if not notes
            else "нет улучшения: " + "; ".join(notes)
        )
        return res
    res.detail = f"{len(changed)} цветов: " + ", ".join(changed[:6])
    if kept_readable:
        res.detail += (
            f"; {kept_readable} цветов оставлены — перенос в палитру "
            "ухудшил бы читаемость"
        )
    if unimproved:
        res.detail += (
            f"; {unimproved} ранов без слота лучше текущего — "
            "останутся flagged при re-audit"
        )
    if partial:
        res.detail += (
            f"; частично: палитра не дотягивает до {min_ratio:.1f} "
            "— применён лучший слот"
        )
    return res


def _apply_merge_slide(prs, action: RepairAction, slide) -> ActionResult:
    """merge_slide → удаление слайда-дубля из колоды.

    Находит запись слайда в ``sldIdLst`` по rel→slide.part, убирает её
    и снимает relationship: осиротевший slide-парт python-pptx не
    сериализует (iter_parts идёт по rels-графу), так что в пакете не
    остаётся ни висячего партa, ни dangling-rel. Смысл merge для
    почти-идентичных слайдов — оставить оригинал; возможная потеря
    ≤10% расходящихся токенов честно заявлена в preconditions
    действия ещё planner'ом.
    """
    res = ActionResult(index=-1, action_type="merge_slide", status=_STATUS_APPLIED)
    # noqa: SLF001 — публичного API удаления слайда в python-pptx нет
    sld_id_lst = prs.slides._sldIdLst
    match = None
    for sld_id in sld_id_lst.sldId_lst:
        try:
            rel = prs.part.rels[sld_id.rId]
        except KeyError:
            continue
        if not rel.is_external and rel.target_part is slide.part:
            match = sld_id
            break
    if match is None:
        res.status = _STATUS_FAILED
        res.detail = "запись слайда в sldIdLst не найдена"
        return res

    r_id = match.rId
    sld_id_lst.remove(match)
    prs.part.drop_rel(r_id)
    res.detail = "слайд удалён из sldIdLst и relationships"
    return res


def apply_repairs(
    pptx_path: str | Path,
    actions: list[RepairAction],
    out_path: str | Path,
    protected: Iterable[int] | ProtectedSlides | None = None,
) -> RepairApplyReport:
    """Применить список RepairAction к копии pptx_path → out_path.

    Порядок действий сохраняется за planner'ом (blocker-first);
    каждое действие изолировано — исключение одного не роняет
    остальные и помечается failed.

    *protected* — 0-based индексы защищённых слайдов (OR-031) или
    готовый ProtectedSlides: действия, чей target указывает на
    защищённый слайд, не применяются и репортятся skipped с
    пометкой protected_slide.
    """
    pptx_path = Path(pptx_path)
    out_path = Path(out_path)
    if not pptx_path.exists():
        raise DeckDNAError(
            code="internal_error",
            message=f"входной файл не найден: {pptx_path}",
            stage=STAGE,
        )
    try:
        from pptx import Presentation
    except ImportError as exc:  # pragma: no cover — python-pptx всегда стоит в env
        raise DeckDNAError(
            code="internal_error",
            message="python-pptx не установлен — apply невозможен",
            stage=STAGE,
        ) from exc

    try:
        prs = Presentation(str(pptx_path))
    except Exception as exc:
        raise DeckDNAError(
            code="internal_error",
            message=f"не удалось открыть {pptx_path}: {exc}",
            stage=STAGE,
        ) from exc

    guard = (
        protected
        if isinstance(protected, ProtectedSlides)
        else ProtectedSlides(protected or ())
    )
    slide_index_of = {
        str(slide.slide_id): i for i, slide in enumerate(prs.slides)
    }

    report = RepairApplyReport(out_path=str(out_path))
    for idx, action in enumerate(actions):
        atype = action.action_type.value if hasattr(action.action_type, "value") else str(
            action.action_type
        )
        if atype not in _IMPLEMENTED:
            report.results.append(
                ActionResult(
                    index=idx,
                    action_type=atype,
                    status=_STATUS_NOT_IMPLEMENTED,
                    detail="исполнитель для этого action_type ещё не реализован",
                )
            )
            continue
        if guard.indices:
            _, rejected = guard.filter_actions(
                [action.model_dump(mode="json")], slide_index_of
            )
            if rejected:
                report.results.append(
                    ActionResult(
                        index=idx,
                        action_type=atype,
                        status=_STATUS_SKIPPED,
                        detail="protected_slide: действие на защищённом слайде",
                    )
                )
                continue
        slide = _find_slide(prs, action.target.slide_id)
        if slide is None:
            report.results.append(
                ActionResult(
                    index=idx,
                    action_type=atype,
                    status=_STATUS_SKIPPED,
                    detail=f"slide_id {action.target.slide_id} не найден в колоде",
                )
            )
            continue
        try:
            if atype == "resize_shape":
                res = _apply_resize_shape(prs, action, slide)
            elif atype == "recrop_image":
                res = _apply_recrop_image(prs, action, slide)
            elif atype == "remove_placeholder":
                res = _apply_remove_placeholder(prs, action, slide)
            elif atype == "move_shape":
                res = _apply_move_shape(prs, action, slide)
            elif atype == "shorten_text":
                res = _apply_shorten_text(prs, action, slide)
            elif atype == "map_font":
                res = _apply_map_font(prs, action, slide)
            elif atype == "map_color":
                res = _apply_map_color(prs, action, slide)
            else:
                res = _apply_merge_slide(prs, action, slide)
            res.index = idx
            report.results.append(res)
        except Exception as exc:  # noqa: BLE001 — изоляция per-action
            logger.exception("apply %s failed", atype)
            report.results.append(
                ActionResult(
                    index=idx,
                    action_type=atype,
                    status=_STATUS_FAILED,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        prs.save(str(out_path))
    except Exception as exc:
        raise DeckDNAError(
            code="internal_error",
            message=f"не удалось сохранить {out_path}: {exc}",
            stage=STAGE,
        ) from exc

    logger.info(
        "repair apply: %d actions -> %d applied, %d skipped, %d failed, %d not_implemented",
        len(actions),
        report.applied,
        report.skipped,
        report.failed,
        report.not_implemented,
    )
    return report
