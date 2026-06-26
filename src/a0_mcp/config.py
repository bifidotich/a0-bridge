"""Конфигурация через переменные окружения."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Все настройки берутся из .env или переменных окружения."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Agent Zero external API ---
    # База БЕЗ /api — пути формируются как {a0_url}/api/api_message и т.д.
    # ВАЖНО: указывайте порт, на котором A0 реально отдаёт external API (см. Settings → External API
    # в WebUI: примеры там содержат готовый URL). Это может быть НЕ порт WebUI.
    a0_url: str = Field(..., description="База Agent Zero, напр. http://agent-zero:80")
    # X-API-KEY. В A0 это тот же mcp_server_token (Settings → External API / MCP).
    a0_api_key: str = Field(..., description="X-API-KEY из Agent Zero")
    a0_default_project: str = Field(
        default="", description="project_name по умолчанию (активируется на первом сообщении)"
    )
    a0_http_timeout: float = Field(default=30.0, description="HTTP timeout для быстрых вызовов, сек")
    # api_message синхронный: держит соединение, пока агент не ответит. Нужен большой таймаут.
    a0_message_timeout: float = Field(default=600.0, description="Таймаут api_message, сек")
    a0_lifetime_hours: float = Field(default=24.0, description="Дефолтный lifetime чата, ч")

    # --- MCP server ---
    mcp_host: str = Field(default="0.0.0.0")
    mcp_port: int = Field(default=8765)
    mcp_auth_token: str = Field(
        default="", description="Опциональный Bearer token для входящих запросов от Claude Code"
    )
    # DNS-rebinding защита MCP-транспорта. По умолчанию выключена: сервер headless,
    # доступ ограничивается MCP_AUTH_TOKEN, а защита иначе режет доступ по LAN-адресу (421).
    mcp_dns_rebinding_protection: bool = Field(default=False)
    mcp_allowed_hosts: str = Field(default="", description="CSV разрешённых Host, если protection on")
    mcp_allowed_origins: str = Field(default="", description="CSV разрешённых Origin, если protection on")

    log_level: str = Field(default="INFO")


# Singleton — загружается один раз
settings = Settings()  # type: ignore[call-arg]
