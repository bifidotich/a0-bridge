"""Фабрика адаптеров. Выбирает реализацию по A0_VERSION из конфига."""

from __future__ import annotations

from ..client import A0Client
from ..config import settings
from .base import BaseAdapter
from .v1 import V1Adapter
from .v2 import V2Adapter

__all__ = ["BaseAdapter", "V1Adapter", "V2Adapter", "make_adapter"]


def make_adapter(client: A0Client) -> BaseAdapter:
    """Возвращает адаптер под версию Agent Zero, заданную в settings.a0_version."""
    if settings.a0_version == "v1":
        return V1Adapter(client)
    return V2Adapter(client)
