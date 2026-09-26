# Дизайн: FastMCP 4, PostgresProvider, аутентификация и закалка read-only

Дата: 2026-09-26. Статус: утверждён, ждёт плана реализации.

## 1. Контекст и результаты аудита

Аудит кода проводился по четырём вопросам пользователя: работает ли read-only,
работает ли скрытие инструментов, что нужно для FastMCP 4.x, какой должна быть
схема аутентификации. Все выводы проверены запуском кода, а не чтением.

### 1.1. Что работает

| Проверка | Метод | Результат |
| --- | --- | --- |
| Read-only | 85 SQL-запросов через `QueryValidator` во всех трёх ограниченных режимах | Обходы (`SELECT INTO`, data-modifying CTE, `FOR UPDATE`, `COPY ... FROM PROGRAM`, `DO`, `CALL`, `SET`, `COMMIT; INSERT`, `pg_sleep`, `pg_terminate_backend`, `lo_import`, `dblink()`) блокируются. Второй рубеж: `BEGIN TRANSACTION READ ONLY` в `SqlExecutor`. |
| Скрытие тулов | `Client(create_server(basic))` | `full`-тулы отсутствуют в `list_tools`, прямой вызов даёт `Unknown tool`. |
| FastMCP 4.0.10 | Unit-тесты проекта в изолированном окружении | 301 тест проходит без изменений кода. Сигнатуры `FastMCP.__init__`, `disable(tags=)`, `Tool.from_function`, middleware, auth-провайдеров сохранены. |

### 1.2. Что найдено

1. `CREATE EXTENSION <любое из ~110 в ALLOWED_EXTENSIONS>` проходит валидатор во
   всех режимах. В `basic + write_mode=true` транзакция read-write, поэтому это
   реальный DDL-обход (`dblink`, `file_fdw`, `plpython3u`). В read-only спасает
   только транзакция БД.
2. Ложные срабатывания allowlist функций: `generate_series`, `AT TIME ZONE`,
   `SIMILAR TO`, `json_to_recordset(...) AS x(a int)`.
3. Таймаут через `asyncio.timeout` отменяет корутину, запрос в Postgres продолжает
   выполняться.
4. Мёртвая конфигурация: `server.endpoint`, `server.workers`,
   `server.health_endpoint_enabled`, `fastmcp.return_errors_as_strings`,
   `fastmcp.error_traceback_in_strings` нигде не читаются. Эндпоинта `/health`
   не существует, README его обещает.
5. README: `MCP_DATABASE_ROLE=admin` тихо игнорируется (`extra="ignore"`),
   смешаны роли `user/admin` и `basic/full`, скрипт назван `postgres-fastmcp`,
   в `pyproject` он `postgres-mcp`.
6. `AGENTS.md` устарел: описывает `@mcp.tool`, запрещает
   `from __future__ import annotations`, а `tools/registry.py`, `app/server.py`,
   `app/context.py` его используют.
7. FastMCP 4 удалил `ctx.sample()`; `method='llm'` в `analyze_query_indexes` и
   `analyze_workload_indexes` на нём построен.

## 2. Цели и границы

Цели:

- Перейти на FastMCP 4.x.
- Сделать пакет полноценной библиотекой: потребитель подключает наш источник
  тулов к своему серверу через нативный `Provider` FastMCP.
- Добавить аутентификацию, выбираемую конфигом без кода: `none`, `static`,
  `jwt`, `oidc`.
- Добавить сужение прав по claim токена с настраиваемым источником.
- Закрыть найденные дыры валидатора и убрать мёртвую конфигурацию.

Границы:

- Ломающие изменения разрешены, обратная совместимость не поддерживается ни для
  CLI, ни для библиотечного API. Версия пакета становится `0.1.0`.
- `method='llm'` удаляется, а не переписывается на серверный вызов LLM.
- Полный OAuth-сервер (`OAuthProvider`) и провайдеры конкретных вендоров
  (GitHub, Google) не добавляются: `oidc` покрывает корпоративные IdP,
  `jwt` покрывает machine-to-machine.
- Права одного запроса определяются один раз при входе в тул; динамическая
  смена прав внутри вызова не поддерживается.

## 3. Архитектура

### 3.1. Слои после изменения

```text
app/          composition root: config (+auth), фабрика auth-провайдера,
              create_server, /health, CLI
provider.py   PostgresProvider(LocalProvider): пул, политика прав, тулы
access.py     EffectiveAccess, AccessPolicy, AccessResolver, resolve_access,
              full_access_check
tools/        ToolSet (9 тулов как методы) и registry (описания, аннотации)
domains/      без изменений по составу; типизируются против DbAccessPort
postgres/     драйвер, пул, security (валидатор, SafeSqlExecutor)
shared/       errors, enums, logger, utils
```

Направление зависимостей: `app → provider → tools → domains → postgres → shared`;
`access.py` импортируется из `provider` и `app`, сам зависит только от `shared`
и `fastmcp.server.auth`. Модули `app/lifespan.py` и `app/context.py` удаляются.

### 3.2. `PostgresProvider`

```python
class PostgresProvider(LocalProvider):
    def __init__(
        self,
        database: DatabaseConfig,
        *,
        access_policy: AccessPolicy | None = None,
        access_resolver: AccessResolver | None = None,
    ) -> None: ...

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]: ...   # закрывает пул
    async def ping(self) -> None: ...                       # SELECT 1 для /health
```

- Владеет `DbAccessService` (пул создаётся лениво при первом запросе, как
  сейчас; `lifespan()` гарантирует закрытие).
- `access_resolver` имеет приоритет над `access_policy`. Если не задан ни один,
  резолвер всегда возвращает серверный потолок из `database`.
- Регистрирует девять тулов через `tools.registry.register_tools(self, toolset,
  ceiling=database)`. `ToolSet` получает `get_db: Callable[[], DbAccessPort]`,
  который у провайдера реализован как: взять токен через `get_access_token()`,
  посчитать `EffectiveAccess`, вернуть `DbAccessService.view(access)`.
- Видимость: если `database.access_mode == basic`, провайдер вызывает
  `self.disable(tags={"full"})`. Дополнительно каждый `full`-тул регистрируется
  с `auth=full_access_check(resolver)`: тул виден и вызываем только если
  эффективный `access_mode == FULL`. Проверка получает `None` вместо токена в
  stdio и при `mode=none`, резолвер в этом случае возвращает потолок, поэтому
  локальные пользователи ничего не теряют.
- Тулы больше не принимают `Context`: единственное использование было
  `get_db(ctx)` и sampling.

Использование как библиотеки:

```python
from fastmcp import FastMCP
from postgres_fastmcp import AccessPolicy, DatabaseConfig, PostgresProvider

mcp = FastMCP("my-app", auth=my_auth_provider)
mcp.add_provider(PostgresProvider(DatabaseConfig(host="db", name="orders", ...)))
mcp.add_provider(
    PostgresProvider(
        DatabaseConfig(host="db", name="analytics", access_mode="full"),
        access_policy=AccessPolicy(enforced=True, claim="groups", full_values=["dba"]),
    ),
    namespace="analytics",
)
```

Несколько баз в одном хост-сервере через namespace: ограничение «одна БД на
сервер» остаётся только для CLI-режима.

### 3.3. Доступ к БД: `DbAccessPort` и `view()`

В `domains/db_access.py`:

```python
class DbAccessPort(Protocol):
    sql_driver: SqlDriverPort
    access_mode: AccessMode
    write_mode: bool
    table_prefix: str | None
    connection_id: str

@dataclass(frozen=True, slots=True)
class DbAccess:               # реализация DbAccessPort для одного запроса
    ...

class DbAccessService:
    def view(self, access: EffectiveAccess) -> DbAccess: ...
    async def close(self) -> None: ...
```

- Исполнители кэшируются в `dict[EffectiveAccess, SqlDriverPort]`, максимум
  четыре записи; пул один на сервис.
- Небезопасный `SqlExecutor` выдаётся только для `EffectiveAccess(FULL, write=True)`;
  остальные комбинации получают `SafeSqlExecutor` с `SafeSqlConfig`, собранным
  как сейчас, но из `access`, а не из конфига.
- Свойство `sql_driver` у сервиса удаляется. Домены (`querying`, `top_queries`,
  `CatalogService`, `ExplainService`, `IndexAnalysisService`,
  `DatabaseHealthAnalyzer`) принимают `DbAccessPort`.

### 3.4. Права: `access.py`

```python
@dataclass(frozen=True, slots=True)
class EffectiveAccess:
    access_mode: AccessMode
    write_mode: bool

class AccessPolicy(BaseModel):
    enforced: bool = False
    claim: str = "scope"                       # путь через точку: realm_access.roles
    write_values: list[str] = ["pg:write"]     # значения по умолчанию, переопределяются
    full_values: list[str] = ["pg:full"]

AccessResolver = Callable[[AccessToken | None], EffectiveAccess]

def build_resolver(ceiling: EffectiveAccess, policy: AccessPolicy) -> AccessResolver: ...
def resolve_access(ceiling: EffectiveAccess, token: AccessToken | None, policy: AccessPolicy) -> EffectiveAccess: ...
def full_access_check(resolver: AccessResolver) -> AuthCheck: ...
```

Правила `resolve_access`:

1. `token is None` или `policy.enforced is False` → `ceiling`.
2. Значения claim: при `claim == "scope"` берутся `token.scopes`; иначе значение
   по пути в `token.claims`. Строка делится по пробелам, список берётся как есть,
   отсутствие пути или иной тип дают пустое множество.
3. `write_mode = ceiling.write_mode and bool(values & set(policy.write_values))`.
4. `access_mode = FULL` только если `ceiling.access_mode == FULL` и
   `values & set(policy.full_values)` непусто; иначе `BASIC`.
5. Токен никогда не расширяет права выше потолка.

`full_access_check(resolver)` возвращает `AuthCheck`, который читает
`ctx.token`, вызывает резолвер и возвращает `access_mode == FULL`. Он
используется провайдером на `full`-тулах и экспортируется для потребителей,
которые захотят навесить его на свои компоненты.

### 3.5. Конфигурация аутентификации

Новый модуль `app/config/auth.py`, класс `AuthSettings(BaseSettings)`,
`env_prefix="MCP_AUTH_"`, `env_nested_delimiter="__"`, секция `auth` в
`config.json`. `Settings.auth: AuthSettings`.

| Поле | Тип | Режим | Назначение |
| --- | --- | --- | --- |
| `mode` | `none \| static \| jwt \| oidc` | все | `none` по умолчанию |
| `required_scopes` | `list[str]` | static/jwt/oidc | скоупы для входа вообще, передаются провайдеру FastMCP |
| `access_policy` | `AccessPolicy` | все | см. 3.4 |
| `tokens` | `dict[str, StaticToken]` | static | `StaticToken(client_id: str, scopes: list[str] = [], claims: dict[str, Any] = {})` |
| `tokens_file` | `Path` | static | JSON той же формы, что `tokens` (k8s secret) |
| `jwt_jwks_uri` | `str` | jwt | одно из `jwt_jwks_uri` / `jwt_public_key` обязательно |
| `jwt_public_key` | `str` | jwt | PEM |
| `jwt_issuer` | `str` | jwt | обязательно |
| `jwt_audience` | `str \| list[str]` | jwt | обязательно |
| `jwt_algorithm` | `str \| None` | jwt | по умолчанию решает FastMCP |
| `oidc_config_url` | `str` | oidc | `.well-known/openid-configuration`, обязательно |
| `oidc_client_id` | `str` | oidc | обязательно |
| `oidc_client_secret` | `SecretStr` | oidc | обязательно |
| `oidc_audience` | `str \| None` | oidc | |
| `base_url` | `str` | oidc | публичный адрес сервера для callback, обязательно |

Правила валидации (`model_validator`):

- `static`: `tokens` или `tokens_file` заданы, итоговый словарь непуст.
- `jwt`: ровно одно из `jwt_jwks_uri`/`jwt_public_key`; `jwt_issuer` и
  `jwt_audience` заданы.
- `oidc`: четыре обязательных поля заданы.
- Поля чужих режимов игнорируются.

Пример env для групп:

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

Пример `static` с теми же группами:

```json
"auth": {
  "mode": "static",
  "access_policy": {"enforced": true, "claim": "groups", "full_values": ["dba"], "write_values": ["dba"]},
  "tokens": {"s3cr3t-alice": {"client_id": "alice", "claims": {"groups": ["dba"]}}}
}
```

`StaticTokenVerifier` кладёт произвольные ключи словаря токена в
`AccessToken.claims` (проверено на 4.0.10), поэтому `claims` разворачиваются в
словарь токена рядом с `client_id` и `scopes`.

### 3.6. Фабрика auth-провайдера

`app/auth.py::build_auth_provider(auth: AuthSettings) -> AuthProvider | None`:

| `mode` | Провайдер FastMCP |
| --- | --- |
| `none` | `None` |
| `static` | `StaticTokenVerifier(tokens=..., required_scopes=...)` |
| `jwt` | `JWTVerifier(jwks_uri= или public_key=, issuer=, audience=, algorithm=, required_scopes=)` |
| `oidc` | `OIDCProxy(config_url=, client_id=, client_secret=, audience=, base_url=, required_scopes=)` |

### 3.7. `create_server`

```python
def create_server(
    settings: Settings,
    *,
    auth: AuthProvider | None = None,
    access_resolver: AccessResolver | None = None,
    extra_providers: Sequence[Provider] = (),
    extra_middleware: Sequence[Middleware] = (),
) -> FastMCP: ...
```

1. `auth = auth if auth is not None else build_auth_provider(settings.auth)`.
2. `provider = PostgresProvider(settings.database, access_policy=settings.auth.access_policy, access_resolver=access_resolver)`.
3. `FastMCP(name, instructions, providers=[provider, *extra_providers], auth=auth,
   middleware=[TimingMiddleware(), LoggingMiddleware(), *extra_middleware],
   mask_error_details=True, on_duplicate="error")`.
4. При `settings.server.health_endpoint_enabled` регистрируется
   `GET /health` через `custom_route`: `200 {"status": "ok"}` если
   `provider.ping()` успешен за 2 секунды, иначе `503 {"status": "degraded",
   "error": <текст с замаскированным паролем>}`. Auth на маршрут не
   распространяется.
5. Если `settings.server.transport == http`, `settings.auth.mode == none` и
   `settings.server.host` не в `{127.0.0.1, localhost, ::1}`, пишется WARNING.
6. Если `transport == stdio` и `auth.mode != none`, пишется WARNING: auth
   действует только для HTTP.

`main.py` передаёт `path=settings.server.endpoint` в `mcp.run(transport="http", ...)`.

### 3.8. Реестр тулов

- Аннотации в snake_case: `read_only_hint`, `destructive_hint`,
  `idempotent_hint`, `open_world_hint`.
- Описания зависят от серверного потолка (`ceiling`), как сейчас, кроме
  `execute_sql`: его описание нейтрально и одинаково во всех режимах:
  «Execute a SQL statement. The server enforces the access policy: in read-only
  mode only SELECT, EXPLAIN and SHOW are accepted; DDL/DML are rejected with an
  explicit error. Workflow: list_objects → get_object_details → execute_sql».
  Аннотация `execute_sql` отражает потолок: `destructive_hint=True` только при
  `FULL + write`.
- Параметр `method` у `analyze_query_indexes` и `analyze_workload_indexes`
  удаляется вместе с enum `AnalysisMethod`.

## 4. Закалка валидатора и исполнителя

1. `ALLOWED_EXTENSIONS = frozenset({"hypopg", "pg_stat_statements"})`.
   `CreateExtensionStmt` разрешён только при `read_only=False`; в read-only
   валидатор отклоняет его `StatementTypeNotAllowedError`, не дожидаясь БД.
2. В `ALLOWED_FUNCTIONS` добавляются `generate_series`, `timezone`,
   `similar_to_escape`, `similar_escape`. В `ALLOWED_NODE_TYPES` добавляется
   `ColumnDef` (нужен для `RangeFunction` с описанием колонок; DDL по-прежнему
   режется на уровне типа оператора).
3. `SafeSqlExecutor` добавляет в начало транзакции
   `SET LOCAL statement_timeout = '<safe_sql_timeout * 1000>ms';` перед
   `SET LOCAL search_path`. Внешний `asyncio.timeout` остаётся с запасом
   `safe_sql_timeout + 5` секунд как страховка на зависшее соединение.
4. Корпус из аудита становится тестами: `tests/unit/postgres/test_query_validator_corpus.py`
   с двумя параметризованными наборами «обязан блокировать» и «обязан пропускать»
   для трёх режимов.

Не меняются: двухслойная схема, семантика `basic + write` (DML в `public`, без
DDL), `current_setting` в allowlist, откат `explain_query analyze=True` в basic
к обычному EXPLAIN с пометкой.

## 5. Чистка конфигурации, CLI и документации

- Удаляются: `server.workers`, CLI `--workers`, `fastmcp.return_errors_as_strings`,
  `fastmcp.error_traceback_in_strings`, `MountMode` в `shared/enums.py`.
- Начинают работать: `server.endpoint`, `server.health_endpoint_enabled`.
- Скрипт в `pyproject` переименовывается в `postgres-fastmcp`; `Dockerfile`
  ENTRYPOINT обновляется.
- `pyproject`: `fastmcp>=4.0.10,<5`, версия `0.1.0`.
- README: роли `user/admin` заменяются на `basic/full`, `MCP_DATABASE_ROLE`
  убирается, добавляются разделы «Аутентификация» (четыре режима, примеры
  подключения Cursor и Claude Code с `Authorization: Bearer`, OIDC-флоу) и
  «Права по claim» (таблица политики, примеры со `scope` и с `groups`),
  раздел «Использование как библиотека» переписывается под `PostgresProvider`,
  упоминания `method='llm'` удаляются, `/health` описывается как существующий.
- `env.example` и `docker/config.json` синхронизируются с новыми полями.
- `AGENTS.md`: раздел «Tool Definitions» заменяется описанием `ToolSet` +
  `registry`, в «Layered Architecture» добавляются `provider.py`, `access.py`,
  `app/auth.py`, `app/config/auth.py`, удаляются `app/lifespan.py`,
  `app/context.py`; правило про `from __future__` остаётся и выполняется.

## 6. Публичный API пакета

`postgres_fastmcp/__init__.py` экспортирует: `PostgresProvider`,
`DatabaseConfig`, `AccessPolicy`, `EffectiveAccess`, `AccessResolver`,
`full_access_check`, `Settings`, `create_server`, `__version__`.
Реэкспорты `Middleware`, `LocalProvider`, `FileSystemProvider` убираются:
потребитель импортирует их из `fastmcp`.

## 7. Обработка ошибок

| Ситуация | Кто отвечает | Что видит клиент |
| --- | --- | --- |
| Невалидный или отсутствующий токен по HTTP при `mode != none` | транспорт FastMCP | HTTP 401 |
| Токен без `required_scopes` | провайдер FastMCP | HTTP 403 `insufficient_scope` |
| `full`-тул при эффективном `basic` | `full_access_check` | тул не в списке, вызов даёт `Unknown tool` |
| Запись при эффективном read-only | `QueryValidator` + транзакция | `StatementTypeNotAllowedError` с текстом про read-only |
| `CREATE EXTENSION` в read-only | `QueryValidator` | `StatementTypeNotAllowedError` |
| `CREATE EXTENSION` не из allowlist в write | `QueryValidator` | `CreateExtensionNotSupportedError` |
| Запрос дольше `safe_sql_timeout` | Postgres `statement_timeout` | ошибка Postgres `canceling statement due to statement timeout` |
| Некорректный блок `auth` в конфиге | `AuthSettings` | процесс не стартует, `ValidationError` с именем поля |
| БД недоступна | `/health` | 503 `degraded` с замаскированным паролем |

## 8. Тестирование

Unit:

- `resolve_access`: матрица потолок × claim, включая строковый `scope`,
  списковый `groups`, вложенный путь `realm_access.roles`, `enforced=False`,
  токен `None`, попытка расширить права выше потолка.
- `AuthSettings`: валидные и невалидные наборы полей для каждого режима,
  `tokens_file`, разбор `MCP_AUTH_TOKENS` из JSON-строки.
- `build_auth_provider`: тип возвращаемого провайдера по режиму.
- `DbAccessService.view`: кэширование исполнителей по `EffectiveAccess`,
  один пул, `close()`.
- `PostgresProvider`: список тулов при `basic`/`full` без auth; `full_access_check`
  с токеном `None`, с нужным и без нужного значения claim.
- Корпус валидатора (раздел 4).
- `/health`: 200 при успешном `ping`, 503 при ошибке.

Интеграция (реальная БД в CI, как сейчас):

- HTTP-сервер со `StaticTokenVerifier` и `access_policy.enforced=true`; три
  токена (без значений, `write`, `full`); для каждого проверяется `list_tools`,
  `execute_sql` с `CREATE TABLE` и `INSERT`, вызов `list_schemas`.
- Тот же сценарий с `claim="groups"` и списковыми значениями.
- `statement_timeout` прерывает заведомо долгий разрешённый запрос
  (`SELECT count(*) FROM generate_series(1, 10000000000)`) ошибкой Postgres
  `canceling statement due to statement timeout`.
- `CREATE EXTENSION hypopg`: отклоняется в read-only, выполняется в write.
- Существующие интеграционные тесты тулов адаптируются к `PostgresProvider`.

CI: `FASTMCP_MCP_CAMELCASE_COMPAT=false` в `env`, чтобы остатки camelCase
падали жёстко.

## 9. Порядок работ

Каждый шаг оставляет зелёный CI и оформляется отдельным PR.

1. FastMCP 4: пин, snake_case аннотаций, удаление `llm`-метода и
   `AnalysisMethod`, удаление `from __future__ import annotations`,
   `FASTMCP_MCP_CAMELCASE_COMPAT=false` в CI.
2. Валидатор и исполнитель: `CREATE EXTENSION`, allowlist функций, `ColumnDef`,
   `statement_timeout`, тестовый корпус.
3. Провайдер без auth: `DbAccessPort` + `view()`, `access.py` с резолвером,
   всегда возвращающим потолок, `ToolSet`, `PostgresProvider`, `create_server`
   поверх провайдера, удаление `lifespan.py` и `context.py`, обновление
   публичного API. Поведение для пользователя не меняется.
4. Auth: `AuthSettings`, `build_auth_provider`, политика прав,
   `full_access_check`, предупреждения на старте, HTTP-интеграционные тесты.
5. Чистка: `/health`, `endpoint`, удаление мёртвых полей и `--workers`,
   переименование скрипта, версия `0.1.0`, README, `env.example`,
   `docker/config.json`, `AGENTS.md`.

## 10. Список ломающих изменений

- `fastmcp>=4`; `method='llm'` и параметр `method` удалены.
- Публичный API: `create_server` получает `access_resolver`; экспорты
  `Middleware`, `LocalProvider`, `FileSystemProvider` убраны; добавлены
  `PostgresProvider`, `DatabaseConfig`, `AccessPolicy`, `EffectiveAccess`,
  `AccessResolver`, `full_access_check`.
- `DbAccessService.sql_driver` заменён на `view(access)`; домены типизируются
  против `DbAccessPort`; `app/lifespan.py`, `app/context.py` удалены.
- Конфиг: удалены `server.workers`, `fastmcp.return_errors_as_strings`,
  `fastmcp.error_traceback_in_strings`; добавлен блок `auth`.
- CLI: удалён `--workers`; скрипт переименован в `postgres-fastmcp`.
- Валидатор: `CREATE EXTENSION` только `hypopg`/`pg_stat_statements` и только в
  write; в read-only отклоняется до обращения к БД.
- Версия `0.1.0`.
