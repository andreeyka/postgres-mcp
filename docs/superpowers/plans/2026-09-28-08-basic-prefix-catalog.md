# Каталог в basic с `table_prefix` — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `get_object_details` и `list_objects` работают в `access_mode=basic` с `table_prefix`, а ограничения basic для SQL агента не ослабевают.

**Architecture:** Служебные запросы каталога уходят с `sql_driver` агента на отдельный `CatalogSqlExecutor`: он выполняет только шаблоны-константы из `postgres/catalog.py` со строковыми параметрами, в read-only транзакции с `statement_timeout` и AST-валидацией без схемы и префикса. Схему и префикс имени объекта домен каталога проверяет сам до любого запроса. Спека: `docs/superpowers/specs/2026-09-28-basic-prefix-catalog-design.md`.

**Tech Stack:** Python 3.12, uv, psycopg 3.3 (`psycopg.sql`), pglast, pytest (asyncio auto), FastMCP 4.0.10.

## Global Constraints

- Всё, что видит агент или внешняя система (ошибки, логи, коммиты), — на английском; docstring и комментарии — по-русски.
- Нет `from __future__ import annotations`.
- Ломающие изменения разрешены; никаких шимов, deprecation-предупреждений и упоминаний старого поведения в коде.
- Перед каждым коммитом: `uv run ruff check --fix --show-fixes`, `uv run mypy src/`, `uv run ruff format`; затем `uv run ruff check .` без ошибок.
- Юнит-тесты: `uv run pytest tests/unit -q`. Интеграция: `uv run pytest tests/integration -q` — локально пропускается (нет Docker); код интеграционных тестов сверять с реальным кодом статически.
- Коммиты: `type(scope): message`, повелительное наклонение, английский.
- **Никогда не запускать `git config` и `git stash`.** Не пушить.
- `_run_concurrently` в `domains/catalog/tables.py` не заменять на `asyncio.TaskGroup`.

---

### Task 1: `CatalogSqlExecutor` и список шаблонов каталога

**Files:**
- Modify: `src/postgres_fastmcp/postgres/catalog.py`
- Create: `src/postgres_fastmcp/postgres/security/catalog_driver.py`
- Test: `tests/unit/postgres/test_catalog_driver.py`

**Interfaces:**
- Produces: `postgres_fastmcp.postgres.catalog.CATALOG_QUERIES: frozenset[str]`; `postgres_fastmcp.postgres.security.catalog_driver.CatalogSqlExecutor(delegate: SqlDriverPort, *, timeout: float | None, query_tag: str)` с методом `async execute(query: str, params: list[Any] | None = None, *, readonly: bool = True) -> list[RowResult] | None` (структурно `QueryExecutorPort`). Внутренний `SafeSqlExecutor` хранится в атрибуте `_inner`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/postgres/test_catalog_driver.py`:

```python
# mypy: ignore-errors
"""Unit tests for CatalogSqlExecutor: only server catalog templates, only str parameters, read-only."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from psycopg.sql import SQL, Identifier, Literal

from postgres_fastmcp.postgres.catalog import (
    CATALOG_QUERIES,
    QUERY_GET_EXTENSION_DETAILS,
    QUERY_GET_INDEXES,
    QUERY_LIST_EXTENSIONS,
)
from postgres_fastmcp.postgres.security.catalog_driver import CatalogSqlExecutor


def _delegate() -> MagicMock:
    delegate = MagicMock()
    delegate.execute = AsyncMock(return_value=[])
    return delegate


@pytest.mark.parametrize("query", sorted(CATALOG_QUERIES))
async def test_every_catalog_query_passes_the_catalog_executor(query: str) -> None:
    delegate = _delegate()
    executor = CatalogSqlExecutor(delegate, timeout=7, query_tag="t")
    params = ["x"] * query.count("{}") or None
    await executor.execute(query, params=params)
    delegate.execute.assert_awaited_once()


async def test_runs_catalog_query_read_only_with_timeout_and_literal_params() -> None:
    delegate = _delegate()
    executor = CatalogSqlExecutor(delegate, timeout=7, query_tag="t")

    await executor.execute(QUERY_GET_INDEXES, params=["public", "app_x' OR 1=1 --"])

    sent = delegate.execute.await_args.args[0]
    assert sent.startswith("SET LOCAL statement_timeout = 7000;")
    assert "search_path" not in sent
    assert "pg_indexes" in sent
    assert "'app_x'' OR 1=1 --'" in sent
    assert delegate.execute.await_args.kwargs["readonly"] is True


async def test_readonly_false_does_not_open_a_write_transaction() -> None:
    delegate = _delegate()
    executor = CatalogSqlExecutor(delegate, timeout=None, query_tag="t")

    await executor.execute(QUERY_LIST_EXTENSIONS, readonly=False)

    assert delegate.execute.await_args.kwargs["readonly"] is True


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM pg_catalog.pg_authid",
        QUERY_GET_INDEXES + " ",
        "DELETE FROM app_users",
    ],
)
async def test_rejects_query_that_is_not_a_catalog_template(query: str) -> None:
    delegate = _delegate()
    executor = CatalogSqlExecutor(delegate, timeout=None, query_tag="t")

    with pytest.raises(ValueError, match="catalog"):
        await executor.execute(query, params=None)

    delegate.execute.assert_not_awaited()


class _StrSubclass(str):
    __slots__ = ()


@pytest.mark.parametrize(
    "param",
    [SQL("'plpgsql' OR TRUE"), Identifier("plpgsql"), Literal("plpgsql"), b"plpgsql", 1, None, _StrSubclass("x")],
)
async def test_rejects_non_str_parameter_before_rendering(param: object) -> None:
    """Composable вставился бы в запрос как SQL: SQL("'x' OR TRUE") меняет условие."""
    delegate = _delegate()
    executor = CatalogSqlExecutor(delegate, timeout=None, query_tag="t")

    with pytest.raises(TypeError, match="str"):
        await executor.execute(QUERY_GET_EXTENSION_DETAILS, params=[param])

    delegate.execute.assert_not_awaited()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/postgres/test_catalog_driver.py -q`
Expected: FAIL — `ImportError: cannot import name 'CATALOG_QUERIES'`.

- [ ] **Step 3: Add `CATALOG_QUERIES`**

Append at the end of `src/postgres_fastmcp/postgres/catalog.py` (system relations are qualified later, in Task 3, together with the switch to the catalog executor — otherwise basic without prefix would break between commits):

```python
# Единственные шаблоны, которые выполняет CatalogSqlExecutor: сравнение по тексту до подстановки параметров.
CATALOG_QUERIES: frozenset[str] = frozenset(
    {
        QUERY_LIST_SCHEMAS,
        QUERY_LIST_TABLES_VIEWS,
        QUERY_TABLE_EXISTS,
        QUERY_LIST_SEQUENCES,
        QUERY_LIST_EXTENSIONS,
        QUERY_GET_COLUMNS,
        QUERY_GET_CONSTRAINTS,
        QUERY_GET_INDEXES,
        QUERY_GET_SEQUENCE_DETAILS,
        QUERY_GET_EXTENSION_DETAILS,
    }
)
```

- [ ] **Step 4: Create `CatalogSqlExecutor`**

`src/postgres_fastmcp/postgres/security/catalog_driver.py`:

```python
"""Исполнитель служебных запросов каталога: только шаблоны сервера и строковые параметры, только чтение."""

from typing import Any

from postgres_fastmcp.postgres.catalog import CATALOG_QUERIES
from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.postgres.ports import SqlDriverPort
from postgres_fastmcp.postgres.security.driver import SafeSqlConfig, SafeSqlExecutor
from postgres_fastmcp.postgres.security.query_validator import QueryValidator


class CatalogSqlExecutor:
    """Выполняет только запросы из CATALOG_QUERIES: read-only транзакция, statement_timeout, AST-валидация.

    Граница доверия: SQL агента сюда не попадает — execute_sql идёт через sql_driver. Схему и
    префикс имени проверяет домен каталога до запроса, поэтому валидатор здесь без схемы и
    префикса (правила full read-only). Параметры — только str: SafeSqlExecutor.render()
    вставляет Composable как SQL-фрагмент, а строка всегда становится Literal.

    Реализует только execute (QueryExecutorPort): как SqlDriverPort его не передать.
    """

    def __init__(self, delegate: SqlDriverPort, *, timeout: float | None, query_tag: str) -> None:
        """Инициализация поверх исполнителя без проверок.

        Args:
            delegate: Исполнитель без проверок (SqlExecutor) на пуле сервиса.
            timeout: statement_timeout в секундах; None — без таймаута.
            query_tag: Тег запросов для логирования/мониторинга.
        """
        config = SafeSqlConfig(query_tag=query_tag, timeout=timeout, read_only=True)
        self._inner = SafeSqlExecutor(delegate=delegate, validator=QueryValidator(read_only=True), config=config)

    async def execute(
        self,
        query: str,
        params: list[Any] | None = None,
        *,
        readonly: bool = True,  # noqa: ARG002 — part of QueryExecutorPort; always read-only
    ) -> list[RowResult] | None:
        """Выполнить шаблон каталога со строковыми параметрами.

        Args:
            query: Шаблон из CATALOG_QUERIES (с {} плейсхолдерами).
            params: Строковые параметры шаблона.
            readonly: Игнорируется: исполнитель всегда только читает.

        Returns:
            Строки результата или None.

        Raises:
            ValueError: query не входит в CATALOG_QUERIES.
            TypeError: Параметр не str (в том числе Composable).
        """
        if query not in CATALOG_QUERIES:
            msg = "Only server catalog queries can run on the catalog executor"
            raise ValueError(msg)
        for param in params or []:
            if type(param) is not str:
                msg = f"Catalog query parameters must be str, got {type(param).__name__}"
                raise TypeError(msg)
        return await self._inner.execute(query, params)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/postgres/test_catalog_driver.py -q`
Expected: PASS.

Run: `uv run pytest tests/unit -q`
Expected: PASS (nothing uses the new executor yet).

- [ ] **Step 6: Lint, type-check, commit**

```bash
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add src/postgres_fastmcp/postgres/catalog.py src/postgres_fastmcp/postgres/security/catalog_driver.py tests/unit/postgres/test_catalog_driver.py
git commit -m "feat(catalog): add a read-only executor for server catalog queries"
```

---

### Task 2: `catalog_driver` в `DbAccess`

**Files:**
- Modify: `src/postgres_fastmcp/domains/db_access.py`
- Modify: `tests/unit/conftest.py` (фикстура `mock_db_access`)
- Modify: `tests/unit/test_provider.py:53-59`, `tests/unit/app/test_auth_http.py:52-58`, `tests/unit/domains/test_catalog_service.py` (конструктор `DbAccess` в `test_basic_table_prefix_error_is_unchanged`)
- Test: `tests/unit/domains/test_db_access.py`

**Interfaces:**
- Consumes: `CatalogSqlExecutor(delegate, *, timeout, query_tag)` (Task 1), атрибут `_inner`.
- Produces: `DbAccessPort.catalog_driver -> QueryExecutorPort`; поле `DbAccess.catalog_driver: QueryExecutorPort` (второе поле после `sql_driver`, конструктор всегда вызывается по именам). В юнит-фикстуре `mock_db_access.catalog_driver is mock_executor`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/domains/test_db_access.py` (add import `from postgres_fastmcp.postgres.security.catalog_driver import CatalogSqlExecutor`):

```python
def test_catalog_driver_is_one_read_only_executor_for_every_access() -> None:
    """Каталог: один исполнитель на сервис, без схемы и префикса, только чтение, с таймаутом; не sql_driver агента."""
    service = _service(access_mode=AccessMode.FULL, write_mode=True, table_prefix="app_", safe_sql_timeout=7)
    views = [service.view(access) for access in _ALL_ACCESS]

    catalogs = {id(view.catalog_driver) for view in views}
    assert len(catalogs) == 1
    catalog = views[0].catalog_driver
    assert isinstance(catalog, CatalogSqlExecutor)
    assert all(view.catalog_driver is not view.sql_driver for view in views)

    inner = catalog._inner
    assert inner._config.read_only is True
    assert inner._config.allowed_schema is None
    assert inner._config.table_prefix is None
    assert inner._config.timeout == 7
    assert inner._config.query_tag == "postgres_fastmcp"
    assert inner._validator.read_only is True
    assert inner._validator.allowed_schema is None
    assert inner._validator.table_prefix is None
    assert inner._validator.allow_explain_analyze is False
    assert inner._delegate.conn is service._pool


def test_basic_agent_driver_keeps_prefix_next_to_catalog_driver() -> None:
    """Появление catalog_driver не меняет исполнитель агента в BASIC."""
    service = _service(access_mode=AccessMode.BASIC, write_mode=True, table_prefix="app_")
    for write_mode in (False, True):
        driver = service.view(EffectiveAccess(AccessMode.BASIC, write_mode=write_mode)).sql_driver
        assert isinstance(driver, SafeSqlExecutor)
        assert driver._validator.allowed_schema == "public"
        assert driver._validator.table_prefix == "app_"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/domains/test_db_access.py -q`
Expected: FAIL — `AttributeError: 'DbAccess' object has no attribute 'catalog_driver'`.

- [ ] **Step 3: Implement**

In `src/postgres_fastmcp/domains/db_access.py`:

1. Imports: `from postgres_fastmcp.postgres.ports import QueryExecutorPort, SqlDriverPort` and `from postgres_fastmcp.postgres.security.catalog_driver import CatalogSqlExecutor`.
2. In `DbAccessPort`, after `sql_driver`:

```python
    @property
    def catalog_driver(self) -> QueryExecutorPort:
        """Исполнитель служебных запросов каталога: только шаблоны сервера, только чтение."""
        ...
```

3. In `DbAccess`, after `sql_driver: SqlDriverPort`: `catalog_driver: QueryExecutorPort`.
4. In `DbAccessService.__init__`, after `self._executors = {}`:

```python
        # Каталог не зависит от прав запроса: схему и префикс проверяет домен до запроса.
        self._catalog = CatalogSqlExecutor(
            SqlExecutor(conn=self._pool),
            timeout=config.safe_sql_timeout,
            query_tag=config.query_tag or DEFAULT_QUERY_TAG,
        )
```

5. In `view()`: pass `catalog_driver=self._catalog` right after `sql_driver=...`.
6. Class docstring of `DbAccessService`: `"""Пул подключений, исполнители по эффективным правам (не больше четырёх) и один исполнитель каталога."""`

In tests, every direct `DbAccess(...)` gets `catalog_driver=`:
- `tests/unit/test_provider.py` and `tests/unit/app/test_auth_http.py` (`FakeService.view`): `catalog_driver=self.sql_driver,`.
- `tests/unit/domains/test_catalog_service.py` (`test_basic_table_prefix_error_is_unchanged`): `catalog_driver=AsyncMock(),`.
- `tests/unit/conftest.py`, `mock_db_access`: add `db.catalog_driver = mock_executor` after `db.sql_driver = mock_executor`; docstring → `"""Mock DbAccess (DbAccessPort): sql_driver и catalog_driver — один mock_executor."""`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit -q`
Expected: PASS.

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add src/postgres_fastmcp/domains/db_access.py tests/unit
git commit -m "feat(db-access): expose the catalog executor on every request view"
```

---

### Task 3: домен каталога через `catalog_driver` и проверка префикса

**Files:**
- Create: `src/postgres_fastmcp/domains/catalog/prefix.py`
- Modify: `src/postgres_fastmcp/domains/catalog/service.py`, `tables.py`, `sequences.py`, `extensions.py`
- Modify: `src/postgres_fastmcp/postgres/catalog.py` (квалификация системных источников)
- Test: `tests/unit/domains/test_catalog_service.py`, `tests/unit/postgres/test_catalog_driver.py`

**Interfaces:**
- Consumes: `DbAccess.catalog_driver` (Task 2); `CATALOG_QUERIES` (Task 1); `DbAccessService`, `SqlExecutor` в `postgres_fastmcp.domains.db_access` (патчится в тестах).
- Produces: `domains.catalog.prefix.active_prefix(db: DbAccessPort) -> str | None`, `domains.catalog.prefix.matches_prefix(name: str, prefix: str) -> bool`.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/postgres/test_catalog_driver.py`: add imports `import pglast`, `from pglast.ast import Node, RangeVar`, `from pglast.visitors import Ancestor, Visitor`; in `test_runs_catalog_query_read_only_with_timeout_and_literal_params` change `assert "pg_indexes" in sent` to `assert "pg_catalog.pg_indexes" in sent`; append:

```python
class _Relations(Visitor):
    """Собирает (schemaname, relname) всех RangeVar запроса."""

    def __init__(self) -> None:
        super().__init__()
        self.found: list[tuple[str | None, str]] = []

    def visit(self, _ancestors: Ancestor, node: Node) -> None:
        if isinstance(node, RangeVar):
            self.found.append((node.schemaname, node.relname))


@pytest.mark.parametrize("query", sorted(CATALOG_QUERIES))
def test_catalog_queries_qualify_every_relation(query: str) -> None:
    """Без search_path источник закреплён явной схемой: только information_schema и pg_catalog."""
    relations = _Relations()
    relations(pglast.parse_sql(query.replace("{}", "NULL")))
    assert relations.found
    assert {schema for schema, _ in relations.found} <= {"information_schema", "pg_catalog"}, relations.found
```

In `tests/unit/domains/test_catalog_service.py`:

1. Delete `test_basic_table_prefix_error_is_unchanged` entirely.
2. Replace imports of `SafeSqlConfig, SafeSqlExecutor` and `QueryValidator` (now unused) with:

```python
import postgres_fastmcp.domains.db_access as db_access_module
from postgres_fastmcp.access import EffectiveAccess
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.domains.db_access import DbAccess, DbAccessService
```

and add `SchemaNotAllowedError` to the `postgres_fastmcp.shared.errors` import.

3. Append at the end of the file:

```python
_PREFIX_ERROR = "Access to table '{}' is not allowed. Only tables with names starting with 'app_' are permitted."


def _fake_postgres(*, present: bool):
    """Ответы делегата по тексту отрендеренного запроса: объект есть (present) или нет."""

    async def execute(query, params=None, *, readonly=True):
        if "SELECT 1 AS present" in query:
            return [RowResult(cells={"present": 1})] if present else []
        if "pg_catalog.pg_indexes" in query:
            row = {"indexname": "app_users_name_idx", "indexdef": "CREATE INDEX app_users_name_idx ON public.app_users"}
            return [RowResult(cells=row)] if present else []
        if "information_schema.sequences" in query:
            row = {
                "sequence_schema": "public",
                "sequence_name": "app_users_id_seq",
                "data_type": "integer",
                "start_value": "1",
                "increment": "1",
            }
            return [RowResult(cells=row)] if present else []
        if "pg_catalog.pg_extension" in query:
            return [RowResult(cells={"extname": "plpgsql", "extversion": "1.0", "extrelocatable": False})]
        return []

    return execute


@pytest.fixture
def fake_delegate(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Делегат вместо SqlExecutor: настоящие SafeSqlExecutor и CatalogSqlExecutor собирает DbAccessService."""
    delegate = MagicMock()
    delegate.execute = AsyncMock(side_effect=_fake_postgres(present=True))
    monkeypatch.setattr(db_access_module, "SqlExecutor", lambda conn: delegate)
    return delegate


def _basic_prefix_db(*, write_mode: bool) -> DbAccess:
    config = DatabaseConfig(
        host="h",
        user="u",
        password="p",
        name="d",
        access_mode=AccessMode.BASIC,
        write_mode=write_mode,
        table_prefix="app_",
    )
    return DbAccessService(config).view(EffectiveAccess(AccessMode.BASIC, write_mode=write_mode))


@pytest.mark.parametrize("write_mode", [False, True])
class TestBasicTablePrefix:
    """BASIC с table_prefix="app_" на настоящей цепочке исполнителей поверх фальшивого делегата."""

    @pytest.mark.parametrize("object_type", ["table", "view"])
    async def test_prefixed_relation_details_include_indexes(
        self, fake_delegate: MagicMock, object_type: str, *, write_mode: bool
    ) -> None:
        result = await CatalogService(_basic_prefix_db(write_mode=write_mode)).get_object_details(
            "public", "app_users", object_type
        )

        assert result["basic"] == {"schema": "public", "name": "app_users", "type": object_type}
        assert result["indexes"] == [
            {"name": "app_users_name_idx", "definition": "CREATE INDEX app_users_name_idx ON public.app_users"}
        ]
        sent = [c.args[0] for c in fake_delegate.execute.await_args_list]
        assert any("pg_catalog.pg_indexes" in q for q in sent)
        # Каталог только читает, даже при write_mode=True
        assert all(c.kwargs["readonly"] is True for c in fake_delegate.execute.await_args_list)

    @pytest.mark.usefixtures("fake_delegate")
    async def test_prefix_match_ignores_case(self, *, write_mode: bool) -> None:
        result = await CatalogService(_basic_prefix_db(write_mode=write_mode)).get_object_details(
            "public", "APP_Users", "table"
        )
        assert result["basic"]["name"] == "APP_Users"

    @pytest.mark.parametrize("object_type", ["table", "view", "sequence"])
    async def test_unprefixed_object_is_rejected_before_any_query(
        self, fake_delegate: MagicMock, object_type: str, *, write_mode: bool
    ) -> None:
        """Ответ одинаков для существующего и несуществующего объекта: каталог не опрашивается."""
        with pytest.raises(TablePrefixAccessError) as exc_info:
            await CatalogService(_basic_prefix_db(write_mode=write_mode)).get_object_details(
                "public", "users", object_type
            )

        assert str(exc_info.value) == _PREFIX_ERROR.format("users")
        fake_delegate.execute.assert_not_awaited()

    @pytest.mark.parametrize("object_type", ["table", "view", "sequence"])
    async def test_missing_prefixed_object_is_not_found(
        self, fake_delegate: MagicMock, object_type: str, *, write_mode: bool
    ) -> None:
        fake_delegate.execute.side_effect = _fake_postgres(present=False)

        with pytest.raises(ObjectNotFoundError):
            await CatalogService(_basic_prefix_db(write_mode=write_mode)).get_object_details(
                "public", "app_ghost", object_type
            )

    async def test_object_name_reaches_postgres_as_a_literal(
        self, fake_delegate: MagicMock, *, write_mode: bool
    ) -> None:
        fake_delegate.execute.side_effect = _fake_postgres(present=False)

        with pytest.raises(ObjectNotFoundError):
            await CatalogService(_basic_prefix_db(write_mode=write_mode)).get_object_details(
                "public", "app_x' OR 1=1 --", "table"
            )

        sent = [c.args[0] for c in fake_delegate.execute.await_args_list]
        assert len(sent) == 4
        assert all("'app_x'' OR 1=1 --'" in q for q in sent)

    @pytest.mark.usefixtures("fake_delegate")
    async def test_prefixed_sequence_details(self, *, write_mode: bool) -> None:
        result = await CatalogService(_basic_prefix_db(write_mode=write_mode)).get_object_details(
            "public", "app_users_id_seq", "sequence"
        )
        assert result["name"] == "app_users_id_seq"

    @pytest.mark.usefixtures("fake_delegate")
    async def test_extensions_ignore_the_prefix(self, *, write_mode: bool) -> None:
        """Имя расширения не принадлежит схеме: префикс к нему не применяется."""
        service = CatalogService(_basic_prefix_db(write_mode=write_mode))

        details = await service.get_object_details("public", "plpgsql", "extension")
        listed = await service.list_objects("public", "extension")

        assert details["name"] == "plpgsql"
        assert [e["name"] for e in listed] == ["plpgsql"]

    @pytest.mark.parametrize("object_type", ["table", "view", "sequence", "extension"])
    async def test_other_schema_is_rejected_before_any_query(
        self, fake_delegate: MagicMock, object_type: str, *, write_mode: bool
    ) -> None:
        service = CatalogService(_basic_prefix_db(write_mode=write_mode))

        with pytest.raises(SchemaAccessError):
            await service.get_object_details("other", "app_users", object_type)
        with pytest.raises(SchemaAccessError):
            await service.list_objects("other", object_type)

        fake_delegate.execute.assert_not_awaited()

    async def test_list_schemas_does_not_query(self, fake_delegate: MagicMock, *, write_mode: bool) -> None:
        schemas = await CatalogService(_basic_prefix_db(write_mode=write_mode)).list_schemas()

        assert [s["schema_name"] for s in schemas] == ["public"]
        fake_delegate.execute.assert_not_awaited()

    @pytest.mark.parametrize(
        ("sql", "error"),
        [
            ("SELECT * FROM pg_indexes", TablePrefixAccessError),
            ("SELECT * FROM pg_catalog.pg_indexes", SchemaNotAllowedError),
            ("SELECT * FROM pg_catalog.pg_class", SchemaNotAllowedError),
            ("SELECT * FROM other_users", TablePrefixAccessError),
        ],
    )
    async def test_agent_sql_driver_is_still_restricted(
        self, fake_delegate: MagicMock, sql: str, error: type[Exception], *, write_mode: bool
    ) -> None:
        with pytest.raises(error):
            await _basic_prefix_db(write_mode=write_mode).sql_driver.execute(sql)

        fake_delegate.execute.assert_not_awaited()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/domains/test_catalog_service.py tests/unit/postgres/test_catalog_driver.py -q`
Expected: FAIL — `test_catalog_queries_qualify_every_relation` for the index/extension queries; in `TestBasicTablePrefix` prefixed details raise `TablePrefixAccessError('pg_indexes')` (catalog still on `sql_driver`), unprefixed sequence returns details, extensions fail on `pg_extension`. `test_agent_sql_driver_is_still_restricted` and `test_list_schemas_does_not_query` already pass.

- [ ] **Step 3: Qualify system relations**

In `src/postgres_fastmcp/postgres/catalog.py`:

1. Module docstring → `"""Константы SQL-запросов каталога БД (information_schema, pg_catalog) и список разрешённых шаблонов."""`
2. In `QUERY_LIST_EXTENSIONS` and `QUERY_GET_EXTENSION_DETAILS`: `FROM pg_extension` → `FROM pg_catalog.pg_extension`.
3. In `QUERY_GET_INDEXES`: `FROM pg_indexes` → `FROM pg_catalog.pg_indexes`.
4. Replace the three-line comment above `QUERY_TABLE_EXISTS` with:

```python
# Существование таблицы/представления: тот же источник и тот же table_type, что у QUERY_LIST_TABLES_VIEWS
# ('BASE TABLE' = relkind r/p, 'VIEW' = v).
```

- [ ] **Step 3b: Create the prefix helper**

`src/postgres_fastmcp/domains/catalog/prefix.py`:

```python
"""Префикс имён объектов в BASIC: одно правило для списков и деталей каталога."""

from postgres_fastmcp.domains.db_access import DbAccessPort
from postgres_fastmcp.shared.enums import AccessMode


def active_prefix(db: DbAccessPort) -> str | None:
    """Префикс, который действует в запросе: только BASIC с непустым table_prefix."""
    if db.access_mode == AccessMode.BASIC and db.table_prefix:
        return db.table_prefix
    return None


def matches_prefix(name: str, prefix: str) -> bool:
    """Имя начинается с префикса без учёта регистра — то же правило, что у валидатора SQL (schema_guard)."""
    return name.lower().startswith(prefix.lower())
```

- [ ] **Step 4: Switch catalog services to `catalog_driver` and reuse the helper**

`tables.py`:
- Drop the `AccessMode` import; add `from .prefix import active_prefix, matches_prefix`.
- `list_tables_views`: `sql_driver = self.db.sql_driver` → `catalog = self.db.catalog_driver` and `await catalog.execute(...)`; replace the prefix block with:

```python
        prefix = active_prefix(self.db)
        if prefix:
            objects = [o for o in objects if matches_prefix(o["name"], prefix)]
        return objects
```

- `get_details`: `sql_driver = self.db.sql_driver` → `catalog = self.db.catalog_driver`, all four `sql_driver.execute(` → `catalog.execute(`. Replace the docstring's first paragraph with:

```
        Существование решает каталог (QUERY_TABLE_EXISTS), а не пустые разделы: таблица
        без столбцов (CREATE TABLE t()) существует. Схему и префикс имени проверяет
        CatalogService до вызова.
```

`sequences.py`: same — drop `AccessMode` import, add `from .prefix import active_prefix, matches_prefix`, both methods use `self.db.catalog_driver`, and `list_sequences` filters with the same three-line block as above.

`extensions.py`: both methods use `self.db.catalog_driver` instead of `self.db.sql_driver`.

`service.py`:
- `list_schemas`: `self.db.sql_driver.execute(` → `self.db.catalog_driver.execute(`.
- Imports: add `TablePrefixAccessError` to the `shared.errors` import and `from .prefix import active_prefix, matches_prefix`.
- Module-level constant after imports:

```python
# Типы объектов, чьи имена в BASIC обязаны начинаться с table_prefix (как в execute_sql и list_objects).
_PREFIXED_TYPES = frozenset({"table", "view", "sequence"})
```

- New method in `CatalogService` after `_resolve_schema`:

```python
    def _check_prefix(self, object_name: str) -> None:
        """В BASIC с table_prefix имя таблицы, представления или последовательности начинается с префикса.

        Проверка идёт до запросов: ответ одинаков для существующего и несуществующего объекта.
        Расширения не проверяются: имя расширения не принадлежит схеме.

        Raises:
            TablePrefixAccessError: Имя не начинается с префикса.
        """
        prefix = active_prefix(self.db)
        if prefix and not matches_prefix(object_name, prefix):
            raise TablePrefixAccessError(object_name, prefix)
```

- In `get_object_details`, right after `schema_name = self._resolve_schema(schema_name)`:

```python
        if object_type in _PREFIXED_TYPES:
            self._check_prefix(object_name)
```

and add to its `Raises:` section: `TablePrefixAccessError: Если в BASIC с table_prefix имя не начинается с префикса.`

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit -q`
Expected: PASS (existing catalog tests use `mock_db_access`, whose `catalog_driver` is `mock_executor`).

Run: `grep -rn "sql_driver" src/postgres_fastmcp/domains/catalog`
Expected: no output.

- [ ] **Step 6: Lint, type-check, commit**

```bash
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add src/postgres_fastmcp/domains/catalog src/postgres_fastmcp/postgres/catalog.py tests/unit/domains/test_catalog_service.py tests/unit/postgres/test_catalog_driver.py
git commit -m "fix(catalog): serve object details in basic mode with table_prefix"
```

---

### Task 4: интеграционные тесты и документация

**Files:**
- Test: `tests/integration/test_table_prefix.py`
- Modify: `README.md:169`
- Modify: `docs/superpowers/specs/2026-09-28-tool-results-design.md` (§4.1), `docs/superpowers/specs/2026-09-28-basic-prefix-catalog-design.md` (статус)

**Interfaces:**
- Consumes: фикстуры `db_full` (FULL+write, `SqlExecutor`) и `db_user_prefix` (BASIC, read-only, `table_prefix="app_"`) из `tests/integration/conftest.py`; `setup_test_tables(db_full)` из этого же файла (создаёт `app_users(id SERIAL PK, name, email UNIQUE)`, `app_orders(id SERIAL PK, user_id, amount)`, `other_users`, `test_users`); `CatalogService`.

- [ ] **Step 1: Add integration tests**

In `tests/integration/test_table_prefix.py`: extend the `shared.errors` import with `ObjectNotFoundError`, then add after `setup_test_tables`:

```python
async def setup_catalog_objects(driver: DbAccess) -> None:
    """Неуникальный индекс и представление с префиксом для проверки деталей каталога."""
    await driver.sql_driver.execute(
        "CREATE INDEX IF NOT EXISTS app_orders_user_id_idx ON app_orders (user_id)", readonly=False
    )
    await driver.sql_driver.execute(
        "CREATE OR REPLACE VIEW app_active_users AS SELECT id, name FROM app_users", readonly=False
    )
```

and append at the end of the file:

```python
@pytest.mark.asyncio
async def test_get_object_details_shows_indexes_of_prefixed_tables(
    db_full: DbAccess,
    db_user_prefix: DbAccess,
) -> None:
    """BASIC + table_prefix: колонки, ограничения и все индексы, включая неуникальный."""
    await setup_test_tables(db_full)
    await setup_catalog_objects(db_full)
    catalog = CatalogService(db_user_prefix)

    users = await catalog.get_object_details("public", "app_users", "table")
    assert [c["column"] for c in users["columns"]] == ["id", "name", "email"]
    assert {"PRIMARY KEY", "UNIQUE"} <= {c["type"] for c in users["constraints"]}
    assert {"app_users_pkey", "app_users_email_key"} <= {i["name"] for i in users["indexes"]}

    orders = await catalog.get_object_details("public", "app_orders", "table")
    assert {"app_orders_pkey", "app_orders_user_id_idx"} <= {i["name"] for i in orders["indexes"]}

    view = await catalog.get_object_details("public", "app_active_users", "view")
    assert [c["column"] for c in view["columns"]] == ["id", "name"]
    assert view["indexes"] == []


@pytest.mark.asyncio
async def test_get_object_details_rejects_unprefixed_and_reports_missing(
    db_full: DbAccess,
    db_user_prefix: DbAccess,
) -> None:
    await setup_test_tables(db_full)
    catalog = CatalogService(db_user_prefix)

    with pytest.raises(TablePrefixAccessError, match="'other_users'"):
        await catalog.get_object_details("public", "other_users", "table")
    with pytest.raises(ObjectNotFoundError):
        await catalog.get_object_details("public", "app_ghost", "table")

    sequence = await catalog.get_object_details("public", "app_users_id_seq", "sequence")
    assert sequence["name"] == "app_users_id_seq"
    with pytest.raises(TablePrefixAccessError, match="'other_users_id_seq'"):
        await catalog.get_object_details("public", "other_users_id_seq", "sequence")


@pytest.mark.asyncio
async def test_extensions_are_listed_and_detailed_regardless_of_prefix(db_user_prefix: DbAccess) -> None:
    catalog = CatalogService(db_user_prefix)

    listed = await catalog.list_objects("public", "extension")
    assert "plpgsql" in {e["name"] for e in listed}

    details = await catalog.get_object_details("public", "plpgsql", "extension")
    assert details["name"] == "plpgsql"


@pytest.mark.asyncio
async def test_agent_sql_still_cannot_read_system_catalogs(db_user_prefix: DbAccess) -> None:
    """Путь каталога не открывает системные представления для execute_sql."""
    with pytest.raises(TablePrefixAccessError):
        await db_user_prefix.sql_driver.execute("SELECT indexname FROM pg_indexes", readonly=True)
    with pytest.raises(SchemaNotAllowedError):
        await db_user_prefix.sql_driver.execute("SELECT indexname FROM pg_catalog.pg_indexes", readonly=True)
```

- [ ] **Step 2: Run the integration suite (skips locally) and verify statically**

Run: `uv run pytest tests/integration/test_table_prefix.py -q`
Expected: tests collected and skipped (no Docker) — no import or collection errors.

Static check against real code, record the answers in the task report:
- `app_users.email VARCHAR(255) UNIQUE` → Postgres names the index `app_users_email_key`; `SERIAL PRIMARY KEY` → `app_users_pkey`, sequence `app_users_id_seq`; `other_users` → `other_users_id_seq`.
- `QUERY_GET_COLUMNS` orders by `ordinal_position`, so `["id", "name", "email"]`.
- `information_schema.table_constraints` also lists NOT NULL as `CHECK` — hence `<=`, not `==`.
- `CatalogService.get_object_details` returns `decode_bytes_to_utf8(result)`; keys `basic/columns/constraints/indexes` for relations, `name` for sequences and extensions.

- [ ] **Step 3: Update documentation**

`README.md:169` — replace the line with:

```markdown
**Для access_mode=basic опционально:** `table_prefix` ограничивает таблицы, представления и последовательности по префиксу имени: `list_objects` скрывает объекты без префикса, а `get_object_details` и `execute_sql` отказывают по ним. Расширения префиксом не ограничиваются. Для full игнорируется.
```

`docs/superpowers/specs/2026-09-28-tool-results-design.md` — append a paragraph at the end of §4.1 (after the paragraph starting `На \`769ad48\``):

```markdown
Исправлено отдельной задачей: `docs/superpowers/specs/2026-09-28-basic-prefix-catalog-design.md`.
```

`docs/superpowers/specs/2026-09-28-basic-prefix-catalog-design.md` — in the status line, `Статус: согласовано, к реализации.` → `Статус: реализовано.`

- [ ] **Step 4: Full verification, lint, commit**

```bash
uv run pytest tests/unit -q
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add tests/integration/test_table_prefix.py README.md docs/superpowers/specs
git commit -m "test(catalog): cover basic mode with table_prefix against Postgres"
```
