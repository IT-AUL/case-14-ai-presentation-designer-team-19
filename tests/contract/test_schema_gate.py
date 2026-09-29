"""Contract gate: выход стадий реально проходит JSON-схему, не только
структурные инварианты. Ловит класс бага «model_dump() шлёт null там,
где схема ждёт отсутствие поля».
"""

import json
from pathlib import Path

import pytest
from deckdna.contracts import to_schema_dict, to_schema_json
from deckdna.contracts.content_pack import Block, ContentPack, Section, SourceRef
from deckdna.contracts.deck_plan import Brief
from deckdna.errors import DeckDNAError
from deckdna.planning import plan_deck, validate_deck_plan
from deckdna.planning.config import DeckBounds, GenerationConfig
from jsonschema import validate

SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"
CFG = GenerationConfig(deck=DeckBounds(min_slides=3, max_slides=40))


def _pack() -> ContentPack:
    return ContentPack(
        schema_version="1.0",
        id="pack-gate",
        language="ru",
        title_hint="Gate-тест",
        sections=[
            Section(
                id="sec-0",
                heading="Проблема",
                level=1,
                blocks=[
                    Block(
                        kind="paragraph",
                        text="Текст проблемы.",
                        source_ref=SourceRef(artifact_id="a.md"),
                    )
                ],
            )
        ],
        tables=[],
        assets=[],
        warnings=[],
    )


def _brief(target: int) -> Brief:
    # tone намеренно не задан — дефолт None; схема не принимает null
    return Brief(
        purpose="Проверка gate", audience="тест", language="ru", target_slide_count=target
    )


def test_deck_plan_canonical_dump_passes_schema():
    """plan_deck -> to_schema_dict -> jsonschema.validate — тот же путь,
    по которому план уходит в pptx-compose и API."""
    plan = plan_deck(_pack(), _brief(3), config=CFG)
    schema = json.loads((SCHEMAS / "deck-plan.schema.json").read_text())
    validate(instance=to_schema_dict(plan), schema=schema)


def test_validate_deck_plan_runs_schema_gate():
    """Валидатор должен ловить нарушение схемы, а не только инвариантов."""
    plan = plan_deck(_pack(), _brief(3), config=CFG)
    broken = to_schema_dict(plan)
    broken["slides"][0]["purpose"] = "not_a_purpose"
    with pytest.raises(DeckDNAError, match="deck-plan.schema.json") as exc:
        validate_deck_plan(broken, config=CFG)
    assert exc.value.code == "validation_failed"


def test_validate_deck_plan_normalizes_plain_dump():
    """Чужой model_dump() с tone:null нормализуется внутри валидатора —
    вызывающий код не обязан помнить про exclude_none."""
    plan = plan_deck(_pack(), _brief(3), config=CFG)
    plain_dump = plan.model_dump(mode="json")  # содержит "tone": null
    assert plain_dump["brief"]["tone"] is None
    validate_deck_plan(plain_dump, config=CFG)


def test_to_schema_json_roundtrip():
    plan = plan_deck(_pack(), _brief(3), config=CFG)
    assert "null" not in to_schema_json(plan)


def test_content_pack_canonical_dump_passes_schema():
    schema = json.loads((SCHEMAS / "content-pack.schema.json").read_text())
    validate(instance=to_schema_dict(_pack()), schema=schema)
