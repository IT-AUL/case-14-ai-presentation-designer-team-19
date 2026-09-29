"""Исходы repair по каждой проблеме, стабильный fingerprint и описание
исправления для интерфейса (contract D2 + D8).

Всё здесь — чистые функции над уже существующими типами: repair-план
(``RepairPlanReport``), отчёт исполнителя (``RepairApplyReport``) и
результат повторного аудита. Ничего не мутирует и не открывает .pptx.

Главный принцип — **отчёт совпадает с результатом**. Исход ``fixed``
выдаётся только если проблемы больше нет в повторном аудите (её
fingerprint исчез); «действие применено, а проблема осталась» — это
``failed`` с честной причиной, а не «applied» (contract B6).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from deckdna.audit.catalog import rule_info
from deckdna.audit.issues import AuditIssue
from deckdna.contracts.repair_action import RepairAction
from deckdna.repair.apply import RepairApplyReport
from deckdna.repair.planner import RepairPlanReport, plan_repairs_with_report

FIXED = "fixed"
FAILED = "failed"
SKIPPED = "skipped"
PLANNED = "planned"  # только dry_run


def fingerprint(issue: AuditIssue) -> str:
    """Стабильный между ревизиями идентификатор проблемы.

    Хэш ``rule_code + слайд + shape_ids``. Слайд — по ``slide_id``
    (sldId переживает удаление соседних слайдов), а при его отсутствии
    (контекстные VLM-проверки) — по индексу. ``id`` проблемы после
    повторного аудита меняется, fingerprint — нет.
    """
    slide = issue.slide_id if issue.slide_id is not None else f"i{issue.slide_index}"
    key = "|".join(
        [issue.rule_code, str(slide), ",".join(sorted(issue.shape_ids or []))]
    )
    return hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()[:16]


# --------------------------------------------------------------------------
# Человеческое описание исправления
# --------------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return f"{round((value or 0) * 100)}%"


def describe_action(action: RepairAction, issue: AuditIssue) -> str:
    """Одна строка по-русски: что сделает это действие для этой проблемы."""
    kind = action.action_type.value
    rule = issue.rule_code
    params = action.params
    if kind == "resize_shape":
        if rule == "layout.out_of_bounds":
            return "Вернуть элемент в границы слайда"
        if rule == "template.anchor_position":
            return "Вернуть элемент на место, заданное шаблоном"
        if params.bbox and params.bbox.w and params.bbox.h:
            return (
                "Расширить рамку текста до "
                f"{_pct(params.bbox.w)}×{_pct(params.bbox.h)} слайда"
            )
        return "Подогнать размер рамки под текст"
    if kind == "shorten_text":
        return "Если текст всё ещё не помещается — сократить его с «…»"
    if kind == "recrop_image":
        return "Обрезать картинку до исходных пропорций"
    if kind == "merge_slide":
        if rule == "integrity.empty_slide":
            return "Убрать пустой слайд"
        return "Убрать повторяющийся слайд, оставив оригинал"
    if kind == "remove_placeholder":
        return "Удалить заглушку шаблона"
    if kind == "move_shape":
        if rule == "layout.edge_margin":
            return "Отодвинуть элемент от края слайда"
        return "Сдвинуть элемент внутрь слайда"
    if kind == "map_font":
        return "Заменить лишние шрифты на основной шрифт шаблона"
    if kind == "map_color":
        if params.scope is not None and params.scope.value == "text":
            return "Подобрать читаемый цвет текста из палитры шаблона"
        return "Заменить цвета на ближайшие из палитры шаблона"
    return kind


def fix_preview(issue: AuditIssue) -> dict[str, Any] | None:
    """Что будет сделано для этой проблемы — или None, если не чинится.

    Считается тем же планировщиком, что применит исправление, поэтому
    предпросмотр не может разойтись с реальным действием.
    """
    if not issue.repairable:
        return None
    report = plan_repairs_with_report([issue])
    if not report.actions:
        return None
    info = rule_info(issue.rule_code)
    return {
        "title_ru": info.fix_title_ru if info else None,
        "description_ru": "; ".join(
            describe_action(a, issue) for a in report.actions
        ),
        "actions": [a.action_type.value for a in report.actions],
    }


# --------------------------------------------------------------------------
# Исходы
# --------------------------------------------------------------------------


@dataclass
class IssueOutcome:
    issue_id: str
    status: str
    action: str | None = None
    summary: str | None = None
    reason: str | None = None
    fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "status": self.status,
            "action": self.action,
            "summary": self.summary,
            "reason": self.reason,
            "fingerprint": self.fingerprint,
        }


def _actions_for(issue_id: str, actions: Sequence[RepairAction]) -> list[int]:
    return [i for i, a in enumerate(actions) if issue_id in (a.issue_ids or [])]


def plan_outcomes(
    selected: Iterable[AuditIssue], plan: RepairPlanReport
) -> list[IssueOutcome]:
    """Сухой прогон: что было бы сделано, без применения (dry_run)."""
    out: list[IssueOutcome] = []
    for issue in selected:
        fp = fingerprint(issue)
        if issue.id in plan.unresolved:
            out.append(
                IssueOutcome(
                    issue.id, SKIPPED, reason=plan.unresolved[issue.id], fingerprint=fp
                )
            )
            continue
        idxs = _actions_for(issue.id, plan.actions)
        acts = [plan.actions[i] for i in idxs]
        out.append(
            IssueOutcome(
                issue.id,
                PLANNED,
                action="+".join(a.action_type.value for a in acts) or None,
                summary="; ".join(describe_action(a, issue) for a in acts) or None,
                fingerprint=fp,
            )
        )
    return out


def build_outcomes(
    selected: Iterable[AuditIssue],
    plan: RepairPlanReport,
    applied: RepairApplyReport,
    fresh: Iterable[AuditIssue],
) -> list[IssueOutcome]:
    """Исход по каждой выбранной проблеме — по итогам повторного аудита.

    ``fixed`` — хотя бы одно действие применено и fingerprint проблемы
    исчез из свежего аудита.
    ``failed`` — исполнитель упал/отказал, либо действие применено, но
    проблема осталась. ``skipped`` — для проблемы нет действия или его
    не запускали (защищённый слайд, нет обработчика).
    """
    still_open = {fingerprint(i) for i in fresh}
    out: list[IssueOutcome] = []
    for issue in selected:
        fp = fingerprint(issue)
        if issue.id in plan.unresolved:
            out.append(
                IssueOutcome(
                    issue.id, SKIPPED, reason=plan.unresolved[issue.id], fingerprint=fp
                )
            )
            continue
        idxs = _actions_for(issue.id, plan.actions)
        results = [applied.results[i] for i in idxs if i < len(applied.results)]
        acts = [plan.actions[i] for i in idxs]
        did = [r for r in results if r.status == "applied"]
        summary = "; ".join(describe_action(a, issue) for a in acts)
        kinds = "+".join(dict.fromkeys(r.action_type for r in did)) or None

        # fixed = действие реально применено И проблема исчезла из повторного
        # аудита; «исчезла, но ничего не применялось» — не наша заслуга
        if fp not in still_open and did:
            out.append(
                IssueOutcome(
                    issue.id, FIXED, action=kinds, summary=summary or None, fingerprint=fp
                )
            )
            continue
        failed = next((r for r in results if r.status == "failed"), None)
        if failed is not None:
            reason = failed.detail or "Исполнитель не смог применить действие"
        elif did:
            reason = "Действие применено, но повторный аудит всё ещё находит проблему"
        else:
            detail = next((r.detail for r in results if r.detail), "")
            reason = detail or "Действие не было выполнено"
        status = FAILED if (failed is not None or did) else SKIPPED
        out.append(
            IssueOutcome(
                issue.id,
                status,
                action=kinds or (acts[0].action_type.value if acts else None),
                reason=reason,
                fingerprint=fp,
            )
        )
    return out


def count_outcomes(
    outcomes: Iterable[IssueOutcome], plan: RepairPlanReport
) -> dict[str, int]:
    """Счётчики job по итогам: applied — только реально исправленное
    (проблема исчезла из повторного аудита); unresolved — у планировщика
    нет действия; skipped — действие было, но не выполнялось; failed —
    выполнялось, но не помогло."""
    counts = {"applied": 0, "failed": 0, "skipped": 0, "unresolved": 0}
    for o in outcomes:
        if o.status == FIXED:
            counts["applied"] += 1
        elif o.status == FAILED:
            counts["failed"] += 1
        elif o.issue_id in plan.unresolved:
            counts["unresolved"] += 1
        else:
            counts["skipped"] += 1
    return counts
