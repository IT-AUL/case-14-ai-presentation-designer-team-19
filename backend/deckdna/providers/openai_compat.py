"""OpenAI-compatible chat-completions provider.

Works against any endpoint exposing POST {base_url}/chat/completions —
self-hosted vLLM/TGI, OpenRouter-style routers, and VK Inference once
its base URL is supplied. Secrets never enter logs or persistence.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import random
import time
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ValidationError

from deckdna.errors import DeckDNAError
from deckdna.provider_options import ChatOptions
from deckdna.providers.base import json_schema_of
from deckdna.providers.prompts import render_prompt_by_name

logger = logging.getLogger(__name__)


class _ProbeAck(BaseModel):
    """Ответ probe-вызова: минимальная inline-схема без prompt registry."""

    ok: bool


_PROBE_MESSAGE = 'Health probe. Respond with JSON: {"ok": true}'

# Found live: 3 variants planning/reranking/styling concurrently against a
# rate-limited provider (Cerebras dev tier) hit a 429 on ONE call and that
# alone killed the whole variant -- retryable=True was set on the error but
# nothing ever actually retried (it only fed the API's 503-vs-422 mapping).
# Bounded exponential backoff (honors Retry-After when the server sends
# one) turns a transient rate-limit blip into a short pause instead of a
# hard failure, without raising overall concurrency (which would fight the
# 5-minute budget elsewhere).
_DEFAULT_MAX_RETRIES = 2
_DEFAULT_RETRY_BASE_DELAY_S = 1.0
_RETRY_MAX_DELAY_S = 20.0

# Confirmed live (27.09): the account hitting
# 429s was on a paid tier limited by TOKENS/minute (150K uncached TPM), not
# request COUNT — several batched stages (storyline/content_style/rerank,
# each with their own Semaphore(4)) fire concurrently across up to 3
# variants sharing one gateway during planning, bursting well past that in
# a few seconds even though request count alone is nowhere near a typical
# RPM cap. Retries (above) are reactive — they only help after a 429
# already happened. This is proactive: a floor on the gap between the
# START of consecutive outgoing requests from this provider instance,
# spreading a burst out over time instead of firing it all at once.
_DEFAULT_MIN_REQUEST_INTERVAL_S = 0.5

# 1×1 прозрачный PNG — минимальная картинка для probe image_input.
_PROBE_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _sanitize_provider_name(base_url: str) -> str:
    """host[:port] для паспорта — никогда не уносит креденшелы из URL.

    netloc может содержать userinfo (user:pass@host); кроме того,
    urlparse не выдаёт hostname для URL без схемы, поэтому сырой
    base_url в fallback вернуть нельзя — там тоже может быть секрет.
    """
    netloc = urlparse(base_url).netloc
    if not netloc:
        # URL без схемы: часть до первого '/' или '?', затем отбрасываем userinfo
        netloc = base_url.split("://", 1)[-1].split("/", 1)[0].split("?", 1)[0]
    return netloc.rsplit("@", 1)[-1]


class OpenAICompatibleProvider:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        model_text: str,
        model_vision: str = "",
        model_embed: str = "",
        model_image: str = "",
        timeout_s: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        retry_base_delay_s: float = _DEFAULT_RETRY_BASE_DELAY_S,
        min_request_interval_s: float = _DEFAULT_MIN_REQUEST_INTERVAL_S,
        chat_options: ChatOptions | dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        self._model_text = model_text
        # Provider-specific request fields (e.g. OpenRouter's
        # {"provider": {"sort": "throughput"}}). Merged FIRST, so they can
        # never override the model, messages or structured-output format.
        self._extra_body = dict(extra_body or {})
        self._chat_options = ChatOptions.model_validate(chat_options or {}).model_dump(
            exclude_none=True
        )
        self._model_vision = model_vision
        self._model_embed = model_embed
        self._model_image = model_image
        self._used: dict[str, str] = {}
        self._usage: dict[str, dict[str, int]] = {}
        self._max_retries = max_retries
        self._retry_base_delay_s = retry_base_delay_s
        self._min_request_interval_s = min_request_interval_s
        # Lazily (re)created per running loop in _pace() -- an asyncio.Lock
        # made here, outside any loop, binds to whichever loop first awaits
        # it and breaks on a later asyncio.run() with the exact error
        # test_provider_survives_multiple_event_loops exists to catch for
        # the HTTP client (see that test and _use_client() below); a
        # provider instance living across several asyncio.run scopes is
        # the normal case (session_gateway, portable-skill runs), not an
        # edge case.
        self._pace_lock: asyncio.Lock | None = None
        self._pace_loop: asyncio.AbstractEventLoop | None = None
        self._next_request_at = 0.0
        self.provider_name = _sanitize_provider_name(base_url)
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._transport = transport
        # Фаза 3: опциональный переиспользуемый
        # клиент для вызовов ВНУТРИ одного ``async with self.session():``
        # блока — не создаётся и не переживает отдельный вызов по
        # умолчанию. Ранняя версия этой фазы пробовала лениво привязывать
        # один клиент к «текущему running loop» неявно на каждом вызове
        # (без explicit session) — это ломает ровно тот сценарий, что уже
        # закрыт регрессионными тестами ниже: один provider, три РАЗНЫХ
        # asyncio.run подряд (план → VLM-аудит → следующий вариант) БЕЗ
        # явного aclose() между ними — старый клиент чужого/закрытого
        # loop'а либо валится «Event loop is closed», либо копится по
        # клиенту на каждый новый цикл, если просто отбросить ссылку (см.
        # tests/providers/test_provider_robustness.py::
        # test_provider_survives_multiple_event_loops и
        # ::test_client_pools_close_deterministically). Поэтому вне
        # активной сессии поведение остаётся прежним: каждый вызов
        # открывает и детерминированно закрывает СВОЙ клиент — безопасный
        # дефолт; connection reuse — осознанный opt-in для мест, которые
        # гарантируют один asyncio.run на весь блок (generation/pipeline.py,
        # api/app.py — см. ``session()``).
        self._session_client: httpx.AsyncClient | None = None

    def _new_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                # Found live 28.09: Cerebras (Cloudflare-fronted) started
                # returning a bare 403 "error code: 1010" ("browser's
                # signature" ban) for every /chat/completions call in this
                # environment -- reproduced outside the app too, and it
                # cleared with ANY explicit User-Agent, including httpx's
                # own default string set explicitly. httpx.AsyncClient does
                # send a default UA, but something in this network path
                # (proxy/NAT strips it, or a Cloudflare heuristic keys off
                # header ORDER/casing rather than bare absence) was enough
                # to trip WAF classification. Pinning an explicit,
                # descriptive UA is the documented-safe fix, not a guess.
                "User-Agent": "DeckDNA/1.0 (+https://github.com/deckdna/deckdna)",
            },
            timeout=self._timeout_s,
            transport=self._transport,
        )

    @contextlib.asynccontextmanager
    async def _use_client(self) -> AsyncIterator[httpx.AsyncClient]:
        """Клиент для ОДНОГО вызова: активная сессия (см. ``session()``),
        если она сейчас открыта в этом же running loop'е, иначе — свежий
        клиент, закрываемый сразу после вызова (прежнее, всегда безопасное
        поведение — см. комментарий в ``__init__``)."""
        if self._session_client is not None:
            yield self._session_client
            return
        client = self._new_client()
        try:
            yield client
        finally:
            await client.aclose()

    @contextlib.asynccontextmanager
    async def session(self) -> AsyncIterator[None]:
        """Opt-in: все вызовы provider'а ВНУТРИ этого ``async with`` делят
        один ``httpx.AsyncClient`` (keep-alive пул вместо TLS-хендшейка на
        каждый POST) — используется вызывающей стороной вокруг ОДНОГО
        asyncio.run-скоупа (один batched план/rerank/fit/аудит вызов на
        вариант), где с батчингом Фазы 1/ADR-012 отдельных HTTP-вызовов
        стало заметно больше. Закрывает клиент детерминированно на выходе
        из блока (даже при исключении) — НЕ полагается на то, что тот же
        loop ещё жив к моменту закрытия. Вложенный вызов (session внутри
        session) безопасен — переиспользует уже открытый клиент и ничего
        не закрывает на внутреннем выходе.
        """
        if self._session_client is not None:
            yield
            return
        client = self._new_client()
        self._session_client = client
        try:
            yield
        finally:
            self._session_client = None
            await client.aclose()

    async def aclose(self) -> None:
        """Закрыть текущую сессию, если она почему-то осталась открытой
        (``session()`` уже закрывает её сам на выходе из ``async with`` —
        это защитная сетка, а не основной путь). Без активной сессии —
        no-op, как и раньше: обычные вызовы сами открывают и закрывают
        свой клиент."""
        if self._session_client is not None:
            client, self._session_client = self._session_client, None
            await client.aclose()

    def used_model_ids(self) -> dict[str, str]:
        """role -> model_id for calls that completed successfully."""
        return dict(self._used)

    def usage_report(self) -> dict[str, dict[str, int]]:
        """model_id -> {calls, input_tokens, output_tokens} (UsageSource)."""
        return {model: dict(counts) for model, counts in self._usage.items()}

    def _record_usage(self, model: str, data: dict[str, Any]) -> None:
        counts = self._usage.setdefault(
            model, {"calls": 0, "input_tokens": 0, "output_tokens": 0}
        )
        counts["calls"] += 1
        usage = data.get("usage")
        if isinstance(usage, dict):
            for key, field in (
                ("input_tokens", "prompt_tokens"),
                ("output_tokens", "completion_tokens"),
            ):
                value = usage.get(field)
                if isinstance(value, int) and not isinstance(value, bool):
                    counts[key] += value

    async def _sleep(self, seconds: float) -> None:
        """Isolated so tests can patch it and skip the real wall-clock wait."""
        await asyncio.sleep(seconds)

    async def _pace(self) -> None:
        """Block until at least ``self._min_request_interval_s`` has
        elapsed since the last outgoing request STARTED (not finished —
        concurrent callers each reserve their own slot under the lock
        before releasing it, so a slow response doesn't stall the next
        caller's wait longer than necessary). A no-op when the interval is
        0 (the pre-existing, unthrottled default some callers may still
        want, e.g. a high-RPM provider)."""
        if self._min_request_interval_s <= 0:
            return
        loop = asyncio.get_running_loop()
        if self._pace_lock is None or self._pace_loop is not loop:
            # First use, or a new asyncio.run() scope since the last one --
            # a lock from a different loop can't be reused (see __init__).
            # _next_request_at is a time.monotonic() timestamp, which is
            # loop-independent, so it stays meaningful across the switch.
            self._pace_lock = asyncio.Lock()
            self._pace_loop = loop
        async with self._pace_lock:
            wait = self._next_request_at - time.monotonic()
            if wait > 0:
                await self._sleep(wait)
            self._next_request_at = time.monotonic() + self._min_request_interval_s

    def _retry_delay(self, attempt: int, retry_after: str | None) -> float:
        """Seconds to wait before retry *attempt* (0-indexed). Honors the
        server's ``Retry-After`` header when present and parseable as a
        plain integer of seconds (the only form these APIs send in
        practice — HTTP-date values aren't worth parsing here);
        otherwise exponential backoff with jitter so several concurrent
        callers retrying the same rate limit don't all wake up at once."""
        if retry_after is not None:
            with contextlib.suppress(ValueError):
                return min(float(retry_after), _RETRY_MAX_DELAY_S)
        delay = min(self._retry_base_delay_s * (2**attempt), _RETRY_MAX_DELAY_S)
        return delay * (0.5 + random.random())  # noqa: S311 — jitter, not crypto

    async def _post_with_retry(
        self, path: str, body: dict[str, Any], *, model: str, error_prefix: str
    ) -> httpx.Response:
        """POST *path*, retrying a 429/5xx/network failure up to
        ``self._max_retries`` times with backoff. A non-retryable 4xx (or
        the final retry) raises the exact ``DeckDNAError`` shape a
        non-retrying call would — callers never need to know retries
        happened, only that the call eventually failed."""
        attempt = 0
        while True:
            try:
                await self._pace()
                async with self._use_client() as client:
                    resp = await client.post(path, json=body)
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as exc:
                http_status = exc.response.status_code
                retryable = http_status == 429 or http_status >= 500
                if not retryable or attempt >= self._max_retries:
                    raise DeckDNAError(
                        code="provider_unavailable",
                        message=f"{error_prefix} HTTP {http_status}",
                        stage="providers",
                        retryable=retryable,
                        http_status=http_status,
                        details={"model": model},
                    ) from exc
                delay = self._retry_delay(attempt, exc.response.headers.get("Retry-After"))
                logger.warning(
                    "provider call retry: model=%s status=%d attempt=%d/%d delay=%.1fs",
                    model,
                    http_status,
                    attempt + 1,
                    self._max_retries,
                    delay,
                )
            except httpx.HTTPError as exc:
                if attempt >= self._max_retries:
                    raise DeckDNAError(
                        code="provider_unavailable",
                        message=f"{error_prefix} request failed: {exc.__class__.__name__}",
                        stage="providers",
                        retryable=True,
                        details={"model": model},
                    ) from exc
                delay = self._retry_delay(attempt, None)
                logger.warning(
                    "provider call retry: model=%s error=%s attempt=%d/%d delay=%.1fs",
                    model,
                    exc.__class__.__name__,
                    attempt + 1,
                    self._max_retries,
                    delay,
                )
            await self._sleep(delay)
            attempt += 1

    async def _chat(
        self, model: str, body: dict[str, Any], *, role: str = ""
    ) -> dict[str, Any]:
        """POST /chat/completions with structured start/duration/status
        logging around every call — this is what let the live 24-minute
        contextual-audit hang go completely invisible (root logger had no
        handler at the time, so nothing here reached ``docker compose
        logs``; see ``_configure_logging`` in ``api/app.py``)."""
        started = time.monotonic()
        status = "ok"
        logger.info("provider call start: role=%s model=%s", role or "?", model)
        try:
            try:
                resp = await self._post_with_retry(
                    "/chat/completions", body, model=model, error_prefix="provider"
                )
            except DeckDNAError as exc:
                status = (
                    f"http_{exc.http_status}"
                    if exc.http_status is not None
                    else f"network_{type(exc.__cause__).__name__}"
                )
                raise
            try:
                data = resp.json()
            except json.JSONDecodeError as exc:
                status = "invalid_json"
                raise DeckDNAError(
                    code="structured_output_invalid",
                    message="provider returned non-JSON response envelope",
                    stage="providers",
                    details={"model": model},
                ) from exc
            if not isinstance(data, dict):
                status = "invalid_envelope"
                raise DeckDNAError(
                    code="structured_output_invalid",
                    message="provider returned non-object response envelope",
                    stage="providers",
                    details={"model": model},
                )
            self._record_usage(model, data)
            return data
        finally:
            duration = time.monotonic() - started
            log = logger.info if status == "ok" else logger.warning
            log(
                "provider call: role=%s model=%s duration=%.1fs status=%s",
                role or "?",
                model,
                duration,
                status,
            )

    async def text_json(
        self, prompt_name: str, payload: dict, schema: type[BaseModel]
    ) -> BaseModel:
        result = await self._json_completion(
            self._model_text,
            messages=[{"role": "user", "content": self._render(prompt_name, payload)}],
            schema=schema,
            role="text",
        )
        self._used["text"] = self._model_text
        return result

    async def vision_json(
        self,
        prompt_name: str,
        images: list[str],
        payload: dict,
        schema: type[BaseModel],
    ) -> BaseModel:
        if not self._model_vision:
            raise DeckDNAError(
                code="provider_capability_missing",
                message="provider has no vision model configured",
                stage="providers",
            )
        content: list[dict[str, Any]] = [
            {"type": "text", "text": self._render(prompt_name, payload)}
        ]
        content += [
            {"type": "image_url", "image_url": {"url": img}} for img in images
        ]
        result = await self._json_completion(
            self._model_vision,
            messages=[{"role": "user", "content": content}],
            schema=schema,
            role="vision",
        )
        self._used["vision"] = self._model_vision
        return result

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not self._model_embed:
            raise DeckDNAError(
                code="provider_capability_missing",
                message="provider has no embedding model configured",
                stage="providers",
            )
        started = time.monotonic()
        status = "ok"
        logger.info("provider call start: role=embed model=%s", self._model_embed)
        try:
            try:
                resp = await self._post_with_retry(
                    "/embeddings",
                    {"model": self._model_embed, "input": texts},
                    model=self._model_embed,
                    error_prefix="embeddings",
                )
            except DeckDNAError as exc:
                status = (
                    f"http_{exc.http_status}"
                    if exc.http_status is not None
                    else f"network_{type(exc.__cause__).__name__}"
                )
                raise
            try:
                data = resp.json()
                embeddings = [item["embedding"] for item in data["data"]]
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                status = "invalid_envelope"
                raise DeckDNAError(
                    code="structured_output_invalid",
                    message="provider returned malformed embeddings response",
                    stage="providers",
                    details={"model": self._model_embed},
                ) from exc
            self._used["embed"] = self._model_embed
            return embeddings
        finally:
            duration = time.monotonic() - started
            log = logger.info if status == "ok" else logger.warning
            log(
                "provider call: role=embed model=%s duration=%.1fs status=%s",
                self._model_embed,
                duration,
                status,
            )

    async def image_generate(self, prompt: str, *, size: str = "1024x1024") -> bytes:
        """POST /images/generations (ADR-017) — same OpenAI-compatible shape
        most inference gateways expose for text-to-image. ``response_format:
        b64_json`` avoids a second round-trip to fetch a hosted URL (some
        gateways only support one or the other; b64 is the more portable
        choice for a self-hosted/open-weight backend). No token usage to
        record (image APIs don't report prompt/completion tokens the way
        chat completions do) -- ``_record_usage`` is deliberately not
        called here."""
        if not self._model_image:
            raise DeckDNAError(
                code="provider_capability_missing",
                message="provider has no image model configured",
                stage="providers",
            )
        started = time.monotonic()
        status = "ok"
        logger.info("provider call start: role=image model=%s", self._model_image)
        try:
            try:
                resp = await self._post_with_retry(
                    "/images/generations",
                    {
                        "model": self._model_image,
                        "prompt": prompt,
                        "size": size,
                        "response_format": "b64_json",
                    },
                    model=self._model_image,
                    error_prefix="images",
                )
            except DeckDNAError as exc:
                status = (
                    f"http_{exc.http_status}"
                    if exc.http_status is not None
                    else f"network_{type(exc.__cause__).__name__}"
                )
                raise
            try:
                data = resp.json()
                b64 = data["data"][0]["b64_json"]
                image_bytes = base64.b64decode(b64)
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
                status = "invalid_envelope"
                raise DeckDNAError(
                    code="structured_output_invalid",
                    message="provider returned malformed image response",
                    stage="providers",
                    details={"model": self._model_image},
                ) from exc
            self._used["image"] = self._model_image
            return image_bytes
        finally:
            duration = time.monotonic() - started
            log = logger.info if status == "ok" else logger.warning
            log(
                "provider call: role=image model=%s duration=%.1fs status=%s",
                self._model_image,
                duration,
                status,
            )

    async def probe_structured_output(self) -> None:
        """Минимальный реальный вызов: json_schema + валидный JSON-ответ.

        Проба для ``test_provider_session`` — проверяет connectivity/auth
        и capability ``structured_output`` одним round-trip, без
        зависимости от prompt registry (инлайн-схема, инлайн-сообщение).
        """
        await self._json_completion(
            self._model_text,
            messages=[{"role": "user", "content": _PROBE_MESSAGE}],
            schema=_ProbeAck,
            role="probe_text",
        )

    async def probe_image_input(self) -> None:
        """То же с картинкой 1×1 PNG — capability ``image_input``.

        Без vision-модели — ``provider_capability_missing``, как у
        ``vision_json``.
        """
        if not self._model_vision:
            raise DeckDNAError(
                code="provider_capability_missing",
                message="provider has no vision model configured",
                stage="providers",
            )
        await self._json_completion(
            self._model_vision,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _PROBE_MESSAGE},
                        {
                            "type": "image_url",
                            "image_url": {"url": _PROBE_PNG_DATA_URL},
                        },
                    ],
                }
            ],
            schema=_ProbeAck,
            role="probe_vision",
        )

    async def _json_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        schema: type[BaseModel],
        *,
        role: str = "",
    ) -> BaseModel:
        if not model:
            raise DeckDNAError(
                code="provider_capability_missing",
                message="no model configured for this role",
                stage="providers",
            )
        data = await self._chat(
            model,
            {
                **self._extra_body,
                **self._chat_options,
                "model": model,
                "messages": messages,
                "temperature": 0,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": json_schema_of(schema),
                },
            },
            role=role,
        )
        try:
            content = data["choices"][0]["message"]["content"]
            return schema.model_validate(json.loads(content))
        except (
            KeyError,
            IndexError,
            TypeError,
            json.JSONDecodeError,
            ValidationError,
        ) as exc:
            raise DeckDNAError(
                code="structured_output_invalid",
                message="provider returned invalid structured output",
                stage="providers",
                details={"model": model},
            ) from exc

    @staticmethod
    def _render(prompt_name: str, payload: dict) -> str:
        # Реальный prompt registry: prompts/**/<name>.v<N>.yaml — instructions
        # + payload по input_fields. Отсутствующий промпт — typed not_found.
        return render_prompt_by_name(prompt_name, payload)
