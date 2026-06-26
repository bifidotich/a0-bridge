#!/usr/bin/env python3
"""Smoke-тест запущенного a0-mcp сервера.

Подключается к серверу по streamable HTTP как обычный MCP-клиент (так же, как это
делает Claude Code), перечисляет инструменты и пингует каждый из них.

Запуск (сервер уже должен быть поднят, напр. `a0-mcp` или docker compose up):

    python test_connection.py
    python test_connection.py --url http://localhost:8765/mcp --token secret
    python test_connection.py --full          # включить реальный a0_run -> a0_wait

Без --full дёргаются только безопасные read-only инструменты (ничего не создаётся
в Agent Zero). С --full запускается короткая задача субагенту и ожидается ответ.

Код выхода: 0 — все ожидаемые инструменты присутствуют и read-only вызовы прошли;
1 — есть провалы. Ошибки самого Agent Zero (например, недоступен) выводятся, но
не считаются провалом транспорта MCP, если явно не указан --strict.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

# Windows-консоль часто в cp1251 и падает на '→'/UTF-8. Форсируем UTF-8 для вывода.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

try:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
except ImportError:  # pragma: no cover
    sys.exit("Не найден пакет mcp. Установите проект: pip install -e .")


# Инструменты, которые мы ожидаем увидеть на сервере.
EXPECTED_TOOLS = {
    "a0_health",
    "a0_run",
    "a0_log_tail",
    "a0_reset",
    "a0_terminate",
    "a0_files_get",
}

# Read-only вызовы: (имя, аргументы). Безопасны — ничего не создают в Agent Zero.
# a0_run создаёт чат, поэтому он только в --full. a0_log_tail требует context_id (тоже в --full).
READONLY_CALLS: list[tuple[str, dict[str, Any]]] = [
    ("a0_health", {}),
]


def _c(ok: bool) -> str:
    return "\033[32mOK\033[0m" if ok else "\033[31mFAIL\033[0m"


def _result_text(result: Any) -> str:
    """Достаём краткое текстовое представление результата call_tool."""
    parts = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    s = " ".join(parts) if parts else repr(getattr(result, "structuredContent", result))
    return s.replace("\n", " ")[:200]


async def run(url: str, token: str | None, full: bool, strict: bool) -> int:
    headers = {"Authorization": f"Bearer {token}"} if token else None
    failures = 0
    soft_failures = 0

    print(f"→ Подключаюсь к {url}")
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print(f"{_c(True)} initialize — соединение установлено\n")

            # 1. Список инструментов
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            print(f"Инструменты на сервере ({len(names)}): {', '.join(sorted(names))}\n")

            missing = EXPECTED_TOOLS - names
            if missing:
                failures += 1
                print(f"{_c(False)} отсутствуют ожидаемые инструменты: {', '.join(sorted(missing))}")
            else:
                print(f"{_c(True)} все {len(EXPECTED_TOOLS)} ожидаемых инструмента зарегистрированы\n")

            # 2. Read-only пинги
            print("Пингую read-only инструменты:")
            for name, args in READONLY_CALLS:
                if name not in names:
                    continue
                try:
                    result = await session.call_tool(name, args)
                    is_err = bool(getattr(result, "isError", False))
                    print(f"  {_c(not is_err)} {name}({args}) → {_result_text(result)}")
                    if is_err:
                        soft_failures += 1
                except Exception as e:  # noqa: BLE001
                    soft_failures += 1
                    print(f"  {_c(False)} {name}({args}) → исключение: {e}")

            # 3. Полный прогон субагента (опционально)
            if full and "a0_run" in names:
                print("\n--full: запускаю реальную задачу субагенту")
                rc = await _full_roundtrip(session)
                soft_failures += rc

    total_soft = soft_failures
    print("\n" + "=" * 60)
    if failures == 0 and total_soft == 0:
        print(f"{_c(True)} Всё зелёное.")
        return 0
    if failures == 0 and not strict:
        print(
            f"Транспорт MCP в порядке, но {total_soft} вызов(а) к Agent Zero вернули ошибку "
            "(вероятно A0 не запущен/не настроен). Это не провал самого моста."
        )
        return 0
    print(f"{_c(False)} Провалов: транспорт={failures}, вызовы A0={total_soft}")
    return 1


async def _full_roundtrip(session: ClientSession) -> int:
    """a0_run -> a0_log_tail -> a0_terminate. Возвращает кол-во soft-провалов."""
    try:
        run_res = await session.call_tool(
            "a0_run", {"message": "Reply with exactly one word: PONG"}
        )
        struct = getattr(run_res, "structuredContent", None) or {}
        ctx = struct.get("context_id")
        resp = struct.get("response")
        if not ctx:
            print(f"  {_c(False)} a0_run не вернул context_id: {_result_text(run_res)}")
            return 1
        print(f"  {_c(True)} a0_run → context_id={ctx}  response={resp!r}")

        log_res = await session.call_tool("a0_log_tail", {"context_id": ctx, "length": 5})
        print(f"  {_c(True)} a0_log_tail → {_result_text(log_res)}")

        await session.call_tool("a0_terminate", {"context_id": ctx})
        print(f"  {_c(True)} a0_terminate → чат удалён")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"  {_c(False)} full roundtrip упал: {e}")
        return 1


def main() -> None:
    p = argparse.ArgumentParser(description="Smoke-тест a0-mcp сервера")
    p.add_argument(
        "--url",
        default=os.environ.get("A0_MCP_URL", "http://localhost:8765/mcp"),
        help="URL MCP endpoint (default: http://localhost:8765/mcp)",
    )
    p.add_argument(
        "--token",
        default=os.environ.get("MCP_AUTH_TOKEN") or None,
        help="Bearer token, если на сервере включён MCP_AUTH_TOKEN",
    )
    p.add_argument("--full", action="store_true", help="Прогнать реальный a0_run -> a0_wait")
    p.add_argument(
        "--strict",
        action="store_true",
        help="Считать ошибки вызовов Agent Zero провалом (exit 1)",
    )
    args = p.parse_args()

    try:
        rc = asyncio.run(run(args.url, args.token, args.full, args.strict))
    except Exception as e:  # noqa: BLE001
        sys.exit(f"\n\033[31mНе удалось подключиться к {args.url}: {e}\033[0m")
    sys.exit(rc)


if __name__ == "__main__":
    main()
