"""OpenAICompatibleProvider robustness: typed errors по реальному HTTP.

Проверяется, что сбойные ответы endpoint'а мапятся в честные typed
DeckDNAError (provider_unavailable / structured_output_invalid) с
корректным ``retryable`` и ``http_status``, а не утекают голыми
traceback'ами. Сервер — локальный stdlib-стаб на реальном TCP,
ответ настраивается в каждом тесте (статус + тело).
"""

import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from deckdna.errors import DeckDNAError
from deckdna.providers.openai_compat import OpenAICompatibleProvider
from pydantic import BaseModel


class _Out(BaseModel):
    answer: str


@pytest.fixture()
def stub_server():
    """Стаб: отдаёт (status, body_bytes) из ``server.next`` для любого POST."""
    state = {"next": (500, b"{}")}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            status, body = state["next"]
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", state
    server.shutdown()
    thread.join(timeout=5)


def _provider(endpoint: str, **kwargs) -> OpenAICompatibleProvider:
    config = {
        "api_key": "test-key",
        "model_text": "text-model",
        "model_vision": "vision-model",
        "model_embed": "embed-model",
        "min_request_interval_s": 0,
        # near-zero so the 429/500/503-retry tests below don't burn real
        # wall-clock time on backoff; still exercises the real retry loop.
        "retry_base_delay_s": 0.001,
    }
    config.update(kwargs)
    return OpenAICompatibleProvider(endpoint, **config)


async def _text_call(provider):
    return await provider.text_json("storyline", {"brief": {}}, _Out)


async def test_4xx_is_not_retryable(stub_server):
    endpoint, state = stub_server
    state["next"] = (401, b'{"error": "unauthorized"}')
    provider = _provider(endpoint)

    with pytest.raises(DeckDNAError) as exc:
        await _text_call(provider)
    await provider.aclose()

    assert exc.value.code == "provider_unavailable"
    assert exc.value.retryable is False  # без новых креденшелов ретрай бесполезен
    assert exc.value.http_status == 401


async def test_5xx_is_retryable(stub_server):
    endpoint, state = stub_server
    state["next"] = (503, b"upstream unavailable")
    provider = _provider(endpoint)

    with pytest.raises(DeckDNAError) as exc:
        await _text_call(provider)
    await provider.aclose()

    assert exc.value.code == "provider_unavailable"
    assert exc.value.retryable is True
    assert exc.value.http_status == 503


async def test_429_is_retryable(stub_server):
    endpoint, state = stub_server
    state["next"] = (429, b'{"error": "rate limited"}')
    provider = _provider(endpoint)

    with pytest.raises(DeckDNAError) as exc:
        await _text_call(provider)
    await provider.aclose()

    assert exc.value.code == "provider_unavailable"
    assert exc.value.retryable is True


async def test_non_json_200_body_is_typed(stub_server):
    """200 + HTML-страница прокси — typed ошибка, не голый JSONDecodeError."""
    endpoint, state = stub_server
    state["next"] = (200, b"<html>Bad Gateway</html>")
    provider = _provider(endpoint)

    with pytest.raises(DeckDNAError) as exc:
        await _text_call(provider)
    await provider.aclose()

    assert exc.value.code == "structured_output_invalid"


async def test_non_object_envelope_is_typed(stub_server):
    """JSON, но не объект (список) — typed, не голый TypeError."""
    endpoint, state = stub_server
    state["next"] = (200, b"[1, 2, 3]")
    provider = _provider(endpoint)

    with pytest.raises(DeckDNAError) as exc:
        await _text_call(provider)
    await provider.aclose()

    assert exc.value.code == "structured_output_invalid"


async def test_null_content_is_typed(stub_server):
    """message.content=null (refusal/tool_calls) — typed, не TypeError."""
    endpoint, state = stub_server
    envelope = {"choices": [{"message": {"content": None, "role": "assistant"}}]}
    state["next"] = (200, json.dumps(envelope).encode())
    provider = _provider(endpoint)

    with pytest.raises(DeckDNAError) as exc:
        await _text_call(provider)
    await provider.aclose()

    assert exc.value.code == "structured_output_invalid"


async def test_embed_malformed_response_is_typed(stub_server):
    endpoint, state = stub_server
    state["next"] = (200, b'{"unexpected": "shape"}')
    provider = _provider(endpoint)

    with pytest.raises(DeckDNAError) as exc:
        await provider.embed(["текст"])
    await provider.aclose()

    assert exc.value.code == "structured_output_invalid"


async def test_embed_http_error_is_typed(stub_server):
    endpoint, state = stub_server
    state["next"] = (500, b"upstream exploded")
    provider = _provider(endpoint)

    with pytest.raises(DeckDNAError) as exc:
        await provider.embed(["текст"])
    await provider.aclose()

    assert exc.value.code == "provider_unavailable"
    assert exc.value.retryable is True
    assert exc.value.http_status == 500


async def test_embed_happy_path(stub_server):
    """Регрессия: валидный embeddings-ответ по-прежнему парсится."""
    endpoint, state = stub_server
    state["next"] = (
        200,
        json.dumps({"data": [{"embedding": [0.1, 0.2, 0.3]}]}).encode(),
    )
    provider = _provider(endpoint)

    assert await provider.embed(["текст"]) == [[0.1, 0.2, 0.3]]
    await provider.aclose()


@pytest.fixture()
def keepalive_server():
    """HTTP/1.1 keep-alive стаб: соединения живут между запросами —
    именно переиспользуемый пул ломается при смене event loop."""
    ok = json.dumps(
        {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({"answer": "ok"}),
                        "role": "assistant",
                    }
                }
            ]
        }
    ).encode()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(ok)))
            self.end_headers()
            self.wfile.write(ok)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    thread.join(timeout=5)


def test_provider_survives_multiple_event_loops(keepalive_server):
    """Один provider через три РАЗНЫХ asyncio.run: text → vision → text.

    generate() зовёт asyncio.run на каждую LLM/VLM-стадию; pooled
    keep-alive соединение прошлого цикла давало RuntimeError
    'Event loop is closed' — теперь каждый вызов открывает и
    детерминированно закрывает клиент в своём цикле.
    """
    provider = _provider(keepalive_server)

    async def _text():
        return await provider.text_json("storyline", {"brief": {}}, _Out)

    async def _vision():
        return await provider.vision_json(
            "slide_checks", ["data:image/png;base64,AAAA"], {}, _Out
        )

    assert asyncio.run(_text()).answer == "ok"      # loop 1 — планирование
    assert asyncio.run(_vision()).answer == "ok"    # loop 2 — VLM-аудит
    assert asyncio.run(_text()).answer == "ok"      # loop 3 — след. вариант
    assert provider.used_model_ids() == {
        "text": "text-model",
        "vision": "vision-model",
    }


def test_pacing_survives_multiple_event_loops(keepalive_server):
    """Same scenario as test_provider_survives_multiple_event_loops, but
    with request pacing enabled (min_request_interval_s > 0). Found live:
    _pace()'s asyncio.Lock was created once in __init__, outside any
    running loop -- it silently bound itself to the FIRST loop that
    awaited it, then raised "is bound to a different event loop" on the
    second asyncio.run(), exactly the trap this file's docstring already
    warns about for the HTTP client. _pace() must (re)create its lock
    per current running loop, the same way the client itself is scoped."""
    provider = _provider(keepalive_server, min_request_interval_s=0.01)

    async def _text():
        return await provider.text_json("storyline", {"brief": {}}, _Out)

    async def _vision():
        return await provider.vision_json(
            "slide_checks", ["data:image/png;base64,AAAA"], {}, _Out
        )

    assert asyncio.run(_text()).answer == "ok"  # loop 1
    assert asyncio.run(_vision()).answer == "ok"  # loop 2 -- new loop, must not raise
    assert asyncio.run(_text()).answer == "ok"  # loop 3


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="/proc/self/fd — Linux-only"
)
def test_client_pools_close_deterministically(keepalive_server):
    """Ресурс-регрессия: keep-alive пулы закрываются сразу после вызова,
    а не когда-нибудь в GC — fd-счётчик возвращается к базе без
    gc.collect() и без aclose(). Старый lazy-bind накапливал по
    клиенту на каждый новый цикл (в API — десятки за один 3-variant run).
    """
    provider = _provider(keepalive_server)

    async def _text():
        return await provider.text_json("storyline", {"brief": {}}, _Out)

    asyncio.run(_text())  # прогрев: первый вызов может открыть стабильные fd
    baseline = len(os.listdir("/proc/self/fd"))

    for _ in range(10):  # несколько разных event loops подряд
        asyncio.run(_text())

    assert len(os.listdir("/proc/self/fd")) == baseline
    asyncio.run(provider.aclose())
