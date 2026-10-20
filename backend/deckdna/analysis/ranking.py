"""Детерминированное ранжирование фактов (Calliope / DataShot / LIDA / Draco).

Каждому кандидату из ``view_insights`` считается композитная оценка по четырём
осям с весами из ``configs/analysis.default.yaml``:
- ``significance`` — величина эффекта (темп изменения, доля лидера, разрыв, |r|,
  доля выбросов);
- ``unusualness`` — отклонение от равномерного или плоского базового уровня;
- ``intent_match`` — совпадение с аналитической задачей и намерением из брифа
  (``task.intent_of``);
- ``coverage`` — доля строк/суммы/периодов, которую объясняет факт.

На слайд выводится лучший факт представления; второй (сопутствующий) факт
добавляется только тогда, когда он описывает другое измерение, другой ряд или
ортогональный аспект структуры."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from deckdna.analysis.task import TaskResult, intent_of
from deckdna.analysis.views import (
    CATEGORICAL,
    DISTRIBUTION,
    KPI,
    RELATIONSHIP,
    SERIES,
    DataView,
)
from deckdna.contracts.dataset import Insight

DEFAULT_ANALYSIS_CONFIG_PATH = (
    Path(__file__).resolve().parents[3] / "configs" / "analysis.default.yaml"
)


@dataclass(frozen=True)
class InsightWeights:
    significance: float = 0.45
    unusualness: float = 0.20
    intent_match: float = 0.20
    coverage: float = 0.15


@dataclass(frozen=True)
class InsightThresholds:
    wide_spread_max_min_ratio: float = 3.0
    wide_spread_cv: float = 0.50
    wide_spread_range_to_median: float = 1.0
    leader_min_share_pct: float = 25.0
    leader_relative_dominance: float = 2.0
    leader_runner_up_ratio: float = 1.25
    leader_gap_ratio: float = 1.20
    correlation_min_r: float = 0.50
    cross_table_min_r: float = 0.60
    cross_table_min_overlap: int = 4
    reference_min_rows: int = 200
    missing_min_ratio: float = 0.01
    number_tolerance_rel: float = 0.02


@dataclass(frozen=True)
class AnalysisConfig:
    version: str = "1.0"
    weights: InsightWeights = field(default_factory=InsightWeights)
    thresholds: InsightThresholds = field(default_factory=InsightThresholds)
    intent_fact_types: dict[str, tuple[str, ...]] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InsightScore:
    significance: float
    unusualness: float
    intent_match: float
    coverage: float
    total: float

    def to_dict(self) -> dict[str, float]:
        return {
            "significance": round(self.significance, 3),
            "unusualness": round(self.unusualness, 3),
            "intent_match": round(self.intent_match, 3),
            "coverage": round(self.coverage, 3),
            "total": round(self.total, 3),
        }


_DEFAULT_INTENT_MAP: dict[str, tuple[str, ...]] = {
    "trend": ("growth", "decline", "acceleration", "deceleration", "peak", "flat"),
    "comparison": ("leader", "gap", "driver", "even"),
    "composition": ("leader", "concentration", "tail", "even", "share"),
    "distribution": ("wide_spread", "spread", "outlier"),
    "relationship": ("correlation", "no_correlation", "cross_table"),
    "reference": ("reference",),
    "kpi": ("kpi",),
}


@lru_cache(maxsize=4)
def _load_cached(resolved: str) -> AnalysisConfig:
    path = Path(resolved)
    if not path.exists():
        return AnalysisConfig(intent_fact_types=dict(_DEFAULT_INTENT_MAP))
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    w_raw = data.get("weights") or {}
    t_raw = data.get("thresholds") or {}
    i_raw = data.get("intent_fact_types") or _DEFAULT_INTENT_MAP
    weights = InsightWeights(
        significance=float(w_raw.get("significance", 0.45)),
        unusualness=float(w_raw.get("unusualness", 0.20)),
        intent_match=float(w_raw.get("intent_match", 0.20)),
        coverage=float(w_raw.get("coverage", 0.15)),
    )
    thresholds = InsightThresholds(
        wide_spread_max_min_ratio=float(t_raw.get("wide_spread_max_min_ratio", 3.0)),
        wide_spread_cv=float(t_raw.get("wide_spread_cv", 0.50)),
        wide_spread_range_to_median=float(t_raw.get("wide_spread_range_to_median", 1.0)),
        leader_min_share_pct=float(t_raw.get("leader_min_share_pct", 25.0)),
        leader_relative_dominance=float(t_raw.get("leader_relative_dominance", 2.0)),
        leader_runner_up_ratio=float(t_raw.get("leader_runner_up_ratio", 1.25)),
        leader_gap_ratio=float(t_raw.get("leader_gap_ratio", 1.20)),
        correlation_min_r=float(t_raw.get("correlation_min_r", 0.50)),
        cross_table_min_r=float(t_raw.get("cross_table_min_r", 0.60)),
        cross_table_min_overlap=int(t_raw.get("cross_table_min_overlap", 4)),
        reference_min_rows=int(t_raw.get("reference_min_rows", 200)),
        missing_min_ratio=float(t_raw.get("missing_min_ratio", 0.01)),
        number_tolerance_rel=float(t_raw.get("number_tolerance_rel", 0.02)),
    )
    intent_map = {str(k): tuple(str(x) for x in v) for k, v in i_raw.items()}
    return AnalysisConfig(
        version=str(data.get("version", "1.0")),
        weights=weights,
        thresholds=thresholds,
        intent_fact_types=intent_map,
        raw=data,
    )


def load_analysis_config(path: Path | None = None) -> AnalysisConfig:
    target = path if path is not None else DEFAULT_ANALYSIS_CONFIG_PATH
    return _load_cached(str(target.resolve()))


def _clamp(val: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, val))


def _unusualness(insight: Insight, view: DataView) -> float:
    ftype = insight.fact_type or ""
    if ftype in ("even", "flat", "no_correlation"):
        return 0.15
    if ftype in ("growth", "decline"):
        return _clamp(insight.strength)
    if ftype == "leader":
        vals = [v for v in (view.metric.values if view.metric else []) if v is not None and v >= 0]
        total = sum(vals)
        n = len(vals)
        if total > 0 and n >= 2:
            top_share = max(vals) / total
            expected = 1.0 / n
            rel = _clamp((top_share - expected) / max(1.0 - expected, 0.25) * 1.4)
            return max(0.65, rel)
        return max(0.65, _clamp(insight.strength))
    if ftype == "concentration":
        vals = sorted(
            (v for v in (view.metric.values if view.metric else []) if v is not None and v >= 0),
            reverse=True,
        )
        total = sum(vals)
        n = len(vals)
        if total > 0 and n >= 4:
            top3_share = sum(vals[:3]) / total
            expected = min(1.0, 3.0 / n)
            return _clamp((top3_share - expected) / max(1.0 - expected, 0.2) * 1.1, 0.35, 0.68)
        return 0.50
    if ftype == "gap":
        return _clamp(insight.strength)
    if ftype == "wide_spread":
        return 0.78
    if ftype == "spread":
        return 0.55
    if ftype == "outlier":
        n = max(len(view.raw_values), 1)
        try:
            k = int(insight.numbers[0])
            return _clamp(0.45 + min(0.35, k / max(n * 0.15, 1)))
        except (ValueError, IndexError):
            return 0.5
    if ftype in ("correlation", "cross_table"):
        return _clamp(insight.strength)
    if ftype in ("acceleration", "deceleration", "driver"):
        return 0.55
    if ftype == "peak":
        return 0.45
    return 0.40


def _coverage(insight: Insight, view: DataView) -> float:
    ftype = insight.fact_type or ""
    if ftype in (
        "growth",
        "decline",
        "flat",
        "even",
        "spread",
        "wide_spread",
        "correlation",
        "no_correlation",
        "cross_table",
        "reference",
    ):
        return 1.0
    if ftype in ("acceleration", "deceleration", "driver"):
        return 0.60
    if ftype == "leader":
        return 0.90
    if ftype == "concentration":
        vals = sorted(
            (v for v in (view.metric.values if view.metric else []) if v is not None and v >= 0),
            reverse=True,
        )
        total = sum(vals)
        if total > 0 and vals:
            return _clamp(sum(vals[:3]) / total, 0.4, 0.75)
        return 0.65
    if ftype == "gap":
        n = max(view.n_points, 2)
        return _clamp(2.0 / n + 0.15)
    if ftype in ("peak", "tail"):
        n = max(view.n_points, 2)
        return _clamp(1.0 / n + 0.1)
    if ftype == "outlier":
        n = max(len(view.raw_values), 1)
        try:
            k = int(insight.numbers[0])
            return _clamp(k / n + 0.2, 0.15, 0.45)
        except (ValueError, IndexError):
            return 0.25
    return 0.50


def _intent_match(
    insight: Insight,
    view: DataView,
    task: TaskResult | None,
    intent_text: str | None,
    config: AnalysisConfig,
) -> float:
    ftype = insight.fact_type or ""
    explicit_intent = (task.intent if task else None) or intent_of(intent_text)
    active_task = task.task if task else None
    allowed_explicit = config.intent_fact_types.get(explicit_intent or "", ())
    allowed_task = config.intent_fact_types.get(active_task or "", ())
    if explicit_intent and ftype in allowed_explicit:
        # Прямое попадание в цель брифа («показать рост», «сравнить регионы», «доли»)
        if ftype in ("growth", "decline", "leader", "correlation", "wide_spread", "spread"):
            return 1.0
        return 0.85
    if active_task and ftype in allowed_task:
        primary_types = (
            "growth",
            "decline",
            "leader",
            "correlation",
            "wide_spread",
            "spread",
            "reference",
        )
        if ftype in primary_types:
            return 0.85
        return 0.65
    return 0.35


def score_insight(
    insight: Insight,
    view: DataView,
    task: TaskResult | None = None,
    intent_text: str | None = None,
    config: AnalysisConfig | None = None,
) -> InsightScore:
    cfg = config or load_analysis_config()
    w = cfg.weights
    sig = _clamp(insight.strength)
    unusual = _unusualness(insight, view)
    intent_m = _intent_match(insight, view, task, intent_text, cfg)
    cov = _coverage(insight, view)
    total = (
        w.significance * sig
        + w.unusualness * unusual
        + w.intent_match * intent_m
        + w.coverage * cov
    )
    # Производный срез «доля от итога» (derived=True): на первом слайде уже
    # назван лидер, поэтому на втором слайде важнее концентрация Топ-3.
    if getattr(view, "derived", False):
        if insight.fact_type == "concentration":
            total = min(1.0, total + 0.22)
        elif insight.fact_type == "leader":
            total = max(0.1, total - 0.12)
    return InsightScore(
        significance=sig,
        unusualness=unusual,
        intent_match=intent_m,
        coverage=cov,
        total=_clamp(total, 0.05, 1.0),
    )


def rank_view_insights(
    insights: list[Insight],
    view: DataView,
    task: TaskResult | None = None,
    intent_text: str | None = None,
    config: AnalysisConfig | None = None,
) -> list[Insight]:
    """Пересчитать ``strength`` по четырём критериям и упорядочить кандидатов."""
    if not insights:
        return []
    if view.shape == KPI:
        return list(insights)
    cfg = config or load_analysis_config()
    scored: list[tuple[int, Insight]] = []
    for idx, ins in enumerate(insights):
        sc = score_insight(ins, view, task=task, intent_text=intent_text, config=cfg)
        scored.append((idx, ins.model_copy(update={"strength": round(sc.total, 3)})))
    scored.sort(key=lambda pair: (not pair[1].confirmed, -pair[1].strength, pair[0]))
    return [ins for _, ins in scored]


def insight_dimension(insight: Insight, view: DataView | None = None) -> str:
    """Измерение или предмет факта: для отсева дублирующих выводов на одном слайде."""
    ftype = insight.fact_type or ""
    if view is not None and view.shape == SERIES:
        # В мультирядном представлении id вида «...-s1-i0» хранит индекс ряда
        if "-s" in insight.id:
            return f"series:{insight.id.split('-s', 1)[1].split('-', 1)[0]}"
        return "series:0"
    if view is not None and view.shape == CATEGORICAL:
        if getattr(view, "bridge", False):
            return "bridge:total" if ftype in ("growth", "decline") else "bridge:driver"
        if ftype == "leader":
            return f"cat:leader:{view.category_label.casefold()}"
        if ftype == "gap":
            return f"cat:gap:{view.category_label.casefold()}"
        if ftype == "even":
            return f"cat:even:{view.category_label.casefold()}"
        if ftype == "concentration":
            return f"cat:top3:{view.category_label.casefold()}"
        if ftype == "tail":
            return f"cat:tail:{view.category_label.casefold()}"
    if view is not None and view.shape == DISTRIBUTION:
        return "dist:spread" if ftype in ("spread", "wide_spread") else f"dist:{ftype}"
    if view is not None and view.shape == RELATIONSHIP:
        return f"rel:{ftype}"
    return f"{insight.view_id or insight.dataset_id}:{ftype}"


def select_slide_insights(
    insights: list[Insight],
    view: DataView | None = None,
    *,
    max_supporting: int = 1,
) -> tuple[Insight | None, list[Insight]]:
    """На слайд — лучший факт; второй — только если он про другое измерение/аспект."""
    if not insights:
        return None, []
    ordered = sorted(insights, key=lambda i: (not i.confirmed, -i.strength))
    top = ordered[0]
    if max_supporting <= 0:
        return top, []
    used_dims = {insight_dimension(top, view)}
    used_nums = set(top.numbers)
    supporting: list[Insight] = []
    for cand in ordered[1:]:
        if len(supporting) >= max_supporting:
            break
        dim = insight_dimension(cand, view)
        if dim in used_dims:
            continue
        if cand.numbers and set(cand.numbers) <= used_nums:
            continue
        used_dims.add(dim)
        used_nums |= set(cand.numbers)
        supporting.append(cand)
    return top, supporting


__all__ = [
    "DEFAULT_ANALYSIS_CONFIG_PATH",
    "AnalysisConfig",
    "InsightScore",
    "InsightThresholds",
    "InsightWeights",
    "insight_dimension",
    "load_analysis_config",
    "rank_view_insights",
    "score_insight",
    "select_slide_insights",
]
