"""Фактическая загрузка versioned prompts по реальному HTTP.

Отличие от test_openai_compat (httpx.MockTransport): здесь провайдер
ходит на настоящий localhost-сокет — доказательство, что промпт-файлы
реально читаются с диска и доезжают в тело запроса, а не только в
in-memory транспорте. Покрывает оба реестровых промпта LLM/VLM-путей:
``storyline`` (plan_deck_llm) и ``slide_checks`` (contextual audit).
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from deckdna.providers.openai_compat import OpenAICompatibleProvider
from pydantic import BaseModel


class _Out(BaseModel):
    answer: str


@pytest.fixture()
def http_endpoint():
    """Реальный localhost HTTP-сервер, отдающий chat/completion."""
    bodies: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 — имя диктует BaseHTTPRequestHandler
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            bodies.append(body)
            payload = {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({"answer": "ok"}),
                            "role": "assistant",
                        }
                    }
                ]
            }
            raw = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):  # тихий сервер в тестах
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1", bodies
    server.shutdown()
    thread.join(timeout=5)


def _provider(endpoint: str) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        endpoint,
        api_key="test-key",
        model_text="text-model",
        model_vision="vision-model",
        model_embed="embed-model",
        min_request_interval_s=0,
    )


async def test_storyline_prompt_loads_and_reaches_http_body(http_endpoint):
    endpoint, bodies = http_endpoint
    provider = _provider(endpoint)

    result = await provider.text_json(
        "storyline",
        {"brief": {"purpose": "p"}, "evidence_graph": {"nodes": []}},
        _Out,
    )
    await provider.aclose()

    assert isinstance(result, _Out)
    # instructions из prompts/content_planning/storyline.v2.yaml (последняя
    # версия) реально в теле POST /chat/completions — файл прочитан с диска
    content = bodies[0]["messages"][0]["content"]
    assert "story director" in content.lower()
    assert "## brief" in content and "## evidence_graph" in content


async def test_slide_checks_prompt_loads_and_reaches_http_body(http_endpoint):
    endpoint, bodies = http_endpoint
    provider = _provider(endpoint)

    result = await provider.vision_json(
        "slide_checks",
        ["data:image/png;base64,AAAA"],
        {"slide_index": 0, "checks": []},
        _Out,
    )
    await provider.aclose()

    assert isinstance(result, _Out)
    content = bodies[0]["messages"][0]["content"]
    # prompts/contextual_audit/slide_checks.v1.yaml прочитан с диска
    assert isinstance(content, list)
    assert content[0]["type"] == "text" and content[0]["text"].strip()
    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,AAAA"},
    }


async def test_missing_prompt_name_is_typed_not_found_over_http(http_endpoint):
    endpoint, _ = http_endpoint
    provider = _provider(endpoint)

    from deckdna.errors import DeckDNAError

    with pytest.raises(DeckDNAError) as exc:
        await provider.text_json("no_such_prompt", {}, _Out)
    await provider.aclose()

    assert exc.value.code == "not_found"
