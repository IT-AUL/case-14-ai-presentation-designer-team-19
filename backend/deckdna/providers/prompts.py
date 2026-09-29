"""Prompt registry — versioned prompt assets from ``prompts/**/<name>.v<N>.yaml``.

Промпты — отдельные версионируемые файлы (OR-006), в код не встраиваются.
``load_prompt`` резолвит файл по имени и версии (``storyline.v1.yaml``),
``render_prompt`` склеивает ``instructions`` с сериализованным payload
строго по ``input_fields`` — лишние ключи payload игнорируются,
отсутствующие поля отдаются как ``null`` (видно модели, не скрыто).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from deckdna.errors import DeckDNAError

# providers/prompts.py -> providers -> deckdna -> backend -> repo root.
_PROMPTS_ROOT = Path(__file__).resolve().parents[3] / "prompts"

_VERSION_RE = re.compile(r"^v(\d+)$")


class PromptSpec(BaseModel):
    """Pydantic-модель полей промпт-файла (см. prompts/*/*.yaml)."""

    name: str
    version: str
    model_role: str
    output_schema: str
    language: str
    temperature: float
    instructions: str
    input_fields: list[str] = Field(default_factory=list)


def _resolve_file(name: str, version: str | None) -> Path:
    """Ищет prompts/**/<name>.v<version>.yaml; без версии — максимальный vN."""
    if not _PROMPTS_ROOT.is_dir():
        raise DeckDNAError(
            code="not_found",
            message=f"prompts directory not found at {_PROMPTS_ROOT}",
            stage="prompts",
        )
    if version is not None:
        candidates = sorted(_PROMPTS_ROOT.glob(f"**/{name}.v{version}.yaml"))
    else:
        candidates = sorted(_PROMPTS_ROOT.glob(f"**/{name}.v*.yaml"))
        candidates.sort(
            key=lambda p: _version_of(p, name), reverse=True
        )
    if not candidates:
        suffix = f".v{version}" if version is not None else ".v*"
        raise DeckDNAError(
            code="not_found",
            message=f"prompt not found: {name}{suffix}.yaml under {_PROMPTS_ROOT}",
            stage="prompts",
            details={"name": name, "version": version},
        )
    return candidates[0]


def _version_of(path: Path, name: str) -> int:
    match = _VERSION_RE.match(path.name[len(name) + 1 : -len(".yaml")])
    return int(match.group(1)) if match else -1


@lru_cache(maxsize=64)
def _load_cached(path_str: str) -> PromptSpec:
    path = Path(path_str)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise DeckDNAError(
            code="internal_error",
            message=f"prompt file is not valid YAML: {path.name}: {exc}",
            stage="prompts",
        ) from exc
    if not isinstance(data, dict):
        raise DeckDNAError(
            code="internal_error",
            message=f"prompt file is not a YAML mapping: {path.name}",
            stage="prompts",
        )
    try:
        return PromptSpec.model_validate(data)
    except Exception as exc:
        raise DeckDNAError(
            code="internal_error",
            message=f"prompt file does not match PromptSpec: {path.name}",
            stage="prompts",
            details={"errors": str(exc)[:500]},
        ) from exc


def load_prompt(name: str, version: str | None = None) -> PromptSpec:
    """Грузит PromptSpec из prompts/**/<name>.v<version>.yaml.

    ``version=None`` — берётся файл с максимальной vN-версией.
    Отсутствующий файл → typed ``not_found``, битый YAML → ``internal_error``.
    """
    return _load_cached(str(_resolve_file(name, version)))


def render_prompt(spec: PromptSpec, payload: dict[str, Any]) -> str:
    """instructions + payload, сериализованный по spec.input_fields.

    Поля payload вне input_fields игнорируются; отсутствующие поля —
    explicit null в выводе.
    """
    parts = [spec.instructions.strip()]
    for field in spec.input_fields:
        value = payload.get(field)
        serialized = (
            value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, default=str)
        )
        parts.append(f"\n## {field}\n{serialized}")
    return "\n".join(parts) + "\n"


def render_prompt_by_name(name: str, payload: dict[str, Any]) -> str:
    """Ярлык для провайдера: ``storyline`` или ``storyline.v1`` -> текст."""
    prompt_name, _, version = name.rpartition(".v")
    if prompt_name and version.isdigit():
        return render_prompt(load_prompt(prompt_name, version), payload)
    return render_prompt(load_prompt(name), payload)
