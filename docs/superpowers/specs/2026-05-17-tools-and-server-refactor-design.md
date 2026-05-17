# Рефакторинг DB-тулов и серверной фабрики

Дата: 2026-05-17
Статус: design

## Контекст

Аудит слоя `tools/` и связанных провайдеров выявил набор проблем: неиспользуемые константы, stringly-typed параметры, дублирование логики выбора сервисов, длинные description с дублем параметров, последовательные `await` для независимых проверок здоровья БД, class-level кэш, self-загрязнение в `get_top_queries`, узкий пул. Часть из них исправлена в первой итерации (см. конец документа). Текущий документ описывает оставшиеся изменения **плюс** миграцию на FastMCP 3.3.1 с использованием новых возможностей: lifespan, visibility по тегам, programmatic-регистрация тулов, middleware, расширяемый API фабрики для использования пакета как библиотеки.

## Цели

1. Сделать пакет пригодным для использования как библиотеки: `from postgres_fastmcp import create_server`.
2. Полностью убрать глобальное состояние `app_config.current`.
3. Дать потребителю-библиотеки штатные точки расширения: auth, дополнительные providers, дополнительные middleware.
4. Использовать встроенные возможности FastMCP 3.3.1 вместо собственных аналогов (logging, timing, dispatch ошибок).
5. Не поддерживать legacy-пути: никаких deprecation-фолбэков, dual-path кода, type-hint-инжекта Context, module-level декораторов.
6. Поправить замеченные дефекты эффективности и читаемости в `services/`.

## Не-цели

- Реализация аутентификации в этом проекте (только открыть точку расширения).
- Поддержка обратной совместимости с предыдущими версиями API библиотеки (проект пока без внешних потребителей).
- Кастомные middleware на этой итерации.
- Кэширование ответов от БД.

## Архитектура

### Целевая структура пакета

```
src/postgres_fastmcp/
├── __init__.py          # публичный API: create_server, Settings, reexport FastMCP classes
├── server.py            # create_server(settings, *, auth, extra_providers, extra_middleware)
├── lifespan.py          # app_lifespan(settings) — создание/закрытие DbAccessService, конфиг в context
├── main.py              # CLI: build_settings_from_cli → create_server → mcp.run
├── config/              # Settings (без app_config singleton, без current)
├── enums.py             # + ObjectType, TopQueriesSortBy, AnalysisMethod, AnnotationsPreset
├── tools/
│   ├── basic/           # async def без декораторов
│   ├── full/            # async def без декораторов
│   ├── registry.py      # _register_tools(mcp, settings) → Tool.from_function + add_tool
│   └── constants.py     # PG_STAT_STATEMENTS, HEALTH_TYPE_VALUES, annotation-пресеты
├── services/            # внутренняя логика — структура без больших изменений
├── sql/                 # без изменений
└── common/
```

**Что исчезает:**
- Папка `providers/` целиком. Сервисы создаются один раз в `app_lifespan` и кладутся в `ctx.lifespan_context`. Тулы достают их оттуда.
- `app_config.current`, класс-синглтон `AppConfig`, `SettingsNotInitializedError`. Конфиг передаётся только параметром `create_server(settings, ...)`.
- Module-level `@tool`-декораторы. Все тулы регистрируются программно в `registry.py`.

### Публичный API библиотеки

```python
def create_server(
    settings: Settings,
    *,
    auth: AuthProvider | None = None,
    extra_providers: Sequence[Provider] = (),
    extra_middleware: Sequence[Middleware] = (),
) -> FastMCP:
    ...
```

**Семантика параметров:**

- `settings` — обязательный. Содержит database, server, fastmcp блоки.
- `auth=None` — без аутентификации (для stdio, локалхоста и встраивания во внешний сервер с собственным auth). Если потребитель передаёт `BearerAuthProvider`, `JWTAuthProvider` или свой subclass — FastMCP применит ко всем HTTP-эндпоинтам.
- `extra_providers=()` — добавляются к нашим встроенным провайдерам тулов. Потребитель может прицепить свои тулы/ресурсы/промпты.
- `extra_middleware=()` — вешаются **после** наших встроенных, чтобы наш timing/logging видел все запросы целиком.

**Реэкспорты из `__init__.py`:**
- `create_server`, `Settings` (наши).
- `BearerAuthProvider`, `JWTAuthProvider`, `LocalProvider`, `FileSystemProvider`, `Middleware` (из FastMCP — для удобства потребителя).

**Пример использования библиотеки:**
```python
from postgres_fastmcp import create_server, Settings, BearerAuthProvider

settings = Settings(database=DatabaseConfig(...))
server = create_server(
    settings,
    auth=BearerAuthProvider(tokens={"secret-1": {"scopes": ["read"]}}),
)
server.run(transport="http", host="0.0.0.0", port=8000)
```

### Lifespan

Lifespan создаёт `DbAccessService` (с пулом) при старте, кладёт его в context, закрывает при остановке.

```python
@lifespan
async def app_lifespan(server):
    db = DbAccessService(settings.database)
    try:
        yield {"db": db, "settings": settings}
    finally:
        await db.close()
```

Тулы получают `db` через `ctx.lifespan_context["db"]`. Никаких DI-провайдеров, никаких `Depends(get_db_access_service)`.

### Регистрация тулов

`tools/registry.py`:

```python
def _register_tools(mcp: FastMCP, settings: Settings) -> None:
    for spec in _basic_tool_specs(settings):
        mcp.add_tool(Tool.from_function(**spec))
    for spec in _full_tool_specs(settings):
        mcp.add_tool(Tool.from_function(**spec))
```

`_basic_tool_specs(settings)` строит описания тулов с учётом `settings.database.access_mode` и `write_mode` (для `execute_sql`). Description вычисляется при регистрации — после того, как `settings` уже на руках. Никакого `app_config.current` на module-level.

### Visibility

После регистрации обоих наборов тулов:

```python
if settings.database.access_mode == AccessMode.BASIC:
    mcp.disable(tags={ToolTag.FULL})
```

FULL-тулы остаются известны серверу, но скрыты от клиентов в BASIC-режиме. Это убирает условную регистрацию.

### Middleware-стек

```python
mcp.add_middleware(TimingMiddleware())
mcp.add_middleware(LoggingMiddleware())
for m in extra_middleware:
    mcp.add_middleware(m)
```

Два встроенных, ноль кастомных. `ErrorHandlingMiddleware` не подключаем: FastMCP сам ловит исключения тулов и превращает их в MCP error response.

### Обработка ошибок

- `FastMCP(name=..., mask_error_details=True)` — безопасный дефолт. Детали обычных исключений скрыты от клиента.
- User-facing исключения (`EmptyQueriesError`, `InvalidSortCriteriaError`, `ExplainAnalyzeWithHypotheticalError`, `QueriesLimitError`) наследуют `fastmcp.exceptions.ToolError`. Их сообщения всегда доходят до клиента.
- Внутренние исключения (`SqlExecutionError`, `ExplainPlanError`, драйверные ошибки) остаются обычными `Exception` и маскируются.

### Context

Везде, где тул принимает `Context`, используется только `ctx: Context = CurrentContext()`. Type-hint-инжект (`ctx: Context`) удаляется — FastMCP 3.x помечает его legacy.

## Конкретные правки в коде

### Тулы (`tools/`)

| Файл | Изменение |
|---|---|
| `tools/basic/execute_sql.py` | Убрать `@tool`, `app_config.current`. Принимать `db` из `ctx.lifespan_context`. Description формируется в registry с учётом `access_mode` + `write_mode`. |
| `tools/basic/list_objects.py` | Аналогично. |
| `tools/basic/get_object_details.py` | Аналогично. |
| `tools/basic/explain_query.py` | Убрать три DI-провайдера. Один `ExplainService.explain(sql, *, analyze, hypothetical_indexes)`. Валидация `analyze + hypothetical_indexes` уходит в сервис. |
| `tools/full/analyze_query_indexes.py` | Один `IndexAnalysisService`. Параметр `method: AnalysisMethod` передаётся в сервис, сервис диспатчит. |
| `tools/full/analyze_workload_indexes.py` | Аналогично. |
| `tools/full/get_top_queries.py` | Литерал `Literal["total_time", "mean_time", "resources"]` уже поправлен → `TopQueriesSortBy`. |
| `tools/full/analyze_db_health.py` | Без изменений в сигнатуре. |
| `tools/full/list_schemas.py` | Без изменений. |
| `tools/constants.py` | Добавить три annotation-пресета (`READ_ONLY_IDEMPOTENT`, `READ_ONLY_NON_IDEMPOTENT`, `DESTRUCTIVE`). |
| `tools/registry.py` (новый) | Сборка `Tool.from_function` для всех 9 тулов с описаниями и пресетами. |

**Description-блоки:** в description тула — только назначение, когда выбирать, workflow с другими тулами, важные предупреждения. Параметры — только в `Field(description=...)`. Снимает дубль, который читает LLM.

### Сервисы (`services/`)

| Файл | Изменение |
|---|---|
| `services/health/database_health.py:72-100` | Заменить последовательные `await` на `asyncio.gather(..., return_exceptions=True)`. Упавшие проверки попадают в отчёт как «check X failed: …». |
| `services/health/index_health_calc.py:10` | `_cached_indexes: list[Index] \| None` как **аннотация типа** на классе, `self._cached_indexes = None` в `__init__`. Гарантирует instance-уровень. |
| `services/objects/tables.py:83,98,112` | `asyncio.gather` для columns/constraints/indexes — три независимых запроса параллельно. |
| `services/top_queries/top_queries_calc.py:91-100` | Добавить `WHERE calls > 0 AND query NOT LIKE '%pg_stat_statements%'`. |
| `services/explain/*` | Сложить три explain-реализации в один `ExplainService.explain(*, analyze, hypothetical_indexes)`. |
| `services/index/service.py` | Метод `analyze(method: AnalysisMethod, ...)` сам диспатчит на dta/llm. |

### Провайдеры (`providers/`)

Удалить папку целиком. Сервисы создаются в `lifespan.py` и доступны через `ctx.lifespan_context`.

### Конфиг (`config/`)

| Файл | Изменение |
|---|---|
| `config/__init__.py` | Убрать `app_config` синглтон, `current` property, `SettingsNotInitializedError`. Оставить экспорт `Settings`. |
| `config/database.py:58` | `pool_max_size: int = 10` (было 5). |
| `common/errors.py` | Убрать `SettingsNotInitializedError`. Помеченные user-facing исключения наследуют `ToolError`. |

### Сервер и main (`server.py`, `main.py`)

| Файл | Изменение |
|---|---|
| `server.py` | Текущий `compose_mcp` → `create_server(settings, *, auth, extra_providers, extra_middleware)`. Применяет lifespan, middleware, регистрирует тулы через registry, делает visibility-фильтр по тегам, передаёт `auth` и `extra_providers` в `FastMCP(...)`, `mask_error_details=True`. |
| `main.py` | Зовёт `create_server(settings)` (без auth). Без других изменений. |
| `__init__.py` | Реэкспортирует `create_server`, `Settings`, `BearerAuthProvider`, `JWTAuthProvider`, `LocalProvider`, `FileSystemProvider`, `Middleware`. |

### Зависимости

`pyproject.toml`: `fastmcp = "^3.3.1"` (было `3.1.1`).

## План миграции

Реализация будет одним PR, поскольку изменения связаны и затрагивают точку входа. Внутри PR — последовательность шагов с прохождением тестов на каждом:

1. Bump `fastmcp` до 3.3.1; запустить тесты, поправить deprecation-ошибки.
2. `enums.py`: добавить `AnalysisMethod = Literal["dta", "llm"]`. Annotation-пресеты оформляются как dict-константы в `tools/constants.py`, не enum-ы.
3. `services/`: правки эффективности (3.1, 3.2, 3.3, 3.4). Чисто внутри сервисов, тулы не трогаются.
4. `services/explain/*`: склейка трёх классов в один.
5. `services/index/service.py`: метод-диспатчер.
6. Новый `lifespan.py`. `DbAccessService` создаётся там.
7. Новый `tools/registry.py`. `Tool.from_function` для всех 9 тулов.
8. Тулы переписываются на `async def` без декоратора, забирают `db` из `ctx.lifespan_context`.
9. Удалить папку `providers/`.
10. Удалить `app_config.current` и `SettingsNotInitializedError`.
11. `server.py`: `create_server(...)` с тремя расширениями. Middleware-стек.
12. `__init__.py`: публичный API + реэкспорты FastMCP-классов.
13. `main.py`: подгонка под новый `create_server`.
14. `config/database.py`: `pool_max_size=10`.
15. `README.md`: раздел «Use as a library» с примером Bearer-auth.

## Тесты

- Существующие тесты на сервисы должны проходить после правок 3.x.
- Добавить smoke-тест: `create_server(settings)` без аргументов → проверить, что 4 тула видны при BASIC и 9 при FULL.
- Добавить тест: `create_server(settings, extra_middleware=[m])` → проверить, что `m` действительно вызывается на `on_call_tool`.
- Добавить тест: тул, кидающий `ToolError("…")`, возвращает сообщение клиенту даже при `mask_error_details=True`.

## Открытые вопросы

Нет на данный момент. Все обсуждённые развилки зафиксированы в тексте.

## Уже сделано в предыдущей итерации

Для полноты картины, что уже в `develop` (до этой спеки):

- `tools/constants.py`: удалено 17 неиспользуемых констант.
- `tools/basic/execute_sql.py`: `default="all"` убран, условие unrestricted вынесено в `_UNRESTRICTED`.
- `enums.py`: добавлены `ObjectType`, `TopQueriesSortBy`.
- `tools/basic/list_objects.py`, `tools/basic/get_object_details.py`: `object_type: str` → `ObjectType`.
- `tools/full/get_top_queries.py`: `sort_by: str` → `TopQueriesSortBy`.
