"""FastMCP сервер. Регистрирует инструменты для Claude Code.

Инструменты:
  a0_run          — отправить задачу субагенту, вернуть context_id (не ждать)
  a0_wait         — дождаться завершения задачи и вернуть финальный ответ (+ streaming прогресса)
  a0_status       — получить текущее состояние без ожидания (для poll)
  a0_log_tail     — последние N строк лога (для отладки)
  a0_projects     — список проектов
  a0_presets      — список model presets
  a0_schedule     — управление cron/planned задачами планировщика Agent Zero
  a0_reset        — сбросить историю чата
  a0_terminate    — остановить чат
  a0_health       — проверка связности с Agent Zero
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from .adapters import BaseAdapter, make_adapter
from .client import A0Client, A0HTTPError
from .config import settings

log = logging.getLogger(__name__)

mcp = FastMCP(
    name="a0-mcp",
    instructions=(
        "Wrapper around Agent Zero. Use a0_run to delegate work to a subordinate agent, "
        "then a0_wait or a0_status to retrieve the result. a0_wait also streams live progress "
        "while the subagent works. Each subagent maintains its own context, so multiple a0_run "
        "calls in parallel give you isolated workers. Use project_id to scope the workspace and "
        "preset to switch model configuration. Use a0_schedule for recurring (cron) or one-off "
        "background tasks."
    ),
)


# --- Глобальный shared client + adapter (живут весь lifespan сервера) ---
_client: A0Client | None = None
_adapter: BaseAdapter | None = None


def _adapter_ref() -> BaseAdapter:
    """Lazy инициализация. Используем при первом вызове любого tool."""
    global _client, _adapter
    if _adapter is None:
        _client = A0Client()
        _adapter = make_adapter(_client)
        log.info(
            "Connected to Agent Zero %s at %s (adapter=%s)",
            settings.a0_version,
            settings.a0_url,
            _adapter.version_label,
        )
    return _adapter


def _resolve_project(project_id: str | None) -> str | None:
    """Если пользователь не указал project_id, используем default из конфига."""
    return project_id or (settings.a0_default_project or None)


def _extract_context_id(resp: dict[str, Any]) -> str:
    """Поддерживаем разные имена поля между версиями."""
    for key in ("context_id", "context", "ctx_id", "chat_id"):
        v = resp.get(key)
        if v:
            return str(v)
    raise A0HTTPError(f"Agent Zero не вернул context_id, ответ: {resp}")


def _log_obj(log_resp: dict[str, Any]) -> dict[str, Any]:
    """Достаём тело лога (некоторые версии оборачивают в ключ 'log')."""
    return log_resp.get("log") or log_resp


def _log_items(log_resp: dict[str, Any]) -> list[dict[str, Any]]:
    return _log_obj(log_resp).get("items") or []


def _is_finished(log_resp: dict[str, Any]) -> bool:
    """Эвристика: считаем чат завершённым, если progress=done или последний item имеет финальный type."""
    log_obj = _log_obj(log_resp)
    progress = (log_obj.get("progress") or "").lower()
    if progress in {"done", "finished", "complete", "completed"}:
        return True

    items = log_obj.get("items") or []
    if not items:
        return False
    last = items[-1]
    last_type = (last.get("type") or "").lower()
    return last_type in {"response", "result", "final", "answer", "done"}


def _final_text(log_resp: dict[str, Any]) -> str:
    """Достаём финальный текст из последнего assistant-сообщения."""
    items = _log_items(log_resp)
    for item in reversed(items):
        t = (item.get("type") or "").lower()
        if t in {"response", "result", "final", "answer"}:
            content = item.get("content") or item.get("text") or item.get("kvps", {}).get("text")
            if content:
                return str(content)
    # Фоллбек — последний item целиком
    return str(items[-1]) if items else "(пустой лог)"


def _item_brief(item: dict[str, Any]) -> str:
    """Короткая человекочитаемая строка про один шаг лога — для streaming в Claude Code."""
    t = item.get("type") or "?"
    head = item.get("heading") or item.get("kvps", {}).get("tool_name") or ""
    body = item.get("content") or item.get("text") or item.get("kvps", {}).get("text") or ""
    line = " ".join(str(x) for x in (f"[{t}]", head, body) if x).strip()
    return line[:240]


# ============================================================================
#                                  TOOLS
# ============================================================================


@mcp.tool()
async def a0_health() -> dict[str, Any]:
    """Проверить связность с Agent Zero. Возвращает версию и базовую информацию."""
    adapter = _adapter_ref()
    return {
        "configured_version": settings.a0_version,
        "adapter": adapter.version_label,
        "a0_url": settings.a0_url,
        **(await adapter.health()),
    }


@mcp.tool()
async def a0_run(
    prompt: Annotated[str, Field(description="Задача субагенту, на естественном языке")],
    project_id: Annotated[
        str | None,
        Field(default=None, description="ID проекта Agent Zero. Изолирует workspace, memory, secrets."),
    ] = None,
    preset: Annotated[
        str | None,
        Field(default=None, description="Имя model preset (например 'fast', 'balanced', 'best')."),
    ] = None,
    context_id: Annotated[
        str | None,
        Field(default=None, description="Если задан — продолжить существующий чат, иначе создать новый."),
    ] = None,
) -> dict[str, Any]:
    """Отправить задачу субагенту Agent Zero. Возвращает context_id мгновенно (async режим).

    Дальше используйте a0_wait для блокирующего ожидания (со стримингом прогресса)
    или a0_status для poll. Несколько одновременных a0_run создают изолированных
    параллельных субагентов.
    """
    adapter = _adapter_ref()
    resp = await adapter.send_message(
        prompt=prompt,
        context_id=context_id,
        project_id=_resolve_project(project_id),
        preset=preset,
        async_mode=True,
    )
    ctx = _extract_context_id(resp) if not context_id else context_id
    return {"context_id": ctx, "started": True, "raw": resp}


@mcp.tool()
async def a0_wait(
    context_id: Annotated[str, Field(description="Context ID из a0_run")],
    ctx: Context,
    timeout: Annotated[
        float | None,
        Field(default=None, description="Макс. ожидание в секундах. По умолчанию из конфига."),
    ] = None,
) -> dict[str, Any]:
    """Дождаться завершения задачи и вернуть финальный ответ субагента.

    Внутри делает polling /api_log_get с интервалом из A0_WAIT_POLL_INTERVAL и
    параллельно стримит прогресс в Claude Code: каждую новую запись лога субагента
    пушит как log-сообщение, а долю прошедшего времени — как progress notification.
    """
    adapter = _adapter_ref()
    total = timeout or settings.a0_wait_timeout
    start = time.monotonic()
    deadline = start + total
    seen = 0
    last: dict[str, Any] = {}

    while time.monotonic() < deadline:
        last = await adapter.get_log(context_id, length=200)
        items = _log_items(last)

        # --- streaming: пушим новые шаги субагента и долю прогресса ---
        for item in items[seen:]:
            brief = _item_brief(item)
            if brief:
                await _safe_log(ctx, brief)
        seen = len(items)
        await _safe_progress(ctx, progress=time.monotonic() - start, total=total)

        if _is_finished(last):
            await _safe_progress(ctx, progress=total, total=total)
            await _safe_log(ctx, "subagent finished")
            return {
                "context_id": context_id,
                "status": "done",
                "result": _final_text(last),
                "log": last,
            }
        await asyncio.sleep(settings.a0_wait_poll_interval)

    return {
        "context_id": context_id,
        "status": "timeout",
        "partial": _final_text(last) if last else None,
        "log": last,
    }


async def _safe_log(ctx: Context, message: str) -> None:
    """Отправить log-сообщение клиенту, не падая если канал недоступен."""
    try:
        await ctx.info(message)
    except Exception:  # noqa: BLE001 — streaming не должен ломать основную работу
        log.debug("ctx.info failed, message dropped: %s", message)


async def _safe_progress(ctx: Context, *, progress: float, total: float) -> None:
    """Отправить progress notification; no-op если клиент не передал progressToken."""
    try:
        await ctx.report_progress(progress=progress, total=total)
    except Exception:  # noqa: BLE001
        log.debug("ctx.report_progress failed (нет progressToken?)")


@mcp.tool()
async def a0_status(
    context_id: Annotated[str, Field(description="Context ID из a0_run")],
) -> dict[str, Any]:
    """Не блокирующая проверка состояния задачи. Не ждёт завершения."""
    adapter = _adapter_ref()
    log_resp = await adapter.get_log(context_id, length=50)
    finished = _is_finished(log_resp)
    return {
        "context_id": context_id,
        "status": "done" if finished else "running",
        "result": _final_text(log_resp) if finished else None,
        "progress": _log_obj(log_resp).get("progress"),
    }


@mcp.tool()
async def a0_log_tail(
    context_id: Annotated[str, Field(description="Context ID из a0_run")],
    length: Annotated[int, Field(default=20, description="Кол-во последних записей")] = 20,
) -> dict[str, Any]:
    """Последние N записей лога чата. Для отладки и понимания что делает субагент."""
    adapter = _adapter_ref()
    return await adapter.get_log(context_id, length=length)


@mcp.tool()
async def a0_projects() -> list[dict[str, Any]]:
    """Список доступных проектов Agent Zero. Пустой список означает что endpoint недоступен —
    используйте project_id напрямую как строку, как в WebUI."""
    return await _adapter_ref().list_projects()


@mcp.tool()
async def a0_presets() -> list[dict[str, Any]]:
    """Список model presets (Best, Balanced, Fast Cheap, Local, ...)."""
    return await _adapter_ref().list_presets()


@mcp.tool()
async def a0_schedule(
    action: Annotated[
        Literal["create", "list", "delete", "run"],
        Field(description="Что сделать: create / list / delete / run"),
    ],
    name: Annotated[
        str | None,
        Field(default=None, description="Имя задачи (для action=create)"),
    ] = None,
    prompt: Annotated[
        str | None,
        Field(default=None, description="Что должен выполнить субагент (для action=create)"),
    ] = None,
    schedule: Annotated[
        str | None,
        Field(
            default=None,
            description="Crontab '<m> <h> <dom> <mon> <dow>', напр. '0 9 * * 1-5' (для type=scheduled)",
        ),
    ] = None,
    task_type: Annotated[
        Literal["scheduled", "adhoc"],
        Field(default="scheduled", description="scheduled — по cron; adhoc — разовая, запуск вручную"),
    ] = "scheduled",
    task_id: Annotated[
        str | None,
        Field(default=None, description="ID задачи (для action=delete / run)"),
    ] = None,
    system_prompt: Annotated[
        str,
        Field(default="", description="Доп. системный промпт для задачи"),
    ] = "",
    project_id: Annotated[
        str | None,
        Field(default=None, description="Проект, в котором выполнять задачу"),
    ] = None,
    dedicated_context: Annotated[
        bool,
        Field(default=False, description="Запускать в выделенном контексте (свежая память каждый раз)"),
    ] = False,
) -> dict[str, Any]:
    """Управление планировщиком Agent Zero (Cron / Planned задачи).

    Примеры:
      a0_schedule(action="create", name="nightly-tests", prompt="прогони pytest и пришли отчёт",
                  schedule="0 3 * * *")          — каждый день в 03:00
      a0_schedule(action="list")                 — все задачи планировщика
      a0_schedule(action="run", task_id="...")   — запустить немедленно
      a0_schedule(action="delete", task_id="...")
    """
    adapter = _adapter_ref()

    if action == "list":
        return {"tasks": await adapter.list_schedules()}

    if action == "create":
        if not name or not prompt:
            raise ValueError("Для action='create' обязательны name и prompt")
        task = await adapter.create_schedule(
            name=name,
            prompt=prompt,
            schedule=schedule,
            system_prompt=system_prompt,
            task_type=task_type,
            project_id=_resolve_project(project_id),
            dedicated_context=dedicated_context,
        )
        return {"created": True, "task": task}

    if action == "run":
        if not task_id:
            raise ValueError("Для action='run' обязателен task_id")
        return {"ran": True, "result": await adapter.run_schedule(task_id)}

    if action == "delete":
        if not task_id:
            raise ValueError("Для action='delete' обязателен task_id")
        return {"deleted": True, "result": await adapter.delete_schedule(task_id)}

    raise ValueError(f"Неизвестный action: {action}")


@mcp.tool()
async def a0_reset(
    context_id: Annotated[str, Field(description="Context ID для сброса")],
) -> dict[str, Any]:
    """Сбросить историю чата (контекст остаётся, начинаем заново)."""
    return await _adapter_ref().reset_chat(context_id)


@mcp.tool()
async def a0_terminate(
    context_id: Annotated[str, Field(description="Context ID для остановки")],
) -> dict[str, Any]:
    """Остановить чат полностью (удаление контекста). Используйте после завершения работы."""
    return await _adapter_ref().terminate_chat(context_id)


def run_streamable_http() -> None:
    """Запустить сервер по streamable HTTP — для удалённого подключения из Claude Code."""
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # FastMCP в свежих версиях знает про streamable_http_app(). Получаем ASGI app и запускаем uvicorn.
    import uvicorn

    app = mcp.streamable_http_app()

    # Опциональный middleware для проверки bearer токена
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
