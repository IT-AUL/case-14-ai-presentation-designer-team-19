import httpx
import pytest
from deckdna.providers.factory import build_gateway
from deckdna.providers.openai_compat import OpenAICompatibleProvider
from deckdna.settings import Settings
from pydantic import BaseModel


class _TestSchema(BaseModel):
    answer: str


_PAYLOAD = {"brief": "тест", "evidence_graph": {"nodes": []}}


@pytest.mark.asyncio
async def test_fallback_called_on_primary_http_error():
    primary_called = False
    fallback_called = False

    def primary_handler(request: httpx.Request) -> httpx.Response:
        nonlocal primary_called
        primary_called = True
        return httpx.Response(500, json={"error": {"message": "Server error"}})

    def fallback_handler(request: httpx.Request) -> httpx.Response:
        nonlocal fallback_called
        fallback_called = True
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"answer": "from_fallback"}'}}]},
        )

    primary_transport = httpx.MockTransport(primary_handler)
    fallback_transport = httpx.MockTransport(fallback_handler)

    fallback = OpenAICompatibleProvider(
        base_url="https://fallback.example.com/v1",
        api_key="fb-key",
        model_text="fb-model",
        transport=fallback_transport,
    )
    primary = OpenAICompatibleProvider(
        base_url="https://primary.example.com/v1",
        api_key="pri-key",
        model_text="pri-model",
        transport=primary_transport,
        fallback_provider=fallback,
    )

    res = await primary.text_json("storyline", _PAYLOAD, _TestSchema)
    assert primary_called is True
    assert fallback_called is True
    assert res.answer == "from_fallback"
    assert primary.used_model_ids()["text"] == "fb-model"


@pytest.mark.asyncio
async def test_fallback_not_called_when_primary_succeeds():
    fallback_called = False

    def primary_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"answer": "from_primary"}'}}]},
        )

    def fallback_handler(request: httpx.Request) -> httpx.Response:
        nonlocal fallback_called
        fallback_called = True
        return httpx.Response(200, json={})

    primary_transport = httpx.MockTransport(primary_handler)
    fallback_transport = httpx.MockTransport(fallback_handler)

    fallback = OpenAICompatibleProvider(
        base_url="https://fallback.example.com/v1",
        api_key="fb-key",
        model_text="fb-model",
        transport=fallback_transport,
    )
    primary = OpenAICompatibleProvider(
        base_url="https://primary.example.com/v1",
        api_key="pri-key",
        model_text="pri-model",
        transport=primary_transport,
        fallback_provider=fallback,
    )

    res = await primary.text_json("storyline", _PAYLOAD, _TestSchema)
    assert fallback_called is False
    assert res.answer == "from_primary"
    assert primary.used_model_ids()["text"] == "pri-model"


def test_factory_builds_fallback_gateway():
    cfg = Settings(
        mock_provider=False,
        provider_base_url="https://vk.example.com/v1",
        provider_api_key="vk-key",
        model_text="vk-qwen",
        fallback_provider_base_url="https://cerebras.example.com/v1",
        fallback_provider_api_key="cerebras-key",
        fallback_model_text="cerebras-qwen",
    )
    gw = build_gateway(cfg)
    assert isinstance(gw, OpenAICompatibleProvider)
    assert gw._fallback_provider is not None
    assert gw._fallback_provider._base_url == "https://cerebras.example.com/v1"
    assert gw._fallback_provider._model_text == "cerebras-qwen"


@pytest.mark.asyncio
async def test_fallback_circuit_breaker_skips_primary_during_cooldown():
    primary_call_count = 0
    fallback_call_count = 0

    def primary_handler(request: httpx.Request) -> httpx.Response:
        nonlocal primary_call_count
        primary_call_count += 1
        return httpx.Response(500, json={"error": {"message": "Server error"}})

    def fallback_handler(request: httpx.Request) -> httpx.Response:
        nonlocal fallback_call_count
        fallback_call_count += 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"answer": "ok"}'}}]},
        )

    fallback = OpenAICompatibleProvider(
        base_url="https://fallback.example.com/v1",
        api_key="fb-key",
        model_text="fb-model",
        transport=httpx.MockTransport(fallback_handler),
    )
    primary = OpenAICompatibleProvider(
        base_url="https://primary.example.com/v1",
        api_key="pri-key",
        model_text="pri-model",
        transport=httpx.MockTransport(primary_handler),
        fallback_provider=fallback,
    )

    # First call: primary fails, fallback responds
    await primary.text_json("storyline", _PAYLOAD, _TestSchema)
    assert primary_call_count >= 1
    assert fallback_call_count == 1

    # Second call: primary should be cooled down and skipped!
    prev_pri_calls = primary_call_count
    await primary.text_json("storyline", _PAYLOAD, _TestSchema)
    assert primary_call_count == prev_pri_calls  # Primary was skipped directly!
    assert fallback_call_count == 2


@pytest.mark.asyncio
async def test_fallback_session_context():
    fallback_called = False

    def fallback_handler(request: httpx.Request) -> httpx.Response:
        nonlocal fallback_called
        fallback_called = True
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"answer": "session_ok"}'}}]},
        )

    def primary_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "fail"})

    fallback = OpenAICompatibleProvider(
        base_url="https://fallback.example.com/v1",
        api_key="fb-key",
        model_text="fb-model",
        transport=httpx.MockTransport(fallback_handler),
    )
    primary = OpenAICompatibleProvider(
        base_url="https://primary.example.com/v1",
        api_key="pri-key",
        model_text="pri-model",
        transport=httpx.MockTransport(primary_handler),
        fallback_provider=fallback,
    )

    async with primary.session():
        res = await primary.text_json("storyline", _PAYLOAD, _TestSchema)
        assert res.answer == "session_ok"
    assert fallback_called is True
