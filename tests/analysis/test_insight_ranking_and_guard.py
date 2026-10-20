"""Тесты ранжирования фактов, guard-проверки и регрессий F-82, F-85, F-86, F-87, F-88."""

from __future__ import annotations

from pathlib import Path

import pytest
from deckdna.analysis import (
    analyze_dataset,
    guard_insight,
    is_wide_spread,
    materialize,
    normalize_ru_text,
    score_insight,
    select_slide_insights,
    verify_insight_numbers,
)
from deckdna.analysis.guard import has_narrow_spread_claim
from deckdna.analysis.insights import cross_table_insights
from deckdna.analysis.interpret import check_text
from deckdna.analysis.profile import profile_dataset
from deckdna.analysis.ranking import load_analysis_config
from deckdna.contracts.content_pack import ContentPack, SourceRef
from deckdna.contracts.dataset import Dataset, DatasetColumn, Insight
from deckdna.contracts.deck_plan import Brief, Purpose
from deckdna.ingestion.content_parsers import parse_file
from deckdna.ingestion.merge import merge_packs
from deckdna.planning.data_story import _has_card_label, plan_data_deck

STAGE2 = Path(__file__).resolve().parents[2] / "examples" / "stage2"


def _brief(purpose: str = "Анализ показателей", target: int = 6) -> Brief:
    return Brief(
        purpose=purpose,
        audience="руководство",
        language="ru",
        target_slide_count=target,
    )


def _ds(
    title: str,
    columns: dict[str, list],
    *,
    ds_id: str = "ds-test",
    context: str | None = None,
) -> Dataset:
    n_rows = len(next(iter(columns.values()))) if columns else 0
    return Dataset(
        id=ds_id,
        title=title,
        file_name=f"{ds_id}.csv",
        row_count=n_rows,
        context_text=context,
        source_ref=SourceRef(artifact_id=f"{ds_id}.csv"),
        columns=[DatasetColumn(name=k, values=v) for k, v in columns.items()],
    )


@pytest.mark.parametrize(
    ("filename", "expected_task", "expected_charts"),
    [
        ("sales_monthly.csv", "trend", {"line"}),
        ("regions.xlsx", "comparison", {"bar_horizontal", "bar"}),
        ("channels_share.json", "composition", {"pie", "doughnut", "bar"}),
        ("order_values.csv", "distribution", {"histogram"}),
        ("products_reference.xlsx", "reference", {"table"}),
        ("nps_with_gaps.csv", "trend", {"line"}),
        ("report_with_table.txt", "trend", {"line", "bar"}),
    ],
)
def test_stage2_seven_types_task_chart_headline_and_zero_contradictions(
    filename: str,
    expected_task: str,
    expected_charts: set[str],
) -> None:
    """Приёмка по 7 типам данных второй ступени: ожидаемая задача, тип графика
    в топ-1, заголовок содержит число из данных, ноль противоречий."""
    pack = parse_file(STAGE2 / filename)
    assert pack.datasets, f"Нет датасета в {filename}"
    ds = pack.datasets[0]
    analysis = analyze_dataset(ds)
    primary = analysis.primary

    assert primary.task.task == expected_task
    assert primary.rec.chosen.chart_type in expected_charts
    assert primary.insights, f"Нет инсайтов для {filename}"

    top = primary.insights[0]
    assert top.headline, f"Пустой заголовок для {filename}"
    for ins in primary.insights:
        unverified = verify_insight_numbers(ins, view=primary.view, dataset=analysis.profile)
        assert not unverified, f"Неподтверждённые числа {unverified} в {ins.headline} ({filename})"
        if is_wide_spread(
            primary.view.raw_values or (primary.view.metric.values if primary.view.metric else [])
        ):
            assert not has_narrow_spread_claim(ins.headline)
            assert not has_narrow_spread_claim(ins.fact)


def test_f82_wide_spread_never_gets_narrow_spread_conclusion() -> None:
    """F-82: гистограмма и scatter с 10-кратным разбросом никогда не получают
    вывод «Разброс узкий: планировать от типичного значения»."""
    # 1. Распределение с широким разбросом (order_values.csv)
    dist_pack = parse_file(STAGE2 / "order_values.csv")
    dist_plan = plan_data_deck(dist_pack, _brief("Суммы заказов", target=5))
    all_texts = [u.text or "" for s in dist_plan.slides for u in s.content_units]
    assert not any(has_narrow_spread_claim(t) for t in all_texts), all_texts

    # 2. Scatter с 10-кратным разбросом (ad_spend_vs_sales.csv):
    # не должно строиться лишнее представление DISTRIBUTION и не должно быть «Разброс узкий»
    scatter_pack = parse_file(STAGE2 / "ad_spend_vs_sales.csv")
    scatter_analysis = analyze_dataset(scatter_pack.datasets[0])
    shapes = [va.view.shape for va in scatter_analysis.views]
    assert shapes == ["relationship"], f"Лишний срез для scatter-датасета: {shapes}"
    scatter_plan = plan_data_deck(scatter_pack, _brief("Реклама и продажи", target=5))
    scatter_texts = [u.text or "" for s in scatter_plan.slides for u in s.content_units]
    assert not any(has_narrow_spread_claim(t) for t in scatter_texts), scatter_texts

    # 3. Guard перехватывает противоречие даже если модель вернула «Разброс узкий»
    view = scatter_analysis.primary.view
    bad_ins = Insight(
        id="bad-spread",
        dataset_id=scatter_pack.datasets[0].id,
        view_id=view.id,
        fact_type="spread",
        headline="Разброс узкий: планировать от типичного значения 50",
        fact="Медиана 50, разброс узкий",
        meaning="Разброс узкий: планировать от типичного значения.",
        implication="Разброс узкий: планировать от типичного значения.",
        strength=0.5,
        numbers=["50"],
    )
    guarded = guard_insight(bad_ins, view=view)
    assert guarded.fact_type == "wide_spread"
    assert not has_narrow_spread_claim(guarded.headline)
    assert not has_narrow_spread_claim(guarded.fact)
    assert not has_narrow_spread_claim(guarded.meaning)
    assert not has_narrow_spread_claim(guarded.implication)
    assert check_text("Разброс узкий: планировать от типичного значения", guarded) is not None


def test_f82_no_duplicate_so_what_stamps_across_slides() -> None:
    """F-82: штамп «Стоит сохранить драйверы...» или «Стоит сфокусироваться на лидере...»
    не повторяется на соседних слайдах одной колоды."""
    pack = parse_file(STAGE2 / "purchases.csv")
    plan = plan_data_deck(pack, _brief("Закупки по категориям и поставщикам", target=6))
    data_slides = [s for s in plan.slides if s.purpose == Purpose.data]
    assert len(data_slides) >= 2
    so_whats: list[str] = []
    for slide in data_slides:
        for u in slide.content_units:
            if u.role == "bullet" and (u.text or "").startswith(
                ("Стоит ", "Разброс ", "Диапазон ", "Результат ")
            ):
                so_whats.append(u.text)
    assert len(so_whats) == len(set(so_whats)), f"Повтор штампов между слайдами: {so_whats}"


def test_f85_inverted_top_n_and_uppercase_pronouns_after_colon() -> None:
    """F-85: инверсия «240 позиций вошли в топ-6» и местоимения с прописной
    после двоеточия исправляются детерминированно; буллеты «Ключевых выводов»
    имеют защищённую структуру «Подпись: текст»."""
    fixed_top = normalize_ru_text("Товары: 240 позиций вошли в топ-6.")
    assert "в таблице топ-6 из 240 позиций" in fixed_top
    assert "240 позиций вошли в топ-6" not in fixed_top

    fixed_pair = normalize_ru_text("Товары: в таблице топ-240 из 6 позиций")
    assert "топ-6 из 240" in fixed_pair

    fixed_pron = normalize_ru_text("Сайт: Он даёт крупнейшую долю 38,5%. Каналы — Они растут.")
    assert ": он даёт" in fixed_pron
    assert "— они растут" in fixed_pron

    pack = merge_packs(
        [
            parse_file(STAGE2 / "nps_with_gaps.csv"),
            parse_file(STAGE2 / "channels_share.json"),
            parse_file(STAGE2 / "products_reference.xlsx"),
        ]
    )
    plan = plan_data_deck(pack, _brief("Операционные показатели и каталог", target=6))
    overview = next(s for s in plan.slides if s.purpose == Purpose.overview)
    bullets = [u.text for u in overview.content_units if u.role == "bullet"]
    assert len(bullets) >= 3
    for b in bullets:
        assert _has_card_label(b) or len(b.split()) <= 6, f"Буллет уйдёт в LLM-переписывание: {b}"
        assert "240 позиций вошли в топ-6" not in b
        assert ": Он " not in b and ": Она " not in b and ": Они " not in b


def test_f86_reference_240_rows_builds_top_n_table_not_supplier_bar_chart() -> None:
    """F-86: для справочника >= 200 строк строится только таблица Top-N + сводка
    по главной метрике, без барчарта «число записей по Поставщику», даже если
    поставщики распределены неравномерно."""
    # 1. Эталонный файл products_reference.xlsx (240 строк)
    pack = parse_file(STAGE2 / "products_reference.xlsx")
    analysis = analyze_dataset(pack.datasets[0])
    assert [va.view.shape for va in analysis.views] == ["reference"]
    assert analysis.primary.rec.chosen.chart_type == "table"
    tbl = next(t for t in pack.tables if t.id == f"tbl-{analysis.primary.view.id}")
    assert tbl.rows[-2][0].startswith("Прочие (234 ")
    assert tbl.rows[-1][0] == "Итого"
    # Остаток, шт. суммируется в Итого, а Цена, ₽ — нет
    ost_idx = tbl.headers.index("Остаток, шт.")
    price_idx = tbl.headers.index("Цена, ₽")
    assert isinstance(tbl.rows[-1][ost_idx], (int, float)) and tbl.rows[-1][ost_idx] > 0
    assert tbl.rows[-1][price_idx] == ""

    # 2. Справочник 240 строк с сильным перекосом по Поставщику (70% у одного)
    skewed_ds = _ds(
        "Справочник позиций",
        {
            "Артикул": [f"SKU-{i:04d}" for i in range(240)],
            "Наименование": [f"Товар {i}" for i in range(240)],
            "Категория": ["Серверы" if i < 180 else "Сеть" for i in range(240)],
            "Поставщик": ["Альфа" if i < 170 else "Бета" for i in range(240)],
            "Цена, ₽": [1000 + i * 10 for i in range(240)],
            "Остаток, шт.": [10 + (i % 5) for i in range(240)],
        },
    )
    skewed_analysis = analyze_dataset(skewed_ds)
    assert [va.view.shape for va in skewed_analysis.views] == ["reference"]


def test_f87_regions_leader_and_no_duplicate_share_slide_in_multi_view_decks() -> None:
    """F-87: в regions.xlsx Москва распознаётся как лидер (20% из 14 регионов),
    на втором слайде одиночного датасета выводится концентрация Топ-3 (без дубля заголовка),
    а в многосрезовых колодах дублирующий слайд «доля от итога» подавляется."""
    reg_pack = parse_file(STAGE2 / "regions.xlsx")
    reg_analysis = analyze_dataset(reg_pack.datasets[0])
    primary_ins = reg_analysis.primary.insights
    assert primary_ins[0].fact_type == "leader"
    assert "Москва" in primary_ins[0].headline
    assert "нет явного лидера" not in primary_ins[0].headline

    # В одиночной колоде по regions.xlsx заголовки 1-го и 2-го слайдов данных различны
    reg_plan = plan_data_deck(reg_pack, _brief("Сравнить регионы", target=5))
    reg_data_slides = [s for s in reg_plan.slides if s.purpose == Purpose.data]
    assert len(reg_data_slides) == 2
    assert reg_data_slides[0].title_intent != reg_data_slides[1].title_intent
    assert "Топ-3" in reg_data_slides[1].title_intent

    # В многосрезовом пакете (sales_monthly + regions + channels_share)
    # производный слайд «доля от итога» не создаётся
    multi_pack = merge_packs(
        [
            parse_file(STAGE2 / "sales_monthly.csv"),
            parse_file(STAGE2 / "regions.xlsx"),
            parse_file(STAGE2 / "channels_share.json"),
        ]
    )
    multi_plan = plan_data_deck(multi_pack, _brief("Финансовый обзор", target=7))
    view_ids = [
        (s.chart or {}).get("view_id", "") for s in multi_plan.slides if s.purpose == Purpose.data
    ]
    assert not any(v.endswith("-share") for v in view_ids), view_ids


def test_f88_unit_column_name_never_becomes_subject() -> None:
    """F-88: имя колонки «Млн ₽» в мостовой таблице распознаётся как единица
    измерения и заменяется на «Показатель», не попадая в подлежащее заголовка."""
    ds = _ds(
        "Мост EBITDA 2024–2025",
        {
            "Фактор": [
                "База 2024",
                "Рост объёма",
                "Индексация цен",
                "Инфляция ФОТ",
                "Инфраструктура",
                "Эффект масштаба",
                "Факт 2025",
            ],
            "Млн ₽": [820.0, 240.0, 135.0, -145.0, -85.0, 95.0, 1060.0],
        },
        ds_id="ds-bridge",
    )
    prof = profile_dataset(ds)
    metric_col = prof.column("Млн ₽")
    assert metric_col.unit == "млн ₽"
    assert metric_col.label == "Показатель"

    analysis = analyze_dataset(ds)
    assert analysis.primary.view.bridge
    for ins in analysis.primary.insights:
        assert not ins.headline.startswith("Млн ₽")
        assert not ins.fact.startswith("Млн ₽")
        assert "млн ₽" in ins.fact or "млн ₽" in ins.headline
        assert "5 факторов" in ins.fact or ins.fact_type == "driver"


def test_ranking_weights_and_orthogonal_slide_selection() -> None:
    """Ранжирование по весам из configs/analysis.default.yaml и отбор
    не более одного ортогонального сопутствующего факта на слайд."""
    cfg = load_analysis_config()
    total_w = (
        cfg.weights.significance
        + cfg.weights.unusualness
        + cfg.weights.intent_match
        + cfg.weights.coverage
    )
    assert abs(total_w - 1.0) < 1e-6

    pack = parse_file(STAGE2 / "sales_monthly.csv")
    analysis = analyze_dataset(pack.datasets[0], brief_text="Показать динамику и рост выручки")
    va = analysis.primary
    sc = score_insight(va.insights[0], va.view, task=va.task, intent_text="Показать рост")
    assert sc.intent_match >= 0.85
    assert 0.5 <= sc.total <= 1.0

    top, supporting = select_slide_insights(va.insights, va.view, max_supporting=1)
    assert top is not None
    assert len(supporting) <= 1
    if supporting:
        assert supporting[0].fact_type != top.fact_type or "-s" in supporting[0].id


def test_cross_table_insights_at_most_one_on_shared_axis() -> None:
    """Межтабличные факты: максимум одна связка только при явной общей оси."""
    ds1 = _ds(
        "Выручка по месяцам",
        {
            "Месяц": ["2025-01", "2025-02", "2025-03", "2025-04", "2025-05", "2025-06"],
            "Выручка, млн ₽": [100, 120, 140, 165, 190, 220],
        },
        ds_id="ds-rev",
    )
    ds2 = _ds(
        "Маркетинг по месяцам",
        {
            "Месяц": ["2025-01", "2025-02", "2025-03", "2025-04", "2025-05", "2025-06"],
            "Затраты, млн ₽": [10, 12, 15, 17, 20, 23],
        },
        ds_id="ds-mkt",
    )
    ds3 = _ds(
        "Регионы",
        {
            "Регион": ["Москва", "СПб", "Казань", "Урал"],
            "Продажи, млн ₽": [400, 200, 100, 80],
        },
        ds_id="ds-reg",
    )
    pack = materialize(
        ContentPack(
            schema_version="1.0",
            id="pack-cross",
            language="ru",
            sections=[],
            tables=[],
            assets=[],
            warnings=[],
            datasets=[ds1, ds2, ds3],
        )
    )
    cross = [i for i in pack.insights if i.fact_type == "cross_table"]
    assert len(cross) == 1
    assert "синхронно" in cross[0].headline

    # Пакет без общих осей не порождает ложных межтабличных связок
    disjoint_pack = merge_packs(
        [
            parse_file(STAGE2 / "nps_with_gaps.csv"),
            parse_file(STAGE2 / "channels_share.json"),
            parse_file(STAGE2 / "products_reference.xlsx"),
        ]
    )
    assert not [i for i in disjoint_pack.insights if i.fact_type == "cross_table"]
    assert cross_table_insights([]) == []


def test_chart_explanation_populated_on_every_data_slide() -> None:
    """Объяснение выбора графика (why, reasons, alternatives) доходит до плана
    на каждом слайде с данными."""
    pack = merge_packs(
        [
            parse_file(STAGE2 / "sales_monthly.csv"),
            parse_file(STAGE2 / "regions.xlsx"),
            parse_file(STAGE2 / "order_values.csv"),
        ]
    )
    plan = plan_data_deck(pack, _brief("Обзор метрик", target=6))
    data_slides = [s for s in plan.slides if s.purpose == Purpose.data]
    assert len(data_slides) >= 3
    for slide in data_slides:
        assert slide.chart is not None
        assert slide.chart.get("type")
        assert slide.chart.get("reasons")
        assert slide.chart.get("why")
        assert isinstance(slide.chart.get("alternatives"), list)
        assert len(slide.chart["alternatives"]) >= 1
