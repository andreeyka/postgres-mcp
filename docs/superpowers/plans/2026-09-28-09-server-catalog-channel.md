# Канал сервера для проверки расширений и версии — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Служебный SQL проверки расширений и версии идёт только через `CatalogSqlExecutor`, ошибки каталога не маскируются под «не установлено», а функции, выполняющие SQL из строки, запрещены во всех режимах.

**Architecture:** Запросы `pg_extension`, `pg_available_extensions` и `SHOW server_version` переезжают в `postgres/catalog.py` и в `CATALOG_QUERIES`. `ExtensionInspectorAdapter` принимает `QueryExecutorPort` и передаёт параметры исполнителю, а не рендерит их сам. Все потребители инспектора (explain, index_tuning, top_queries) получают обязательный именованный `catalog_driver`. Спека: `docs/superpowers/specs/2026-09-28-basic-confinement-design.md`, §3 и §5 (ветка 1).

**Tech Stack:** Python 3.12, uv, psycopg 3.3, pglast, pytest (asyncio auto).

## Global Constraints

- Всё, что видит агент или внешняя система (ошибки, логи, коммиты), — на английском; docstring и комментарии — по-русски.
- Нет `from __future__ import annotations`.
- Ломающие изменения разрешены; никаких шимов, deprecation-предупреждений и упоминаний старого поведения в коде.
- Перед каждым коммитом: `uv run ruff check --fix --show-fixes`, `uv run mypy src/`, `uv run ruff format`; затем `uv run ruff check .` без ошибок.
- Юнит-тесты: `uv run pytest tests/unit -q`. Интеграция: `uv run pytest tests/integration -q` — локально пропускается (нет Docker), сверять статически с реальным кодом.
- Коммиты: `type(scope): message`, повелительное наклонение, английский.
- **Никогда не запускать `git config` и `git stash`.** Не пушить.
- Ветка: `claude/server-catalog-channel` (уже создана, спека закоммичена).

---

### Task 1: R0 — запрет функций, выполняющих SQL из строки

**Files:**
- Modify: `src/postgres_fastmcp/postgres/security/_allowed_functions.py`
- Modify: `tests/unit/postgres/test_query_validator_corpus.py` (список `MUST_BLOCK_EVERYWHERE`)
- Create: `tests/unit/postgres/test_allowed_functions.py`

**Interfaces:**
- Produces: `ALLOWED_FUNCTIONS` без `ts_stat` и `ts_rewrite`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/postgres/test_allowed_functions.py`:

```python
"""Список разрешённых функций не содержит функций, выполняющих SQL из строки-аргумента."""

from postgres_fastmcp.postgres.security.policies import ALLOWED_FUNCTIONS


# Функции, которые выполняют SQL, переданный строкой: валидатор эту строку не разбирает,
# поэтому такая функция обходит и проверку схемы, и список разрешённых функций.
SQL_EXECUTING_FUNCTIONS = frozenset(
    {
        "ts_stat",
        "ts_rewrite",
        "query_to_xml",
        "query_to_xmlschema",
        "query_to_xml_and_xmlschema",
        "cursor_to_xml",
        "cursor_to_xmlschema",
        "table_to_xml",
        "table_to_xmlschema",
        "table_to_xml_and_xmlschema",
        "schema_to_xml",
        "schema_to_xmlschema",
        "schema_to_xml_and_xmlschema",
        "database_to_xml",
        "database_to_xmlschema",
        "database_to_xml_and_xmlschema",
        "dblink",
        "dblink_exec",
        "dblink_open",
        "dblink_fetch",
        "dblink_send_query",
    }
)


def test_no_function_runs_sql_from_a_string() -> None:
    assert ALLOWED_FUNCTIONS.isdisjoint(SQL_EXECUTING_FUNCTIONS), sorted(ALLOWED_FUNCTIONS & SQL_EXECUTING_FUNCTIONS)
```

In `tests/unit/postgres/test_query_validator_corpus.py`, append to `MUST_BLOCK_EVERYWHERE` (after `"SELECT query_to_xml('select 1', true, false, '')",`):

```python
    "SELECT * FROM ts_stat('SELECT to_tsvector(c) FROM other.secret')",
    "SELECT ts_rewrite('a'::tsquery, 'SELECT t, s FROM other.aliases')",
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/postgres/test_allowed_functions.py tests/unit/postgres/test_query_validator_corpus.py -q`
Expected: FAIL — `test_no_function_runs_sql_from_a_string` lists `['ts_rewrite', 'ts_stat']`; the two new corpus cases pass validation in every mode (`DID NOT RAISE`).

- [ ] **Step 3: Implement**

In `src/postgres_fastmcp/postgres/security/_allowed_functions.py` delete the lines `"ts_rewrite",` and `"ts_stat",`.

Run: `grep -rn "ts_stat\|ts_rewrite" src tests`
Expected: only `tests/unit/postgres/test_allowed_functions.py` and `tests/unit/postgres/test_query_validator_corpus.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit -q`
Expected: PASS.

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add src/postgres_fastmcp/postgres/security/_allowed_functions.py tests/unit/postgres/test_allowed_functions.py tests/unit/postgres/test_query_validator_corpus.py
git commit -m "fix(security): disallow functions that run SQL from a string"
```

---

### Task 2: инспектор расширений на канале сервера

**Files:**
- Modify: `src/postgres_fastmcp/postgres/catalog.py`
- Modify: `src/postgres_fastmcp/postgres/extensions.py`
- Modify: `src/postgres_fastmcp/domains/explain/explain_plan.py`, `src/postgres_fastmcp/domains/explain/service.py`
- Modify: `src/postgres_fastmcp/domains/index_tuning/cost_eval.py`, `base.py`, `dta_calc.py`, `service.py`
- Modify: `src/postgres_fastmcp/domains/top_queries.py`
- Test: `tests/unit/postgres/test_catalog_driver.py`, `tests/unit/postgres/test_extensions.py`, `tests/unit/domains/test_explain_service.py`
- Modify (construction sites): `tests/unit/domains/index_tuning/test_pareto_objective.py`, `tests/unit/domains/test_top_queries.py`, `tests/integration/test_top_queries_integration.py`, `tests/integration/dta/test_dta_calc_integration.py`

**Interfaces:**
- Consumes: `DbAccess.catalog_driver: QueryExecutorPort`, `CatalogSqlExecutor` (only templates from `CATALOG_QUERIES`, only `str` params, read-only), test pattern of `tests/unit/domains/test_catalog_service.py` (`fake_delegate` fixture monkeypatching `postgres_fastmcp.domains.db_access.SqlExecutor`).
- Produces:
  - `postgres.catalog.QUERY_EXTENSION_INSTALLED`, `QUERY_EXTENSION_AVAILABLE`, `QUERY_SERVER_VERSION` (in `CATALOG_QUERIES`);
  - `ExtensionInspectorAdapter(executor: QueryExecutorPort, connection_id: str)`;
  - `ExplainPlanBuilder(sql_driver: SqlDriverPort, *, catalog_driver: QueryExecutorPort, connection_id: str = "")`;
  - `CostEvaluator(sql_driver, *, catalog_driver: QueryExecutorPort, connection_id: str = "", trace=None)`;
  - `IndexTuningBase(sql_driver, *, catalog_driver: QueryExecutorPort, connection_id: str = "", pareto_alpha=2.0, budget_mb=-1)`;
  - `DatabaseTuningAdvisor(sql_driver, *, catalog_driver: QueryExecutorPort, connection_id: str = "", ...rest unchanged)`;
  - `TopQueriesCalc(sql_driver, *, catalog_driver: QueryExecutorPort, connection_id: str = "")`.

- [ ] **Step 1: Write the failing tests**

1. `tests/unit/postgres/test_extensions.py` — change the existing constructor call to `ExtensionInspectorAdapter(MagicMock(), "test")` and append:

```python
import psycopg

from postgres_fastmcp.postgres.catalog import (
    QUERY_EXTENSION_AVAILABLE,
    QUERY_EXTENSION_INSTALLED,
    QUERY_SERVER_VERSION,
)
from postgres_fastmcp.postgres.extensions import CATALOG_ERROR_MESSAGE, get_postgres_version
from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.shared.errors import TablePrefixAccessError


def _executor(*answers: object) -> MagicMock:
    executor = MagicMock()
    executor.execute = AsyncMock(side_effect=list(answers))
    return executor


async def test_check_extension_passes_the_template_and_a_str_param() -> None:
    """Параметр уходит исполнителю отдельно: CatalogSqlExecutor проверяет шаблон до рендера."""
    executor = _executor([RowResult(cells={"extversion": "1.5.1"})])

    status = await ExtensionInspectorAdapter(executor, "test").check_extension("hypopg")

    assert status.is_installed is True
    executor.execute.assert_awaited_once_with(QUERY_EXTENSION_INSTALLED, params=["hypopg"], readonly=True)


async def test_database_error_from_available_extensions_is_a_catalog_error() -> None:
    """Битый .control ломает pg_available_extensions: статус неизвестен, а не «не установлено»."""
    executor = _executor([], psycopg.Error("could not read control file"))

    status = await ExtensionInspectorAdapter(executor, "test").check_extension("hypopg")

    assert status.catalog_error == CATALOG_ERROR_MESSAGE
    assert executor.execute.await_args_list[1].args[0] is QUERY_EXTENSION_AVAILABLE


async def test_validator_error_is_not_reported_as_missing_extension() -> None:
    executor = _executor(TablePrefixAccessError("pg_extension", "app_"))

    with pytest.raises(TablePrefixAccessError):
        await ExtensionInspectorAdapter(executor, "test").check_extension("hypopg")


async def test_server_version_uses_the_catalog_template() -> None:
    executor = _executor([RowResult(cells={"server_version": "16.4 (Debian 16.4-1)"})])

    assert await get_postgres_version(executor, "version-test") == 16
    executor.execute.assert_awaited_once_with(QUERY_SERVER_VERSION, params=None, readonly=True)
```

(Merge these imports into the file's import block; `AsyncMock`, `MagicMock`, `pytest`, `ExtensionInspectorAdapter` are already imported.)

2. `tests/unit/postgres/test_catalog_driver.py`, in `test_catalog_queries_qualify_every_relation`: delete the line `assert relations.found` and change the docstring to `"""Без search_path источник закреплён явной схемой: только information_schema и pg_catalog (у SHOW отношений нет)."""`.

3. `tests/unit/domains/test_explain_service.py` — append a chain test (add imports `import postgres_fastmcp.domains.db_access as db_access_module`, `from postgres_fastmcp.access import EffectiveAccess`, `from postgres_fastmcp.app.config.database import DatabaseConfig`, `from postgres_fastmcp.domains.db_access import DbAccessService`, `from postgres_fastmcp.postgres.models import RowResult`, `from postgres_fastmcp.shared.enums import AccessMode`):

```python
_SEQ_SCAN_PLAN = [
    {"Plan": {"Node Type": "Seq Scan", "Total Cost": 1.0, "Startup Cost": 0.0, "Plan Rows": 1, "Plan Width": 4}}
]


async def test_hypothetical_explain_works_in_basic_with_table_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверка hypopg идёт по каналу сервера: валидатор агента с table_prefix её не отклоняет."""

    async def execute(query, params=None, *, readonly=True):
        if "pg_catalog.pg_extension" in query:
            return [RowResult(cells={"extversion": "1.4.1"})]
        if "EXPLAIN" in query:
            return [RowResult(cells={"QUERY PLAN": _SEQ_SCAN_PLAN})]
        return []

    delegate = MagicMock()
    delegate.execute = AsyncMock(side_effect=execute)
    monkeypatch.setattr(db_access_module, "SqlExecutor", lambda conn: delegate)
    config = DatabaseConfig(
        host="h", user="u", password="p", name="d", access_mode=AccessMode.BASIC, write_mode=False, table_prefix="app_"
    )
    db = DbAccessService(config).view(EffectiveAccess(AccessMode.BASIC, write_mode=False))

    result = await ExplainService(db=db).explain(
        "SELECT * FROM app_users WHERE name = 'x'",
        hypothetical_indexes=[{"table": "app_users", "columns": ["name"]}],
    )

    assert "Seq Scan" in result
    sent = [c.args[0] for c in delegate.execute.await_args_list]
    assert any("pg_catalog.pg_extension" in q for q in sent)
    assert any("hypopg_create_index" in q and "EXPLAIN" in q for q in sent)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/postgres/test_extensions.py tests/unit/postgres/test_catalog_driver.py tests/unit/domains/test_explain_service.py -q`
Expected: FAIL — collecting `test_extensions.py` fails with `ImportError: cannot import name 'QUERY_EXTENSION_AVAILABLE'`; `test_hypothetical_explain_works_in_basic_with_table_prefix` fails with `HypopgNotInstalledError` (the inspector goes through the agent validator, which rejects `pg_extension` without the prefix); `test_catalog_queries_qualify_every_relation` still passes. Record the failures in the report.

- [ ] **Step 3: Move the queries into the catalog**

In `src/postgres_fastmcp/postgres/catalog.py`, add before `CATALOG_QUERIES`:

```python
QUERY_EXTENSION_INSTALLED = """
SELECT extversion
FROM pg_catalog.pg_extension
WHERE extname = {}
"""

QUERY_EXTENSION_AVAILABLE = """
SELECT default_version
FROM pg_catalog.pg_available_extensions
WHERE name = {}
"""

QUERY_SERVER_VERSION = "SHOW server_version"
```

and add `QUERY_EXTENSION_INSTALLED`, `QUERY_EXTENSION_AVAILABLE`, `QUERY_SERVER_VERSION` to the `CATALOG_QUERIES` set. Update the module docstring to `"""Константы служебных SQL-запросов сервера (каталог, расширения, версия) и список разрешённых шаблонов."""`.

- [ ] **Step 4: Rework `ExtensionInspectorAdapter`**

In `src/postgres_fastmcp/postgres/extensions.py`:

1. Imports: add `import psycopg` and `from postgres_fastmcp.postgres.catalog import QUERY_EXTENSION_AVAILABLE, QUERY_EXTENSION_INSTALLED, QUERY_SERVER_VERSION`; import only `QueryExecutorPort` from `postgres_fastmcp.postgres.ports`; drop `cast` from `typing` if unused.
2. Delete `EXT_INSTALLED_QUERY` and `EXT_AVAILABLE_QUERY`.
3. `get_postgres_version`: `rows = await executor.execute(QUERY_SERVER_VERSION, params=None, readonly=True)`; `except Exception as e:` → `except (psycopg.Error, ValueError) as e:` (ValueError — нечисловая мажорная версия).
4. Constructor:

```python
    def __init__(self, executor: QueryExecutorPort, connection_id: str) -> None:
        """Инициализация с исполнителем служебных запросов (catalog_driver) и идентификатором подключения для кэша."""
        self._executor = executor
        self._connection_id = connection_id
```

5. Delete `_run_param`. In `check_extension`:

```python
        try:
            installed = await self._executor.execute(QUERY_EXTENSION_INSTALLED, params=[extension_name], readonly=True)
        except psycopg.Error as e:
```

and likewise `available = await self._executor.execute(QUERY_EXTENSION_AVAILABLE, params=[extension_name], readonly=True)` with `except psycopg.Error as e:`. Log messages and `CATALOG_ERROR_MESSAGE` stay unchanged.
6. Class docstring: `"""Проверка расширений и версии PostgreSQL через исполнитель служебных запросов (catalog_driver)."""`

- [ ] **Step 5: Thread `catalog_driver` through the consumers**

Each constructor gets a required keyword-only `catalog_driver: QueryExecutorPort` right after `sql_driver` (put `*,` before it) and a Russian Args line `catalog_driver: Исполнитель служебных запросов (проверка расширений и версии).`:

- `domains/explain/explain_plan.py` `ExplainPlanBuilder.__init__(self, sql_driver: SqlDriverPort, *, catalog_driver: QueryExecutorPort, connection_id: str = "")`; `self._ext_inspector = ExtensionInspectorAdapter(catalog_driver, connection_id)`.
- `domains/explain/service.py`: `_make_tool` passes `catalog_driver=self.db.catalog_driver`; `_explain_hypothetical` builds `ExtensionInspectorAdapter(self.db.catalog_driver, self.db.connection_id)`.
- `domains/index_tuning/cost_eval.py` `CostEvaluator.__init__(self, sql_driver, *, catalog_driver, connection_id="", trace=None)`; store `self._catalog_driver = catalog_driver`; `ExplainPlanBuilder(self.sql_driver, catalog_driver=self._catalog_driver, connection_id=self._connection_id)`.
- `domains/index_tuning/base.py` `IndexTuningBase.__init__(self, sql_driver, *, catalog_driver, connection_id="", pareto_alpha=2.0, budget_mb=-1)`; `ExtensionInspectorAdapter(catalog_driver, connection_id)`; `CostEvaluator(sql_driver, catalog_driver=catalog_driver, connection_id=connection_id, trace=self.dta_trace)`.
- `domains/index_tuning/dta_calc.py` `DatabaseTuningAdvisor.__init__(self, sql_driver, *, catalog_driver, connection_id="", ...)`; pass `catalog_driver=catalog_driver` to `super().__init__`.
- `domains/index_tuning/service.py` `_presentation`: `DatabaseTuningAdvisor(sql_driver, catalog_driver=self.db.catalog_driver, connection_id=self.db.connection_id)`.
- `domains/top_queries.py` `TopQueriesCalc.__init__(self, sql_driver, *, catalog_driver, connection_id="")`; `ExtensionInspectorAdapter(catalog_driver, connection_id)`; docstring of `sql_driver` → `SQL-драйвер для запросов к pg_stat_statements.`; `get_top_queries` passes `catalog_driver=db.catalog_driver`.

Then update every other construction site:

Run: `grep -rn "DatabaseTuningAdvisor(\|TopQueriesCalc(\|CostEvaluator(\|ExplainPlanBuilder(\|ExtensionInspectorAdapter(" src tests`

- unit tests: pass `catalog_driver=MagicMock()` (`test_pareto_objective.py`) or `catalog_driver=mock_executor` (`test_top_queries.py`);
- integration tests: pass `catalog_driver=<the same DbAccess>.catalog_driver` next to the existing `sql_driver`/`connection_id` arguments (`test_top_queries_integration.py`, `dta/test_dta_calc_integration.py`).

Every production call site must pass `db.catalog_driver`, never `sql_driver`, as `catalog_driver`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit -q`
Expected: PASS.

Run: `grep -rn "ExtensionInspectorAdapter(" src`
Expected: every call's first argument is a `catalog_driver` (`catalog_driver`, `self.db.catalog_driver`).

Run: `uv run pytest tests/integration -q`
Expected: collected and skipped, no collection errors.

- [ ] **Step 7: Lint, type-check, commit**

```bash
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add src tests
git commit -m "fix(extensions): check extensions and server version on the catalog executor"
```

---

### Task 3: интеграционный тест и статус спеки

**Files:**
- Test: `tests/integration/test_table_prefix.py`
- Modify: `docs/superpowers/specs/2026-09-28-basic-confinement-design.md` (строка статуса)

**Interfaces:**
- Consumes: fixtures `db_full` (FULL+write) and `db_user_prefix` (BASIC read-only, `table_prefix="app_"`), helper `setup_test_tables` in the same file; `ExplainService(db)` with `explain(sql, *, analyze=False, hypothetical_indexes=None) -> str`; hypopg is installed in the CI image (see `tests/integration/dta/conftest.py`, fixture `db_with_hypopg`, for how its absence is handled).

- [ ] **Step 1: Add the integration test**

In `tests/integration/test_table_prefix.py` add `from postgres_fastmcp.domains.explain.service import ExplainService` to the imports and append:

```python
@pytest.mark.asyncio
async def test_explain_with_hypothetical_index_in_basic_with_prefix(
    db_full: DbAccess,
    db_user_prefix: DbAccess,
) -> None:
    """basic + table_prefix: проверка hypopg идёт по каналу сервера, план с гипотетическим индексом строится."""
    await setup_test_tables(db_full)
    await db_full.sql_driver.execute("CREATE EXTENSION IF NOT EXISTS hypopg", readonly=False)

    result = await ExplainService(db_user_prefix).explain(
        "SELECT * FROM app_users WHERE name = 'x'",
        hypothetical_indexes=[{"table": "app_users", "columns": ["name"]}],
    )

    assert "app_users" in result
```

If `tests/integration/dta/conftest.py` skips when `CREATE EXTENSION hypopg` fails, mirror that exact pattern (same exception type, `pytest.skip` message `"hypopg extension is not available"`) instead of the bare `execute` call.

- [ ] **Step 2: Verify statically**

Record in the report, with file:line:
- how `ExplainPlanArtifact.to_text()` renders a scan node — confirm the relation name (`app_users`) appears for a `Seq Scan`/`Index Scan` on it; if it does not, assert on a string that `to_text()` does print for any plan (and say which);
- that the hypothetical-index statement built in `generate_explain_plan_with_hypothetical_indexes` passes the BASIC + `app_` validator (functions `hypopg_reset`, `hypopg_create_index` are allowed; the only relation is `app_users`).

Run: `uv run pytest tests/integration/test_table_prefix.py -q`
Expected: collected and skipped, no errors.

- [ ] **Step 3: Update the spec status**

In `docs/superpowers/specs/2026-09-28-basic-confinement-design.md`, replace `Статус: согласовано, к реализации.` with `Статус: ветка 1 (канал сервера, R0) реализована; ветка 2 (политика агента) — к реализации.`

- [ ] **Step 4: Full verification, lint, commit**

```bash
uv run pytest tests/unit -q
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add tests/integration/test_table_prefix.py docs/superpowers/specs/2026-09-28-basic-confinement-design.md
git commit -m "test(explain): cover hypothetical indexes in basic mode with table_prefix"
```
