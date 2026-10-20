"""Типизированный реестр вычисленных фактов (FactLedger) — выжимка 19.10 (§1–§3, §6).

Код вычисляет все числа, производные величины и предикаты, проверяет каждый факт
жёсткими правилами допустимости (`validate_fact`), ранжирует по формуле
`S = V·C·(0.30R + 0.25I + 0.25E + 0.20U)` и выполняет MMR-дедупликацию
с различением противоречий (одинаковый ключ, несовместимые предикаты) и
контрастов срезов (разный `scope`)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from deckdna.analysis.title_templates import (
    allowed_title_ids_for_fact,
    default_title_for_fact,
    format_unit_ru,
    render_title,
)
from deckdna.analysis.values import parse_period

if TYPE_CHECKING:
    from deckdna.analysis.pipeline import DatasetAnalysis
    from deckdna.analysis.profile import ColumnProfile, TableProfile

FactKey = tuple[str, str, str, tuple[tuple[str, str], ...], str, str, str]

_DEFAULT_INSIGHTS_CONFIG: dict[str, Any] = {
    "version": "1.0",
    "ranking": {
        "relevance": 0.30,
        "impact": 0.25,
        "effect_size": 0.25,
        "unusualness": 0.20,
    },
    "mmr": {
        "lambda_sim": 0.30,
        "min_marginal_gain": 0.15,
        "similarity": {
            "same_metric": 0.35,
            "same_scope": 0.25,
            "same_period": 0.20,
            "same_evidence": 0.20,
        },
    },
    "validation": {
        "narrow_dispersion_forbid_max_min_ratio": 10.0,
        "wide_dispersion_max_min_ratio": 3.0,
        "steady_trend_min_points": 8,
        "steady_trend_min_r2": 0.70,
        "steady_trend_min_same_sign_share": 0.70,
        "flat_max_abs_pct": 5.0,
        "share_sum_tolerance_pct": 3.0,
        "trivial_correlation_min_r": 0.9999,
        "incomplete_period_min_day_ratio": 0.35,
    },
}

_SCALE_SUFFIX_RE = re.compile(
    r"(?:[_\s\-]+|\b)(?:"
    r"thousands?|тыс\.?|k|thous|"
    r"millions?|млн\.?|mln|m|"
    r"billions?|млрд\.?|bln|b|"
    r"rub|руб\.?|usd|eur|pct|percent"
    r")$",
    re.I,
)

_NON_ADDITIVE_NAME_RE = re.compile(
    r"(?:conversion|конверси|rate|ставк|доля|share|margin|маржа|рентабельн|"
    r"roi|romi|roas|ctr|cpc|cpm|cpa|nps|churn|отток|arpu|arppu|средний\s+чек|avg[_\s]*check)",
    re.I,
)

_SEMI_ADDITIVE_NAME_RE = re.compile(
    r"(?:balance|баланс|остаток|остатки|stock|inventory|запас|headcount|штат|численност)",
    re.I,
)

_IDENTIFIER_NAME_RE = re.compile(
    r"(?:^id$|_id$|^uuid$|^код$|^инн$|^огрн$|^артикул$|^sku$|^номер$)",
    re.I,
)

_CAUSAL_CLAIM_RE = re.compile(
    r"(?:причин[аы]|вызван[оаы]?|из-за|вследствие|потому\s+что|caused[_\s]*by|because[_\s]*of)",
    re.I,
)

_INCREASE_PREDICATES = frozenset(
    {
        "increase",
        "growth",
        "steady_increase",
        "sustained_growth",
        "every_month_increase",
        "every_period_increase",
        "strict_increase",
        "zero_base_increase",
        "acceleration",
    }
)

_DECREASE_PREDICATES = frozenset(
    {
        "decrease",
        "decline",
        "steady_decrease",
        "sustained_decline",
        "every_month_decrease",
        "every_period_decrease",
        "strict_decrease",
        "zero_base_decrease",
        "deceleration",
    }
)

_POSITIVE_CHANGE_PREDICATES = _INCREASE_PREDICATES | frozenset(
    {"loss_reduced", "turnaround_to_positive"}
)
_NEGATIVE_CHANGE_PREDICATES = _DECREASE_PREDICATES | frozenset(
    {"loss_deepened", "turnaround_to_negative"}
)

_PCT_OR_RATIO_TEMPLATES = frozenset(
    {
        "increase_pct",
        "decrease_pct",
        "increase_ratio",
        "decrease_ratio",
        "steady_increase",
        "steady_decrease",
        "every_month_increase",
        "every_month_decrease",
        "every_period_increase",
        "every_period_decrease",
    }
)


@lru_cache(maxsize=4)
def load_insights_config(config_path: str | None = None) -> dict[str, Any]:
    """Загрузить версионируемый конфиг весов и порогов FactLedger из
    `configs/insights.default.yaml`."""
    candidates: list[Path] = []
    if config_path:
        candidates.append(Path(config_path))
    repo_root = Path(__file__).resolve().parents[3]
    candidates.append(repo_root / "configs" / "insights.default.yaml")
    candidates.append(Path.cwd() / "configs" / "insights.default.yaml")

    cfg: dict[str, Any] = {
        "version": _DEFAULT_INSIGHTS_CONFIG["version"],
        "ranking": dict(_DEFAULT_INSIGHTS_CONFIG["ranking"]),
        "mmr": {
            **_DEFAULT_INSIGHTS_CONFIG["mmr"],
            "similarity": dict(_DEFAULT_INSIGHTS_CONFIG["mmr"]["similarity"]),
        },
        "validation": dict(_DEFAULT_INSIGHTS_CONFIG["validation"]),
    }
    for path in candidates:
        if path.is_file():
            try:
                loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                continue
            if isinstance(loaded, dict):
                if "version" in loaded:
                    cfg["version"] = str(loaded["version"])
                for section in ("ranking", "validation"):
                    if isinstance(loaded.get(section), dict):
                        cfg[section].update(loaded[section])
                if isinstance(loaded.get("mmr"), dict):
                    mmr_in = loaded["mmr"]
                    for k, v in mmr_in.items():
                        if k == "similarity" and isinstance(v, dict):
                            cfg["mmr"]["similarity"].update(v)
                        else:
                            cfg["mmr"][k] = v
                break
    return cfg


def canonical_metric_stem(name: str) -> str:
    """Нормализовать имя метрики для обнаружения тривиально масштабированных дублей
    (например, `revenue` vs `revenue_thousands`, `выручка` vs `выручка_тыс`)."""
    raw = (name or "").strip().casefold()
    raw = re.sub(r"\([^)]*\)", "", raw).strip(" ,_-")
    prev = None
    while prev != raw:
        prev = raw
        raw = _SCALE_SUFFIX_RE.sub("", raw).strip(" ,_-")
    aliases = {
        "выручка": "revenue",
        "доход": "revenue",
        "продажи": "sales",
        "оборот": "turnover",
        "прибыль": "profit",
        "убыток": "loss",
        "расходы": "cost",
        "затраты": "expenses",
        "заказы": "orders",
        "клиенты": "clients",
    }
    return aliases.get(raw, raw)


def are_rescaled_metric_names(metric_a: str, metric_b: str) -> bool:
    """Проверить, являются ли два имени одной и той же метрикой в разных масштабах."""
    a_clean = (metric_a or "").strip().casefold()
    b_clean = (metric_b or "").strip().casefold()
    if not a_clean or not b_clean or a_clean == b_clean:
        return False
    stem_a = canonical_metric_stem(a_clean)
    stem_b = canonical_metric_stem(b_clean)
    return bool(stem_a and stem_a == stem_b)


def are_trivially_related_metrics(
    metric_a: str,
    metric_b: str,
    series_a: list[float] | None = None,
    series_b: list[float] | None = None,
) -> bool:
    """Определить тривиальную линейную/масштабную зависимость двух метрик."""
    if are_rescaled_metric_names(metric_a, metric_b):
        return True
    if series_a and series_b and len(series_a) == len(series_b) and len(series_a) >= 3:
        ratios: list[float] = []
        for a, b in zip(series_a, series_b, strict=False):
            if a is None or b is None:
                continue
            fa, fb = float(a), float(b)
            if abs(fa) > 1e-9 and abs(fb) > 1e-9:
                ratios.append(fb / fa)
        if len(ratios) >= 3:
            mean_r = sum(ratios) / len(ratios)
            if abs(mean_r) > 1e-12 and all(abs(r - mean_r) / abs(mean_r) < 1e-4 for r in ratios):
                # Проверяем типичные коэффициенты масштабирования (10^k) или совпадение корня
                log10_r = math.log10(abs(mean_r))
                if abs(log10_r - round(log10_r)) < 1e-3:
                    return True
    return False


def infer_metric_additivity(
    metric_name: str,
    unit: str | None = None,
    *,
    role: str | None = None,
    explicit_additivity: str | None = None,
) -> str:
    """Вывести тип аддитивности метрики (`additive`, `semi_additive`, `non_additive`, `unknown`)."""
    if explicit_additivity:
        return explicit_additivity.strip().casefold()
    if role == "identifier" or _IDENTIFIER_NAME_RE.search(metric_name or ""):
        return "non_additive"
    norm_unit = format_unit_ru(unit) or ""
    if norm_unit in ("%", "п.п.") or _NON_ADDITIVE_NAME_RE.search(metric_name or ""):
        return "non_additive"
    if _SEMI_ADDITIVE_NAME_RE.search(metric_name or ""):
        return "semi_additive"
    return "additive"


def _compute_series_stats(series: list[float]) -> dict[str, Any]:
    """Вычислить R², долю шагов одного знака и монотонность по числовому ряду."""
    n = len(series)
    if n < 2:
        return {"n_points": n, "r2": 0.0, "same_sign_share": 0.0, "monotonic": False}
    diffs = [float(series[i + 1]) - float(series[i]) for i in range(n - 1)]
    pos = sum(1 for d in diffs if d > 0)
    neg = sum(1 for d in diffs if d < 0)
    total_steps = len(diffs)
    overall = float(series[-1]) - float(series[0])
    if overall > 0:
        same_sign_share = pos / total_steps
        monotonic = all(d > 0 for d in diffs)
    elif overall < 0:
        same_sign_share = neg / total_steps
        monotonic = all(d < 0 for d in diffs)
    else:
        same_sign_share = 0.0
        monotonic = False

    mean_x = (n - 1) / 2.0
    mean_y = sum(series) / n
    ss_xx = sum((i - mean_x) ** 2 for i in range(n))
    ss_yy = sum((y - mean_y) ** 2 for y in series)
    ss_xy = sum((i - mean_x) * (y - mean_y) for i, y in enumerate(series))
    r2 = (ss_xy * ss_xy) / (ss_xx * ss_yy) if ss_xx > 0 and ss_yy > 0 else 0.0
    return {
        "n_points": n,
        "r2": round(r2, 4),
        "same_sign_share": round(same_sign_share, 4),
        "monotonic": monotonic,
    }


def _normalize_scope_tuple(scope: dict[str, Any] | None) -> tuple[tuple[str, str], ...]:
    if not scope:
        return ()
    return tuple(sorted((str(k).strip(), str(v).strip()) for k, v in scope.items()))


def _period_key_from_comparison(comparison: dict[str, Any] | None) -> str:
    if not comparison:
        return "all"
    if "from" in comparison and "to" in comparison:
        return f"{comparison['from']}..{comparison['to']}"
    if "period" in comparison and comparison["period"] is not None:
        return str(comparison["period"])
    return "all"


def _baseline_key_from_comparison(comparison: dict[str, Any] | None) -> str:
    if not comparison:
        return "none"
    if comparison.get("baseline") is not None:
        return str(comparison["baseline"])
    if comparison.get("from") is not None:
        return str(comparison["from"])
    if comparison.get("x_metric") is not None:
        return str(comparison["x_metric"])
    return "none"


@dataclass
class Fact:
    """Атомарный вычисленный факт в `FactLedger`.

    Все числа (`values`, `derived`), `predicate` и `allowed_title_ids`
    вычисляются кодом детерминированно."""

    id: str
    dataset_id: str
    metric: str
    aggregation: str = "sum"
    scope: dict[str, Any] = field(default_factory=dict)
    kind: str = "endpoint_change"
    predicate: str = ""
    comparison: dict[str, Any] = field(default_factory=dict)
    values: dict[str, Any] = field(default_factory=dict)
    derived: dict[str, Any] = field(default_factory=dict)
    unit: str | None = None
    source_refs: list[str] = field(default_factory=list)
    allowed_title_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    score: float = 0.0
    dataset_version: str = "v1"
    view_id: str = ""
    additivity: str = ""
    missing_ratio: float = 0.0
    semantic_confidence: float = 1.0
    coverage: float = 1.0
    score_components: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.scope = dict(self.scope or {})
        self.comparison = dict(self.comparison or {})
        self.values = dict(self.values or {})
        self.derived = dict(self.derived or {})
        self.warnings = list(self.warnings or [])
        self.unit = format_unit_ru(self.unit)

        if not self.additivity:
            self.additivity = infer_metric_additivity(
                self.metric,
                self.unit,
                role=self.comparison.get("metric_role"),
                explicit_additivity=self.values.get("additivity")
                or self.comparison.get("additivity"),
            )

        self._compute_derived_values()
        if not self.predicate:
            self.predicate = self._compute_predicate()
        if not self.source_refs:
            scope_part = ",".join(f"{k}={v}" for k, v in sorted(self.scope.items())) or "all"
            self.source_refs = [
                f"{self.dataset_id or 'dataset'}:{self.metric or 'metric'}:"
                f"{self.aggregation or 'agg'}:{scope_part}"
            ]
        if not self.allowed_title_ids:
            self.allowed_title_ids = allowed_title_ids_for_fact(self)

    def _compute_derived_values(self) -> None:
        v_from = self.values.get("from", self.values.get("start"))
        v_to = self.values.get("to", self.values.get("end"))
        if isinstance(v_from, (int, float)) and isinstance(v_to, (int, float)):
            f_val = float(v_from)
            t_val = float(v_to)
            if "delta" not in self.derived:
                self.derived["delta"] = round(t_val - f_val, 6)
            if f_val > 0 and t_val >= 0:
                if "pct" not in self.derived:
                    self.derived["pct"] = round((t_val - f_val) / f_val * 100.0, 4)
                if "ratio" not in self.derived and t_val > 0:
                    ratio = t_val / f_val if t_val >= f_val else f_val / t_val
                    self.derived["ratio"] = round(ratio, 4)

        series = self.values.get("series")
        if isinstance(series, (list, tuple)) and len(series) >= 2:
            clean_series = [float(x) for x in series if isinstance(x, (int, float))]
            if len(clean_series) >= 2:
                stats = _compute_series_stats(clean_series)
                self.values.setdefault("n_points", stats["n_points"])
                self.derived.setdefault("r2", stats["r2"])
                self.derived.setdefault("same_sign_share", stats["same_sign_share"])
                self.values.setdefault("monotonic_steps", stats["monotonic"])

        part = self.values.get("part", self.values.get("top"))
        total = self.values.get("total", self.values.get("sum"))
        if (
            isinstance(part, (int, float))
            and isinstance(total, (int, float))
            and float(total) > 0
            and float(part) >= 0
        ):
            self.derived.setdefault("share_pct", round(float(part) / float(total) * 100.0, 4))

        v_min = self.values.get("min")
        v_max = self.values.get("max")
        if isinstance(v_min, (int, float)) and isinstance(v_max, (int, float)):
            self.derived.setdefault("range", round(float(v_max) - float(v_min), 6))
            if float(v_min) > 0 and float(v_max) >= float(v_min):
                self.derived.setdefault("ratio", round(float(v_max) / float(v_min), 4))

        q1 = self.values.get("q1")
        q3 = self.values.get("q3")
        if isinstance(q1, (int, float)) and isinstance(q3, (int, float)):
            self.derived.setdefault("iqr", round(float(q3) - float(q1), 6))

    def _compute_predicate(self) -> str:
        kind = (self.kind or "").strip().casefold()
        v_from = self.values.get("from", self.values.get("start"))
        v_to = self.values.get("to", self.values.get("end"))
        cfg = load_insights_config()["validation"]
        flat_pct = float(cfg.get("flat_max_abs_pct", 5.0))

        if kind in ("strict_trend", "steady_trend", "endpoint_change", "trend", "change"):
            if isinstance(v_from, (int, float)) and isinstance(v_to, (int, float)):
                f_val = float(v_from)
                t_val = float(v_to)
                if f_val == 0:
                    if t_val > 0:
                        return "zero_base_increase"
                    if t_val < 0:
                        return "zero_base_decrease"
                    return "flat"
                if f_val < 0:
                    if f_val < t_val < 0:
                        return "loss_reduced"
                    if t_val < f_val < 0:
                        return "loss_deepened"
                    if t_val >= 0:
                        return "turnaround_to_positive"
                    return "flat"
                if f_val > 0 and t_val < 0:
                    return "turnaround_to_negative"

                pct = abs((t_val - f_val) / f_val * 100.0) if f_val > 0 else 0.0
                if pct <= flat_pct:
                    return "flat"

                missing_periods = int(
                    self.values.get("missing_periods", self.comparison.get("missing_periods", 0))
                    or 0
                )
                monotonic = bool(self.values.get("monotonic_steps", False))
                n_points = int(self.values.get("n_points", 2) or 2)
                r2 = float(self.derived.get("r2", 0.0) or 0.0)
                same_sign = float(self.derived.get("same_sign_share", 0.0) or 0.0)

                if kind == "strict_trend" or (monotonic and missing_periods == 0 and n_points >= 3):
                    return "every_month_increase" if t_val > f_val else "every_month_decrease"
                if kind == "steady_trend" or (
                    missing_periods == 0
                    and n_points >= int(cfg.get("steady_trend_min_points", 8))
                    and r2 >= float(cfg.get("steady_trend_min_r2", 0.70))
                    and same_sign >= float(cfg.get("steady_trend_min_same_sign_share", 0.70))
                ):
                    return "steady_increase" if t_val > f_val else "steady_decrease"
                return "increase" if t_val > f_val else "decrease"

        if kind in ("share", "composition", "part_to_whole"):
            sh = float(self.derived.get("share_pct", self.derived.get("pct", 0.0)) or 0.0)
            if sh >= 50.0:
                return "dominant_share"
            return "leader_share"
        if kind == "concentration":
            return "concentration"
        if kind in ("dispersion", "distribution", "spread"):
            return "numeric_dispersion"
        if kind in ("correlation", "relationship"):
            r = float(self.derived.get("r", self.values.get("r", 0.0)) or 0.0)
            return "no_correlation" if abs(r) < 0.25 else "correlation"
        if kind == "driver":
            return "driver"
        if kind == "bridge":
            return "bridge_change"
        return kind or "fact"

    @property
    def key(self) -> FactKey:
        """Канонический 7-элементный ключ факта:
        `(dataset_version, metric, aggregation, scope, period, baseline, unit)`."""
        return fact_key(self)

    def render_title(self, realization_id: str | None = None) -> str:
        """Сформировать заголовок по `realization_id`
        (или основному шаблону из `allowed_title_ids`)."""
        if realization_id is None:
            return default_title_for_fact(self)
        return render_title(realization_id, self)

    def to_dict(self) -> dict[str, Any]:
        """Сериализовать факт для паспорта прогона (`run.analysis.fact_ledger`)."""
        title = ""
        if self.allowed_title_ids:
            try:
                title = render_title(self.allowed_title_ids[0], self)
            except Exception:
                title = ""
        return {
            "id": self.id,
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "metric": self.metric,
            "aggregation": self.aggregation,
            "scope": dict(self.scope),
            "kind": self.kind,
            "predicate": self.predicate,
            "comparison": dict(self.comparison),
            "values": dict(self.values),
            "derived": dict(self.derived),
            "unit": self.unit,
            "source_refs": list(self.source_refs),
            "allowed_title_ids": list(self.allowed_title_ids),
            "warnings": list(self.warnings),
            "score": round(float(self.score), 4),
            "key": [
                self.key[0],
                self.key[1],
                self.key[2],
                [[k, v] for k, v in self.key[3]],
                self.key[4],
                self.key[5],
                self.key[6],
            ],
            "title": title,
        }


def fact_key(fact: Fact) -> FactKey:
    """Вычислить ключ факта
    `(dataset_version, metric, aggregation, scope, period, baseline, unit)`."""
    ds_ver = str(
        fact.dataset_version or fact.comparison.get("dataset_version") or fact.dataset_id or "v1"
    ).strip()
    metric_norm = (fact.metric or "").strip().casefold()
    agg_norm = (fact.aggregation or "sum").strip().casefold()
    scope_norm = _normalize_scope_tuple(fact.scope)
    period_norm = _period_key_from_comparison(fact.comparison)
    baseline_norm = _baseline_key_from_comparison(fact.comparison)
    unit_norm = format_unit_ru(fact.unit) or ""
    return (ds_ver, metric_norm, agg_norm, scope_norm, period_norm, baseline_norm, unit_norm)


def validate_fact(f: Fact) -> list[str]:
    """Жёсткая проверка допустимости факта (выжимка 19.10 §2).

    Возвращает список кодов/описаний ошибок. Если список непустой,
    факт получает `V(f) = 0`, не попадает в `FactLedger` и логируется в `warnings`."""
    errors: list[str] = []
    cfg = load_insights_config()["validation"]
    kind = (f.kind or "").strip().casefold()
    pred = (f.predicate or "").strip().casefold()
    vals = f.values or {}
    derived = f.derived or {}
    comp = f.comparison or {}
    agg = (f.aggregation or "").strip().casefold()
    additivity = (
        (
            f.additivity
            or vals.get("additivity")
            or comp.get("additivity")
            or infer_metric_additivity(f.metric, f.unit, role=comp.get("metric_role"))
        )
        .strip()
        .casefold()
    )

    # 0. Провенанс
    if not f.source_refs or any(not str(ref).strip() for ref in f.source_refs):
        errors.append("missing_source_refs: каждое число факта должно ссылаться на source_refs")

    # 1. Аддитивность и семантика метрики (§2.6)
    if additivity == "unknown":
        if agg not in ("none", "raw", "value", "as_is", "point", ""):
            errors.append(
                f"additivity_unknown_transformation_forbidden: метрика {f.metric!r} "
                f"с additivity='unknown' не допускает агрегацию/преобразование {agg!r}"
            )
        if (
            any(k in derived for k in ("pct", "ratio", "share_pct"))
            and comp.get("allow_unknown_derived") is not True
        ):
            errors.append(
                f"additivity_unknown_derived_forbidden: метрика {f.metric!r} "
                "с additivity='unknown' не допускает производные преобразования"
            )

    if additivity == "identifier" or comp.get("metric_role") == "identifier":
        if agg in ("sum", "mean", "avg", "median"):
            errors.append(
                f"additivity_identifier_aggregation_forbidden: идентификатор {f.metric!r} "
                f"нельзя агрегировать ({agg})"
            )

    is_rate_or_conversion = (
        additivity == "non_additive"
        or f.unit in ("%", "п.п.")
        or bool(_NON_ADDITIVE_NAME_RE.search(f.metric or ""))
    )
    if is_rate_or_conversion:
        if agg == "sum":
            errors.append(
                f"additivity_non_additive_sum_forbidden: неаддитивную метрику/долю/конверсию "
                f"{f.metric!r} нельзя суммировать"
            )
        agg_method = str(
            comp.get("aggregation_method") or vals.get("aggregation_method") or agg
        ).casefold()
        if (
            agg_method
            in (
                "mean",
                "avg",
                "mean_of_pct",
                "avg_of_pct",
                "mean_of_percentages",
                "simple_mean",
                "average_of_percentages",
            )
            and comp.get("equal_denominator_weights") is not True
        ):
            errors.append(
                f"additivity_conversion_mean_of_pct_forbidden: {f.metric!r} нельзя считать "
                "как среднее процентов; требуется отношение сумм числителя и знаменателя"
            )

    if additivity == "semi_additive" or bool(_SEMI_ADDITIVE_NAME_RE.search(f.metric or "")):
        if agg == "sum" and (
            comp.get("across_time") is True
            or comp.get("dimension_role") == "time"
            or comp.get("time_aggregation") == "sum"
        ):
            errors.append(
                "additivity_semi_additive_time_sum_forbidden: полуаддитивную метрику "
                f"(остаток/баланс) {f.metric!r} нельзя суммировать по времени"
            )

    if kind in ("driver", "bridge") or pred in ("driver", "bridge_change"):
        if additivity != "additive":
            errors.append(
                "driver_requires_additive_metric: декомпозиция вклада требует additive-метрику, "
                f"получено {additivity!r}"
            )
        claim_text = f"{pred} {vals.get('claim', '')} {comp.get('claim', '')}"
        if _CAUSAL_CLAIM_RE.search(claim_text):
            errors.append(
                "driver_causal_claim_forbidden: данные позволяют заявлять вклад в изменение, "
                "но не причинность"
            )

    # 2. Неполный последний период и несовместимость единиц/гранулярности (§2.7)
    if (
        comp.get("incomplete_last_period") is True or vals.get("incomplete_last_period") is True
    ) and not comp.get("incomplete_period_excluded", False):
        errors.append(
            "incomplete_last_period: сравнение включает неполный последний период "
            "без исключения или пометки"
        )

    from_grain = comp.get("from_grain")
    to_grain = comp.get("to_grain")
    if comp.get("grain_mismatch") is True or (
        from_grain and to_grain and str(from_grain) != str(to_grain)
    ):
        errors.append(
            "incompatible_period_grains: нельзя сравнивать периоды разной гранулярности "
            f"({from_grain} vs {to_grain})"
        )
    elif "from" in comp and "to" in comp:
        p_from = parse_period(str(comp["from"]))
        p_to = parse_period(str(comp["to"]))
        if p_from is not None and p_to is not None and p_from.grain != p_to.grain:
            errors.append(
                f"incompatible_period_grains: периоды {comp['from']!r} ({p_from.grain}) и "
                f"{comp['to']!r} ({p_to.grain}) имеют разную гранулярность"
            )

    from_unit = comp.get("from_unit")
    to_unit = comp.get("to_unit")
    if comp.get("unit_mismatch") is True or (
        from_unit and to_unit and format_unit_ru(str(from_unit)) != format_unit_ru(str(to_unit))
    ):
        errors.append(
            f"incompatible_units: несовместимые единицы измерения ({from_unit!r} vs {to_unit!r})"
        )

    # 3. Направление и база роста (>0 / =0 / <0) (§2.1, §2.2)
    v_from = vals.get("from", vals.get("start"))
    v_to = vals.get("to", vals.get("end"))
    if isinstance(v_from, (int, float)) and isinstance(v_to, (int, float)):
        f_val = float(v_from)
        t_val = float(v_to)
        delta = t_val - f_val

        if "delta" in derived and derived["delta"] is not None:
            if abs(float(derived["delta"]) - delta) > 1e-3:
                errors.append(
                    "derived_delta_mismatch: derived['delta']="
                    f"{derived['delta']} != to - from ({delta})"
                )

        if pred in _INCREASE_PREDICATES and t_val <= f_val:
            errors.append(
                f"direction_mismatch: предикат {pred!r} требует роста (to > from), "
                f"но from={f_val}, to={t_val}"
            )
        if pred in _DECREASE_PREDICATES and t_val >= f_val:
            errors.append(
                f"direction_mismatch: предикат {pred!r} требует снижения (to < from), "
                f"но from={f_val}, to={t_val}"
            )
        if pred == "flat" and f_val > 0:
            pct_abs = abs(delta / f_val) * 100.0
            if pct_abs > float(cfg.get("flat_max_abs_pct", 5.0)):
                errors.append(
                    f"direction_mismatch: предикат 'flat' несовместим с изменением {pct_abs:.1f}%"
                )

        # База = 0
        if f_val == 0:
            if derived.get("pct") is not None or derived.get("ratio") is not None:
                errors.append(
                    "zero_base_forbidden_pct_or_ratio: при нулевой базе (from = 0) "
                    "проценты и кратность запрещены"
                )
            if pred in (
                "increase",
                "decrease",
                "steady_increase",
                "steady_decrease",
                "every_month_increase",
                "every_month_decrease",
            ) and (
                derived.get("pct") is not None
                or any(tid in _PCT_OR_RATIO_TEMPLATES for tid in f.allowed_title_ids)
            ):
                errors.append(
                    "zero_base_invalid_predicate_or_template: при нулевой базе допустимо "
                    "только абсолютное изменение с нуля"
                )
            if any(tid in _PCT_OR_RATIO_TEMPLATES for tid in f.allowed_title_ids):
                errors.append(
                    "zero_base_invalid_title_template: шаблоны с %/кратностью запрещены "
                    f"при from = 0 ({f.allowed_title_ids})"
                )

        # База < 0 (отрицательные значения / убыток)
        if f_val < 0:
            if derived.get("pct") is not None or derived.get("ratio") is not None:
                errors.append(
                    "negative_base_forbidden_pct_or_ratio: при отрицательной базе (from < 0) "
                    "проценты и кратность запрещены"
                )
            if pred in (
                "increase",
                "growth",
                "decrease",
                "decline",
                "steady_increase",
                "steady_decrease",
                "every_month_increase",
                "every_month_decrease",
            ):
                errors.append(
                    f"negative_base_ambiguous_predicate: при from={f_val} < 0 "
                    f"предикат {pred!r} запрещён; используйте 'loss_reduced', "
                    "'loss_deepened' или 'turnaround_to_positive'"
                )
            if pred == "loss_reduced" and not (f_val < t_val < 0):
                errors.append(
                    "negative_base_direction_mismatch: 'loss_reduced' требует from < to < 0, "
                    f"получено {f_val} -> {t_val}"
                )
            if pred == "loss_deepened" and not (t_val < f_val < 0):
                errors.append(
                    "negative_base_direction_mismatch: 'loss_deepened' требует to < from < 0, "
                    f"получено {f_val} -> {t_val}"
                )
            if pred == "turnaround_to_positive" and not (f_val < 0 <= t_val):
                errors.append(
                    "negative_base_direction_mismatch: 'turnaround_to_positive' требует "
                    f"from < 0 <= to, получено {f_val} -> {t_val}"
                )
            if any(tid in _PCT_OR_RATIO_TEMPLATES for tid in f.allowed_title_ids):
                errors.append(
                    "negative_base_invalid_title_template: процентные/кратные шаблоны "
                    f"запрещены при from < 0 ({f.allowed_title_ids})"
                )

        # Смена знака с плюса на минус (from > 0 > to)
        if f_val > 0 and t_val < 0:
            if derived.get("pct") is not None or derived.get("ratio") is not None:
                errors.append(
                    "sign_change_forbidden_pct_or_ratio: при переходе из плюса в минус "
                    "проценты и кратность запрещены"
                )
            if pred in ("decrease", "decline", "steady_decrease", "every_month_decrease"):
                errors.append(
                    "sign_change_ambiguous_predicate: при переходе from > 0 > to "
                    "используйте 'turnaround_to_negative'"
                )
            if any(tid in _PCT_OR_RATIO_TEMPLATES for tid in f.allowed_title_ids):
                errors.append(
                    "sign_change_invalid_title_template: процентные/кратные шаблоны "
                    "запрещены при смене знака"
                )

        if pred == "turnaround_to_negative" and not (f_val > 0 > t_val):
            errors.append(
                "direction_mismatch: 'turnaround_to_negative' требует from > 0 > to, "
                f"получено {f_val} -> {t_val}"
            )

    # 4. Различие «выросло за период» / «устойчиво росло» / «каждый месяц» (§2.3)
    series = vals.get("series")
    periods_list = vals.get("periods", comp.get("periods"))
    missing_periods = int(vals.get("missing_periods", comp.get("missing_periods", 0)) or 0)
    if isinstance(periods_list, (list, tuple)) and len(periods_list) >= 2 and missing_periods == 0:
        parsed_p = [parse_period(str(p)) for p in periods_list]
        if all(p is not None and p.grain == "month" for p in parsed_p):
            months_idx = [p.key[0] * 12 + p.key[1] for p in parsed_p if p is not None]
            gaps = sum(
                max(0, months_idx[i + 1] - months_idx[i] - 1) for i in range(len(months_idx) - 1)
            )
            missing_periods = max(missing_periods, gaps)

    is_every_period_claim = (
        kind == "strict_trend"
        or pred
        in (
            "every_month_increase",
            "every_month_decrease",
            "every_period_increase",
            "every_period_decrease",
            "strict_increase",
            "strict_decrease",
        )
        or (
            bool(f.allowed_title_ids)
            and f.allowed_title_ids[0]
            in (
                "every_month_increase",
                "every_month_decrease",
                "every_period_increase",
                "every_period_decrease",
            )
        )
    )
    if is_every_period_claim:
        if missing_periods > 0:
            errors.append(
                "strict_trend_missing_periods: запрещено заявлять «каждый месяц/период» "
                f"при пропущенных периодах ({missing_periods})"
            )
        if isinstance(series, (list, tuple)) and len(series) >= 2:
            diffs = [float(series[i + 1]) - float(series[i]) for i in range(len(series) - 1)]
            if pred in ("every_month_increase", "every_period_increase", "strict_increase") and any(
                d <= 0 for d in diffs
            ):
                errors.append(
                    "strict_trend_non_monotonic: ряд не растёт в каждом периоде без исключений"
                )
            if pred in ("every_month_decrease", "every_period_decrease", "strict_decrease") and any(
                d >= 0 for d in diffs
            ):
                errors.append(
                    "strict_trend_non_monotonic: ряд не снижается в каждом периоде без исключений"
                )
        if vals.get("monotonic_steps") is False:
            errors.append("strict_trend_non_monotonic: monotonic_steps=False")
        same_sign = derived.get("same_sign_share", vals.get("same_sign_share"))
        if same_sign is not None and float(same_sign) < 1.0 - 1e-6:
            errors.append(
                f"strict_trend_non_monotonic: доля однонаправленных шагов {same_sign} < 1.0"
            )

    is_steady_claim = (
        kind == "steady_trend"
        or pred in ("steady_increase", "steady_decrease", "sustained_growth", "sustained_decline")
        or (
            bool(f.allowed_title_ids)
            and f.allowed_title_ids[0] in ("steady_increase", "steady_decrease")
        )
    )
    if is_steady_claim:
        if missing_periods > 0:
            errors.append(
                "steady_trend_missing_periods: запрещено заявлять «устойчиво росло/снижалось» "
                f"при пропусках периодов ({missing_periods})"
            )
        n_pts = vals.get("n_points", len(series) if isinstance(series, (list, tuple)) else None)
        min_pts = int(cfg.get("steady_trend_min_points", 8))
        if n_pts is not None and int(n_pts) < min_pts:
            errors.append(
                f"steady_trend_insufficient_points: для «устойчиво росло» нужно >= {min_pts} "
                f"точек, получено {n_pts}"
            )
        r2 = derived.get("r2", vals.get("r2"))
        min_r2 = float(cfg.get("steady_trend_min_r2", 0.70))
        if r2 is not None and float(r2) < min_r2:
            errors.append(
                f"steady_trend_low_r2: для «устойчиво росло» нужен R2 >= {min_r2}, получено {r2}"
            )
        same_sign = derived.get("same_sign_share", vals.get("same_sign_share"))
        min_share = float(cfg.get("steady_trend_min_same_sign_share", 0.70))
        if same_sign is not None and float(same_sign) < min_share:
            errors.append(
                f"steady_trend_low_consistency: доля шагов одного знака {same_sign} < {min_share}"
            )

    # 5. Доли / структура целого (§2.4)
    if kind in ("share", "composition", "part_to_whole", "concentration") or pred in (
        "dominant_share",
        "leader_share",
        "share",
        "concentration",
    ):
        same_denom = comp.get("same_denominator", vals.get("same_denominator", True))
        denominators = comp.get("denominators", vals.get("denominators"))
        if same_denom is False or (
            isinstance(denominators, (list, tuple, set)) and len(set(denominators)) > 1
        ):
            errors.append(
                "share_different_denominators: доли имеют разные знаменатели "
                "и не образуют части одного целого"
            )
        if comp.get("mutually_exclusive") is False or comp.get("overlapping_categories") is True:
            errors.append(
                "share_overlapping_categories: категории пересекаются, расчёт долей целого запрещён"
            )

        parts = vals.get("parts")
        part = vals.get("part", vals.get("top", vals.get("value")))
        total = vals.get("total", vals.get("sum"))
        tol_pct = float(cfg.get("share_sum_tolerance_pct", 3.0))

        if isinstance(parts, (list, tuple)) and parts:
            num_parts = [float(p) for p in parts if isinstance(p, (int, float))]
            if any(p < 0 for p in num_parts):
                errors.append(
                    "share_negative_parts: расчёт долей запрещён при наличии "
                    "отрицательных компонент"
                )
            parts_sum = sum(num_parts)
            if parts_sum <= 0:
                errors.append(
                    "share_non_positive_total: сумма частей должна быть строго положительной"
                )
            elif total is not None and float(total) > 0 and not vals.get("partial_parts", False):
                if abs(parts_sum - float(total)) / float(total) * 100.0 > tol_pct:
                    errors.append(
                        f"share_parts_total_mismatch: сумма частей ({parts_sum}) "
                        f"не сходится с итогом ({total})"
                    )
            if f.unit == "%" and vals.get("exhaustive_parts") is True:
                if abs(parts_sum - 100.0) > tol_pct:
                    errors.append(
                        f"share_percent_sum_not_100: сумма процентных долей ({parts_sum}%) "
                        "не равна 100%"
                    )

        if isinstance(part, (int, float)) and float(part) < 0:
            errors.append(f"share_negative_part: часть целого не может быть отрицательной ({part})")
        if isinstance(total, (int, float)):
            if float(total) <= 0:
                errors.append(
                    f"share_non_positive_total: знаменатель доли должен быть > 0 ({total})"
                )
            elif isinstance(part, (int, float)) and float(part) - float(total) > 1e-6:
                errors.append(
                    f"share_part_exceeds_total: часть ({part}) не может превышать целое ({total})"
                )

        sh_pct = derived.get("share_pct", derived.get("pct"))
        if isinstance(sh_pct, (int, float)) and (
            float(sh_pct) < -1e-6 or float(sh_pct) > 100.0 + 1e-4
        ):
            errors.append(
                "share_pct_out_of_bounds: доля в процентах должна лежать в [0, 100], "
                f"получено {sh_pct}"
            )

    # 6. Разброс / распределение — запрет субъективного «узкий/широкий разброс» (§2.5)
    subjective_dispersion_preds = {
        "narrow_dispersion",
        "wide_dispersion",
        "low_spread",
        "high_spread",
        "узкий_разброс",
        "широкий_разброс",
    }
    claim_str = str(vals.get("claim") or comp.get("claim") or "").casefold()
    if (
        pred in subjective_dispersion_preds
        or any(tid in subjective_dispersion_preds for tid in f.allowed_title_ids)
        or "узкий разброс" in claim_str
        or "широкий разброс" in claim_str
    ):
        errors.append(
            "dispersion_subjective_label_forbidden: субъективные ярлыки «узкий/широкий разброс» "
            "запрещены; допустимо только числовое описание диапазона/кратности/IQR"
        )

    v_min = vals.get("min")
    v_max = vals.get("max")
    if isinstance(v_min, (int, float)) and isinstance(v_max, (int, float)):
        min_f = float(v_min)
        max_f = float(v_max)
        if max_f < min_f:
            errors.append(f"dispersion_min_exceeds_max: min ({min_f}) > max ({max_f})")
        elif min_f > 0:
            spread_ratio = max_f / min_f
            forbid_ratio = float(cfg.get("narrow_dispersion_forbid_max_min_ratio", 10.0))
            if spread_ratio >= forbid_ratio and (
                pred in ("narrow_dispersion", "low_spread", "even", "uniform")
                or "узк" in claim_str
                or "равномерн" in claim_str
            ):
                errors.append(
                    f"dispersion_10x_spread_contradiction: при разбросе {spread_ratio:.1f}x "
                    f"(>= {forbid_ratio}x) запрещено заявлять узкий разброс или равномерность"
                )

    if kind in ("dispersion", "distribution", "spread"):
        has_range = isinstance(v_min, (int, float)) and isinstance(v_max, (int, float))
        has_iqr = isinstance(vals.get("median"), (int, float)) and (
            isinstance(derived.get("iqr"), (int, float))
            or (
                isinstance(vals.get("q1"), (int, float))
                and isinstance(vals.get("q3"), (int, float))
            )
        )
        if not (has_range or has_iqr):
            errors.append(
                "dispersion_missing_numeric_bounds: факт о разбросе обязан содержать "
                "числовые границы (min/max или медиана/IQR)"
            )

    # 7. Тривиальная зависимость и масштабированные дубли (revenue vs revenue_thousands) (§2.8)
    if comp.get("suppressed_duplicate_metric") is True:
        errors.append(
            f"trivial_duplicate_metric_scale: метрика {f.metric!r} является масштабированным "
            "дублем канонической метрики"
        )
    if kind in ("correlation", "relationship", "cross_table") or pred in (
        "correlation",
        "no_correlation",
        "cross_table_sync",
    ):
        x_m = str(comp.get("x_metric") or vals.get("x_metric") or "")
        y_m = str(comp.get("y_metric") or vals.get("y_metric") or f.metric or "")
        if x_m and y_m:
            if comp.get("trivial_scale_pair") is True or are_trivially_related_metrics(
                x_m,
                y_m,
                vals.get("x_series"),
                vals.get("y_series"),
            ):
                errors.append(
                    "trivial_metric_dependency: корреляция между масштабированными/"
                    f"производными версиями одной метрики ({x_m!r} vs {y_m!r}) подавлена"
                )

    return errors


def score_fact(f: Fact, *, config: dict[str, Any] | None = None) -> float:
    """Вычислить оценку факта `S(f) = V(f) * C(f) * (0.30*R + 0.25*I + 0.25*E + 0.20*U)`."""
    errors = validate_fact(f)
    if errors:
        f.score = 0.0
        f.score_components = {"V": 0.0, "C": 0.0, "R": 0.0, "I": 0.0, "E": 0.0, "U": 0.0}
        return 0.0

    cfg = config or load_insights_config()
    weights = cfg.get("ranking", _DEFAULT_INSIGHTS_CONFIG["ranking"])
    w_r = float(weights.get("relevance", 0.30))
    w_i = float(weights.get("impact", 0.25))
    w_e = float(weights.get("effect_size", 0.25))
    w_u = float(weights.get("unusualness", 0.20))

    comp_in = dict(f.score_components or {})
    v_gate = 1.0

    c_val = comp_in.get("C")
    if c_val is None:
        c_val = (
            (1.0 - max(0.0, min(1.0, float(f.missing_ratio))))
            * float(f.semantic_confidence)
            * float(f.coverage)
        )
    c_val = max(0.0, min(1.0, float(c_val)))

    r_val = comp_in.get("R")
    if r_val is None:
        r_val = _estimate_relevance(f)
    r_val = max(0.0, min(1.0, float(r_val)))

    i_val = comp_in.get("I")
    if i_val is None:
        i_val = _estimate_impact(f)
    i_val = max(0.0, min(1.0, float(i_val)))

    e_val = comp_in.get("E")
    if e_val is None:
        e_val = _estimate_effect_size(f)
    e_val = max(0.0, min(1.0, float(e_val)))

    u_val = comp_in.get("U")
    if u_val is None:
        u_val = _estimate_unusualness(f)
    u_val = max(0.0, min(1.0, float(u_val)))

    s_val = v_gate * c_val * (w_r * r_val + w_i * i_val + w_e * e_val + w_u * u_val)
    f.score = round(float(s_val), 4)
    f.score_components = {
        "V": round(v_gate, 4),
        "C": round(c_val, 4),
        "R": round(r_val, 4),
        "I": round(i_val, 4),
        "E": round(e_val, 4),
        "U": round(u_val, 4),
    }
    return f.score


def _estimate_relevance(f: Fact) -> float:
    kind = (f.kind or "").casefold()
    base = {
        "strict_trend": 0.90,
        "steady_trend": 0.88,
        "endpoint_change": 0.85,
        "trend": 0.85,
        "bridge": 0.88,
        "driver": 0.85,
        "share": 0.82,
        "composition": 0.82,
        "comparison": 0.80,
        "concentration": 0.78,
        "dispersion": 0.74,
        "correlation": 0.75,
        "cross_table": 0.80,
        "outlier": 0.72,
        "kpi": 0.65,
        "reference": 0.45,
        "missing": 0.50,
    }.get(kind, 0.70)
    if not f.scope:
        base = min(1.0, base + 0.05)
    return base


def _estimate_impact(f: Fact) -> float:
    vals = f.values or {}
    derived = f.derived or {}
    if "share_pct" in derived:
        return max(0.25, min(1.0, float(derived["share_pct"]) / 60.0))
    if "top3_pct" in derived:
        return max(0.30, min(1.0, float(derived["top3_pct"]) / 90.0))
    if not f.scope:
        return 0.85
    cov = vals.get("volume_share", f.coverage)
    return max(0.30, min(1.0, float(cov)))


def _estimate_effect_size(f: Fact) -> float:
    derived = f.derived or {}
    vals = f.values or {}
    if "pct" in derived and derived["pct"] is not None:
        return max(0.20, min(1.0, abs(float(derived["pct"])) / 50.0))
    if "ratio" in derived and derived["ratio"] is not None:
        return max(0.25, min(1.0, (float(derived["ratio"]) - 1.0) / 3.0))
    if "share_pct" in derived and derived["share_pct"] is not None:
        return max(0.25, min(1.0, float(derived["share_pct"]) / 50.0))
    if "r" in derived and derived["r"] is not None:
        return max(0.20, min(1.0, abs(float(derived["r"]))))
    if "delta" in derived and derived["delta"] is not None:
        v_to = abs(float(vals.get("to", vals.get("from", 1.0)) or 1.0))
        denom = max(v_to, abs(float(derived["delta"])), 1.0)
        return max(0.30, min(1.0, abs(float(derived["delta"])) / denom))
    return 0.60


def _estimate_unusualness(f: Fact) -> float:
    pred = (f.predicate or "").casefold()
    if pred in (
        "turnaround_to_positive",
        "turnaround_to_negative",
        "every_month_increase",
        "every_month_decrease",
        "acceleration",
        "deceleration",
        "outlier",
    ):
        return 0.85
    if pred in (
        "steady_increase",
        "steady_decrease",
        "loss_reduced",
        "loss_deepened",
        "dominant_share",
    ):
        return 0.75
    return 0.60


def fact_similarity(a: Fact, b: Fact, *, config: dict[str, Any] | None = None) -> float:
    """Структурная близость двух фактов (§3.2):
    `sim(a, b) = 0.35·same_metric + 0.25·same_scope + 0.20·same_period + 0.20·same_evidence`."""
    cfg = (
        (config or load_insights_config())
        .get("mmr", {})
        .get("similarity", _DEFAULT_INSIGHTS_CONFIG["mmr"]["similarity"])
    )
    w_metric = float(cfg.get("same_metric", 0.35))
    w_scope = float(cfg.get("same_scope", 0.25))
    w_period = float(cfg.get("same_period", 0.20))
    w_ev = float(cfg.get("same_evidence", 0.20))

    ma = canonical_metric_stem(a.metric)
    mb = canonical_metric_stem(b.metric)
    same_metric = 1.0 if (ma and ma == mb) else 0.0

    sa = _normalize_scope_tuple(a.scope)
    sb = _normalize_scope_tuple(b.scope)
    if sa == sb:
        same_scope = 1.0
    elif sa and sb:
        set_a, set_b = set(sa), set(sb)
        same_scope = len(set_a & set_b) / len(set_a | set_b)
    else:
        same_scope = 0.0

    pa = _period_key_from_comparison(a.comparison)
    pb = _period_key_from_comparison(b.comparison)
    same_period = 1.0 if pa == pb else 0.0

    refs_a = {str(r) for r in (a.source_refs or [])}
    refs_b = {str(r) for r in (b.source_refs or [])}
    if refs_a and refs_b:
        same_ev = len(refs_a & refs_b) / len(refs_a | refs_b)
    elif a.view_id and a.view_id == b.view_id:
        same_ev = 1.0
    else:
        same_ev = 0.0

    sim = w_metric * same_metric + w_scope * same_scope + w_period * same_period + w_ev * same_ev
    return round(max(0.0, min(1.0, sim)), 4)


def are_contradictory(a: Fact, b: Fact) -> bool:
    """Проверить, противоречат ли два факта друг другу (§3.3).

    Противоречие возникает ТОЛЬКО при одинаковом ключе
    `(dataset_version, metric, aggregation, scope, period, baseline, unit)`
    и несовместимых предикатах. Разные срезы (`scope`) — это контраст, а не противоречие."""
    if a.key != b.key:
        return False
    pa = (a.predicate or "").strip().casefold()
    pb = (b.predicate or "").strip().casefold()
    if pa == pb:
        return False
    if (pa in _POSITIVE_CHANGE_PREDICATES and pb in _NEGATIVE_CHANGE_PREDICATES) or (
        pb in _POSITIVE_CHANGE_PREDICATES and pa in _NEGATIVE_CHANGE_PREDICATES
    ):
        return True
    if (pa == "flat" and pb in (_POSITIVE_CHANGE_PREDICATES | _NEGATIVE_CHANGE_PREDICATES)) or (
        pb == "flat" and pa in (_POSITIVE_CHANGE_PREDICATES | _NEGATIVE_CHANGE_PREDICATES)
    ):
        return True
    if {pa, pb} == {"correlation", "no_correlation"}:
        return True
    return False


def is_contrast(a: Fact, b: Fact) -> bool:
    """Проверить, образуют ли два факта допустимый контраст срезов (§3.3):
    одна и та же метрика и период, но разный `scope` и противоположная динамика."""
    ka = a.key
    kb = b.key
    if ka[3] == kb[3]:
        return False
    if (ka[0], ka[1], ka[2], ka[4], ka[6]) != (kb[0], kb[1], kb[2], kb[4], kb[6]):
        return False
    pa = (a.predicate or "").strip().casefold()
    pb = (b.predicate or "").strip().casefold()
    return (pa in _POSITIVE_CHANGE_PREDICATES and pb in _NEGATIVE_CHANGE_PREDICATES) or (
        pb in _POSITIVE_CHANGE_PREDICATES and pa in _NEGATIVE_CHANGE_PREDICATES
    )


def mmr_select(
    facts: list[Fact],
    *,
    lambda_sim: float | None = None,
    min_gain: float | None = None,
    max_facts: int | None = None,
    config: dict[str, Any] | None = None,
) -> list[Fact]:
    """Жадный MMR-отбор фактов:
    `argmax(score(f) - lambda_sim * max_{s in Selected} sim(f, s))`,
    остановка при приросте `< min_gain` (`0.15`)."""
    cfg = (config or load_insights_config()).get("mmr", _DEFAULT_INSIGHTS_CONFIG["mmr"])
    lam = float(lambda_sim if lambda_sim is not None else cfg.get("lambda_sim", 0.30))
    stop_gain = float(min_gain if min_gain is not None else cfg.get("min_marginal_gain", 0.15))

    pool = [f for f in facts if f.score > 0.0]
    pool.sort(key=lambda x: (-x.score, x.id))
    selected: list[Fact] = []

    while pool:
        if max_facts is not None and len(selected) >= max_facts:
            break
        best_idx = -1
        best_gain = -1e9
        for idx, cand in enumerate(pool):
            if not selected:
                gain = cand.score
            else:
                max_s = max(fact_similarity(cand, s, config=config) for s in selected)
                # Если кандидат образует осмысленный контраст срезов с уже выбранным фактом,
                # не штрафуем его как дубликат одного и того же тезиса
                if any(is_contrast(cand, s) for s in selected):
                    max_s = min(max_s, 0.50)
                gain = cand.score - lam * max_s
            if gain > best_gain + 1e-9:
                best_gain = gain
                best_idx = idx
        if best_idx < 0 or best_gain < stop_gain:
            break
        selected.append(pool.pop(best_idx))

    return selected


class FactKeyIndex(dict[FactKey, Fact]):
    """Индекс `FactLedger.key`, работающий одновременно как:
    - словарь `ledger.key[fact_key] -> Fact`;
    - функция `ledger.key(fact_or_id) -> FactKey`."""

    def __init__(self, by_id: dict[str, Fact] | None = None) -> None:
        super().__init__()
        self._by_id: dict[str, Fact] = by_id if by_id is not None else {}

    def __call__(self, fact_or_id: Fact | str) -> FactKey:
        if isinstance(fact_or_id, Fact):
            return fact_key(fact_or_id)
        if isinstance(fact_or_id, str):
            if fact_or_id in self._by_id:
                return fact_key(self._by_id[fact_or_id])
            raise KeyError(f"Факт {fact_or_id!r} не найден в FactLedger")
        raise TypeError(f"Ожидался Fact или str, получено {type(fact_or_id)!r}")


@dataclass
class FactLedger:
    """Реестр вычисленных и проверенных фактов (`by_id`, `key`, `ranked`).

    Невалидные факты (`validate_fact(f) != []`) и тривиальные дубли масштаба
    (`revenue_thousands` при наличии `revenue`) в `by_id` и `ranked` не попадают,
    а причины отказа записываются в `warnings`."""

    by_id: dict[str, Fact] = field(default_factory=dict)
    key: FactKeyIndex = field(default_factory=FactKeyIndex)
    ranked: list[Fact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rejected: dict[str, list[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.key, FactKeyIndex):
            idx = FactKeyIndex(self.by_id)
            if isinstance(self.key, dict):
                idx.update(self.key)
            self.key = idx
        else:
            self.key._by_id = self.by_id

    @classmethod
    def from_facts(
        cls,
        facts: list[Fact],
        *,
        config: dict[str, Any] | None = None,
        lambda_sim: float | None = None,
        min_gain: float | None = None,
    ) -> FactLedger:
        cfg = config or load_insights_config()
        by_id: dict[str, Fact] = {}
        key_idx = FactKeyIndex(by_id)
        warnings: list[str] = []
        rejected: dict[str, list[str]] = {}

        # Канонические метрики для подавления масштабированных дублей (revenue vs revenue_thousands)
        canonical_metrics_by_stem: dict[tuple[str, str], str] = {}
        for f in facts:
            m_clean = (f.metric or "").strip()
            stem = canonical_metric_stem(m_clean)
            if stem and not _SCALE_SUFFIX_RE.search(m_clean):
                canonical_metrics_by_stem.setdefault((f.dataset_id, stem), m_clean)

        valid_candidates: list[Fact] = []
        for f in facts:
            m_clean = (f.metric or "").strip()
            stem = canonical_metric_stem(m_clean)
            canon_name = canonical_metrics_by_stem.get((f.dataset_id, stem))
            if (
                canon_name
                and canon_name.casefold() != m_clean.casefold()
                and are_rescaled_metric_names(canon_name, m_clean)
            ):
                reason = (
                    f"trivial_duplicate_metric_scale: метрика {m_clean!r} подавлена в пользу "
                    f"канонической метрики {canon_name!r}"
                )
                f.warnings.append(reason)
                rejected[f.id] = [reason]
                warnings.append(f"[{f.id}] {reason}")
                continue

            errs = validate_fact(f)
            if errs:
                f.score = 0.0
                f.warnings.extend(e for e in errs if e not in f.warnings)
                rejected[f.id] = list(errs)
                for err in errs:
                    warnings.append(f"[{f.id}] {err}")
                continue

            if f.score <= 0.0:
                score_fact(f, config=cfg)
            valid_candidates.append(f)

        # Разрешение противоречий при одинаковом ключе (оставляем факт с наибольшим score)
        valid_candidates.sort(key=lambda x: (-x.score, x.id))
        deduped_by_key: list[Fact] = []
        for cand in valid_candidates:
            k = cand.key
            existing = key_idx.get(k)
            if existing is not None:
                if are_contradictory(existing, cand):
                    msg = (
                        f"[{cand.id}] contradiction_suppressed: предикат {cand.predicate!r} "
                        f"противоречит {existing.predicate!r} ({existing.id!r}) "
                        f"при одинаковом ключе {k}"
                    )
                    warnings.append(msg)
                    rejected[cand.id] = [msg]
                elif cand.id not in by_id:
                    by_id[cand.id] = cand
                continue
            by_id[cand.id] = cand
            key_idx[k] = cand
            deduped_by_key.append(cand)

        ranked = mmr_select(
            deduped_by_key,
            lambda_sim=lambda_sim,
            min_gain=min_gain,
            config=cfg,
        )
        return cls(
            by_id=by_id,
            key=key_idx,
            ranked=ranked,
            warnings=warnings,
            rejected=rejected,
        )

    def get(self, fact_id: str) -> Fact | None:
        return self.by_id.get(fact_id)

    def __getitem__(self, fact_id: str) -> Fact:
        return self.by_id[fact_id]

    def __contains__(self, fact_id: object) -> bool:
        return fact_id in self.by_id

    def __len__(self) -> int:
        return len(self.by_id)

    def to_dict(self) -> dict[str, Any]:
        cfg = load_insights_config()
        return {
            "version": str(cfg.get("version", "1.0")),
            "by_id": {fid: f.to_dict() for fid, f in self.by_id.items()},
            "ranked_ids": [f.id for f in self.ranked],
            "ranked": [f.to_dict() for f in self.ranked],
            "warnings": list(self.warnings),
            "rejected": {fid: list(reasons) for fid, reasons in self.rejected.items()},
        }


def build_view_facts(
    view: Any,
    profile: TableProfile | None = None,
    *,
    dataset_version: str = "v1",
) -> list[Fact]:
    """Построить типизированные `Fact` по представлению `DataView` и профилю `TableProfile`."""
    import statistics

    facts: list[Fact] = []
    ds_id = getattr(view, "dataset_id", "") or getattr(profile, "dataset_id", "dataset")
    view_id = getattr(view, "id", getattr(view, "view_id", "view"))
    shape = getattr(view, "shape", getattr(view, "kind", ""))
    col_by_name: dict[str, ColumnProfile] = (
        {c.name: c for c in profile.columns}
        if profile and getattr(profile, "columns", None)
        else {}
    )
    col_by_label: dict[str, ColumnProfile] = (
        {c.label.casefold(): c for c in profile.columns}
        if profile and getattr(profile, "columns", None)
        else {}
    )
    incomplete_last = bool(getattr(profile, "incomplete_last_period", False))
    suppressed_set = {m.casefold() for m in (getattr(profile, "suppressed_metrics", None) or [])}

    ev_refs = list(getattr(view, "evidence", None) or [])
    if not ev_refs:
        ev_refs = [f"{ds_id}:{view_id}:rows"]

    def _resolve_col(name: str | None) -> ColumnProfile | None:
        if not name:
            return None
        if name in col_by_name:
            return col_by_name[name]
        return col_by_label.get(name.strip().casefold())

    if shape in ("series", "time_series", "delta_bridge"):
        series_list = getattr(view, "series", None) or []
        categories = list(getattr(view, "categories", None) or [])
        for si, s in enumerate(series_list[:5]):
            s_name = getattr(s, "name", None) or getattr(view, "title", "Показатель")
            s_vals = list(getattr(s, "values", None) or [])
            s_unit = format_unit_ru(getattr(s, "unit", None) or getattr(view, "unit", None))
            col = _resolve_col(s_name)
            additivity = getattr(col, "additivity", None) or infer_metric_additivity(s_name, s_unit)
            missing_ratio = float(col.missing_ratio) if col else 0.0
            non_null_idx = [idx for idx, v in enumerate(s_vals) if v is not None]
            if len(non_null_idx) < 2:
                continue
            i0, i1 = non_null_idx[0], non_null_idx[-1]
            v_from = float(s_vals[i0])
            v_to = float(s_vals[i1])
            window_vals = s_vals[i0 : i1 + 1]
            known_vals = [float(v) for v in window_vals if v is not None]
            missing_periods = sum(1 for v in window_vals if v is None)
            window_cats = categories[i0 : i1 + 1] if len(categories) > i1 else []
            if window_cats and missing_periods == 0:
                parsed_p = [parse_period(str(lbl)) for lbl in window_cats]
                if all(p is not None and p.grain == "month" for p in parsed_p):
                    m_idx = [p.key[0] * 12 + p.key[1] for p in parsed_p if p is not None]
                    missing_periods = sum(
                        max(0, m_idx[k + 1] - m_idx[k] - 1) for k in range(len(m_idx) - 1)
                    )

            stats = _compute_series_stats(known_vals)
            cfg_val = load_insights_config()["validation"]
            f_kind = "endpoint_change"
            if v_from > 0 and v_to > 0 and missing_periods == 0:
                if stats["monotonic"] and len(known_vals) >= 3:
                    f_kind = "strict_trend"
                elif (
                    stats["n_points"] >= int(cfg_val.get("steady_trend_min_points", 8))
                    and stats["r2"] >= float(cfg_val.get("steady_trend_min_r2", 0.70))
                    and stats["same_sign_share"]
                    >= float(cfg_val.get("steady_trend_min_same_sign_share", 0.70))
                ):
                    f_kind = "steady_trend"

            from_lbl = categories[i0] if i0 < len(categories) else str(i0)
            to_lbl = categories[i1] if i1 < len(categories) else str(i1)
            raw_agg = str(getattr(view, "aggregation", None) or "")
            agg = "sum" if "сумма" in raw_agg.casefold() else (raw_agg or "value")
            comp: dict[str, Any] = {
                "from": from_lbl,
                "to": to_lbl,
                "baseline": from_lbl,
                "missing_periods": missing_periods,
                "dimension_role": "time",
            }
            if incomplete_last:
                comp["incomplete_last_period"] = True
            if (
                col and col.name.casefold() in suppressed_set
            ) or s_name.casefold() in suppressed_set:
                comp["suppressed_duplicate_metric"] = True

            suffix = f"-s{si}" if si else ""
            facts.append(
                Fact(
                    id=f"{view_id}{suffix}-i0",
                    dataset_id=ds_id,
                    dataset_version=dataset_version,
                    view_id=view_id,
                    metric=s_name,
                    aggregation=agg,
                    scope={},
                    kind=f_kind,
                    comparison=comp,
                    values={
                        "from": v_from,
                        "to": v_to,
                        "series": known_vals,
                        "periods": window_cats,
                        "n_points": len(known_vals),
                        "missing_periods": missing_periods,
                        "monotonic_steps": stats["monotonic"],
                    },
                    unit=s_unit,
                    source_refs=list(ev_refs),
                    additivity=additivity,
                    missing_ratio=missing_ratio,
                )
            )

        primary_s = getattr(view, "metric", None)
        if primary_s is not None:
            s_vals = list(getattr(primary_s, "values", None) or [])
            s_unit = format_unit_ru(getattr(primary_s, "unit", None) or getattr(view, "unit", None))
            known_pairs = [(idx, float(v)) for idx, v in enumerate(s_vals) if v is not None]
            if len(known_pairs) >= 4:
                peak_idx, peak_val = max(known_pairs, key=lambda kv: kv[1])
                if peak_idx != known_pairs[-1][0]:
                    peak_lbl = categories[peak_idx] if peak_idx < len(categories) else str(peak_idx)
                    facts.append(
                        Fact(
                            id=f"{view_id}-i1",
                            dataset_id=ds_id,
                            dataset_version=dataset_version,
                            view_id=view_id,
                            metric=primary_s.name,
                            aggregation="value",
                            scope={},
                            kind="peak",
                            predicate="peak",
                            comparison={
                                "period": peak_lbl,
                                "baseline": categories[known_pairs[0][0]],
                            },
                            values={
                                "peak": peak_val,
                                "from": known_pairs[0][1],
                                "to": known_pairs[-1][1],
                            },
                            unit=s_unit,
                            source_refs=list(ev_refs),
                        )
                    )
            if len(known_pairs) >= 6 and known_pairs[0][1] > 0:
                mid = len(known_pairs) // 2
                first_h = known_pairs[:mid]
                second_h = known_pairs[mid:]
                if first_h[0][1] > 0 and second_h[0][1] > 0:
                    g1 = round((first_h[-1][1] / first_h[0][1] - 1.0) * 100.0, 2)
                    g2 = round((second_h[-1][1] / second_h[0][1] - 1.0) * 100.0, 2)
                    if g2 - g1 >= 10.0 and g2 > 0:
                        facts.append(
                            Fact(
                                id=f"{view_id}-i2",
                                dataset_id=ds_id,
                                dataset_version=dataset_version,
                                view_id=view_id,
                                metric=primary_s.name,
                                aggregation="value",
                                scope={},
                                kind="acceleration",
                                predicate="acceleration",
                                comparison={
                                    "from": categories[known_pairs[0][0]],
                                    "to": categories[known_pairs[-1][0]],
                                    "baseline": "first_half",
                                },
                                values={"g1_pct": g1, "g2_pct": g2},
                                derived={"g1_pct": g1, "g2_pct": g2},
                                unit=s_unit,
                                source_refs=list(ev_refs),
                            )
                        )
                    elif g1 - g2 >= 10.0:
                        facts.append(
                            Fact(
                                id=f"{view_id}-i2",
                                dataset_id=ds_id,
                                dataset_version=dataset_version,
                                view_id=view_id,
                                metric=primary_s.name,
                                aggregation="value",
                                scope={},
                                kind="deceleration",
                                predicate="deceleration",
                                comparison={
                                    "from": categories[known_pairs[0][0]],
                                    "to": categories[known_pairs[-1][0]],
                                    "baseline": "first_half",
                                },
                                values={"g1_pct": g1, "g2_pct": g2},
                                derived={"g1_pct": g1, "g2_pct": g2},
                                unit=s_unit,
                                source_refs=list(ev_refs),
                            )
                        )

    elif shape in ("categorical", "category_breakdown", "part_to_whole"):
        s = getattr(view, "metric", None)
        categories = list(getattr(view, "categories", None) or [])
        s_vals = list(getattr(s, "values", None) or []) if s else []
        s_name = getattr(s, "name", None) or getattr(view, "title", "Показатель")
        s_unit = format_unit_ru(getattr(s, "unit", None) or getattr(view, "unit", None))
        col = _resolve_col(s_name)
        additivity = getattr(col, "additivity", None) or infer_metric_additivity(s_name, s_unit)
        missing_ratio = float(col.missing_ratio) if col else 0.0
        pairs = [
            (str(c), float(v)) for c, v in zip(categories, s_vals, strict=False) if v is not None
        ]
        if getattr(view, "bridge", False) and len(pairs) >= 3:
            (start_cat, start_v), (end_cat, end_v) = pairs[0], pairs[-1]
            mids = pairs[1:-1]
            top_mid = max(mids, key=lambda kv: abs(kv[1]), default=None)
            facts.append(
                Fact(
                    id=f"{view_id}-i0",
                    dataset_id=ds_id,
                    dataset_version=dataset_version,
                    view_id=view_id,
                    metric=s_name,
                    aggregation="sum",
                    scope={},
                    kind="bridge",
                    predicate="bridge_change",
                    comparison={"from": start_cat, "to": end_cat, "baseline": start_cat},
                    values={
                        "from": start_v,
                        "to": end_v,
                        "driver": top_mid[0] if top_mid else None,
                    },
                    unit=s_unit,
                    source_refs=list(ev_refs),
                    additivity="additive",
                    missing_ratio=missing_ratio,
                )
            )
            if top_mid:
                dim_lbl = getattr(view, "category_label", None) or "factor"
                facts.append(
                    Fact(
                        id=f"{view_id}-i1",
                        dataset_id=ds_id,
                        dataset_version=dataset_version,
                        view_id=view_id,
                        metric=s_name,
                        aggregation="sum",
                        scope={dim_lbl: top_mid[0]},
                        kind="driver",
                        predicate="driver",
                        comparison={"baseline": start_cat},
                        values={"driver": top_mid[0], "contribution": top_mid[1]},
                        unit=s_unit,
                        source_refs=list(ev_refs),
                        additivity="additive",
                        missing_ratio=missing_ratio,
                    )
                )
        elif pairs:
            parts = [v for _, v in pairs]
            total = sum(parts)
            if total > 0 and all(p >= 0 for p in parts):
                pairs_sorted = sorted(pairs, key=lambda kv: -kv[1])
                leader, top_val = pairs_sorted[0]
                dim_lbl = getattr(view, "category_label", None) or "category"
                is_pct_share = getattr(s, "kind", "") == "percent" and 97.0 <= total <= 103.0
                share_pct = round(top_val if is_pct_share else (top_val / total * 100.0), 2)
                facts.append(
                    Fact(
                        id=f"{view_id}-i0",
                        dataset_id=ds_id,
                        dataset_version=dataset_version,
                        view_id=view_id,
                        metric=s_name,
                        aggregation="share" if is_pct_share else "sum",
                        scope={dim_lbl: leader},
                        kind="share",
                        predicate="dominant_share" if share_pct >= 50.0 else "leader_share",
                        comparison={"baseline": "total", "same_denominator": True},
                        values={
                            "leader": leader,
                            "dimension": dim_lbl,
                            "part": top_val,
                            "total": 100.0 if is_pct_share else total,
                            "parts": parts,
                            "exhaustive_parts": is_pct_share,
                        },
                        derived={"share_pct": share_pct},
                        unit="%" if is_pct_share else s_unit,
                        source_refs=list(ev_refs),
                        additivity="additive" if not is_pct_share else "non_additive",
                        missing_ratio=missing_ratio,
                    )
                )
                if len(pairs_sorted) >= 2 and pairs_sorted[1][1] > 0 and not is_pct_share:
                    second_lbl, second_val = pairs_sorted[1]
                    gap = round(top_val / second_val, 4)
                    if gap >= 1.2:
                        facts.append(
                            Fact(
                                id=f"{view_id}-i1",
                                dataset_id=ds_id,
                                dataset_version=dataset_version,
                                view_id=view_id,
                                metric=s_name,
                                aggregation="sum",
                                scope={dim_lbl: leader},
                                kind="comparison",
                                predicate="gap",
                                comparison={"baseline": second_lbl},
                                values={
                                    "leader": leader,
                                    "runner_up": second_lbl,
                                    "from": second_val,
                                    "to": top_val,
                                },
                                derived={"gap_ratio": gap, "ratio": gap},
                                unit=s_unit,
                                source_refs=list(ev_refs),
                                additivity="additive",
                                missing_ratio=missing_ratio,
                            )
                        )
                if len(pairs_sorted) >= 5 or (is_pct_share and len(pairs_sorted) >= 4):
                    top3_sum = sum(v for _, v in pairs_sorted[:3])
                    top3_pct = round(top3_sum if is_pct_share else (top3_sum / total * 100.0), 2)
                    facts.append(
                        Fact(
                            id=f"{view_id}-i2" if not is_pct_share else f"{view_id}-i1",
                            dataset_id=ds_id,
                            dataset_version=dataset_version,
                            view_id=view_id,
                            metric=s_name,
                            aggregation="share" if is_pct_share else "sum",
                            scope={dim_lbl: "top3"},
                            kind="concentration",
                            predicate="concentration",
                            comparison={"baseline": "total", "same_denominator": True},
                            values={
                                "dimension": dim_lbl,
                                "part": top3_sum,
                                "total": 100.0 if is_pct_share else total,
                            },
                            derived={"top3_pct": top3_pct, "share_pct": top3_pct},
                            unit="%" if is_pct_share else s_unit,
                            source_refs=list(ev_refs),
                            additivity="additive" if not is_pct_share else "non_additive",
                            missing_ratio=missing_ratio,
                        )
                    )

    elif shape == "distribution":
        raw_vals = sorted(
            float(v) for v in (getattr(view, "raw_values", None) or []) if v is not None
        )
        s = getattr(view, "metric", None)
        s_name = getattr(s, "name", None) or getattr(view, "title", "Показатель")
        s_unit = format_unit_ru(getattr(view, "unit", None))
        if len(raw_vals) >= 5:
            q1, med, q3 = statistics.quantiles(raw_vals, n=4)
            iqr = q3 - q1
            facts.append(
                Fact(
                    id=f"{view_id}-i0",
                    dataset_id=ds_id,
                    dataset_version=dataset_version,
                    view_id=view_id,
                    metric=s_name,
                    aggregation="distribution",
                    scope={},
                    kind="dispersion",
                    predicate="numeric_dispersion",
                    comparison={"baseline": "range"},
                    values={
                        "min": raw_vals[0],
                        "max": raw_vals[-1],
                        "median": med,
                        "q1": q1,
                        "q3": q3,
                    },
                    derived={"iqr": round(iqr, 6)},
                    unit=s_unit,
                    source_refs=list(ev_refs),
                )
            )
            outliers = [v for v in raw_vals if v > q3 + 1.5 * iqr or v < q1 - 1.5 * iqr]
            if outliers:
                facts.append(
                    Fact(
                        id=f"{view_id}-i1",
                        dataset_id=ds_id,
                        dataset_version=dataset_version,
                        view_id=view_id,
                        metric=s_name,
                        aggregation="distribution",
                        scope={},
                        kind="outlier",
                        predicate="outlier",
                        comparison={"baseline": "iqr_1.5"},
                        values={"outlier_count": len(outliers), "max": raw_vals[-1]},
                        unit=s_unit,
                        source_refs=list(ev_refs),
                    )
                )

    elif shape in ("relationship", "correlation"):
        xs = [float(x) for x in (getattr(view, "x_values", None) or []) if x is not None]
        s = getattr(view, "metric", None)
        ys = [float(y) for y in (getattr(s, "values", None) or []) if y is not None] if s else []
        n = min(len(xs), len(ys))
        if n >= 3:
            mx = sum(xs[:n]) / n
            my = sum(ys[:n]) / n
            sxx = sum((x - mx) ** 2 for x in xs[:n])
            syy = sum((y - my) ** 2 for y in ys[:n])
            sxy = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
            if sxx > 0 and syy > 0:
                r_val = round(sxy / math.sqrt(sxx * syy), 4)
                x_lbl = getattr(view, "x_label", None) or "X"
                y_lbl = getattr(s, "name", None) or getattr(view, "title", "Y")
                facts.append(
                    Fact(
                        id=f"{view_id}-i0",
                        dataset_id=ds_id,
                        dataset_version=dataset_version,
                        view_id=view_id,
                        metric=y_lbl,
                        aggregation="correlation",
                        scope={},
                        kind="correlation",
                        predicate="correlation"
                        if abs(r_val) >= 0.5 and n >= 10
                        else "no_correlation",
                        comparison={
                            "x_metric": x_lbl,
                            "y_metric": y_lbl,
                            "baseline": x_lbl,
                        },
                        values={
                            "r": r_val,
                            "n": n,
                            "x_metric": x_lbl,
                            "y_metric": y_lbl,
                            "x_series": xs[:n],
                            "y_series": ys[:n],
                        },
                        derived={"r": r_val},
                        unit=format_unit_ru(getattr(view, "unit", None)),
                        source_refs=list(ev_refs),
                    )
                )

    elif shape in ("kpi", "kpi_summary"):
        s = getattr(view, "metric", None)
        cats = list(getattr(view, "categories", None) or [])
        s_vals = list(getattr(s, "values", None) or []) if s else []
        units = list(getattr(view, "kpi_units", None) or [getattr(view, "unit", None)] * len(cats))
        for k, (lbl, val) in enumerate(zip(cats, s_vals, strict=False)):
            if val is None:
                continue
            u = format_unit_ru(units[k] if k < len(units) else None)
            facts.append(
                Fact(
                    id=f"{view_id}-i{k}",
                    dataset_id=ds_id,
                    dataset_version=dataset_version,
                    view_id=view_id,
                    metric=str(lbl),
                    aggregation="value",
                    scope={},
                    kind="kpi",
                    predicate="kpi",
                    comparison={"baseline": "summary"},
                    values={"label": str(lbl), "value": float(val)},
                    unit=u,
                    source_refs=list(ev_refs),
                    additivity=infer_metric_additivity(str(lbl), u),
                )
            )

    elif shape == "reference" and getattr(view, "primary_hint", False):
        n_rows = int(getattr(view, "source_rows", 0) or 0)
        top_n = min(6, n_rows)
        facts.append(
            Fact(
                id=f"{view_id}-i0",
                dataset_id=ds_id,
                dataset_version=dataset_version,
                view_id=view_id,
                metric=str(getattr(view, "title", "Справочник")),
                aggregation="none",
                scope={},
                kind="reference",
                predicate="reference",
                comparison={"baseline": "top_n"},
                values={
                    "title": str(getattr(view, "title", "Справочник")),
                    "top_n": top_n,
                    "total_rows": n_rows,
                },
                source_refs=list(ev_refs),
                additivity="non_additive",
            )
        )

    return facts


def build_dataset_fact_ledger(
    profile: TableProfile,
    views: list[Any],
    *,
    dataset_version: str = "v1",
    extra_facts: list[Fact] | None = None,
) -> FactLedger:
    """Собрать FactLedger таблицы по профилю и представлениям (DataView/ViewAnalysis)."""
    candidates: list[Fact] = []
    for item in views:
        v = getattr(item, "view", item)
        candidates.extend(build_view_facts(v, profile, dataset_version=dataset_version))
    if extra_facts:
        candidates.extend(extra_facts)
    ledger = FactLedger.from_facts(candidates)
    for supp in getattr(profile, "suppressed_metrics", None) or []:
        msg = (
            f"trivial_duplicate_metric_scale: колонка {supp!r} подавлена как масштабированный дубль"
        )
        if msg not in ledger.warnings:
            ledger.warnings.append(msg)
    return ledger


def build_pack_fact_ledger(
    datasets: list[DatasetAnalysis],
    *,
    extra_facts: list[Fact] | None = None,
) -> FactLedger:
    """Собрать единый `FactLedger` по всем таблицам пакета данных."""
    all_facts: list[Fact] = []
    inherited_warnings: list[str] = []
    inherited_rejected: dict[str, list[str]] = {}
    for ds in datasets:
        ds_ledger = getattr(ds, "fact_ledger", None)
        if isinstance(ds_ledger, FactLedger):
            all_facts.extend(ds_ledger.by_id.values())
            inherited_warnings.extend(ds_ledger.warnings)
            inherited_rejected.update(ds_ledger.rejected)
        else:
            for va in ds.views:
                v = getattr(va, "view", va)
                all_facts.extend(build_view_facts(v, ds.profile))
    if extra_facts:
        all_facts.extend(extra_facts)
    ledger = FactLedger.from_facts(all_facts)
    for w in inherited_warnings:
        if w not in ledger.warnings:
            ledger.warnings.append(w)
    for fid, errs in inherited_rejected.items():
        ledger.rejected.setdefault(fid, errs)
    return ledger


__all__ = [
    "Fact",
    "FactKey",
    "FactKeyIndex",
    "FactLedger",
    "are_contradictory",
    "are_rescaled_metric_names",
    "are_trivially_related_metrics",
    "build_dataset_fact_ledger",
    "build_pack_fact_ledger",
    "build_view_facts",
    "canonical_metric_stem",
    "fact_key",
    "fact_similarity",
    "infer_metric_additivity",
    "is_contrast",
    "load_insights_config",
    "mmr_select",
    "score_fact",
    "validate_fact",
]
