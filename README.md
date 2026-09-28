
# Postgres MCP Pro (форк FastMCP)

## Обзор

**Postgres MCP Pro** — MCP-сервер (Model Context Protocol) с открытым исходным кодом на базе [FastMCP](https://gofastmcp.com/), который помогает вам и ИИ-агентам на всех этапах: от написания кода до тестирования, развёртывания и эксплуатации в production.

Этот форк оригинального проекта [postgres-fastmcp](https://github.com/crystaldba/postgres-fastmcp) переписан на FastMCP и даёт:

- **🚀 Высокая производительность** — FastMCP оптимизирован для быстрой работы
- **🔧 Гибкая настройка** — `config.json`, переменные окружения (`MCP_SERVER_*`, `MCP_DATABASE_*`, `MCP_AUTH_*`, `MCP_FASTMCP_*`) или CLI
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

Все опции CLI необязательны и действуют независимо от `--database-uri`: `--transport` (http|stdio), `--host`, `--port`, `--access-mode` (basic|full), `--write-mode` / `--no-write-mode`. Незаданная опция ничего не переопределяет: значение берётся из `config.json`, затем из переменных окружения, затем по умолчанию.

#### 2. Конфигурационный файл (`config.json`)

Создайте `config.json` в текущей директории с секциями `server`, `fastmcp` и `database` (одна база на сервер):

```json
{
    "server": {
        "host": "0.0.0.0",
        "port": 8000,
        "transport": "http",
        "endpoint": "/mcp"
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

Имя переменной — `MCP_<СЕКЦИЯ>_<ПОЛЕ>`, где секция совпадает с секцией `config.json`: `MCP_SERVER_*`, `MCP_DATABASE_*`, `MCP_AUTH_*`, `MCP_FASTMCP_*` (см. [env.example](env.example)). Переменные без префикса секции (`MCP_PORT`, `DATABASE` и т.п.) не читаются.

```bash
export MCP_SERVER_HOST=0.0.0.0
export MCP_SERVER_PORT=8000
export MCP_SERVER_TRANSPORT=http
export MCP_DATABASE_HOST=localhost
export MCP_DATABASE_PORT=5432
export MCP_DATABASE_USER=user
export MCP_DATABASE_PASSWORD=password
export MCP_DATABASE_NAME=dbname
export MCP_DATABASE_ACCESS_MODE=full
export MCP_DATABASE_WRITE_MODE=false
export MCP_SERVER_RESPONSE_MAX_TOKENS=20000

uv run postgres-fastmcp
```

#### 4. Приоритет конфигурации

Порядок (от высшего к низшему):

1. Явно заданные параметры CLI
2. Файл `config.json` в текущей директории; CLI переопределяет в нём отдельные поля, а не секцию целиком
3. Переменные окружения и `.env`
4. Значения по умолчанию

Особенности `--database-uri`:

- Задаёт только то, что есть в URI: `host`, `port`, `user`, `password`, `name`, а также `sslmode` и `client_encoding` из query string; остальные поля секции `database` (например `table_prefix`) берутся из `config.json`/env как обычно.
- Сам по себе **не** переключает сервер в режим только чтения: `write_mode` по-прежнему берётся из `config.json`/env (по умолчанию `false`). Чтобы принудительно оставить только чтение вместе с `--database-uri`, добавьте `--no-write-mode` — именно он отвечает за режим SQL. `--access-mode basic` — независимый параметр: он сужает видимость до схемы `public` и базовых инструментов, но не отключает запись сам по себе (`basic` + `write_mode=true` по-прежнему разрешает DML, см. таблицу ниже).
- Если в URI нет пароля, он берётся из `config.json` или переменных окружения — даже если host или user в URI отличаются от заданных там же.
- Если в URI не указан порт, подставляется `5432`; это значение перекрывает порт, заданный в `config.json` или env.

## Конфигурация

### Контроль доступа

Безопасность задаётся двумя независимыми параметрами:

#### Уровень доступа (`access_mode`)

Определяет доступ к схемам и набор доступных инструментов:

| access_mode | Схемы          | Инструменты | Описание |
| ----------- | -------------- | ----------- | -------- |
| `basic`     | Только `public` | Базовые (4) | Только схема public; опционально `table_prefix` — ограничение по префиксу имён таблиц |
| `full`      | Все схемы      | Все (9)     | Все схемы и расширенные инструменты (схемы, здоровье, топ запросов, анализ индексов) |

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

**Для access_mode=basic опционально:** `table_prefix` ограничивает таблицы, представления и последовательности по префиксу имени: `list_objects` скрывает объекты без префикса, а `get_object_details` и `execute_sql` отказывают по ним. Расширения префиксом не ограничиваются. Для full игнорируется.

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

Сервер доступен по адресу `http://localhost:8000/mcp`; путь задаёт `server.endpoint` (`MCP_SERVER_ENDPOINT`, ведущий `/` добавляется сам).

#### Проверка здоровья: `GET /health`

`http://localhost:8000/health` — для балансировщика и проб Kubernetes. Маршрут всегда в корне, независимо от `server.endpoint`, и не требует токена даже при включённой аутентификации. Сервер открывает отдельное соединение с базой (пул инструментов не трогается) и выполняет `SELECT 1`:

| Ответ | Когда |
| --- | --- |
| `200 {"status": "ok"}` | база ответила не дольше чем за 2 секунды |
| `503 {"status": "degraded", "error": "database unavailable"}` | ошибка подключения (адрес, пользователь, пароль в ответе не раскрываются) |
| `503 {"status": "degraded", "error": "database did not answer within 2 s"}` | база не ответила за 2 секунды |

Подробности неудачи (замаскированный пароль) уходят только в лог сервера на уровне WARNING, не чаще раза в секунду: результат пробы кэшируется на ~1 секунду и общий для всех одновременных запросов, чтобы поток анонимных `GET /health` не открывал по соединению на запрос. Если нужно увидеть в логе, к какому хосту/порту/базе реально подключается драйвер, поднимите уровень логгера `psycopg` до `DEBUG` — тогда в лог попадают сообщения libpq с хостом, портом, пользователем и именем базы (без пароля).

Отключается `server.health_endpoint_enabled=false` (`MCP_SERVER_HEALTH_ENDPOINT_ENABLED=false`), тогда `/health` отвечает `404`. В `stdio` маршрута нет.

#### STDIO

Для настольных MCP-клиентов (например Claude Desktop) или интеграции через процесс:

```bash
uv run postgres-fastmcp \
  --database-uri "postgresql://user:password@localhost:5432/dbname" \
  --transport stdio
```

### Справочник конфигурации

- **CLI:** `--database-uri`, `--transport`, `--host`, `--port`, `--access-mode`, `--write-mode` / `--no-write-mode`. Вывод версии: `--version`. Каждая заданная опция переопределяет `config.json` и переменные окружения на этот запуск.
- **config.json:** Должен содержать `server`, `fastmcp` и `database` (см. Быстрый старт). Загружается из текущей директории.
- **Переменные окружения / .env:** `MCP_<СЕКЦИЯ>_<ПОЛЕ>`: `MCP_SERVER_*`, `MCP_DATABASE_*`, `MCP_AUTH_*`, `MCP_FASTMCP_*` (см. [env.example](env.example)).
- **Итоговая конфигурация в логе:** на старте сервер пишет одну строку INFO `Database ceiling: access_mode=..., write_mode=..., table_prefix=...; auth=...` (класс auth-провайдера или `none`), а для HTTP ещё `Serving MCP over HTTP on host:port/endpoint`; секретов в них нет.

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
                "MCP_DATABASE_ACCESS_MODE": "basic",
                "MCP_DATABASE_WRITE_MODE": "false"
            }
        }
    }
}
```

## Аутентификация

Аутентификация защищает HTTP-транспорт: сервер принимает запрос, только если клиент прислал действующий токен. В `stdio` клиент сам запускает сервер как процесс, токенов там нет и аутентификация не применяется. Библиотечная функция `create_server(settings)` всегда строит провайдер из блока `auth`, независимо от `settings.server.transport` — это только конфигурационное значение, а не гарантия того, каким транспортом вызывающий код реально поднимет сервер. Пропускает сборку провайдера (и обращения к IdP, и файлы на диске для `oidc`) только сам CLI этого пакета, и только когда он в самом деле запускает `stdio`.

Коротко о терминах:

- **Токен** — строка, которую клиент отправляет в каждом запросе в заголовке `Authorization: Bearer <токен>`.
- **Claim** — именованное поле внутри токена: `sub` (кто), `iss` (кто выдал), `groups` (группы пользователя) и т.д.
- **Scope** — claim `scope`: список разрешений через пробел, например `pg:read pg:write`.
- **Группы/роли** — claim со списком групп или ролей пользователя в IdP (`groups`, `realm_access.roles` в Keycloak).
- **IdP** — сервис входа, который выдаёт токены: Keycloak, Auth0, Okta, корпоративный SSO.

### Режимы

Режим задаётся `auth.mode` в `config.json` или переменной `MCP_AUTH_MODE`:

| Режим | Когда использовать | Что проверяет токен |
| --- | --- | --- |
| `none` (по умолчанию) | `stdio`, доверенный localhost | ничего, токен не нужен |
| `static` | сервисные клиенты, тестовые стенды, небольшая команда | строка токена из списка в конфиге |
| `jwt` | токены выдаёт внешний IdP (machine-to-machine, CI) | подпись, `iss`, `aud`, срок действия |
| `oidc` | люди входят через корпоративный SSO прямо из MCP-клиента | вход в браузере через IdP, затем токен сервера |

Все настройки лежат в блоке `auth` (`config.json`) или в переменных `MCP_AUTH_*`; вложенные поля в переменных разделяются `__` (`MCP_AUTH_ACCESS_POLICY__ENFORCED`). Поля чужого режима игнорируются. Некорректный блок `auth` останавливает запуск с `ValidationError`, в которой названо поле; строки токенов и секреты в текст ошибки не попадают.

`required_scopes` (`MCP_AUTH_REQUIRED_SCOPES='["mcp"]'`) действует во всех режимах, кроме `none`: токен без любого из этих скоупов отклоняется целиком. Поля-списки и поля-словари в переменных окружения задаются JSON-строкой, как в этом примере; то же самое для `MCP_AUTH_TOKENS` (см. ниже) — и если JSON в `MCP_AUTH_TOKENS` некорректен, сервер не запустится ни в одном режиме, даже не в `static`.

#### `static`

```json
"auth": {
  "mode": "static",
  "tokens": {
    "<длинная случайная строка>": {"client_id": "ci-bot", "scopes": ["pg:write"]},
    "<другая строка>": {"client_id": "alice", "claims": {"groups": ["dba"]}}
  }
}
```

- `client_id` — имя клиента в логах; `scopes` — его скоупы; `claims` — любые дополнительные поля токена (например `groups` для политики прав).
- Строка токена — секрет: не храните её в git. Для Kubernetes положите словарь той же формы в secret и укажите файл: `MCP_AUTH_TOKENS_FILE=/run/secrets/mcp-tokens.json`. Токены из `tokens` и из `tokens_file` объединяются; при совпадении строки побеждает `tokens`.
- Через окружение: `MCP_AUTH_TOKENS='{"<строка>": {"client_id": "ci-bot", "scopes": ["pg:write"]}}'`.
- `MCP_AUTH_TOKENS` задаётся только JSON-строкой, как выше. Вложенная форма `MCP_AUTH_TOKENS__<строка_токена>__client_id=...` тоже работает, но приводит строку токена к нижнему регистру — для случайной строки со смешанным регистром сервер её не узнает.

#### `jwt`

| Поле | Обязательно | Назначение |
| --- | --- | --- |
| `jwt_jwks_uri` или `jwt_public_key` | ровно одно из двух | где взять ключ проверки подписи: URL набора ключей IdP (JWKS) или PEM публичного ключа |
| `jwt_issuer` | да | ожидаемый `iss` токена |
| `jwt_audience` | да | ожидаемый `aud`: строка или список |
| `jwt_algorithm` | нет | алгоритм подписи, по умолчанию `RS256` |

Чтобы положить в `jwt_public_key` общий HS-секрет (а не PEM), нужно явно задать `MCP_AUTH_JWT_ALGORITHM=HS256` — по умолчанию сервер проверяет подпись как RS256, и любой токен, подписанный HS-секретом, будет отклонён с `401`.

```bash
MCP_AUTH_MODE=jwt
MCP_AUTH_JWT_JWKS_URI=https://sso.example.com/realms/main/protocol/openid-connect/certs
MCP_AUTH_JWT_ISSUER=https://sso.example.com/realms/main
MCP_AUTH_JWT_AUDIENCE=postgres-mcp
```

#### `oidc`

| Поле | Обязательно | Назначение |
| --- | --- | --- |
| `oidc_config_url` | да | `https://<IdP>/.well-known/openid-configuration` |
| `oidc_client_id`, `oidc_client_secret` | да | клиент, заведённый для этого сервера в IdP |
| `base_url` | да | публичный адрес сервера, например `https://mcp.example.com` |
| `oidc_audience` | нет | ожидаемый `aud` токена IdP |

Как проходит вход:

1. MCP-клиент подключается без токена и получает `401` со ссылкой на метаданные сервера.
2. Клиент регистрируется на сервере и открывает браузер на `<base_url>/authorize`.
3. Сервер показывает страницу подтверждения доступа и перенаправляет пользователя в IdP, пользователь входит.
4. IdP возвращает пользователя на `<base_url>/auth/callback` — этот адрес нужно добавить в разрешённые redirect URI клиента в IdP.
5. Сервер выдаёт MCP-клиенту свой токен и на каждом запросе проверяет токен IdP за ним.

Сервер читает `oidc_config_url` при запуске: если IdP недоступен, сервер не стартует. Исключение — CLI этого пакета при реальном запуске с `--transport stdio` (или `MCP_SERVER_TRANSPORT=stdio`): он знает, каким транспортом запускается, и в этом случае сознательно не строит провайдер и не обращается к IdP, поскольку в `stdio` аутентификация всё равно не действует. Библиотечный `create_server()` от этого не зависит и всегда пытается построить провайдер (см. начало раздела). Регистрации MCP-клиентов хранятся зашифрованными в каталоге данных FastMCP (`FASTMCP_HOME`, по умолчанию `~/.local/share/fastmcp`); в контейнере вынесите его в volume, иначе после перезапуска клиентам придётся войти заново. `FASTMCP_HOME` должен указывать на доступный для записи volume — с read-only корневой файловой системой контейнера `oidc` не заработает.

### Подключение клиентов с токеном

Cursor (`~/.cursor/mcp.json`):

```json
{
    "mcpServers": {
        "postgres": {
            "url": "https://mcp.example.com/mcp",
            "headers": {"Authorization": "Bearer <токен>"}
        }
    }
}
```

Claude Code:

```bash
claude mcp add --transport http postgres https://mcp.example.com/mcp --header "Authorization: Bearer <токен>"
```

В режиме `oidc` заголовок не нужен: добавьте сервер без `--header`, клиент сам откроет браузер для входа (в Claude Code — команда `/mcp`).

### Ответы при отказе

| Ситуация | Ответ |
| --- | --- |
| Нет токена или токен неизвестен, подделан, просрочен | HTTP `401` с заголовком `WWW-Authenticate: Bearer` |
| У токена нет скоупа из `required_scopes` | HTTP `401`: верификаторы FastMCP отклоняют такой токен целиком |
| full-инструмент при эффективном режиме `basic` | инструмента нет в списке, вызов даёт `Unknown tool` |
| Запись при эффективном режиме только чтения | ошибка `... allowed in read-only mode` |

### Предупреждения на старте

Сервер пишет WARNING, если:

- транспорт HTTP, аутентификации нет, а `host` не `127.0.0.1`/`localhost`/`::1` — любой, кто достучится до порта, получит все права из `database`;
- транспорт `stdio`, а аутентификация включена — в `stdio` она не действует;
- `access_policy.enforced=true`, а аутентификации нет — без токена политика ничего не сужает.

## Права по claim

Серверный **потолок** прав задают `access_mode` и `write_mode` в `database`. Политика `auth.access_policy` сужает его для каждого запроса по значениям одного claim токена. Токен никогда не даёт больше потолка.

| Поле | По умолчанию | Назначение |
| --- | --- | --- |
| `enforced` | `false` | включить сужение; при `false` каждый запрос получает потолок |
| `claim` | `scope` | откуда брать значения: `scope`, имя claim (`groups`) или путь через точку (`realm_access.roles`) |
| `write_values` | `["pg:write"]` | любое из этих значений разрешает запись (если её разрешает потолок) |
| `full_values` | `["pg:full"]` | любое из этих значений даёт режим `full` (если потолок `full`) |

Если путь через точку и в токене одновременно есть буквальный ключ верхнего уровня с тем же именем целиком (например ключ `"realm_access.roles"`, а не вложенный объект `realm_access: {roles: [...]}`), побеждает этот буквальный ключ — так claim'ы Auth0 с URL-именами (`https://example.com/roles`) не разбираются по точкам ошибочно.

Правила:

- Сравнение значений — точное и с учётом регистра: `pg:full` не совпадёт с `PG:FULL`. Строковый claim делится по пробелам (`"Domain Admins"` даёт два значения — `Domain` и `Admins`); из claim-списка берутся только строковые элементы, остальные игнорируются; нет claim — значений нет.
- Без значения из `full_values` запрос работает в `basic`: только схема `public`, четыре инструмента; full-инструменты для этого токена скрыты.
- Без значения из `write_values` запрос только читает.
- Без токена (`stdio`, `auth.mode=none`) запрос получает потолок.

Пример со скоупами (потолок `full` + `write_mode=true`):

```bash
MCP_AUTH_ACCESS_POLICY__ENFORCED=true
```

| Скоупы токена | Права запроса |
| --- | --- |
| нет | `basic`, только чтение |
| `pg:write` | `basic`, запись в `public` (DML) |
| `pg:full` | `full`, только чтение |
| `pg:full pg:write` | `full`, запись и DDL |

Пример с группами из IdP:

```bash
MCP_AUTH_MODE=jwt
MCP_AUTH_JWT_JWKS_URI=https://sso.example.com/realms/main/protocol/openid-connect/certs
MCP_AUTH_JWT_ISSUER=https://sso.example.com/realms/main
MCP_AUTH_JWT_AUDIENCE=postgres-mcp
MCP_AUTH_ACCESS_POLICY__ENFORCED=true
MCP_AUTH_ACCESS_POLICY__CLAIM=groups
MCP_AUTH_ACCESS_POLICY__WRITE_VALUES='["dba","backend-writers"]'
MCP_AUTH_ACCESS_POLICY__FULL_VALUES='["dba"]'
```

То же со статическими токенами:

```json
"auth": {
  "mode": "static",
  "access_policy": {"enforced": true, "claim": "groups", "full_values": ["dba"], "write_values": ["dba"]},
  "tokens": {"s3cr3t-alice": {"client_id": "alice", "claims": {"groups": ["dba"]}}}
}
```

## Технические детали

### FastMCP

Форк построен на [FastMCP](https://gofastmcp.com/), который обеспечивает:

- Асинхронное выполнение и высокую производительность
- Транспорты HTTP и stdio
- Управление жизненным циклом и внедрение зависимостей для инструментов

### Управление жизненным циклом

Пулом соединений владеет `PostgresProvider`, его жизненный цикл привязан к lifespan сервера FastMCP:

- Пул открывается при первом запросе к БД
- Соединения закрываются при остановке сервера
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
- Оператор без результирующего набора (`INSERT`/`UPDATE`/`DELETE`/`MERGE` без `RETURNING`, DDL) `execute_sql` описывает тегом команды PostgreSQL: в `table` — одна строка `UPDATE 3: 3 rows affected.` (для DDL — `CREATE TABLE: done.`), в `json` — `{"rows": [], "row_count": 0, "status": "UPDATE 3", "affected_rows": 3}`; у DDL `affected_rows` равно `null`. Тег — это то, что вернул PostgreSQL: для `CREATE TABLE ... AS` это `SELECT n`; если в запросе несколько операторов, показывается тег последнего.
- `get_object_details` проверяет существование объекта по каталогу: отсутствующий объект — ошибка `Object not found ...`, пустая таблица возвращает заголовок без разделов. Тип важен: представление, запрошенное с `object_type="table"` (по умолчанию), даёт «не найдено» с подсказкой повторить с `object_type="view"` (и наоборот). Таблица, на которую у роли нет прав, тоже считается ненайденной — как и в `list_objects`.
- Ответ инструмента больше `response_max_tokens` (переменная `MCP_SERVER_RESPONSE_MAX_TOKENS`, по умолчанию 20000) заменяется ошибкой `Response is too large ... Refine the request`: агенту нужно добавить `WHERE`/`LIMIT`, выбрать меньше колонок или агрегировать. Размер оценивается как байты текста / 3. Если инструмент мог записать данные (аннотация `readOnlyHint=false`, то есть `execute_sql` при `write_mode=true`), текст другой и не утверждает, что запись точно произошла — это мог быть и обычный `SELECT`: `... If the statement modified data, its changes are already applied — do not re-run it; query the affected rows with a narrower SELECT instead. Otherwise refine the request: ...`.
- Ввод нормализуется: `object_type` понимает `Tables`, `VIEW`, `sequences`; `health_type` — список или строку через запятую в любом регистре; `sort_by` — синонимы `total`, `mean`, `avg`, `resource`; `limit` больше 100 урезается до 100 и действует для всех `sort_by`, включая `resources`. Неверное значение даёт ошибку, в которой всегда перечислены допустимые значения; если есть похожее, добавляется подсказка `Did you mean ...?`.

### Ограничения по доступу

- **access_mode=basic**: только базовые инструменты (`list_objects`, `get_object_details`, `explain_query`, `execute_sql`); опционально `table_prefix` для ограничения набора таблиц
- **access_mode=full**: все инструменты (базовые + `list_schemas`, `analyze_workload_indexes`, `analyze_query_indexes`, `analyze_db_health`, `get_top_queries`)
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

### Пример 2: access_mode=basic с префиксом таблиц

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

### Пример 4: Разработка (чтение-запись, access_mode=full)

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

Публичное API пакета:

```python
from postgres_fastmcp import (
    PostgresProvider,  # источник девяти инструментов одной базы
    DatabaseConfig,  # подключение и серверный потолок прав (access_mode, write_mode)
    AccessPolicy,  # политика сужения прав по claim токена
    EffectiveAccess,  # права одного запроса
    AccessResolver,  # тип резолвера: токен -> EffectiveAccess
    full_access_check,  # проверка «эффективный режим full» для своих компонентов
    ResponseBudgetMiddleware,  # бюджет ответа тула в токенах
    Settings,
    create_server,
)
```

`Middleware`, `LocalProvider` и прочие классы FastMCP импортируйте из `fastmcp`.

### Свой сервер: `PostgresProvider`

`DatabaseConfig` берёт только то, что передано в конструктор: переменные `MCP_DATABASE_*` и файл `.env` он не читает, поэтому окружение хоста не может поднять потолок прав. Не переданные `access_mode` и `write_mode` означают `basic` без записи; неизвестное поле (например устаревшее `role`) — ошибка `ValidationError`. Если конфигурация базы должна приходить из окружения, как у CLI, возьмите `Settings().database` или `DatabaseSettings()` из `postgres_fastmcp.app.config.database`.

```python
from fastmcp import FastMCP
from postgres_fastmcp import DatabaseConfig, PostgresProvider

mcp = FastMCP("my-app")
mcp.add_provider(
    PostgresProvider(
        DatabaseConfig(
            host="db", port=5432, user="u", password="p", name="orders", access_mode="basic", write_mode=False
        )
    )
)
mcp.add_provider(
    PostgresProvider(
        DatabaseConfig(host="db", user="u", password="p", name="analytics", access_mode="full", write_mode=False)
    ),
    namespace="analytics",  # инструменты analytics_execute_sql, analytics_list_schemas, ...
)
mcp.run(transport="http", host="0.0.0.0", port=8000)
```

- Провайдер владеет пулом соединений: пул открывается при первом запросе и закрывается при остановке сервера.
- Видимость инструментов задаёт `access_mode` из `DatabaseConfig`: в `basic` доступны четыре инструмента, в `full` — девять.
- `access_policy=AccessPolicy(enforced=True, ...)` сужает права запроса по claim токена, как описано в разделе «Права по claim»; токен даёт `AuthProvider` вашего сервера. `access_resolver` — функция `AccessToken | None -> EffectiveAccess` вместо политики, если нужна своя логика; она приоритетнее `access_policy`. Результат обоих никогда не превышает потолок из `DatabaseConfig`. Без политики и резолвера права запроса равны потолку.
- `/health` на своём сервере не появляется сам: `create_server` добавляет его через `custom_route`. Для своего маршрута вызовите `await provider.ping()` — `SELECT 1` на отдельном соединении, при недоступной базе бросает ошибку psycopg (её текст может содержать строку подключения — не отдавайте его наружу без маскировки); таймаут задайте сами, например `asyncio.timeout(2)`.
- `create_server` подключает бюджет ответа автоматически. На своём сервере добавьте его сами первым middleware: `mcp.add_middleware(ResponseBudgetMiddleware(20000))`, импорт — `from postgres_fastmcp import ResponseBudgetMiddleware`.

### Готовый сервер: `create_server`

```python
from postgres_fastmcp import Settings, create_server

server = create_server(
    Settings(),
    auth=None,  # None: провайдер из settings.auth; или любой AuthProvider из fastmcp.server.auth
    access_resolver=None,  # свой резолвер прав для PostgresProvider
    extra_providers=[],  # свои источники tools/resources/prompts
    extra_middleware=[],  # добавляются после встроенных middleware
)
server.run(transport="http", host="0.0.0.0", port=8000)
```

Замечания:
- `auth=None` означает «взять из `settings.auth`» (раздел «Аутентификация»); при `mode=none` аутентификации нет, что безопасно только для `stdio` или доверенного localhost. Переданный `AuthProvider` из `fastmcp.server.auth` (или собственный) заменяет провайдер из настроек; политика прав `settings.auth.access_policy` действует и с ним.
- Имена инструментов из `extra_providers` не должны совпадать со встроенными (`execute_sql`, `list_objects`, `get_object_details`, `explain_query`, `list_schemas`, `analyze_db_health`, `get_top_queries`, `analyze_query_indexes`, `analyze_workload_indexes`): при совпадении FastMCP пишет предупреждение и оставляет встроенный инструмент. Чтобы развести имена, подключите свой провайдер через `server.add_provider(provider, namespace="...")`.
- В `access_mode=basic` скрываются только full-инструменты этого пакета; инструменты из `extra_providers` тегом `full` не фильтруются и остаются видимыми независимо от `access_mode`.
- Middleware выполняются в порядке: бюджет ответа, timing, logging, затем `extra_middleware`. Бюджет стоит первым и проверяет в том числе результат ваших middleware.

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
| Настройка через CLI/env | config.json, env (`MCP_SERVER_*`, `MCP_DATABASE_*`, `MCP_AUTH_*`, `MCP_FASTMCP_*`) и CLI |
| —                      | Опциональный `table_prefix` для `access_mode=basic`; endpoint здоровья `/health` |

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
