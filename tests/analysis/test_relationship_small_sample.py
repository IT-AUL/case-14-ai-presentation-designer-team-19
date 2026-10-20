"""A strong correlation on a small sample must not read as «no link»."""

import pytest
from deckdna.analysis.insights import _relationship_facts
from deckdna.analysis.views import DataView, ViewSeries


def _view(x, y):
    return DataView(
        id="rel",
        dataset_id="ds",
        shape="relationship",
        title="Маржинальность и выручка",
        categories=[],
        series=[ViewSeries(name="Маржинальность", values=y)],
        x_values=x,
        x_label="Выручка",
    )


def test_strong_r_on_six_points_is_a_correlation_with_caveat():
    x = [94.0, 119.0, 133.0, 146.0, 238.0, 413.0]
    y = [26.5, 27.8, 28.6, 29.4, 32.1, 34.6]
    (fact,) = _relationship_facts(_view(x, y))
    assert fact.fact_type == "correlation"
    assert "не видна" not in fact.headline and "нет" not in fact.fact
    assert "связаны" in fact.headline and "6 наблюдениям" in fact.headline
    assert "предварительный" in fact.fact


@pytest.mark.parametrize(
    ("y", "word"),
    [([5.0, 1.0, 4.0, 2.0, 3.0, 5.0], "не видна"), ([2.0, 1.0, 3.0, 2.0, 1.0, 4.0], "слабая")],
)
def test_weak_or_absent_link_is_not_called_a_correlation(y, word):
    x = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    (fact,) = _relationship_facts(_view(x, y))
    assert fact.fact_type == "no_correlation"
    assert word in fact.headline
