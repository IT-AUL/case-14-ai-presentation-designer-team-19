"""Представления датасета: что именно показывает график (ADR-033 §2–§3, §7).

Сырые транзакции («покупки») графиком не показать: их нужно свести.
Код строит кандидатов детерминированно:

- **ряд** — метрика по времени (транзакции суммируются по месяцам, при
  > 60 точках — по кварталам, короткий период — по неделям);
- **разрез** — метрика по категории (сумма по региону, каналу…);
- **распределение** — одна числовая колонка, много строк;
- **взаимосвязь** — две метрики на одних объектах;
- **KPI** — 1–4 значения;
- **справка** — нетипизируемое или много колонок без главной метрики.

Каждое представление — маленькая таблица, по которой дальше считаются
задача, выбор графика и выводы. Шаблон и формат файла сюда не попадают."""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, timedelta

from deckdna.analysis.profile import (
    CATEGORY,
    IDENT,
    MONEY,
    NUMBER,
    PERCENT,
    TEXT,
    ColumnProfile,
    TableProfile,
    profile_dataset,
)
from deckdna.analysis.table_columns import column_indices
from deckdna.analysis.values import ParsedPeriod, fmt_number, period, plural
from deckdna.contracts.dataset import Dataset

SERIES, CATEGORICAL, DISTRIBUTION, RELATIONSHIP, KPI, REFERENCE = (
    "series",
    "categorical",
    "distribution",
    "relationship",
    "kpi",
    "reference",
)
_MAX_SERIES_POINTS = 60
_MAX_CATEGORIES = 50
_GENERIC_MONEY = re.compile(
    r"^(сумма|amount|value|значение|стоимость|итого|total|price|цена)$", re.I
)
_MISSING_LABEL = "Не указано"


@dataclass
class ViewSeries:
    name: str
    values: list[float | None]
    unit: str | None = None
    kind: str = NUMBER


@dataclass
class DataView:
    id: str
    dataset_id: str
    shape: str
    title: str
    categories: list[str]
    series: list[ViewSeries]
    category_label: str = ""
    category_kind: str = "category"  # time | category | none
    unit: str | None = None
    aggregation: str | None = None
    structure: bool = False
    # точки исходных значений для распределения и рассеяния
    raw_values: list[float] = field(default_factory=list)
    x_values: list[float] = field(default_factory=list)
    x_label: str | None = None
    notes: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    # сколько исходных строк стоит за представлением
    source_rows: int = 0
    # строки-образец для справки и нетипизируемых данных
    sample_headers: list[str] = field(default_factory=list)
    sample_rows: list[list[str]] = field(default_factory=list)
    reason: str | None = None
    # справочник сущностей: главным показываем таблицу «топ-N + сводка»
    primary_hint: bool = False
    # единица каждого KPI («млн ₽», «тыс.», None)
    kpi_units: list[str | None] = field(default_factory=list)
    # производное представление того же среза (доли от суммы): значимо
    # как отдельный слайд, но главным представлением не становится
    derived: bool = False
    # мост отклонений «старт → факторы → итог»: категории в исходном
    # порядке, водопад — выбранный тип
    bridge: bool = False

    @property
    def n_points(self) -> int:
        if self.shape == DISTRIBUTION:
            return len(self.raw_values)
        if self.shape == RELATIONSHIP:
            return len(self.x_values)
        return len(self.categories)

    @property
    def metric(self) -> ViewSeries | None:
        return self.series[0] if self.series else None


def _metric_name(col: ColumnProfile, aggregated: bool) -> str:
    label = col.label
    if aggregated and col.kind == MONEY and _GENERIC_MONEY.match(label.strip()):
        return "Выручка"
    return label


def _ref(dataset: Dataset, column: str) -> str:
    src = dataset.source_ref
    where = " · ".join(x for x in (dataset.file_name or src.artifact_id, src.sheet) if x)
    return f"{where} · {column}, строки 2–{dataset.row_count + 1}"


def _grain_for(periods: list[ParsedPeriod], grain: str) -> str:
    keys = sorted(p.key for p in periods)
    if grain == "day":
        first, last = date(*keys[0]), date(*keys[-1])
        span = (last - first).days
        if span <= 92:
            return "week"
        if span / 30.4 > _MAX_SERIES_POINTS:
            return "quarter"
        return "month"
    if grain == "month":
        months = (keys[-1][0] - keys[0][0]) * 12 + keys[-1][1] - keys[0][1] + 1
        return "quarter" if months > _MAX_SERIES_POINTS else "month"
    return grain


def _bucket(p: ParsedPeriod, grain: str) -> ParsedPeriod:
    y, m, d = p.key
    if grain == "week":
        day = date(y, m, d)
        monday = day - timedelta(days=day.weekday())
        return ParsedPeriod(
            (monday.year, monday.month, monday.day), "week", f"нед. {monday:%d.%m.%Y}"
        )
    if grain == "month":
        return period(y, m, grain="month")
    if grain == "quarter":
        return period(y, (m - 1) // 3 * 3 + 1, grain="quarter")
    if grain == "year":
        return period(y, grain="year")
    return p


def _fill_gaps(keys: list[ParsedPeriod], grain: str) -> list[ParsedPeriod]:
    """Пропущенные периоды ряда — явные точки без данных (разрыв линии)."""
    if grain not in ("month", "quarter", "year") or len(keys) < 2:
        return keys
    step = {"month": 1, "quarter": 3, "year": 12}[grain]
    out: list[ParsedPeriod] = []
    y, m = keys[0].key[0], max(keys[0].key[1], 1)
    end = (keys[-1].key[0], max(keys[-1].key[1], 1))
    while (y, m) <= end and len(out) < 400:
        out.append(_bucket(period(y, m, 1), grain))
        m += step
        while m > 12:
            m -= 12
            y += 1
    return out


_GRAIN_RU = {
    "week": "неделям",
    "month": "месяцам",
    "quarter": "кварталам",
    "year": "годам",
    "day": "дням",
}


def _time_views(
    dataset: Dataset, prof: TableProfile, metrics: list[ColumnProfile]
) -> list[DataView]:
    tcol = prof.column(prof.time_column)
    periods = tcol.periods
    known = [p for p in periods if p is not None]
    if not known:
        return []
    aggregate = not prof.time_unique or len(known) > _MAX_SERIES_POINTS or tcol.grain == "day"
    grain = _grain_for(known, tcol.grain or "day") if aggregate else (tcol.grain or "day")
    views: list[DataView] = []

    # метрики с одной единицей и сопоставимым порядком величины — одним
    # графиком (до 5 рядов): «выручка, млн ₽» и «заказы, шт.» на одной оси
    # не читаются, если у них нет явной единицы — сравниваем медианы.
    # Делим только при расхождении в 100+ раз: «новые/ушедшие клиенты»
    # (1240…2610 и ~410) — один график, «выручка» и «число заказов» в
    # абсолютах — разные.
    def _median(col: ColumnProfile) -> float:
        vals = sorted(abs(v) for v in col.numbers if v)
        return vals[len(vals) // 2] if vals else 0.0

    groups: OrderedDict[tuple[str | None, int], list[ColumnProfile]] = OrderedDict()
    _by_unit: OrderedDict[str | None, list[ColumnProfile]] = OrderedDict()
    for col in metrics:
        _by_unit.setdefault(col.unit, []).append(col)
    for unit, cols_u in _by_unit.items():
        ranked = sorted(cols_u, key=_median)
        cluster = 0
        prev_med: float | None = None
        for col in ranked:
            med = _median(col)
            if prev_med is not None and med > 0 and med / max(prev_med, 1e-9) >= 100:
                cluster += 1
            prev_med = med or prev_med
            groups.setdefault((unit, cluster), []).append(col)
    if aggregate:
        groups = OrderedDict(((c.unit, i), [c]) for i, c in enumerate(metrics[:2]))
    for n, ((unit, _scale), cols) in enumerate(groups.items()):
        cols = cols[:5]
        if aggregate:
            buckets: dict[tuple, ParsedPeriod] = {}
            sums: dict[tuple, list[float]] = {}
            for p, *vals in zip(periods, *[c.numbers for c in cols], strict=False):
                if p is None:
                    continue
                b = _bucket(p, grain)
                buckets[b.key] = b
                acc = sums.setdefault(b.key, [0.0] * len(cols))
                for i, v in enumerate(vals):
                    if v is not None:
                        acc[i] += v if cols[i].kind != PERCENT else v
            keys = _fill_gaps([buckets[k] for k in sorted(buckets)], grain)
            categories = [k.label for k in keys]
            series = [
                ViewSeries(
                    name=_metric_name(c, True),
                    unit=c.unit,
                    kind=c.kind,
                    values=[sums[k.key][i] if k.key in sums else None for k in keys],
                )
                for i, c in enumerate(cols)
            ]
            aggregation = f"сумма по {_GRAIN_RU.get(grain, grain)}"
        else:
            order = sorted(
                range(len(periods)), key=lambda i: periods[i].key if periods[i] else (9999, 0, 0)
            )
            order = [i for i in order if periods[i] is not None]
            keys = [periods[i] for i in order]
            filled = _fill_gaps(keys, grain)
            index = {periods[i].key: i for i in order}
            categories = [k.label for k in filled]
            series = [
                ViewSeries(
                    name=_metric_name(c, False),
                    unit=c.unit,
                    kind=c.kind,
                    values=[c.numbers[index[k.key]] if k.key in index else None for k in filled],
                )
                for c in cols
            ]
            aggregation = None
        gaps = [categories[i] for i, v in enumerate(series[0].values) if v is None]
        notes = []
        if gaps:
            shown = ", ".join(gaps[:3]) + ("…" if len(gaps) > 3 else "")
            notes.append(f"нет данных за {shown}")
        name = series[0].name
        views.append(
            DataView(
                id=f"{dataset.id}-trend{'' if n == 0 else n}",
                dataset_id=dataset.id,
                shape=SERIES,
                title=f"{name} по {_GRAIN_RU.get(grain, 'периодам')}",
                categories=categories,
                series=series,
                category_label=tcol.label,
                category_kind="time",
                unit=unit,
                aggregation=aggregation,
                notes=notes,
                evidence=[_ref(dataset, tcol.name), *(_ref(dataset, c.name) for c in cols)],
                source_rows=len(known),
            )
        )
    return views


# показатели одной записи, сумма которых по группам ничего не значит:
# длительность ответа, возраст, оценка, средние и доли
_PER_RECORD_METRIC = re.compile(
    r"(длительн|продолжительн|время|секунд|минут|duration|time\b|возраст|\bage\b|"
    r"оценк|рейтинг|балл|score|rating|nps|средн|average|\bavg|mean|медиан|"
    r"конверси|conversion|\brate\b|ставк|доля|share|маржа|margin|рентабельн)",
    re.I,
)


def _summable(metric: ColumnProfile) -> bool:
    return metric.kind != PERCENT and not _PER_RECORD_METRIC.search(metric.name)


_BRIDGE_START = re.compile(
    r"^(было|начало|на\s+начало|старт|база|базис|план|бюджет|opening|start|begin|base"
    r"|plan|budget)\b",
    re.IGNORECASE,
)
_BRIDGE_END = re.compile(
    r"^(стало|конец|на\s+конец|итог|итого|финал|факт|закрытие|closing|end|final"
    r"|actual|total|fact)\b",
    re.IGNORECASE,
)


def _is_bridge(totals: OrderedDict) -> bool:
    """Мост отклонений: первая строка — уровень «было», последняя —
    «стало», середина — факторы, и они сходятся: start + ΣΔ ≈ end
    (допуск 2% — округления в исходных данных)."""
    if not 3 <= len(totals) <= 16:
        return False
    keys = [str(k) for k in totals]
    if not (_BRIDGE_START.match(keys[0]) and _BRIDGE_END.match(keys[-1])):
        return False
    vals = list(totals.values())
    scale = max(abs(vals[0]), abs(vals[-1]), 1e-9)
    return abs(vals[0] + sum(vals[1:-1]) - vals[-1]) <= scale * 0.02


def _category_view(
    dataset: Dataset,
    prof: TableProfile,
    dim: ColumnProfile,
    metric: ColumnProfile | None,
    n: int,
) -> DataView | None:
    totals: OrderedDict[str, float] = OrderedDict()
    missing = 0
    rows = 0
    values = metric.numbers if metric is not None else [1.0] * len(dim.labels)
    for label, value in zip(dim.labels, values, strict=False):
        if value is None:
            continue
        rows += 1
        if label is None:
            # строки без категории — сноской, а не отдельным столбцом
            missing += 1
            continue
        totals[label] = totals.get(label, 0.0) + value
    if len(totals) < 2:
        return None
    if metric is None and max(totals.values()) <= 1:
        return None  # каждая категория по разу: считать нечего
    pre_aggregated = rows == len(totals)
    # мост отклонений держит исходный порядок и свою итоговую строку:
    # сортировка по убыванию разорвала бы логику «старт → факторы → итог»
    bridge = (
        metric is not None
        and metric.kind in (MONEY, NUMBER)
        and pre_aggregated
        and _is_bridge(totals)
    )
    if not bridge:
        # итоговая строка в данных — не категория
        totals = OrderedDict(
            (k, v)
            for k, v in totals.items()
            if not re.match(r"^(итого|всего|total|итог)\b", k, re.I)
        )
    ordered = list(totals.items()) if bridge else sorted(totals.items(), key=lambda kv: -kv[1])
    name = _metric_name(metric, not pre_aggregated) if metric is not None else "Число записей"
    notes = []
    if missing:
        notes.append(
            f"{missing} {plural(missing, ('строка', 'строки', 'строк'))} без значения «{dim.label}»"
        )
    return DataView(
        id=f"{dataset.id}-by{n}",
        dataset_id=dataset.id,
        shape=CATEGORICAL,
        title=f"{name} по {dim.label.casefold()}",
        categories=[k for k, _ in ordered],
        series=[
            ViewSeries(
                name=name,
                unit=metric.unit if metric else "шт.",
                kind=metric.kind if metric else NUMBER,
                values=[v for _, v in ordered],
            )
        ],
        category_label=dim.label,
        category_kind="category",
        unit=metric.unit if metric else "шт.",
        aggregation=(
            None
            if pre_aggregated
            else "сумма по категориям"
            if metric is not None
            else "число записей по категориям"
        ),
        structure=prof.structure
        or (metric is not None and metric.kind in (MONEY, NUMBER) and min(totals.values()) >= 0),
        notes=notes,
        bridge=bridge,
        evidence=[_ref(dataset, dim.name)] + ([_ref(dataset, metric.name)] if metric else []),
        source_rows=rows,
    )


def _share_view(dataset: Dataset, base: DataView | None) -> DataView | None:
    """Доли категорий от суммы — второе представление того же разреза
    (ADR-033 §8: по слайду на значимое представление данных).

    Строится, только когда метрика аддитивна и неотрицательна
    (``base.structure``): «доля от итога» — честный пересчёт исходных
    значений в проценты, новых чисел не появляется. Меньше трёх
    категорий — второй график ничего не добавляет."""
    metric = base.metric if base else None
    if (
        base is None
        or metric is None
        or metric.kind == PERCENT
        or not base.structure
        or base.category_kind != "category"
        or len(base.categories) < 3
        # счёт записей по категориям: «доля от итога» повторяет сам разрез
        or metric.name == "Число записей"
    ):
        return None
    values = [v for v in metric.values if v is not None]
    total = sum(values)
    if total <= 0 or len(values) != len(base.categories):
        return None
    # равномерный разрез: доли одинаковы, отдельный график ничего не добавляет
    if max(values) > 0 and (max(values) - min(values)) <= max(values) * 0.05:
        return None
    return DataView(
        id=f"{base.id}-share",
        dataset_id=dataset.id,
        shape=CATEGORICAL,
        title=f"{metric.name}: доля от итога",
        categories=list(base.categories),
        series=[
            ViewSeries(
                name="Доля",
                unit="%",
                kind=PERCENT,
                values=[
                    round(v / total * 100, 2) if v is not None else None for v in metric.values
                ],
            )
        ],
        category_label=base.category_label,
        category_kind="category",
        unit="%",
        aggregation="доля от суммы",
        structure=True,
        notes=list(base.notes),
        evidence=list(base.evidence),
        source_rows=base.source_rows,
        derived=True,
    )


REFERENCE_TOP = 6
REFERENCE_PAGE = 10
REFERENCE_COLUMNS = 4
REFERENCE_PAGES = 3


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        # «44 862», а не «44862»: узкая колонка ломает длинное число
        return fmt_number(float(value))
    return str(value)


def _sample(
    dataset: Dataset,
    limit: int = 8,
    prof: TableProfile | None = None,
) -> tuple[list[str], list[list[str]]]:
    """Образец строк. Для справочника — «топ-N» по главной метрике и
    страницы продолжения (до ``REFERENCE_PAGES`` по ``REFERENCE_PAGE``)."""
    headers = [c.name for c in dataset.columns]
    order = list(range(dataset.row_count))
    if prof is not None:
        limit = REFERENCE_TOP + REFERENCE_PAGE * REFERENCE_PAGES
        metric = next(
            (prof.column(m) for m in prof.metrics if prof.column(m).kind == MONEY),
            prof.column(prof.metrics[0]) if prof.metrics else None,
        )
        if metric is not None:
            order.sort(key=lambda i: -(metric.numbers[i] or 0))
    columns = list(dataset.columns)
    if len(columns) > REFERENCE_COLUMNS:
        profile = prof or profile_dataset(dataset)
        key = next((c.name for c in profile.columns if c.kind == IDENT), None)
        if key is None:
            key = next((c.name for c in profile.columns if c.kind in (CATEGORY, TEXT)), headers[0])
        columns.sort(key=lambda c: c.name != key)
        headers = [c.name for c in columns]
        raw_rows = [[c.values[i] for c in columns] for i in range(dataset.row_count)]
        indices = column_indices(headers, raw_rows, REFERENCE_COLUMNS)
        columns = [columns[i] for i in indices]
        headers = [c.name for c in columns]
    rows = [[_cell(c.values[i]) for c in columns] for i in order[:limit]]
    return headers, rows


def _reference_notes(dataset: Dataset, prof: TableProfile) -> list[str]:
    """Сводка справочника: сколько строк, итоги по метрикам."""
    from deckdna.analysis.values import fmt_scaled

    notes = [f"{dataset.row_count} {plural(dataset.row_count, ('строка', 'строки', 'строк'))}"]
    for name in prof.metrics[:2]:
        col = prof.column(name)
        values = [v for v in col.numbers if v is not None]
        if values:
            values.sort()
            notes.append(f"{col.label}: медиана {fmt_scaled(values[len(values) // 2], col.unit)}")
    return notes


_KPI_HEADERS = re.compile(r"^(показатель|метрика|kpi|indicator|metric|параметр)", re.I)


def _kpi_view(
    dataset: Dataset,
    prof: TableProfile,
    metrics: list[ColumnProfile],
    dims: list[ColumnProfile],
) -> DataView | None:
    """1–6 ключевых чисел: строка значений или «показатель — значение»."""
    if not metrics or prof.time_column or dataset.row_count > 6:
        return None
    from deckdna.analysis.profile import unit_of

    if dataset.row_count == 1 and not dims:
        cats = [m.label for m in metrics[:6]]
        values = [m.numbers[0] for m in metrics[:6]]
        units = [m.unit for m in metrics[:6]]
    elif (
        len(dims) == 1
        and len(metrics) == 1
        and (
            _KPI_HEADERS.match(dims[0].name.strip())
            or sum(1 for lbl in dims[0].labels if lbl and "," in lbl) * 2 >= len(dims[0].labels)
        )
    ):
        labels = dims[0].labels
        cats = [
            _UNIT_TAIL.sub("", lbl or "").strip() or f"#{i + 1}" for i, lbl in enumerate(labels)
        ]
        values = list(metrics[0].numbers)
        units = [unit_of(lbl or "", [], []) for lbl in labels]
    else:
        return None
    return DataView(
        id=f"{dataset.id}-kpi",
        dataset_id=dataset.id,
        shape=KPI,
        title=dataset.title,
        categories=cats,
        series=[ViewSeries(name="Значение", values=values, unit=None)],
        category_kind="none",
        unit=None,
        evidence=[_ref(dataset, m.name) for m in metrics[:6]],
        source_rows=dataset.row_count,
        kpi_units=units,
    )


_UNIT_TAIL = re.compile(r"[,(]\s*[^,()]{1,16}\s*\)?\s*$")


def build_views(dataset: Dataset, prof: TableProfile) -> list[DataView]:
    if not prof.typable:
        headers, rows = _sample(dataset)
        return [
            DataView(
                id=f"{dataset.id}-ref",
                dataset_id=dataset.id,
                shape=REFERENCE,
                title=dataset.title,
                categories=[],
                series=[],
                category_kind="none",
                sample_headers=headers,
                sample_rows=rows,
                reason=prof.reason,
                source_rows=dataset.row_count,
            )
        ]
    metrics = [prof.column(m) for m in prof.metrics]
    views: list[DataView] = []
    dims = [
        prof.column(d)
        for d in prof.dimensions
        if 2 <= prof.column(d).cardinality <= _MAX_CATEGORIES
    ]
    kpi = _kpi_view(dataset, prof, metrics, dims)
    if kpi is not None:
        return [kpi]
    if prof.time_column and metrics:
        views.extend(_time_views(dataset, prof, metrics))
    # справочник сущностей (артикул, id на каждой строке): сумма цен по
    # категориям бессмысленна — считаем записи, главное — таблица
    entity = (
        not prof.time_column
        and len(prof.columns) >= 5
        and any(c.kind == IDENT and c.cardinality >= 0.9 * dataset.row_count for c in prof.columns)
    )
    primary = next((m for m in metrics if m.kind == MONEY), metrics[0] if metrics else None)
    # по записям (ответы опроса, сделки) показатель одной записи не
    # суммируется по группам: долю считаем по числу записей
    per_record = bool(dims) and dataset.row_count > max(d.cardinality for d in dims)
    if per_record and primary is not None and not _summable(primary):
        primary = next((m for m in metrics if _summable(m)), None)
    if entity:
        primary = None
    if dims and (primary is not None or not prof.time_column):
        cat_views: list[DataView] = []
        for n, dim in enumerate(dims[:4]):
            view = _category_view(dataset, prof, dim, primary, n)
            if view is not None:
                cat_views.append(view)
        if entity:
            # Для справочника >= 200 строк строим только Top-N по главной метрике
            # со сводкой, а не «число записей по категории/поставщику» (F-86).
            if dataset.row_count >= 200:
                cat_views = []
            else:
                cat_views = [v for v in cat_views if _skewed(v)]
        views.extend(cat_views)
        # второй взгляд на первый разрез: структура (доли от суммы)
        share = _share_view(dataset, cat_views[0] if cat_views else None)
        if share is not None:
            views.append(share)
    # распределение: одна метрика, много строк, без времени
    if len(metrics) == 1 and not entity and not prof.time_column and dataset.row_count >= 20:
        m = metrics[0]
        values = [v for v in m.numbers if v is not None]
        views.append(
            DataView(
                id=f"{dataset.id}-dist",
                dataset_id=dataset.id,
                shape=DISTRIBUTION,
                title=f"Распределение: {m.label}",
                categories=[],
                series=[ViewSeries(name=m.label, values=values, unit=m.unit, kind=m.kind)],
                category_label=m.label,
                category_kind="none",
                unit=m.unit,
                raw_values=values,
                evidence=[_ref(dataset, m.name)],
                source_rows=len(values),
            )
        )
    # взаимосвязь: две метрики на одних объектах
    if len(metrics) >= 2 and not prof.time_column and dataset.row_count >= 5 and not entity:
        x, y = metrics[0], metrics[1]
        pairs = [
            (a, b)
            for a, b in zip(x.numbers, y.numbers, strict=False)
            if a is not None and b is not None
        ]
        if len(pairs) >= 5:
            views.append(
                DataView(
                    id=f"{dataset.id}-rel",
                    dataset_id=dataset.id,
                    shape=RELATIONSHIP,
                    title=f"{y.label} и {x.label.casefold()}",
                    categories=[],
                    series=[
                        ViewSeries(
                            name=y.label, values=[b for _, b in pairs], unit=y.unit, kind=y.kind
                        )
                    ],
                    category_label=x.label,
                    category_kind="none",
                    unit=y.unit,
                    x_values=[a for a, _ in pairs],
                    x_label=x.label,
                    evidence=[_ref(dataset, x.name), _ref(dataset, y.name)],
                    source_rows=len(pairs),
                )
            )
    wants_ref = entity or (len(prof.columns) >= 6 and dataset.row_count >= 200)
    if wants_ref:
        headers, rows = _sample(dataset, prof=prof)
        views.append(
            DataView(
                id=f"{dataset.id}-ref",
                dataset_id=dataset.id,
                shape=REFERENCE,
                title=dataset.title,
                categories=[],
                series=[],
                category_kind="none",
                sample_headers=headers,
                sample_rows=rows,
                source_rows=dataset.row_count,
                primary_hint=entity and dataset.row_count >= 200,
                notes=_reference_notes(dataset, prof),
            )
        )
    elif not views:
        headers, rows = _sample(dataset)
        views.append(
            DataView(
                id=f"{dataset.id}-ref",
                dataset_id=dataset.id,
                shape=REFERENCE,
                title=dataset.title,
                categories=[],
                series=[],
                category_kind="none",
                sample_headers=headers,
                sample_rows=rows,
                source_rows=dataset.row_count,
                reason="нет главной метрики для графика",
            )
        )
    return views


def _skewed(view: DataView) -> bool:
    """Есть ли выраженный лидер: иначе «число записей по категориям»
    справочника — равномерное распределение без вывода. Перекос в
    относительных величинах: лидер держит ≥ четверти записей и в
    полтора раза опережает минимум."""
    metric = view.metric
    vals = [v for v in metric.values if v is not None] if metric else []
    total = sum(vals)
    return bool(vals) and total > 0 and max(vals) > min(vals) * 1.5 and max(vals) / total >= 0.25


__all__ = [
    "CATEGORICAL",
    "DISTRIBUTION",
    "KPI",
    "REFERENCE",
    "RELATIONSHIP",
    "SERIES",
    "DataView",
    "ViewSeries",
    "build_views",
]
