"""Выбор визуализации (ADR-033 §4): ранжированные 2–3 варианта с
причинами и отклонённые типы с причинами.

На вход — только представление данных и задача. Шаблона и формата файла
здесь нет, поэтому один датасет на любом шаблоне и в любом формате даёт
один и тот же тип графика (проверяется тестами инвариантности)."""

from __future__ import annotations

from dataclasses import dataclass

from deckdna.analysis.task import (
    COMPARISON,
    COMPOSITION,
    DISTRIB,
    KPI_TASK,
    REFER,
    RELATION,
    TREND,
    TaskResult,
)
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
from deckdna.contracts.dataset import RejectedViz, VizRecommendation

CHART_TYPES = (
    "line",
    "bar",
    "bar_horizontal",
    "bar_stacked",
    "bar_stacked_100",
    "waterfall",
    "histogram",
    "pie",
    "doughnut",
    "scatter",
    "kpi",
    "table",
)
CHART_RU = {
    "line": "линия",
    "bar": "столбцы",
    "bar_horizontal": "горизонтальные столбцы",
    "bar_stacked": "накопительные столбцы",
    "bar_stacked_100": "столбцы 100%",
    "waterfall": "водопад",
    "histogram": "гистограмма",
    "pie": "круговая",
    "doughnut": "кольцевая",
    "scatter": "точечная",
    "kpi": "карточки с числами",
    "table": "таблица",
}
# насколько тип решает задачу
FIT: dict[str, dict[str, float]] = {
    TREND: {"line": 1.0, "bar": 0.7, "bar_stacked": 0.5, "table": 0.3},
    COMPARISON: {"bar_horizontal": 0.95, "bar": 0.9, "table": 0.4, "line": 0.1},
    COMPOSITION: {
        "pie": 0.9,
        "doughnut": 0.85,
        "bar_stacked_100": 0.8,
        "bar_horizontal": 0.6,
        "bar_stacked": 0.6,
        "table": 0.3,
    },
    DISTRIB: {"histogram": 1.0, "table": 0.2},
    RELATION: {"scatter": 1.0, "table": 0.3},
    KPI_TASK: {"kpi": 1.0, "bar": 0.4, "table": 0.3},
    REFER: {"table": 1.0},
}
MAX_PIE_SLICES = 6
MAX_BAR_CATEGORIES = 12


@dataclass
class Recommendation:
    ranked: list[VizRecommendation]
    rejected: list[RejectedViz]

    @property
    def chosen(self) -> VizRecommendation:
        return self.ranked[0]

    @property
    def alternatives(self) -> list[VizRecommendation]:
        return self.ranked[1:3]


def _grain_word(view: DataView) -> str:
    cats = view.categories
    if not cats:
        return "периодам"
    sample = cats[0]
    if "кв." in sample:
        return "кварталам"
    if sample.startswith("нед."):
        return "неделям"
    if sample.isdigit():
        return "годам"
    return "месяцам"


def _applicability(view: DataView, chart: str) -> tuple[bool, list[str]]:
    """(можно ли, причины за или против)."""
    n = view.n_points
    series = len(view.series)
    values = [v for s in view.series for v in s.values if v is not None]
    negatives = any(v < 0 for v in values)
    longest = max((len(c) for c in view.categories), default=0)
    if chart == "table":
        return True, ["точные значения важнее формы"]
    if view.shape == REFERENCE:
        return False, [view.reason or "нет главной метрики для графика"]
    if chart == "line":
        if view.shape != SERIES:
            return False, ["нет оси времени"]
        if n < 3:
            return False, ["меньше трёх точек"]
        reasons = [f"{n} {plural(n, ('точка', 'точки', 'точек'))} по {_grain_word(view)}"]
        reasons.append(
            "одна метрика"
            if series == 1
            else f"{series} {plural(series, ('ряд', 'ряда', 'рядов'))} в одних единицах"
        )
        gaps = sum(v is None for v in view.series[0].values)
        if gaps:
            reasons.append("пропуски видны разрывом линии")
        return True, reasons
    if chart == "bar":
        if view.shape not in (SERIES, CATEGORICAL, KPI):
            return False, ["нет категорий или периодов для столбцов"]
        if n > MAX_BAR_CATEGORIES * 2:
            return False, [f"{n} столбцов не читаются на слайде"]
        if view.shape == SERIES:
            return True, [f"{n} {plural(n, ('период', 'периода', 'периодов'))}, каждый столбцом"]
        return True, [f"{n} {plural(n, ('категория', 'категории', 'категорий'))} одной метрики"]
    if chart == "bar_horizontal":
        if view.shape != CATEGORICAL:
            return False, ["ранжирование нужно для категорий, а не для времени"]
        reasons = ["категории упорядочены по убыванию"]
        if longest > 12:
            reasons.append("длинные подписи читаются по горизонтали")
        if n > MAX_BAR_CATEGORIES:
            reasons.append(f"показаны топ-10 из {n}, остальные одной строкой")
        return True, reasons
    if chart in ("pie", "doughnut"):
        if view.shape != CATEGORICAL:
            return False, ["доли показывают для категорий, а не для ряда"]
        if not view.structure:
            return False, ["значения не складываются в целое"]
        if negatives:
            return False, ["есть отрицательные значения"]
        if series > 1:
            return False, ["больше одной метрики"]
        vals = sorted((v for v in values if v is not None), reverse=True)
        if n >= 3 and vals and vals[-1] >= vals[0] * 0.95:
            return False, ["доли почти равны — круговая ничего не выделяет"]
        if n > MAX_PIE_SLICES:
            total = sum(vals)
            tail = sum(vals[MAX_PIE_SLICES - 1 :])
            if total > 0 and tail / total > 0.25:
                return False, ["«прочие» дали бы больше четверти круга"]
            return True, [f"{n} долей: топ-5 и «прочие»"]
        return True, [f"{n} {plural(n, ('доля', 'доли', 'долей'))} одного целого"]
    if chart in ("bar_stacked", "bar_stacked_100"):
        if series < 2:
            return False, ["нужно хотя бы два ряда для накопления"]
        if negatives:
            return False, ["есть отрицательные значения"]
        return True, [
            f"{series} составляющих целого по {n} {plural(n, ('точке', 'точкам', 'точкам'))}"
        ]
    if chart == "waterfall":
        if not getattr(view, "bridge", False):
            return False, ["нет структуры «старт → факторы → итог»"]
        return True, ["разложение изменения на факторы, мост сходится"]
    if chart == "histogram":
        if view.shape != DISTRIBUTION:
            return False, ["нужна одна числовая колонка со многими значениями"]
        return True, [f"{n} {plural(n, ('значение', 'значения', 'значений'))} одной метрики"]
    if chart == "scatter":
        if view.shape != RELATIONSHIP:
            return False, ["нужны две метрики на одних объектах"]
        return True, [f"{n} {plural(n, ('пара', 'пары', 'пар'))} значений"]
    if chart == "kpi":
        if view.shape != KPI:
            return False, ["значений больше, чем помещается на карточки"]
        return True, [f"{n} {plural(n, ('ключевое число', 'ключевых числа', 'ключевых чисел'))}"]
    return False, ["тип не поддерживается"]


def recommend(view: DataView, task: TaskResult, *, top: int = 3) -> Recommendation:
    ranked: list[VizRecommendation] = []
    rejected: list[RejectedViz] = []
    for chart in CHART_TYPES:
        ok, reasons = _applicability(view, chart)
        if not ok:
            rejected.append(RejectedViz(chart_type=chart, reasons=reasons))
            continue
        # балл: лучшее «тип × задача» среди возможных задач
        score = max(FIT.get(t, {}).get(chart, 0.0) * s for t, s in task.scores.items())
        if chart == "waterfall" and getattr(view, "bridge", False):
            # мост отклонений показывается только водопадом — это единственный
            # тип, где видны и вклад факторов, и итог одновременно
            score = 1.0
        if score <= 0:
            rejected.append(
                RejectedViz(chart_type=chart, reasons=[f"не решает задачу «{task.task_ru}»"])
            )
            continue
        if chart == task_best(task.task):
            reasons = [*reasons, *task.reasons[1:]]
        # мелкие предпочтения по данным
        if chart == "bar" and view.shape == CATEGORICAL and view.n_points > 6:
            score -= 0.1
        if chart == "bar_horizontal" and view.n_points <= 4:
            score -= 0.1
        if chart in ("pie", "doughnut") and view.n_points > MAX_PIE_SLICES:
            score -= 0.15
        if chart == "doughnut":
            score -= 0.01
        ranked.append(VizRecommendation(chart_type=chart, score=round(score, 3), reasons=reasons))
    ranked.sort(key=lambda r: (-r.score, CHART_TYPES.index(r.chart_type)))
    return Recommendation(
        ranked=ranked[:top],
        rejected=rejected
        + [RejectedViz(chart_type=r.chart_type, reasons=["ниже по баллу"]) for r in ranked[top:]],
    )


def task_best(task: str) -> str:
    fits = FIT.get(task, {})
    return max(fits, key=fits.get) if fits else "table"


def explain_ru(view: DataView, rec: Recommendation, task: TaskResult) -> str:
    """Одно предложение «почему такой график» для карточки плана."""
    chosen = rec.chosen
    why = ", ".join(chosen.reasons[:2])
    return f"{CHART_RU[chosen.chart_type].capitalize()}: {why}; задача: {task.task_ru}."


__all__ = ["CHART_RU", "CHART_TYPES", "Recommendation", "explain_ru", "recommend"]
