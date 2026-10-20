"""Клиент к REST API DeckDNA для MCP-сервера (ADR-032 §1).

MCP-инструменты — тонкий слой: каждый вызов идёт в существующий REST API
(``docs/contracts/API.md``), бизнес-логика не дублируется. По умолчанию API
поднимается в том же процессе (``httpx.ASGITransport`` поверх
``deckdna.api.app.app``) — навык работает одной командой ``deckdna mcp``.
С ``api_url`` клиент ходит в отдельный сервис (контейнер ``api`` в compose).

Ошибки API (конверт ``{"error": {"code", "message", "stage", ...}}``)
превращаются в ``DeckDNAToolError`` с тем же кодом ``DeckDNAError``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

API_PREFIX = "/api/v1"


class DeckDNAToolError(Exception):
    """Типизированная ошибка API: код ``DeckDNAError`` + сообщение."""

    def __init__(self, code: str, message: str, status: int | None = None,
                 stage: str | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status
        self.stage = stage

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "stage": self.stage,
                "http_status": self.status}


class DeckDNAClient:
    """Асинхронный клиент API. ``api_url=None`` — API в этом же процессе."""

    def __init__(self, api_url: str | None = None, timeout_s: float = 600.0) -> None:
        if api_url:
            self._http = httpx.AsyncClient(base_url=api_url.rstrip("/"), timeout=timeout_s)
        else:
            from deckdna.api.app import app

            self._http = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://deckdna.local",
                timeout=timeout_s,
            )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def request(
        self, method: str, path: str, *, json_body: Any = None, files: Any = None,
        data: Any = None, params: dict[str, Any] | None = None,
    ) -> Any:
        response = await self._http.request(
            method, f"{API_PREFIX}{path}", json=json_body, files=files, data=data,
            params=params,
        )
        if response.status_code >= 400:
            try:
                err = response.json().get("error") or {}
            except (json.JSONDecodeError, AttributeError):
                err = {}
            raise DeckDNAToolError(
                err.get("code") or "internal_error",
                err.get("message") or response.text[:300],
                status=response.status_code,
                stage=err.get("stage"),
            )
        if response.status_code == 204 or not response.content:
            return None
        if response.headers.get("content-type", "").startswith("application/json"):
            return response.json()
        return response.content

    async def get(self, path: str, **params: Any) -> Any:
        return await self.request("GET", path, params={k: v for k, v in params.items()
                                                       if v is not None} or None)

    async def post(self, path: str, body: Any = None, **kwargs: Any) -> Any:
        return await self.request("POST", path, json_body=body, **kwargs)


__all__ = ["API_PREFIX", "DeckDNAClient", "DeckDNAToolError"]
