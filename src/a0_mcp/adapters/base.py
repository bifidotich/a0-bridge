"""Абстрактный интерфейс адаптера версии Agent Zero.

Адаптер инкапсулирует знание про конкретные endpoint'ы, имена полей и квирки
конкретного семейства версий. Tools слой работает только через этот интерфейс.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..client import A0Client


class BaseAdapter(ABC):
    """Базовый адаптер. Реализации: V1Adapter (v1.2-1.20), V2Adapter (v2.0+)."""

    version_label: str = "base"

    def __init__(self, client: A0Client) -> None:
        self.client = client

    # --- Health / discovery ---

    @abstractmethod
    async def health(self) -> dict[str, Any]:
        """Проверка доступности и базовая инфо. Должен бросать A0HTTPError при недоступности."""
        ...

    # --- Сообщения / задачи ---

    @abstractmethod
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
        """Отправляет сообщение агенту. Возвращает {context_id, message_id, ...}.

        async_mode=True — endpoint /message_async, возврат немедленный.
        async_mode=False — синхронный /api_message, ждёт финального ответа.
        """
        ...

    @abstractmethod
    async def get_log(
        self,
        context_id: str,
        *,
        length: int | None = None,
        from_index: int | None = None,
    ) -> dict[str, Any]:
        """Читает лог чата. Возвращает {items: [...], total_items, progress, ...}."""
        ...

    @abstractmethod
    async def reset_chat(self, context_id: str) -> dict[str, Any]:
        """Сбрасывает историю, контекст остаётся."""
        ...

    @abstractmethod
    async def terminate_chat(self, context_id: str) -> dict[str, Any]:
        """Останавливает текущее выполнение и удаляет контекст."""
        ...

    # --- Проекты ---

    @abstractmethod
    async def list_projects(self) -> list[dict[str, Any]]:
        """Список доступных проектов."""
        ...

    # --- Presets ---

    @abstractmethod
    async def list_presets(self) -> list[dict[str, Any]]:
        """Список model presets."""
        ...

    # --- Scheduler (Cron / Planned задачи) ---

    @abstractmethod
    async def list_schedules(self) -> list[dict[str, Any]]:
        """Список запланированных задач планировщика Agent Zero."""
        ...

    @abstractmethod
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
        """Создаёт задачу планировщика.

        task_type:
          "scheduled" — повторяемая по crontab-строке ``schedule`` ("m h dom mon dow").
          "adhoc"     — разовая, запускается вручную через run_schedule.
        Возвращает созданную задачу (как минимум с её id/uuid).
        """
        ...

    @abstractmethod
    async def run_schedule(self, task_id: str) -> dict[str, Any]:
        """Запускает задачу планировщика немедленно, не дожидаясь расписания."""
        ...

    @abstractmethod
    async def delete_schedule(self, task_id: str) -> dict[str, Any]:
        """Удаляет задачу планировщика."""
        ...
