"""Async HTTP клиент к Agent Zero. Транспортный слой (не знает о версиях)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import settings

log = logging.getLogger(__name__)


class A0HTTPError(RuntimeError):
    """Исключение для всех ошибок взаимодействия с Agent Zero."""

    def __init__(self, message: str, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


class A0Client:
    """Тонкая обёртка над httpx.AsyncClient для Agent Zero external API.

    Транспортный слой — знает только об HTTP, ключе и базовых нюансах ответа.
    Маршрутизация по endpoint'ам делегируется адаптеру конкретной версии.
    """

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.a0_url.rstrip("/"),
            timeout=settings.a0_http_timeout,
            headers={
                "X-API-KEY": settings.a0_api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "A0Client":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """POST с JSON body, возвращает JSON ответ."""
        return await self._request("POST", path, json=payload or {})

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET, возвращает JSON ответ."""
        return await self._request("GET", path, params=params)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        log.debug("%s %s json=%s params=%s", method, path, json, params)
        try:
            resp = await self._client.request(method, path, json=json, params=params)
        except httpx.HTTPError as e:
            raise A0HTTPError(f"Сеть/таймаут: {e}") from e

        if resp.status_code >= 400:
            text = resp.text[:500]
            log.warning("Agent Zero %s %s -> %s: %s", method, path, resp.status_code, text)
            raise A0HTTPError(
                f"Agent Zero вернул {resp.status_code}: {text}",
                status=resp.status_code,
                body=text,
            )

        try:
            return resp.json()
        except ValueError:
            # Не-JSON ответ — оборачиваем как plain text
            return {"raw": resp.text}
