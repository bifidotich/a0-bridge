"""Клиент к external API Agent Zero.

Реальные endpoint'ы (из Settings → External API в WebUI), все под префиксом /api/ и
с двойным api в имени, аутентификация по заголовку X-API-KEY:

  POST /api/api_message         — отправить сообщение, СИНХРОННО вернуть {context_id, response}
  GET/POST /api/api_log_get     — лог чата: {context_id, log: {items, progress, total_items, ...}}
  POST /api/api_reset_chat      — сброс истории чата
  POST /api/api_terminate_chat  — удалить чат
  POST /api/api_files_get       — файлы по путям, base64

Scheduler / projects / presets во external API не представлены (только эти 5 ручек).
"""

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
    """Тонкая обёртка над httpx.AsyncClient для external API Agent Zero."""

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

    # --- low-level ---

    async def _post(
        self, path: str, payload: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        log.debug("POST %s payload=%s", path, payload)
        try:
            resp = await self._client.post(path, json=payload, timeout=timeout)
        except httpx.HTTPError as e:
            raise A0HTTPError(f"Сеть/таймаут: {e}") from e

        if resp.status_code >= 400:
            text = resp.text[:500]
            log.warning("Agent Zero POST %s -> %s: %s", path, resp.status_code, text)
            raise A0HTTPError(
                f"Agent Zero вернул {resp.status_code}: {text}", status=resp.status_code, body=text
            )
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    # --- API methods ---

    async def send_message(
        self,
        message: str,
        *,
        context_id: str | None = None,
        project_name: str | None = None,
        attachments: list[dict[str, str]] | None = None,
        lifetime_hours: float | None = None,
    ) -> dict[str, Any]:
        """POST /api/api_message. Синхронно: возвращает {context_id, response}.

        project_name активирует проект только на ПЕРВОМ сообщении (без context_id).
        attachments — список {filename, base64}.
        """
        payload: dict[str, Any] = {
            "message": message,
            "lifetime_hours": lifetime_hours if lifetime_hours is not None else settings.a0_lifetime_hours,
        }
        if context_id:
            payload["context_id"] = context_id
        if project_name and not context_id:
            payload["project_name"] = project_name
        if attachments:
            payload["attachments"] = attachments
        return await self._post("/api/api_message", payload, timeout=settings.a0_message_timeout)

    async def get_log(self, context_id: str, *, length: int | None = None) -> dict[str, Any]:
        """POST /api/api_log_get. Возвращает {context_id, log: {items, progress, ...}}."""
        payload: dict[str, Any] = {"context_id": context_id}
        if length is not None:
            payload["length"] = length
        return await self._post("/api/api_log_get", payload)

    async def reset_chat(self, context_id: str) -> dict[str, Any]:
        return await self._post("/api/api_reset_chat", {"context_id": context_id})

    async def terminate_chat(self, context_id: str) -> dict[str, Any]:
        return await self._post("/api/api_terminate_chat", {"context_id": context_id})

    async def get_files(self, paths: list[str]) -> dict[str, Any]:
        """POST /api/api_files_get. Возвращает {filename: base64}."""
        return await self._post("/api/api_files_get", {"paths": paths})

    async def health(self) -> dict[str, Any]:
        """Лёгкая проверка: дёргаем api_log_get с несуществующим контекстом.

        200 → API доступен и ключ принят. 401 → ключ неверный. Сеть → A0 недоступен.
        Неизвестный контекст обычно тоже даёт 200 (пустой лог) либо мягкую ошибку 4xx.
        """
        try:
            await self.get_log("__healthcheck__", length=1)
            return {"alive": True, "auth": "ok"}
        except A0HTTPError as e:
            if e.status == 401:
                return {"alive": True, "auth": "invalid_key", "detail": e.body}
            if e.status and 400 <= e.status < 500:
                # Сервер ответил — значит жив; контекст просто не найден.
                return {"alive": True, "auth": "ok", "note": f"probe {e.status}"}
            raise
