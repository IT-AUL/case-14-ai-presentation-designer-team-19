"""VK Inference требует заголовок Project-ID (инструкция организаторов 17.10).
Провайдер шлёт дополнительные заголовки, не давая перекрыть авторизацию."""

from __future__ import annotations

import asyncio
import json

import httpx
from deckdna.providers.factory import build_gateway, provider_headers
from deckdna.providers.openai_compat import OpenAICompatibleProvider
from deckdna.settings import Settings
from pydantic import BaseModel


class Answer(BaseModel):
    answer: str


def _provider(seen: list[httpx.Request], **kw) -> OpenAICompatibleProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps({"answer": "Москва"})}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    return OpenAICompatibleProvider(
        base_url="https://vk.example/v1",
        api_key="secret-key",
        model_text="qwen38-27b-fp8",
        transport=httpx.MockTransport(handler),
        min_request_interval_s=0,
        **kw,
    )


def test_project_id_header_is_sent_and_auth_is_not_overridable():
    seen: list[httpx.Request] = []
    provider = _provider(seen, extra_headers={
        "Project-ID": "PRJ-123",
        "Authorization": "Bearer stolen",
        "X-Empty": "",
    })
    asyncio.run(provider.text_json("storyline", {"x": 1}, Answer))
    request = seen[0]
    assert request.headers["Project-ID"] == "PRJ-123"
    assert request.headers["Authorization"] == "Bearer secret-key"
    assert "X-Empty" not in request.headers


def test_factory_builds_project_header_from_settings():
    cfg = Settings(
        _env_file=None,
        mock_provider=False,
        provider_base_url="https://vk.example/v1",
        provider_api_key="k",
        model_text="qwen38-27b-fp8",
        provider_project_id="PRJ-9",
        provider_extra_headers={"X-Trace": "1"},
    )
    assert provider_headers(cfg) == {"X-Trace": "1", "Project-ID": "PRJ-9"}
    gateway = build_gateway(cfg)
    assert gateway._extra_headers == {"X-Trace": "1", "Project-ID": "PRJ-9"}


def test_no_project_id_means_no_extra_header():
    cfg = Settings(_env_file=None, provider_project_id="")
    assert provider_headers(cfg) == {}


def test_provider_session_carries_the_vk_project_id():
    from deckdna.api import app as app_module
    from fastapi.testclient import TestClient

    client = TestClient(app_module.app)
    pid = client.post("/api/v1/projects", json={"name": "vk"}).json()["id"]
    resp = client.post("/api/v1/provider-sessions", json={
        "label": "VK Inference",
        "base_url": "https://ai-demo.platformpreview.ru/api/inference-providers-router/"
                    "00000000-0000-0000-0000-000000000001/v1",
        "api_token": "k",
        "models": {"text": "qwen38-27b-fp8", "vision": ""},
        "project_id": pid,
        "provider_project_id": "PRJ-UUID",
    })
    assert resp.status_code == 201, resp.text
    session = app_module.STORE.provider_sessions[resp.json()["id"]]
    gateway = app_module._session_gateway(session)
    assert gateway._extra_headers == {"Project-ID": "PRJ-UUID"}


def test_structured_answer_survives_think_blocks_and_fences():
    """Qwen на VK может прислать рассуждение <think> или обёртку ```json —
    структурированный ответ всё равно разбирается."""
    from deckdna.providers.openai_compat import extract_json_text

    for content in (
        '{"answer": "Москва"}',
        '```json\n{"answer": "Москва"}\n```',
        '<think>сначала подумаю {"x": 1}</think>\n{"answer": "Москва"}',
        'Ответ: {"answer": "Мо}сква \\"столица\\""} — готово',
    ):
        assert json.loads(extract_json_text(content))["answer"].startswith("Мо")

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {
            "content": '<think>…</think>```json\n{"answer": "Москва"}\n```'}}]})

    provider = OpenAICompatibleProvider(
        base_url="https://vk.example/v1", api_key="k", model_text="m",
        transport=httpx.MockTransport(handler), min_request_interval_s=0,
    )
    assert asyncio.run(provider.text_json("storyline", {}, Answer)).answer == "Москва"


class Verdicts(BaseModel):
    verdicts: list[Answer]


def test_schema_goes_into_the_prompt_and_a_wrong_shape_gets_one_fix():
    """VK Inference не навязывает json_schema: Qwen ответил на slide_checks
    своей формой. Схема дублируется текстом в промпте, а ответ не той
    формы возвращается модели на одно исправление (17.10)."""
    bodies: list[dict] = []
    replies = [
        '{"check_1": {"answer": "Москва"}}',          # своя форма
        '{"verdicts": [{"answer": "Москва"}]}',        # исправлено
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {
            "content": replies[len(bodies) - 1]}}]})

    provider = OpenAICompatibleProvider(
        base_url="https://vk.example/v1", api_key="k", model_text="m",
        transport=httpx.MockTransport(handler), min_request_interval_s=0,
    )
    out = asyncio.run(provider.text_json("storyline", {}, Verdicts))
    assert out.verdicts[0].answer == "Москва"
    first, second = bodies
    assert first["messages"][0]["role"] == "user"
    assert '"verdicts"' in first["messages"][0]["content"]
    assert second["messages"][-2] == {"role": "assistant", "content": replies[0]}
    assert "JSON Schema" in second["messages"][-1]["content"]


def test_still_invalid_after_the_fix_is_a_typed_error():
    import pytest
    from deckdna.errors import DeckDNAError

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"choices": [{"message": {"content": "не JSON"}}]})

    provider = OpenAICompatibleProvider(
        base_url="https://vk.example/v1", api_key="k", model_text="m",
        transport=httpx.MockTransport(handler), min_request_interval_s=0,
    )
    with pytest.raises(DeckDNAError) as exc:
        asyncio.run(provider.text_json("storyline", {}, Verdicts))
    assert exc.value.code == "structured_output_invalid"
    assert len(calls) == 2
