"""VK Inference adapter: request-contract tests with a mocked transport,
plus an env-gated live smoke test for the real endpoint.

The TZ makes VK inference mandatory for top-10 teams, so the adapter
must be proven before the defense — not just documented.
"""

import json
import os

import httpx
import pytest
from deckdna.errors import DeckDNAError
from deckdna.providers.vk_inference import DEFAULT_VK_MODEL, VKInferenceProvider
from pydantic import BaseModel


class _Out(BaseModel):
    answer: str


def _provider(handler):
    transport = httpx.MockTransport(handler)
    return VKInferenceProvider(
        base_url="https://vk-inference.example.com/v1",
        api_key="test-key",
        transport=transport,
    )


async def test_request_contract():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps({"answer": "ok"})}}
                ]
            },
        )

    provider = _provider(handler)
    result = await provider.text_json("storyline", {"brief": "x"}, _Out)

    assert result.answer == "ok"
    assert seen["url"].endswith("/v1/chat/completions")
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["model"] == DEFAULT_VK_MODEL
    assert seen["body"]["response_format"]["type"] == "json_schema"
    assert seen["body"]["response_format"]["json_schema"]["name"] == "_Out"
    await provider.aclose()


async def test_http_error_maps_to_typed_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "down"})

    provider = _provider(handler)
    with pytest.raises(DeckDNAError) as exc:
        await provider.text_json("storyline", {}, _Out)
    assert exc.value.code == "provider_unavailable"
    assert exc.value.retryable
    await provider.aclose()


async def test_malformed_output_maps_to_structured_output_invalid():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "not json"}}]}
        )

    provider = _provider(handler)
    with pytest.raises(DeckDNAError) as exc:
        await provider.text_json("storyline", {}, _Out)
    assert exc.value.code == "structured_output_invalid"
    await provider.aclose()


async def test_env_wiring(monkeypatch):
    monkeypatch.setenv("DECKDNA_VK_BASE_URL", "https://x/v1")
    monkeypatch.setenv("DECKDNA_VK_API_KEY", "k")
    monkeypatch.setenv("DECKDNA_VK_MODEL", "qwen-custom")
    provider = VKInferenceProvider(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert provider._model_text == "qwen-custom"  # noqa: SLF001
    await provider.aclose()


LIVE = os.environ.get("DECKDNA_VK_LIVE") == "1"


@pytest.mark.skipif(not LIVE, reason="set DECKDNA_VK_LIVE=1 + VK creds to run")
async def test_live_endpoint_smoke():
    """Real smoke test against the VK endpoint — run before the defense."""
    provider = VKInferenceProvider()
    out = await provider.text_json("storyline", {"brief": "say ok"}, _Out)
    assert out.answer
    await provider.aclose()
