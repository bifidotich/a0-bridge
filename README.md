# a0-mcp

MCP-сервер, оборачивающий Agent Zero так, чтобы Claude Code мог делегировать ему задачи как субагенту.

## Зачем

Claude Code — отличный планировщик и исследователь, но дорого тратит контекст на грязную работу: рутинные скрипты, поиск по большим репозиториям, прогон тестов, парсинг страниц. Agent Zero делает это дёшево и в собственной изолированной Linux-среде. Этот пакет связывает их через MCP, и Claude Code видит Agent Zero как набор обычных инструментов.

```
Claude Code  ──tool_use──▶  a0-mcp (HTTP/MCP)  ──REST──▶  Agent Zero (Docker)
                                                                │
                                                                ▼
                                                  subordinate agents inside A0
```

Получается двухуровневая иерархия: Claude Code порождает субагентов A0 параллельными `tool_use`, а сам A0 внутри ещё спавнит свои subordinate-агенты.

## Поддерживаемые версии Agent Zero

| Версия | Docker image | A0_VERSION |
|--------|--------------|------------|
| v1.2 — v1.20 | `frdel/agent-zero:latest` | `v1` |
| v2.0+ | `agent0ai/agent-zero:latest` | `v2` |

Версия задаётся явно в `.env` — мы не делаем автоопределение, чтобы избежать ложных переключений.

## Быстрый старт

### 1. Получить API ключ Agent Zero

В Web UI Agent Zero: `Settings → External Services → API Keys` → создать ключ.

### 2. Скопировать `.env`

```bash
cp .env.example .env
# отредактировать: A0_URL, A0_API_KEY, A0_VERSION
```

### 3. Запустить через docker-compose

```bash
docker compose up -d --build
```

Поднимутся два контейнера:
- `agent-zero` на `http://localhost:50080` (Web UI)
- `a0-mcp` на `http://localhost:8765/mcp` (MCP endpoint)

### 4. Подключить к Claude Code

```bash
claude mcp add --transport http a0 http://localhost:8765/mcp
```

Если включена авторизация (`MCP_AUTH_TOKEN` задан):

```bash
claude mcp add --transport http a0 http://localhost:8765/mcp \
  --header "Authorization: Bearer your-token"
```

Проверка:

```bash
claude mcp list
```

Внутри Claude Code:

```
/mcp
```

— должно показать `a0` как `connected` с набором инструментов.

## Доступные инструменты

| Инструмент | Что делает |
|-----------|------------|
| `a0_health` | Проверить связь с Agent Zero |
| `a0_run` | Запустить задачу, вернуть `context_id` мгновенно (не ждёт) |
| `a0_wait` | Дождаться завершения задачи и получить ответ (стримит прогресс) |
| `a0_status` | Узнать статус без блокировки |
| `a0_log_tail` | Последние N строк лога (для отладки) |
| `a0_projects` | Список проектов A0 |
| `a0_presets` | Список model presets |
| `a0_schedule` | Cron/разовые задачи планировщика (create/list/run/delete) |
| `a0_reset` | Сбросить историю чата |
| `a0_terminate` | Остановить и удалить чат |

### Стриминг прогресса

`a0_wait` не просто блокируется до конца — пока субагент работает, он пушит в Claude Code
progress-notifications (доля прошедшего времени) и каждую новую запись лога субагента как
log-сообщение. В UI Claude Code это видно как живой прогресс выполнения, а не «висящий» вызов.

### Планировщик (`a0_schedule`)

```
a0_schedule(action="create", name="nightly-tests",
            prompt="прогони pytest и пришли отчёт", schedule="0 3 * * *")
a0_schedule(action="list")
a0_schedule(action="run", task_id="...")     # запустить немедленно
a0_schedule(action="delete", task_id="...")
```

`schedule` — обычный crontab из 5 полей `<m> <h> <dom> <mon> <dow>`. Для разовой задачи,
запускаемой вручную, используйте `task_type="adhoc"` без `schedule`.

## Проверка после запуска

```bash
python test_connection.py           # пингует все инструменты (read-only)
python test_connection.py --full    # + реальный a0_run -> a0_wait
python test_connection.py --url http://localhost:8765/mcp --token <bearer>
```

Скрипт подключается к серверу как настоящий MCP-клиент, сверяет список инструментов с
ожидаемым и дёргает безопасные вызовы. Код выхода `0` — мост в порядке.

## Параллельная субагентность

Главный сценарий — Claude Code в одном ответе делает несколько `a0_run` и затем собирает результаты:

```
1. a0_run("проанализируй package.json и список deps")   → ctx_A
2. a0_run("прогони pytest, верни количество failed")    → ctx_B
3. a0_run("найди все TODO в src/, верни путь:строка")   → ctx_C
4. a0_wait(ctx_A); a0_wait(ctx_B); a0_wait(ctx_C)       → агрегация
```

Каждый `context_id` — это изолированный субагент со своей памятью.

## Проекты и presets

```
a0_run(prompt="...", project_id="frontend-v3", preset="fast")
```

`project_id` изолирует workspace/memory/secrets, `preset` переключает model setup (быстрый/дешёвый/локальный). Если не передавать — будут default'ы.

## Локальная разработка

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env  # выставить A0_URL и A0_API_KEY
a0-mcp
```

## Архитектура

```
src/a0_mcp/
├── config.py       — pydantic-settings, всё из .env
├── client.py       — async httpx обёртка, X-API-KEY auth
├── adapters/
│   ├── base.py     — абстрактный интерфейс
│   ├── v1.py       — Agent Zero v1.2-v1.20
│   └── v2.py       — Agent Zero v2.0+ (наследуется от v1)
├── server.py       — FastMCP, регистрация tools, polling логики
└── __main__.py     — entry point streamable HTTP
```

Адаптер изолирует знания о различиях версий. Добавить поддержку v3.x в будущем — это новый файл в `adapters/`.

## Известные ограничения MVP

- Прогресс собирается polling'ом лога A0 и ретранслируется в Claude Code как progress/log
  notifications. Это не нативный SSE-поток от A0 — гранулярность ограничена `A0_WAIT_POLL_INTERVAL`.
- `a0_schedule`, `list_projects`, `list_presets` пробуют несколько вариантов endpoint'ов,
  потому что их имена менялись между минорными релизами Agent Zero. При пустом результате
  используйте `project_id` напрямую как строку из WebUI, а задачи планировщика — через A0 WebUI.

## Лицензия

MIT
