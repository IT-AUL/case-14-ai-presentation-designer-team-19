"""Каждое имя промпта, которое код передаёт модели, находит свой файл.

На моках это не видно: MockProvider отвечает по имени fixture'а и файл
промпта не читает. Найдено на живом VK Inference 17.10: промпты анализа
данных лежали как ``intent.v1.yaml``/``interpret.v1.yaml``, а код звал
``analysis_intent``/``analysis_interpret`` — модель ни разу не вызывалась.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from deckdna.providers.prompts import load_prompt

ROOT = Path(__file__).resolve().parents[2]
_CONST_RE = re.compile(r'^[A-Z_]*PROMPT(?:_NAME)?\s*=\s*"([a-z0-9_]+)"', re.MULTILINE)


def _prompt_constants() -> set[str]:
    names: set[str] = set()
    for path in (ROOT / "backend" / "deckdna").rglob("*.py"):
        names.update(_CONST_RE.findall(path.read_text(encoding="utf-8")))
    return names


def test_constants_were_found():
    assert {"analysis_intent", "analysis_interpret", "storyline"} <= _prompt_constants()


@pytest.mark.parametrize("name", sorted(_prompt_constants()))
def test_prompt_constant_resolves_to_a_file(name):
    spec = load_prompt(name)
    assert spec.instructions.strip()
