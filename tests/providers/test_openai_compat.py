"""OpenAI-compatible provider: request contract and error mapping.

All tests run against httpx.MockTransport — no real network calls.
"""

import asyncio
import base64
import json

import httpx
import pytest
from deckdna.errors import DeckDNAError
from deckdna.providers.openai_compat import OpenAICompatibleProvider
from pydantic import BaseModel


class _Out(BaseModel):
    answer: str


def _provider(handler, **kwargs):
    transport = httpx.MockTransport(handler)
    config = {
        "base_url": "https://llm.example.com/v1",
        "api_key": "test-key",
        "model_text": "text-model",
        "model_vision": "vision-model",
        "model_embed": "embed-model",
        "transport": transport,
        # near-zero so retry-path tests below don't burn real wall-clock
        # time; still exercises the real retry/pacing loops (production
        # defaults are 1.0s / 0.5s), just without waiting for them.
        "retry_base_delay_s": 0.001,
        "min_request_interval_s": 0.0,
    }
    config.update(kwargs)
    return OpenAICompatibleProvider(**config)


async def test_text_json_request_contract():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"answer": "ok"})}}]},
        )

    provider = _provider(handler)
    result = await provider.text_json("storyline", {"brief": "x"}, _Out)

    assert result.answer == "ok"
    assert seen["url"].endswith("/v1/chat/completions")
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["model"] == "text-model"
    assert seen["body"]["response_format"]["type"] == "json_schema"
    schema = seen["body"]["response_format"]["json_schema"]
    assert schema["name"] == "_Out"
    assert schema["strict"] is True
    await provider.aclose()


async def test_vision_json_sends_image_parts():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"answer": "v"})}}]},
        )

    provider = _provider(handler)
    result = await provider.vision_json(
        "slide_checks", ["https://img/1.png", "https://img/2.png"], {"k": 1}, _Out
    )

    assert result.answer == "v"
    content = seen["body"]["messages"][0]["content"]
    assert seen["body"]["model"] == "vision-model"
    assert content[0]["type"] == "text"
    assert [p["image_url"]["url"] for p in content[1:]] == [
        "https://img/1.png",
        "https://img/2.png",
    ]
    await provider.aclose()


async def test_vision_without_model_is_capability_missing():
    provider = _provider(lambda r: httpx.Response(200), model_vision="")
    with pytest.raises(DeckDNAError) as exc:
        await provider.vision_json("slide_checks", ["https://img/1.png"], {}, _Out)
    assert exc.value.code == "provider_capability_missing"
    assert not exc.value.retryable
    await provider.aclose()


async def test_embed_posts_to_embeddings():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]}
        )

    provider = _provider(handler)
    vectors = await provider.embed(["a", "b"])

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert seen["url"].endswith("/v1/embeddings")
    assert seen["body"]["model"] == "embed-model"
    assert seen["body"]["input"] == ["a", "b"]
    await provider.aclose()


async def test_embed_without_model_is_capability_missing():
    provider = _provider(lambda r: httpx.Response(200), model_embed="")
    with pytest.raises(DeckDNAError) as exc:
        await provider.embed(["x"])
    assert exc.value.code == "provider_capability_missing"
    await provider.aclose()


async def test_image_generate_posts_to_images_generations():
    """ADR-017: request contract for the new image_generate method."""
    seen = {}
    png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\nfake-but-decodable").decode()

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"b64_json": png_b64}]})

    provider = _provider(handler, model_image="image-model")
    data = await provider.image_generate("a friendly robot", size="512x512")

    assert data == base64.b64decode(png_b64)
    assert seen["url"].endswith("/v1/images/generations")
    assert seen["body"] == {
        "model": "image-model",
        "prompt": "a friendly robot",
        "size": "512x512",
        "response_format": "b64_json",
    }
    assert provider.used_model_ids()["image"] == "image-model"
    await provider.aclose()


async def test_image_generate_without_model_is_capability_missing():
    provider = _provider(lambda r: httpx.Response(200))  # no model_image kwarg
    with pytest.raises(DeckDNAError) as exc:
        await provider.image_generate("x")
    assert exc.value.code == "provider_capability_missing"
    assert not exc.value.retryable
    await provider.aclose()


async def test_image_generate_malformed_envelope_is_structured_output_invalid():
    provider = _provider(
        lambda r: httpx.Response(200, json={"data": []}), model_image="image-model"
    )
    with pytest.raises(DeckDNAError) as exc:
        await provider.image_generate("x")
    assert exc.value.code == "structured_output_invalid"
    await provider.aclose()


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_http_error_maps_to_provider_unavailable(status):
    """Stateless handler always returns the same status -- retries run
    (2, by the near-zero-delay test default) and still exhaust, proving
    retries don't change the final error shape."""
    calls = []

    def handler(r):
        calls.append(1)
        return httpx.Response(status, json={"error": "x"})

    provider = _provider(handler)
    with pytest.raises(DeckDNAError) as exc:
        await provider.text_json("storyline", {}, _Out)
    assert exc.value.code == "provider_unavailable"
    assert exc.value.retryable
    assert exc.value.http_status == status
    assert len(calls) == 3  # 1 initial attempt + 2 retries (default max_retries)
    await provider.aclose()


@pytest.mark.parametrize("status", [400, 401, 404])
async def test_client_http_error_is_not_retryable(status):
    calls = []

    def handler(r):
        calls.append(1)
        return httpx.Response(status, json={"error": "x"})

    provider = _provider(handler)
    with pytest.raises(DeckDNAError) as exc:
        await provider.text_json("storyline", {}, _Out)
    assert exc.value.code == "provider_unavailable"
    assert not exc.value.retryable
    assert exc.value.http_status == status
    assert len(calls) == 1  # non-retryable: fails on the first attempt
    await provider.aclose()


async def test_429_retry_succeeds_on_second_attempt():
    """The scenario found live (three variants hitting a rate-limited
    provider concurrently): a single transient 429
    must not kill the whole call — a later attempt succeeding is accepted
    same as if there had been no failure at all."""
    calls = []

    def handler(r):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"answer": "ok"})}}]},
        )

    provider = _provider(handler)
    result = await provider.text_json("storyline", {}, _Out)
    assert result.answer == "ok"
    assert len(calls) == 2
    await provider.aclose()


async def test_429_retry_honors_retry_after_header():
    delays: list[float] = []
    provider = _provider(
        lambda r: httpx.Response(429, headers={"Retry-After": "7"}, json={"error": "x"})
    )

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    provider._sleep = fake_sleep
    with pytest.raises(DeckDNAError):
        await provider.text_json("storyline", {}, _Out)
    assert delays == [7.0, 7.0]  # both retries honor the server's hint
    await provider.aclose()


async def test_max_retries_zero_fails_on_first_429():
    calls = []

    def handler(r):
        calls.append(1)
        return httpx.Response(429, json={"error": "x"})

    provider = _provider(handler, max_retries=0)
    with pytest.raises(DeckDNAError):
        await provider.text_json("storyline", {}, _Out)
    assert len(calls) == 1
    await provider.aclose()


async def test_min_request_interval_spaces_out_concurrent_calls():
    """Found live: a paid Cerebras account hit 429s not from too many
    REQUESTS but from too many TOKENS/minute (150K uncached TPM) — several
    batched stages fire concurrently (each with their own Semaphore(4))
    across up to 3 variants sharing one gateway. Pacing spreads the START
    of each outgoing request out over time instead of firing a burst."""
    provider = _provider(
        lambda r: httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"answer": "ok"})}}]},
        ),
        min_request_interval_s=0.05,
    )
    started: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        started.append(seconds)

    provider._sleep = fake_sleep
    # 3 "concurrent" callers, as _plan_all_strategies fires across variants.
    await asyncio.gather(*(provider.text_json("storyline", {}, _Out) for _ in range(3)))
    # first call never waits; the other two each queue behind the pace lock.
    assert len(started) == 2
    assert started[0] == pytest.approx(0.05, abs=0.01)
    assert started[1] == pytest.approx(0.05, abs=0.01)
    await provider.aclose()


async def test_min_request_interval_zero_disables_pacing():
    provider = _provider(
        lambda r: httpx.Response(200, json={"choices": []}), min_request_interval_s=0.0
    )
    calls = 0

    async def fake_sleep(seconds: float) -> None:
        nonlocal calls
        calls += 1

    provider._sleep = fake_sleep
    with pytest.raises(DeckDNAError):
        await provider.text_json("storyline", {}, _Out)
    assert calls == 0  # _pace() is a no-op at interval 0 -- never calls _sleep
    await provider.aclose()


async def test_network_failure_maps_to_provider_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    provider = _provider(handler)
    with pytest.raises(DeckDNAError) as exc:
        await provider.text_json("storyline", {}, _Out)
    assert exc.value.code == "provider_unavailable"
    await provider.aclose()


@pytest.mark.parametrize(
    "body",
    [
        {"choices": [{"message": {"content": "not json"}}]},
        {"choices": [{"message": {"content": json.dumps({"wrong": 1})}}]},
        {"choices": []},
    ],
)
async def test_invalid_output_maps_to_structured_output_invalid(body):
    provider = _provider(lambda r: httpx.Response(200, json=body))
    with pytest.raises(DeckDNAError) as exc:
        await provider.text_json("storyline", {}, _Out)
    assert exc.value.code == "structured_output_invalid"
    assert not exc.value.retryable
    await provider.aclose()


async def test_real_prompt_text_reaches_request_body():
    """_render теперь резолвит реальный yaml-промпт: instructions +
    payload по input_fields должны долететь до messages[0].content."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"answer": "ok"})}}]},
        )

    provider = _provider(handler)
    await provider.text_json(
        "storyline",
        {"brief": "корпоративный отчёт", "evidence_graph": {"nodes": []}},
        _Out,
    )

    content = seen["body"]["messages"][0]["content"]
    assert "story director" in content.lower()
    assert "## brief\nкорпоративный отчёт" in content
    assert '## evidence_graph\n{"nodes": []}' in content
    # поле input_fields, не присланное в payload, видно как null
    assert "## design_dna_capacities\nnull" in content
    await provider.aclose()


async def test_unknown_prompt_name_is_typed_not_found():
    provider = _provider(lambda r: httpx.Response(200))
    with pytest.raises(DeckDNAError) as exc:
        await provider.text_json("no_such_prompt", {}, _Out)
    assert exc.value.code == "not_found"
    await provider.aclose()


def test_provider_name_sanitizes_userinfo():
    """provider_name попадает в Quality Passport — userinfo из URL
    (user:pass@host) не должно туда утечь."""
    with_creds = _provider(
        lambda r: httpx.Response(200),
        base_url="https://user:secret@llm.example.com:8443/v1",
    )
    assert with_creds.provider_name == "llm.example.com:8443"
    assert "secret" not in with_creds.provider_name
    assert "user" not in with_creds.provider_name

    plain = _provider(lambda r: httpx.Response(200))
    assert plain.provider_name == "llm.example.com"


def test_provider_name_sanitizes_schemeless_url():
    """URL без схемы: urlparse не даёт hostname — fallback не имеет права
    вернуть сырую строку, там может быть userinfo/токен."""
    creds = _provider(
        lambda r: httpx.Response(200),
        base_url="user:secret@llm.example.com:8443/v1",
    )
    assert creds.provider_name == "llm.example.com:8443"
    assert "secret" not in creds.provider_name
    assert "user" not in creds.provider_name
