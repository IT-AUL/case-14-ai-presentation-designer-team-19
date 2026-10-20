"""A shorter agenda must still navigate to every topic."""
from pathlib import Path

from deckdna.assistant.plan_edit import apply_action
from deckdna.contracts.content_pack import ContentPack
from deckdna.contracts.deck_plan import ContentUnit, DeckPlan, Kind, Origin, Purpose


def test_shorten_agenda_keeps_topics_and_assumption_provenance():
    plan = DeckPlan.model_validate_json(
        (Path(__file__).parents[1] / "fixtures/content/poc_deck_plan.json").read_text())
    entries = [
        "Проблема: почему существующий процесс занимает слишком много времени у команды",
        "Решение: как организовать совместную подготовку презентации без потери данных",
        "Предположение: Пилот — проверить сценарий на небольшой группе пользователей",
    ]
    slide = plan.slides[1].model_copy(update={
        "purpose": Purpose.agenda,
        "content_units": [ContentUnit(role="agenda", kind=Kind.bullet, text=t,
                                      origin=Origin.model if i == 2 else Origin.user,
                                      evidence_ids=[f"topic-{i}"])
                          for i, t in enumerate(entries)],
    })
    plan.slides[1] = slide
    pack = ContentPack(schema_version="1", id="pack", language="ru",
                       sections=[], tables=[], assets=[], warnings=[])
    edited, _ = apply_action(plan, slide.id, "shorten", pack)
    units = edited.slides[1].content_units
    assert len(units) == len(entries)
    assert [u.evidence_ids for u in units] == [[f"topic-{i}"] for i in range(3)]
    assert all(len(u.text) < len(t) for u, t in zip(units, entries, strict=True))
    assert units[-1].text.startswith("Предположение: Пилот")
    assert units[-1].origin == Origin.model
