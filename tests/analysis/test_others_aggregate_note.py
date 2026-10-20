"""Агрегат «Остальные (N)», конкурирующий с топ-барами, уходит в сноску.

QA: на графике «Остальные (15)» со значением 291 выглядел как ещё одна
топовая категория рядом с барами 410 и 390 — сводный столбец не должен
быть крупнее половины лидера показанных значений.
"""

from deckdna.analysis.shape import shape_for_type
from deckdna.contracts.content_pack import Chart, ChartSeries, SourceRef


def _chart(values: list[float]) -> Chart:
    return Chart(
        id="c",
        categories=[f"К{i}" for i in range(len(values))],
        series=[ChartSeries(name="Выручка", values=values)],
        source_ref=SourceRef(artifact_id="x"),
        chart_type="bar",
    )


def test_others_competing_with_top_bars_moves_to_footnote():
    # 15 категорий: топ-10 = [410, 390, 300, 58×5, 50, 50], хвост 250 > 410/2
    chart = _chart([410, 390, 300] + [58] * 5 + [50] * 7)
    shaped = shape_for_type(chart, "bar")
    assert "Остальные" not in " ".join(shaped.categories)
    assert len(shaped.categories) == 10
    assert "всего" in (shaped.source_note or "")


def test_small_others_stays_a_bar():
    # хвост «Остальные (3)» = 3 не крупнее самой мелкой показанной
    # категории (3) — столбец остаётся
    chart = _chart([100, 50, 40, 30, 20, 10, 5, 4, 3, 3, 1, 1, 1])
    shaped = shape_for_type(chart, "bar")
    assert shaped.categories[-1] == "Остальные (3)"


def test_others_larger_than_min_top_bar_moves_to_footnote():
    # «Остальные (3)» = 4 крупнее самого мелкого показанного бара (3):
    # сводный столбец соперничал бы с реальными категориями — в сноску
    chart = _chart([100, 50, 40, 30, 20, 10, 5, 4, 3, 3, 2, 1, 1])
    shaped = shape_for_type(chart, "bar")
    assert "Остальные" not in " ".join(shaped.categories)
    assert "всего" in (shaped.source_note or "")
