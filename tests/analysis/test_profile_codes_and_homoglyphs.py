"""Survey sheets: constant/code columns are not measures, homoglyph labels merge."""

from deckdna.analysis.profile import CATEGORY, NUMBER, profile_column, profile_dataset
from deckdna.analysis.views import build_views
from deckdna.contracts.dataset import Dataset


def _dataset(columns: dict[str, list]) -> Dataset:
    n = len(next(iter(columns.values())))
    return Dataset.model_validate(
        {
            "id": "ds-survey",
            "title": "Ответы",
            "row_count": n,
            "columns": [{"name": k, "values": v} for k, v in columns.items()],
            "source_ref": {"artifact_id": "survey.xlsx"},
        }
    )


def test_constant_numeric_column_is_not_a_metric():
    col = profile_column("Версия анкеты", ["2.0"] * 34)
    assert col.kind == CATEGORY and not col.is_metric


def test_small_integer_code_is_a_dimension_but_money_stays_a_metric():
    assert profile_column("Волна", [1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2, 3]).kind == CATEGORY
    revenue = profile_column("Выручка, млн ₽", [11.4, 12.0, 13.1, 12.8, 14.2, 15.0])
    assert revenue.is_metric
    orders = profile_column("Заказы", [120, 135, 150, 149, 160, 171, 180, 190, 201, 215])
    assert orders.kind == NUMBER


def test_cyrillic_and_latin_lookalikes_are_one_segment():
    col = profile_column("Сегмент", ["А1"] * 12 + ["A1"] * 7)
    assert col.cardinality == 1
    assert set(col.labels) == {"А1"}


def test_float_formatted_integers_read_as_integers():
    col = profile_column("Посещения за 30 дней", ["0.0", "0.0", "1.0", "4–7", "8+", "2.0"])
    assert "0.0" not in col.labels and "0" in col.labels


def test_survey_views_never_sum_a_constant_version_column():
    ds = _dataset(
        {
            "Версия анкеты": ["2.0"] * 34,
            "Источник анкеты": ["v-centre-sobytiy"] * 25 + ["off-topic"] * 8 + ["direct"],
            "Возраст": ["18–21"] * 20 + ["22–25"] * 9 + ["26–30"] * 2 + ["До 18"] * 3,
        }
    )
    prof = profile_dataset(ds)
    assert "Версия анкеты" not in prof.metrics
    titles = " ".join(
        v.title + " " + " ".join(s.name for s in v.series) for v in build_views(ds, prof)
    )
    assert "Версия анкеты" not in titles
