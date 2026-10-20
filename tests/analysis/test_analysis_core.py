"""ADR-033 §1–§5: табличные входы, профиль, задача, выбор графика, факты.

Всё детерминированно, без модели. Данные — синтетические файлы из
``examples/stage2/`` (генератор ``scripts/make_stage2_data.py``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from deckdna.analysis import analyze_dataset, analyze_pack
from deckdna.analysis.profile import MONEY, PERIOD, profile_dataset
from deckdna.analysis.values import parse_number, parse_period
from deckdna.ingestion.content_parsers import parse_file
from deckdna.ingestion.tabular import read_csv, read_json_table, read_xlsx, sniff_delimiter

STAGE2 = Path(__file__).resolve().parents[2] / "examples" / "stage2"


@pytest.mark.parametrize(
    ("raw", "value", "percent"),
    [
        ("1 234,5", 1234.5, False),
        ("12%", 12.0, True),
        ("−7.1", -7.1, False),
        ("₽ 3,5 млн", 3.5, False),
        ("1,234.50", 1234.5, False),
        (42, 42.0, False),
    ],
)
def test_parse_number(raw, value, percent):
    parsed = parse_number(raw)
    assert parsed is not None and parsed.value == pytest.approx(value)
    assert parsed.percent is percent


@pytest.mark.parametrize("raw", ["12 месяцев", "Москва", "", None])
def test_parse_number_rejects_text(raw):
    assert parse_number(raw) is None


@pytest.mark.parametrize(
    ("raw", "grain"),
    [
        ("2025-03-14", "day"),
        ("14.03.2025", "day"),
        ("2025-03", "month"),
        ("март 2025", "month"),
        ("Q1 2025", "quarter"),
        ("1 кв. 2025", "quarter"),
        ("2025", "year"),
    ],
)
def test_parse_period(raw, grain):
    assert parse_period(raw).grain == grain


def test_csv_with_semicolon_and_comma_in_header():
    text = "Дата;Регион;Сумма, руб.\n2025-01-01;Москва;1 200,50\n2025-01-02;Казань;800,00\n"
    assert sniff_delimiter(text) == ";"
    ds = read_csv(text.encode("cp1251"), "x.csv")
    assert [c.name for c in ds.columns] == ["Дата", "Регион", "Сумма, руб."]
    prof = profile_dataset(ds)
    assert prof.column("Сумма, руб.").kind == MONEY
    assert prof.column("Дата").kind == PERIOD


@pytest.mark.parametrize(
    "payload",
    [
        [{"Месяц": "2025-01", "Выручка": 10}, {"Месяц": "2025-02", "Выручка": 12}],
        {"columns": ["Месяц", "Выручка"], "data": [["2025-01", 10], ["2025-02", 12]]},
        {"Месяц": ["2025-01", "2025-02"], "Выручка": [10, 12]},
        {
            "title": "Продажи",
            "data": [{"Месяц": "2025-01", "Выручка": 10}, {"Месяц": "2025-02", "Выручка": 12}],
        },
    ],
)
def test_tabular_json_shapes(payload):
    ds = read_json_table(json.dumps(payload, ensure_ascii=False), "x.json")
    assert ds is not None
    assert [c.name for c in ds.columns] == ["Месяц", "Выручка"]
    assert ds.row_count == 2


def test_content_pack_json_still_parses_as_pack(tmp_path):
    path = tmp_path / "pack.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "id": "p",
                "language": "ru",
                "sections": [],
                "tables": [],
                "assets": [],
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )
    assert parse_file(path).datasets == []


def test_xlsx_header_below_a_title_row(tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Регионы"
    ws.append(["Выручка по регионам за 2025 год"])
    ws.append([])
    ws.append(["Регион", "Выручка, млн ₽"])
    for name, value in [("Москва", 41.2), ("Казань", 12.4), ("Пермь", 8.1)]:
        ws.append([name, value])
    path = tmp_path / "regions.xlsx"
    wb.save(path)
    [ds] = read_xlsx(path.read_bytes(), "regions.xlsx")
    assert [c.name for c in ds.columns] == ["Регион", "Выручка, млн ₽"]
    assert ds.context_text == "Выручка по регионам за 2025 год"
    assert ds.row_count == 3


def test_monthly_series_is_a_trend_drawn_as_a_line():
    pack = parse_file(STAGE2 / "sales_monthly.csv")
    [analysis] = analyze_pack(pack)
    primary = analysis.primary
    assert primary.task.task == "trend"
    assert primary.rec.chosen.chart_type == "line"
    assert any("24 точки" in r for r in primary.rec.chosen.reasons)
    assert {r.chart_type for r in primary.rec.alternatives} <= {"bar", "table"}
    headline = primary.headline
    assert headline.startswith("Выручка выросла в 2")
    assert "24 месяца" in headline


def test_transactions_are_aggregated_into_views():
    pack = parse_file(STAGE2 / "purchases.csv")
    [analysis] = analyze_pack(pack, "Итоги продаж за два года")
    shapes = {va.view.id.rsplit("-", 1)[-1]: va for va in analysis.views}
    trend = analysis.primary
    assert trend.view.shape == "series" and trend.view.aggregation == "сумма по месяцам"
    assert len(trend.view.categories) == 24
    assert trend.rec.chosen.chart_type == "line"
    regions = next(va for va in analysis.views if va.view.category_label == "Регион")
    assert regions.task.task == "comparison"
    assert regions.rec.chosen.chart_type in ("bar_horizontal", "bar")
    assert regions.headline.startswith("Москва — 4")  # ~41% выручки
    # строки без региона — сноска, а не столбец «Не указано»
    assert "Не указано" not in regions.view.categories
    assert any("без значения «Регион»" in n for n in regions.view.notes)
    assert "ref" in shapes  # справка по сырой таблице — на случай «покажи данные»
    assert any(i.fact_type == "missing" for i in analysis.extra_insights)


def test_intent_shifts_choice_but_never_makes_it_impossible():
    pack = parse_file(STAGE2 / "purchases.csv")
    channel = lambda a: next(  # noqa: E731
        va for va in a.views if va.view.category_label == "Канал"
    )
    plain = channel(analyze_pack(pack, "Итоги продаж")[0])
    share = channel(analyze_pack(pack, "Какая доля продаж приходится на каждый канал")[0])
    assert plain.rec.chosen.chart_type in ("bar", "bar_horizontal")
    assert share.task.task == "composition"
    assert share.rec.chosen.chart_type in ("pie", "doughnut")
    # «доля» для ряда по месяцам не делает круговую возможной
    trend = next(
        va for va in analyze_pack(pack, "Какая доля продаж")[0].views if va.view.shape == "series"
    )
    assert "pie" not in {r.chart_type for r in trend.rec.ranked}


def test_numbers_in_facts_come_from_the_data():
    pack = parse_file(STAGE2 / "sales_monthly.csv")
    [analysis] = analyze_pack(pack)
    view = analysis.primary.view
    first, last = view.series[0].values[0], view.series[0].values[-1]
    fact = analysis.primary.insights[0]
    from deckdna.analysis.values import fmt_ratio, fmt_scaled

    assert fmt_scaled(first, view.unit) in fact.fact
    assert fmt_scaled(last, view.unit) in fact.fact
    assert fmt_ratio(last / first) in fact.headline


@pytest.mark.parametrize("name", ["sales_monthly.csv", "sales_monthly.xlsx", "sales_monthly.json"])
def test_format_invariance_inputs_exist(name):
    assert (STAGE2 / name).exists()


def test_same_dataset_in_csv_xlsx_json_gives_same_choice_and_facts():
    """ADR-033 §10: выбор не зависит от формата файла."""
    results = []
    for name in ("sales_monthly.csv", "sales_monthly.xlsx", "sales_monthly.json"):
        pack = parse_file(STAGE2 / name)
        [ds] = pack.datasets
        analysis = analyze_dataset(ds, "Итоги продаж")
        results.append(
            [
                (
                    va.view.shape,
                    va.task.task,
                    [r.chart_type for r in va.rec.ranked],
                    [i.fact for i in va.insights],
                    va.view.categories,
                )
                for va in analysis.views
            ]
        )
    assert results[0] == results[1] == results[2]


def test_untypable_table_becomes_a_reference_view():
    text = "Имя;Комментарий\n" + "".join(
        f"Сотрудник {i};Длинный свободный комментарий номер {i} о работе отдела\n"
        for i in range(40)
    )
    ds = read_csv(text.encode(), "notes.csv")
    analysis = analyze_dataset(ds)
    assert not analysis.profile.typable
    assert analysis.primary.view.shape == "reference"
    assert analysis.primary.rec.chosen.chart_type == "table"
    assert analysis.primary.view.reason
