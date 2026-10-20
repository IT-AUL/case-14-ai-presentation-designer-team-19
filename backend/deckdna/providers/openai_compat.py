"""OpenAI-compatible chat-completions provider.

Works against any endpoint exposing POST {base_url}/chat/completions —
self-hosted vLLM/TGI, OpenRouter-style routers, and VK Inference once
its base URL is supplied. Secrets never enter logs or persistence.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import contextvars
import json
import logging
import random
import re
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ValidationError

from deckdna.errors import DeckDNAError
from deckdna.provider_options import ChatOptions
from deckdna.providers.base import json_schema_of
from deckdna.providers.prompts import inject_schema_before_payload, render_prompt_by_name

logger = logging.getLogger(__name__)

_STAGE_DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "_STAGE_DEADLINE", default=None
)


@contextlib.contextmanager
def provider_deadline(seconds: float | None) -> Iterator[None]:
    """Ограничить время вызовов провайдера в текущем контексте (сквозной бюджет этапа)."""
    if seconds is None:
        yield
        return
    new_deadline = time.monotonic() + max(0.0, float(seconds))
    current = _STAGE_DEADLINE.get()
    if current is not None:
        new_deadline = min(current, new_deadline)
    token = _STAGE_DEADLINE.set(new_deadline)
    try:
        yield
    finally:
        _STAGE_DEADLINE.reset(token)


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

# Confirmed live (27.09 ночь): the account hitting
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


class _RequestPacer:
    """Thread-safe floor on the gap between the START of consecutive
    outgoing requests -- one instance can be shared by construction
    across SEVERAL ``OpenAICompatibleProvider`` objects.

    Found live (29.09): three variants used to share ONE gateway/pacer
    while running compose/rerank/audit strictly sequentially, so a
    single pacer correctly spaced out every request across the whole
    run and kept it under Cerebras' TPM ceiling (deliberately tuned so
    a request left roughly every ``min_request_interval_s``). Once
    compose/rerank/audit became parallel across variants (Фаза 5, see
    architecture notes), each variant got its OWN gateway instance --
    needed because ``session()``/``aclose()`` share ``_session_client``
    as a plain, non-thread-safe attribute -- and each pacer then only
    throttled its own variant's requests, so the COMBINED rate across
    all variants running at once was no longer bounded and started
    tripping 429s live.

    A plain ``asyncio.Lock`` can't fix this: it is bound to whichever
    event loop first awaits it and breaks across threads (each variant
    thread runs its own ``asyncio.run``). ``threading.Lock`` plus
    ``time.monotonic()`` are process-global and loop-independent, so
    this reserves slots correctly regardless of which thread or event
    loop calls ``wait_turn`` -- the lock is only held long enough to
    reserve a slot, never across the actual sleep, so it doesn't
    serialize unrelated work, only the outgoing request rate."""

    def __init__(self, min_interval_s: float) -> None:
        self._min_interval_s = min_interval_s
        self._lock = threading.Lock()
        self._next_request_at = 0.0

    async def wait_turn(self, sleep: Callable[[float], Awaitable[None]] | None = None) -> None:
        if self._min_interval_s <= 0:
            return
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next_request_at)
            self._next_request_at = start + self._min_interval_s
        delay = start - now
        if delay > 0:
            await (sleep(delay) if sleep is not None else asyncio.sleep(delay))


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


def extract_json_text(content: str) -> str:
    """JSON из ответа модели: без блока рассуждений ``<think>…</think>``
    (Qwen с включённым thinking) и без обёртки ````` ```json ````` — модели
    класса 27B часто так отвечают даже при ``response_format``. Если после
    очистки текст не начинается с ``{``/``[``, берётся первый
    сбалансированный объект; иначе — исходная строка (пусть json.loads
    честно упадёт)."""
    text = _THINK_RE.sub("", content or "").strip()
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    if text[:1] in ("{", "["):
        return text
    start = text.find("{")
    if start < 0:
        return text
    depth = 0
    in_str = escape = False
    for i, ch in enumerate(text[start:], start):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text


def _compact_schema(node: Any) -> Any:
    """Схема для текста промпта: без ``title``/``description`` — модели
    нужны имена полей, типы и вложенность, а не документация."""
    if isinstance(node, dict):
        return {
            k: _compact_schema(v)
            for k, v in node.items()
            if k not in {"title", "description"} or k == "properties"
        }
    if isinstance(node, list):
        return [_compact_schema(v) for v in node]
    return node


_SHARED_PACERS: dict[tuple[str, float], _RequestPacer] = {}
_SHARED_PACERS_LOCK = threading.Lock()


def shared_pacer(base_url: str, min_interval_s: float) -> _RequestPacer:
    """Один пейсер на процесс для каждого endpoint'а: gateway'и трёх
    параллельных вариантов (Фаза 5) вместе не превышают темп провайдера."""
    key = (base_url.rstrip("/"), float(min_interval_s))
    with _SHARED_PACERS_LOCK:
        pacer = _SHARED_PACERS.get(key)
        if pacer is None:
            pacer = _SHARED_PACERS[key] = _RequestPacer(min_interval_s)
        return pacer


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


_ASPECTS = {"1:1": 1.0, "16:9": 16 / 9, "4:3": 4 / 3, "3:2": 1.5, "9:16": 9 / 16, "3:4": 0.75}


def _aspect_ratio(size: str) -> str:
    """«1792x1024» → ближайшее из поддерживаемых соотношений сторон."""
    try:
        w, h = (int(v) for v in size.lower().split("x"))
        ratio = w / h
    except (ValueError, ZeroDivisionError):
        return "1:1"
    return min(_ASPECTS, key=lambda k: abs(_ASPECTS[k] - ratio))


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
        timeout_s: float = 45.0,
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        retry_base_delay_s: float = _DEFAULT_RETRY_BASE_DELAY_S,
        min_request_interval_s: float = _DEFAULT_MIN_REQUEST_INTERVAL_S,
        chat_options: ChatOptions | dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
        image_transport: str = "images",
        image_delegate: OpenAICompatibleProvider | None = None,
        vision_delegate: OpenAICompatibleProvider | None = None,
        pacer: _RequestPacer | None = None,
        extra_headers: dict[str, str] | None = None,
        schema_in_prompt: bool = True,
        repair_invalid_json: bool = True,
        fallback_provider: OpenAICompatibleProvider | None = None,
    ) -> None:
        self._schema_in_prompt = schema_in_prompt
        self._repair_invalid_json = repair_invalid_json
        self._vision_delegate = vision_delegate
        self._fallback_provider = fallback_provider
        self._primary_cooldown_until = 0.0
        # Заголовки, которых требует конкретный провайдер (VK Inference —
        # ``Project-ID``). Авторизацию и User-Agent они не перекрывают.
        self._extra_headers = {
            k: v
            for k, v in (extra_headers or {}).items()
            if v and k.lower() not in {"authorization", "user-agent", "host", "content-length"}
        }
        # Картинки часто у другого провайдера, чем текст (FLUX.2 klein —
        # OpenRouter, Qwen — Cerebras/VK): image_generate уходит делегату.
        # image_transport: "images" — POST /images/generations (OpenAI,
        # Together, VK); "chat" — /chat/completions с modalities=["image"]
        # (OpenRouter).
        self._image_transport = image_transport
        self._image_delegate = image_delegate
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
        self._telemetry: dict[str, dict[str, Any]] = {}
        self._max_retries = max_retries
        self._retry_base_delay_s = retry_base_delay_s
        self._min_request_interval_s = min_request_interval_s
        # Own private pacer by default (today's behaviour, unchanged for
        # every existing call site); a caller building several instances
        # that should throttle as ONE combined rate (e.g. one gateway per
        # concurrently-running variant, Фаза 5) passes the SAME
        # _RequestPacer to each -- see its docstring for why this needs to
        # be threading-safe, not just asyncio-safe.
        self._pacer = pacer or _RequestPacer(min_request_interval_s)
        self.provider_name = _sanitize_provider_name(base_url)
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._transport = transport
        # Фаза 3 : опциональный переиспользуемый
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
        kwargs: dict[str, Any] = {
            "base_url": self._base_url,
            "headers": {
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
                **self._extra_headers,
            },
            "timeout": self._timeout_s,
            "transport": self._transport,
        }
        try:
            return httpx.AsyncClient(**kwargs)
        except ImportError:
            return httpx.AsyncClient(**kwargs, trust_env=False)

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
        if self._fallback_provider is not None:
            async with self._fallback_provider.session():
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
        else:
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
        if self._fallback_provider is not None:
            await self._fallback_provider.aclose()
        if self._session_client is not None:
            client, self._session_client = self._session_client, None
            await client.aclose()

    def used_model_ids(self) -> dict[str, str]:
        """role -> model_id for calls that completed successfully."""
        used = dict(self._used)
        if self._fallback_provider is not None:
            for role, model_id in self._fallback_provider.used_model_ids().items():
                used.setdefault(role, model_id)
        return used

    def usage_report(self) -> dict[str, dict[str, int]]:
        """model_id -> {calls, input_tokens, output_tokens} (UsageSource)."""
        report = {model: dict(counts) for model, counts in self._usage.items()}
        if self._fallback_provider is not None:
            for model, counts in self._fallback_provider.usage_report().items():
                if model in report:
                    for k in ("calls", "input_tokens", "output_tokens"):
                        report[model][k] = report[model].get(k, 0) + counts.get(k, 0)
                else:
                    report[model] = dict(counts)
        return report

    def telemetry_report(self) -> dict[str, dict[str, Any]]:
        """model_id -> {calls, input_tokens, output_tokens, image_tokens, ...}."""
        return {model: dict(counts) for model, counts in self._telemetry.items()}

    def _record_usage(
        self,
        model: str,
        data: dict[str, Any],
        *,
        duration_ms: float | None = None,
        image_count: int = 0,
    ) -> dict[str, Any]:
        counts = self._usage.setdefault(model, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        counts["calls"] += 1
        tel = self._telemetry.setdefault(
            model,
            {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "image_tokens": 0,
                "prefill_ms": 0.0,
                "decode_ms": 0.0,
                "cache_hits": 0,
            },
        )
        tel["calls"] += 1
        usage = data.get("usage")
        in_tok = 0
        out_tok = 0
        img_tok = 0
        cache_hit = False
        if isinstance(usage, dict):
            raw_in = usage.get("prompt_tokens", usage.get("input_tokens"))
            if isinstance(raw_in, int) and not isinstance(raw_in, bool):
                in_tok = raw_in
                counts["input_tokens"] += in_tok
                tel["input_tokens"] += in_tok
            raw_out = usage.get("completion_tokens", usage.get("output_tokens"))
            if isinstance(raw_out, int) and not isinstance(raw_out, bool):
                out_tok = raw_out
                counts["output_tokens"] += out_tok
                tel["output_tokens"] += out_tok
            details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
            raw_img = usage.get("image_tokens")
            if raw_img is None and isinstance(details, dict):
                raw_img = details.get("image_tokens")
            if isinstance(raw_img, int) and not isinstance(raw_img, bool):
                img_tok = raw_img
            cached_tok = (
                details.get("cached_tokens", 0) if isinstance(details, dict) else 0
            ) or usage.get("prompt_cache_hit_tokens", 0)
            if (
                bool(usage.get("cache_hit"))
                or bool(data.get("cache_hit"))
                or (isinstance(cached_tok, int) and cached_tok > 0)
            ):
                cache_hit = True
        elif bool(data.get("cache_hit")):
            cache_hit = True
        if img_tok == 0 and image_count > 0:
            raw_top_img = data.get("image_tokens")
            if isinstance(raw_top_img, int) and not isinstance(raw_top_img, bool):
                img_tok = raw_top_img
        tel["image_tokens"] += img_tok
        if cache_hit:
            tel["cache_hits"] += 1

        timings = data.get("timings") if isinstance(data.get("timings"), dict) else {}
        raw_prefill = (
            data.get("prefill_ms")
            if data.get("prefill_ms") is not None
            else (usage.get("prefill_ms") if isinstance(usage, dict) else None)
        )
        if raw_prefill is None and isinstance(timings, dict):
            raw_prefill = timings.get("prompt_ms", timings.get("prefill_ms"))
        raw_decode = (
            data.get("decode_ms")
            if data.get("decode_ms") is not None
            else (usage.get("decode_ms") if isinstance(usage, dict) else None)
        )
        if raw_decode is None and isinstance(timings, dict):
            raw_decode = timings.get("predicted_ms", timings.get("decode_ms"))

        if isinstance(raw_prefill, (int, float)) and not isinstance(raw_prefill, bool):
            prefill_ms = round(float(raw_prefill), 2)
        elif duration_ms is not None:
            denom = max(1, in_tok + out_tok * 4)
            prefill_ms = (
                round(float(duration_ms) * (in_tok / denom), 2)
                if in_tok > 0
                else round(float(duration_ms) * 0.3, 2)
            )
        else:
            prefill_ms = 0.0

        if isinstance(raw_decode, (int, float)) and not isinstance(raw_decode, bool):
            decode_ms = round(float(raw_decode), 2)
        elif duration_ms is not None:
            decode_ms = round(max(0.0, float(duration_ms) - prefill_ms), 2)
        else:
            decode_ms = 0.0

        tel["prefill_ms"] = round(tel["prefill_ms"] + prefill_ms, 2)
        tel["decode_ms"] = round(tel["decode_ms"] + decode_ms, 2)
        return {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "image_tokens": img_tok,
            "prefill_ms": prefill_ms,
            "decode_ms": decode_ms,
            "cache_hit": cache_hit,
        }

    async def _sleep(self, seconds: float) -> None:
        """Isolated so tests can patch it and skip the real wall-clock wait."""
        await asyncio.sleep(seconds)

    async def _pace(self) -> None:
        """Block until at least ``min_request_interval_s`` has elapsed since
        the last outgoing request STARTED, across every provider instance
        that shares this ``_RequestPacer`` (see its docstring). A no-op when
        the interval is 0."""
        await self._pacer.wait_turn(self._sleep)

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

    def _effective_timeout(self, model: str, error_prefix: str) -> float:
        deadline = _STAGE_DEADLINE.get()
        if deadline is None:
            return self._timeout_s
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DeckDNAError(
                code="provider_unavailable",
                message=f"{error_prefix} stage deadline exceeded",
                stage="providers",
                retryable=True,
                details={"model": model},
            ) from httpx.ReadTimeout("stage deadline exceeded")
        return min(self._timeout_s, max(0.05, remaining))

    async def _post_with_retry(
        self, path: str, body: dict[str, Any], *, model: str, error_prefix: str
    ) -> httpx.Response:
        """POST *path*, retrying a 429/5xx/network failure up to
        ``self._max_retries`` times with backoff. A non-retryable 4xx (or
        the final retry) raises the exact ``DeckDNAError`` shape a
        non-retrying call would — callers never need to know retries
        happened, only that the call eventually failed."""
        attempt = 0
        effective_max_retries = 1 if self._fallback_provider is not None else self._max_retries
        while True:
            try:
                call_timeout = self._effective_timeout(model, error_prefix)
                await self._pace()
                call_timeout = self._effective_timeout(model, error_prefix)
                async with self._use_client() as client:
                    resp = await client.post(path, json=body, timeout=call_timeout)
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as exc:
                http_status = exc.response.status_code
                retryable = http_status == 429 or http_status >= 500
                delay = self._retry_delay(attempt, exc.response.headers.get("Retry-After"))
                if self._fallback_provider is not None:
                    delay = min(1.0, delay)
                deadline = _STAGE_DEADLINE.get()
                deadline_exceeded = deadline is not None and (time.monotonic() + delay >= deadline)
                if not retryable or attempt >= effective_max_retries or deadline_exceeded:
                    raise DeckDNAError(
                        code="provider_unavailable",
                        message=f"{error_prefix} HTTP {http_status}",
                        stage="providers",
                        retryable=retryable,
                        http_status=http_status,
                        details={"model": model},
                    ) from exc
                logger.warning(
                    "provider call retry: model=%s status=%d attempt=%d/%d delay=%.1fs",
                    model,
                    http_status,
                    attempt + 1,
                    self._max_retries,
                    delay,
                )
            except httpx.HTTPError as exc:
                delay = self._retry_delay(attempt, None)
                if self._fallback_provider is not None:
                    delay = min(1.0, delay)
                deadline = _STAGE_DEADLINE.get()
                deadline_exceeded = deadline is not None and (time.monotonic() + delay >= deadline)
                is_timeout = isinstance(exc, httpx.TimeoutException)
                if attempt >= effective_max_retries or deadline_exceeded or is_timeout:
                    raise DeckDNAError(
                        code="provider_unavailable",
                        message=f"{error_prefix} request failed: {exc.__class__.__name__}",
                        stage="providers",
                        retryable=True,
                        details={"model": model},
                    ) from exc
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

    async def _chat(self, model: str, body: dict[str, Any], *, role: str = "") -> dict[str, Any]:
        """POST /chat/completions with structured start/duration/status
        logging around every call — this is what let the live 24-minute
        contextual-audit hang go completely invisible (root logger had no
        handler at the time, so nothing here reached ``docker compose
        logs``; see ``_configure_logging`` in ``api/app.py``)."""
        started = time.monotonic()
        status = "ok"
        call_tel: dict[str, Any] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "image_tokens": 0,
            "prefill_ms": 0.0,
            "decode_ms": 0.0,
            "cache_hit": False,
        }
        image_count = 0
        for msg in body.get("messages") or []:
            if isinstance(msg, dict) and isinstance(msg.get("content"), list):
                image_count += sum(
                    1
                    for part in msg["content"]
                    if isinstance(part, dict) and part.get("type") == "image_url"
                )
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
            elapsed_ms = round((time.monotonic() - started) * 1000.0, 2)
            call_tel = self._record_usage(
                model, data, duration_ms=elapsed_ms, image_count=image_count
            )
            return data
        finally:
            duration = time.monotonic() - started
            log = logger.info if status == "ok" else logger.warning
            log(
                "provider call: role=%s model=%s duration=%.1fs prefill_ms=%.1f decode_ms=%.1f "
                "input_tokens=%d output_tokens=%d image_tokens=%d cache_hit=%s status=%s",
                role or "?",
                model,
                duration,
                call_tel["prefill_ms"],
                call_tel["decode_ms"],
                call_tel["input_tokens"],
                call_tel["output_tokens"],
                call_tel["image_tokens"],
                call_tel["cache_hit"],
                status,
            )

    async def text_json(
        self, prompt_name: str, payload: dict, schema: type[BaseModel]
    ) -> BaseModel:
        if self._fallback_provider is not None and time.monotonic() < self._primary_cooldown_until:
            return await self._fallback_provider.text_json(prompt_name, payload, schema)
        try:
            result = await self._json_completion(
                self._model_text,
                messages=[{"role": "user", "content": self._render(prompt_name, payload)}],
                schema=schema,
                role="text",
            )
            self._primary_cooldown_until = 0.0
            self._used["text"] = self._model_text
            return result
        except Exception as exc:
            if self._fallback_provider is not None:
                self._primary_cooldown_until = time.monotonic() + 30.0
                logger.warning(
                    "primary %s failed for text_json (%s: %s); fallback to %s",
                    self.provider_name,
                    type(exc).__name__,
                    exc,
                    self._fallback_provider.provider_name,
                )
                return await self._fallback_provider.text_json(prompt_name, payload, schema)
            raise

    async def vision_json(
        self,
        prompt_name: str,
        images: list[str],
        payload: dict,
        schema: type[BaseModel],
    ) -> BaseModel:
        if self._vision_delegate is not None:
            result = await self._vision_delegate.vision_json(prompt_name, images, payload, schema)
            self._used["vision"] = self._vision_delegate._model_vision
            return result
        if not self._model_vision:
            if self._fallback_provider is not None:
                return await self._fallback_provider.vision_json(
                    prompt_name, images, payload, schema
                )
            raise DeckDNAError(
                code="provider_capability_missing",
                message="provider has no vision model configured",
                stage="providers",
            )
        if self._fallback_provider is not None and time.monotonic() < self._primary_cooldown_until:
            return await self._fallback_provider.vision_json(prompt_name, images, payload, schema)
        try:
            content: list[dict[str, Any]] = [
                {"type": "text", "text": self._render(prompt_name, payload)}
            ]
            content += [{"type": "image_url", "image_url": {"url": img}} for img in images]
            result = await self._json_completion(
                self._model_vision,
                messages=[{"role": "user", "content": content}],
                schema=schema,
                role="vision",
            )
            self._primary_cooldown_until = 0.0
            self._used["vision"] = self._model_vision
            return result
        except Exception as exc:
            if self._fallback_provider is not None:
                self._primary_cooldown_until = time.monotonic() + 30.0
                logger.warning(
                    "primary %s failed for vision_json (%s: %s); fallback to %s",
                    self.provider_name,
                    type(exc).__name__,
                    exc,
                    self._fallback_provider.provider_name,
                )
                return await self._fallback_provider.vision_json(
                    prompt_name, images, payload, schema
                )
            raise

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
        if self._image_delegate is not None:
            data = await self._image_delegate.image_generate(prompt, size=size)
            self._used["image"] = self._image_delegate._model_image
            return data
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
                if self._image_transport == "chat":
                    resp = await self._post_with_retry(
                        "/chat/completions",
                        {
                            "model": self._model_image,
                            "messages": [{"role": "user", "content": prompt}],
                            "modalities": ["image"],
                            "image_config": {"aspect_ratio": _aspect_ratio(size)},
                        },
                        model=self._model_image,
                        error_prefix="images",
                    )
                else:
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
                if self._image_transport == "chat":
                    url = data["choices"][0]["message"]["images"][0]["image_url"]["url"]
                    b64 = url.split(",", 1)[1] if url.startswith("data:") else url
                else:
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
        try:
            await self._json_completion(
                self._model_text,
                messages=[{"role": "user", "content": _PROBE_MESSAGE}],
                schema=_ProbeAck,
                role="probe_text",
            )
        except Exception:
            if self._fallback_provider is not None:
                await self._fallback_provider.probe_structured_output()
                return
            raise

    async def probe_image_input(self) -> None:
        if not self._model_vision:
            if self._fallback_provider is not None:
                await self._fallback_provider.probe_image_input()
                return
            raise DeckDNAError(
                code="provider_capability_missing",
                message="provider has no vision model configured",
                stage="providers",
            )
        try:
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
        except Exception:
            if self._fallback_provider is not None:
                await self._fallback_provider.probe_image_input()
                return
            raise

    async def _json_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        schema: type[BaseModel],
        *,
        role: str = "",
    ) -> BaseModel:
        """Структурный ответ модели.

        Не все OpenAI-совместимые endpoint'ы навязывают ``response_format``
        (VK Inference, 17.10: принимает json_schema, но имена полей не
        соблюдает — Qwen придумал свою форму ответа на ``slide_checks``).
        Поэтому: схема дублируется текстом в конце промпта, а ответ,
        не прошедший валидацию, возвращается модели на одно исправление с
        текстом ошибки. Не исправила — typed ``structured_output_invalid``."""
        if not model:
            raise DeckDNAError(
                code="provider_capability_missing",
                message="no model configured for this role",
                stage="providers",
            )
        json_schema = json_schema_of(schema)
        if self._schema_in_prompt and messages:
            note = (
                "\n\nОтвет — только JSON-объект строго по этой JSON Schema: те же "
                "имена полей, вложенность и типы, без лишних полей и без пояснений "
                "вокруг.\n"
                + json.dumps(
                    _compact_schema(json_schema.get("schema", json_schema)),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            last = dict(messages[-1])
            if isinstance(last.get("content"), str):
                last["content"] = inject_schema_before_payload(last["content"], note)
            elif isinstance(last.get("content"), list):
                # к текстовой части до переменных секций и картинок (стабильный префикс)
                parts = [dict(part) for part in last["content"]]
                for part in parts:
                    if part.get("type") == "text":
                        part["text"] = inject_schema_before_payload(str(part.get("text", "")), note)
                        break
                last["content"] = parts
            messages = [*messages[:-1], last]
        chat_template_kwargs = {
            "enable_thinking": False,
            **(
                self._extra_body.get("chat_template_kwargs")
                if isinstance(self._extra_body.get("chat_template_kwargs"), dict)
                else {}
            ),
        }
        attempts = 2 if self._repair_invalid_json else 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            data = await self._chat(
                model,
                {
                    **self._extra_body,
                    **self._chat_options,
                    "chat_template_kwargs": chat_template_kwargs,
                    "model": model,
                    "messages": messages,
                    "temperature": 0,
                    "response_format": {"type": "json_schema", "json_schema": json_schema},
                },
                role=role,
            )
            content: Any = None
            try:
                content = data["choices"][0]["message"]["content"]
                return schema.model_validate(json.loads(extract_json_text(content)))
            except (
                KeyError,
                IndexError,
                TypeError,
                json.JSONDecodeError,
                ValidationError,
            ) as exc:
                last_exc = exc
                if attempt + 1 >= attempts or not isinstance(content, str):
                    break
                logger.warning(
                    "structured output invalid (model=%s, %s); asking the model to fix it",
                    model,
                    type(exc).__name__,
                )
                messages = [
                    *messages,
                    {"role": "assistant", "content": content[:6000]},
                    {
                        "role": "user",
                        "content": (
                            "Ответ не соответствует JSON Schema: "
                            + str(exc)[:800]
                            + "\nВерни исправленный JSON строго по схеме, только JSON."
                        ),
                    },
                ]
        raise DeckDNAError(
            code="structured_output_invalid",
            message="provider returned invalid structured output",
            stage="providers",
            details={"model": model},
        ) from last_exc

    @staticmethod
    def _render(prompt_name: str, payload: dict) -> str:
        # Реальный prompt registry: prompts/**/<name>.v<N>.yaml — instructions
        # + payload по input_fields. Отсутствующий промпт — typed not_found.
        return render_prompt_by_name(prompt_name, payload)
