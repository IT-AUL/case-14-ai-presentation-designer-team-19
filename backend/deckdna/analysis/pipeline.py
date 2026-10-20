"""Сквозной анализ датасетов пака (ADR-033 §2–§5).

``analyze_dataset`` — профиль → представления → задача → выбор графика →
факты. ``materialize`` кладёт результат в пак при загрузке: по графику
на представление (``pack.charts``, тип — выбор без брифа), таблицу для
справки и выводы (``pack.insights``). ``analyze_pack`` пересчитывает
задачу и выбор с намерением брифа на этапе плана — дёшево и
детерминированно."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from deckdna.analysis.insights import cross_table_insights, missing_insights, view_insights
from deckdna.analysis.profile import TableProfile, profile_dataset
from deckdna.analysis.ranking import rank_view_insights
from deckdna.analysis.recommend import Recommendation, explain_ru, recommend
from deckdna.analysis.table_columns import column_note
from deckdna.analysis.task import TaskResult, score_tasks
from deckdna.analysis.values import fmt_number
from deckdna.analysis.views import (
    DISTRIBUTION,
    REFERENCE,
    REFERENCE_PAGE,
    REFERENCE_TOP,
    RELATIONSHIP,
    SERIES,
    DataView,
    build_views,
)
from deckdna.contracts.content_pack import (
    Chart,
    ChartSeries,
    ContentPack,
    SourceRef,
    Table,
)
from deckdna.contracts.dataset import Dataset, Insight


@dataclass
class ViewAnalysis:
    view: DataView
    task: TaskResult
    rec: Recommendation
    insights: list[Insight]

    @property
    def strength(self) -> float:
        best = max((i.strength for i in self.insights), default=0.0)
        return round(best + 0.1 * self.task.scores[self.task.task], 3)

    @property
    def headline(self) -> str:
        top = max(self.insights, key=lambda i: i.strength, default=None)
        return (top.headline or top.fact) if top else self.view.title

    @property
    def why(self) -> str:
        return explain_ru(self.view, self.rec, self.task)


@dataclass
class DatasetAnalysis:
    dataset: Dataset
    profile: TableProfile
    views: list[ViewAnalysis]
    extra_insights: list[Insight] = field(default_factory=list)
    fact_ledger: Any = None

    @property
    def primary(self) -> ViewAnalysis:
        return self.views[0]

    @property
    def insights(self) -> list[Insight]:
        return [i for v in self.views for i in v.insights] + self.extra_insights


def intent_text_of(dataset: Dataset, brief_text: str | None = None) -> str:
    return " ".join(t for t in (brief_text, dataset.context_text, dataset.title) if t)


def analyze_dataset(dataset: Dataset, brief_text: str | None = None) -> DatasetAnalysis:
    prof = profile_dataset(dataset)
    intent = intent_text_of(dataset, brief_text)
    analyses: list[ViewAnalysis] = []
    for view in build_views(dataset, prof):
        if view.shape == REFERENCE and len(view.sample_headers) < len(dataset.columns):
            view.notes.append(column_note(len(view.sample_headers), len(dataset.columns)))
        task = score_tasks(view, intent)
        ranked = rank_view_insights(
            view_insights(view),
            view,
            task=task,
            intent_text=intent,
        )
        analyses.append(ViewAnalysis(view, task, recommend(view, task), ranked))
    # первым — представление, которое точнее отвечает намерению, затем сильнейшее
    analyses.sort(
        key=lambda a: (
            -(1 if a.task.intent and a.task.task == a.task.intent else 0),
            not a.view.primary_hint,
            a.view.shape == REFERENCE and not a.view.primary_hint,
            # производные представления (доли от суммы) — после основных
            a.view.derived,
            # ряд по времени — главный рассказ датасета с осью времени
            -(a.strength + (0.2 if a.view.shape == SERIES else 0.0)),
        )
    )
    from deckdna.analysis.fact_ledger import build_dataset_fact_ledger

    src = dataset.source_ref
    ref = " · ".join(x for x in (dataset.file_name or src.artifact_id, src.sheet) if x)
    ledger = build_dataset_fact_ledger(prof, [va.view for va in analyses], dataset_version="v1")
    return DatasetAnalysis(
        dataset,
        prof,
        analyses,
        missing_insights(dataset.id, prof, ref),
        fact_ledger=ledger,
    )


def source_note(dataset: Dataset, view: DataView | None = None) -> str:
    src = dataset.source_ref
    where = ", ".join(x for x in (dataset.file_name or src.artifact_id, src.sheet) if x)
    extra = []
    if view is not None and view.aggregation:
        extra.append(view.aggregation)
    if view is not None:
        extra.extend(view.notes)  # пропуски и сноски — под графиком, не пунктом
    tail = "".join(f"; {e}" for e in extra)
    return f"Источник: {where}{tail}"


def _histogram(values: list[float]) -> tuple[list[str], list[float]]:
    """Бины по правилу Фридмана–Диакониса, 5–20 бинов."""
    values = sorted(values)
    n = len(values)
    lo, hi = values[0], values[-1]
    if hi == lo:
        return [fmt_number(lo)], [float(n)]
    q = [values[int(n * 0.25)], values[int(n * 0.75)]]
    iqr = q[1] - q[0]
    width = 2 * iqr / (n ** (1 / 3)) if iqr > 0 else (hi - lo) / 10
    bins = int(math.ceil((hi - lo) / width)) if width > 0 else 10
    bins = max(5, min(20, bins))
    width = (hi - lo) / bins
    counts = [0.0] * bins
    for v in values:
        k = min(int((v - lo) / width), bins - 1)
        counts[k] += 1
    labels = [
        f"{fmt_number(lo + k * width)}–{fmt_number(lo + (k + 1) * width)}" for k in range(bins)
    ]
    return labels, counts


def view_chart(dataset: Dataset, va: ViewAnalysis) -> Chart | None:
    view = va.view
    chart_type = va.rec.chosen.chart_type
    src = SourceRef(
        artifact_id=dataset.source_ref.artifact_id,
        sheet=dataset.source_ref.sheet,
        range=view.id,
    )
    common = {
        "id": f"chart-{view.id}",
        "source_ref": src,
        "chart_type": chart_type,
        "title": view.title,
        "unit": view.unit,
        "source_note": source_note(dataset, view),
    }
    if view.shape == REFERENCE or chart_type == "table":
        if view.shape == REFERENCE:
            return None
    if view.shape == DISTRIBUTION:
        labels, counts = _histogram(view.raw_values)
        return Chart(
            categories=labels,
            series=[ChartSeries(name="Число значений", values=counts)],
            value_axis_title="Число значений",
            category_axis_title=_axis(view.category_label, view.unit),
            **common,
        )
    if view.shape == RELATIONSHIP:
        return Chart(
            categories=[fmt_number(x) for x in view.x_values],
            series=[ChartSeries(name=view.metric.name, values=view.metric.values)],
            value_axis_title=_axis(view.metric.name, view.metric.unit),
            category_axis_title=_axis(view.x_label or "", None),
            **common,
        )
    factor, unit = _scale(view)
    if unit != view.unit:
        common["unit"] = unit
    return Chart(
        categories=list(view.categories),
        series=[
            ChartSeries(
                name=s.name, values=[None if v is None else round(v / factor, 3) for v in s.values]
            )
            for s in view.series
        ],
        value_axis_title=_axis(view.metric.name if len(view.series) == 1 else "", unit),
        category_axis_title=view.category_label or None,
        **common,
    )


def _scale(view: DataView) -> tuple[float, str | None]:
    """Крупные суммы на оси — в тысячах или миллионах: «16,9» с подписью
    «млн ₽» читается, «16 900 000» — нет. Единица с масштабом в шапке
    («млн ₽») не масштабируется повторно."""
    unit = view.unit
    if unit == "%" or (unit and unit.split()[0] in ("тыс.", "млн", "млрд")):
        return 1.0, unit
    values = [abs(v) for s in view.series for v in s.values if v is not None]
    top = max(values, default=0)
    for limit, word in ((1e9, "млрд"), (1e6, "млн"), (1e4, "тыс.")):
        if top >= limit:
            return limit if word != "тыс." else 1e3, f"{word} {unit}" if unit else word
    return 1.0, unit


def _axis(name: str, unit: str | None) -> str | None:
    if not name and not unit:
        return None
    if unit and name:
        return f"{name}, {unit}"
    return name or unit


def view_table(dataset: Dataset, va: ViewAnalysis) -> Table:
    view = va.view
    src = SourceRef(
        artifact_id=dataset.source_ref.artifact_id, sheet=dataset.source_ref.sheet, range=view.id
    )
    if view.shape == REFERENCE or not view.series:
        rows = [list(r) for r in view.sample_rows]
        shown = rows[:REFERENCE_TOP] if view.primary_hint else rows[:8]
        if view.primary_hint and view.source_rows > len(shown):
            shown = shown + _reference_summary_rows(dataset, view, shown)
        return Table(
            id=f"tbl-{view.id}",
            title=view.title,
            headers=view.sample_headers,
            rows=shown,
            source_ref=src,
        )
    if view.shape == DISTRIBUTION:
        labels, counts = _histogram(view.raw_values)
        return Table(
            id=f"tbl-{view.id}",
            title=view.title,
            headers=[view.category_label, "Число значений"],
            rows=[[lbl, c] for lbl, c in zip(labels, counts, strict=False)],
            source_ref=src,
        )
    if view.shape == RELATIONSHIP:
        return Table(
            id=f"tbl-{view.id}",
            title=view.title,
            headers=[view.x_label or "X", view.metric.name],
            rows=[[x, y] for x, y in zip(view.x_values, view.metric.values, strict=False)][:40],
            source_ref=src,
        )
    headers = [view.category_label or "", *(s.name for s in view.series)]
    rows = [[cat, *(s.values[i] for s in view.series)] for i, cat in enumerate(view.categories)]
    if len(rows) > 15:
        # длинный разрез на слайде не читается: топ-14 строк + сводка
        # «Прочие» с суммами по числовым колонкам и строкой «Итого»
        from deckdna.analysis.values import plural

        head, tail = rows[:14], rows[14:]
        n_rest = len(tail)
        summary = [f"Прочие ({n_rest} {plural(n_rest, ('позиция', 'позиции', 'позиций'))})"]
        total = ["Итого"]
        for si, s in enumerate(view.series):
            rest_vals = [r[si + 1] for r in tail if isinstance(r[si + 1], (int, float))]
            all_vals = [r[si + 1] for r in rows if isinstance(r[si + 1], (int, float))]
            additive = not _NON_ADDITIVE.search(str(s.name))
            summary.append(round(sum(rest_vals), 2) if rest_vals and additive else "")
            total.append(round(sum(all_vals), 2) if all_vals and additive else "")
        rows = head + [summary, total]
    units = {s.name: s.unit for s in view.series if s.unit}
    return Table(
        id=f"tbl-{view.id}",
        title=view.title,
        headers=headers,
        rows=rows,
        units=units or None,
        source_ref=src,
    )


# Неаддитивные метрики: сумма «Цена», «Ставка, %» или «Срок поставки, дн.»
# по справочнику — бессмысленное число; такие колонки в «Итого» не суммируются,
# тогда как «Остаток, шт.» является аддитивным запасом и суммируется.
_NON_ADDITIVE = re.compile(
    r"(?:цен|чек|ставк|прайс|price|тариф|оклад|вознагражд|средн|удельн|доля|"
    r"%|на\s+чел|per\b|(?:за|/)\s*шт\.?$|срок|дн\.?$|дней|балл|рейтинг|nps|возраст)",
    re.IGNORECASE,
)


def _reference_summary_rows(dataset: Dataset, view, shown: list[list]) -> list[list]:
    """Свёртка справочника после «топ-N»: строка «Прочие» с числом
    позиций и «Итого» с суммами по аддитивным (деньги/числа) колонкам.
    Суммы считаются по исходному датасету — образец покрывает только
    верхние строки."""
    from deckdna.analysis.profile import MONEY, NUMBER, profile_dataset
    from deckdna.analysis.values import plural

    n_rest = view.source_rows - len(shown)
    width = len(view.sample_headers)
    rest_row = [
        f"Прочие ({n_rest} {plural(n_rest, ('позиция', 'позиции', 'позиций'))})",
        *("" for _ in range(width - 1)),
    ]
    kinds = {c.name: c.kind for c in profile_dataset(dataset).columns}
    total_row = ["Итого"]
    for header in view.sample_headers[1:]:
        col = next((c for c in dataset.columns if c.name == header), None)
        values = [
            v
            for v in (col.values if col else [])
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
        total_row.append(
            round(sum(values), 2)
            if values
            and kinds.get(header) in (MONEY, NUMBER)
            and not _NON_ADDITIVE.search(str(header))
            else ""
        )
    return [rest_row, total_row]


def reference_pages(dataset: Dataset, va: ViewAnalysis) -> list[Table]:
    """Продолжения справочника после «топ-N»: до 3 страниц (ADR-033 §7);
    остальное — в HTML-экспорте и приложении."""
    view = va.view
    if view.shape != REFERENCE or not view.primary_hint:
        return []
    rest = view.sample_rows[REFERENCE_TOP:]
    pages = []
    for k in range(0, len(rest), REFERENCE_PAGE):
        pages.append(
            Table(
                id=f"tbl-{view.id}-p{k // REFERENCE_PAGE + 2}",
                title=f"{view.title}: продолжение",
                headers=view.sample_headers,
                rows=[list(r) for r in rest[k : k + REFERENCE_PAGE]],
                source_ref=SourceRef(
                    artifact_id=dataset.source_ref.artifact_id,
                    sheet=dataset.source_ref.sheet,
                    range=f"{view.id}#p{k // REFERENCE_PAGE + 2}",
                ),
            )
        )
    return pages


def materialize(pack: ContentPack) -> ContentPack:
    """Графики, таблицы и выводы датасетов пака (без брифа)."""
    if not pack.datasets:
        return pack
    charts = {c.id: c for c in pack.charts}
    tables = {t.id: t for t in pack.tables}
    insights = {i.id: i for i in pack.insights}
    cross_items: list[tuple[str, str, list[DataView]]] = []
    for dataset in pack.datasets:
        analysis = analyze_dataset(dataset)
        cross_items.append((dataset.id, dataset.title, [va.view for va in analysis.views]))
        for va in analysis.views:
            chart = view_chart(dataset, va)
            if chart is not None:
                charts.setdefault(chart.id, chart)
            table = view_table(dataset, va)
            tables.setdefault(table.id, table)
            for page in reference_pages(dataset, va):
                tables.setdefault(page.id, page)
        for ins in analysis.insights:
            insights.setdefault(ins.id, ins)
    for cross_ins in cross_table_insights(cross_items):
        insights.setdefault(cross_ins.id, cross_ins)
    return pack.model_copy(
        update={
            "charts": list(charts.values()),
            "tables": list(tables.values()),
            "insights": list(insights.values()),
        }
    )


def analyze_pack(pack: ContentPack, brief_text: str | None = None) -> list[DatasetAnalysis]:
    """Анализ всех датасетов с намерением брифа; выводы берутся из пака
    (там их подтверждения и правки пользователя)."""
    stored = {i.id: i for i in pack.insights}
    out: list[DatasetAnalysis] = []
    cross_items: list[tuple[str, str, list[DataView]]] = []
    for dataset in pack.datasets:
        analysis = analyze_dataset(dataset, brief_text)
        intent = intent_text_of(dataset, brief_text)
        for va in analysis.views:
            restored = [stored.get(i.id, i) for i in va.insights]
            va.insights = rank_view_insights(
                restored,
                va.view,
                task=va.task,
                intent_text=intent,
            )
        analysis.extra_insights = [stored.get(i.id, i) for i in analysis.extra_insights]
        cross_items.append((dataset.id, dataset.title, [va.view for va in analysis.views]))
        out.append(analysis)
    if len(out) >= 2:
        by_ds = {a.dataset.id: a for a in out}
        for cross_ins in cross_table_insights(cross_items):
            target = by_ds.get(cross_ins.dataset_id, out[0])
            restored_cross = stored.get(cross_ins.id, cross_ins)
            if all(existing.id != restored_cross.id for existing in target.extra_insights):
                target.extra_insights.append(restored_cross)
    return out


__all__ = [
    "DatasetAnalysis",
    "ViewAnalysis",
    "analyze_dataset",
    "analyze_pack",
    "materialize",
    "source_note",
    "view_chart",
    "view_table",
]
