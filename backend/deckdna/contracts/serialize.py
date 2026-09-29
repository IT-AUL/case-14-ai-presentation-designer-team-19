"""Каноническая сериализация контрактных моделей для схемы/провода.

JSON-схемы в ``schemas/`` не принимают explicit ``null`` у необязательных
полей: поле либо присутствует со значением, либо отсутствует. Голый
``model_dump()`` включает ``None``, поэтому ВСЯ сериализация контрактов
наружу — ответы API, файлы, jsonschema-валидация, передача между стадиями
пайплайна — обязана идти через ``to_schema_dict`` / ``to_schema_json``.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel


def to_schema_dict(model: BaseModel) -> dict[str, Any]:
    """dict-дамп, совместимый с JSON-схемой контракта (null-поля опущены)."""
    return model.model_dump(mode="json", exclude_none=True, by_alias=True)


def to_schema_json(model: BaseModel, **json_kwargs: Any) -> str:
    """JSON-строка из ``to_schema_dict``; по умолчанию без ascii-экранирования."""
    json_kwargs.setdefault("ensure_ascii", False)
    return json.dumps(to_schema_dict(model), **json_kwargs)
