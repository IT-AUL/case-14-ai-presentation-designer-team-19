#!/usr/bin/env python3
"""Проверка VK Inference перед прогонами: что умеет endpoint (17.10).

Параметры — из ``.env``:
- ``DECKDNA_PROVIDER_BASE_URL``, ``DECKDNA_PROVIDER_API_KEY``;
- ``DECKDNA_PROVIDER_PROJECT_ID`` → заголовок ``Project-ID``;
- ``DECKDNA_MODEL_TEXT``, ``DECKDNA_PROVIDER_EXTRA_BODY``.

Каждая проверка — один короткий запрос:

1. ``chat`` — обычный ответ, время ответа;
2. ``json_schema`` — ``response_format: json_schema`` (так работает весь пайплайн);
3. ``json_object`` — запасной режим, если json_schema не поддержан;
4. ``thinking_off`` — принимается ли ``chat_template_kwargs.enable_thinking=false``
   и есть ли в ответах ``<think>``;
5. ``vision`` — принимает ли модель картинку на вход;
6. ``parallel`` — 3 одновременных запроса (три варианта идут параллельно).

Ключ и Project-ID не печатаются. Итог — таблица и советы для ``.env``, плюс
JSON в ``artifacts/vk/probe-<время>.json``.

    uv run python scripts/vk_probe.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from deckdna.providers.factory import provider_headers  # noqa: E402
from deckdna.providers.openai_compat import extract_json_text  # noqa: E402
from deckdna.settings import Settings  # noqa: E402

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
_SCHEMA = {
    "name": "capital",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"city": {"type": "string"}, "population_mln": {"type": "number"}},
        "required": ["city", "population_mln"],
        "additionalProperties": False,
    },
}


def _host(url: str) -> str:
    return urlparse(url).hostname or "<no-host>"


async def _post(client: httpx.AsyncClient, body: dict[str, Any]) -> tuple[int, dict | str, float]:
    started = time.monotonic()
    try:
        resp = await client.post("/chat/completions", json=body)
    except httpx.HTTPError as exc:
        return 0, f"{type(exc).__name__}: {exc}", time.monotonic() - started
    elapsed = time.monotonic() - started
    try:
        return resp.status_code, resp.json(), elapsed
    except ValueError:
        return resp.status_code, resp.text[:300], elapsed


def _content(data: dict | str) -> str:
    if isinstance(data, dict):
        try:
            return data["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError):
            return ""
    return ""


def _err(data: dict | str) -> str:
    if isinstance(data, dict):
        err = data.get("error") or data.get("detail") or data
        return json.dumps(err, ensure_ascii=False)[:200]
    return str(data)[:200]


async def probe(cfg: Settings) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {cfg.provider_api_key}",
        "Content-Type": "application/json",
        "User-Agent": "DeckDNA/1.0 (+https://github.com/deckdna/deckdna)",
        **provider_headers(cfg),
    }
    model = cfg.model_text
    question = [{"role": "user", "content": "Столица России? Ответь одним словом."}]
    results: dict[str, Any] = {}
    async with httpx.AsyncClient(
        base_url=cfg.provider_base_url.rstrip("/"), headers=headers, timeout=120
    ) as client:
        status, data, t = await _post(client, {"model": model, "messages": question})
        text = _content(data)
        results["chat"] = {
            "ok": status == 200 and bool(text), "status": status, "seconds": round(t, 2),
            "answer": text[:120] if status == 200 else _err(data),
            "think_in_answer": "<think>" in text,
            "usage": data.get("usage") if isinstance(data, dict) else None,
        }

        json_q = [{"role": "user", "content":
                   "Дай столицу России и население в млн. Только JSON по схеме."}]
        for mode, fmt in (("json_schema", {"type": "json_schema", "json_schema": _SCHEMA}),
                          ("json_object", {"type": "json_object"})):
            status, data, t = await _post(client, {
                "model": model, "messages": json_q, "temperature": 0, "response_format": fmt,
            })
            text = _content(data)
            parsed = None
            if status == 200:
                try:
                    parsed = json.loads(extract_json_text(text))
                except json.JSONDecodeError:
                    parsed = None
            results[mode] = {
                # валидный JSON-объект — этого хватает пайплайну: форму ответа
                # задаёт текст промпта, поля проверяет pydantic
                "ok": isinstance(parsed, dict),
                # схема соблюдена буквально (ограниченное декодирование)
                "schema_enforced": isinstance(parsed, dict)
                and set(parsed) == {"city", "population_mln"},
                "status": status, "seconds": round(t, 2),
                "raw_is_clean_json": status == 200 and text.strip()[:1] == "{",
                "detail": (text[:160] if status == 200 else _err(data)),
            }

        status, data, t = await _post(client, {
            "model": model, "messages": question,
            "chat_template_kwargs": {"enable_thinking": False},
        })
        text = _content(data)
        results["thinking_off"] = {
            "ok": status == 200, "status": status, "seconds": round(t, 2),
            "think_in_answer": "<think>" in text,
            "detail": text[:120] if status == 200 else _err(data),
        }

        image = "data:image/png;base64," + base64.b64encode(_PNG).decode()
        status, data, t = await _post(client, {"model": model, "messages": [{
            "role": "user", "content": [
                {"type": "text", "text": "Какого цвета эта картинка? Одно слово."},
                {"type": "image_url", "image_url": {"url": image}},
            ]}]})
        results["vision"] = {
            "ok": status == 200 and bool(_content(data)), "status": status,
            "seconds": round(t, 2),
            "detail": _content(data)[:120] if status == 200 else _err(data),
        }

        started = time.monotonic()
        calls = await asyncio.gather(*(
            _post(client, {"model": model, "messages": question}) for _ in range(3)
        ))
        results["parallel"] = {
            "ok": all(s == 200 for s, _, _ in calls),
            "statuses": [s for s, _, _ in calls],
            "seconds_total": round(time.monotonic() - started, 2),
            "seconds_each": [round(x, 2) for _, _, x in calls],
        }
    return results


def advice(r: dict[str, Any]) -> list[str]:
    tips: list[str] = []
    if not r["chat"]["ok"]:
        tips.append("Базовый запрос не проходит — проверь ключ, Project-ID и URL.")
        return tips
    if not r["json_schema"]["ok"]:
        tips.append(
            "json_schema не работает: пайплайн получит structured_output_invalid. "
            + (
                "Работает json_object — переключить провайдер на него."
                if r["json_object"]["ok"]
                else "json_object тоже не работает — нужен разбор без response_format."
            )
        )
    elif not r["json_schema"]["schema_enforced"]:
        tips.append("json_schema принимается, но схема НЕ навязывается: имена полей "
                    "держит только текст промпта, отклонения ловит валидация.")
    elif not r["json_schema"]["raw_is_clean_json"]:
        tips.append("json_schema работает, ответ в обёртке — его чистит extract_json_text.")
    if r["thinking_off"]["ok"]:
        tips.append("enable_thinking=false принимается — DECKDNA_PROVIDER_EXTRA_BODY оставить.")
    else:
        tips.append("enable_thinking=false отвергнут — убери DECKDNA_PROVIDER_EXTRA_BODY из .env.")
    if r["chat"]["think_in_answer"] or r["thinking_off"]["think_in_answer"]:
        tips.append("В ответах есть <think> — время вырастет; extract_json_text его вырезает.")
    if r["vision"]["ok"]:
        tips.append("Картинки принимаются — DECKDNA_MODEL_VISION=<та же модель> (VLM-аудит).")
    else:
        tips.append("Картинки не принимаются — оставь DECKDNA_MODEL_VISION пустым "
                    "(контекстный аудит пропустится с раскрытием).")
    if not r["parallel"]["ok"]:
        tips.append("Параллельные запросы получают отказ — подними "
                    "DECKDNA_PROVIDER_MIN_REQUEST_INTERVAL_S (например 2.0) "
                    "или DECKDNA_MAX_PARALLEL_VARIANTS=1.")
    return tips


def main() -> int:
    cfg = Settings()
    missing = [n for n, v in (("DECKDNA_PROVIDER_BASE_URL", cfg.provider_base_url),
                              ("DECKDNA_PROVIDER_API_KEY", cfg.provider_api_key),
                              ("DECKDNA_MODEL_TEXT", cfg.model_text)) if not v]
    if missing:
        print(f"Не заполнено в .env: {', '.join(missing)}", file=sys.stderr)
        return 1
    if not cfg.provider_project_id:
        print("Внимание: DECKDNA_PROVIDER_PROJECT_ID пуст — VK, скорее всего, ответит ошибкой.",
              file=sys.stderr)
    print(f"endpoint: {_host(cfg.provider_base_url)}  model: {cfg.model_text}  "
          f"Project-ID: {'задан' if cfg.provider_project_id else 'нет'}")
    results = asyncio.run(probe(cfg))
    for name, r in results.items():
        mark = "OK " if r.get("ok") else "НЕТ"
        secs = r.get("seconds", r.get("seconds_total"))
        detail = r.get("answer") or r.get("detail") or r.get("statuses") or ""
        print(f"  [{mark}] {name:<13} {secs!s:>6} с  {str(detail)[:90]}")
    print("\nЧто сделать:")
    for tip in advice(results):
        print(f"  - {tip}")
    out = ROOT / "artifacts" / "vk" / f"probe-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"model": cfg.model_text, "results": results},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nПолные результаты: {out.relative_to(ROOT)}")
    return 0 if results["chat"]["ok"] and results["json_schema"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
