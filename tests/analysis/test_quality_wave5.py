"""Регрессии волны 5 (LEAD-BE): Action Titles, честные выводы и числа.

Покрывает замечания LEAD-QA-DECK / LEAD-DESIGN-UX / W7:
- заголовки слайдов — глагольные выводы («Выручка выросла в 2,1 раза»),
  а не ярлыки «Выручка: рост…» и не единицы «Млн ₽: …»;
- «в 5 раз», а не «в 5 раза»; «+5%»/«−79%», а не «+-79%»;
- «лидер» с долей < 25% не называется лидером;
- справочник: «в таблице топ-6 из 240 позиций»;
- разброс шире медианы — отдельный тип факта со своим «so what»;
- пропуски данных видны буллетом на слайде;
- Итого не суммирует неаддитивные колонки («Цена», «Ставка, %»).
"""

from pathlib import Path

import pytest
from deckdna.analysis.insights import _metric_title, view_insights
from deckdna.analysis.pipeline import _reference_summary_rows, analyze_pack
from deckdna.analysis.profile import PERCENT, profile_dataset
from deckdna.analysis.values import fmt_signed_pct, times_phrase, verb
from deckdna.analysis.views import (
    CATEGORICAL,
    DISTRIBUTION,
    REFERENCE,
    DataView,
    ViewSeries,
    _cell,
    _sample,
    _skewed,
)
from deckdna.contracts.content_pack import SourceRef
from deckdna.contracts.dataset import Dataset, DatasetColumn
from deckdna.contracts.deck_plan import Brief, Purpose
from deckdna.ingestion.content_parsers import parse_file
from deckdna.planning.data_story import _SO_WHAT, plan_data_deck

STAGE2 = Path("examples/stage2")


def _ds(columns: list[DatasetColumn], title: str = "Данные") -> Dataset:
    return Dataset(
        id="d1",
        title=title,
        columns=columns,
        row_count=len(columns[0].values),
        source_ref=SourceRef(artifact_id="test.csv"),
    )


def _col(name: str, values) -> DatasetColumn:
    return DatasetColumn(name=name, values=list(values))


def _view(
    dataset: Dataset,
    *,
    shape: str = CATEGORICAL,
    name: str = "Метрика",
    values=None,
    categories=None,
    category_label: str = "Категория",
    kind: str = "number",
    unit: str | None = None,
    raw_values=None,
    source_rows: int = 0,
    primary_hint: bool = False,
    sample_headers=None,
    notes=None,
    title: str = "Вид",
) -> DataView:
    return DataView(
        id="v1",
        dataset_id=dataset.id,
        shape=shape,
        title=title,
        categories=list(categories or []),
        series=[
            ViewSeries(name=name, values=list(values or []), unit=unit, kind=kind)
        ]
        if values is not None
        else [],
        category_label=category_label,
        unit=unit,
        raw_values=list(raw_values or []),
        source_rows=source_rows,
        primary_hint=primary_hint,
        sample_headers=list(sample_headers or []),
        notes=list(notes or []),
    )


# ── Action Titles ─────────────────────────────────────────────────────────────


def test_trend_headline_is_a_verb_conclusion():
    pack = parse_file(STAGE2 / "sales_monthly.csv")
    [analysis] = analyze_pack(pack)
    headline = analysis.primary.headline
    assert headline.startswith("Выручка выросла")
    assert "раза" in headline or " раз" in headline
    assert "24 месяца" in headline


def test_times_phrase_agreement():
    assert times_phrase(2.0) == "в 2 раза"
    assert times_phrase(5.0) == "в 5 раз"
    assert times_phrase(12.0) == "в 12 раз"
    assert times_phrase(2.1) == "в 2,1 раза"


def test_signed_percent_never_shows_plus_minus():
    assert fmt_signed_pct(5) == "+5%"
    assert fmt_signed_pct(-79) == "−79%"
    assert "+-" not in fmt_signed_pct(-79)


def test_metric_title_strips_units():
    assert _metric_title("Млн ₽") == "Показатель"
    assert _metric_title("Цена, млн ₽") == "Цена"
    assert _metric_title("Выполнено плановых задач, %") == "Выполнено плановых задач"
    assert _metric_title("Выручка") == "Выручка"


def test_low_share_leader_is_called_uniform_not_leader():
    ds = _ds([_col("Записей", [11] * 6 + [10] * 4 + [9])])
    view = _view(
        ds,
        categories=[f"П{i}" for i in range(11)],
        values=[11] * 6 + [10] * 4 + [9],
        name="Число записей",
        category_label="Поставщик",
    )
    ins = view_insights(view)
    assert ins and ins[0].fact_type == "even"
    assert "нет явного лидера" in ins[0].headline


def test_low_share_percent_leader_is_called_uniform():
    ds = _ds([_col("Доля", [16.7] * 6)])
    view = _view(
        ds,
        categories=[f"К{i}" for i in range(6)],
        values=[16.7] * 6,
        name="Доля",
        kind=PERCENT,
        category_label="Канал",
        unit="%",
    )
    ins = view_insights(view)
    # почти равные доли (16,7% × 6) — «even» ветка ещё раньше, лидера нет
    assert ins[0].fact_type == "even"
    assert "крупнейш" not in ins[0].headline


def test_dominant_leader_still_named():
    view = _view(
        _ds([_col("Выручка", [630, 370])]),
        categories=["Сайт", "Розница"],
        values=[630, 370],
        name="Выручка",
        category_label="Канал",
    )
    ins = view_insights(view)
    assert ins[0].fact_type == "leader"
    assert ins[0].headline == "Сайт — 63% выручки"


# ── справочник ────────────────────────────────────────────────────────────────


def test_reference_headline_names_top_n_of_total():
    view = _view(
        _ds([_col("x", [0])]),
        shape=REFERENCE,
        title="Товары",
        source_rows=240,
        primary_hint=True,
        sample_headers=["Артикул", "Цена"],
        notes=["240 строк"],
        values=[],
    )
    [ins] = view_insights(view)
    assert ins.headline == "Товары: в таблице топ-6 из 240 позиций"


def test_reference_totals_skip_non_additive_columns():
    ds = _ds(
        [
            _col("Товар", [f"Т{i}" for i in range(10)]),
            _col("Цена", [100] * 10),
            _col("Остаток", [5] * 10),
        ]
    )
    view = _view(
        ds,
        shape=REFERENCE,
        title=ds.title,
        source_rows=10,
        sample_headers=["Товар", "Цена", "Остаток"],
    )
    rest, total = _reference_summary_rows(ds, view, [])
    assert rest[0].startswith("Прочие")
    # «Цена» не суммируется, аддитивный «Остаток» — да
    assert total[0] == "Итого" and total[1] == "" and total[2] == 50


def test_reference_cells_use_thousand_separators():
    ds = _ds(
        [
            _col("Артикул", [f"А-{i}" for i in range(10)]),
            _col("Цена", [float(44000 + i * 1000) for i in range(10)]),
        ]
    )
    _, rows = _sample(ds)
    assert all(
        not cell.replace(" ", "").replace(" ", "").isdigit() or " " in cell
        for row in rows for cell in row
    )
    assert _cell(44862.0) == "44 862"
    assert _cell("44862") == "44862"  # сырая строка — не трогаем


# ── разброс и so_what ─────────────────────────────────────────────────────────


def test_wide_spread_gets_own_fact_type_and_so_what():
    values = [10, 12, 11, 13, 12, 11, 12, 14, 100, 95, 90, 105]
    view = _view(
        _ds([_col("Чек", values)]),
        shape=DISTRIBUTION,
        name="Чек",
        values=values,
        raw_values=values,
        unit="₽",
    )
    [ins] = view_insights(view)
    assert ins.fact_type == "wide_spread"
    assert "разброс" in ins.fact
    assert _SO_WHAT["wide_spread"] != _SO_WHAT["spread"]
    assert "запасом" in _SO_WHAT["wide_spread"]


def test_narrow_spread_keeps_narrow_wording():
    values = [100, 101, 99, 100, 102, 98, 100, 101, 99, 100]
    view = _view(
        _ds([_col("Чек", values)]),
        shape=DISTRIBUTION,
        name="Чек",
        values=values,
        raw_values=values,
        unit="₽",
    )
    [ins] = view_insights(view)
    assert ins.fact_type == "spread"
    assert _SO_WHAT["spread"].startswith("Разброс узкий")


# ── пропуски данных на слайде ─────────────────────────────────────────────────


def test_missing_values_surface_as_a_slide_bullet():
    pack = parse_file(STAGE2 / "purchases.csv")
    plan = plan_data_deck(
        pack,
        Brief(
            purpose="Итоги продаж",
            audience="команда",
            language="ru",
            target_slide_count=10,
        ),
    )
    bullets = [
        u.text
        for slide in plan.slides
        if slide.purpose == Purpose.data
        for u in slide.content_units
        if u.text and "нет значений в" in u.text
    ]
    assert bullets, "пропуски данных должны быть видны буллетом на слайде"


# ── морфология глагола ────────────────────────────────────────────────────────


def test_verb_agrees_with_metric_gender():
    assert verb("grow", "Выручка") == "выросла"
    assert verb("grow", "Прибыль") == "выросла"
    assert verb("grow", "Средний чек") == "вырос"
    assert verb("grow", "Число записей") == "выросло"
    assert verb("grow", "Новые клиенты") == "выросли"
    assert verb("fall", "Выручка") == "снизилась"


@pytest.mark.parametrize("name", ["sales_monthly.csv", "sales_monthly.xlsx"])
def test_format_invariant_action_title(name):
    pack = parse_file(STAGE2 / name)
    [analysis] = analyze_pack(pack)
    assert analysis.primary.headline.startswith("Выручка выросла")


def test_skew_gate_requires_a_meaningful_leader():
    """Число записей по ~одинаковым категориям справочника — шум: лидер
    должен держать ≥ четверти записей и опережать минимум (F-79)."""
    ds = _ds([_col("x", [0])])
    flat = _view(
        ds,
        categories=[f"П{i}" for i in range(11)],
        values=[11] * 6 + [10] * 4 + [9],
        name="Число записей",
        category_label="Поставщик",
    )
    assert not _skewed(flat)
    skewed = _view(
        ds,
        categories=["A", "B", "C", "D"],
        values=[60, 12, 14, 14],
        name="Число записей",
        category_label="Склад",
    )
    assert _skewed(skewed)


def test_build_views_suppresses_uniform_entity_counts():
    """Справочник с ~равными записями по поставщикам не получает
    график «число записей» — слайд ничего бы не сказал (LEAD-QA)."""
    from deckdna.analysis.views import build_views

    ds = _ds(
        [
            _col("Артикул", [f"SKU-{i}" for i in range(120)]),
            _col("Поставщик", [f"П{i % 11}" for i in range(120)]),
            _col("Категория", ["Шины"] * 120),
            _col("Цена", [100 + i for i in range(120)]),
            _col("Склад", ["A"] * 60 + ["B"] * 40 + ["C"] * 20),
            _col("Ед", ["шт"] * 120),
        ],
        title="Каталог",
    )
    views = build_views(ds, profile_dataset(ds))
    counts = [
        v
        for v in views
        if v.shape == CATEGORICAL and v.metric and "запис" in v.metric.name.casefold()
    ]
    for v in counts:
        vals = [x for x in v.metric.values if x is not None]
        assert max(vals) / sum(vals) >= 0.25
