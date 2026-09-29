"""MockProvider determinism + build_gateway flag wiring. No network involved."""

from __future__ import annotations

from typing import Literal

import pytest
from deckdna.errors import DeckDNAError
from deckdna.providers import build_gateway
from deckdna.providers.mock import MockProvider
from deckdna.providers.openai_compat import OpenAICompatibleProvider
from deckdna.settings import Settings
from pydantic import BaseModel, Field


class _Out(BaseModel):
    answer: str


class _Rich(BaseModel):
    title: str = Field(min_length=3, max_length=40)
    kind: Literal["faithful", "balanced", "visual"]
    score: float = Field(ge=0.0, le=1.0)
    count: int = Field(gt=0)
    tags: list[str] = Field(min_length=1)
    nested: _Out


class _Node(BaseModel):
    name: str
    child: _Node | None = None  # self-reference, like a grouped Scene Graph


_Node.model_rebuild()


class _Bounded(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    level: int = Field(ge=1, le=5)


async def test_text_json_synthesizes_valid_model():
    provider = MockProvider()
    out = await provider.text_json("plan_deck", {"brief": "x"}, _Rich)
    assert isinstance(out, _Rich)
    assert out.kind in ("faithful", "balanced", "visual")
    assert len(out.title) >= 3
    assert out.count > 0
    assert 0.0 <= out.score <= 1.0
    assert len(out.tags) >= 1


async def test_deterministic_across_calls_and_instances():
    provider = MockProvider()
    a = await provider.text_json("plan_deck", {"brief": "x"}, _Rich)
    b = await provider.text_json("plan_deck", {"brief": "x"}, _Rich)
    c = await MockProvider().text_json("plan_deck", {"brief": "x"}, _Rich)
    assert a.model_dump() == b.model_dump() == c.model_dump()

    other = await provider.text_json("plan_deck", {"brief": "different"}, _Rich)
    assert isinstance(other, _Rich)


async def test_vision_json_records_images_and_stays_deterministic():
    provider = MockProvider()
    images = ["https://img/1.png", "https://img/2.png"]
    a = await provider.vision_json("audit_slide", images, {"k": 1}, _Out)
    b = await provider.vision_json("audit_slide", images, {"k": 1}, _Out)
    assert a.model_dump() == b.model_dump()
    assert provider.calls[-1]["images"] == images


async def test_embed_is_deterministic_and_well_shaped():
    provider = MockProvider(embedding_dim=8)
    v1 = await provider.embed(["hello", "world"])
    v2 = await provider.embed(["hello", "world"])
    assert v1 == v2
    assert len(v1) == 2 and all(len(v) == 8 for v in v1)
    assert v1[0] != v1[1]  # distinct texts embed distinctly
    assert all(0.0 <= x <= 1.0 for v in v1 for x in v)


async def test_image_generate_returns_decodable_png_and_logs_call():
    provider = MockProvider()
    data = await provider.image_generate("a friendly robot")
    assert data.startswith(b"\x89PNG\r\n\x1a\n")  # real PNG magic bytes
    assert provider.calls[-1] == {
        "method": "image_generate",
        "prompt": "a friendly robot",
        "size": "1024x1024",
    }
    assert provider.used_model_ids()["image"] == "mock"


async def test_image_generate_fixture_overrides_default_bytes():
    provider = MockProvider(fixtures={"image_generate": lambda prompt: prompt.encode()})
    data = await provider.image_generate("custom bytes")
    assert data == b"custom bytes"


async def test_fixture_overrides_synthesis():
    provider = MockProvider(fixtures={"plan_deck": {"answer": "pinned"}})
    out = await provider.text_json("plan_deck", {"brief": "anything"}, _Out)
    assert out.answer == "pinned"

    provider2 = MockProvider(fixtures={"plan_deck": _Out(answer="model instance")})
    assert (await provider2.text_json("plan_deck", {}, _Out)).answer == "model instance"


async def test_fixture_callable_sees_payload():
    provider = MockProvider(
        fixtures={"echo": lambda payload: {"answer": payload["brief"].upper()}}
    )
    out = await provider.text_json("echo", {"brief": "abc"}, _Out)
    assert out.answer == "ABC"


def test_build_gateway_returns_mock_when_flag_on():
    gateway = build_gateway(Settings(mock_provider=True))
    assert isinstance(gateway, MockProvider)


def test_build_gateway_returns_openai_provider_when_flag_off():
    gateway = build_gateway(
        Settings(
            mock_provider=False,
            provider_base_url="https://llm.example.com/v1",
            provider_api_key="k",
            model_text="m",
        )
    )
    assert isinstance(gateway, OpenAICompatibleProvider)


def test_build_gateway_requires_config_when_flag_off():
    with pytest.raises(DeckDNAError) as exc:
        build_gateway(Settings(mock_provider=False))
    assert exc.value.code == "provider_capability_missing"
    assert "provider_base_url" in exc.value.details["missing"]


def test_default_settings_use_mock():
    gateway = build_gateway(Settings())
    assert isinstance(gateway, MockProvider)


async def test_self_referential_model_stops_at_depth_cap():
    """Optional[A] inside A must terminate, not recurse forever."""
    provider = MockProvider()
    out = await provider.text_json("scene_graph", {"i": 0}, _Node)
    assert isinstance(out, _Node)

    depth = 0
    node = out
    while isinstance(getattr(node, "child", None), _Node):
        node = node.child
        depth += 1
    assert depth <= 6  # _MAX_DEPTH cap + margin


async def test_recursive_model_is_still_deterministic():
    provider = MockProvider()
    a = await provider.text_json("scene_graph", {"i": 0}, _Node)
    b = await provider.text_json("scene_graph", {"i": 0}, _Node)
    assert a.model_dump() == b.model_dump()


async def test_bounded_numbers_vary_inside_range_not_just_at_bound():
    """ge/le bounds shape the hash mapping; values must not pin to the edge."""
    provider = MockProvider()
    scores, levels = [], []
    for i in range(30):
        out = await provider.text_json("bounded", {"i": i}, _Bounded)
        assert 0.0 <= out.score <= 1.0
        assert 1 <= out.level <= 5
        scores.append(out.score)
        levels.append(out.level)

    assert len(set(scores)) >= 10  # not all clamped to le=1.0
    assert len(set(levels)) >= 3
    assert any(s != 1.0 for s in scores)
    assert any(lvl not in (1, 5) for lvl in levels)


async def test_bounded_numbers_stay_deterministic_per_seed():
    provider = MockProvider()
    a = await provider.text_json("bounded", {"i": 7}, _Bounded)
    b = await provider.text_json("bounded", {"i": 7}, _Bounded)
    assert a.model_dump() == b.model_dump()
