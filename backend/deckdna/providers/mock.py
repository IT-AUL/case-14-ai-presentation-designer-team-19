"""Deterministic offline provider — default when ``settings.mock_provider``.

Implements the frozen ``ModelGateway`` protocol without any network so
every stage can run end-to-end locally and in CI. Determinism means the
same ``(prompt_name, payload)`` always yields the same structured output,
so golden fixtures and audits are reproducible.

Two output modes:

- ``fixtures``: explicit per-prompt payloads (dict, model instance, or
  callable) win when present — for contract tests that need exact data;
- synthesis: a schema-valid instance is built from the Pydantic model's
  fields (Literals pick the first variant, enums the first member,
  numbers are hashed *inside* ge/gt/le/lt bounds, strings and lists
  respect min/max length metadata). Recursion into nested models is
  depth-capped so self-referential schemas cannot blow the stack.
"""

from __future__ import annotations

import base64
import datetime as dt
import enum
import hashlib
import math
import types
from collections.abc import Callable, Mapping
from typing import Any, Literal, Union, get_args, get_origin

import annotated_types
from pydantic import BaseModel, ValidationError

from deckdna.errors import DeckDNAError

FixtureValue = Mapping[str, Any] | BaseModel | Callable[[dict[str, Any]], Any]

_SEED_NAMESPACE = b"deckdna-mock-v1"
_MAX_DEPTH = 4  # max nesting of synthesized BaseModel fields
_HASH_SPACE = 1_000_000  # resolution of the hash -> range mapping

# 1x1 transparent PNG, same bytes probe_image_input already uses
# (openai_compat.py's _PROBE_PNG_DATA_URL) -- real, decodable image bytes,
# not a placeholder string, so downstream format detection (image_fill.py's
# detect_image_content_type) sees genuine PNG magic bytes.
_MOCK_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _digest(*parts: str) -> bytes:
    h = hashlib.sha256(_SEED_NAMESPACE)
    for part in parts:
        h.update(part.encode("utf-8", errors="replace"))
        h.update(b"\x00")
    return h.digest()


class MockProvider:
    """Offline ``ModelGateway`` for development and tests. No I/O."""

    def __init__(
        self,
        *,
        fixtures: Mapping[str, FixtureValue] | None = None,
        embedding_dim: int = 16,
    ) -> None:
        self.fixtures = dict(fixtures or {})
        self.embedding_dim = embedding_dim
        self.calls: list[dict[str, Any]] = []  # introspectable call log
        self.provider_name = "mock"
        self._used: dict[str, str] = {}

    async def aclose(self) -> None:
        """Interface parity with networked providers."""

    def used_model_ids(self) -> dict[str, str]:
        """role -> 'mock' for calls that completed successfully."""
        return dict(self._used)

    async def text_json(
        self, prompt_name: str, payload: dict, schema: type[BaseModel]
    ) -> BaseModel:
        self.calls.append({"method": "text_json", "prompt": prompt_name, "payload": payload})
        result = self._resolve(prompt_name, payload, schema)
        self._used["text"] = "mock"
        return result

    async def vision_json(
        self,
        prompt_name: str,
        images: list[str],
        payload: dict,
        schema: type[BaseModel],
    ) -> BaseModel:
        self.calls.append(
            {
                "method": "vision_json",
                "prompt": prompt_name,
                "payload": payload,
                "images": list(images),
            }
        )
        seed = _digest(prompt_name, str(len(images)))
        result = self._resolve(prompt_name, payload, schema, seed=seed)
        self._used["vision"] = "mock"
        return result

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append({"method": "embed", "n_texts": len(texts)})
        result = [self._embed_one(text) for text in texts]
        self._used["embed"] = "mock"
        return result

    async def image_generate(self, prompt: str, *, size: str = "1024x1024") -> bytes:
        """Deterministic offline stand-in (ADR-017) — a real 1x1 PNG, not a
        schema-validated object, so it bypasses ``_resolve``/synthesis
        entirely. ``fixtures["image_generate"]`` (a callable returning
        bytes) overrides for tests that need specific content; otherwise
        every prompt gets the same minimal valid PNG — determinism here
        means "always produces decodable image bytes", not "unique per
        prompt" (unlike ``_embed_one``, there is no schema-shaped output to
        vary by hash)."""
        self.calls.append({"method": "image_generate", "prompt": prompt, "size": size})
        fixture = self.fixtures.get("image_generate")
        result = fixture(prompt) if callable(fixture) else _MOCK_PNG_BYTES
        self._used["image"] = "mock"
        return result

    def _resolve(
        self,
        prompt_name: str,
        payload: dict,
        schema: type[BaseModel],
        *,
        seed: bytes | None = None,
    ) -> BaseModel:
        fixture = self.fixtures.get(prompt_name)
        if fixture is not None:
            raw = fixture(payload) if callable(fixture) else fixture
            return raw if isinstance(raw, schema) else schema.model_validate(raw)
        return _synthesize(schema, seed=seed or _digest(prompt_name, repr(sorted(payload.items()))))

    def _embed_one(self, text: str) -> list[float]:
        digest = _digest("embed", text)
        return [byte / 255.0 for byte in digest[: self.embedding_dim]]


def _synthesize(schema: type[BaseModel], *, seed: bytes, depth: int = 0) -> BaseModel:
    kwargs = {
        name: _field_value(name, field.annotation, field.metadata, seed, depth)
        for name, field in schema.model_fields.items()
    }
    try:
        return schema.model_validate(kwargs)
    except ValidationError as exc:
        raise DeckDNAError(
            code="structured_output_invalid",
            message=(
                f"mock provider cannot synthesize {schema.__name__}: "
                f"{exc.error_count()} errors"
            ),
            stage="providers",
        ) from exc


def _field_value(
    name: str, annotation: Any, metadata: list[Any], seed: bytes, depth: int
) -> Any:
    min_len, max_len = _len_bounds(metadata)
    value = _value_for(name, annotation, seed, depth)
    if isinstance(value, str):
        if len(value) < min_len:
            value = value.ljust(min_len, "x")
        if max_len is not None:
            value = value[:max_len]
    elif isinstance(value, bool):
        pass
    elif isinstance(value, (int, float)):
        value = _bounded_number(name, seed, metadata, integer=isinstance(value, int))
    elif isinstance(value, list) and len(value) < min_len:
        inner = _list_inner(annotation)
        value = [
            _value_for(f"{name}_{i}", inner, seed, depth + 1) for i in range(min_len)
        ]
    return value


def _numeric_bounds(metadata: list[Any]) -> tuple[float, float, bool, bool]:
    """Effective (lo, hi, lo_exclusive, hi_exclusive); defaults 0..100 inclusive."""
    lo, hi = 0.0, 100.0
    lo_excl = hi_excl = False
    for meta in metadata:
        if isinstance(meta, annotated_types.Ge):
            lo, lo_excl = float(meta.ge), False
        elif isinstance(meta, annotated_types.Gt):
            lo, lo_excl = float(meta.gt), True
        elif isinstance(meta, annotated_types.Le):
            hi, hi_excl = float(meta.le), False
        elif isinstance(meta, annotated_types.Lt):
            hi, hi_excl = float(meta.lt), True
    return lo, hi, lo_excl, hi_excl


def _bounded_number(
    name: str, seed: bytes, metadata: list[Any], *, integer: bool
) -> int | float:
    """Map the hash into the field's declared range instead of clamping."""
    raw = int.from_bytes(_digest(name) + seed, "big")
    lo, hi, lo_excl, hi_excl = _numeric_bounds(metadata)
    if integer:
        ilo = math.floor(lo) + 1 if lo_excl else math.ceil(lo)
        ihi = math.ceil(hi) - 1 if hi_excl else math.floor(hi)
        if ilo > ihi:
            return ilo  # degenerate range; pydantic will reject honestly
        return ilo + raw % (ihi - ilo + 1)
    eps = max((hi - lo) * 1e-6, 1e-12)
    a = lo + eps if lo_excl else lo
    b = hi - eps if hi_excl else hi
    if a >= b:
        return (lo + hi) / 2
    return a + (raw % _HASH_SPACE) / _HASH_SPACE * (b - a)


def _len_bounds(metadata: list[Any]) -> tuple[int, int | None]:
    min_len = 0
    max_len = None
    for meta in metadata:
        if isinstance(meta, annotated_types.MinLen):
            min_len = meta.min_length
        elif isinstance(meta, annotated_types.MaxLen):
            max_len = meta.max_length
        elif isinstance(meta, annotated_types.Len):
            min_len = meta.min_length or 0
            max_len = meta.max_length
    return min_len, max_len


def _list_inner(annotation: Any) -> Any:
    args = get_args(annotation)
    return args[0] if args else str


def _value_for(name: str, annotation: Any, seed: bytes, depth: int) -> Any:
    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is Literal and args:
        return args[0]
    if origin in (Union, types.UnionType):
        non_none = [a for a in args if a is not type(None)]
        if not non_none:
            return None
        if depth > _MAX_DEPTH and len(non_none) < len(args):
            return None  # optional field at depth cap: take the None branch
        return _value_for(name, non_none[0], seed, depth)
    if origin is not None and issubclass(origin, (list, tuple, set, frozenset)):
        return []
    if origin is not None and issubclass(origin, dict):
        return {}
    if annotation is str or annotation is Any:
        return f"mock_{name}"
    if annotation is bool:
        return True
    if annotation is int:
        return int.from_bytes(_digest(name) + seed, "big") % 100
    if annotation is float:
        return (int.from_bytes(_digest(name) + seed, "big") % 1000) / 10.0
    if annotation is dt.datetime:
        return dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    if annotation is dt.date:
        return dt.date(2026, 1, 1)
    if isinstance(annotation, type):
        if issubclass(annotation, enum.Enum):
            members = list(annotation)
            return members[0] if members else None
        if issubclass(annotation, BaseModel):
            if depth > _MAX_DEPTH:
                return annotation.model_construct()
            return _synthesize(annotation, seed=_digest(name) + seed, depth=depth + 1)
    return None
