"""Подгонка данных под выбранный тип графика (ADR-033 §7).

- круговая или кольцевая: > 6 долей → топ-5 + «Прочие»;
- столбцы по категориям: > 12 → топ-10 + «Остальные (N)» и сноска;
- несколько рядов > 5 → 5 крупнейших;
- доли и гистограмма показывают один ряд.

Тип приходит из плана (выбор кода или правка пользователя), поэтому
подгонка делается при вёрстке, а в паке лежат полные данные."""

from __future__ import annotations

from deckdna.analysis.values import fmt_number
from deckdna.contracts.content_pack import Chart, ChartSeries

MAX_PIE = 6
MAX_BARS = 12
MAX_SERIES = 5


def _top_n(chart: Chart, keep: int, other_label: str) -> tuple[Chart, int]:
    s = chart.series[0]
    pairs = [(c, v) for c, v in zip(chart.categories, s.values, strict=False) if v is not None]
    pairs.sort(key=lambda kv: -kv[1])
    head, tail = pairs[:keep], pairs[keep:]
    if not tail:
        return chart, 0
    rest = sum(v for _, v in tail)
    cats = [c for c, _ in head] + [other_label.format(n=len(tail))]
    vals = [v for _, v in head] + [rest]
    return chart.model_copy(
        update={
            "categories": cats,
            "series": [ChartSeries(name=s.name, values=vals)],
        }
    ), len(tail)


def shape_for_type(chart: Chart, chart_type: str | None) -> Chart:
    if chart.chart_type is None:
        return chart  # график не из датасета: как раньше
    kind = chart_type or chart.chart_type
    if kind == "table":
        kind = chart.chart_type
    chart = chart.model_copy(update={"chart_type": kind})
    if len(chart.series) > MAX_SERIES:
        top = sorted(chart.series, key=lambda s: -sum(v for v in s.values if v is not None))
        chart = chart.model_copy(update={"series": top[:MAX_SERIES]})
    if kind in ("pie", "doughnut"):
        chart = chart.model_copy(update={"series": chart.series[:1]})
        if len(chart.categories) > MAX_PIE:
            chart, _ = _top_n(chart, MAX_PIE - 1, "Прочие")
    elif (
        kind in ("bar", "bar_horizontal")
        and len(chart.series) == 1
        and (len(chart.categories) > MAX_BARS)
        and not _is_time(chart)
    ):
        chart, rest = _top_n(chart, 10, "Остальные ({n})")
        if rest:
            values = chart.series[0].values
            top_min = min((v for v in values[:-1] if v is not None), default=0)
            if values[-1] is not None and values[-1] > top_min:
                # агрегат крупнее хотя бы одной показанной категории:
                # столбец «Остальные» читался бы как топовая позиция —
                # сумма уходит в сноску
                tail_sum = fmt_number(values[-1])
                chart = chart.model_copy(
                    update={
                        "categories": chart.categories[:-1],
                        "series": [chart.series[0].model_copy(update={"values": values[:-1]})],
                    }
                )
                tail_note = f"остальные {rest} — всего {tail_sum}"
            else:
                tail_note = f"остальные {rest} одной строкой"
            note = f"{chart.source_note or ''}; показаны 10 крупнейших, {tail_note}"
            chart = chart.model_copy(update={"source_note": note.lstrip("; ")})
    return chart


def _is_time(chart: Chart) -> bool:
    cats = chart.categories
    return bool(cats) and (any(m in cats[0] for m in ("кв.", "нед.", " 20")) or cats[0].isdigit())


__all__ = ["shape_for_type"]
