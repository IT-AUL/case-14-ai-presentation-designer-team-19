#!/usr/bin/env python3
"""Smoke: реальный OpenAI-compatible endpoint end-to-end (text + vision).

Проверяет, что production-конфиг-путь провайдера живой:
`DECKDNA_MOCK_PROVIDER=false` + `DECKDNA_PROVIDER_BASE_URL` /
`DECKDNA_PROVIDER_API_KEY` / `DECKDNA_MODEL_TEXT` /
`DECKDNA_MODEL_VISION` → `build_gateway()` → один `text_json`
(storyline → DeckPlan) и один `vision_json` (slide_checks →
SlideChecksResult с 1×1 PNG data URL).

Секреты не печатаются: в выводе только host endpoint'а и имена моделей.

Run:
    DECKDNA_MOCK_PROVIDER=false \
    DECKDNA_PROVIDER_BASE_URL=https://<endpoint>/v1 \
    DECKDNA_PROVIDER_API_KEY=<key> \
    DECKDNA_MODEL_TEXT=<text-model> \
    DECKDNA_MODEL_VISION=<vision-model> \
    .venv/bin/python scripts/smoke_llm_endpoint.py

Exit 0 — оба вызова вернули схема-валидные ответы; 1 — конфиг неполный,
endpoint недоступен или ответ не распарсился (typed ошибка на stderr).
"""

from __future__ import annotations

import asyncio
import base64
import sys
import zlib
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from deckdna.errors import DeckDNAError  # noqa: E402
from deckdna.providers.factory import build_gateway  # noqa: E402
from deckdna.settings import Settings  # noqa: E402


def _png_1px() -> str:
    """Минимальный валидный PNG 1×1 как data URL (без PIL-зависимости)."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            len(data).to_bytes(4, "big")
            + tag
            + data
            + zlib.crc32(tag + data).to_bytes(4, "big")
        )

    ihdr = (1).to_bytes(4, "big") * 2 + b"\x08\x02\x00\x00\x00"
    raw = zlib.compress(b"\x00\xff\x00\x00")  # filter byte + RGB pixel
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", raw)
        + chunk(b"IEND", b"")
    )
    return "data:image/png;base64," + base64.b64encode(png).decode()


def sanitized_host(url: str) -> str:
    """host[:port] без userinfo/query — parsed.netloc тащил бы user:token@ в вывод."""
    parsed = urlparse(url)
    host = parsed.hostname or "<no-host>"
    if ":" in host and not host.startswith("["):  # IPv6
        host = f"[{host}]"
    return f"{host}:{parsed.port}" if parsed.port is not None else host


async def _smoke(settings: Settings) -> int:
    from deckdna.audit.contextual import SlideChecksResult
    from deckdna.contracts.deck_plan import Brief, DeckPlan
    from deckdna.contracts.serialize import to_schema_dict

    gateway = build_gateway(settings)
    host = sanitized_host(settings.provider_base_url)
    print(f"endpoint: {host}")
    print(f"models: text={settings.model_text} vision={settings.model_vision}")
    try:
        plan = await gateway.text_json(
            "storyline",
            {
                "brief": to_schema_dict(
                    Brief(
                        purpose="smoke test",
                        audience="dev",
                        language="ru",
                        target_slide_count=10,
                    )
                ),
                "evidence_graph": None,
                "design_dna_capacities": None,
            },
            DeckPlan,
        )
        print(f"text_json/storyline OK: DeckPlan id={plan.id}, "
              f"{len(plan.slides)} slides")
        checks = await gateway.vision_json(
            "slide_checks",
            [_png_1px()],
            {
                "slide_image": "smoke-1px",
                "slide_text": "Smoke check slide",
                "title_intent": "verify vision path",
                "evidence_excerpt": "",
                "deck_language": "ru",
            },
            SlideChecksResult,
        )
        print(f"vision_json/slide_checks OK: {len(checks.verdicts)} verdicts")
    finally:
        close = getattr(gateway, "aclose", None)
        if close is not None:
            await close()
    return 0


def main() -> int:
    try:
        settings = Settings()
    except Exception as exc:  # noqa: BLE001 — smoke печатает причину
        print(f"settings parse failed: {exc}", file=sys.stderr)
        return 1
    if settings.mock_provider:
        print(
            "DECKDNA_MOCK_PROVIDER не выключен — выстави =false и задай "
            "DECKDNA_PROVIDER_BASE_URL/API_KEY/MODEL_*",
            file=sys.stderr,
        )
        return 1
    try:
        return asyncio.run(_smoke(settings))
    except DeckDNAError as exc:
        print(
            f"smoke failed: {exc.code} (stage={exc.stage}): {exc.message}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
