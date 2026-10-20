"""Регрессионные и модульные тесты для FactLedger и проверок валидации допустимости (§1–§3).

Проверяемые случаи:
1. Отрицательные значения (loss reduced/deepened, turnaround, запрет % при базе < 0).
2. Нулевая база (рост с нуля, запрет % и кратности при базе = 0, zero_base_increase_abs).
3. Пропущенные месяцы (запрет «устойчиво росло» и «каждый месяц» при пропусках).
4. Доли с разными знаменателями (запрет разных знаменателей, пересечений и частей < 0).
5. 10-кратный разброс (10x spread: запрет узкого разброса/равномерности, только числа).
6. Конверсия как среднее процентов (запрещено: additivity=non_additive, отношение сумм).
7. Revenue vs revenue_thousands (подавление масштабированных дублей).
8. Аддитивность unknown (запрет скрытых агрегаций и производных величин).
9. Неполный последний период (incomplete_last_period).
10. Ключ факта (7-элементный кортеж) и двусторонний индекс FactKeyIndex.
11. Противоречие при одинаковом ключе vs контраст срезов (разный scope).
12. Формула ранжирования S = V·C·(0.30R + 0.25I + 0.25E + 0.20U) и MMR-дедуп.
13. Шаблоны заголовков title_templates: русский формат («23%», «млн ₽», «с ноября по декабрь»).
14. Сквозная приёмка: analysis_report на stage2 содержит fact_ledger с source_refs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from deckdna.analysis.explain import analysis_report
from deckdna.analysis.fact_ledger import (
    Fact,
    FactLedger,
    are_contradictory,
    are_rescaled_metric_names,
    are_trivially_related_metrics,
    fact_key,
    is_contrast,
    load_insights_config,
    mmr_select,
    score_fact,
    validate_fact,
)
from deckdna.analysis.pipeline import analyze_dataset
from deckdna.analysis.title_templates import (
    format_period_span_ru,
    format_unit_ru,
    render_title,
)
from deckdna.ingestion.content_parsers import parse_file

STAGE2 = Path(__file__).resolve().parents[2] / "examples" / "stage2"


# ---------------------------------------------------------------------------
# 1. Отрицательные значения
# ---------------------------------------------------------------------------


def test_negative_values_loss_reduced_deepened_and_turnaround() -> None:
    """Отрицательная база: сокращение/рост убытка и смена знака, запрет % и кратности."""
    # 1.1 Сокращение убытка: from = -100, to = -80
    f_red = Fact(
        id="F_neg_red",
        dataset_id="D1",
        metric="profit",
        aggregation="sum",
        values={"from": -100, "to": -80},
        unit="млн руб.",
        source_refs=["test.xlsx:A1:B10"],
    )
    assert f_red.predicate == "loss_reduced"
    assert f_red.allowed_title_ids == ["loss_reduced"]
    assert "pct" not in f_red.derived
    assert "ratio" not in f_red.derived
    assert validate_fact(f_red) == []
    title = f_red.render_title()
    assert "сократился на 20 млн ₽" in title
    assert "с -100 до -80 млн ₽" in title

    # 1.2 Увеличение убытка: from = -80, to = -120
    f_deep = Fact(
        id="F_neg_deep",
        dataset_id="D1",
        metric="profit",
        aggregation="sum",
        values={"from": -80, "to": -120},
        unit="млн руб.",
        source_refs=["test.xlsx:A1:B10"],
    )
    assert f_deep.predicate == "loss_deepened"
    assert f_deep.allowed_title_ids == ["loss_deepened"]
    assert validate_fact(f_deep) == []
    assert "увеличился на 40 млн ₽" in f_deep.render_title()

    # 1.3 Выход из минуса в плюс: from = -50, to = 25
    f_turn_pos = Fact(
        id="F_turn_pos",
        dataset_id="D1",
        metric="ebitda",
        aggregation="sum",
        values={"from": -50, "to": 25},
        unit="млн руб.",
        source_refs=["test.xlsx:A1:B10"],
    )
    assert f_turn_pos.predicate == "turnaround_to_positive"
    assert validate_fact(f_turn_pos) == []
    assert "в плюс" in f_turn_pos.render_title()

    # 1.4 Уход из плюса в минус: from = 50, to = -20
    f_turn_neg = Fact(
        id="F_turn_neg",
        dataset_id="D1",
        metric="profit",
        aggregation="sum",
        values={"from": 50, "to": -20},
        unit="млн руб.",
        source_refs=["test.xlsx:A1:B10"],
    )
    assert f_turn_neg.predicate == "turnaround_to_negative"
    assert validate_fact(f_turn_neg) == []
    assert "ушла в минус" in f_turn_neg.render_title()

    # 1.5 Нарушение: расчёт процентов или кратности при отрицательной базе
    f_bad_pct = Fact(
        id="F_bad_pct",
        dataset_id="D1",
        metric="profit",
        aggregation="sum",
        predicate="increase",
        values={"from": -100, "to": -80},
        derived={"pct": 20.0},
        allowed_title_ids=["increase_pct"],
        unit="млн руб.",
        source_refs=["test.xlsx:A1:B10"],
    )
    errs = validate_fact(f_bad_pct)
    assert any("negative_base_forbidden_pct_or_ratio" in e for e in errs)
    assert any("negative_base_ambiguous_predicate" in e for e in errs)
    assert any("negative_base_invalid_title_template" in e for e in errs)

    # 1.6 Попытка отрендерить increase_pct с отрицательной базой должна падать
    with pytest.raises(ValueError, match="запрещён при неположительной базе"):
        render_title("increase_pct", f_bad_pct)


# ---------------------------------------------------------------------------
# 2. Нулевая база
# ---------------------------------------------------------------------------


def test_zero_base_valid_absolute_growth_and_forbidden_pct() -> None:
    """Нулевая база: допустим только абсолютный рост с нуля; % и кратность запрещены."""
    # 2.1 Корректный факт с нуля: from = 0, to = 50
    f_zero = Fact(
        id="F_zero_good",
        dataset_id="D1",
        metric="sales",
        aggregation="sum",
        values={"from": 0, "to": 50},
        unit="млн руб.",
        source_refs=["test.xlsx:A1:B10"],
    )
    assert f_zero.predicate == "zero_base_increase"
    assert "pct" not in f_zero.derived
    assert "ratio" not in f_zero.derived
    assert f_zero.allowed_title_ids == ["zero_base_increase_abs", "increase_abs"]
    assert validate_fact(f_zero) == []
    assert f_zero.render_title() == "Продажи: рост с нуля на 50 млн ₽ за период (до 50 млн ₽)"

    # 2.2 Нарушение: при from = 0 подсунули pct или кратный шаблон
    f_zero_bad = Fact(
        id="F_zero_bad",
        dataset_id="D1",
        metric="sales",
        aggregation="sum",
        predicate="increase",
        values={"from": 0, "to": 50},
        derived={"pct": 5000.0, "ratio": 50.0},
        allowed_title_ids=["increase_pct"],
        unit="млн руб.",
        source_refs=["test.xlsx:A1:B10"],
    )
    errs = validate_fact(f_zero_bad)
    assert any("zero_base_forbidden_pct_or_ratio" in e for e in errs)
    assert any("zero_base_invalid_predicate_or_template" in e for e in errs)
    assert any("zero_base_invalid_title_template" in e for e in errs)

    with pytest.raises(ValueError, match="запрещён при неположительной базе"):
        render_title("increase_pct", f_zero_bad)


# ---------------------------------------------------------------------------
# 3. Пропущенные месяцы
# ---------------------------------------------------------------------------


def test_missing_months_forbidden_steady_and_strict_trend() -> None:
    """Пропущенные месяцы: запрет утверждений «каждый месяц» и «устойчиво росло»."""
    # 3.1 Явный missing_periods > 0 блокирует «каждый месяц»
    f_strict_gap = Fact(
        id="F_strict_gap",
        dataset_id="D1",
        metric="sales",
        kind="strict_trend",
        predicate="every_month_increase",
        values={
            "from": 10,
            "to": 40,
            "series": [10, 20, 30, 40],
            "missing_periods": 1,
            "monotonic_steps": True,
        },
        allowed_title_ids=["every_month_increase"],
        source_refs=["test.xlsx:A1:B10"],
    )
    errs = validate_fact(f_strict_gap)
    assert any("strict_trend_missing_periods" in e for e in errs)

    # 3.2 Автодетект пропуска по списку месяцев: 2025-01, 2025-02, 2025-04 (пропущен март)
    f_auto_gap = Fact(
        id="F_auto_gap",
        dataset_id="D1",
        metric="sales",
        kind="strict_trend",
        predicate="every_month_increase",
        values={
            "from": 10,
            "to": 30,
            "periods": ["2025-01", "2025-02", "2025-04"],
            "series": [10, 20, 30],
            "monotonic_steps": True,
        },
        allowed_title_ids=["every_month_increase"],
        source_refs=["test.xlsx:A1:B10"],
    )
    errs_auto = validate_fact(f_auto_gap)
    assert any("strict_trend_missing_periods" in e for e in errs_auto)

    # 3.3 «Устойчиво росло» требует >= 8 точек, R2 >= 0.70, 70% шагов и отсутствие пропусков
    f_steady_gap = Fact(
        id="F_steady_gap",
        dataset_id="D1",
        metric="sales",
        kind="steady_trend",
        predicate="steady_increase",
        values={"from": 10, "to": 90, "n_points": 8, "missing_periods": 2},
        derived={"r2": 0.85, "same_sign_share": 0.8},
        allowed_title_ids=["steady_increase"],
        source_refs=["test.xlsx:A1:B10"],
    )
    errs_steady = validate_fact(f_steady_gap)
    assert any("steady_trend_missing_periods" in e for e in errs_steady)

    # При недостатке точек (5 < 8) или низком R2 — отдельная ошибка
    f_steady_short = Fact(
        id="F_steady_short",
        dataset_id="D1",
        metric="sales",
        kind="steady_trend",
        predicate="steady_increase",
        values={"from": 10, "to": 90, "n_points": 5, "missing_periods": 0},
        derived={"r2": 0.60, "same_sign_share": 0.6},
        allowed_title_ids=["steady_increase"],
        source_refs=["test.xlsx:A1:B10"],
    )
    errs_short = validate_fact(f_steady_short)
    assert any("steady_trend_insufficient_points" in e for e in errs_short)
    assert any("steady_trend_low_r2" in e for e in errs_short)
    assert any("steady_trend_low_consistency" in e for e in errs_short)


# ---------------------------------------------------------------------------
# 4. Доли с разными знаменателями
# ---------------------------------------------------------------------------


def test_shares_with_different_denominators_and_overlapping_categories() -> None:
    """Доли: запрет разных знаменателей, пересекающихся категорий и отрицательных частей."""
    # 4.1 Разные знаменатели
    f_denom = Fact(
        id="F_diff_denom",
        dataset_id="D1",
        metric="sales",
        kind="share",
        predicate="leader_share",
        comparison={"same_denominator": False, "denominators": [1000, 5000]},
        values={"part": 300, "total": 1000},
        source_refs=["test.xlsx:A1:B10"],
    )
    errs_denom = validate_fact(f_denom)
    assert any("share_different_denominators" in e for e in errs_denom)

    # 4.2 Пересекающиеся категории
    f_overlap = Fact(
        id="F_overlap",
        dataset_id="D1",
        metric="sales",
        kind="share",
        predicate="leader_share",
        comparison={"mutually_exclusive": False, "overlapping_categories": True},
        values={"part": 300, "total": 1000},
        source_refs=["test.xlsx:A1:B10"],
    )
    errs_overlap = validate_fact(f_overlap)
    assert any("share_overlapping_categories" in e for e in errs_overlap)

    # 4.3 Отрицательные части целого
    f_neg_parts = Fact(
        id="F_neg_parts",
        dataset_id="D1",
        metric="sales",
        kind="share",
        predicate="leader_share",
        values={"parts": [100, -20, 50], "total": 130},
        source_refs=["test.xlsx:A1:B10"],
    )
    errs_parts = validate_fact(f_neg_parts)
    assert any("share_negative_parts" in e for e in errs_parts)

    # 4.4 Несхождение суммы частей с итогом (> 3%)
    f_mismatch = Fact(
        id="F_mismatch",
        dataset_id="D1",
        metric="sales",
        kind="share",
        predicate="leader_share",
        values={"parts": [50, 40, 30], "total": 200},  # sum = 120 != 200
        source_refs=["test.xlsx:A1:B10"],
    )
    errs_mismatch = validate_fact(f_mismatch)
    assert any("share_parts_total_mismatch" in e for e in errs_mismatch)

    # 4.5 Сумма исчерпывающих процентных долей не равна 100%
    f_pct_not_100 = Fact(
        id="F_pct_not_100",
        dataset_id="D1",
        metric="sales",
        kind="share",
        predicate="leader_share",
        unit="%",
        values={"parts": [40, 40], "exhaustive_parts": True, "total": 100},
        source_refs=["test.xlsx:A1:B10"],
    )
    errs_pct = validate_fact(f_pct_not_100)
    assert any("share_percent_sum_not_100" in e for e in errs_pct)


# ---------------------------------------------------------------------------
# 5. 10-кратный разброс
# ---------------------------------------------------------------------------


def test_tenfold_spread_forbids_narrow_dispersion_label() -> None:
    """10-кратный разброс (max/min >= 10): запрет «узкий разброс», только числовое описание."""
    # 5.1 Нарушение: при разбросе 5..50 (10x) заявляется «узкий разброс»
    f_10x_bad = Fact(
        id="F_10x_bad",
        dataset_id="D1",
        metric="order_value",
        kind="dispersion",
        predicate="narrow_dispersion",
        values={"min": 5, "max": 50, "median": 20, "q1": 10, "q3": 30},
        allowed_title_ids=["dispersion_range"],
        source_refs=["orders.csv:amount"],
    )
    errs = validate_fact(f_10x_bad)
    assert any("dispersion_subjective_label_forbidden" in e for e in errs)
    assert any("dispersion_10x_spread_contradiction" in e for e in errs)

    # 5.2 Корректный факт: детерминированное числовое описание (кратность и границы)
    f_10x_good = Fact(
        id="F_10x_good",
        dataset_id="D1",
        metric="order_value",
        kind="dispersion",
        predicate="numeric_dispersion",
        values={"min": 5, "max": 50, "median": 20, "q1": 10, "q3": 30},
        unit="млн руб.",
        source_refs=["orders.csv:amount"],
    )
    assert validate_fact(f_10x_good) == []
    assert f_10x_good.allowed_title_ids[0] == "dispersion_ratio"
    title = f_10x_good.render_title("dispersion_ratio")
    assert title == "Сумма заказа: значения различаются в 10 раз, от 5 до 50 млн ₽"

    # Шаблон по медиане и IQR
    title_iqr = f_10x_good.render_title("dispersion_median_iqr")
    assert "медиана 20 млн ₽" in title_iqr
    assert "от 10 до 30 млн ₽" in title_iqr


# ---------------------------------------------------------------------------
# 6. Конверсия как среднее процентов (запрещено)
# ---------------------------------------------------------------------------


def test_conversion_as_mean_of_percentages_is_forbidden() -> None:
    """Конверсия/доля: запрет вычисления через среднее процентов и запрет sum."""
    # 6.1 Неаддитивная метрика с агрегацией mean_of_percentages
    f_conv_mean = Fact(
        id="F_conv_mean",
        dataset_id="D1",
        metric="conversion",
        aggregation="avg",
        comparison={"aggregation_method": "mean_of_percentages"},
        values={"from": 5.0, "to": 8.0},
        unit="%",
        source_refs=["funnel.xlsx:conv"],
    )
    errs_mean = validate_fact(f_conv_mean)
    assert any("additivity_conversion_mean_of_pct_forbidden" in e for e in errs_mean)

    # 6.2 Запрет суммирования конверсии
    f_conv_sum = Fact(
        id="F_conv_sum",
        dataset_id="D1",
        metric="конверсия",
        aggregation="sum",
        values={"from": 5.0, "to": 8.0},
        unit="%",
        source_refs=["funnel.xlsx:conv"],
    )
    errs_sum = validate_fact(f_conv_sum)
    assert any("additivity_non_additive_sum_forbidden" in e for e in errs_sum)

    # 6.3 Корректно: отношение сумм числителя и знаменателя
    f_conv_ok = Fact(
        id="F_conv_ok",
        dataset_id="D1",
        metric="conversion",
        aggregation="value",
        comparison={"aggregation_method": "ratio_of_sums"},
        values={"from": 5.0, "to": 8.0},
        unit="%",
        source_refs=["funnel.xlsx:clicks,orders"],
    )
    assert validate_fact(f_conv_ok) == []


# ---------------------------------------------------------------------------
# 7. Revenue vs revenue_thousands (подавлено)
# ---------------------------------------------------------------------------


def test_revenue_vs_revenue_thousands_suppression() -> None:
    """Масштабированные дубли (revenue vs revenue_thousands): выявление и подавление."""
    assert are_rescaled_metric_names("revenue", "revenue_thousands")
    assert are_rescaled_metric_names("выручка", "выручка_тыс")
    assert are_rescaled_metric_names("sales_rub", "sales_mln")
    assert not are_rescaled_metric_names("revenue", "profit")

    # Детекция по линейному масштабированию числового ряда (x1000)
    series_a = [100.0, 200.0, 300.0, 400.0]
    series_b = [100000.0, 200000.0, 300000.0, 400000.0]
    assert are_trivially_related_metrics("metric_a", "metric_b", series_a, series_b)

    f_rev = Fact(
        id="F_rev",
        dataset_id="D1",
        metric="revenue",
        aggregation="sum",
        values={"from": 100, "to": 150},
        unit="млн руб.",
        source_refs=["sales.csv:rev"],
    )
    f_rev_thous = Fact(
        id="F_rev_thous",
        dataset_id="D1",
        metric="revenue_thousands",
        aggregation="sum",
        values={"from": 100000, "to": 150000},
        unit="тыс. руб.",
        source_refs=["sales.csv:rev_thous"],
    )

    ledger = FactLedger.from_facts([f_rev, f_rev_thous])
    assert "F_rev" in ledger.by_id
    assert "F_rev_thous" not in ledger.by_id
    assert [f.id for f in ledger.ranked] == ["F_rev"]
    assert "F_rev_thous" in ledger.rejected
    assert any("trivial_duplicate_metric_scale" in r for r in ledger.rejected["F_rev_thous"])
    assert any("[F_rev_thous] trivial_duplicate_metric_scale" in w for w in ledger.warnings)

    # Подавление тривиальной корреляции между ними
    f_corr_trivial = Fact(
        id="F_corr_triv",
        dataset_id="D1",
        metric="revenue_thousands",
        kind="correlation",
        predicate="correlation",
        comparison={
            "x_metric": "revenue",
            "y_metric": "revenue_thousands",
            "trivial_scale_pair": True,
        },
        values={"r": 1.0, "n": 24},
        source_refs=["sales.csv"],
    )
    errs_corr = validate_fact(f_corr_trivial)
    assert any("trivial_metric_dependency" in e for e in errs_corr)


# ---------------------------------------------------------------------------
# 8. Аддитивность unknown и semi_additive
# ---------------------------------------------------------------------------


def test_additivity_unknown_and_semi_additive_rules() -> None:
    """Метрика unknown не допускает агрегацию; остатки не суммируются по времени."""
    # Unknown метрика не допускает sum
    f_unk = Fact(
        id="F_unk",
        dataset_id="D1",
        metric="custom_score",
        aggregation="sum",
        additivity="unknown",
        values={"from": 10, "to": 20},
        source_refs=["score.csv:val"],
    )
    errs_unk = validate_fact(f_unk)
    assert any("additivity_unknown_transformation_forbidden" in e for e in errs_unk)

    # Полуаддитивная метрика (остаток/склад) не суммируется по времени
    f_stock = Fact(
        id="F_stock",
        dataset_id="D1",
        metric="остаток на складе",
        aggregation="sum",
        comparison={"across_time": True, "time_aggregation": "sum"},
        values={"from": 100, "to": 120},
        source_refs=["inventory.csv"],
    )
    errs_stock = validate_fact(f_stock)
    assert any("additivity_semi_additive_time_sum_forbidden" in e for e in errs_stock)

    # Идентификатор не суммируется
    f_id = Fact(
        id="F_id",
        dataset_id="D1",
        metric="client_id",
        aggregation="sum",
        comparison={"metric_role": "identifier"},
        values={"from": 1, "to": 10},
        source_refs=["clients.csv:id"],
    )
    assert any("additivity_identifier_aggregation_forbidden" in e for e in validate_fact(f_id))


# ---------------------------------------------------------------------------
# 9. Неполный последний период
# ---------------------------------------------------------------------------


def test_incomplete_last_period_unmarked_comparison_rejected() -> None:
    """Сравнение с неполным последним периодом без явного исключения/пометки бракуется."""
    f_incomp = Fact(
        id="F_incomp",
        dataset_id="D1",
        metric="sales",
        aggregation="sum",
        comparison={"from": "2025-01", "to": "2025-10*", "incomplete_last_period": True},
        values={"from": 100, "to": 40},
        source_refs=["sales.csv"],
    )
    errs = validate_fact(f_incomp)
    assert any("incomplete_last_period" in e for e in errs)

    # С явным флагом исключения/оговорки проверка проходит
    f_incomp_ok = Fact(
        id="F_incomp_ok",
        dataset_id="D1",
        metric="sales",
        aggregation="sum",
        comparison={
            "from": "2025-01",
            "to": "2025-09",
            "incomplete_last_period": True,
            "incomplete_period_excluded": True,
        },
        values={"from": 100, "to": 150},
        source_refs=["sales.csv"],
    )
    assert validate_fact(f_incomp_ok) == []


# ---------------------------------------------------------------------------
# 10. Ключ факта и индекс FactKeyIndex
# ---------------------------------------------------------------------------


def test_fact_key_structure_and_index() -> None:
    """Ключ: (dataset_version, metric, aggregation, scope, period, baseline, unit) и индекс."""
    f = Fact(
        id="F_key_test",
        dataset_id="D1",
        dataset_version="v2",
        metric="Revenue",
        aggregation="Sum",
        scope={"region": "North", "channel": "Retail"},
        comparison={"from": "2025-01", "to": "2025-06", "baseline": "2025-01"},
        values={"from": 100, "to": 150},
        unit="млн руб.",
        source_refs=["sales.xlsx"],
    )
    expected_key = (
        "v2",
        "revenue",
        "sum",
        (("channel", "Retail"), ("region", "North")),
        "2025-01..2025-06",
        "2025-01",
        "млн ₽",
    )
    assert f.key == expected_key
    assert fact_key(f) == expected_key

    ledger = FactLedger.from_facts([f])
    assert ledger.key[expected_key].id == "F_key_test"
    assert ledger.key(f) == expected_key
    assert ledger.key("F_key_test") == expected_key


# ---------------------------------------------------------------------------
# 11. Противоречие при одинаковом ключе vs контраст срезов
# ---------------------------------------------------------------------------


def test_contradiction_suppression_vs_slice_contrast() -> None:
    """Несовместимые предикаты при одинаковом ключе — противоречие; разные срезы — контраст."""
    # 11.1 Противоречие: один и тот же ключ, один говорит рост, другой спад
    f_inc = Fact(
        id="F_inc",
        dataset_id="D1",
        metric="sales",
        aggregation="sum",
        values={"from": 100, "to": 130},
        unit="млн руб.",
        source_refs=["ref1"],
    )
    f_dec = Fact(
        id="F_dec",
        dataset_id="D1",
        metric="sales",
        aggregation="sum",
        values={"from": 130, "to": 100},
        unit="млн руб.",
        source_refs=["ref2"],
    )
    assert are_contradictory(f_inc, f_dec)
    ledger = FactLedger.from_facts([f_inc, f_dec])
    assert len(ledger) == 1
    assert "F_inc" in ledger.by_id
    assert "F_dec" in ledger.rejected
    assert any("contradiction_suppressed" in w for w in ledger.warnings)

    # 11.2 Контраст срезов: одна метрика и период, но разный scope (Север растёт, Юг падает)
    f_north = Fact(
        id="F_north",
        dataset_id="D1",
        metric="sales",
        aggregation="sum",
        scope={"region": "North"},
        comparison={"from": "2025-01", "to": "2025-12"},
        values={"from": 100, "to": 140},
        unit="млн руб.",
        source_refs=["ref1"],
    )
    f_south = Fact(
        id="F_south",
        dataset_id="D1",
        metric="sales",
        aggregation="sum",
        scope={"region": "South"},
        comparison={"from": "2025-01", "to": "2025-12"},
        values={"from": 100, "to": 70},
        unit="млн руб.",
        source_refs=["ref2"],
    )
    assert not are_contradictory(f_north, f_south)
    assert is_contrast(f_north, f_south)
    ledger_contrast = FactLedger.from_facts([f_north, f_south])
    assert "F_north" in ledger_contrast.by_id
    assert "F_south" in ledger_contrast.by_id
    assert len(ledger_contrast.ranked) == 2


# ---------------------------------------------------------------------------
# 12. Ранжирование и MMR-дедуп
# ---------------------------------------------------------------------------


def test_ranking_formula_and_mmr_selection() -> None:
    """S = V·C·(0.30R + 0.25I + 0.25E + 0.20U); MMR отбирает сильнейшие без дублей."""
    cfg = load_insights_config()
    assert cfg["ranking"]["relevance"] == 0.30
    assert cfg["ranking"]["impact"] == 0.25
    assert cfg["ranking"]["effect_size"] == 0.25
    assert cfg["ranking"]["unusualness"] == 0.20

    f_sales = Fact(
        id="F_main_sales",
        dataset_id="D1",
        metric="sales",
        aggregation="sum",
        values={"from": 100, "to": 140},
        unit="млн руб.",
        source_refs=["ref1"],
    )
    f_sales_near_dup = Fact(
        id="F_dup_sales",
        dataset_id="D1",
        metric="sales",
        aggregation="sum",
        values={"from": 100, "to": 120},
        unit="млн руб.",
        source_refs=["ref1"],
    )
    f_profit = Fact(
        id="F_other_profit",
        dataset_id="D1",
        metric="profit",
        aggregation="sum",
        values={"from": 20, "to": 45},
        unit="млн руб.",
        source_refs=["ref2"],
    )

    s1 = score_fact(f_sales)
    s2 = score_fact(f_sales_near_dup)
    s3 = score_fact(f_profit)
    assert s1 > s2 > 0.0 and s3 > 0.0

    selected = mmr_select([f_sales, f_sales_near_dup, f_profit])
    # Выбираются две разные метрики (sales и profit), а не дублирующий sales
    assert {f.id for f in selected[:2]} == {"F_main_sales", "F_other_profit"}
    assert selected[2].id == "F_dup_sales"


# ---------------------------------------------------------------------------
# 13. Шаблоны заголовков title_templates
# ---------------------------------------------------------------------------


def test_title_templates_russian_phrasing_and_allowed_ids() -> None:
    """Проверка русского форматирования чисел, единиц, интервалов дат и allowed_title_ids."""
    assert format_unit_ru("млн руб.") == "млн ₽"
    assert format_unit_ru("тыс. руб.") == "тыс. ₽"
    assert format_unit_ru("руб") == "₽"

    span_months = format_period_span_ru({"from": "2025-11", "to": "2025-12"})
    assert span_months == "с ноября по декабрь"

    span_years = format_period_span_ru({"from": "2024", "to": "2025"})
    assert span_years == "с 2024 по 2025 год"

    span_quarters = format_period_span_ru({"from": "2025-Q1", "to": "2025-Q4"})
    assert span_quarters == "с 1 по 4 кв. 2025"

    f = Fact(
        id="F_title_check",
        dataset_id="D1",
        metric="revenue",
        aggregation="sum",
        comparison={"from": "2025-11", "to": "2025-12"},
        values={"from": 100, "to": 123},
        unit="млн руб.",
        source_refs=["sales.xlsx"],
    )
    assert "increase_pct" in f.allowed_title_ids
    assert "increase_abs" in f.allowed_title_ids
    assert (
        f.render_title("increase_pct")
        == "Выручка выросла на 23% с ноября по декабрь: со 100 до 123 млн ₽"
    )
    assert (
        f.render_title("increase_abs")
        == "Выручка выросла на 23 млн ₽ с ноября по декабрь: со 100 до 123 млн ₽"
    )

    # Неразрешённый шаблон вызывает исключение
    with pytest.raises(ValueError, match="не входит в allowed_title_ids"):
        render_title("loss_reduced", f)


# ---------------------------------------------------------------------------
# 14. Сквозная приёмка: analysis_report на stage2 с run.analysis.fact_ledger
# ---------------------------------------------------------------------------


def test_stage2_datasets_produce_fact_ledger_in_analysis_report() -> None:
    """Сквозной прогон по датасетам stage2: реестр попадает в analysis_report с source_refs."""
    for filename in (
        "sales_monthly.csv",
        "regions.xlsx",
        "channels_share.json",
        "order_values.csv",
    ):
        pack = parse_file(STAGE2 / filename)
        assert pack.datasets
        ds = pack.datasets[0]
        analysis = analyze_dataset(ds)
        assert analysis.fact_ledger is not None
        assert len(analysis.fact_ledger) >= 1

        for f in analysis.fact_ledger.by_id.values():
            assert f.source_refs, f"Факт {f.id} не имеет source_refs"
            assert all(str(r).strip() for r in f.source_refs)
            assert f.score > 0.0

        report = analysis_report(pack)
        assert report is not None
        assert "fact_ledger" in report
        ledger_dict = report["fact_ledger"]
        assert "by_id" in ledger_dict
        assert "ranked" in ledger_dict
        assert len(ledger_dict["ranked"]) >= 1
