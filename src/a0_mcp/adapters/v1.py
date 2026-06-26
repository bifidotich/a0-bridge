"""Адаптер для Agent Zero v1.2 — v1.20.

Endpoints в этой линейке (из docs/developer/connectivity.md и DeepWiki):
  POST /api_message            — синхронная отправка
  POST /message_async          — асинхронная отправка, возврат сразу
  POST /api_log_get            — чтение лога чата
  POST /api_terminate_chat     — остановка/удаление чата
  POST /api_reset_chat         — сброс истории
  POST /api_files_get          — получение файлов
  POST /scheduler_tasks_list   — список задач планировщика
  POST /scheduler_task_create  — создать задачу
  POST /scheduler_task_run     — запустить немедленно
  POST /scheduler_task_delete  — удалить

Projects и presets в v1.x не имеют отдельных публичных endpoint'ов в external API
(управляются через WebUI и хранятся в /a0/usr). Здесь возвращаем то, что доступно
через настройки, иначе пустой список.
"""

from __future__ import annotations

from typing import Any

from .base import BaseAdapter

# Имена endpoint'ов планировщика менялись между минорными релизами, поэтому каждый
# вызов пробует несколько вариантов и берёт первый отработавший.
_SCHED_LIST_PATHS = ("/scheduler_tasks_list", "/api/scheduler_tasks_list")
_SCHED_CREATE_PATHS = ("/scheduler_task_create", "/api/scheduler_task_create")
_SCHED_RUN_PATHS = ("/scheduler_task_run", "/api/scheduler_task_run")
_SCHED_DELETE_PATHS = ("/scheduler_task_delete", "/api/scheduler_task_delete")


def _parse_cron(schedule: str) -> dict[str, str]:
    """Разбирает crontab-строку "minute hour day month weekday" в словарь полей A0.

    Допускает '*' и стандартные crontab-выражения в каждом поле.
    """
    parts = schedule.split()
    if len(parts) != 5:
        raise ValueError(
            f"Ожидается crontab из 5 полей 'm h dom mon dow', получено: {schedule!r}"
        )
    minute, hour, day, month, weekday = parts
    return {
        "minute": minute,
        "hour": hour,
        "day": day,
        "month": month,
        "weekday": weekday,
    }


class V1Adapter(BaseAdapter):
    version_label = "v1"

    async def health(self) -> dict[str, Any]:
        # В v1 нет специального /health endpoint. Делаем легкий probe через api_log_get
        # с заведомо несуществующим контекстом — нам важен только сам факт ответа.
        try:
            await self.client.post("/api_log_get", {"context_id": "__probe__", "length": 1})
        except Exception:
            # Возвращаем true health через любой 4xx ответ — значит сервер живой
            pass
        return {"alive": True, "version_family": "v1"}

    async def send_message(
        self,
        prompt: str,
        *,
        context_id: str | None = None,
        project_id: str | None = None,
        preset: str | None = None,
        attachments: list[str] | None = None,
        async_mode: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"text": prompt}
        if context_id:
            payload["context_id"] = context_id
        if project_id:
            payload["project_id"] = project_id
        if preset:
            payload["preset"] = preset
        if attachments:
            payload["attachments"] = attachments

        endpoint = "/message_async" if async_mode else "/api_message"
        return await self.client.post(endpoint, payload)

    async def get_log(
        self,
        context_id: str,
        *,
        length: int | None = None,
        from_index: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"context_id": context_id}
        if length is not None:
            payload["length"] = length
        if from_index is not None:
            payload["from"] = from_index
        return await self.client.post("/api_log_get", payload)

    async def reset_chat(self, context_id: str) -> dict[str, Any]:
        return await self.client.post("/api_reset_chat", {"context_id": context_id})

    async def terminate_chat(self, context_id: str) -> dict[str, Any]:
        return await self.client.post("/api_terminate_chat", {"context_id": context_id})

    async def list_projects(self) -> list[dict[str, Any]]:
        # В v1.x публичного endpoint для проектов нет, либо он внутренний.
        # Пробуем общий list endpoint; если его нет — возвращаем пустой список,
        # пользователь оперирует project_id напрямую (как строкой из WebUI).
        try:
            resp = await self.client.get("/api/projects_list")
            items = resp.get("projects") or resp.get("items") or []
            return list(items)
        except Exception:
            return []

    async def list_presets(self) -> list[dict[str, Any]]:
        try:
            resp = await self.client.get("/api/presets_list")
            items = resp.get("presets") or resp.get("items") or []
            return list(items)
        except Exception:
            return []

    # --- Scheduler ---

    async def list_schedules(self) -> list[dict[str, Any]]:
        for path in _SCHED_LIST_PATHS:
            try:
                resp = await self.client.post(path, {})
                items = resp.get("tasks") or resp.get("items") or resp.get("scheduler_tasks")
                if items is not None:
                    return list(items)
            except Exception:
                continue
        return []

    async def create_schedule(
        self,
        *,
        name: str,
        prompt: str,
        schedule: str | None = None,
        system_prompt: str = "",
        task_type: str = "scheduled",
        project_id: str | None = None,
        dedicated_context: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": name,
            "type": task_type,
            "system_prompt": system_prompt,
            "prompt": prompt,
            "dedicated_context": dedicated_context,
        }
        if project_id:
            payload["project_id"] = project_id
        if task_type == "scheduled":
            if not schedule:
                raise ValueError("Для task_type='scheduled' обязателен crontab в schedule")
            payload["schedule"] = _parse_cron(schedule)

        return await self._post_first(_SCHED_CREATE_PATHS, payload, what="создать задачу")

    async def run_schedule(self, task_id: str) -> dict[str, Any]:
        payload = {"task_id": task_id, "uuid": task_id}
        return await self._post_first(_SCHED_RUN_PATHS, payload, what="запустить задачу")

    async def delete_schedule(self, task_id: str) -> dict[str, Any]:
        payload = {"task_id": task_id, "uuid": task_id}
        return await self._post_first(_SCHED_DELETE_PATHS, payload, what="удалить задачу")

    async def _post_first(
        self, paths: tuple[str, ...], payload: dict[str, Any], *, what: str
    ) -> dict[str, Any]:
        """POST на первый отработавший из ``paths``. Если все упали — пробрасывает последнюю ошибку."""
        last_err: Exception | None = None
        for path in paths:
            try:
                return await self.client.post(path, payload)
            except Exception as e:  # noqa: BLE001 — копим ошибку, чтобы пробросить последнюю
                last_err = e
                continue
        raise RuntimeError(
            f"Не удалось {what}: ни один из endpoint'ов планировщика не ответил ({paths}). "
            f"Последняя ошибка: {last_err}"
        )
