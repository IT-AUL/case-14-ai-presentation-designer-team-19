"""Respondent-level sheets: shares count answers, bare codes keep their column name"""

from deckdna.analysis.insights import view_insights
from deckdna.analysis.profile import profile_dataset
from deckdna.analysis.views import CATEGORICAL, build_views
from deckdna.contracts.dataset import Dataset

_SOURCES = ["Сайт"] * 20 + ["Соцсети"] * 8 + ["Друзья"] * 6
_VISITS = [0] * 22 + [1] * 6 + ["4–7"] * 4 + ["8+"] * 2
_SECONDS = [120 + (i * 37) % 300 for i in range(34)]


def _dataset() -> Dataset:
    columns = {
        "Источник": _SOURCES,
        "Посещения клубов за 30 дней": _VISITS,
        "Длительность в секундах": _SECONDS,
    }
    return Dataset.model_validate(
        {
            "id": "ds-answers",
            "title": "Ответы",
            "row_count": 34,
            "columns": [{"name": k, "values": v} for k, v in columns.items()],
            "source_ref": {"artifact_id": "survey.xlsx"},
        }
    )


def _category_views(ds: Dataset):
    return [v for v in build_views(ds, profile_dataset(ds)) if v.shape == CATEGORICAL]


def test_per_record_duration_is_not_summed_by_segment():
    views = _category_views(_dataset())
    assert views
    for view in views:
        assert "Длительность" not in view.series[0].name
        assert view.series[0].name == "Число записей"
    by_source = next(v for v in views if v.category_label == "Источник")
    assert by_source.series[0].values[0] == 20


def test_bare_numeric_category_is_named_by_its_column():
    view = next(v for v in _category_views(_dataset()) if v.category_label.startswith("Посещения"))
    facts = view_insights(view)
    leader = next(i for i in facts if i.fact_type == "leader")
    assert leader.headline.startswith("Посещения клубов за 30 дней: «0»")
    gap = next(i for i in facts if i.fact_type == "gap")
    assert "«0»" in gap.fact and "«1»" in gap.fact and "»:" not in gap.fact
