"""ADR-018: describe_exemplars (template/dna.py) — one batched LLM call
over a template's exemplar slides at analyze time, cached in Design DNA
afterwards. No call happens during generation; a provider failure or an
untrusted mock just yields an honest empty dict, never blocks analyze."""

from __future__ import annotations

import asyncio

from deckdna.providers.mock import MockProvider
from deckdna.template import dna as builder


def _feature(index: int, part: str, title: str = "", paragraphs=None) -> builder._SlideFeatures:
    return builder._SlideFeatures(
        index=index,
        part=part,
        layout_part=None,
        layout_type="",
        title=title,
        paragraphs=list(paragraphs or []),
        all_text=title,
    )


def _run(coro):
    return asyncio.run(coro)


def _fixture(payload: dict) -> dict:
    return {
        "slides": [
            {
                "index": s["index"],
                "description": f"desc for {s['index']}",
                "is_entity_specific": False,
            }
            for s in payload["slides"]
        ]
    }


def test_describe_exemplars_maps_part_to_description():
    features = [
        _feature(0, "ppt/slides/slide1.xml", title="Слайд-визитка команды"),
        _feature(1, "ppt/slides/slide2.xml", title="Сравнение тарифов"),
    ]
    gw = MockProvider(fixtures={builder.EXEMPLAR_DESCRIBE_PROMPT: _fixture})

    result = _run(builder.describe_exemplars(features, gw))

    assert result["ppt/slides/slide1.xml"].description == "desc for 0"
    assert result["ppt/slides/slide1.xml"].is_entity_specific is False
    assert result["ppt/slides/slide2.xml"].description == "desc for 1"


def test_describe_exemplars_carries_is_entity_specific_flag():
    def _team_fixture(payload: dict) -> dict:
        return {
            "slides": [
                {
                    "index": s["index"],
                    "description": "Слайд-визитка команды",
                    "is_entity_specific": True,
                }
                for s in payload["slides"]
            ]
        }

    features = [_feature(0, "ppt/slides/slide1.xml", title="Слайд-визитка команды")]
    gw = MockProvider(fixtures={builder.EXEMPLAR_DESCRIBE_PROMPT: _team_fixture})

    result = _run(builder.describe_exemplars(features, gw))

    assert result["ppt/slides/slide1.xml"].is_entity_specific is True


def test_describe_exemplars_batches_large_pools():
    features = [_feature(i, f"ppt/slides/slide{i}.xml", title=f"T{i}") for i in range(25)]
    gw = MockProvider(fixtures={builder.EXEMPLAR_DESCRIBE_PROMPT: _fixture})

    result = _run(builder.describe_exemplars(features, gw))

    assert len(result) == 25
    calls = [c for c in gw.calls if c["prompt"] == builder.EXEMPLAR_DESCRIBE_PROMPT]
    assert len(calls) >= 3  # 25 slides / batch size 10 -> at least 3 batches


def test_describe_exemplars_no_gateway_is_empty():
    features = [_feature(0, "ppt/slides/slide1.xml", title="T")]
    assert _run(builder.describe_exemplars(features, None)) == {}


def test_describe_exemplars_mock_without_fixture_is_empty():
    """can_describe() gate: offline mock without an explicit fixture for
    this prompt must not participate -- its synthesized text would be
    schema-valid gibberish, not a real description."""
    features = [_feature(0, "ppt/slides/slide1.xml", title="T")]
    gw = MockProvider()  # no exemplar_describe fixture

    assert _run(builder.describe_exemplars(features, gw)) == {}
    assert gw.calls == []


def test_describe_exemplars_provider_failure_is_honest_empty():
    class FailingGateway:
        provider_name = "failing"

        async def text_json(self, prompt_name, payload, schema):
            raise RuntimeError("endpoint unreachable")

    features = [_feature(0, "ppt/slides/slide1.xml", title="T")]

    assert _run(builder.describe_exemplars(features, FailingGateway())) == {}


def test_describe_exemplars_items_outside_batch_are_ignored():
    def _rogue(payload: dict) -> dict:
        items = [
            {
                "index": s["index"],
                "description": f"desc {s['index']}",
                "is_entity_specific": False,
            }
            for s in payload["slides"]
        ]
        items.append(
            {"index": 9999, "description": "не наш индекс", "is_entity_specific": False}
        )
        return {"slides": items}

    features = [_feature(0, "ppt/slides/slide1.xml", title="T")]
    gw = MockProvider(fixtures={builder.EXEMPLAR_DESCRIBE_PROMPT: _rogue})

    result = _run(builder.describe_exemplars(features, gw))

    assert list(result) == ["ppt/slides/slide1.xml"]
    assert result["ppt/slides/slide1.xml"].description == "desc 0"


def test_sample_text_prefers_title_and_paragraphs_over_all_text():
    f = _feature(
        0,
        "ppt/slides/slide1.xml",
        title="Заголовок",
        paragraphs=["Первый абзац.", "Второй абзац."],
    )
    f.all_text = "совсем другой текст, не должен использоваться"
    assert builder._sample_text(f) == "Заголовок | Первый абзац. | Второй абзац."


def test_sample_text_falls_back_to_all_text_when_no_title_or_paragraphs():
    f = _feature(0, "ppt/slides/slide1.xml", title="", paragraphs=[])
    f.all_text = "только сплошной текст без структуры"
    assert builder._sample_text(f) == "только сплошной текст без структуры"
