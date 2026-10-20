"""Справочный слайд — компактный срез, исходный датасет сохраняется."""

from deckdna.analysis import analyze_dataset
from deckdna.analysis.pipeline import reference_pages, view_table
from deckdna.ingestion.tabular import read_csv


def test_untypable_reference_limits_columns():
    headers = ["Сущность", *[f"Описание {i}" for i in range(10)]]
    rows = [[f"Компания {i}", *[f"Длинный текст {i} {j}" for j in range(10)]] for i in range(8)]
    ds = read_csv(
        (";".join(headers) + "\n" + "\n".join(";".join(r) for r in rows)).encode(), "wide.csv"
    )
    va = next(v for v in analyze_dataset(ds).views if v.view.shape == "reference")
    table = view_table(ds, va)
    assert len(table.headers) <= 4
    assert table.headers[0] == "Сущность"
    assert all(len(r) == len(table.headers) for r in table.rows)
    assert len(ds.columns) == 11


def test_reference_prefers_variable_metrics_and_keeps_pages_consistent():
    headers = ["Артикул", "Описание", "Постоянная", "Статус", "Продажи", "Расходы", "Маржа"]
    rows = [
        f"SKU-{i};Длинное описание товара;1;{'Да' if i % 2 else 'Нет'};{i};{i * 10};{i * i}"
        for i in range(20)
    ]
    ds = read_csv((";".join(headers) + "\n" + "\n".join(rows)).encode(), "catalog.csv")
    va = next(v for v in analyze_dataset(ds).views if v.view.shape == "reference")
    table = view_table(ds, va)
    assert table.headers == ["Артикул", "Продажи", "Расходы", "Маржа"]
    for page in reference_pages(ds, va):
        assert page.headers == table.headers
        assert all(len(r) == 4 for r in page.rows)
    assert any("колонок" in n for n in va.view.notes)


def test_free_text_does_not_displace_numeric_metrics():
    from deckdna.analysis.table_columns import column_indices

    headers = ["Сущность", "Описание", "Число A", "Число B", "Число C"]
    rows = [
        ["A", "Рекомендуемая литература", 10, 20, 30],
        ["B", "Подробное описание компании", 11, 21, 31],
    ]
    assert column_indices(headers, rows) == [0, 2, 3, 4]


def test_reference_uses_first_identifier_even_when_metrics_come_first():
    rows = [f"{i * 10};SKU-{i};Описание товара;{i};{i * 2};{i * 3}" for i in range(20)]
    ds = read_csv(
        ("Выручка;Артикул;Описание;Продажи;Расходы;Маржа\n" + "\n".join(rows)).encode(), "ref.csv"
    )
    va = next(v for v in analyze_dataset(ds).views if v.view.shape == "reference")
    assert view_table(ds, va).headers[0] == "Артикул"


def test_reference_plan_discloses_column_projection(tmp_path):
    from deckdna.contracts.deck_plan import Brief
    from deckdna.ingestion.content_parsers import parse_file
    from deckdna.planning.story_director import plan_deck

    path = tmp_path / "reference.csv"
    path.write_text(
        "Артикул;Описание;Цена;Продажи;Расходы;Маржа\n"
        + "\n".join(f"SKU-{i};Описание;10;{i};{i * 2};{i * 3}" for i in range(20)),
        encoding="utf-8",
    )
    pack = parse_file(path)
    plan = plan_deck(
        pack,
        Brief(purpose="Справочник", audience="Руководство", language="ru", target_slide_count=5),
    )
    ref = next(
        s
        for s in plan.slides
        if s.purpose.value == "data" and any(u.table_ref for u in s.content_units)
    )
    assert "Отображены первые 4 из 6 колонок" in ref.speaker_note
    assert len(pack.datasets[0].columns) == 6
