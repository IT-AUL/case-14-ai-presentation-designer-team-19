"""Профиль датасета без модели (ADR-033 §2).

По колонке: тип, кардинальность, доля пропусков, выбросы (IQR ×1,5),
монотонность, единица. По таблице: временная ось, измерения и метрики,
иерархия, признак структуры (доли), ``typable`` с причиной."""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from deckdna.analysis.values import ParsedPeriod, parse_number, parse_period, plural
from deckdna.contracts.dataset import Dataset

# типы колонок
NUMBER, PERCENT, MONEY, PERIOD, CATEGORY, TEXT, IDENT = (
    "number",
    "percent",
    "money",
    "period",
    "category",
    "text",
    "id",
)
METRIC_KINDS = (NUMBER, PERCENT, MONEY)

_ID_NAME = re.compile(
    r"^(id|ид|код|артикул|sku|№|номер|client|клиент|customer|order|заказ)"
    r"(\s*(id|№|клиента|заказа|товара))?$|(^|[\s_\-])(id|ид|sku)$|артикул|^№",
    re.I,
)
_TIME_NAME = re.compile(
    r"(дата|date|месяц|month|год|year|период|period|квартал|quarter|неделя|week|день|day)", re.I
)
_UNIT_IN_NAME = re.compile(r"[,(]\s*([^,()]{1,16})\s*\)?\s*$")
_UNIT_ONLY_NAME = re.compile(
    r"^\s*((?:млн\.?|тыс\.?|млрд\.?)\s*(?:₽|руб\.?|р\.?|\$|€)?"
    r"|(?:₽|руб\.?|р\.?|\$|€|%|п\.?\s*п\.?|шт\.?|ед\.?))\s*$",
    re.I,
)
_TOTAL = re.compile(r"^(итого|всего|total|сумма|итог)\b", re.I)


_INCOMPLETE_PERIOD_LABEL_RE = re.compile(
    r"(?:неполн|незаверш|partial|ytd|mtd|прогноз|forecast|est\b|\*+$)",
    re.I,
)
_SCALE_SUFFIX_METRIC_RE = re.compile(
    r"(?:[_\s\-]+|\b)(?:thousands?|тыс\.?|k|thous|millions?|млн\.?|mln|billions?|млрд\.?|bln)$",
    re.I,
)


@dataclass
class ColumnProfile:
    name: str
    kind: str
    unit: str | None = None
    cardinality: int = 0
    missing: int = 0
    missing_ratio: float = 0.0
    numbers: list[float | None] = field(default_factory=list)
    periods: list[ParsedPeriod | None] = field(default_factory=list)
    labels: list[str | None] = field(default_factory=list)
    outliers: list[int] = field(default_factory=list)
    monotonic: str | None = None  # up | down | None
    grain: str | None = None
    additivity: str = "additive"  # additive | semi_additive | non_additive | unknown

    @property
    def is_metric(self) -> bool:
        return self.kind in METRIC_KINDS

    @property
    def label(self) -> str:
        """Имя колонки без единицы в шапке: «Сумма, ₽» → «Сумма»."""
        if _UNIT_ONLY_NAME.match(self.name):
            return "Показатель"
        return _UNIT_IN_NAME.sub("", self.name).strip() or self.name


@dataclass
class TableProfile:
    columns: list[ColumnProfile]
    n_rows: int
    n_cols: int
    time_column: str | None = None
    time_grain: str | None = None
    time_regular: bool = False
    time_unique: bool = False
    dimensions: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    hierarchy: list[tuple[str, str]] = field(default_factory=list)
    structure: bool = False
    structure_reason: str | None = None
    typable: bool = True
    reason: str | None = None
    incomplete_last_period: bool = False
    suppressed_metrics: list[str] = field(default_factory=list)
    trivial_metric_pairs: list[tuple[str, str]] = field(default_factory=list)

    def column(self, name: str) -> ColumnProfile:
        return next(c for c in self.columns if c.name == name)

    def summary_ru(self) -> str:
        """Одна строка для экрана «Вот что я понял»."""
        parts = [
            f"{self.n_rows} {plural(self.n_rows, ('строка', 'строки', 'строк'))}",
            f"{self.n_cols} {plural(self.n_cols, ('колонка', 'колонки', 'колонок'))}",
        ]
        if self.time_column:
            col = self.column(self.time_column)
            known = [p for p in col.periods if p is not None]
            if known:
                first, last = min(known, key=lambda p: p.key), max(known, key=lambda p: p.key)
                parts.append(f"период {first.label} – {last.label}")
        if self.metrics:
            parts.append("метрики: " + ", ".join(self.column(m).label for m in self.metrics[:3]))
        if self.dimensions:
            parts.append("разрезы: " + ", ".join(self.column(d).label for d in self.dimensions[:3]))
        gaps = [c for c in self.columns if c.missing]
        if gaps:
            parts.append("есть пропуски: " + ", ".join(c.label for c in gaps[:3]))
        return "; ".join(parts)


def unit_of(name: str, numbers: list, raw: list) -> str | None:
    m = _UNIT_IN_NAME.search(name) or _UNIT_ONLY_NAME.match(name)
    hint = (m.group(1).strip() if m else "").casefold()
    scale = next(
        (w for w in ("тыс.", "тыс", "млрд", "млн") if w in hint.split() or hint.startswith(w)), None
    )
    rest = hint
    for word in ("тыс.", "тыс", "млрд", "млн"):
        rest = rest.replace(word, " ")
    base = _base_unit(rest.strip(), raw)
    if scale:
        scale = "тыс." if scale.startswith("тыс") else scale
        return f"{scale} {base}" if base and base != "%" else scale
    return base


def _base_unit(hint: str, raw: list) -> str | None:
    text = " ".join(str(v) for v in raw[:50] if v is not None).casefold()
    for probe in (hint, text):
        if not probe:
            continue
        if "%" in probe:
            return "%"
        if "₽" in probe or "руб" in probe or "rub" in probe:
            return "₽"
        if "$" in probe or "usd" in probe:
            return "$"
        if "€" in probe or "eur" in probe:
            return "€"
        if "шт" in probe or "pcs" in probe:
            return "шт."
    if hint and len(hint) <= 12 and not any(ch.isdigit() for ch in hint):
        return hint
    return None


SCALED_UNIT = re.compile(r"^(тыс\.|млн|млрд)\b")


def _monotonic(values: list[float]) -> str | None:
    if len(values) < 3:
        return None
    diffs = [b - a for a, b in zip(values, values[1:], strict=False)]
    ups = sum(d > 0 for d in diffs)
    downs = sum(d < 0 for d in diffs)
    if ups >= 0.8 * len(diffs):
        return "up"
    if downs >= 0.8 * len(diffs):
        return "down"
    return None


def _outliers(values: list[float]) -> list[int]:
    clean = [v for v in values if v is not None]
    if len(clean) < 8:
        return []
    q = statistics.quantiles(clean, n=4)
    iqr = q[2] - q[0]
    lo, hi = q[0] - 1.5 * iqr, q[2] + 1.5 * iqr
    return [i for i, v in enumerate(values) if v is not None and (v < lo or v > hi)]


def _infer_column_additivity(name: str, kind: str, unit: str | None) -> str:
    from deckdna.analysis.fact_ledger import infer_metric_additivity

    if kind == IDENT:
        return "non_additive"
    if kind == PERCENT:
        return "non_additive"
    if kind not in METRIC_KINDS:
        return "non_additive"
    return infer_metric_additivity(name, unit)


# Кириллица и латиница одного начертания («А1» и «A1») — одна подпись:
# опечатка ввода не должна становиться двумя сегментами.
_HOMOGLYPHS = str.maketrans("АВЕКМНОРСТХаеорсух", "ABEKMHOPCTXaeopcyx")
_FLOAT_INT = re.compile(r"^-?\d+\.0+$")
_CODE_NAME = re.compile(
    r"(верси|version|волн|wave|код|code|тип|type|класс|class|групп|group|сегмент|segment|"
    r"категор|вариант|анкет|шкал|scale|ступен|уровен|level)",
    re.I,
)


_BUCKET = re.compile(r"\d\s*[–—-]\s*\d|\d\s*\+\s*$|^\D+$")


def _bucketed(present: list, numbers: list) -> bool:
    """Ответы-корзины («0», «1», «4–7», «8+», «Не помню»): шкала опроса,
    а не измеренная величина."""
    unparsed = sum(1 for p in numbers if p is None) > len(numbers) - len(present)
    return unparsed or any(_BUCKET.search(str(v).strip()) for v in present)


def _canonical_labels(labels: list[str | None]) -> list[str | None]:
    """Свести написания одной подписи к самому частому; «2.0» → «2»."""

    def key(text: str) -> str:
        return re.sub(r"\s+", " ", text.translate(_HOMOGLYPHS)).strip().casefold()

    cleaned = [
        None if v is None else (v.split(".")[0] if _FLOAT_INT.match(v) else v) for v in labels
    ]
    counts: dict[str, dict[str, int]] = {}
    for v in cleaned:
        if v is not None:
            spellings = counts.setdefault(key(v), {})
            spellings[v] = spellings.get(v, 0) + 1
    best = {k: max(sp, key=lambda t: (sp[t], -list(sp).index(t))) for k, sp in counts.items()}
    return [None if v is None else best[key(v)] for v in cleaned]


def profile_column(name: str, raw: list) -> ColumnProfile:
    n = len(raw)
    present = [v for v in raw if v is not None and str(v).strip() != ""]
    missing = n - len(present)
    prof = ColumnProfile(
        name=name, kind=TEXT, missing=missing, missing_ratio=round(missing / n, 3) if n else 0.0
    )
    if not present:
        prof.kind = TEXT
        prof.additivity = "unknown"
        return prof
    labels = _canonical_labels(
        [None if v is None or str(v).strip() == "" else str(v).strip() for v in raw]
    )
    prof.labels = labels
    prof.cardinality = len({v for v in labels if v is not None})

    periods = [parse_period(v) if v is not None and str(v).strip() else None for v in raw]
    period_hits = sum(p is not None for p in periods)
    numbers = [parse_number(v) if v is not None and str(v).strip() else None for v in raw]
    number_hits = sum(p is not None for p in numbers)
    time_named = bool(_TIME_NAME.search(name))

    # годы — и числа, и периоды: решают имя колонки и диапазон
    if period_hits >= 0.9 * len(present) and (number_hits < 0.9 * len(present) or time_named):
        prof.kind = PERIOD
        prof.periods = periods
        grains = {p.grain for p in periods if p is not None}
        prof.grain = (
            "day" if "day" in grains else min(grains, key=["month", "quarter", "year"].index)
        )
        prof.additivity = "non_additive"
        return prof
    if number_hits >= 0.9 * len(present):
        values = [p.value if p is not None else None for p in numbers]
        scales = {p.scale_word for p in numbers if p is not None and p.scale_word}
        prof.numbers = values
        prof.unit = unit_of(name, values, raw)
        if scales and prof.unit and prof.unit != "%":
            prof.unit = f"{sorted(scales)[0]} {prof.unit}"
        elif scales and not prof.unit:
            prof.unit = sorted(scales)[0]
        if any(p is not None and p.percent for p in numbers) or prof.unit == "%":
            prof.kind = PERCENT
            prof.unit = "%"
        elif any(p is not None and p.currency for p in numbers) or (
            prof.unit and any(c in prof.unit for c in ("₽", "$", "€"))
        ):
            prof.kind = MONEY
        else:
            prof.kind = NUMBER
        integers = all(v is None or float(v).is_integer() for v in values)
        if _ID_NAME.search(name.strip()) and integers and prof.cardinality >= 0.9 * len(present):
            prof.kind = IDENT
            prof.numbers = []
            prof.additivity = "non_additive"
            return prof
        present_values = [v for v in values if v is not None]
        # Константа-код («Версия анкеты» = 2 во всех строках) и мелкие целые
        # коды (волна, тип, уровень) не складываются: их сумма по категориям — это число строк,
        # умноженное на код, а не показатель. Такие колонки — разрез.
        if (
            prof.kind == NUMBER
            and not prof.unit
            and integers
            and (_CODE_NAME.search(name) or _bucketed(present, numbers))
            and (
                prof.cardinality == 1
                or (
                    prof.cardinality <= 6
                    and prof.cardinality <= 0.3 * len(present_values)
                    and max(abs(v) for v in present_values) <= 10
                )
            )
        ):
            prof.kind = CATEGORY
            prof.numbers = []
            prof.additivity = "non_additive"
            return prof
        prof.outliers = _outliers(values)
        prof.monotonic = _monotonic(present_values)
        prof.additivity = _infer_column_additivity(name, prof.kind, prof.unit)
        return prof
    unique_ratio = prof.cardinality / len(present)
    avg_len = sum(len(v) for v in labels if v) / len(present)
    if _ID_NAME.search(name.strip()) and unique_ratio > 0.5:
        prof.kind = IDENT
    elif prof.cardinality <= max(30, 0.5 * len(present)) and avg_len <= 40:
        prof.kind = CATEGORY
    elif unique_ratio >= 0.95 and avg_len <= 24:
        prof.kind = IDENT
    else:
        prof.kind = TEXT
    prof.additivity = "non_additive"
    return prof


def _detect_incomplete_last_period(tcol: ColumnProfile) -> bool:
    """Проверить, является ли последний период неполным (по подписи или числу дней в месяце)."""
    non_empty_labels = [lbl for lbl in tcol.labels if lbl]
    if non_empty_labels and _INCOMPLETE_PERIOD_LABEL_RE.search(non_empty_labels[-1]):
        return True
    known = [p for p in tcol.periods if p is not None]
    if len(known) >= 10 and tcol.grain == "day":
        by_month: dict[tuple[int, int], int] = {}
        for p in known:
            ym = (p.key[0], p.key[1])
            by_month[ym] = by_month.get(ym, 0) + 1
        if len(by_month) >= 3:
            ordered_months = [by_month[k] for k in sorted(by_month)]
            prev_median = statistics.median(ordered_months[:-1])
            if prev_median >= 10 and ordered_months[-1] < prev_median * 0.35:
                return True
    return False


def _detect_trivial_metric_duplicates(
    metric_cols: list[ColumnProfile],
) -> tuple[list[tuple[str, str]], list[str]]:
    from deckdna.analysis.fact_ledger import are_trivially_related_metrics

    pairs: list[tuple[str, str]] = []
    suppressed: list[str] = []
    for i, col_a in enumerate(metric_cols):
        for col_b in metric_cols[i + 1 :]:
            if are_trivially_related_metrics(col_a.name, col_b.name, col_a.numbers, col_b.numbers):
                pairs.append((col_a.name, col_b.name))
                a_scaled = bool(_SCALE_SUFFIX_METRIC_RE.search(col_a.name))
                b_scaled = bool(_SCALE_SUFFIX_METRIC_RE.search(col_b.name))
                if b_scaled and not a_scaled and col_b.name not in suppressed:
                    suppressed.append(col_b.name)
                elif a_scaled and not b_scaled and col_a.name not in suppressed:
                    suppressed.append(col_a.name)
                elif col_b.name not in suppressed:
                    suppressed.append(col_b.name)
    return pairs, suppressed


def profile_dataset(dataset: Dataset) -> TableProfile:
    cols = [profile_column(c.name, list(c.values)) for c in dataset.columns]
    prof = TableProfile(columns=cols, n_rows=dataset.row_count, n_cols=len(cols))
    time_cols = [c for c in cols if c.kind == PERIOD]
    if time_cols:
        col = time_cols[0]
        prof.time_column = col.name
        prof.time_grain = col.grain
        keys = [p.key for p in col.periods if p is not None]
        prof.time_unique = len(set(keys)) == len(keys)
        prof.time_regular = prof.time_unique and _regular(sorted(keys), col.grain or "day")
        prof.incomplete_last_period = _detect_incomplete_last_period(col)
    metric_cols = [c for c in cols if c.is_metric]
    prof.trivial_metric_pairs, prof.suppressed_metrics = _detect_trivial_metric_duplicates(
        metric_cols
    )
    prof.metrics = [c.name for c in metric_cols]
    prof.dimensions = [c.name for c in cols if c.kind == CATEGORY]
    prof.hierarchy = _hierarchy(cols)
    prof.structure, prof.structure_reason = _structure(prof)
    if not prof.metrics and not prof.dimensions:
        prof.typable = False
        prof.reason = "нет ни числовых колонок, ни категорий: таблица из текста или идентификаторов"
    elif (
        not prof.metrics
        and not prof.time_column
        and all(prof.column(d).cardinality > 30 for d in prof.dimensions)
    ):
        prof.typable = False
        prof.reason = "нет числовых колонок, а категорий слишком много для подсчёта"
    elif dataset.row_count == 0:
        prof.typable = False
        prof.reason = "в таблице нет строк с данными"
    return prof


def _regular(keys: list[tuple[int, int, int]], grain: str) -> bool:
    if len(keys) < 3:
        return True
    if grain == "year":
        steps = {b[0] - a[0] for a, b in zip(keys, keys[1:], strict=False)}
    elif grain in ("month", "quarter"):
        steps = {(b[0] - a[0]) * 12 + b[1] - a[1] for a, b in zip(keys, keys[1:], strict=False)}
    else:
        from datetime import date

        days = [date(*k) for k in keys]
        steps = {(b - a).days for a, b in zip(days, days[1:], strict=False)}
    return len(steps) == 1


def _hierarchy(cols: list[ColumnProfile]) -> list[tuple[str, str]]:
    """Категория → подкатегория: каждое значение дочерней колонки живёт
    ровно в одном значении родительской."""
    cats = [c for c in cols if c.kind == CATEGORY]
    pairs = []
    for parent in cats:
        for child in cats:
            if parent is child or child.cardinality <= parent.cardinality:
                continue
            owner: dict[str, str] = {}
            ok = True
            for p, c in zip(parent.labels, child.labels, strict=False):
                if p is None or c is None:
                    continue
                if owner.setdefault(c, p) != p:
                    ok = False
                    break
            if ok and owner:
                pairs.append((parent.name, child.name))
    return pairs


def _structure(prof: TableProfile) -> tuple[bool, str | None]:
    """Доли: неотрицательная метрика по категориям, сумма ≈ 100% или
    строка «итог»."""
    if not prof.metrics or not prof.dimensions:
        return False, None
    metric = prof.column(prof.metrics[0])
    values = [v for v in metric.numbers if v is not None]
    if not values or min(values) < 0:
        return False, None
    dim = prof.column(prof.dimensions[0])
    if any(lbl and _TOTAL.match(lbl) for lbl in dim.labels):
        return True, "есть строка «итого»"
    if metric.kind == PERCENT and 97 <= sum(values) <= 103:
        return True, "доли в сумме дают 100%"
    if 0.97 <= sum(values) <= 1.03 and max(values) <= 1:
        return True, "доли в сумме дают 1"
    # суммы по категориям неотрицательны и складываются в целое
    if metric.kind in (MONEY, NUMBER) and prof.time_column is None:
        return True, "неотрицательные значения по категориям складываются в целое"
    return False, None


__all__ = [
    "CATEGORY",
    "IDENT",
    "METRIC_KINDS",
    "MONEY",
    "NUMBER",
    "PERCENT",
    "PERIOD",
    "TEXT",
    "ColumnProfile",
    "TableProfile",
    "profile_column",
    "profile_dataset",
    "unit_of",
]
