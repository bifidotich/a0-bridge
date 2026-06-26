"""Адаптер для Agent Zero v2.0+.

v2.0 в основном сохраняет совместимость external API с v1.x, но добавляет:
  - project-scoped MCP конфигурацию
  - project-scoped LLM presets
  - inspectable parallel tool calls
  - Responses API transport под капотом (нам не виден напрямую)

Базовые endpoints (/api_message, /message_async, /api_log_get, /api_reset_chat,
/api_terminate_chat) и scheduler совпадают с v1.x. Здесь наследуемся от V1Adapter и
переопределяем только то, что реально различается (health, projects, presets).
"""

from __future__ import annotations

from typing import Any

from .v1 import V1Adapter


class V2Adapter(V1Adapter):
    version_label = "v2"

    async def health(self) -> dict[str, Any]:
        # В v2 ожидается более явный health endpoint; пробуем его, иначе fallback на v1.
        try:
            resp = await self.client.get("/api/health")
            return {"alive": True, "version_family": "v2", "details": resp}
        except Exception:
            return await super().health() | {"version_family": "v2"}

    async def list_projects(self) -> list[dict[str, Any]]:
        # В v2 проекты first-class, ожидаем стабильный endpoint.
        for path in ("/api/projects", "/api/projects_list"):
            try:
                resp = await self.client.get(path)
                items = resp.get("projects") or resp.get("items") or []
                if items:
                    return list(items)
            except Exception:
                continue
        return []

    async def list_presets(self) -> list[dict[str, Any]]:
        # В v2 project-scoped presets; сначала пытаемся новый путь.
        for path in ("/api/model_presets", "/api/presets_list"):
            try:
                resp = await self.client.get(path)
                items = resp.get("presets") or resp.get("items") or []
                if items:
                    return list(items)
            except Exception:
                continue
        return []
