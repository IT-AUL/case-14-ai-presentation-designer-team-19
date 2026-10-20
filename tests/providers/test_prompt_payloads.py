"""ADR-031: каждый промпт отдаёт модели свои входные данные.

``render_prompt`` сериализует в текст только поля из ``input_fields``. У
четырёх промптов добора контента (ADR-030: ``research_queries``,
``research_extract``, ``content_expand``, ``slide_fill``) списка не было —
живая модель получала инструкцию без брифа, страниц и фактов. На моках это
не видно: ``MockProvider`` отвечает фикстурой, не читая промпт.
"""

from pathlib import Path

import pytest
import yaml
from deckdna.providers.prompts import load_prompt, render_prompt

PROMPTS = Path(__file__).resolve().parents[2] / "prompts"
ALL = sorted(PROMPTS.rglob("*.yaml"))

# поля payload, которые код реально передаёт в text_json (research/*.py,
# planning/complete.py, research/slide.py)
PAYLOADS = {
    "research_queries": ["brief", "covered_topics", "missing_slides", "max_queries", "focus"],
    "research_extract": ["brief", "page", "max_facts", "focus"],
    "content_expand": ["brief", "covered_topics", "existing_facts", "sections_needed",
                       "bullets_per_section"],
    "slide_fill": ["brief", "slide", "deck_outline", "facts", "bullets"],
}


@pytest.mark.parametrize("path", ALL, ids=[p.stem for p in ALL])
def test_every_prompt_declares_input_fields(path):
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert spec.get("input_fields"), f"{path.name}: без input_fields модель не получит данных"


@pytest.mark.parametrize("name,fields", sorted(PAYLOADS.items()))
def test_research_prompts_render_their_payload(name, fields):
    spec = load_prompt(name)
    assert set(fields) <= set(spec.input_fields)
    payload = {field: f"ЗНАЧЕНИЕ-{field}" for field in fields}
    text = render_prompt(spec, payload)
    for field in fields:
        assert f"ЗНАЧЕНИЕ-{field}" in text
