"""Confirmation cannot leak from a fact into a new model recommendation."""

from pathlib import Path

from deckdna.analysis.interpret import INTERPRET_PROMPT, interpret_pack
from deckdna.contracts.deck_plan import Brief
from deckdna.ingestion.content_parsers import parse_file
from deckdna.providers.mock import MockProvider

ROOT = Path(__file__).resolve().parents[2]


async def test_confirmed_insights_stay_locked_on_another_model_pass():
    pack = parse_file(ROOT / "examples/stage2/sales_monthly.csv")
    pack = pack.model_copy(update={"insights": [
        i.model_copy(update={"confirmed": True}) for i in pack.insights
    ]})
    brief = Brief(purpose="Итоги", audience="команда", language="ru", target_slide_count=7)
    gateway = MockProvider(fixtures={INTERPRET_PROMPT: lambda payload: {
        "items": [{"id": i["id"], "meaning": "Новая интерпретация.",
                   "implication": "Предлагаю поощрить команду."} for i in payload["items"]],
    }})
    result, report = await interpret_pack(pack, brief, gateway)
    assert result == pack and report.interpreted == 0 and gateway.calls == []
    first = pack.insights[0]
    pack = pack.model_copy(update={"insights": [
        i.model_copy(update={"confirmed": False}) if i.id == first.id else i
        for i in pack.insights
    ]})
    result, report = await interpret_pack(pack, brief, gateway)
    changed = next(i for i in result.insights if i.id == first.id)
    assert changed.implication and changed.needs_confirmation and not changed.confirmed
    assert [i["id"] for i in gateway.calls[0]["payload"]["items"]] == [first.id]
    assert [i for i in result.insights if i.id != first.id] == [
        i for i in pack.insights if i.id != first.id
    ]


def test_interpretation_checks_single_digits_and_negative_signs():
    from deckdna.analysis.interpret import check_text
    from deckdna.contracts.dataset import Insight

    fact = Insight(id="i", dataset_id="d", fact="Показатель упал на -2 пункта",
                   headline="Изменение: -2", numbers=["-2"], strength=0.8)
    assert check_text("Падение на -2 пункта", fact) is None
    assert check_text("Показатель вырос на 2 пункта", fact) is not None
    assert check_text("Теперь нужно 7 сотрудников", fact) is not None
    assert check_text("Уменьшение на −2 пункта", fact) is None
    growth = fact.model_copy(update={"fact": "Рост в 2,1 раза", "numbers": ["2,1"],
                                     "headline": None})
    assert check_text("Рост в 2 раза", growth) is not None


def test_interpretation_cannot_round_a_ratio_by_spelling_it_out():
    from deckdna.analysis.interpret import check_text
    from deckdna.contracts.dataset import Insight

    fact = Insight(id="i", dataset_id="d", fact="Выручка выросла в 2,1 раза",
                   numbers=["2,1"], strength=0.8)
    for text in ("Двукратное увеличение выручки", "Выручка удвоилась", "Revenue doubled"):
        assert check_text(text, fact) is not None
    exact = fact.model_copy(update={"fact": "Выручка выросла в 2 раза", "numbers": ["2"]})
    assert check_text("Выручка удвоилась", exact) is None
    assert check_text("Заметное увеличение выручки", fact) is None
    assert check_text("Утром показатель вырос", fact) is None
