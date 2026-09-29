"""DeckPlan validation against schemas/deck-plan.schema.json and the
active GenerationConfig.

Slide-count limits come exclusively from the config object — the 10–15
standard is a default profile, not a validator constant.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema
from pydantic import BaseModel

from deckdna.contracts.serialize import to_schema_dict
from deckdna.errors import DeckDNAError
from deckdna.planning.config import GenerationConfig, load_generation_config

# backend/deckdna/planning/validation.py → <repo>/schemas/
_SCHEMAS_DIR = Path(__file__).resolve().parents[3] / "schemas"


@lru_cache(maxsize=4)
def _load_schema(name: str) -> dict[str, Any]:
    return json.loads((_SCHEMAS_DIR / name).read_text(encoding="utf-8"))


def _strip_none(obj: Any) -> Any:
    """Убирает ключи с None-значением из dict'ов рекурсивно — нормализует
    чужой ``model_dump()`` без ``exclude_none`` под ожидания схемы."""
    if isinstance(obj, dict):
        return {k: _strip_none(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip_none(v) for v in obj]
    return obj


def validate_slide_count(target: int, config: GenerationConfig | None = None) -> None:
    """Raise `invalid_input` when the requested count is outside the
    configured bounds (or absolute schema guard rails)."""
    cfg = config or load_generation_config()
    cfg.require_slide_count(target)


def validate_deck_plan(
    plan: dict[str, Any] | BaseModel, config: GenerationConfig | None = None
) -> None:
    """Полная валидация DeckPlan: JSON-схема + структурные инварианты.

    Принимает контрактную модель или dict-дамп (None-поля в dict
    нормализуются — вызывающий код не обязан помнить про exclude_none).

    - JSON-schema validation против schemas/deck-plan.schema.json;
    - slide count equals brief.target_slide_count;
    - count inside configured bounds;
    - unique slide ids and contiguous indices.
    """
    cfg = config or load_generation_config()
    plan_dict = to_schema_dict(plan) if isinstance(plan, BaseModel) else _strip_none(plan)

    try:
        jsonschema.validate(plan_dict, _load_schema("deck-plan.schema.json"))
    except jsonschema.ValidationError as exc:
        raise DeckDNAError(
            code="validation_failed",
            message=f"deck plan fails schemas/deck-plan.schema.json: {exc.message}",
            stage="planning",
            details={
                "instance_path": list(exc.absolute_path),
                "schema_path": list(exc.absolute_schema_path),
            },
        ) from exc

    brief = plan_dict.get("brief", {})
    slides = plan_dict.get("slides", [])
    target = brief.get("target_slide_count")

    if target is None:
        raise DeckDNAError(
            code="invalid_input",
            message="deck plan lacks brief.target_slide_count",
            stage="planning",
        )
    cfg.require_slide_count(target)

    if len(slides) != target:
        raise DeckDNAError(
            code="validation_failed",
            message=f"plan has {len(slides)} slides, expected {target}",
            stage="planning",
            details={"target": target, "actual": len(slides)},
        )

    ids = [s.get("id") for s in slides]
    if len(set(ids)) != len(ids):
        raise DeckDNAError(
            code="validation_failed",
            message="duplicate slide ids in deck plan",
            stage="planning",
        )

    indices = sorted(s.get("index") for s in slides)
    if indices != list(range(len(slides))):
        raise DeckDNAError(
            code="validation_failed",
            message="slide indices are not contiguous from 0",
            stage="planning",
            details={"indices": indices},
        )
