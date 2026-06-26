"""Конфигурация через переменные окружения."""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Все настройки берутся из .env или переменных окружения."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Agent Zero
    a0_url: str = Field(..., description="URL Agent Zero (например http://agent-zero:80)")
    a0_api_key: str = Field(..., description="API ключ из Agent Zero Settings")
    a0_version: Literal["v1", "v2"] = Field(
        default="v2",
        description="Семейство версий Agent Zero: v1 для 1.2-1.20, v2 для 2.0+",
    )
    a0_default_project: str = Field(default="", description="Дефолтный project_id")
    a0_http_timeout: float = Field(default=30.0, description="HTTP timeout, сек")
    a0_wait_timeout: float = Field(default=600.0, description="Макс. ожидание задачи, сек")
    a0_wait_poll_interval: float = Field(default=2.0, description="Интервал polling логов, сек")

    # MCP server
    mcp_host: str = Field(default="0.0.0.0")
    mcp_port: int = Field(default=8765)
    mcp_auth_token: str = Field(
        default="",
        description="Опциональный Bearer token для входящих запросов от Claude Code",
    )
    # DNS-rebinding защита MCP-транспорта. По умолчанию выключена: сервер headless,
    # доступ ограничивается MCP_AUTH_TOKEN, а защита иначе режет доступ по LAN-адресу (421).
    mcp_dns_rebinding_protection: bool = Field(default=False)
    mcp_allowed_hosts: str = Field(
        default="",
        description="CSV разрешённых Host (вкл. порт), если protection включён. '*' — любой.",
    )
    mcp_allowed_origins: str = Field(
        default="",
        description="CSV разрешённых Origin, если protection включён.",
    )

    log_level: str = Field(default="INFO")


# Singleton — загружается один раз
settings = Settings()  # type: ignore[call-arg]
