"""Аналитическая задача представления (ADR-033 §3).

Баллы задач считаются по профилю. Намерение (бриф, текст вокруг таблицы,
ответы помощника) добавляет вес по словарю маркеров, но не делает
возможным невозможное: доли без признака структуры остаются нулём."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from deckdna.analysis.profile import PERCENT
from deckdna.analysis.values import plural
from deckdna.analysis.views import (
    CATEGORICAL,
    DISTRIBUTION,
    KPI,
    REFERENCE,
    RELATIONSHIP,
    SERIES,
    DataView,
)

TREND, COMPARISON, COMPOSITION, DISTRIB, RELATION, REFER, KPI_TASK = (
    "trend",
    "comparison",
    "composition",
    "distribution",
    "relationship",
    "reference",
    "kpi",
)
TASKS = (TREND, COMPARISON, COMPOSITION, DISTRIB, RELATION, REFER, KPI_TASK)
TASK_RU = {
    TREND: "динамика",
    COMPARISON: "сравнение",
    COMPOSITION: "доли",
    DISTRIB: "распределение",
    RELATION: "взаимосвязь",
    REFER: "справка",
    KPI_TASK: "ключевые числа",
}
# винительный падеж для «сказано про …»: «про динамику», «про справку»
TASK_RU_ACC = {
    TREND: "динамику",
    COMPARISON: "сравнение",
    COMPOSITION: "доли",
    DISTRIB: "распределение",
    RELATION: "взаимосвязь",
    REFER: "справку",
    KPI_TASK: "ключевые числа",
}

INTENT_MARKERS: dict[str, tuple[str, ...]] = {
    TREND: (
        "рост",
        "вырос",
        "увелич",
        "вдвое",
        "растёт",
        "растет",
        "динамик",
        "тренд",
        "измен",
        "за год",
        "по месяцам",
        "growth",
        "trend",
        "over time",
        "снижен",
        "падени",
    ),
    COMPARISON: (
        "сравн",
        "рейтинг",
        "лидер",
        "топ",
        "лучш",
        "худш",
        "больше всего",
        "compare",
        "ranking",
        "leader",
        "top",
    ),
    COMPOSITION: ("доля", "доли", "структур", "состав", "вклад", "share", "composition", "mix"),
    DISTRIB: ("разброс", "распредел", "типичн", "медиан", "чек", "distribution", "spread"),
    RELATION: ("связь", "зависи", "влия", "коррел", "relationship", "correlat", "depend"),
}

_BASE = {
    SERIES: {
        TREND: 0.9,
        COMPARISON: 0.3,
        COMPOSITION: 0.0,
        DISTRIB: 0.0,
        RELATION: 0.0,
        REFER: 0.1,
        KPI_TASK: 0.0,
    },
    CATEGORICAL: {
        TREND: 0.0,
        COMPARISON: 0.8,
        COMPOSITION: 0.0,
        DISTRIB: 0.0,
        RELATION: 0.0,
        REFER: 0.1,
        KPI_TASK: 0.0,
    },
    DISTRIBUTION: {DISTRIB: 0.9, REFER: 0.1},
    RELATIONSHIP: {RELATION: 0.9, COMPARISON: 0.1, REFER: 0.1},
    KPI: {KPI_TASK: 0.95, COMPARISON: 0.3, REFER: 0.1},
    REFERENCE: {REFER: 1.0},
}


@dataclass
class TaskResult:
    task: str
    scores: dict[str, float]
    intent: str | None = None
    reasons: list[str] = field(default_factory=list)

    @property
    def task_ru(self) -> str:
        return TASK_RU[self.task]


def intent_of(text: str | None) -> str | None:
    """Задача по маркерам в тексте; ``None`` — маркеров нет."""
    if not text:
        return None
    low = text.casefold()
    hits = {
        task: sum(len(re.findall(re.escape(m), low)) for m in markers)
        for task, markers in INTENT_MARKERS.items()
    }
    best = max(hits, key=hits.get)
    return best if hits[best] else None


def score_tasks(view: DataView, intent_text: str | None = None) -> TaskResult:
    scores = {t: 0.0 for t in TASKS}
    scores.update(_BASE.get(view.shape, {}))
    reasons: list[str] = []
    if view.shape == CATEGORICAL and view.structure:
        scores[COMPOSITION] = 0.55
    # значения уже даны в долях и складываются в целое (~100%): задача
    # «доли», а ранжирование одних и тех же чисел второй раз — вторично
    if (
        view.shape == CATEGORICAL
        and view.metric is not None
        and view.metric.kind == PERCENT
        and 97 <= sum(v for v in view.metric.values if v is not None) <= 103
    ):
        scores[COMPOSITION] = 0.9
        scores[COMPARISON] = min(scores[COMPARISON], 0.3)
    if view.shape == SERIES and len(view.series) >= 2 and view.structure:
        scores[COMPOSITION] = 0.4
    intent = intent_of(intent_text)
    if intent and scores.get(intent, 0) > 0:
        scores[intent] += 0.35
        reasons.append(f"в задаче сказано про {TASK_RU_ACC[intent]}")
    elif intent:
        reasons.append(f"в задаче сказано про {TASK_RU_ACC[intent]}, но данные этого не позволяют")
    task = max(TASKS, key=lambda t: scores[t])
    if view.shape == SERIES and task == TREND:
        reasons.insert(
            0, f"{view.n_points} {plural(view.n_points, ('точка', 'точки', 'точек'))} по времени"
        )
    elif view.shape == CATEGORICAL and task in (COMPARISON, COMPOSITION):
        n = view.n_points
        reasons.insert(
            0,
            f"{n} {plural(n, ('категория', 'категории', 'категорий'))} "
            f"«{view.category_label}» и одна метрика",
        )
    return TaskResult(task=task, scores=scores, intent=intent, reasons=reasons)


__all__ = [
    "COMPARISON",
    "COMPOSITION",
    "DISTRIB",
    "INTENT_MARKERS",
    "KPI_TASK",
    "REFER",
    "RELATION",
    "TASKS",
    "TASK_RU",
    "TASK_RU_ACC",
    "TREND",
    "TaskResult",
    "intent_of",
    "score_tasks",
]
