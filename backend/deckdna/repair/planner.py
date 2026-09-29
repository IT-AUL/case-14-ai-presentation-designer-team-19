"""Repair Planner — детерминированный маппинг AuditIssue → RepairAction.

Только планирование: функция НЕ трогает .pptx. Применение действий —
отдельная стадия (executor), здесь формируется список типизированных
предложений по замороженному контракту contracts/repair_action.py.

Маппинг rule_code → action_type:

- ``text.overflow`` → ``resize_shape`` + цепочечный ``shorten_text``:
  расширение рамки по оси переполнения на реальный ratio
  ``measured/threshold`` (оба числа — из issue). ``font_size_pt``
  честно вычислить нельзя — текущий кегль в AuditIssue не
  переносится, а ``shorten_text`` теряет контент. При коллизии со
  слайд-боксом (или соседями — их видит только apply) расширение
  зажимается и остаток не закрывается геометрией → за resize_shape
  всегда ставится самопроверяющийся ``shorten_text``: исполнитель
  перемеряет фактическую вместимость и пропускает фигуру, если resize
  уже справился, иначе урезает текст с '…'.
- ``image.aspect_ratio`` → ``recrop_image`` с ``keep_aspect=True``.
- ``integrity.empty_slide`` → ``merge_slide`` (пустой слайд сливается
  с соседним — в таксономии нет remove_slide, merge покрывает удаление
  пустого). ``new_index`` — индекс соседа-поглотителя.
- ``editability.raster_only`` → ``native_rebuild``: не авто-чинится
  одной типизированной операцией (нужна регенерация из векторных
  источников) — это флаг «требует внимания» для пользователя.
- ``layout.out_of_bounds`` → ``resize_shape``: bbox из issue зажимается
  в границы слайда [0,1] — одна типизированная операция покрывает и
  move, и resize (``move_shape`` используется для edge_margin).
- ``integrity.duplicate_slide`` → ``merge_slide``: в таксономии нет
  ``remove_slide``, а merge в исходный слайд — то же удаление повтора;
  индекс оригинала берётся из evidence (``vs slide[N]``). Сходство
  вероятностное (jaccard ≥0.9) — preconditions честно фиксируют, что
  расхождения до ~10% токенов могут быть потеряны и выбор за
  пользователем (issues user-selectable по ТЗ).
- ``integrity.placeholder_text`` → ``remove_placeholder``: фигура
  целиком состоит из стокового текста — удаление фрейма теряет только
  заглушку. STRONG-маркер внутри длинного текста — редкий кейс; для
  него удаление фигуры в preconditions помечено как возможно затраги-
  вающее настоящий контент (финальный выбор за пользователем).
- ``template.anchor_position`` → ``resize_shape``: issue уже несёт
  declared-xfrm (x/y/cx/cy из layout/master) в evidence detail —
  действие выставляет все четыре компоненты обратно на declared
  (одна операция покрывает и сдвиг, и смену размера).
- ``text.slide_clip`` → ``move_shape`` + цепочечный ``shorten_text``:
  при однозначно-вертикальном вылете (anchor t/b) фигура сдвигается
  на величину вылета, иначе/дополнительно текст усекается под
  вместимость рамки — рамка внутри слайда, поэтому поместившийся
  в неё текст гарантированно не обрезается краем.

Issue без известного rule_code или без slide_id/target-геометрии не
получает выдуманного действия — он попадает в ``RepairPlanReport.
unresolved`` с причиной. Покрытие честное, не подгоняется под 100%.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from deckdna.audit.basic import (
    RULE_ANCHOR_POSITION,
    RULE_ASPECT_RATIO,
    RULE_CHART_METADATA,
    RULE_CHART_SERIES,
    RULE_COLOR_PALETTE,
    RULE_CONTRAST,
    RULE_DUPLICATE_SLIDE,
    RULE_EDGE_MARGIN,
    RULE_EMPTY_SLIDE,
    RULE_FONT_FAMILY,
    RULE_FONT_SCALE,
    RULE_LAYOUT_ORIGIN,
    RULE_OCCUPANCY,
    RULE_OUT_OF_BOUNDS,
    RULE_PACKAGE,
    RULE_PLACEHOLDER_TEXT,
    RULE_RASTER_ONLY,
    RULE_SLIDE_CLIP,
    RULE_TABLE_SIZE,
    RULE_TEXT_OVERFLOW,
    RULE_UNINTENDED_OVERLAP,
)
from deckdna.audit.config import default_audit_config
from deckdna.audit.issues import AuditIssue
from deckdna.contracts.repair_action import (
    ActionType,
    Bbox,
    Params,
    RepairAction,
    Target,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"
PLANNER_VERSION = "repair-planner/0.1.0"

# Порядок выдачи: blocker первыми — это то, что пользователь должен
# увидеть в панели repair раньше остального.
_SEVERITY_RANK = {"blocker": 0, "error": 1, "warning": 2, "info": 3}


@dataclass
class RepairPlanReport:
    """Результат планирования: действия + честная статистика покрытия."""

    actions: list[RepairAction]
    planned_issue_ids: list[str]
    # issue_id -> причина, по которой действие не предложено
    unresolved: dict[str, str] = field(default_factory=dict)

    @property
    def coverage(self) -> float:
        total = len(self.planned_issue_ids) + len(self.unresolved)
        return len(self.planned_issue_ids) / total if total else 1.0


def _target(issue: AuditIssue) -> Target | None:
    if issue.slide_id is None:
        return None
    return Target(slide_id=str(issue.slide_id), shape_ids=issue.shape_ids or None)


_AXES_RE = {
    "w": re.compile(
        r"(?:unbreakable word|widest line)\s*≈\s*([\d.]+)pt\s*>\s*usable width\s*([\d.]+)pt"
    ),
    "h": re.compile(
        r"estimated text height\s*≈\s*([\d.]+)pt\s*>\s*usable height\s*([\d.]+)pt"
    ),
}


def _axis_ratios(issue: AuditIssue) -> tuple[float | None, float | None]:
    """Per-axis overflow ratios parsed from evidence detail text.

    audit/basic.py emits one or two clauses: 'unbreakable word ≈Xpt >
    usable width Ypt' / 'widest line ≈Xpt > usable width Ypt' and
    'estimated text height ≈Xpt > usable height Ypt'. measured_value /
    threshold mix axes (max of the two), so per-axis numbers must come
    from the detail text itself.
    """
    detail = " ".join(e.get("detail", "") for e in (issue.evidence or []))
    ratios: list[float | None] = []
    for axis in ("w", "h"):
        m = _AXES_RE[axis].search(detail)
        if m and float(m.group(2)) > 0:
            ratios.append(float(m.group(1)) / float(m.group(2)))
        else:
            ratios.append(None)
    return ratios[0], ratios[1]


def _overflow_action(issue: AuditIssue) -> list[RepairAction] | None:
    """resize_shape + shorten_text: расширение рамки по каждой оси
    переполнения, с fallback-усечением текста за ним.

    Каждая ось масштабируется своим ratio из evidence detail
    (unbreakable-word/width и text-height/height могут встречаться
    вместе); если detail не парсится — fallback на measured/threshold
    по оси из detail ('width' → горизонталь, иначе вертикаль). Новый
    bbox зажат в границы слайда [0,1].
    """
    target = _target(issue)
    if target is None:
        return None
    detail = " ".join(e.get("detail", "") for e in (issue.evidence or []))
    w_ratio, h_ratio = _axis_ratios(issue)
    if w_ratio is None and h_ratio is None and isinstance(
        issue.measured_value, int | float
    ) and isinstance(issue.threshold, int | float) and issue.threshold:
        ratio = float(issue.measured_value) / float(issue.threshold)
        if "width" in detail or "wrap=none" in issue.message:
            w_ratio = ratio
        else:
            h_ratio = ratio

    new_bbox = None
    pre = ["фигура существует на слайде и доступна для изменения"]
    if issue.bbox:
        x, y = issue.bbox["x"], issue.bbox["y"]
        w, h = issue.bbox["w"], issue.bbox["h"]
        if w_ratio is not None and w_ratio > 1.0:
            w = min(w * w_ratio, 1.0 - x)
        if h_ratio is not None and h_ratio > 1.0:
            h = min(h * h_ratio, 1.0 - y)
        if w != issue.bbox["w"] or h != issue.bbox["h"]:
            new_bbox = Bbox(x=x, y=y, w=round(w, 4), h=round(h, 4))
            if x + w >= 1.0 or y + h >= 1.0:
                pre.append(
                    "расширение упёрлось в границу слайда — остаток "
                    "переполнения закроет shorten_text"
                )
            pre.append("расширенная рамка не пересекается с соседними фигурами")
    resize = RepairAction(
        schema_version=SCHEMA_VERSION,
        action_type=ActionType.resize_shape,
        issue_ids=[issue.id],
        target=target,
        params=Params(bbox=new_bbox),
        preconditions=pre,
        expected_postconditions=[
            "текст помещается в рамку без изменения содержимого",
            f"повторный аудит не выдаёт {RULE_TEXT_OVERFLOW} для этого shape",
        ],
    )
    # Fallback: зажим у соседей/у границы виден не планировщику, а
    # исполнителю — shorten_text сам перемеряет вместимость и пропускает
    # уже вписавшийся текст, поэтому ставится всегда вторым шагом.
    shorten = RepairAction(
        schema_version=SCHEMA_VERSION,
        action_type=ActionType.shorten_text,
        issue_ids=[issue.id],
        target=target,
        params=Params(),
        preconditions=[
            "применяется только если после resize_shape/зажима overflow "
            "остаётся — исполнитель перемеряет вместимость и пропускает "
            "уже помещающийся текст",
            "часть текста усекается с '…' и не восстанавливается — "
            "действие на выбор пользователя",
        ],
        expected_postconditions=[
            "текст помещается в рамку при сохранённой геометрии",
            f"повторный аудит не выдаёт {RULE_TEXT_OVERFLOW} для этого shape",
        ],
    )
    return [resize, shorten]


def _aspect_ratio_action(issue: AuditIssue) -> RepairAction | None:
    target = _target(issue)
    if target is None:
        return None
    return RepairAction(
        schema_version=SCHEMA_VERSION,
        action_type=ActionType.recrop_image,
        issue_ids=[issue.id],
        target=target,
        params=Params(keep_aspect=True),
        preconditions=["image part читаем из пакета"],
        expected_postconditions=[
            "отображаемые пропорции совпадают с исходными (±3%)",
            f"повторный аудит не выдаёт {RULE_ASPECT_RATIO} для этого shape",
        ],
    )


def _empty_slide_action(issue: AuditIssue) -> RepairAction | None:
    """merge_slide: пустой слайд сливается с соседним (в таксономии
    action_type нет remove_slide — merge покрывает удаление пустого)."""
    target = _target(issue)
    if target is None:
        return None
    idx = issue.slide_index or 0
    merge_into = idx - 1 if idx > 0 else idx + 1
    return RepairAction(
        schema_version=SCHEMA_VERSION,
        action_type=ActionType.merge_slide,
        issue_ids=[issue.id],
        target=target,
        params=Params(new_index=merge_into),
        preconditions=["слайд-поглотитель существует (соседний по индексу)"],
        expected_postconditions=[
            "пустой слайд удалён из колоды, контент не потерян",
            f"повторный аудит не выдаёт {RULE_EMPTY_SLIDE} для этого слайда",
        ],
    )


def _out_of_bounds_action(issue: AuditIssue) -> RepairAction | None:
    """resize_shape: фигура возвращается в границы слайда [0,1].

    Берёт issue.bbox и зажимает: позицию в [0, 1], размер — чтобы
    x+w ≤ 1 и y+h ≤ 1. Одна операция resize_shape покрывает и move, и
    resize (обе записываются в a:xfrm); отдельный ``move_shape``
    исполнителя пока не имеет.
    """
    target = _target(issue)
    if target is None or not issue.bbox:
        return None
    x = max(0.0, float(issue.bbox["x"]))
    y = max(0.0, float(issue.bbox["y"]))
    w = min(float(issue.bbox["w"]), 1.0 - x)
    h = min(float(issue.bbox["h"]), 1.0 - y)
    if w <= 0 or h <= 0:
        return None
    return RepairAction(
        schema_version=SCHEMA_VERSION,
        action_type=ActionType.resize_shape,
        issue_ids=[issue.id],
        target=target,
        params=Params(
            bbox=Bbox(x=round(x, 4), y=round(y, 4), w=round(w, 4), h=round(h, 4))
        ),
        preconditions=[
            "фигура существует на слайде и доступна для изменения",
            "сдвинутая/сжатая рамка не пересекается с соседними фигурами",
        ],
        expected_postconditions=[
            "вся фигура внутри границ слайда",
            f"повторный аудит не выдаёт {RULE_OUT_OF_BOUNDS} для этого shape",
        ],
    )


_DUP_ORIGINAL_RE = re.compile(r"slide\[(\d+)\]")


def _duplicate_slide_action(issue: AuditIssue) -> RepairAction | None:
    """merge_slide в индекс исходного слайда: повтор растворяется в
    оригинале. В ActionType нет ``remove_slide`` — merge покрывает
    удаление дубликата; контент повтора почти идентичен оригиналу,
    потери ограничены ~10% токенов (порог аудита) и фиксируются в
    preconditions: финальный выбор за пользователем.
    """
    target = _target(issue)
    if target is None:
        return None
    detail = " ".join(e.get("detail", "") for e in (issue.evidence or []))
    m = _DUP_ORIGINAL_RE.search(detail)
    if m is None:
        return None
    original_idx = int(m.group(1))
    return RepairAction(
        schema_version=SCHEMA_VERSION,
        action_type=ActionType.merge_slide,
        issue_ids=[issue.id],
        target=target,
        params=Params(new_index=original_idx),
        preconditions=[
            f"слайд {original_idx + 1} — исходный экземпляр и сохраняется",
            "сходство текстовое (jaccard ≥0.9), не побайтовое: расхождения "
            "до ~10% токенов могут быть потеряны — действие на выбор пользователя",
        ],
        expected_postconditions=[
            "повторный слайд удалён, исходный сохранён",
            f"повторный аудит не выдаёт {RULE_DUPLICATE_SLIDE} для этого слайда",
        ],
    )


def _raster_only_action(issue: AuditIssue) -> RepairAction | None:
    """native_rebuild — флаг «требует внимания»: одной типизированной
    операцией raster-only слайд не чинится (нужны векторные источники)."""
    target = _target(issue)
    if target is None:
        return None
    return RepairAction(
        schema_version=SCHEMA_VERSION,
        action_type=ActionType.native_rebuild,
        issue_ids=[issue.id],
        target=target,
        params=Params(),
        preconditions=[
            "требует внимания пользователя: авто-репейр невозможен — "
            "нужна регенерация слайда из векторных/текстовых источников"
        ],
        expected_postconditions=[
            "слайд собран нативными фигурами, повторный аудит не выдаёт "
            f"{RULE_RASTER_ONLY}"
        ],
    )


def _placeholder_text_action(issue: AuditIssue) -> RepairAction | None:
    """remove_placeholder: фигура целиком — стоковый текст, удаление
    теряет только заглушку. Для STRONG-маркера внутри длинного текста
    preconditions честно помечают риск удаления настоящего контента."""
    target = _target(issue)
    if target is None:
        return None
    detail = " ".join(e.get("detail", "") for e in (issue.evidence or []))
    pre = ["фигура существует на слайде и доступна для изменения"]
    if "strong marker" in detail:
        pre.append(
            "маркер внутри более длинного текста — удаление фигуры может "
            "затронуть настоящий контент, действие на выбор пользователя"
        )
    else:
        pre.append("фигура содержит только placeholder-текст")
    return RepairAction(
        schema_version=SCHEMA_VERSION,
        action_type=ActionType.remove_placeholder,
        issue_ids=[issue.id],
        target=target,
        params=Params(),
        preconditions=pre,
        expected_postconditions=[
            "стоковый текст-заглушка удалён из колоды",
            f"повторный аудит не выдаёт {RULE_PLACEHOLDER_TEXT} для этого shape",
        ],
    )


_EDGE_SIDE_RE = re.compile(r"(left|right|top|bottom) edge gap")


def _edge_margin_action(issue: AuditIssue) -> RepairAction | None:
    """move_shape: сдвиг прижатой фигуры внутрь на недостающий отступ.

    Направление однозначно по прижатой стороне из evidence detail
    ('<side> edge gap = ...'); запасной путь — сторона с минимальным
    зазором по issue.bbox. Целевая координата — ровно edge_margin
    (порог из configs/audit.default.yaml) от края; размер и ось по
    другой координате не меняются (params.bbox несёт только
    сдвигаемую ось). Фигура, которая не помещается в margin-полосу
    с двух сторон (w > 1 − 2·margin), неподвижна — честный
    unresolved, а не догадывание.
    """
    margin = default_audit_config().edge_margin
    target = _target(issue)
    if target is None or not issue.bbox:
        return None

    detail = " ".join(e.get("detail", "") for e in (issue.evidence or []))
    m = _EDGE_SIDE_RE.search(detail)
    if m:
        side = m.group(1)
    else:
        # fallback: минимальный зазор по bbox — та же сторона, что
        # решил детектор, определяется однозначно
        gaps = {
            "left": float(issue.bbox["x"]),
            "right": 1.0 - float(issue.bbox["x"]) - float(issue.bbox["w"]),
            "top": float(issue.bbox["y"]),
            "bottom": 1.0 - float(issue.bbox["y"]) - float(issue.bbox["h"]),
        }
        side = min(gaps, key=gaps.get)

    w = float(issue.bbox["w"])
    h = float(issue.bbox["h"])
    if side in ("left", "right"):
        if w > 1.0 - 2 * margin:
            return None  # фигура шире внутренней полосы — некуда сдвинуть
        x = margin if side == "left" else 1.0 - margin - w
        bbox = Bbox(x=round(x, 4))
    else:
        if h > 1.0 - 2 * margin:
            return None
        y = margin if side == "top" else 1.0 - margin - h
        bbox = Bbox(y=round(y, 4))

    return RepairAction(
        schema_version=SCHEMA_VERSION,
        action_type=ActionType.move_shape,
        issue_ids=[issue.id],
        target=target,
        params=Params(bbox=bbox),
        preconditions=[
            "фигура существует на слайде и доступна для изменения",
            "сдвиг внутрь не создаёт пересечений с соседними фигурами",
        ],
        expected_postconditions=[
            f"фигура отодвинута от {side} края на отступ {margin:.0%}",
            f"повторный аудит не выдаёт {RULE_EDGE_MARGIN} для этого shape",
        ],
    )


_XFRM_PAIR_RE = re.compile(
    r"own xfrm=\((-?\d+), (-?\d+), (-?\d+), (-?\d+)\)\s*vs\s*"
    r"declared=\((-?\d+), (-?\d+), (-?\d+), (-?\d+)\)"
)


def _anchor_position_action(issue: AuditIssue) -> RepairAction | None:
    """resize_shape: вернуть xfrm фигуры на declared-значения.

    check_anchor_position уже вычислил declared-позицию (x/y/cx/cy из
    layout/master) — она зашита в evidence detail строкой
    'own xfrm=(…) vs declared=(…)' в EMU. Planner не знает размер
    слайда, поэтому нормализация [0,1] восстанавливается из самого
    issue: sw_emu = own_cx / bbox.w (bbox — тот же own xfrm,
    нормализованный _shape_bbox). Сдвиг и смена размера закрываются
    одним resize_shape — отдельный move_shape покрыл бы только off.
    """
    target = _target(issue)
    if target is None or not issue.bbox:
        return None
    detail = " ".join(e.get("detail", "") for e in (issue.evidence or []))
    m = _XFRM_PAIR_RE.search(detail)
    if m is None:
        return None
    own = [int(g) for g in m.groups()[:4]]
    decl = [int(g) for g in m.groups()[4:]]
    bw, bh = float(issue.bbox["w"]), float(issue.bbox["h"])
    if bw <= 0 or bh <= 0 or own[2] <= 0 or own[3] <= 0:
        return None
    sw, sh = own[2] / bw, own[3] / bh  # размер слайда в EMU
    return RepairAction(
        schema_version=SCHEMA_VERSION,
        action_type=ActionType.resize_shape,
        issue_ids=[issue.id],
        target=target,
        params=Params(
            bbox=Bbox(
                x=round(decl[0] / sw, 4),
                y=round(decl[1] / sh, 4),
                w=round(decl[2] / sw, 4),
                h=round(decl[3] / sh, 4),
            )
        ),
        preconditions=[
            "фигура существует на слайде и доступна для изменения",
            "declared-позиция актуальна (issue от того же аудита, "
            "layout/master не менялись)",
        ],
        expected_postconditions=[
            "x/y/cx/cy фигуры совпадают с задекларированными на layout",
            f"повторный аудит не выдаёт {RULE_ANCHOR_POSITION} для этого shape",
        ],
    )


_CLIP_RE = re.compile(
    r"anchor=(\w+)\s+wrap=\w+;\s*text extent beyond slide edge "
    r"x=(-?[\d.]+)pt\s+y=(-?[\d.]+)pt"
)


def _slide_clip_action(issue: AuditIssue) -> list[RepairAction] | None:
    """move_shape + shorten_text: втянуть экстент текста внутрь слайда.

    Правило срабатывает только когда рамка внутри слайда, а текст
    вылетает за край (anchor t/b — вертикально, wrap=none —
    горизонтально по algn). Чисто-вертикальный вылет с однозначным
    направлением (anchor t → вниз, b → вверх) чинится move_shape на
    нормализованную величину вылета (measured_value — доля размера
    слайда); executor сам зажимает позицию в границы, поэтому
    остаток честно остаётся. Для 'ctr' и горизонтальных вылетов
    направление из issue не восстановить (algn в evidence нет) —
    move не предлагается. За всеми вариантами стоит самопроверяющийся
    shorten_text: текст, поместившийся в рамку, не может выйти за
    край (рамка внутри слайда по предпосылке правила).
    """
    target = _target(issue)
    if target is None:
        return None
    detail = " ".join(e.get("detail", "") for e in (issue.evidence or []))
    m = _CLIP_RE.search(detail)

    actions: list[RepairAction] = []
    if (
        m is not None
        and issue.bbox
        and isinstance(issue.measured_value, int | float)
        and float(issue.measured_value) > 0
    ):
        anchor, clip_x, clip_y = (
            m.group(1),
            float(m.group(2)),
            float(m.group(3)),
        )
        frac = float(issue.measured_value)
        y = float(issue.bbox["y"])
        # только чистый вертикальный вылет: measured_value — это доля
        # слайда на оси y ровно когда x-компонента нет
        if clip_y > 0 and clip_x <= 0 and anchor in ("t", "b"):
            new_y = max(0.0, y - frac) if anchor == "t" else y + frac
            actions.append(
                RepairAction(
                    schema_version=SCHEMA_VERSION,
                    action_type=ActionType.move_shape,
                    issue_ids=[issue.id],
                    target=target,
                    params=Params(bbox=Bbox(y=round(new_y, 4))),
                    preconditions=[
                        "фигура существует на слайде и доступна для изменения",
                        "сдвиг на величину вылета не пересекает соседние "
                        "фигуры — на выбор пользователя",
                    ],
                    expected_postconditions=[
                        "экстент текста втянут внутрь слайда без потери контента",
                        f"повторный аудит не выдаёт {RULE_SLIDE_CLIP} "
                        "для этого shape",
                    ],
                )
            )
    actions.append(
        RepairAction(
            schema_version=SCHEMA_VERSION,
            action_type=ActionType.shorten_text,
            issue_ids=[issue.id],
            target=target,
            params=Params(),
            preconditions=[
                "исполнитель сам перемеряет вместимость рамки и "
                "пропускает уже помещающийся текст",
                "часть текста усекается с '…' и не восстанавливается — "
                "действие на выбор пользователя",
            ],
            expected_postconditions=[
                "текст помещается в рамку внутри слайда — край не обрезает",
                f"повторный аудит не выдаёт {RULE_SLIDE_CLIP} для этого shape",
            ],
        )
    )
    return actions


def _font_family_action(issue: AuditIssue) -> RepairAction | None:
    """map_font: нормализация семейств к топ-N частотным.

    Цель — все фигуры слайда с latin-шрифтами (rule флагает их
    полностью в shape_ids). Исполнитель сам пересчитывает частоты и
    переписывает лишние typeface на доминирующий — planner не имеет
    доступа к деке, поэтому params.font_family не задаётся.
    """
    target = _target(issue)
    if target is None:
        return None
    return RepairAction(
        schema_version=SCHEMA_VERSION,
        action_type=ActionType.map_font,
        issue_ids=[issue.id],
        target=target,
        params=Params(),
        preconditions=[
            "фигуры с text_frame существуют на слайде и доступны",
            "лишние семейства заменяются на доминирующий typeface — "
            "визуальная нормализация, оригинальные шрифты теряются",
        ],
        expected_postconditions=[
            "на слайде не более max_font_families латинских семейств",
            f"повторный аудит не выдаёт {RULE_FONT_FAMILY} для этого слайда",
        ],
    )


def _contrast_action(issue: AuditIssue) -> RepairAction | None:
    """map_color(scope=text): низкий контраст текста → слот с макс. ratio.

    ``params.scope="text"`` (ADR-0001) — typed discriminator: действие
    трогает только цвета текстовых ранов, заливки фигур и фон не
    меняются. Исполнитель перемеряет подложку тем же разрешением, что и
    check_contrast (собственная заливка фигуры → подложка под центром →
    фон слайда), и перекрашивает каждый непроходящий ран в слот
    clrScheme с наибольшим contrast ratio — цвет темы, не хардкод.
    Запись идёт только при строгом улучшении ratio: когда ни один слот
    палитры не лучше текущего, цвет сохраняется и это честно видно
    в detail (unimproved); когда лучший слот не дотягивает до порога —
    «частично».
    """
    target = _target(issue)
    if target is None:
        return None
    return RepairAction(
        schema_version=SCHEMA_VERSION,
        action_type=ActionType.map_color,
        issue_ids=[issue.id],
        target=target,
        params=Params(scope="text"),
        preconditions=[
            "у темы слайда есть разрешимая clrScheme-палитра",
            "подложка разрешима к одному цвету (solid fill) — иначе "
            "исполнитель пропускает фигуру, не гадая",
            "scope=text: перекрашиваются только текстовые раны — "
            "заливки фигур и фон не трогаются; ран перекрашивается "
            "только в строго более читаемый слот палитры, иначе цвет "
            "сохраняется",
        ],
        expected_postconditions=[
            "каждый ран адресата имеет ratio >= contrast_min_ratio к "
            "подложке, либо лучший достижимый (partial в detail), "
            "никогда не хуже исходного",
            f"повторный аудит не выдаёт {RULE_CONTRAST} для этого слайда "
            "или фиксирует улучшенный ratio",
        ],
    )


def _color_palette_action(issue: AuditIssue) -> RepairAction | None:
    """map_color: off-palette явные цвета → ближайший слот clrScheme.

    Исполнитель пересчитывает offenders той же логикой, что и
    check_color_palette (явные solidFill в spPr и rPr непустых ранов,
    нейтрали и schemeClr исключаются), и ставит srgbClr ближайшего по
    ΔE (CIE76) слота палитры темы слайда.
    """
    target = _target(issue)
    if target is None:
        return None
    return RepairAction(
        schema_version=SCHEMA_VERSION,
        action_type=ActionType.map_color,
        issue_ids=[issue.id],
        target=target,
        params=Params(),
        preconditions=[
            "у темы слайда есть разрешимая clrScheme-палитра",
            "явные цвета заменяются на ближайший слот палитры — "
            "точный hex оригинала теряется",
        ],
        expected_postconditions=[
            "все явные цвета адресатов в пределах color_tolerance_delta_e",
            f"повторный аудит не выдаёт {RULE_COLOR_PALETTE} для этого слайда",
        ],
    )


_RULE_HANDLERS = {
    RULE_TEXT_OVERFLOW: _overflow_action,
    RULE_FONT_FAMILY: _font_family_action,
    RULE_COLOR_PALETTE: _color_palette_action,
    RULE_CONTRAST: _contrast_action,
    RULE_ASPECT_RATIO: _aspect_ratio_action,
    RULE_EMPTY_SLIDE: _empty_slide_action,
    RULE_RASTER_ONLY: _raster_only_action,
    RULE_OUT_OF_BOUNDS: _out_of_bounds_action,
    RULE_DUPLICATE_SLIDE: _duplicate_slide_action,
    RULE_PLACEHOLDER_TEXT: _placeholder_text_action,
    RULE_EDGE_MARGIN: _edge_margin_action,
    RULE_ANCHOR_POSITION: _anchor_position_action,
    RULE_SLIDE_CLIP: _slide_clip_action,
}

# Явные причины unresolved для правил без маппинга — честнее общего
# «нет маппинга»: фиксирует ПОЧЕМУ детерминированного действия нет.
_UNRESOLVED_REASONS = {
    RULE_UNINTENDED_OVERLAP: (
        "нет детерминированного fix: какую из двух фигур и куда двигать — "
        "решение дизайнера (нужен layout-reflow с поиском свободного "
        "места, не типизированный RepairAction)"
    ),
    # Правила новой волны аудита, требующие новых типов executor'ов
    # (split_slide, font_scale и т.п.; map_font/map_color уже покрыты) —
    # отдельная итерация repair-покрытия, не гадание существующими.
    RULE_CHART_SERIES: (
        "нет repair-маппинга: split/merge рядов диаграммы требует нового "
        "типа действия — отдельная итерация"
    ),
    RULE_OCCUPANCY: (
        "нет repair-маппинга: перераспределение плотности — layout-решение, "
        "нужен reflow-executor — отдельная итерация"
    ),
    RULE_FONT_SCALE: (
        "нет repair-маппинга: пересчёт шкалы кеглей требует нового "
        "типа действия — отдельная итерация"
    ),
    RULE_TABLE_SIZE: (
        "нет repair-маппинга: разбиение/сжатие таблицы требует "
        "split-executor — отдельная итерация"
    ),
    RULE_PACKAGE: (
        "нет repair-маппинга: нарушение целостности пакета не чинится "
        "типизированным действием на фигуре — отдельная итерация"
    ),
    RULE_CHART_METADATA: (
        "нет repair-маппинга: перепись метаданных диаграммы требует "
        "нового типа действия — отдельная итерация"
    ),
    RULE_LAYOUT_ORIGIN: (
        "нет repair-маппинга: чужой layout не заменяется типизированным "
        "действием — отдельная итерация"
    ),
}


def plan_repairs_with_report(issues: list[AuditIssue]) -> RepairPlanReport:
    """Полный план: действия + статистика покрытия (с причинами пропусков)."""
    actions: list[RepairAction] = []
    planned: list[str] = []
    unresolved: dict[str, str] = {}

    for issue in issues:
        handler = _RULE_HANDLERS.get(issue.rule_code)
        if handler is None:
            unresolved[issue.id] = _UNRESOLVED_REASONS.get(
                issue.rule_code,
                f"нет маппинга для rule_code {issue.rule_code!r}",
            )
            continue
        if not issue.repairable:
            unresolved[issue.id] = "issue помечен repairable=False"
            continue
        action = handler(issue)
        if action is None:
            unresolved[issue.id] = (
                "нет slide_id — некуда адресовать действие"
                if issue.slide_id is None
                else "обработчик не смог построить валидное действие из данных issue"
            )
            continue
        if isinstance(action, list):
            actions.extend(action)
        else:
            actions.append(action)
        planned.append(issue.id)

    # детерминированный порядок: blocker → error → warning → info,
    # стабильно по входному порядку внутри severity
    sev_of = {i.id: _SEVERITY_RANK.get(i.severity, 9) for i in issues}
    actions.sort(key=lambda a: sev_of.get(a.issue_ids[0], 9) if a.issue_ids else 9)

    report = RepairPlanReport(
        actions=actions, planned_issue_ids=planned, unresolved=unresolved
    )
    logger.info(
        "repair plan: %d issues -> %d actions, %d unresolved (%.0f%% покрытие)",
        len(issues),
        len(actions),
        len(unresolved),
        report.coverage * 100,
    )
    return report


def plan_repairs(issues: list[AuditIssue]) -> list[RepairAction]:
    """Только предлагает типизированные действия — мутации .pptx нет."""
    return plan_repairs_with_report(issues).actions
