"""Canonical author preferences and legacy question aliases (ADR-045)."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from deckdna.contracts.deck_plan import Brief
from deckdna.errors import DeckDNAError

BRIEF_FIELDS = frozenset(Brief.model_fields)
_ALIASES = {"length": "target_slide_count", "content_short": "content_fill"}


def normalize_brief(values: dict[str, Any], *, title_hint: bool = False) -> dict[str, Any]:
    """Validate a partial brief without filling unknown author preferences."""
    if not isinstance(values, dict):
        raise DeckDNAError("invalid_input", "brief must be a JSON object", http_status=422)
    unknown = set(values) - BRIEF_FIELDS - ({"title_hint"} if title_hint else set())
    if unknown:
        raise DeckDNAError("invalid_input", "unknown brief fields", http_status=422,
                           details={"fields": sorted(unknown)})
    supplied = {k: v for k, v in values.items() if v is not None}
    if "target_slide_count" in supplied and isinstance(supplied["target_slide_count"], bool):
        raise DeckDNAError("invalid_input", "slide count must be an integer", http_status=422)
    raw = {k: v for k, v in supplied.items() if k in BRIEF_FIELDS}
    try:
        model = Brief.model_validate({"purpose": "", "audience": "", "language": "ru",
                                      "target_slide_count": 5, **raw})
    except ValidationError as exc:
        raise DeckDNAError("invalid_input", "invalid brief preference", http_status=422,
                           details={"fields": sorted({str(e["loc"][0])
                                                      for e in exc.errors()})}) from exc
    result = {k: v for k, v in model.model_dump(mode="json").items() if k in raw}
    if "target_slide_count" in raw and model.requested_slide_count is not None:
        result["requested_slide_count"] = model.requested_slide_count
    if "title_hint" in supplied:
        if not isinstance(supplied["title_hint"], str):
            raise DeckDNAError("invalid_input", "title_hint must be text", http_status=422)
        result["title_hint"] = supplied["title_hint"]
    return result


def normalize_answers(answers: dict[str, Any]) -> tuple[dict[str, Any], dict[str, bool]]:
    unknown = set(answers) - BRIEF_FIELDS - set(_ALIASES)
    if unknown:
        raise DeckDNAError("invalid_input", "unknown answer fields", http_status=422,
                           details={"fields": sorted(unknown)})
    values: dict[str, Any] = {}
    follow_up: dict[str, bool] = {}
    for key, value in answers.items():
        if value is None or value == "":
            continue
        field = _ALIASES.get(key, key)
        if key == "content_short":
            if value == "notes":
                follow_up["notes"] = True
                value = "shrink"
            elif value in ("expand_web", "expand_model"):
                value = "expand"
        canonical = normalize_brief({field: value})[field]
        if field in values and values[field] != canonical:
            raise DeckDNAError("invalid_input", "conflicting answer aliases", http_status=422,
                               details={"fields": [field]})
        values[field] = canonical
    return values, follow_up


def answered_question_ids(values: dict[str, Any]) -> set[str]:
    return set(values) | {alias for alias, field in _ALIASES.items() if field in values}
