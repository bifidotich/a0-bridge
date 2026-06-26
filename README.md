# a0-mcp

MCP-сервер (streamable HTTP), проксирующий external REST API Agent Zero в набор MCP-инструментов. Claude Code делегирует задачи Agent Zero как субагенту; A0 исполняет их в изолированной Linux-среде и рекурсивно спавнит свои subordinate-агенты.

```
Claude Code ──tool_use──▶ a0-mcp (FastMCP/HTTP) ──REST/X-API-KEY──▶ Agent Zero
```

## Версии

| A0_VERSION | Версии A0 | Docker image       |
|------------|-----------|--------------------|
| `v1`       | 1.2–1.20  | `frdel/agent-zero` |
| `v2`       | 2.0+      | `agent0ai/agent-zero` |

Выбор версии явный (`A0_VERSION`), автоопределения нет. `V2Adapter` наследует `V1Adapter`, переопределяя только различающиеся endpoint'ы (health/projects/presets).

## Запуск

```bash
cp .env.example .env          # A0_URL, A0_API_KEY (Settings → External Services → API Keys), A0_VERSION
docker compose up -d --build  # agent-zero :50080 (WebUI), a0-mcp :8765/mcp
```

Подключение к Claude Code:

```bash
claude mcp add --transport http a0 http://localhost:8765/mcp \
  [--header "Authorization: Bearer $MCP_AUTH_TOKEN"]   # header нужен, если MCP_AUTH_TOKEN задан
```

## Инструменты

| Tool | Сигнатура → результат |
|------|------------------------|
| `a0_health` | `()` → версия/связность |
| `a0_run` | `(prompt, project_id?, preset?, context_id?)` → `context_id` (async, не блокирует) |
| `a0_wait` | `(context_id, timeout?)` → финальный ответ; стримит прогресс (см. ниже) |
| `a0_status` | `(context_id)` → `running`/`done` без блокировки |
| `a0_log_tail` | `(context_id, length=20)` → последние N записей лога |
| `a0_projects` / `a0_presets` | `()` → список проектов / model presets |
| `a0_schedule` | `(action, …)` → CRUD задач планировщика |
| `a0_reset` / `a0_terminate` | `(context_id)` → сброс истории / удаление контекста |

`project_id` изолирует workspace/memory/secrets, `preset` переключает model setup. Без них — дефолты (`A0_DEFAULT_PROJECT`).

### Параллелизм

Каждый `context_id` — изолированный субагент. Типовой паттерн: несколько `a0_run` в одном ответе → `a0_wait` по каждому → агрегация.

### Стриминг прогресса

`a0_wait` поллит `api_log_get` с интервалом `A0_WAIT_POLL_INTERVAL` и ретранслирует в Claude Code MCP-нотификации: `report_progress` (доля от `timeout`) + `info` на каждую новую запись лога субагента. Это не нативный SSE A0 — гранулярность ограничена интервалом поллинга.

### Планировщик

```python
a0_schedule(action="create", name="nightly", prompt="...", schedule="0 3 * * *")  # crontab: m h dom mon dow
a0_schedule(action="create", name="adhoc", prompt="...", task_type="adhoc")        # разовая, запуск вручную
a0_schedule(action="list")
a0_schedule(action="run",    task_id="...")
a0_schedule(action="delete", task_id="...")
```

## Конфигурация (`.env`)

`A0_URL`, `A0_API_KEY`, `A0_VERSION`, `A0_DEFAULT_PROJECT`, `A0_HTTP_TIMEOUT`, `A0_WAIT_TIMEOUT`, `A0_WAIT_POLL_INTERVAL`, `MCP_HOST`, `MCP_PORT`, `MCP_AUTH_TOKEN`, `LOG_LEVEL`. См. [.env.example](.env.example).

## Разработка и проверка

```bash
python -m venv .venv && . .venv/Scripts/activate   # POSIX: . .venv/bin/activate
pip install -e .
a0-mcp                                             # = python -m a0_mcp

python test_connection.py [--full] [--url URL] [--token T]
```

`test_connection.py` подключается как MCP-клиент, сверяет набор инструментов и пингует read-only вызовы (`--full` добавляет реальный `a0_run → a0_wait → a0_terminate`). Exit `0` — мост в порядке.

## Архитектура

```
src/a0_mcp/
├── config.py            pydantic-settings (.env)
├── client.py            async httpx, X-API-KEY, обработка ошибок → A0HTTPError
├── adapters/
│   ├── base.py          ABC: send_message/get_log/reset/terminate/projects/presets/scheduler
│   ├── v1.py            A0 1.2–1.20 + crontab-парсер + перебор scheduler-endpoint'ов
│   └── v2.py            A0 2.0+, override health/projects/presets
├── server.py            FastMCP: регистрация tools, poll-loop, стриминг через Context
└── __main__.py          entry point (streamable HTTP + опц. Bearer-middleware)
```

Поддержка новой мажорной версии = новый адаптер в `adapters/` + ветка в `make_adapter`.

## Ограничения

- Прогресс — поллинг, не SSE (гранулярность = `A0_WAIT_POLL_INTERVAL`).
- `a0_schedule`/`list_projects`/`list_presets` перебирают несколько вариантов путей (имена endpoint'ов A0 менялись между релизами); при пустом ответе оперируйте `project_id` как строкой и планировщиком через WebUI.

## Лицензия

MIT
