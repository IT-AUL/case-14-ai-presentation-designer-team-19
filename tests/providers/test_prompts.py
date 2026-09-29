"""Prompt registry: load_prompt / render_prompt against real prompt files."""

import pytest
from deckdna.errors import DeckDNAError
from deckdna.providers.prompts import (
    PromptSpec,
    load_prompt,
    render_prompt,
    render_prompt_by_name,
)


def test_load_storyline_prompt():
    spec = load_prompt("storyline", "1")
    assert spec.name == "storyline"
    assert spec.version == "1.0.1"
    assert spec.model_role == "text"
    # 'correction' — retry-with-feedback field, unused now that plan_deck_llm
    # loads v2 by default (see ADR-012); v1 stays as a historical prompt file.
    assert spec.input_fields == [
        "brief",
        "evidence_graph",
        "design_dna_capacities",
        "correction",
    ]
    assert "story director" in spec.instructions.lower()


def test_load_slide_checks_prompt():
    spec = load_prompt("slide_checks", "1")
    assert spec.name == "contextual_slide_audit"
    assert spec.model_role == "vision"
    assert "slide_image" in spec.input_fields


def test_load_prompt_defaults_to_latest_version():
    spec = load_prompt("storyline")
    assert spec.name == "storyline"
    assert spec.version == "2.0.1"  # batched outline (ADR-012), not v1's whole-deck plan


def test_load_unknown_prompt_is_typed_error():
    with pytest.raises(DeckDNAError) as excinfo:
        load_prompt("nonexistent_prompt")
    assert excinfo.value.code == "not_found"


def test_load_missing_version_is_typed_error():
    with pytest.raises(DeckDNAError) as excinfo:
        load_prompt("storyline", "99")
    assert excinfo.value.code == "not_found"


def test_render_prompt_instructions_and_fields():
    spec = PromptSpec(
        name="demo",
        version="1",
        model_role="text",
        output_schema="schemas/x.schema.json",
        language="ru",
        temperature=0.0,
        instructions="Do the thing.",
        input_fields=["brief", "metrics"],
    )
    rendered = render_prompt(
        spec, {"brief": "Сделай деку", "metrics": {"a": 1}, "extra": "ignored"}
    )
    assert rendered.startswith("Do the thing.")
    assert "## brief\nСделай деку" in rendered
    assert '## metrics\n{"a": 1}' in rendered
    assert "ignored" not in rendered


def test_render_prompt_missing_field_is_null():
    spec = PromptSpec(
        name="demo",
        version="1",
        model_role="text",
        output_schema="schemas/x.schema.json",
        language="ru",
        temperature=0.0,
        instructions="Instr.",
        input_fields=["brief", "missing_field"],
    )
    rendered = render_prompt(spec, {"brief": "x"})
    assert "## missing_field\nnull" in rendered


def test_render_prompt_by_name_real_file():
    rendered = render_prompt_by_name(
        "storyline", {"brief": "бриф", "evidence_graph": {}, "design_dna_capacities": {}}
    )
    assert "story director" in rendered.lower()
    assert "## brief\nбриф" in rendered


def test_render_prompt_by_name_with_version_suffix():
    rendered = render_prompt_by_name("slide_checks.v1", {"slide_text": "текст"})
    assert "presentation auditor" in rendered.lower()
    assert "## slide_text\nтекст" in rendered
