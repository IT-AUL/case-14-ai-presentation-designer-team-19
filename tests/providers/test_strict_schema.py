"""Strict JSON Schema для OpenAI Structured Outputs.

Официальный контракт `strict: true`: у каждого record-объекта схемы
ВСЕ ключи ``properties`` объявлены ``required`` и ``additionalProperties``
закрыт — реальные endpoint'ы (OpenAI, совместимые strict-провайдеры)
отклоняют схему, не удовлетворяющую этим инвариантам, ещё до вызова
модели. Проверяется на схемах обоих LLM/VLM-путей (DeckPlan и
SlideChecksResult) рекурсивно, включая вложенные ``$defs``, и на
проводе — через реальный HTTP stub (``local_openai``, TCP).
"""

from typing import Any

import pytest
from deckdna.audit.contextual import SlideChecksResult
from deckdna.contracts.deck_plan import DeckPlan
from deckdna.providers.base import json_schema_of
from deckdna.providers.openai_compat import OpenAICompatibleProvider

MODELS = [DeckPlan, SlideChecksResult]


def _object_nodes(node: Any, path: str = "") -> list[tuple[str, dict]]:
    """Все record-объекты схемы рекурсивно (properties/items/anyOf/$defs…)."""
    found: list[tuple[str, dict]] = []
    if isinstance(node, dict):
        if isinstance(node.get("properties"), dict):
            found.append((path or "<root>", node))
        for key, value in node.items():
            found += _object_nodes(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found += _object_nodes(value, f"{path}[{i}]")
    return found


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
def test_schema_is_strict_recursive(model):
    schema = json_schema_of(model)["schema"]
    objects = _object_nodes(schema)
    assert len(objects) > 1  # и корень, и вложенные модели проверены
    for path, node in objects:
        assert set(node.get("required", [])) == set(node["properties"]), (
            f"{path}: required покрывает не все поля"
        )
        assert node.get("additionalProperties") is False, (
            f"{path}: additionalProperties не закрыт"
        )


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
def test_optional_fields_are_nullable_not_dropped(model):
    """Strict-optional паттерн: nullable-поля (anyOf с {type: null})
    объявлены required — модель обязана их выдать, может null'ом."""
    schema = json_schema_of(model)["schema"]
    nullable = 0
    for path, node in _object_nodes(schema):
        for name, prop in node["properties"].items():
            branches = prop.get("anyOf") if isinstance(prop, dict) else None
            if branches and any(
                isinstance(b, dict) and b.get("type") == "null" for b in branches
            ):
                nullable += 1
                assert name in node["required"], f"{path}.{name}"
    assert nullable > 0  # реально есть optional-поля, паттерн проверен


def test_strict_schema_does_not_mutate_source_model():
    """Стриктификация не меняет pydantic-модель — повторный вызов чист."""
    first = json_schema_of(DeckPlan)
    second = json_schema_of(DeckPlan)
    assert first == second


def test_pydantic_validation_preserved():
    """Pydantic-семантика не тронута: partial (без optional) и полный
    strict-ответ (optional = null) валидируются одинаково честно."""
    partial = {"verdicts": [{"check": "1", "verdict": "pass"}]}
    strict_full = {
        "verdicts": [
            {
                "check": "1",
                "verdict": "pass",
                "rationale": None,
                "confidence": 0.5,
            }
        ]
    }
    assert SlideChecksResult.model_validate(partial).verdicts[0].rationale is None
    assert SlideChecksResult.model_validate(strict_full).verdicts[0].confidence == 0.5


async def test_strict_schema_sent_over_real_http(local_openai):
    """На проводе: response_format несёт strict-схему, не pydantic-сырую."""
    local_openai.responses["SlideChecksResult"] = {
        "verdicts": [{"check": "5", "verdict": "pass", "rationale": None, "confidence": 1.0}]
    }
    provider = OpenAICompatibleProvider(
        local_openai.base_url,
        api_key="test-key",
        model_text="m",
        model_vision="v",
        min_request_interval_s=0,
    )
    await provider.vision_json(
        "slide_checks", ["data:image/png;base64,AAAA"], {}, SlideChecksResult
    )
    await provider.aclose()

    wire_schema = local_openai.requests[0]["json"]["response_format"][
        "json_schema"
    ]["schema"]
    for path, node in _object_nodes(wire_schema):
        assert set(node["required"]) == set(node["properties"]), path
        assert node["additionalProperties"] is False, path
