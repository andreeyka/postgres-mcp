
# Postgres MCP Pro (форк FastMCP)

## Обзор

**Postgres MCP Pro** — MCP-сервер (Model Context Protocol) с открытым исходным кодом на базе [FastMCP](https://gofastmcp.com/), который помогает вам и ИИ-агентам на всех этапах: от написания кода до тестирования, развёртывания и эксплуатации в production.

Этот форк оригинального проекта [postgres-fastmcp](https://github.com/crystaldba/postgres-fastmcp) переписан на FastMCP и даёт:

- **🚀 Высокая производительность** — FastMCP оптимизирован для быстрой работы
- **🔧 Гибкая настройка** — `config.json`, переменные окружения (например `MCP_SERVER_*`, `MCP_DATABASE_*`) или CLI
- **🌐 HTTP и STDIO** — запуск как HTTP-сервер или через stdio для настольных MCP-клиентов
- **🔐 Детальный контроль доступа** — access_mode (basic/full) и write_mode (true/false)
- **📌 Одна БД на сервер** — один экземпляр MCP-сервера обслуживает одну базу PostgreSQL

### Основные возможности

- **🔍 Здоровье БД** — анализ индексов, загрузки соединений, буферного кэша, vacuum, лимитов последовательностей, лага репликации и др.
- **⚡ Подбор индексов** — перебор тысяч вариантов индексов для вашей нагрузки с использованием промышленных алгоритмов
- **📈 Планы запросов** — проверка и оптимизация по планам EXPLAIN и симуляция гипотетических индексов
- **🧠 Понимание схемы** — контекстная генерация SQL на основе детального знания схемы БД
- **🛡️ Безопасное выполнение SQL** — настраиваемый контроль доступа, режим только чтение и разбор SQL, пригодно для разработки и production

## Быстрый старт

### Требования

Перед началом нужны:

1. Учётные данные для подключения к PostgreSQL
2. Python 3.12 или выше
3. Менеджер зависимостей `uv` (рекомендуется)

### Запуск сервера

Один экземпляр MCP-сервера обслуживает **одну базу данных**. Запуск возможен через CLI, конфигурационный файл или переменные окружения.

#### 1. CLI (одна база)

**Режим HTTP:**

```bash
uv run postgres-fastmcp \
  --database-uri "postgresql://user:password@localhost:5432/dbname" \
  --transport http \
  --port 8000 \
  --access-mode full
```

**Режим STDIO (для MCP-клиентов вроде Claude Desktop):**

```bash
uv run postgres-fastmcp \
  --database-uri "postgresql://user:password@localhost:5432/dbname" \
  --transport stdio \
  --access-mode basic
```

Опции CLI только при использовании `--database-uri`: `--write-mode` (флаг), `--access-mode` (basic|full). Опции сервера: `--host`, `--port`, `--workers`, `--transport`.

#### 2. Конфигурационный файл (`config.json`)

Создайте `config.json` в текущей директории с секциями `server`, `fastmcp` и `database` (одна база на сервер):

```json
{
    "server": {
        "host": "0.0.0.0",
        "port": 8000,
        "transport": "http",
        "endpoint": "mcp",
        "workers": 1
    },
    "fastmcp": {
        "server_name": "postgres-fastmcp"
    },
    "database": {
        "host": "localhost",
        "port": 5432,
        "user": "user",
        "password": "password",
        "name": "dbname",
        "access_mode": "full",
        "write_mode": false
    }
}
```

Затем выполните:

```bash
uv run postgres-fastmcp
```

Подключение к БД задаётся полями (`host`, `port`, `user`, `password`, `name`). Опционально: `access_mode`, `write_mode`, `table_prefix`, `sslmode`, `client_encoding`, `pool_min_size`, `pool_max_size`, `safe_sql_timeout`, `query_tag`.

Опционально в `server`: `response_max_tokens` — предел ответа инструмента в токенах (по умолчанию `20000`, минимум `1000`). Ответ больше предела заменяется ошибкой с просьбой уточнить запрос.

#### 3. Переменные окружения

Используйте префиксы `MCP_SERVER_*`, `MCP_DATABASE_*` и `MCP_FASTMCP_*` (см. [env.example](env.example)):

```bash
export MCP_SERVER_HOST=0.0.0.0
export MCP_SERVER_PORT=8000
export MCP_SERVER_TRANSPORT=http
export MCP_DATABASE_HOST=localhost
export MCP_DATABASE_PORT=5432
export MCP_DATABASE_USER=user
export MCP_DATABASE_PASSWORD=password
export MCP_DATABASE_NAME=dbname
export MCP_DATABASE_ROLE=admin
export MCP_DATABASE_WRITE_MODE=false
export MCP_RESPONSE_MAX_TOKENS=20000

uv run postgres-fastmcp
```

#### 4. Приоритет конфигурации

Порядок (от высшего к низшему):

1. Параметры CLI (при указании `--database-uri` настройки БД берутся из CLI и переопределяют остальное)
2. Файл `config.json` в текущей директории
3. Переменные окружения и `.env`
4. Значения по умолчанию

## Конфигурация

### Контроль доступа

Безопасность задаётся двумя независимыми параметрами:

#### Уровень доступа (`access_mode`)

Определяет доступ к схемам и набор доступных инструментов:

| Роль    | Схемы          | Инструменты | Описание |
| ------- | -------------- | ----------- | -------- |
| `user`  | Только `public` | Базовые (4) | Только схема public; опционально `table_prefix` — ограничение по префиксу имён таблиц |
| `admin` | Все схемы      | Все (9)     | Все схемы и расширенные инструменты (схемы, здоровье, топ запросов, анализ индексов) |

#### Режим записи (`write_mode`)

Определяет уровень выполнения SQL:

| write_mode | SQL-доступ                        | Описание |
| ---------- | --------------------------------- | -------- |
| `false`    | Только чтение (только SELECT)     | Разрешён только SELECT |
| `true`     | Чтение-запись (DML); DDL при access_mode=full | Разрешены INSERT/UPDATE/DELETE; DDL только при access_mode=full |

#### Матрица комбинаций

| access_mode | write_mode | Инструменты | SQL-доступ        | Схемы |
| -------- | ---------- | ----------- | ----------------- | ----- |
| `basic`  | `false`    | Базовые (4) | Только чтение     | `public` (опционально `table_prefix`) |
| `basic`  | `true`     | Базовые (4) | Чтение-запись    | `public` |
| `full`   | `false`    | Все (9)     | Только чтение     | Все |
| `full`   | `true`     | Все (9)     | Полный доступ (DDL) | Все |

**По умолчанию:** `access_mode=basic`, `write_mode=false` (максимально ограниченный режим).

**Для access_mode=basic опционально:** `table_prefix` ограничивает видимые таблицы/представления/последовательности по префиксу имени; для full игнорируется.

### Транспорты

Поддерживаются транспорты **http** и **stdio**.

#### HTTP

Запуск как HTTP-приложение для Cursor, веб-клиентов или production:

```bash
uv run postgres-fastmcp \
  --database-uri "postgresql://user:password@localhost:5432/dbname" \
  --transport http \
  --port 8000
```

Сервер доступен по адресу `http://localhost:8000/mcp` (или по пути из `server.endpoint`). Проверка здоровья по `/health` при `MCP_SERVER_HEALTH_ENDPOINT_ENABLED=true` (по умолчанию включено).

#### STDIO

Для настольных MCP-клиентов (например Claude Desktop) или интеграции через процесс:

```bash
uv run postgres-fastmcp \
  --database-uri "postgresql://user:password@localhost:5432/dbname" \
  --transport stdio
```

### Справочник конфигурации

- **CLI:** `--database-uri`, `--transport`, `--host`, `--port`, `--workers`, `--write-mode`, `--access-mode`. Вывод версии: `--version`. При указании `--database-uri` подключение к БД и access_mode/write_mode берутся из CLI (и переопределяют config/env на этот запуск).
- **config.json:** Должен содержать `server`, `fastmcp` и `database` (см. Быстрый старт). Загружается из текущей директории.
- **Переменные окружения / .env:** Префиксы `MCP_SERVER_*`, `MCP_DATABASE_*`, `MCP_FASTMCP_*` (см. [env.example](env.example)).

### Подключение MCP-клиентов

#### Cursor (HTTP)

В файле `~/.cursor/mcp.json`:

```json
{
    "mcpServers": {
        "postgres": {
            "type": "sse",
            "url": "http://localhost:8000/mcp"
        }
    }
}
```

#### Claude Desktop (STDIO)

В файле `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) или `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
    "mcpServers": {
        "postgres": {
            "command": "uv",
            "args": ["run", "postgres-fastmcp", "--transport", "stdio", "--database-uri", "postgresql://user:pass@localhost:5432/dbname"]
        }
    }
}
```

Либо задать подключение переменными окружения вместо `--database-uri`:

```json
{
    "mcpServers": {
        "postgres": {
            "command": "uv",
            "args": ["run", "postgres-fastmcp", "--transport", "stdio"],
            "env": {
                "MCP_DATABASE_HOST": "localhost",
                "MCP_DATABASE_PORT": "5432",
                "MCP_DATABASE_USER": "user",
                "MCP_DATABASE_PASSWORD": "pass",
                "MCP_DATABASE_NAME": "dbname",
                "MCP_DATABASE_ROLE": "user",
                "MCP_DATABASE_WRITE_MODE": "false"
            }
        }
    }
}
```

## Технические детали

### FastMCP

Форк построен на [FastMCP](https://gofastmcp.com/), который обеспечивает:

- Асинхронное выполнение и высокую производительность
- Транспорты HTTP и stdio
- Управление жизненным циклом и внедрение зависимостей для инструментов

### Управление жизненным циклом

Сервер использует механизм lifespan FastMCP для подключений к БД:

- Создание пула соединений при старте
- Корректное закрытие соединений при остановке
- Обработка сигналов (SIGINT, SIGTERM)

### Безопасное выполнение SQL

В проекте используется многоуровневая защита при выполнении SQL:

1. **Разбор SQL** — библиотека `pglast` анализирует SQL перед выполнением; разрешён только allowlist типов операторов, узлов AST и функций
2. **Транзакции только для чтения** — в режимах только чтение используются read-only транзакции PostgreSQL
3. **Проверки COMMIT/ROLLBACK** — блокируются попытки обойти режим только чтение
4. **Таймауты** — `safe_sql_timeout` выставляется как `statement_timeout` внутри транзакции, запрос отменяет сам PostgreSQL. Таймаут самого тула выводится так, чтобы быть длиннее `statement_timeout` с клиентской страховкой, поэтому первым срабатывает именно PostgreSQL
5. **Расширения** — `CREATE EXTENSION` допускается только для `hypopg` и `pg_stat_statements` и только при `write_mode=true`

## MCP API

Функциональность сервера доступна через [инструменты MCP](https://modelcontextprotocol.io/docs/concepts/tools).

### Доступные инструменты

| Инструмент              | Описание |
| ----------------------- | -------- |
| `list_schemas`          | Список всех схем БД в экземпляре PostgreSQL |
| `list_objects`          | Список объектов БД (таблицы, представления, последовательности, расширения) в указанной схеме |
| `get_object_details`   | Информация об объекте БД: столбцы, ограничения, индексы таблицы и т.п. |
| `execute_sql`           | Выполнение SQL с ограничениями только чтение при write_mode=false |
| `explain_query`         | План выполнения запроса; поддерживаются гипотетические индексы для симуляции |
| `get_top_queries`      | Самые медленные или ресурсоёмкие запросы из `pg_stat_statements`, не больше `limit` (до 100) |
| `analyze_workload_indexes` | Анализ нагрузки и рекомендации оптимальных индексов |
| `analyze_query_indexes`    | Анализ списка запросов (до 10) и рекомендации индексов |
| `analyze_db_health`    | Проверка здоровья БД: буферный кэш, соединения, ограничения, индексы (дубликаты/неиспользуемые/невалидные), последовательности, vacuum |

### Формат ответа и бюджет

- `execute_sql`, `list_schemas`, `list_objects`, `get_object_details` и `get_top_queries` принимают `output`: `table` (по умолчанию) — Markdown-таблица, в которой колонки перечислены один раз, и строка `N rows.`; `json` — `{"rows": [...], "row_count": N}` в `structuredContent` и тот же JSON текстом. `get_object_details` в `json` отдаёт поля объекта и разделы (`columns`, `constraints`, `indexes`) одним объектом.
- Ответ инструмента больше `response_max_tokens` (переменная `MCP_RESPONSE_MAX_TOKENS`, по умолчанию 20000) заменяется ошибкой `Response is too large ... Refine the request`: агенту нужно добавить `WHERE`/`LIMIT`, выбрать меньше колонок или агрегировать. Размер оценивается как байты текста / 3. Если инструмент мог записать данные (аннотация `readOnlyHint=false`, то есть `execute_sql` при `write_mode=true`), ошибка другая: `... The statement was executed and its changes are applied — do not re-run it`. Изменения уже применены, повтор записал бы их дважды; агенту нужно проверить результат узким `SELECT`.
- Ввод нормализуется: `object_type` понимает `Tables`, `VIEW`, `sequences`; `health_type` — список или строку через запятую в любом регистре; `sort_by` — синонимы `total`, `mean`, `avg`, `resource`; `limit` больше 100 урезается до 100 и действует для всех `sort_by`, включая `resources`. Неверное значение даёт ошибку, в которой всегда перечислены допустимые значения; если есть похожее, добавляется подсказка `Did you mean ...?`.

### Ограничения по доступу

- **Роль `user`**: только базовые инструменты (`list_objects`, `get_object_details`, `explain_query`, `execute_sql`); опционально `table_prefix` для ограничения набора таблиц
- **Роль `admin`**: все инструменты (базовые + `list_schemas`, `analyze_workload_indexes`, `analyze_query_indexes`, `analyze_db_health`, `get_top_queries`)
- **write_mode=false**: разрешён только SELECT
- **write_mode=true** при access_mode=basic: в схеме `public` разрешены INSERT/UPDATE/DELETE (изменения коммитятся), DDL отклоняется
- **write_mode=true** при access_mode=full: без ограничений, включая DDL

## Установка расширений PostgreSQL (опционально)

Для подбора индексов и полного анализа производительности в БД нужно установить расширения `pg_stat_statements` и `hypopg`.

### Установка расширений

```sql
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS hypopg;
```

**Важно:** для `pg_stat_statements` необходимо добавить его в `shared_preload_libraries` в конфигурации PostgreSQL и перезапустить сервер.

### Облачные провайдеры

Если PostgreSQL работает в управляемом сервисе (AWS RDS, Azure SQL, Google Cloud SQL), расширения `pg_stat_statements` и `hypopg` обычно уже доступны. Достаточно выполнить `CREATE EXTENSION` от имени роли с достаточными правами.

### Собственный экземпляр Postgres

При самостоятельном управлении Postgres может потребоваться:

- Перед загрузкой `pg_stat_statements` — указать его в `shared_preload_libraries` в конфиге Postgres
- Расширение `hypopg` может потребовать установки на уровне системы (например, через пакетный менеджер), так как не всегда поставляется с Postgres

## Примеры конфигурации

### Пример 1: Production (только чтение, access_mode=full)

`config.json` для одной production-БД с полным набором инструментов и SQL только на чтение:

```json
{
    "server": { "host": "0.0.0.0", "port": 8000, "transport": "http" },
    "fastmcp": { "server_name": "postgres-fastmcp" },
    "database": {
        "host": "prod-server",
        "port": 5432,
        "user": "user",
        "password": "password",
        "name": "production",
        "access_mode": "full",
        "write_mode": false
    }
}
```

### Пример 2: Роль user с префиксом таблиц

Ограничение схемой `public` и объектами, имена которых начинаются с `app_`:

```json
{
    "server": { "transport": "http", "port": 8000 },
    "database": {
        "host": "localhost",
        "port": 5432,
        "user": "appuser",
        "password": "secret",
        "name": "mydb",
        "access_mode": "basic",
        "write_mode": false,
        "table_prefix": "app_"
    }
}
```

### Пример 3: STDIO для Claude Desktop

Запуск с одной БД через CLI:

```bash
uv run postgres-fastmcp --transport stdio --database-uri "postgresql://user:pass@localhost:5432/dbname" --access-mode basic
```

### Пример 4: Разработка (чтение-запись, admin)

Полный набор инструментов и разрешён DML/DDL:

```json
{
    "server": { "host": "127.0.0.1", "port": 8000, "transport": "http" },
    "database": {
        "host": "localhost",
        "port": 5432,
        "user": "dev",
        "password": "dev",
        "name": "development",
        "access_mode": "full",
        "write_mode": true
    }
}
```

## Примеры запросов к агенту

### Проверка здоровья БД

Спросите у ИИ-агента:
> Проверь здоровье моей базы данных и укажи возможные проблемы.

### Анализ медленных запросов

> Какие запросы в моей БД самые медленные и как их ускорить?

### Рекомендации по производительности

> Приложение тормозит. Как его ускорить?

### Рекомендации по индексам

> Проанализируй нагрузку на БД и предложи индексы для улучшения производительности.

### Оптимизация конкретного запроса

> Помоги оптимизировать запрос: SELECT * FROM orders JOIN customers ON orders.customer_id = customers.id WHERE orders.created_at > '2023-01-01';

## Использование как библиотека

Пакет экспортирует `create_server`, чтобы его можно было встроить в другой Python-проект как библиотеку. Публичное API:

```python
from postgres_fastmcp import (
    create_server,
    Settings,
    LocalProvider,
    FileSystemProvider,
    Middleware,
)

settings = Settings()
# При необходимости переопределите поля:
# settings.database = settings.database.model_copy(update={"host": "db", "user": "u", "password": "p", "name": "x"})

server = create_server(
    settings,
    # auth: передайте любой экземпляр AuthProvider из FastMCP (BearerAuth, JWT, OAuth, собственный наследник).
    auth=None,
    # extra_providers: расширьте сервер собственными tools/resources/prompts.
    extra_providers=[],
    # extra_middleware: добавляется после встроенных TimingMiddleware и LoggingMiddleware.
    extra_middleware=[],
)

server.run(transport="http", host="0.0.0.0", port=8000)
```

Замечания:
- `auth=None` означает отсутствие аутентификации. Это безопасно для транспорта `stdio` или доверенного localhost. Для сетевого HTTP-развёртывания подключите `AuthProvider` из `fastmcp.server.auth` или собственный.
- `extra_providers` смешиваются со встроенными источниками инструментов. Имена инструментов не должны конфликтовать со встроенными (`execute_sql`, `list_objects`, `get_object_details`, `explain_query`, `list_schemas`, `analyze_db_health`, `get_top_queries`, `analyze_query_indexes`, `analyze_workload_indexes`) — установлен режим `on_duplicate="error"`, поэтому коллизии приведут к ошибке на старте.
- `extra_middleware` выполняется после нашего timing/logging middleware, поэтому замеры времени запроса/ответа покрывают и ваши обработчики.

## Разработка

### Локальная настройка

1. **Установка uv**:

   ```bash
   curl -sSL https://astral.sh/uv/install.sh | sh
   ```

2. **Клонирование репозитория**:

   ```bash
   git clone https://github.com/your-username/postgres-fastmcp.git
   cd postgres-fastmcp
   ```

3. **Установка зависимостей**:

   ```bash
   uv sync
   ```

4. **Установка пакета в режиме разработки**:

   ```bash
   uv pip install -e .
   ```

### Запуск тестов

**Требования:**

- Установлен и запущен Docker
- Образы Docker собираются при первом запуске тестов или их можно подготовить вручную:

```bash
# Подготовка образов для тестов (опционально, ускоряет последующие запуски)
uv run python tests/prepare_docker_images.py
```

**Запуск всех тестов:**

```bash
uv run python -m pytest
```

**Запуск конкретного файла тестов:**

```bash
uv run python -m pytest tests/unit/domains/index_tuning -v
```

**Интеграционные тесты с реальной БД:**

```bash
uv run python -m pytest tests/integration/ -v
```

### Форматирование кода

В проекте используется `ruff` для форматирования и линтинга:

```bash
uv run ruff format .
uv run ruff check .
```

### Проверка типов

Проверка типов с помощью `mypy`:

```bash
uv run mypy src/
```

## Отличия от оригинального проекта

Этот форк отличается от [postgres-fastmcp](https://github.com/crystaldba/postgres-fastmcp) следующими изменениями:

| Оригинальный проект     | Этот форк |
| ----------------------- | --------- |
| Стандартная реализация MCP | Фреймворк FastMCP |
| Режимы                        | access_mode (`basic` / `full`) + write_mode (true/false)                      |
| Только транспорт SSE   | HTTP и stdio |
| Настройка через CLI/env | config.json, env (`MCP_SERVER_*`, `MCP_DATABASE_*`, `MCP_FASTMCP_*`) и CLI |
| —                      | Опциональный `table_prefix` для роли `user`; endpoint здоровья `/health` |

## Технические заметки

### Подбор индексов

Реализация подбора индексов следует подходу оригинального проекта и использует [Anytime Algorithm of Database Tuning Advisor for Microsoft SQL Server](https://www.microsoft.com/en-us/research/wp-content/uploads/2020/06/Anytime-Algorithm-of-Database-Tuning-Advisor-for-Microsoft-SQL-Server.pdf).

### Здоровье БД

Проверки здоровья БД адаптированы из [PgHero](https://github.com/ankane/pghero) и включают:

- Здоровье индексов (неиспользуемые, дубликаты, раздутые)
- Доля попаданий буферного кэша
- Здоровье соединений
- Vacuum (предотвращение wraparound transaction ID)
- Репликация
- Ограничения
- Последовательности

### Клиентская библиотека Postgres

В проекте используется [psycopg3](https://www.psycopg.org/) для асинхронного I/O при подключении к Postgres с доступом к полному набору возможностей Postgres.

### Защищённое выполнение SQL

Реализована многоуровневая защита при выполнении SQL:

- Разбор SQL через `pglast` для выявления и отклонения небезопасных операторов
- Транзакции только для чтения в ограниченных режимах
- `statement_timeout` на стороне PostgreSQL в ограниченных режимах
- Ограничения по схемам для режима `basic`
- `CREATE EXTENSION` только для `hypopg` и `pg_stat_statements` в режиме записи

## Лицензия

MIT License

## Благодарности

Проект является форком [postgres-fastmcp](https://github.com/crystaldba/postgres-fastmcp) от [Crystal DBA](https://www.crystaldba.ai), переписанным на [FastMCP](https://gofastmcp.com/).
