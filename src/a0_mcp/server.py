"""FastMCP сервер. Оборачивает external API Agent Zero в инструменты для Claude Code.

API синхронный: a0_run отправляет сообщение и сразу возвращает финальный ответ субагента.
Параллельный fan-out достигается параллельными вызовами a0_run из Claude Code — каждый
блокируется до своего ответа независимо.

Инструменты:
  a0_health     — проверка связности и валидности ключа
  a0_run        — отправить сообщение субагенту, синхронно вернуть ответ (+ context_id)
  a0_log_tail   — последние N записей лога чата (для отладки/инспекции)
  a0_reset      — сбросить историю чата (context_id остаётся)
  a0_terminate  — удалить чат
  a0_files_get  — получить файлы из workspace субагента (base64)
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field

from .client import A0Client
from .config import settings

log = logging.getLogger(__name__)


def _csv(raw: str) -> list[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


_transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=settings.mcp_dns_rebinding_protection,
    allowed_hosts=_csv(settings.mcp_allowed_hosts),
    allowed_origins=_csv(settings.mcp_allowed_origins),
)

mcp = FastMCP(
    name="a0-mcp",
    instructions=(
        "Wrapper around Agent Zero's external API. Use a0_run to delegate a task to a subordinate "
        "agent and get its final answer synchronously. Pass context_id to continue an existing chat, "
        "or project_name (first message only) to scope the workspace. Run several a0_run calls in "
        "parallel to fan out isolated subagents. Use a0_log_tail to inspect what a subagent did, "
        "a0_files_get to pull files it produced, and a0_terminate to clean up."
    ),
    transport_security=_transport_security,
)


# --- Глобальный shared client (живёт весь lifespan сервера) ---
_client: A0Client | None = None


def _client_ref() -> A0Client:
    global _client
    if _client is None:
        _client = A0Client()
        log.info("Connected to Agent Zero at %s", settings.a0_url)
    return _client


def _resolve_project(project_name: str | None) -> str | None:
    return project_name or (settings.a0_default_project or None)


def _extract_context_id(resp: dict[str, Any]) -> str | None:
    for key in ("context_id", "context", "ctx_id", "chat_id"):
        if resp.get(key):
            return str(resp[key])
    return None


def _extract_response(resp: dict[str, Any]) -> str:
    for key in ("response", "answer", "result", "text"):
        if resp.get(key):
            return str(resp[key])
    return ""


# ============================================================================
#                                  TOOLS
# ============================================================================


@mcp.tool()
async def a0_health() -> dict[str, Any]:
    """Проверить связность с Agent Zero и валидность API-ключа."""
    return {"a0_url": settings.a0_url, **(await _client_ref().health())}


@mcp.tool()
async def a0_run(
    message: Annotated[str, Field(description="Задача субагенту, на естественном языке")],
    context_id: Annotated[
        str | None,
        Field(default=None, description="Продолжить существующий чат. Пусто — создать новый."),
    ] = None,
    project_name: Annotated[
        str | None,
        Field(default=None, description="Проект A0 (workspace/memory). Активируется только на первом сообщении."),
    ] = None,
    lifetime_hours: Annotated[
        float | None,
        Field(default=None, description="Время жизни чата в часах (по умолчанию из конфига)."),
    ] = None,
) -> dict[str, Any]:
    """Отправить задачу субагенту Agent Zero и синхронно получить финальный ответ.

    API блокирующий: возврат происходит, когда субагент закончил. Для параллельной работы
    делайте несколько вызовов a0_run в одном ответе — каждый создаёт изолированный субагент.
    Сохраните context_id из ответа, чтобы продолжить тот же чат следующим a0_run.
    """
    resp = await _client_ref().send_message(
        message,
        context_id=context_id,
        project_name=_resolve_project(project_name),
        lifetime_hours=lifetime_hours,
    )
    return {
        "context_id": _extract_context_id(resp) or context_id,
        "response": _extract_response(resp),
        "raw": resp,
    }


@mcp.tool()
async def a0_log_tail(
    context_id: Annotated[str, Field(description="Context ID чата")],
    length: Annotated[int, Field(default=20, description="Кол-во последних записей")] = 20,
) -> dict[str, Any]:
    """Последние N записей лога чата. Для отладки и понимания, что делал субагент."""
    return await _client_ref().get_log(context_id, length=length)


@mcp.tool()
async def a0_reset(
    context_id: Annotated[str, Field(description="Context ID для сброса")],
) -> dict[str, Any]:
    """Сбросить историю чата (context_id остаётся живым, начинаем заново)."""
    return await _client_ref().reset_chat(context_id)


@mcp.tool()
async def a0_terminate(
    context_id: Annotated[str, Field(description="Context ID для удаления")],
) -> dict[str, Any]:
    """Удалить чат и освободить ресурсы. Используйте после завершения работы с субагентом."""
    return await _client_ref().terminate_chat(context_id)


@mcp.tool()
async def a0_files_get(
    paths: Annotated[
        list[str],
        Field(description="Пути к файлам в A0, напр. ['/a0/usr/uploads/report.txt']"),
    ],
) -> dict[str, Any]:
    """Получить файлы из workspace Agent Zero. Возвращает {filename: base64}."""
    return await _client_ref().get_files(paths)


def run_streamable_http() -> None:
    """Запустить сервер по streamable HTTP — для удалённого подключения из Claude Code."""
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    import uvicorn

    app = mcp.streamable_http_app()

    if settings.mcp_auth_token:
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.responses import JSONResponse

        token = settings.mcp_auth_token

        class BearerAuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
                auth = request.headers.get("authorization", "")
                if auth != f"Bearer {token}":
                    return JSONResponse({"error": "unauthorized"}, status_code=401)
                return await call_next(request)

        app.add_middleware(BearerAuthMiddleware)
        log.info("Bearer auth enabled")

    log.info("Starting a0-mcp on http://%s:%s/mcp", settings.mcp_host, settings.mcp_port)
    uvicorn.run(app, host=settings.mcp_host, port=settings.mcp_port, log_level=settings.log_level.lower())
