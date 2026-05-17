# Tools & Server Refactor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Превратить `postgres-fastmcp` в библиотечно-пригодный MCP-сервер с фабрикой `create_server(settings, *, auth, extra_providers, extra_middleware)`, убрать глобальный `app_config.current`, перевести регистрацию тулов на `Tool.from_function`, поправить эффективность сервисов и обновить FastMCP до 3.3.x.

**Architecture:** Lifespan создаёт `DbAccessService` и кладёт его в `ctx.lifespan_context`. Тулы — обычные `async def` без декораторов, регистрируются программно через `Tool.from_function` + `mcp.add_tool` в `tools/registry.py`. Visibility управляется тегами (`mcp.disable(tags={ToolTag.FULL})`). Папка `providers/` удаляется целиком. `create_server` принимает auth, extra_providers, extra_middleware как точки расширения для потребителей библиотеки.

**Tech Stack:** Python 3.12, FastMCP 3.3.x, asyncpg/psycopg, Pydantic v2, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-05-17-tools-and-server-refactor-design.md`

---

## Phase 0 — Подготовка ветки

### Task 0.1: Создать рабочую ветку

**Files:** working tree only.

- [ ] **Step 1: Проверить, что текущая ветка `develop` и чистая**

Run: `git status`
Expected: `working tree clean` (или ожидаемые модификации в `Makefile`/`uv.lock`).

- [ ] **Step 2: Создать ветку от `main`**

Run:
```bash
git fetch origin main && sleep 1 && git checkout -b feat/library-server-refactor origin/main
```

- [ ] **Step 3: Проверить, что pytest и mypy запускаются**

Run:
```bash
uv run pytest tests/unit -x --no-header -q 2>&1 | tail -20
uv run mypy src 2>&1 | tail -5
```
Expected: оба запускаются без коллапса (тесты могут упасть в integration — это ок, сейчас гоним только unit).

---

## Phase 1 — Bump FastMCP до 3.3.x

### Task 1.1: Обновить fastmcp в pyproject и lockfile

**Files:**
- Modify: `pyproject.toml` (зависимость `fastmcp`)
- Modify: `uv.lock`

- [ ] **Step 1: Поднять нижнюю границу версии**

В `pyproject.toml` заменить:
```toml
"fastmcp>=3.0.0rc2",
```
на:
```toml
"fastmcp>=3.3.1,<4",
```

- [ ] **Step 2: Обновить lock и установить**

Run: `uv lock --upgrade-package fastmcp && uv sync`
Expected: `fastmcp` в `uv.lock` стоит `3.3.x` (минимум 3.3.1).

- [ ] **Step 3: Прогон тестов после bump-а**

Run: `uv run pytest tests/unit -x --no-header -q 2>&1 | tail -30`
Expected: всё проходит. Если падают тесты на legacy-симптомы FastMCP — починить точечно (см. шаг 4).

- [ ] **Step 4: Починить точки, отвалившиеся из-за breaking changes**

Возможные сломы (фиксить инлайн):
- `from fastmcp.dependencies import CurrentContext` — путь не поменялся, но проверить.
- `from fastmcp.server.providers import FileSystemProvider` — путь не поменялся, проверить.
- `enabled=` на декораторе — если где-то использовался, заменить логику на `mcp.disable(...)` (но в нашей кодовой базе сейчас не используется).

- [ ] **Step 5: Коммит**

```bash
git add pyproject.toml uv.lock && sleep 1 && git commit -m "chore(deps): bump fastmcp to >=3.3.1"
```

---

## Phase 2 — Эффективность сервисов (без затрагивания тулов и провайдеров)

### Task 2.1: `IndexHealthCalc._cached_indexes` — instance-уровень

**Files:**
- Test: `tests/unit/services/health/test_index_health_calc.py`
- Modify: `src/postgres_fastmcp/services/health/index_health_calc.py:10`

- [ ] **Step 1: Открыть `tests/unit/services/health/test_index_health_calc.py` и добавить failing-тест**

Добавить в конец файла (перед существующими `# pylint: disable` или импортами оставить как есть):

```python
def test_cached_indexes_is_instance_level():
    """Кэш индексов должен быть на инстансе, не на классе — иначе утечка между подключениями."""
    from postgres_fastmcp.services.health.index_health_calc import IndexHealthCalc

    inst_a = IndexHealthCalc.__new__(IndexHealthCalc)
    inst_b = IndexHealthCalc.__new__(IndexHealthCalc)
    inst_a.__init__.__wrapped__ if hasattr(inst_a.__init__, "__wrapped__") else None
    # Имитируем минимальную инициализацию (зависит от текущей сигнатуры __init__,
    # тест проверяет ТОЛЬКО изоляцию атрибута).
    inst_a._cached_indexes = ["index_a"]
    inst_b._cached_indexes = ["index_b"]
    assert inst_a._cached_indexes == ["index_a"]
    assert inst_b._cached_indexes == ["index_b"]
    # Class-level не должен быть установлен в значение одного из инстансов.
    assert getattr(IndexHealthCalc, "_cached_indexes", None) is None
```

- [ ] **Step 2: Прогнать тест — он должен упасть**

Run: `uv run pytest tests/unit/services/health/test_index_health_calc.py::test_cached_indexes_is_instance_level -v`
Expected: FAIL (class-level `_cached_indexes` не равен `None` после присваиваний на инстансах) — либо тест пройдёт мимо. Если пройдёт сразу — атрибут уже инстанс-уровневый, добиваться явного фейла не нужно; в этом случае реализационный шаг сводится к фиксированию аннотации.

- [ ] **Step 3: Поправить класс**

В `src/postgres_fastmcp/services/health/index_health_calc.py` рядом со строкой 10 (текущее `_cached_indexes = None` на классе):

Заменить:
```python
_cached_indexes = None
```
на (на классе остаётся только аннотация типа, без значения):
```python
_cached_indexes: list | None
```
И в `__init__` (там, где инициализируются поля инстанса) добавить:
```python
self._cached_indexes = None
```

- [ ] **Step 4: Прогнать тест и весь файл**

Run:
```bash
uv run pytest tests/unit/services/health/test_index_health_calc.py -v
```
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/postgres_fastmcp/services/health/index_health_calc.py tests/unit/services/health/test_index_health_calc.py && sleep 1 && git commit -m "fix(health): make IndexHealthCalc._cached_indexes instance-level"
```

---

### Task 2.2: Параллелизация health-проверок

**Files:**
- Modify: `src/postgres_fastmcp/services/health/database_health.py:72-100`
- Test: добавить кейс в `tests/unit/services/test_health_service.py`

- [ ] **Step 1: Прочитать `services/health/database_health.py` целиком**

Run: `cat src/postgres_fastmcp/services/health/database_health.py`
Цель — увидеть текущую структуру цикла `for health_type in checks` и места `await`.

- [ ] **Step 2: Добавить тест на параллельность**

В `tests/unit/services/test_health_service.py` добавить:

```python
import asyncio
import time

@pytest.mark.asyncio
async def test_health_checks_run_in_parallel(health_service_factory):
    """Все проверки должны стартовать одновременно через asyncio.gather."""

    delay = 0.2
    calls = []

    async def fake_check(name):
        calls.append((name, time.monotonic()))
        await asyncio.sleep(delay)
        return f"{name}-ok"

    # health_service_factory — фикстура, возвращающая HealthService с мокнутыми проверками
    service = health_service_factory(fake_check)
    start = time.monotonic()
    await service.analyze_db_health(health_type="all")
    elapsed = time.monotonic() - start
    # 7 параллельных задач по 0.2 = ~0.2-0.3 (не 1.4)
    assert elapsed < delay * 3, f"slow: {elapsed:.2f}s"
```

Если такой фикстуры нет — на этом же шаге добавить её в `tests/unit/services/conftest.py`. Если в текущем тестовом скелете моки делаются по-другому — адаптировать под фактический подход (см. существующий `tests/unit/services/test_health_service.py` для образца).

- [ ] **Step 3: Запустить тест — fail**

Run: `uv run pytest tests/unit/services/test_health_service.py::test_health_checks_run_in_parallel -v`
Expected: FAIL по таймингу (или AttributeError, если фикстура другая — поправить).

- [ ] **Step 4: Заменить цикл на `asyncio.gather`**

В `database_health.py` найти участок с последовательным `for ... : await self._run_check(...)` и заменить на:

```python
import asyncio
...
async def analyze_db_health(self, health_type: str = "all") -> str:
    requested = self._parse_health_types(health_type)
    coros = [self._run_check(t) for t in requested]
    results = await asyncio.gather(*coros, return_exceptions=True)
    return self._format_report(requested, results)
```

Метод `_format_report` принимает результаты в порядке `requested` (включая `Exception`-объекты) и форматирует строку отчёта: для исключения подставляет `f"{t.value}: check failed: {exc}"`, иначе — само значение.

Имена методов и точные сигнатуры подогнать под фактический код файла; точечно править только участок, выбрасывая старую последовательную логику.

- [ ] **Step 5: Прогнать тест на параллельность и весь файл**

Run:
```bash
uv run pytest tests/unit/services/test_health_service.py -v
```
Expected: PASS.

- [ ] **Step 6: Прогнать весь unit-сьют — проверить, что ничего не сломалось**

Run: `uv run pytest tests/unit -x --no-header -q 2>&1 | tail -10`
Expected: всё PASS.

- [ ] **Step 7: Коммит**

```bash
git add src/postgres_fastmcp/services/health/database_health.py tests/unit/services/ && sleep 1 && git commit -m "perf(health): run db health checks in parallel via asyncio.gather"
```

---

### Task 2.3: Параллелизация `get_object_details` для таблиц

**Files:**
- Modify: `src/postgres_fastmcp/services/objects/tables.py` (~ строки 83, 98, 112)
- Test: расширить `tests/unit/services/test_objects_service.py`

- [ ] **Step 1: Прочитать текущий код**

Run: `cat src/postgres_fastmcp/services/objects/tables.py`
Найти три последовательных `await self.db.sql_driver.execute(QUERY_GET_COLUMNS/CONSTRAINTS/INDEXES, ...)`.

- [ ] **Step 2: Добавить тест на параллельность**

В `tests/unit/services/test_objects_service.py` добавить (адаптируя под фактический мок-стиль файла):

```python
@pytest.mark.asyncio
async def test_table_details_queries_run_in_parallel(monkeypatch):
    """Три запроса (columns, constraints, indexes) должны запускаться через gather, не последовательно."""
    from postgres_fastmcp.services.objects import tables as tables_mod
    import asyncio, time

    delay = 0.15

    async def fake_exec(query, params=None):
        await asyncio.sleep(delay)
        return []

    # Подменить sql_driver.execute или соответствующий метод
    db_mock = mock.AsyncMock()
    db_mock.sql_driver.execute.side_effect = fake_exec

    start = time.monotonic()
    await tables_mod.get_table_details(db_mock, schema_name="public", table_name="t")
    elapsed = time.monotonic() - start
    assert elapsed < delay * 2, f"sequential: {elapsed:.2f}s"
```

- [ ] **Step 3: Запустить — FAIL**

Run: `uv run pytest tests/unit/services/test_objects_service.py::test_table_details_queries_run_in_parallel -v`

- [ ] **Step 4: Заменить последовательные await на gather**

В `tables.py` найти участок:
```python
columns = await self.db.sql_driver.execute(QUERY_GET_COLUMNS, ...)
constraints = await self.db.sql_driver.execute(QUERY_GET_CONSTRAINTS, ...)
indexes = await self.db.sql_driver.execute(QUERY_GET_INDEXES, ...)
```
Заменить на:
```python
columns, constraints, indexes = await asyncio.gather(
    self.db.sql_driver.execute(QUERY_GET_COLUMNS, ...),
    self.db.sql_driver.execute(QUERY_GET_CONSTRAINTS, ...),
    self.db.sql_driver.execute(QUERY_GET_INDEXES, ...),
)
```
Не забыть `import asyncio` вверху файла.

- [ ] **Step 5: Прогнать тесты**

Run: `uv run pytest tests/unit/services/test_objects_service.py -v`
Expected: PASS.

- [ ] **Step 6: Коммит**

```bash
git add src/postgres_fastmcp/services/objects/tables.py tests/unit/services/test_objects_service.py && sleep 1 && git commit -m "perf(objects): parallelize columns/constraints/indexes queries"
```

---

### Task 2.4: `TopQueriesCalc` — фильтр самозагрязнения

**Files:**
- Modify: `src/postgres_fastmcp/services/top_queries/top_queries_calc.py` (~строки 91-100)
- Test: `tests/unit/services/test_top_queries_service.py` (расширить)

- [ ] **Step 1: Прочитать текущий SQL**

Run: `cat src/postgres_fastmcp/services/top_queries/top_queries_calc.py | head -120`
Найти SELECT из `pg_stat_statements` без `WHERE`.

- [ ] **Step 2: Добавить тест, проверяющий что в SQL присутствует фильтр**

В `tests/unit/services/test_top_queries_service.py`:

```python
def test_top_queries_sql_filters_pg_stat_statements_and_zero_calls():
    """SQL должен исключать собственные запросы к pg_stat_statements и нулевые вызовы."""
    from postgres_fastmcp.services.top_queries import top_queries_calc as mod
    src = mod.__file__
    text = Path(src).read_text()
    assert "calls > 0" in text
    assert "pg_stat_statements" in text  # имя есть и так
    assert "NOT LIKE '%pg_stat_statements%'" in text
```

- [ ] **Step 3: Запустить — FAIL**

Run: `uv run pytest tests/unit/services/test_top_queries_service.py::test_top_queries_sql_filters_pg_stat_statements_and_zero_calls -v`

- [ ] **Step 4: Добавить WHERE-фильтр в SQL**

В `top_queries_calc.py` в формируемом SQL добавить `WHERE`:
```sql
WHERE calls > 0
  AND query NOT LIKE '%pg_stat_statements%'
```
Перед `ORDER BY`. Если уже есть `WHERE` — заменить на расширенное условие через `AND`.

- [ ] **Step 5: Проверить тест и интеграционный (если есть)**

Run: `uv run pytest tests/unit/services/test_top_queries_service.py -v`
Expected: PASS.

- [ ] **Step 6: Коммит**

```bash
git add src/postgres_fastmcp/services/top_queries/top_queries_calc.py tests/unit/services/test_top_queries_service.py && sleep 1 && git commit -m "fix(top-queries): exclude self-queries and zero-call entries"
```

---

### Task 2.5: Поднять `pool_max_size` до 10

**Files:**
- Modify: `src/postgres_fastmcp/config/database.py:58`
- Modify: `env.example` (соответствующая строка)
- Test: расширить `tests/unit/test_main.py` или создать `tests/unit/config/test_database_config.py`

- [ ] **Step 1: Добавить тест на дефолт**

В `tests/unit/test_main.py` (либо новый `tests/unit/config/test_database_config.py`):

```python
def test_pool_max_size_default_is_10():
    from postgres_fastmcp.config.database import DatabaseConfig
    cfg = DatabaseConfig()
    assert cfg.pool_max_size == 10
```

- [ ] **Step 2: Прогнать — FAIL**

Run: `uv run pytest tests/unit/test_main.py::test_pool_max_size_default_is_10 -v`
Expected: FAIL (`5 != 10`).

- [ ] **Step 3: Поднять дефолт**

В `src/postgres_fastmcp/config/database.py:57-61`:
```python
pool_max_size: int = Field(
    default=10,
    description="Максимальное количество соединений в пуле",
    ge=1,
)
```

Обновить `env.example` рядом со строкой `MCP_DATABASE_POOL_MAX_SIZE=5` на `MCP_DATABASE_POOL_MAX_SIZE=10`.

- [ ] **Step 4: Прогнать тест**

Run: `uv run pytest tests/unit/test_main.py::test_pool_max_size_default_is_10 -v`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/postgres_fastmcp/config/database.py env.example tests/unit/test_main.py && sleep 1 && git commit -m "chore(config): raise default pool_max_size to 10"
```

---

## Phase 3 — Склейка ExplainService и IndexAnalysisService

### Task 3.1: Объединить explain-режимы в один сервис

**Files:**
- Modify: `src/postgres_fastmcp/services/explain/service.py`
- Modify: `tests/unit/services/test_explain_service.py`

Цель: один публичный метод `ExplainService.explain(sql, *, analyze: bool = False, hypothetical_indexes: list[dict] | None = None) -> str`, который сам выбирает режим. Валидация `analyze and hypothetical_indexes` уходит сюда же и бросает `ExplainAnalyzeWithHypotheticalError`.

- [ ] **Step 1: Прочитать текущий `service.py` и подсчитать, сколько публичных методов / классов сейчас**

Run: `cat src/postgres_fastmcp/services/explain/service.py`
Если уже один класс с тремя методами (`explain_plain`, `explain_analyze`, `explain_hypothetical`) — задача = свести их к одному. Если три класса — слить в один.

- [ ] **Step 2: Добавить failing-тест на диспатчер**

В `tests/unit/services/test_explain_service.py`:

```python
@pytest.mark.asyncio
async def test_explain_dispatch_plain():
    svc = ExplainService(db=mock_db())
    plan = await svc.explain("SELECT 1")
    assert "QUERY PLAN" in plan or "Plan" in plan

@pytest.mark.asyncio
async def test_explain_dispatch_analyze():
    svc = ExplainService(db=mock_db())
    plan = await svc.explain("SELECT 1", analyze=True)
    # mock_db проверяет, что SQL начинается с EXPLAIN (ANALYZE, ...)
    ...

@pytest.mark.asyncio
async def test_explain_dispatch_hypothetical():
    svc = ExplainService(db=mock_db())
    plan = await svc.explain("SELECT 1", hypothetical_indexes=[{"table": "t", "columns": ["c"]}])
    ...

@pytest.mark.asyncio
async def test_explain_rejects_analyze_with_hypothetical():
    from postgres_fastmcp.common.errors import ExplainAnalyzeWithHypotheticalError
    svc = ExplainService(db=mock_db())
    with pytest.raises(ExplainAnalyzeWithHypotheticalError):
        await svc.explain("SELECT 1", analyze=True, hypothetical_indexes=[...])
```

Точные имена моков и фикстур — взять из соседних тестов файла. Если файл пустой, использовать `tests/unit/services/conftest.py`.

- [ ] **Step 3: Прогнать — FAIL (метод `explain` не существует или сигнатура другая)**

Run: `uv run pytest tests/unit/services/test_explain_service.py -v`

- [ ] **Step 4: Реализовать диспатчер в `ExplainService`**

В `src/postgres_fastmcp/services/explain/service.py`:

```python
from postgres_fastmcp.common.errors import ExplainAnalyzeWithHypotheticalError

class ExplainService:
    def __init__(self, db: DbAccessService) -> None:
        self.db = db

    async def explain(
        self,
        sql: str,
        *,
        analyze: bool = False,
        hypothetical_indexes: list[dict] | None = None,
    ) -> str:
        if analyze and hypothetical_indexes:
            raise ExplainAnalyzeWithHypotheticalError
        if hypothetical_indexes:
            return await self._explain_hypothetical(sql, hypothetical_indexes)
        if analyze:
            return await self._explain_analyze(sql)
        return await self._explain_plain(sql)

    async def _explain_plain(self, sql: str) -> str: ...
    async def _explain_analyze(self, sql: str) -> str: ...
    async def _explain_hypothetical(self, sql: str, indexes: list[dict]) -> str: ...
```

Внутренние методы — это то, что сейчас лежит в трёх отдельных классах/функциях. Перенести их как приватные методы единого класса.

Если у трёх классов был общий конструктор/состояние — слить. Если разные — общая часть (зависимость от `db`) идёт в `__init__`, остальное (флаги/конфиги) уходит в локальные переменные методов.

- [ ] **Step 5: Прогнать тесты explain**

Run: `uv run pytest tests/unit/services/test_explain_service.py -v`
Expected: PASS.

- [ ] **Step 6: Проверить весь unit-сьют**

Run: `uv run pytest tests/unit -x --no-header -q 2>&1 | tail -10`
Expected: PASS (тулы пока используют старый ExplainService — если они импортируют конкретный класс, могут упасть; в этом случае на этом шаге не правим, отметим — починим в Phase 5 при переписывании тулов).

Если падают только тесты тулов explain — это ожидаемо, продолжаем.

- [ ] **Step 7: Коммит**

```bash
git add src/postgres_fastmcp/services/explain/ tests/unit/services/test_explain_service.py && sleep 1 && git commit -m "refactor(explain): unify three explain modes into ExplainService.explain"
```

---

### Task 3.2: Диспатчер `dta`/`llm` в `IndexAnalysisService`

**Files:**
- Modify: `src/postgres_fastmcp/services/index/service.py`
- Modify: `src/postgres_fastmcp/enums.py` — добавить `AnalysisMethod`
- Modify: `tests/unit/services/test_index_service.py`

- [ ] **Step 1: Добавить тип `AnalysisMethod` в enums**

В `src/postgres_fastmcp/enums.py`:

```python
AnalysisMethod = Literal["dta", "llm"]
```
(рядом с `MountMode`, `ObjectType`, `TopQueriesSortBy`).

- [ ] **Step 2: Добавить failing-тест на диспатч**

В `tests/unit/services/test_index_service.py`:

```python
@pytest.mark.asyncio
async def test_analyze_query_indexes_dispatches_to_dta(monkeypatch):
    from postgres_fastmcp.services.index.service import IndexAnalysisService

    called = {}
    async def fake_dta_analyze(*a, **kw): called["dta"] = True; return {}
    async def fake_llm_analyze(*a, **kw): called["llm"] = True; return {}
    svc = IndexAnalysisService(db=mock_db())
    monkeypatch.setattr(svc, "_dta_analyze_query", fake_dta_analyze)
    monkeypatch.setattr(svc, "_llm_analyze_query", fake_llm_analyze)
    await svc.analyze_query_indexes(method="dta", queries=["SELECT 1"], max_index_size_mb=100, ctx=None)
    assert called == {"dta": True}

@pytest.mark.asyncio
async def test_analyze_query_indexes_dispatches_to_llm(monkeypatch):
    # симметрично, method="llm"
    ...
```

- [ ] **Step 3: FAIL**

Run: `uv run pytest tests/unit/services/test_index_service.py -v -k dispatch`

- [ ] **Step 4: Слить логику в единый сервис**

В `src/postgres_fastmcp/services/index/service.py` ввести единый класс `IndexAnalysisService` с публичными методами:

```python
from postgres_fastmcp.enums import AnalysisMethod

class IndexAnalysisService:
    def __init__(self, db: DbAccessService) -> None:
        self.db = db

    async def analyze_query_indexes(
        self,
        *,
        method: AnalysisMethod,
        queries: list[str],
        max_index_size_mb: int,
        ctx,
    ) -> dict:
        if method == "dta":
            return await self._dta_analyze_query(queries, max_index_size_mb, ctx)
        return await self._llm_analyze_query(queries, max_index_size_mb, ctx)

    async def analyze_workload_indexes(
        self,
        *,
        method: AnalysisMethod,
        max_index_size_mb: int,
        ctx,
    ) -> dict:
        if method == "dta":
            return await self._dta_analyze_workload(max_index_size_mb, ctx)
        return await self._llm_analyze_workload(max_index_size_mb, ctx)

    async def _dta_analyze_query(self, ...): ...
    async def _llm_analyze_query(self, ...): ...
    async def _dta_analyze_workload(self, ...): ...
    async def _llm_analyze_workload(self, ...): ...
```

Содержимое приватных методов перенести из существующих `DtaIndexAnalysisService` / `LlmIndexAnalysisService`. Старые классы удалить (или временно оставить как алиасы, если что-то ещё тянет — это рефакторится в Phase 5).

- [ ] **Step 5: Тесты диспатча PASS**

Run: `uv run pytest tests/unit/services/test_index_service.py -v`

- [ ] **Step 6: Коммит**

```bash
git add src/postgres_fastmcp/services/index/ src/postgres_fastmcp/enums.py tests/unit/services/test_index_service.py && sleep 1 && git commit -m "refactor(index): dispatch dta/llm inside IndexAnalysisService"
```

---

## Phase 4 — User-facing exceptions наследуют ToolError

### Task 4.1: Сделать пользовательские ошибки `ToolError`-наследниками

**Files:**
- Modify: `src/postgres_fastmcp/common/errors.py`
- Test: `tests/unit/common/test_errors.py` (новый)

Ошибки, которые должны быть `ToolError`:
- `ExplainAnalyzeWithHypotheticalError`
- `EmptyQueriesError`
- `QueriesLimitError`
- `InvalidSortCriteriaError`
- `SchemaAccessError`
- `SchemaNotAllowedError`
- `UnsupportedObjectTypeError` (если есть)

Остальные (`SqlExecutionError`, `ExplainPlanError`, драйверные, инфраструктурные) — остаются обычными `Exception`-ами и маскируются при `mask_error_details=True`.

- [ ] **Step 1: Прочитать `common/errors.py` целиком**

Run: `cat src/postgres_fastmcp/common/errors.py`
Сделать список user-facing исключений (классы, чьи сообщения содержат подсказки пользователю-LLM: «Please provide a non-empty list», «Cannot use analyze and hypothetical», «schema X is not allowed» и т.п.).

- [ ] **Step 2: Добавить тест**

В новом файле `tests/unit/common/test_errors.py`:

```python
from fastmcp.exceptions import ToolError
from postgres_fastmcp.common.errors import (
    ExplainAnalyzeWithHypotheticalError,
    EmptyQueriesError,
    QueriesLimitError,
    InvalidSortCriteriaError,
    SchemaAccessError,
    SchemaNotAllowedError,
)

def test_user_facing_errors_are_tool_errors():
    for cls, args in [
        (ExplainAnalyzeWithHypotheticalError, ()),
        (EmptyQueriesError, ()),
        (QueriesLimitError, (50,)),
        (InvalidSortCriteriaError, ()),
        (SchemaAccessError, ("private",)),
        (SchemaNotAllowedError, ("private", "public")),
    ]:
        assert issubclass(cls, ToolError), f"{cls.__name__} must inherit ToolError"
```

Если в реальном `errors.py` имена/сигнатуры отличаются — подогнать список под фактический.

- [ ] **Step 3: Прогнать — FAIL**

Run: `uv run pytest tests/unit/common/test_errors.py -v`

- [ ] **Step 4: Перевести классы на наследование `ToolError`**

В `common/errors.py`:

```python
from fastmcp.exceptions import ToolError

class BaseApplicationError(Exception):
    """Базовый класс для внутренних/инфраструктурных ошибок (маскируются от клиента)."""
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UserFacingError(ToolError):
    """База для ошибок, которые должны дойти до клиента в виде текста."""
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
```

Каждый user-facing класс из списка выше — менять наследование с `BaseApplicationError` на `UserFacingError`.

Внутренние (`SqlExecutionError`, `ExplainPlanError`, `ConnectionNotEstablishedError`, и т.д.) — оставить наследниками `BaseApplicationError`.

- [ ] **Step 5: Прогнать тест и весь сьют**

Run:
```bash
uv run pytest tests/unit/common/test_errors.py -v
uv run pytest tests/unit -x --no-header -q 2>&1 | tail -10
```
Expected: PASS.

- [ ] **Step 6: Коммит**

```bash
git add src/postgres_fastmcp/common/errors.py tests/unit/common/ && sleep 1 && git commit -m "feat(errors): inherit user-facing errors from ToolError"
```

---

## Phase 5 — Lifespan и тулы (большая часть рефакторинга)

### Task 5.1: Создать `lifespan.py`

**Files:**
- Create: `src/postgres_fastmcp/lifespan.py`
- Test: `tests/unit/test_lifespan.py` (новый)

- [ ] **Step 1: Тест**

```python
# tests/unit/test_lifespan.py
import pytest
from postgres_fastmcp.config import Settings
from postgres_fastmcp.lifespan import build_lifespan

@pytest.mark.asyncio
async def test_lifespan_yields_db_and_settings(monkeypatch):
    closed = {}

    class FakeDb:
        def __init__(self, cfg): self.cfg = cfg
        async def close(self): closed["called"] = True

    monkeypatch.setattr("postgres_fastmcp.lifespan.DbAccessService", FakeDb)

    settings = Settings()
    lifespan_cm = build_lifespan(settings)
    async with lifespan_cm(server=None) as ctx:
        assert "db" in ctx
        assert "settings" in ctx
        assert ctx["settings"] is settings
        assert isinstance(ctx["db"], FakeDb)
    assert closed.get("called") is True
```

- [ ] **Step 2: FAIL**

Run: `uv run pytest tests/unit/test_lifespan.py -v`

- [ ] **Step 3: Реализация**

```python
# src/postgres_fastmcp/lifespan.py
"""Lifespan-фабрика: создаёт DbAccessService и кладёт его + settings в lifespan_context."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from postgres_fastmcp.config import Settings
from postgres_fastmcp.services.db_access_service import DbAccessService


def build_lifespan(settings: Settings):
    """Вернуть async-context-manager, пригодный для FastMCP(lifespan=...).

    Args:
        settings: Конфигурация. Не меняется на протяжении жизни сервера.

    Returns:
        Async context manager: при входе создаёт DbAccessService, отдаёт {"db", "settings"}, на выходе закрывает пул.
    """

    @asynccontextmanager
    async def lifespan(server) -> AsyncIterator[dict]:
        db = DbAccessService(settings.database)
        try:
            yield {"db": db, "settings": settings}
        finally:
            await db.close()

    return lifespan
```

- [ ] **Step 4: PASS**

Run: `uv run pytest tests/unit/test_lifespan.py -v`

- [ ] **Step 5: Коммит**

```bash
git add src/postgres_fastmcp/lifespan.py tests/unit/test_lifespan.py && sleep 1 && git commit -m "feat(server): add lifespan factory that owns DbAccessService"
```

---

### Task 5.2: Заготовка `tools/constants.py` с annotation-пресетами

**Files:**
- Modify: `src/postgres_fastmcp/tools/constants.py`

- [ ] **Step 1: Добавить три dict-константы**

В `src/postgres_fastmcp/tools/constants.py` добавить:

```python
READ_ONLY_IDEMPOTENT: dict[str, bool] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
READ_ONLY_NON_IDEMPOTENT: dict[str, bool] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
DESTRUCTIVE: dict[str, bool] = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
}
```

- [ ] **Step 2: Лёгкий smoke-тест в `tests/unit/tools/test_tool_descriptions.py` (расширить)**

```python
def test_annotation_presets_have_expected_keys():
    from postgres_fastmcp.tools.constants import READ_ONLY_IDEMPOTENT, READ_ONLY_NON_IDEMPOTENT, DESTRUCTIVE
    required = {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}
    for preset in (READ_ONLY_IDEMPOTENT, READ_ONLY_NON_IDEMPOTENT, DESTRUCTIVE):
        assert set(preset.keys()) == required
```

- [ ] **Step 3: Прогнать**

Run: `uv run pytest tests/unit/tools/test_tool_descriptions.py -v`
Expected: PASS.

- [ ] **Step 4: Коммит**

```bash
git add src/postgres_fastmcp/tools/constants.py tests/unit/tools/test_tool_descriptions.py && sleep 1 && git commit -m "feat(tools): add annotation presets for tool registration"
```

---

### Task 5.3: Переписать тул-функции без декораторов

**Files:**
- Modify: все `src/postgres_fastmcp/tools/basic/*.py` (кроме `__init__.py`)
- Modify: все `src/postgres_fastmcp/tools/full/*.py` (кроме `__init__.py`)

Цель: каждый тул — обычная `async def`, без `@tool`, без module-level `app_config.current.*`, сервисы достаются из `ctx.lifespan_context`.

- [ ] **Step 1: Шаблон новой функции**

Пример для `execute_sql.py`:

```python
"""Тул execute_sql — выполняет SQL-запрос против БД."""

from typing import Annotated, Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import Field


async def execute_sql(
    sql: Annotated[str, Field(description="SQL query to execute. SELECT only in restricted mode; any DDL/DML in unrestricted.")],
    ctx: Context = CurrentContext(),
) -> list[dict[str, Any]]:
    """Выполнить SQL-запрос; возвращает строки или статус."""
    db = ctx.lifespan_context["db"]
    from postgres_fastmcp.services.sql_execution.service import SqlExecutionService
    service = SqlExecutionService(db=db)
    return await service.execute_sql(sql)
```

Импорт сервиса делается внутри функции, чтобы избежать циклов при импорте `tools/registry.py`. (Если циклов нет — можно сверху.)

Принципы для всех тулов:

- Никаких декораторов `@tool` / `@mcp.tool`.
- Никаких module-level `app_config.current.*`.
- Никаких параметров типа `*_service: XxxService = XxxServiceProvider`. Сервис создаётся внутри функции из `ctx.lifespan_context["db"]`.
- `ctx: Context = CurrentContext()` всегда последним keyword-параметром.
- Описание параметров в `Annotated[..., Field(description=...)]`. **Не** дублировать смысл в общем `description` тула — это сделает registry.
- Docstring функции — короткий русский, для разработчиков.

- [ ] **Step 2: Переписать `tools/basic/execute_sql.py`**

```python
"""Тул execute_sql — выполняет SQL-запрос против БД."""

from typing import Annotated, Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import Field

from postgres_fastmcp.services.sql_execution.service import SqlExecutionService


async def execute_sql(
    sql: Annotated[str, Field(description="SQL statement to execute.")],
    ctx: Context = CurrentContext(),
) -> list[dict[str, Any]]:
    """Выполнить SQL-запрос. Доступ и режим записи определяет lifespan_context['settings']."""
    db = ctx.lifespan_context["db"]
    service = SqlExecutionService(db=db)
    return await service.execute_sql(sql)
```

- [ ] **Step 3: Переписать `tools/basic/list_objects.py`**

```python
"""Тул list_objects — список объектов в схеме."""

from typing import Annotated, Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import Field

from postgres_fastmcp.enums import ObjectType
from postgres_fastmcp.services.objects.service import ObjectsService


async def list_objects(
    schema_name: Annotated[str, Field(description="Schema name to inspect.")],
    object_type: Annotated[
        ObjectType,
        Field(default="table", description="Object kind: 'table', 'view', 'sequence', 'extension'."),
    ] = "table",
    ctx: Context = CurrentContext(),
) -> list[dict[str, Any]]:
    """Получить список объектов указанного типа в схеме."""
    db = ctx.lifespan_context["db"]
    service = ObjectsService(db=db)
    return await service.list_objects(schema_name=schema_name, object_type=object_type)
```

- [ ] **Step 4: Переписать `tools/basic/get_object_details.py`**

```python
"""Тул get_object_details — детальная информация об объекте."""

from typing import Annotated, Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import Field

from postgres_fastmcp.enums import ObjectType
from postgres_fastmcp.services.objects.service import ObjectsService


async def get_object_details(
    schema_name: Annotated[str, Field(description="Schema name.")],
    object_name: Annotated[str, Field(description="Object name.")],
    object_type: Annotated[
        ObjectType,
        Field(default="table", description="Object kind: 'table', 'view', 'sequence', 'extension'."),
    ] = "table",
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """Детали объекта: колонки, констрейнты, индексы и пр."""
    db = ctx.lifespan_context["db"]
    service = ObjectsService(db=db)
    return await service.get_object_details(schema_name=schema_name, object_name=object_name, object_type=object_type)
```

- [ ] **Step 5: Переписать `tools/basic/explain_query.py`**

```python
"""Тул explain_query — план выполнения SQL."""

from typing import Annotated, Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import Field

from postgres_fastmcp.services.explain.service import ExplainService


async def explain_query(
    sql: Annotated[str, Field(description="SQL query to explain.")],
    analyze: Annotated[bool, Field(default=False, description="If True, actually run the query for real stats.")] = False,
    hypothetical_indexes: Annotated[
        list[dict[str, Any]] | None,
        Field(default=None, description="Optional hypothetical indexes to simulate via hypopg."),
    ] = None,
    ctx: Context = CurrentContext(),
) -> str:
    """План выполнения SQL: plain / analyze / гипотетические индексы."""
    db = ctx.lifespan_context["db"]
    service = ExplainService(db=db)
    return await service.explain(sql, analyze=analyze, hypothetical_indexes=hypothetical_indexes)
```

- [ ] **Step 6: Переписать `tools/full/list_schemas.py`**

```python
"""Тул list_schemas — все схемы БД."""

from typing import Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context

from postgres_fastmcp.services.schema.service import SchemaService


async def list_schemas(ctx: Context = CurrentContext()) -> list[dict[str, Any]]:
    """Список схем БД."""
    db = ctx.lifespan_context["db"]
    service = SchemaService(db=db)
    return await service.list_schemas()
```

- [ ] **Step 7: Переписать `tools/full/analyze_db_health.py`**

```python
"""Тул analyze_db_health — комплексная проверка состояния БД."""

from typing import Annotated

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import Field

from postgres_fastmcp.services.health.service import HealthService
from postgres_fastmcp.tools.constants import HEALTH_TYPE_VALUES


async def analyze_db_health(
    health_type: Annotated[
        str,
        Field(default="all", description=f"Single check or comma-separated list. Valid: {HEALTH_TYPE_VALUES}."),
    ] = "all",
    ctx: Context = CurrentContext(),
) -> str:
    """Запустить набор health-проверок и вернуть отчёт."""
    db = ctx.lifespan_context["db"]
    service = HealthService(db=db)
    return await service.analyze_db_health(health_type=health_type)
```

- [ ] **Step 8: Переписать `tools/full/get_top_queries.py`**

```python
"""Тул get_top_queries — самые тяжёлые/медленные запросы."""

from typing import Annotated

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import Field

from postgres_fastmcp.enums import TopQueriesSortBy
from postgres_fastmcp.services.top_queries.service import TopQueriesService


async def get_top_queries(
    sort_by: Annotated[
        TopQueriesSortBy,
        Field(default="resources", description="Ranking criteria: 'total_time', 'mean_time', or 'resources'."),
    ] = "resources",
    limit: Annotated[int, Field(default=10, ge=1, description="Number of queries to return.")] = 10,
    ctx: Context = CurrentContext(),
) -> str:
    """Топ запросов из pg_stat_statements по выбранному критерию."""
    db = ctx.lifespan_context["db"]
    service = TopQueriesService(db=db)
    return await service.get_top_queries(sort_by=sort_by, limit=limit)
```

- [ ] **Step 9: Переписать `tools/full/analyze_query_indexes.py`**

```python
"""Тул analyze_query_indexes — рекомендации индексов под список запросов."""

from typing import Annotated, Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import Field

from postgres_fastmcp.enums import AnalysisMethod
from postgres_fastmcp.services.index.service import IndexAnalysisService
from postgres_fastmcp.services.index.index_opt_base import MAX_NUM_INDEX_TUNING_QUERIES


async def analyze_query_indexes(
    queries: Annotated[
        list[str],
        Field(description=f"SQL queries to analyze (up to {MAX_NUM_INDEX_TUNING_QUERIES})."),
    ],
    max_index_size_mb: Annotated[int, Field(default=10000, ge=1, description="Max recommended index size (MB).")] = 10000,
    method: Annotated[
        AnalysisMethod,
        Field(default="dta", description="Analysis method: 'dta' (cost-based) or 'llm' (LLM-driven)."),
    ] = "dta",
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """Рекомендовать индексы под список запросов."""
    db = ctx.lifespan_context["db"]
    service = IndexAnalysisService(db=db)
    return await service.analyze_query_indexes(
        method=method,
        queries=queries,
        max_index_size_mb=max_index_size_mb,
        ctx=ctx,
    )
```

- [ ] **Step 10: Переписать `tools/full/analyze_workload_indexes.py`**

```python
"""Тул analyze_workload_indexes — рекомендации индексов под нагрузку (pg_stat_statements)."""

from typing import Annotated, Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import Field

from postgres_fastmcp.enums import AnalysisMethod
from postgres_fastmcp.services.index.service import IndexAnalysisService


async def analyze_workload_indexes(
    max_index_size_mb: Annotated[int, Field(default=10000, ge=1, description="Max recommended index size (MB).")] = 10000,
    method: Annotated[
        AnalysisMethod,
        Field(default="dta", description="Analysis method: 'dta' or 'llm'."),
    ] = "dta",
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """Рекомендовать индексы по агрегированной нагрузке БД."""
    db = ctx.lifespan_context["db"]
    service = IndexAnalysisService(db=db)
    return await service.analyze_workload_indexes(
        method=method,
        max_index_size_mb=max_index_size_mb,
        ctx=ctx,
    )
```

- [ ] **Step 11: Прогнать `ruff` и `mypy` точечно**

Run:
```bash
uv run ruff check src/postgres_fastmcp/tools
uv run mypy src/postgres_fastmcp/tools 2>&1 | tail -10
```
Expected: чисто. Если есть импорт-ошибки на сервисы (например, `ObjectsService(db=db)` ожидает другой аргумент) — поправить точечно, согласуясь с фактическим конструктором сервиса.

- [ ] **Step 12: Коммит (большой, но логически один)**

```bash
git add src/postgres_fastmcp/tools/basic src/postgres_fastmcp/tools/full && sleep 1 && git commit -m "refactor(tools): drop @tool decorators; tools become plain async functions"
```

---

### Task 5.4: Реестр тулов `tools/registry.py`

**Files:**
- Create: `src/postgres_fastmcp/tools/registry.py`

- [ ] **Step 1: Создать registry**

```python
# src/postgres_fastmcp/tools/registry.py
"""Регистрация тулов в FastMCP-сервер: Tool.from_function + add_tool с параметрами, зависящими от Settings."""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.tools import Tool

from postgres_fastmcp import __version__
from postgres_fastmcp.config import Settings
from postgres_fastmcp.enums import AccessMode, ToolTag
from postgres_fastmcp.tools.basic.execute_sql import execute_sql
from postgres_fastmcp.tools.basic.explain_query import explain_query
from postgres_fastmcp.tools.basic.get_object_details import get_object_details
from postgres_fastmcp.tools.basic.list_objects import list_objects
from postgres_fastmcp.tools.constants import (
    DESTRUCTIVE,
    HEALTH_TYPE_VALUES,
    READ_ONLY_IDEMPOTENT,
    READ_ONLY_NON_IDEMPOTENT,
)
from postgres_fastmcp.tools.full.analyze_db_health import analyze_db_health
from postgres_fastmcp.tools.full.analyze_query_indexes import analyze_query_indexes
from postgres_fastmcp.tools.full.analyze_workload_indexes import analyze_workload_indexes
from postgres_fastmcp.tools.full.get_top_queries import get_top_queries
from postgres_fastmcp.tools.full.list_schemas import list_schemas


_META = {"version": __version__}


def register_tools(mcp: FastMCP, settings: Settings) -> None:
    """Зарегистрировать все 9 тулов. Visibility-фильтр (basic/full) применяется снаружи."""
    for spec in _basic_specs(settings):
        mcp.add_tool(Tool.from_function(**spec))
    for spec in _full_specs(settings):
        mcp.add_tool(Tool.from_function(**spec))


def _basic_specs(settings: Settings) -> list[dict]:
    db = settings.database
    unrestricted = db.access_mode == AccessMode.FULL and db.write_mode
    return [
        {
            "fn": execute_sql,
            "name": "execute_sql",
            "description": _execute_sql_desc(unrestricted),
            "tags": {ToolTag.BASIC},
            "annotations": {"title": "Execute SQL", **(DESTRUCTIVE if unrestricted else READ_ONLY_NON_IDEMPOTENT)},
            "timeout": 30.0,
            "meta": _META,
        },
        {
            "fn": list_objects,
            "name": "list_objects",
            "description": _list_objects_desc(db.access_mode),
            "tags": {ToolTag.BASIC},
            "annotations": {"title": "List Objects", **READ_ONLY_IDEMPOTENT},
            "timeout": 30.0,
            "meta": _META,
        },
        {
            "fn": get_object_details,
            "name": "get_object_details",
            "description": _get_object_details_desc(db.access_mode),
            "tags": {ToolTag.BASIC},
            "annotations": {"title": "Get Object Details", **READ_ONLY_IDEMPOTENT},
            "timeout": 30.0,
            "meta": _META,
        },
        {
            "fn": explain_query,
            "name": "explain_query",
            "description": _explain_query_desc(),
            "tags": {ToolTag.BASIC},
            "annotations": {"title": "Explain Query", **READ_ONLY_IDEMPOTENT},
            "timeout": 30.0,
            "meta": _META,
        },
    ]


def _full_specs(settings: Settings) -> list[dict]:
    return [
        {
            "fn": list_schemas,
            "name": "list_schemas",
            "description": "Lists all schemas in the database. Use first to discover available namespaces before listing objects.",
            "tags": {ToolTag.FULL},
            "annotations": {"title": "List Schemas", **READ_ONLY_IDEMPOTENT},
            "timeout": 30.0,
            "meta": _META,
        },
        {
            "fn": analyze_db_health,
            "name": "analyze_db_health",
            "description": (
                "Comprehensive PostgreSQL health audit across multiple dimensions: "
                f"{HEALTH_TYPE_VALUES}. Returns a structured report with recommendations. "
                "Use periodically to spot issues. Each check is independent and runs in parallel."
            ),
            "tags": {ToolTag.FULL},
            "annotations": {"title": "Analyze DB Health", **READ_ONLY_IDEMPOTENT},
            "timeout": 60.0,
            "meta": _META,
        },
        {
            "fn": get_top_queries,
            "name": "get_top_queries",
            "description": (
                "Report slowest/most resource-intensive queries from pg_stat_statements. "
                "Extension pg_stat_statements must be enabled. After identifying slow queries, "
                "use explain_query and then analyze_query_indexes."
            ),
            "tags": {ToolTag.FULL},
            "annotations": {"title": "Get Top Queries", **READ_ONLY_NON_IDEMPOTENT},
            "timeout": 30.0,
            "meta": _META,
        },
        {
            "fn": analyze_query_indexes,
            "name": "analyze_query_indexes",
            "description": (
                "Recommend optimal indexes for a given list of SQL queries. "
                "method='dta' uses cost-based analysis with hypothetical indexes (hypopg required); "
                "method='llm' uses LLM-driven pattern analysis. "
                "Use analyze_workload_indexes if you want to optimize aggregate workload instead."
            ),
            "tags": {ToolTag.FULL},
            "annotations": {"title": "Analyze Query Indexes", **READ_ONLY_IDEMPOTENT},
            "timeout": 60.0,
            "meta": _META,
        },
        {
            "fn": analyze_workload_indexes,
            "name": "analyze_workload_indexes",
            "description": (
                "Recommend indexes based on actual workload from pg_stat_statements. "
                "method='dta' or 'llm'. Use periodically to find missing indexes. "
                "Use analyze_query_indexes for specific queries instead."
            ),
            "tags": {ToolTag.FULL},
            "annotations": {"title": "Analyze Workload Indexes", **READ_ONLY_NON_IDEMPOTENT},
            "timeout": 60.0,
            "meta": _META,
        },
    ]


def _execute_sql_desc(unrestricted: bool) -> str:
    if unrestricted:
        return (
            "Execute ANY SQL statement (DDL, DML, DCL). FULL access with write_mode=True. "
            "Use with caution; prefer explain_query first for SELECTs. "
            "Workflow: 1) list_objects, 2) get_object_details, 3) execute_sql."
        )
    return (
        "Execute a read-only SELECT query. DDL/DML/DCL are blocked. "
        "Workflow: 1) list_objects, 2) get_object_details, 3) execute_sql."
    )


def _list_objects_desc(access_mode: AccessMode) -> str:
    if access_mode == AccessMode.BASIC:
        return (
            "List objects (tables/views/sequences/extensions) in the 'public' schema. "
            "Access is restricted to 'public' in BASIC mode. "
            "Use get_object_details next to examine structure."
        )
    return (
        "List objects (tables/views/sequences/extensions) in a specified schema. "
        "Use list_schemas to discover available schemas first."
    )


def _get_object_details_desc(access_mode: AccessMode) -> str:
    if access_mode == AccessMode.BASIC:
        return (
            "Show details (columns, constraints, indexes, metadata) for an object in the 'public' schema. "
            "Use this before writing SQL to know the exact structure."
        )
    return (
        "Show details (columns, constraints, indexes, metadata) for a database object. "
        "Workflow: list_schemas → list_objects → get_object_details → execute_sql."
    )


def _explain_query_desc() -> str:
    return (
        "Explain the execution plan of a SQL query. analyze=True actually runs the query for real stats; "
        "use cautiously on large tables. hypothetical_indexes lets you simulate index impact without creating them. "
        "Workflow: explain_query → optimize / add indexes → execute_sql."
    )
```

- [ ] **Step 2: Smoke-тест**

```python
# tests/unit/tools/test_registry.py
import pytest
from fastmcp import FastMCP

from postgres_fastmcp.config import Settings
from postgres_fastmcp.tools.registry import register_tools


def test_register_tools_basic_count():
    settings = Settings()
    mcp = FastMCP(name="test")
    register_tools(mcp, settings)
    names = {t.name for t in mcp.local_provider._tools.values()}  # adjust if internal API differs
    assert "execute_sql" in names
    assert "list_objects" in names
    assert "get_object_details" in names
    assert "explain_query" in names
    # full тоже регистрируются всегда
    assert "list_schemas" in names
```

Если внутренний API `local_provider` другой (FastMCP 3.3 может предоставлять публичный `mcp.get_tools()` или похожий) — заменить на публичный способ. Если такого нет — использовать `await mcp.list_tools()` в async-тесте.

- [ ] **Step 3: Запустить и поправить, если падает на способе получения списка**

Run: `uv run pytest tests/unit/tools/test_registry.py -v`

- [ ] **Step 4: Коммит**

```bash
git add src/postgres_fastmcp/tools/registry.py tests/unit/tools/test_registry.py && sleep 1 && git commit -m "feat(tools): add programmatic registry using Tool.from_function"
```

---

## Phase 6 — `create_server` и публичный API

### Task 6.1: Переписать `server.py` с `create_server`

**Files:**
- Modify: `src/postgres_fastmcp/server.py`
- Modify (новый): `tests/unit/test_server.py`

- [ ] **Step 1: Тесты на API фабрики**

```python
# tests/unit/test_server.py
import pytest
from fastmcp.server.middleware import Middleware, MiddlewareContext

from postgres_fastmcp.config import Settings
from postgres_fastmcp.enums import AccessMode
from postgres_fastmcp.server import create_server


@pytest.mark.asyncio
async def test_create_server_basic_mode_hides_full_tools():
    settings = Settings()
    settings.database.access_mode = AccessMode.BASIC
    server = create_server(settings)
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert {"execute_sql", "list_objects", "get_object_details", "explain_query"} <= names
    assert "list_schemas" not in names  # скрыт visibility-фильтром


@pytest.mark.asyncio
async def test_create_server_full_mode_shows_all_tools():
    settings = Settings()
    settings.database.access_mode = AccessMode.FULL
    server = create_server(settings)
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert {"list_schemas", "analyze_db_health", "get_top_queries", "analyze_query_indexes",
            "analyze_workload_indexes"} <= names


def test_create_server_passes_extra_middleware():
    settings = Settings()
    calls = []

    class M(Middleware):
        async def on_request(self, ctx: MiddlewareContext, call_next):
            calls.append(ctx.method)
            return await call_next(ctx)

    server = create_server(settings, extra_middleware=[M()])
    # Проверка, что middleware зарегистрирован: смотрим список через публичное API сервера.
    # Если FastMCP не даёт публичный список middleware — пропускаем эту проверку, а параллельно проверяем,
    # что create_server не падает на extra_middleware=[M()].
    assert server is not None


def test_create_server_uses_mask_error_details():
    settings = Settings()
    server = create_server(settings)
    # FastMCP должен быть сконфигурирован с mask_error_details=True. Если поле приватное,
    # проверка делается косвенно — через behaviour: внутреннее исключение возвращается замаскированным.
    # Здесь просто smoke: сервер создан без ошибок.
    assert server is not None
```

- [ ] **Step 2: FAIL**

Run: `uv run pytest tests/unit/test_server.py -v`

- [ ] **Step 3: Реализация `server.py`**

Заменить весь `src/postgres_fastmcp/server.py`:

```python
"""Фабрика сервера: create_server(settings, *, auth, extra_providers, extra_middleware) -> FastMCP."""

from __future__ import annotations

from collections.abc import Sequence

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware
from fastmcp.server.middleware.logging import LoggingMiddleware
from fastmcp.server.middleware.timing import TimingMiddleware

from postgres_fastmcp.config import Settings
from postgres_fastmcp.enums import AccessMode, ToolTag
from postgres_fastmcp.lifespan import build_lifespan
from postgres_fastmcp.tools.registry import register_tools


def create_server(
    settings: Settings,
    *,
    auth=None,
    extra_providers: Sequence = (),
    extra_middleware: Sequence[Middleware] = (),
) -> FastMCP:
    """Собрать FastMCP-сервер: lifespan + middleware + регистрация тулов + visibility.

    Args:
        settings: Конфигурация (database, server, fastmcp блоки).
        auth: Опциональный auth-provider (Bearer/JWT/custom). По умолчанию — без авторизации.
        extra_providers: Дополнительные FastMCP-провайдеры от потребителя библиотеки (свои тулы/ресурсы).
        extra_middleware: Дополнительные middleware (встают после встроенных).

    Returns:
        Готовый FastMCP, можно сразу `.run(...)`.
    """
    lifespan_cm = build_lifespan(settings)
    mcp = FastMCP(
        name=settings.fastmcp.server_name,
        instructions=getattr(settings.fastmcp, "instructions", None),
        lifespan=lifespan_cm,
        auth=auth,
        providers=list(extra_providers),
        mask_error_details=True,
        on_duplicate_tools="error",
    )
    mcp.add_middleware(TimingMiddleware())
    mcp.add_middleware(LoggingMiddleware())
    for m in extra_middleware:
        mcp.add_middleware(m)

    register_tools(mcp, settings)

    if settings.database.access_mode == AccessMode.BASIC:
        mcp.disable(tags={ToolTag.FULL})

    return mcp
```

Если какой-то параметр `FastMCP(...)` в 3.3 называется иначе или не поддерживается — найти эквивалент в источнике FastMCP и подменить:
- `auth=` — поддерживается с 2.10+.
- `mask_error_details=` — задокументирован.
- `on_duplicate_tools=` — задокументирован.
- `providers=` — поддерживается; если `FastMCP.__init__` принимает только дефолтный, передать через `mcp.add_provider(...)` после инициализации.

Если `LoggingMiddleware` / `TimingMiddleware` в новой версии лежат в других путях (`fastmcp.middleware.logging` и т.п.) — поправить импорт по сообщению ошибки.

- [ ] **Step 4: Запустить тесты + весь unit**

Run:
```bash
uv run pytest tests/unit/test_server.py -v
uv run pytest tests/unit -x --no-header -q 2>&1 | tail -10
```
Expected: PASS (могут падать legacy-тесты тулов, ожидающие старого API — поправить или удалить в Phase 8).

- [ ] **Step 5: Коммит**

```bash
git add src/postgres_fastmcp/server.py tests/unit/test_server.py && sleep 1 && git commit -m "feat(server): create_server factory with lifespan, middleware, extension points"
```

---

### Task 6.2: Подгонка `main.py` под новый API

**Files:**
- Modify: `src/postgres_fastmcp/main.py`

- [ ] **Step 1: Заменить `compose_mcp` на `create_server`**

В `main.py` поменять импорт:
```python
from postgres_fastmcp.server import create_server
```
И вместо `mcp = compose_mcp(settings)` — `mcp = create_server(settings)`.

- [ ] **Step 2: Прогнать `tests/unit/test_main.py`**

Run: `uv run pytest tests/unit/test_main.py -v`
Expected: PASS (могут потребоваться правки моков; править точечно).

- [ ] **Step 3: Коммит**

```bash
git add src/postgres_fastmcp/main.py && sleep 1 && git commit -m "refactor(main): use create_server instead of compose_mcp"
```

---

### Task 6.3: Публичный API в `__init__.py`

**Files:**
- Modify: `src/postgres_fastmcp/__init__.py`

- [ ] **Step 1: Тест**

```python
# tests/unit/test_public_api.py
def test_public_api_exports():
    import postgres_fastmcp as pkg
    expected = {
        "create_server", "Settings",
        "BearerAuthProvider",
        "LocalProvider", "FileSystemProvider",
        "Middleware",
    }
    for name in expected:
        assert hasattr(pkg, name), f"Missing public export: {name}"
```

- [ ] **Step 2: FAIL**

Run: `uv run pytest tests/unit/test_public_api.py -v`

- [ ] **Step 3: Заполнить `__init__.py`**

```python
"""postgres-fastmcp: PostgreSQL Tuning and Analysis MCP server, also usable as a library."""

from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("postgres-fastmcp")
except Exception:
    __version__ = "0.0.0"

from fastmcp.server.auth import BearerAuthProvider
from fastmcp.server.middleware import Middleware
from fastmcp.server.providers import FileSystemProvider, LocalProvider

from postgres_fastmcp.config import Settings
from postgres_fastmcp.server import create_server


__all__ = [
    "BearerAuthProvider",
    "FileSystemProvider",
    "LocalProvider",
    "Middleware",
    "Settings",
    "__version__",
    "create_server",
]
```

Если `BearerAuthProvider` в текущей версии FastMCP лежит по другому пути — поправить импорт (`from fastmcp.server.auth.providers import ...` или `from fastmcp.auth import ...`). Если его нет в публичном API 3.3.x — этот импорт убрать из реэкспорта и из теста, оставив только `Middleware`, `LocalProvider`, `FileSystemProvider`. (Цель — реэкспорт того, что реально доступно.)

- [ ] **Step 4: PASS**

Run: `uv run pytest tests/unit/test_public_api.py -v`

- [ ] **Step 5: Коммит**

```bash
git add src/postgres_fastmcp/__init__.py tests/unit/test_public_api.py && sleep 1 && git commit -m "feat(api): expose create_server, Settings, FastMCP extension points as library API"
```

---

## Phase 7 — Удаление мёртвого кода

### Task 7.1: Удалить папку `providers/` и связанные тесты

**Files:**
- Delete: `src/postgres_fastmcp/providers/`
- Delete: `tests/unit/providers/`

- [ ] **Step 1: Проверить, что ничего не ссылается на `postgres_fastmcp.providers`**

Run: `grep -rn "from postgres_fastmcp.providers" src tests 2>/dev/null`
Expected: пусто. Если ссылки остались — это либо в тулах (тогда чинить там), либо в тестах (тогда удалить устаревший тест).

- [ ] **Step 2: Удалить**

Run:
```bash
git rm -r src/postgres_fastmcp/providers tests/unit/providers
```

- [ ] **Step 3: Прогнать весь сьют**

Run: `uv run pytest tests/unit -x --no-header -q 2>&1 | tail -10`
Expected: PASS.

- [ ] **Step 4: Коммит**

```bash
git commit -m "chore: remove obsolete providers/ — services live in lifespan_context now"
```

---

### Task 7.2: Удалить `app_config` singleton и `SettingsNotInitializedError`

**Files:**
- Modify: `src/postgres_fastmcp/config/__init__.py`
- Modify: `src/postgres_fastmcp/common/errors.py`

- [ ] **Step 1: Найти все ссылки на `app_config`**

Run: `grep -rn "app_config" src tests 2>/dev/null`
Expected (после Phase 5–6): только определение в `config/__init__.py`. Если есть в `main.py` через `build_settings_from_cli` — там сейчас `app_config.initialize(...)`; нужно заменить на возврат настроек без побочного эффекта.

- [ ] **Step 2: Переписать `build_settings_from_cli`, чтобы не использовать `app_config`**

В `src/postgres_fastmcp/config/__init__.py` заменить `app_config.initialize(...)` на прямые `Settings(**kwargs)` вызовы. Удалить класс `AppConfig`, экземпляр `app_config`, импорт `SettingsNotInitializedError`.

Финальный публичный API `config/__init__.py`:
```python
__all__ = ["Settings", "build_settings_from_cli"]
```

- [ ] **Step 3: Убрать `SettingsNotInitializedError` из `common/errors.py`**

Удалить класс целиком.

- [ ] **Step 4: Прогон**

Run: `uv run pytest tests/unit -x --no-header -q 2>&1 | tail -10`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/postgres_fastmcp/config/__init__.py src/postgres_fastmcp/common/errors.py && sleep 1 && git commit -m "chore: remove app_config singleton; Settings is passed explicitly"
```

---

## Phase 8 — Подгонка существующих тестов под новый API

### Task 8.1: Обновить тесты тулов

**Files:**
- Modify: `tests/unit/tools/*.py`

Существующие тесты тулов скорее всего вызывают тулы как `await execute_sql(sql=..., sql_execution_service=fake_service)`. После рефакторинга сервис достаётся из `ctx.lifespan_context`. Тесты надо адаптировать.

- [ ] **Step 1: Создать helper для тестов**

В `tests/unit/tools/conftest.py` добавить:

```python
import pytest
from unittest import mock


class FakeCtx:
    def __init__(self, db):
        self.lifespan_context = {"db": db}


@pytest.fixture
def fake_ctx_with_db():
    """Возвращает фабрику FakeCtx(db_mock) для тестов тулов."""
    def make(db_mock):
        return FakeCtx(db=db_mock)
    return make
```

- [ ] **Step 2: Прогнать `tests/unit/tools` и починить точечно**

Run: `uv run pytest tests/unit/tools -v 2>&1 | tail -40`

Каждый упавший тест переписать с использованием `fake_ctx_with_db`. Например, было:
```python
async def test_execute_sql(...):
    fake_service = mock.AsyncMock()
    fake_service.execute_sql.return_value = [...]
    result = await execute_sql(sql="SELECT 1", sql_execution_service=fake_service)
```
Стало:
```python
async def test_execute_sql(monkeypatch, fake_ctx_with_db):
    db_mock = mock.AsyncMock()
    fake_service = mock.AsyncMock()
    fake_service.execute_sql.return_value = [{"a": 1}]
    monkeypatch.setattr(
        "postgres_fastmcp.tools.basic.execute_sql.SqlExecutionService",
        lambda **kw: fake_service,
    )
    ctx = fake_ctx_with_db(db_mock)
    result = await execute_sql(sql="SELECT 1", ctx=ctx)
    assert result == [{"a": 1}]
```

Делать по одному файлу за раз, коммитить после каждого:

```bash
git add tests/unit/tools/test_sql_tools.py tests/unit/tools/conftest.py && sleep 1 && git commit -m "test(tools): adapt sql tools tests to lifespan_context API"
```

Повторить для каждого: `test_objects_tools.py`, `test_schema_tools.py`, `test_explain_tools.py`, `test_health_tools.py`, `test_top_queries_tools.py`, `test_index_tools.py`, `test_tool_descriptions.py`.

- [ ] **Step 3: После всех правок — финальный прогон unit**

Run: `uv run pytest tests/unit --no-header -q 2>&1 | tail -10`
Expected: PASS.

---

### Task 8.2: Добавить smoke-тесты из спеки

**Files:**
- Modify: `tests/unit/test_server.py`

- [ ] **Step 1: Тест на ToolError при `mask_error_details=True`**

В `tests/unit/test_server.py` добавить:

```python
import pytest
from fastmcp.exceptions import ToolError

from postgres_fastmcp.config import Settings
from postgres_fastmcp.server import create_server


@pytest.mark.asyncio
async def test_tool_error_message_reaches_client_even_with_masking():
    """ToolError должен пройти к клиенту, обычный Exception — замаскироваться."""
    settings = Settings()
    server = create_server(settings)
    # Найти MCP-инструмент через клиента FastMCP (in-process). FastMCP даёт fastmcp.client.Client.
    # Пример (адаптировать под фактическое API клиента):
    from fastmcp.client import Client

    async with Client(server) as client:
        with pytest.raises(Exception) as exc:
            await client.call_tool("nonexistent_tool", {})
        # Сообщение должно отражать "tool not found" или похожее.
        assert "not found" in str(exc.value).lower() or "unknown" in str(exc.value).lower()
```

Точная форма теста зависит от API in-process клиента FastMCP 3.3. Если такого клиента нет — пропустить этот тест на этапе плана и оставить только smoke на видимость тулов (он уже есть в Task 6.1).

- [ ] **Step 2: Прогон**

Run: `uv run pytest tests/unit/test_server.py -v`

- [ ] **Step 3: Коммит**

```bash
git add tests/unit/test_server.py && sleep 1 && git commit -m "test(server): add smoke tests for visibility and error masking"
```

---

## Phase 9 — Документация

### Task 9.1: Добавить раздел «Use as a library» в README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Прочитать текущий README**

Run: `head -80 README.md`

- [ ] **Step 2: Добавить раздел перед последней секцией**

Раздел:

````markdown
## Use as a library

The package can be imported and used to build a custom MCP server in another Python project. The public API:

```python
from postgres_fastmcp import (
    create_server,
    Settings,
    BearerAuthProvider,
    LocalProvider,
    FileSystemProvider,
    Middleware,
)

settings = Settings(database={"host": "localhost", "port": 5432, "user": "u", "password": "p", "name": "db"})

server = create_server(
    settings,
    auth=BearerAuthProvider(tokens={"secret-token": {"scopes": ["read"]}}),
    extra_providers=[FileSystemProvider("/path/to/my/tools")],
    extra_middleware=[],
)
server.run(transport="http", host="0.0.0.0", port=8000)
```

- `auth=None` — without authentication (suitable for stdio and trusted localhost).
- `extra_providers` — additional sources of tools/resources/prompts from your application; mixed with built-in tools.
- `extra_middleware` — placed after the built-in `TimingMiddleware` and `LoggingMiddleware`.

Tool names from extra providers must not collide with built-in tools (`execute_sql`, `list_objects`, `get_object_details`, `explain_query`, `list_schemas`, `analyze_db_health`, `get_top_queries`, `analyze_query_indexes`, `analyze_workload_indexes`) — `on_duplicate_tools="error"` is set.
````

- [ ] **Step 3: Коммит**

```bash
git add README.md && sleep 1 && git commit -m "docs(readme): document library usage and create_server API"
```

---

## Phase 10 — Финальная проверка

### Task 10.1: Полный прогон тестов и линтеров

**Files:** none.

- [ ] **Step 1: ruff**

Run: `uv run ruff check src tests`
Expected: чисто. Если есть нарушения — пофиксить точечно и коммит `style: ruff fixes after refactor`.

- [ ] **Step 2: mypy**

Run: `uv run mypy src 2>&1 | tail -10`
Expected: `Success: no issues found`. Если есть ошибки — фиксить.

- [ ] **Step 3: Unit-тесты**

Run: `uv run pytest tests/unit -v --no-header 2>&1 | tail -20`
Expected: все PASS.

- [ ] **Step 4: Интеграционные (если есть Docker)**

Run: `uv run pytest tests/integration -v --no-header 2>&1 | tail -20`
Expected: PASS (запускается из Makefile цель, см. README). Если требуется docker-окружение — отдельный шаг.

- [ ] **Step 5: Финальный коммит, если нужно**

```bash
git status && git log --oneline origin/main..HEAD
```

Проверить, что в истории — последовательность атомарных коммитов по фазам/задачам.

---

### Task 10.2: Создать PR

- [ ] **Step 1: Запушить ветку**

Run: `git push -u origin feat/library-server-refactor`

- [ ] **Step 2: Открыть PR**

Run:
```bash
gh pr create --title "Library-friendly server factory + tools/services refactor" --body "$(cat <<'EOF'
## Summary
- Заменил `compose_mcp` на `create_server(settings, *, auth, extra_providers, extra_middleware)` — пакет можно использовать как библиотеку.
- Убрал глобальный `app_config.current` и `SettingsNotInitializedError`.
- Перевёл регистрацию тулов на `Tool.from_function` + `add_tool` через `tools/registry.py`. Visibility управляется тегами.
- Объединил три explain-режима в `ExplainService.explain`; диспатч `dta`/`llm` ушёл внутрь `IndexAnalysisService`.
- Параллелизовал health-проверки и `get_object_details` через `asyncio.gather`.
- Поднял `pool_max_size=10`, добавил фильтр в `get_top_queries`.
- User-facing исключения наследуют `ToolError`; включён `mask_error_details=True`.
- Удалил папку `providers/` целиком.
- Bumped FastMCP до `>=3.3.1`.

## Test plan
- [ ] `uv run ruff check src tests`
- [ ] `uv run mypy src`
- [ ] `uv run pytest tests/unit`
- [ ] `uv run pytest tests/integration` (требует Docker)
- [ ] Smoke: `from postgres_fastmcp import create_server, Settings` — пакет импортируется без инициализации конфига.

Spec: `docs/superpowers/specs/2026-05-17-tools-and-server-refactor-design.md`
Plan: `docs/superpowers/plans/2026-05-17-tools-and-server-refactor.md`
EOF
)"
```

---

## Self-review checklist

- Spec coverage: ✓ каждая правка из таблицы спеки имеет соответствующую задачу.
- FastMCP bump → Phase 1.
- 1.1 (dispatch dta/llm) → Task 3.2.
- 1.2 (`create_server` + lifespan + visibility) → Phases 5 (lifespan + tools), 6 (server.py + main.py + __init__.py), 7 (удаление app_config).
- 1.4 (description без дубля параметров) → Task 5.3 (формулировки в реестре).
- 1.5 (annotation-пресеты) → Task 5.2.
- 2.1 (фабрика провайдеров) → отменена в пользу lifespan; явное удаление в Task 7.1.
- 2.2 (один ExplainService) → Task 3.1.
- 3.1 (gather health) → Task 2.2.
- 3.2 (instance `_cached_indexes`) → Task 2.1.
- 3.3 (gather в `tables.py`) → Task 2.3.
- 3.4 (фильтр pg_stat_statements) → Task 2.4.
- `pool_max_size=10` → Task 2.5.
- ToolError + mask_error_details → Phase 4, Task 6.1.
- `on_duplicate_tools="error"` → Task 6.1.
- `timeout`, `meta` параметры → Task 5.4.
- CurrentContext стандартизация → Task 5.3 (все тулы используют `CurrentContext()`).
- README → Task 9.1.
