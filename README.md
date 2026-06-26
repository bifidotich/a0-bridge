# a0-mcp

MCP-сервер (streamable HTTP), проксирующий external API Agent Zero в набор MCP-инструментов. Claude Code делегирует задачи Agent Zero как субагенту; A0 исполняет их в изолированной Linux-среде и рекурсивно спавнит свои subordinate-агенты.

```
Claude Code ──tool_use──▶ a0-mcp (FastMCP/HTTP) ──REST /api/api_* + X-API-KEY──▶ Agent Zero
```

API Agent Zero **синхронный**: `a0_run` отправляет сообщение и возвращает финальный ответ субагента в том же вызове. Параллельный fan-out — это несколько `a0_run` в одном ответе Claude Code; каждый блокируется до своего ответа независимо.

## Agent Zero API

Мост использует external API из WebUI (Settings → External API), аутентификация по `X-API-KEY` (он же `mcp_server_token`). Пути — под префиксом `/api/` с двойным `api`:

| Endpoint | Назначение |
|----------|------------|
| `POST /api/api_message` | сообщение → `{context_id, response}` (синхронно) |
| `GET/POST /api/api_log_get` | лог чата → `{log: {items, progress, ...}}` |
| `POST /api/api_reset_chat` | сброс истории |
| `POST /api/api_terminate_chat` | удалить чат |
| `POST /api/api_files_get` | файлы по путям (base64) |

> **Порт.** External API может отдаваться НЕ на порту WebUI. Возьмите точный адрес из примеров в Settings → External API и пропишите его в `A0_URL` (без `/api`). `scheduler`/`projects`/`presets` во external API недоступны (только эти 5 ручек) — поэтому соответствующих инструментов в мосте нет.

## Запуск

```bash
cp .env.example .env          # A0_URL (с правильным портом!), A0_API_KEY
docker compose up -d --build  # a0-mcp :8765/mcp
```

Подключение к Claude Code:

```bash
claude mcp add --transport http a0 http://localhost:8765/mcp \
  [--header "Authorization: Bearer $MCP_AUTH_TOKEN"]   # header нужен, если MCP_AUTH_TOKEN задан
```

## Инструменты

| Tool | Сигнатура → результат |
|------|------------------------|
| `a0_health` | `()` → связность + валидность ключа |
| `a0_run` | `(message, context_id?, project_name?, attachments?, lifetime_hours?, wait=True)` → `{context_id, response}` или `{job_id}` |
| `a0_result` | `(job_id)` → статус/результат фоновой задачи (`running`/`done`/`error`) |
| `a0_jobs` | `()` → список фоновых задач |
| `a0_log_tail` | `(context_id, length=20)` → последние N записей лога |
| `a0_reset` | `(context_id)` → сброс истории (context_id жив) |
| `a0_terminate` | `(context_id)` → удалить чат |
| `a0_files_get` | `(paths[])` → `{filename: base64}` |

- `context_id` из ответа `a0_run` передавайте в следующий `a0_run`, чтобы продолжить тот же чат.
- `project_name` изолирует workspace/memory и активируется только на первом сообщении (без `context_id`). Дефолт — `A0_DEFAULT_PROJECT`.
- `attachments` — файлы субагенту: список `{filename, base64}`.
- Параллелизм: несколько `a0_run` в одном ответе = несколько изолированных субагентов.

### Синхронно vs job-режим

`a0_run` синхронный по умолчанию (`wait=True`): держит вызов, пока субагент не ответит. Для долгих задач используйте `wait=False` — мост запускает задачу в фоне и сразу возвращает `job_id`, не держа MCP-соединение; результат забираете через `a0_result(job_id)` (или смотрите все задачи через `a0_jobs`). Фоновые задачи живут в памяти моста ~1 час после завершения.

## Конфигурация (`.env`)

`A0_URL`, `A0_API_KEY`, `A0_DEFAULT_PROJECT`, `A0_HTTP_TIMEOUT`, `A0_MESSAGE_TIMEOUT`, `A0_LIFETIME_HOURS`, `MCP_HOST`, `MCP_PORT`, `MCP_AUTH_TOKEN`, `MCP_DNS_REBINDING_PROTECTION`, `MCP_ALLOWED_HOSTS`, `MCP_ALLOWED_ORIGINS`, `LOG_LEVEL`. См. [.env.example](.env.example).

## Разработка и проверка

```bash
python -m venv .venv && . .venv/Scripts/activate   # POSIX: . .venv/bin/activate
pip install -e .
a0-mcp                                             # = python -m a0_mcp

python test_connection.py [--full] [--url URL] [--token T]
```

`test_connection.py` подключается как MCP-клиент, сверяет набор инструментов и пингует `a0_health`. `--full` гоняет реальный `a0_run → a0_log_tail → a0_terminate`. Exit `0` — мост в порядке.

## Архитектура

```
src/a0_mcp/
├── config.py     pydantic-settings (.env)
├── client.py     async httpx, X-API-KEY, 5 методов external API + health
├── server.py     FastMCP: регистрация tools, transport security, Bearer-middleware
└── __main__.py   entry point (streamable HTTP)
```

## Ограничения

- API синхронный, отдельной «async start» ручки наружу нет → **стриминг прогресса невозможен** (поллить нечего, `context_id` приходит только в финале). Долгие задачи ограничены `A0_MESSAGE_TIMEOUT`.
- `scheduler` (cron), `projects`/`presets` (листинг) во external API не представлены — этих инструментов нет. Планировщик/проекты управляются через WebUI A0.

## Лицензия

MIT
