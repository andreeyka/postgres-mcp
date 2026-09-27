# Tool Results Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `execute_sql` для оператора без результирующего набора отдаёт тег команды Postgres и число затронутых строк, а `get_object_details` узнаёт о существовании объекта из каталога, поэтому пустая таблица больше не «не найдена».

**Architecture:** План по спеке `docs/superpowers/specs/2026-09-28-tool-results-design.md` поверх `main` `769ad48`. Сначала слой `postgres/` получает `StatementResult` и метод `execute_statement` у `SqlExecutor`, `SafeSqlExecutor` и `SqlDriverPort` (Task 1). В `SafeSqlExecutor` общий путь `_guarded` обслуживает и `execute`, и `execute_statement`. Затем `execute_sql` переходит на `execute_statement` и рисует итог через `rendering.statement_result` (Task 2). Потом каталог сам решает, существует ли объект: `TablesService` проверяет `information_schema.tables` в том же `gather`, а `CatalogService` бросает `ObjectNotFoundError` (Task 3). Последняя задача — полная локальная проверка (Task 4).

**Tech Stack:** Python 3.12+, uv, FastMCP 4.0.10, pydantic 2.13.4, psycopg 3.3.4 (binary, libpq 18) / psycopg-pool, pytest + pytest-asyncio (asyncio_mode=auto) + pytest-timeout, ruff 0.15.21 (select=ALL, тесты тоже), mypy 2.3.0 strict.

## Global Constraints

- Язык: всё, что видит агент или внешняя система (тексты ответа тулов, сообщения ошибок, описания, логи, коммиты), пишется на английском. По-русски только комментарии и docstring, которые видит лишь разработчик. README — русская пользовательская документация.
- Нигде нет `from __future__ import annotations`.
- Перед каждым коммитом: `uv run ruff check --fix --show-fixes`, `uv run mypy src/`, `uv run ruff format` и `uv run ruff check .` — все с нулём ошибок (тесты входят в `ruff check .`).
- Юнит-тесты: `uv run pytest tests/unit -q`. Переменные `MCP_DATABASE_*` задаёт autouse-фикстура `tests/unit/conftest.py::_database_env`. Базовая линия до плана: `990 passed`.
- Docker локально недоступен: `uv run pytest tests/integration -q` только собирает и пропускает тесты (до плана `96 skipped`). CI гоняет их на Postgres 15 и 16. Интеграционные правки проверяются статически: сбор без ошибок и `uv run ruff check <файл> --select F,ARG --no-fix`.
- Ломающие изменения разрешены, легаси не сохраняется: ни совместимых обёрток, ни предупреждений об устаревании (`SUCCESS_NO_ROWS` и `rows_result(title=...)` удаляются).
- Субагенты никогда не запускают `git config` и `git stash` (в репозитории есть чужая старая запись stash — не применять и не удалять).
- Работа идёт в текущей ветке `claude/tool-results`. Отдельных веток, push и PR в плане нет; последняя задача — полная локальная проверка.
- Формат коммитов: `type(scope): message`, повелительное наклонение, английский.
- Только `querying.execute_sql` вызывает `execute_statement`; остальные домены продолжают звать `execute`.
- Описания тулов не меняются: размер `tools/list` в режиме FULL — 9710 символов до и после плана (лимит теста `10_774` не трогаем).

## Проверено на установленных версиях (FastMCP 4.0.10, pydantic 2.13.4, psycopg 3.3.4, Postgres 16.4)

Справка для исполнителя; каждое утверждение проверено экспериментом или чтением кода `.venv/lib/python3.12/site-packages`. Весь план прогнан в отсоединённом `git worktree` (scratchpad) против `.venv` проекта (`PYTHONPATH=<worktree>/src`, без `uv sync`), поэтому числа тестов ниже реальные. Интеграционные тесты, которые план меняет или добавляет, дополнительно прогнаны на локальном Postgres 16.4: бинарники zonky `embedded-postgres-binaries-linux-arm64v8`, фикстура `test_postgres_connection_string` временно подменена только в worktree. Из 50 тестов прошли 48; `test_create_extension_hypopg_allowed_in_basic_write` и `test_get_top_queries_integration` падают и на `769ad48`, потому что в сборке нет `hypopg` и `pg_stat_statements`. После прогона worktree удалён, editable-установка указывает на репозиторий.

- **`statusmessage` после префикса `SET LOCAL`.** Без параметров psycopg шлёт строку по простому протоколу (`_cursor_base.py::_execute_send` → `send_query`), и libpq отдаёт по результату на оператор. `nextset()` двигает `_iresult`, `_select_current_result` выставляет `_statusmessage = res.command_status` и `_rowcount = res.command_tuples` (или `-1`). После `while cursor.nextset(): pass` текущий результат — последний, то есть оператор пользователя. На Postgres 16.4 для строки `SET LOCAL statement_timeout = 30000; SET LOCAL search_path = public; /* tag */ <sql>` получено:

  | `<sql>` | `statusmessage` | `rowcount` | `description is None` |
  | --- | --- | --- | --- |
  | `INSERT INTO t SELECT generate_series(1,5)` | `INSERT 0 5` | 5 | да |
  | `UPDATE t SET id = id + 1` | `UPDATE 5` | 5 | да |
  | `DELETE FROM t WHERE id > 100` | `DELETE 0` | 0 | да |
  | `MERGE INTO t USING ... WHEN NOT MATCHED THEN INSERT ...` | `MERGE 1` | 1 | да |
  | `CREATE TABLE t2 AS SELECT * FROM t` / `SELECT * INTO t3 FROM t` | `SELECT 6` | 6 | да |
  | `ALTER TABLE ...`, `CREATE INDEX ...`, `TRUNCATE`, `COMMENT`, `DO $$...$$` | тег без числа | -1 | да |
  | `SELECT 1 WHERE false` | `SELECT 0` | 0 | нет |
  | `UPDATE t SET v = 'x' RETURNING id` | `UPDATE 6` | 6 | нет |
  | `UPDATE ...;`, `UPDATE ...;;`, `UPDATE ...; -- tail` | `UPDATE 6` (результатов 3, лишнего нет) | 6 | да |
- **`COMMIT` меняет тег.** После `await cursor.execute("COMMIT")` на том же курсоре `statusmessage == "COMMIT"`. Поэтому тег и `rowcount` читаются до `COMMIT`/`ROLLBACK`.
- **`rowcount` = libpq `PQcmdTuples`.** `pq_ctypes.PGresult.command_tuples` возвращает `int(PQcmdTuples)` или `None` для пустой строки; libpq заполняет её для `INSERT/UPDATE/DELETE/MERGE/SELECT/CREATE TABLE AS/COPY/FETCH/MOVE`. `psycopg.pq.version()` = `180000`, так что `MERGE` поддержан.
- **Поведение на `769ad48` (Postgres 16.4, `create_server` + `Client`):**

  | Режим | Вызов | Ответ |
  | --- | --- | --- |
  | full | `execute_sql UPDATE t SET id = 1` | `Statement executed successfully; no rows were returned.\n\n0 rows.` |
  | full, json | то же | `{"rows": [], "row_count": 0}` |
  | full / basic | `get_object_details t_empty` (`CREATE TABLE t_empty()`) | `Object not found: public.t_empty (table). ...` |
  | full | `get_object_details t, object_type=view` (это таблица) | столбцы таблицы с `type: view` |
  | basic + `table_prefix=app_` | `get_object_details` для `app_empty`, `app_full`, `other_full`, `ghost` | у всех `Access to table 'pg_indexes' is not allowed. Only tables with names starting with 'app_' are permitted.` |
  | basic + `table_prefix=app_` | `get_object_details plpgsql, extension` | `Access to table 'pg_extension' is not allowed ...` |
- **Валидатор BASIC и каталог.** `QueryValidator(allowed_schema="public", table_prefix="app_")` отклоняет неквалифицированные `pg_class`, `pg_indexes` и `pg_extension` (`TablePrefixAccessError`), а `pg_catalog.pg_class` — при любом `table_prefix` (`SchemaNotAllowedError`). `information_schema.tables`, `information_schema.columns` и `to_regclass(...)` проходят. Отсюда существование через `information_schema.tables`, а не `pg_class`.
- **`information_schema.tables`** для `CREATE TABLE t_empty()` отдаёт `('public', 't_empty', 'BASE TABLE')`, для `information_schema.tables` — `('information_schema', 'tables', 'VIEW')`. `information_schema.columns` для `t_empty` пуст.
- **`gather` и ошибка префикса.** `SafeSqlExecutor.execute` вызывает валидатор до первого `await`, поэтому задача запроса `pg_indexes` падает на первом шаге. `asyncio.gather` пробрасывает первое исключение, а «не найдено» проверяется только после успешного `gather`. Значит, в BASIC+префикс ошибка остаётся прежней при любом положении запроса существования в `gather`.
- **Зависание teardown на `769ad48`.** Интеграционный тест `CatalogService(db_user_prefix).get_object_details(...)` получает `TablePrefixAccessError`, после чего teardown зависает (pytest-timeout 30 с). Соседние задачи `gather` не отменяются. Так же ведёт себя и код до плана, поэтому BASIC+префикс закреплён юнит-тестом на настоящем `SafeSqlExecutor`, а не интеграционным.
- **Фейки драйвера в тестах.** `tests/unit/test_provider.py`, `tests/unit/app/test_auth_http.py`, `tests/unit/tools/test_registry.py`, `tests/unit/app/test_response_budget.py` подменяют `sql_driver.execute` через `AsyncMock`. После Task 2 `execute_sql` зовёт `execute_statement` и получает от `MagicMock` не-awaitable (`TypeError: object MagicMock can't be used in 'await' expression`, 25 падений). `list_schemas` у admin в `test_auth_http.py` по-прежнему зовёт `execute`, поэтому `execute_statement` **добавляется рядом**, а не вместо.
- **`MagicMock(spec=AsyncConnection)`** проходит `isinstance(..., AsyncConnection)` в `SqlExecutor.execute_statement`, `set_autocommit` у него — `AsyncMock`, а `cursor.return_value` можно заменить своим фейком. Так тест доходит до `_execute_with_connection` через публичный метод.
- Размер `tools/list` в режиме FULL при `auth.mode=none`: 9710 символов до и после плана.

## Решения там, где спека открыта

- **`SafeSqlExecutor` делит код через `_guarded[T](query, params, run)`** (обобщение PEP 695). `run` — `self._delegate.execute` или `self._delegate.execute_statement`. `_run[T]` вызывает `run(query, params=None, readonly=...)` и разбирает `QueryCanceled`. `execute` по-прежнему зовёт `delegate.execute`, поэтому существующие тесты с `mock_delegate.execute` не меняются. `delegate` типизируется как `SqlDriverPort` вместо `Any` с `noqa: ANN401`, `cast` уходит.
- **`execute_statement` — в `SqlDriverPort`, не в `QueryExecutorPort`.** От `QueryExecutorPort` зависят расчёты `health` и `extensions`; им тег не нужен.
- **`querying.execute_sql` возвращает `list[dict[str, Any]] | StatementResult`**: строки, если есть результирующий набор, иначе сам `StatementResult` (`rows=None`). Тул различает их через `isinstance`. Отдельная доменная модель с теми же тремя полями не нужна.
- **`statement_result(status, affected_rows, output)` в `tools/rendering.py`** принимает скаляры, а не `StatementResult`: модуль рендера не зависит от слоя `postgres`. Если `status` вдруг `None`, пишется `Statement executed: ...`.
- **Тексты:** `"{status}: {n} rows affected."` (как `N rows.` — без согласования числа) и `"{status}: done."` для тега без числа.
- **Проверка существования возвращает `None` из профильных сервисов**, а бросает только фасад `CatalogService`, со схемой после `_resolve_schema` (в BASIC — `public`).
- **`_TABLE_TYPES = {"table": "BASE TABLE", "view": "VIEW"}`** в `tables.py` обслуживает и `list_tables_views`, и проверку существования: соответствие типов задаётся в одном месте.

## Отступления от спеки

Нет. Отступление от согласованного дизайна (`information_schema.tables` вместо `pg_class`) описано в спеке, §5.

## Файлы

| Файл | Ответственность |
| --- | --- |
| `src/postgres_fastmcp/postgres/models.py` | `StatementResult` |
| `src/postgres_fastmcp/postgres/driver.py` | `SqlExecutor.execute_statement`; `execute` — обёртка; тег до `COMMIT` |
| `src/postgres_fastmcp/postgres/security/driver.py` | `SafeSqlExecutor.execute_statement`, общий `_guarded`/`_run` |
| `src/postgres_fastmcp/postgres/ports.py` | `SqlDriverPort.execute_statement` |
| `src/postgres_fastmcp/postgres/catalog.py` | `QUERY_TABLE_EXISTS` |
| `src/postgres_fastmcp/domains/querying.py` | `execute_statement`, возврат `StatementResult`; без `SUCCESS_NO_ROWS` |
| `src/postgres_fastmcp/domains/catalog/{tables,sequences,extensions,service}.py` | `None` для отсутствующего объекта; `ObjectNotFoundError` в фасаде |
| `src/postgres_fastmcp/tools/rendering.py` | `statement_result`; `rows_result` без `title` |
| `src/postgres_fastmcp/tools/definitions.py` | `execute_sql` через `statement_result`; без вывода «не найдено» |
| `README.md`, `src/postgres_fastmcp/tools/AGENTS.md` | Вывод `execute_sql` для DML/DDL |
| `tests/unit/postgres/test_sql_executor.py`, `tests/unit/postgres/test_safe_sql_executor.py` | Драйверы |
| `tests/unit/domains/test_querying.py`, `tests/unit/tools/test_rendering.py`, `tests/unit/tools/test_definitions.py` | `execute_sql`, рендер, тулы |
| `tests/unit/test_provider.py`, `tests/unit/app/test_auth_http.py`, `tests/unit/tools/test_registry.py`, `tests/unit/app/test_response_budget.py` | Фейки драйвера |
| `tests/unit/domains/test_catalog_service.py` | Существование объектов, BASIC+префикс |
| `tests/integration/test_write_mode.py`, `tests/integration/test_tools_integration.py` | Интеграция на реальной БД (CI) |

---

### Task 1: `StatementResult` и `execute_statement` в драйверах

**Files:**
- Modify: `src/postgres_fastmcp/postgres/models.py:1` (docstring), `:14` (после `RowResult`)
- Modify: `src/postgres_fastmcp/postgres/driver.py:18`, `:93-134` (`execute`), `:143-181` (`_execute_with_connection`)
- Modify: `src/postgres_fastmcp/postgres/ports.py:10`, конец `SqlDriverPort` (`:35-41`)
- Modify: `src/postgres_fastmcp/postgres/security/driver.py:3-14` (импорты), `:68-83` (`__init__`), `:85-144` (`execute`, `_run`)
- Test: `tests/unit/postgres/test_sql_executor.py`, `tests/unit/postgres/test_safe_sql_executor.py`

**Interfaces:**
- Consumes: `RowResult` (без изменений).
- Produces:
  - `postgres_fastmcp.postgres.models.StatementResult(rows: list[RowResult] | None, status: str | None, affected_rows: int | None)` — `@dataclass(frozen=True, slots=True)`.
  - `SqlExecutor.execute_statement(query: str | LiteralString, params: list[Any] | None = None, *, readonly: bool = True) -> StatementResult`.
  - `SafeSqlExecutor.execute_statement(query: str, params: list[Any] | None = None, *, readonly: bool = True) -> StatementResult` (`readonly` игнорируется, как у `execute`).
  - `SqlDriverPort.execute_statement(...)` с той же сигнатурой.
  - `SqlExecutor._execute_with_connection(...) -> StatementResult` (было `list[RowResult] | None`).

- [ ] **Step 1: Тесты**

В `tests/unit/postgres/test_sql_executor.py` импорты:

```python
from typing import Self
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from psycopg import AsyncConnection, InterfaceError, OperationalError
```

и

```python
from postgres_fastmcp.postgres.models import RowResult, StatementResult
```

Два теста подменяют `_execute_with_connection`, который теперь отдаёт `StatementResult`. В `test_execute_returns_row_results`:

```python
            mock_exec.return_value = StatementResult(
                rows=[RowResult(cells={"x": 1})], status="SELECT 1", affected_rows=1
            )
```

В `test_execute_with_params_renders_query_before_run`:

```python
            mock_exec.return_value = StatementResult(rows=[], status="SELECT 0", affected_rows=0)
```

В конец файла:

```python
class _FakeCursor:
    """Курсор psycopg в миниатюре: результаты строки запроса по очереди, как после nextset().

    Как у psycopg 3.3: statusmessage и rowcount относятся к текущему результату,
    а COMMIT/ROLLBACK на том же курсоре заменяет их своими.
    """

    def __init__(self, *results: tuple[str, int, list[dict] | None]) -> None:
        self._pending = list(results)
        self._current: tuple[str, int, list[dict] | None] = ("", -1, None)
        self.executed: list[str] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, query: str, params: object = None) -> None:
        self.executed.append(query)
        if query.startswith(("BEGIN", "COMMIT", "ROLLBACK")):
            self._current = (query.split(maxsplit=1)[0], -1, None)
        else:
            self._current = self._pending.pop(0)

    def nextset(self) -> bool | None:
        if not self._pending:
            return None
        self._current = self._pending.pop(0)
        return True

    @property
    def statusmessage(self) -> str:
        return self._current[0]

    @property
    def rowcount(self) -> int:
        return self._current[1]

    @property
    def description(self) -> list[object] | None:
        return None if self._current[2] is None else [object()]

    async def fetchall(self) -> list[dict]:
        return self._current[2] or []


def _executor_on(cursor: _FakeCursor) -> SqlExecutor:
    """SqlExecutor на одиночном подключении, которое отдаёт cursor."""
    conn = MagicMock(spec=AsyncConnection)
    conn.cursor.return_value = cursor
    return SqlExecutor(conn=conn)


class TestSqlExecutorExecuteStatement:
    """execute_statement: тег команды последнего оператора, снятый до COMMIT/ROLLBACK."""

    async def test_status_of_last_statement_after_set_local_prefix(self) -> None:
        """Префикс SET LOCAL даёт свои результаты; тег и счётчик — от оператора пользователя."""
        cursor = _FakeCursor(("SET", -1, None), ("SET", -1, None), ("UPDATE 3", 3, None))
        executor = _executor_on(cursor)

        result = await executor.execute_statement(
            "SET LOCAL statement_timeout = 1000; SET LOCAL search_path = public; UPDATE t SET v = 1",
            readonly=False,
        )

        assert result == StatementResult(rows=None, status="UPDATE 3", affected_rows=3)
        assert cursor.executed[-1] == "COMMIT"

    async def test_ddl_has_status_without_count(self) -> None:
        """У DDL в теге нет числа: psycopg отдаёт rowcount -1, affected_rows — None."""
        executor = _executor_on(_FakeCursor(("CREATE TABLE", -1, None)))

        result = await executor.execute_statement("CREATE TABLE t ()", readonly=False)

        assert result == StatementResult(rows=None, status="CREATE TABLE", affected_rows=None)

    async def test_rows_and_status_for_select(self) -> None:
        """Оператор с результирующим набором: строки, тег SELECT и их число."""
        cursor = _FakeCursor(("SELECT 1", 1, [{"a": 1}]))
        executor = _executor_on(cursor)

        result = await executor.execute_statement("SELECT 1 AS a")

        assert result == StatementResult(rows=[RowResult(cells={"a": 1})], status="SELECT 1", affected_rows=1)
        assert cursor.executed == ["BEGIN TRANSACTION READ ONLY", "SELECT 1 AS a", "ROLLBACK"]

    async def test_execute_returns_rows_of_execute_statement(self) -> None:
        """Метод execute остаётся прежним: строки или None, без тега."""
        executor = _executor_on(_FakeCursor(("INSERT 0 2", 2, None)))

        assert await executor.execute("INSERT INTO t VALUES (1), (2)", readonly=False) is None
```

В `tests/unit/postgres/test_safe_sql_executor.py` импорт:

```python
from postgres_fastmcp.postgres.models import RowResult, StatementResult
```

и новый класс перед `@pytest.mark.parametrize(("message_primary", "elapsed", "timeout", "expected"), ...)`:

```python
class TestSafeSqlExecutorExecuteStatement:
    """execute_statement идёт тем же путём, что execute, но через delegate.execute_statement."""

    async def test_validates_prefixes_and_delegates(self) -> None:
        """Тег, SET LOCAL и read_only из конфигурации; результат делегата возвращается как есть."""
        expected = StatementResult(rows=None, status="UPDATE 3", affected_rows=3)
        mock_delegate = MagicMock()
        mock_delegate.execute_statement = AsyncMock(return_value=expected)
        config = SafeSqlConfig(query_tag="t", timeout=5, allowed_schema="public", read_only=False)
        executor = _make_executor(mock_delegate, validator=QueryValidator(read_only=False), config=config)

        result = await executor.execute_statement("UPDATE t SET v = 1", readonly=True)

        assert result is expected
        mock_delegate.execute_statement.assert_awaited_once_with(
            "SET LOCAL statement_timeout = 5000; SET LOCAL search_path = public; /* t */ UPDATE t SET v = 1",
            params=None,
            readonly=False,
        )
        mock_delegate.execute.assert_not_called()

    async def test_invalid_query_raises_before_delegate(self) -> None:
        """Валидатор отклоняет запрос до обращения к делегату."""
        mock_delegate = MagicMock()
        mock_delegate.execute_statement = AsyncMock()
        executor = _make_executor(mock_delegate, validator=QueryValidator(read_only=True, allowed_schema="public"))

        with pytest.raises(SchemaNotAllowedError):
            await executor.execute_statement("SELECT * FROM other_schema.t")
        mock_delegate.execute_statement.assert_not_called()

    async def test_statement_timeout_cancel_maps_to_query_timeout_error(self) -> None:
        """Отмена по statement_timeout превращается в QueryTimeoutError, как у execute."""
        mock_delegate = MagicMock()
        mock_delegate.execute_statement = AsyncMock(
            side_effect=_query_canceled("canceling statement due to statement timeout")
        )
        executor = _make_executor(mock_delegate, config=SafeSqlConfig(query_tag="t", timeout=30))

        with pytest.raises(QueryTimeoutError):
            await executor.execute_statement("SELECT 1")

    async def test_client_timeout_raises_after_grace(self) -> None:
        """Клиентская страховка действует и для execute_statement."""

        async def slow(*args: object, **kwargs: object) -> StatementResult:
            await asyncio.sleep(1)
            return StatementResult(rows=None, status="UPDATE 0", affected_rows=0)

        mock_delegate = MagicMock()
        mock_delegate.execute_statement = AsyncMock(side_effect=slow)
        config = SafeSqlConfig(query_tag="t", timeout=0.01, client_timeout_grace=0.01)
        executor = _make_executor(mock_delegate, config=config)

        with pytest.raises(QueryTimeoutError):
            await executor.execute_statement("SELECT 1")
```

- [ ] **Step 2: Тесты падают**

Run: `uv run pytest tests/unit/postgres/test_sql_executor.py tests/unit/postgres/test_safe_sql_executor.py -q`
Expected: ошибки сбора обоих модулей — `ImportError: cannot import name 'StatementResult' from 'postgres_fastmcp.postgres.models'`.

- [ ] **Step 3: `StatementResult`**

`src/postgres_fastmcp/postgres/models.py`: docstring модуля:

```python
"""Модели данных слоя SQL: строка результата, результат оператора и определение индекса."""
```

После класса `RowResult`:

```python
@dataclass(frozen=True, slots=True)
class StatementResult:
    """Результат одного оператора: строки и тег команды Postgres.

    Attributes:
        rows: Строки результата; None, если у оператора нет результирующего набора (DML без RETURNING, DDL).
        status: Тег команды (cursor.statusmessage), например "UPDATE 3" или "CREATE TABLE".
        affected_rows: Число строк из тега (cursor.rowcount = libpq PQcmdTuples): INSERT/UPDATE/DELETE/MERGE,
            SELECT, CREATE TABLE AS, COPY, FETCH, MOVE. None, если в теге нет числа (DDL, DO, SET).
    """

    rows: list[RowResult] | None
    status: str | None
    affected_rows: int | None
```

- [ ] **Step 4: `SqlExecutor.execute_statement`**

`src/postgres_fastmcp/postgres/driver.py:18`:

```python
from postgres_fastmcp.postgres.models import RowResult, StatementResult
```

Метод `execute` (`:93-134`) заменить на два метода. Тело прежнего `execute`, начиная с `if params:`, переезжает в `execute_statement` без изменений:

```python
    async def execute(
        self,
        query: str | LiteralString,
        params: list[Any] | None = None,
        *,
        readonly: bool = True,
    ) -> list[RowResult] | None:
        """Выполнение запроса и возвращение строк, или None для операторов без результата.

        Args:
            query: SQL для выполнения (используйте {} для плейсхолдеров если заданы параметры).
            params: Необязательные параметры; если заданы, запрос рендерится и then выполняется.
            readonly: Если True, использовать транзакцию только для чтения; иначе чтение-запись.

        Returns:
            Список RowResult или None для DDL/командных операторов.
        """
        return (await self.execute_statement(query, params, readonly=readonly)).rows

    async def execute_statement(
        self,
        query: str | LiteralString,
        params: list[Any] | None = None,
        *,
        readonly: bool = True,
    ) -> StatementResult:
        """Выполнение запроса: строки и тег команды Postgres ("UPDATE 3", "CREATE TABLE").

        Args:
            query: SQL для выполнения (используйте {} для плейсхолдеров если заданы параметры).
            params: Необязательные параметры; если заданы, запрос рендерится перед выполнением.
            readonly: Если True, использовать транзакцию только для чтения; иначе чтение-запись.

        Returns:
            StatementResult последнего оператора строки запроса.
        """
        if params:
            query = self.render(query, params)
            params = None

        def _fail() -> NoReturn:
            raise ConnectionNotEstablishedError

        try:
            self._ensure_connected()
            if self.conn is None:
                _fail()
            if self._is_pool and isinstance(self.conn, DbConnPool):
                pool = await self.conn.pool_connect()
                async with pool.connection() as connection:
                    await connection.set_autocommit(True)
                    return await self._execute_with_connection(connection, query, params, readonly=readonly)
            if isinstance(self.conn, AsyncConnection):
                if hasattr(self.conn, "set_autocommit"):
                    await self.conn.set_autocommit(True)
                return await self._execute_with_connection(self.conn, query, params, readonly=readonly)
            _fail()
        except Exception as e:
            if _is_connection_error(e):
                self._invalidate(e)
            raise
```

`_execute_with_connection` (`:143-181`) целиком:

```python
    async def _execute_with_connection(
        self,
        connection: AsyncConnection[Any],
        query: str | LiteralString,
        params: list[Any] | None,
        *,
        readonly: bool,
    ) -> StatementResult:
        """Выполнение запроса на данном подключении с явной транзакцией.

        Строка может содержать несколько операторов (префикс SET LOCAL от SafeSqlExecutor):
        после nextset() текущим становится результат последнего, то есть оператора пользователя.
        """
        async with connection.cursor(row_factory=dict_row) as cursor:
            if readonly:
                await cursor.execute("BEGIN TRANSACTION READ ONLY")
            else:
                await cursor.execute("BEGIN")
            try:
                if params:
                    await cursor.execute(query, params)
                else:
                    await cursor.execute(query)
                while cursor.nextset():
                    pass
                # Тег и счётчик читаются до COMMIT/ROLLBACK: после них statusmessage станет "COMMIT"/"ROLLBACK"
                status = cursor.statusmessage
                affected_rows = cursor.rowcount if cursor.rowcount >= 0 else None
                rows = None
                if cursor.description is not None:
                    rows = [RowResult(cells=dict(row)) for row in await cursor.fetchall()]
                if readonly:
                    await cursor.execute("ROLLBACK")
                else:
                    await cursor.execute("COMMIT")
                return StatementResult(rows=rows, status=status, affected_rows=affected_rows)
            except Exception:
                try:
                    await cursor.execute("ROLLBACK")
                except Exception as rollback_error:
                    logger.error("Error rolling back transaction: %s", rollback_error)
                raise
```

- [ ] **Step 5: Порт и `SafeSqlExecutor`**

`src/postgres_fastmcp/postgres/ports.py:10`:

```python
from postgres_fastmcp.postgres.models import RowResult, StatementResult
```

В конец тела `SqlDriverPort` (после docstring):

```python
    async def execute_statement(
        self,
        query: str,
        params: list[Any] | None = None,
        *,
        readonly: bool = True,
    ) -> StatementResult:
        """Выполнение запроса: строки и тег команды Postgres (для execute_sql)."""
        ...
```

`src/postgres_fastmcp/postgres/security/driver.py`, импорты (`:3-14`):

```python
import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any

from psycopg.errors import QueryCanceled
from psycopg.sql import SQL, Composable, Literal

from postgres_fastmcp.postgres.models import RowResult, StatementResult
from postgres_fastmcp.postgres.ports import SqlDriverPort
from postgres_fastmcp.postgres.security.query_validator import QueryValidator
from postgres_fastmcp.shared.errors import QueryCancelledError, QueryTimeoutError
```

В `__init__` параметр и строка docstring:

```python
        delegate: SqlDriverPort,
```

```python
            delegate: Исполнитель без проверок (SqlExecutor): execute и execute_statement.
```

Тело `execute` после docstring (от `query = self.render(...)` до конца `_run`, `:106-144`) заменить на:

```python
        return await self._guarded(query, params, self._delegate.execute)

    async def execute_statement(
        self,
        query: str,
        params: list[Any] | None = None,
        *,
        readonly: bool = True,  # noqa: ARG002 — part of SqlDriverPort; effective value from config
    ) -> StatementResult:
        """То же, что execute, но со строками отдаёт тег команды Postgres ("UPDATE 3", "CREATE TABLE").

        Валидация, statement_timeout, search_path, клиентская страховка и разбор отмены — те же,
        что у execute: оба метода идут через _guarded.

        Raises:
            QueryTimeoutError: Postgres отменил запрос по statement_timeout либо сработала клиентская страховка.
            QueryCancelledError: Postgres отменил запрос по другой причине.
        """
        return await self._guarded(query, params, self._delegate.execute_statement)

    async def _guarded[T](
        self,
        query: str,
        params: list[Any] | None,
        run: Callable[..., Awaitable[T]],
    ) -> T:
        """Тег, валидация, SET LOCAL и клиентская страховка вокруг метода делегата run."""
        query = self.render(query, params) if params else f"/* {self._config.query_tag} */ {query}"
        self._validator.validate(query)
        query = self._with_session_settings(query)
        if self._config.timeout is None:
            return await self._run(query, run)
        try:
            async with asyncio.timeout(self._config.timeout + self._config.client_timeout_grace):
                return await self._run(query, run)
        except TimeoutError as e:
            logger.warning(
                "Client-side timeout after %ss: %s...",
                self._config.timeout,
                query[:100],
            )
            raise QueryTimeoutError(self._config.timeout) from e

    async def _run[T](self, query: str, run: Callable[..., Awaitable[T]]) -> T:
        """Выполнить через делегата; отмену по statement_timeout превратить в QueryTimeoutError.

        Любая другая отмена (pg_cancel_backend, запрос пользователя) становится QueryCancelledError,
        чтобы не выдавать её за таймаут.
        """
        started = monotonic()
        try:
            return await run(query, params=None, readonly=self._config.read_only)
        except QueryCanceled as e:
            reason = e.diag.message_primary or ""
            if _is_statement_timeout(reason, elapsed=monotonic() - started, timeout=self._config.timeout):
                logger.warning(
                    "Postgres cancelled the statement (statement_timeout=%ss): %s...",
                    self._config.timeout,
                    query[:100],
                )
                raise QueryTimeoutError(self._config.timeout or 0.0) from e
            logger.warning("Postgres cancelled the statement (%s): %s...", reason or "no reason", query[:100])
            raise QueryCancelledError from e
```

`_with_session_settings` и `render` не меняются.

- [ ] **Step 6: Тесты проходят**

Run: `uv run pytest tests/unit/postgres/test_sql_executor.py tests/unit/postgres/test_safe_sql_executor.py -q && uv run pytest tests/unit -q`
Expected: `47 passed`; `998 passed`.

- [ ] **Step 7: Гейты и коммит**

Run: `uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .`
Expected: `All checks passed!`, `Success: no issues found in 78 source files`, `143 files left unchanged`, `All checks passed!`.

```bash
git add src/postgres_fastmcp/postgres/models.py src/postgres_fastmcp/postgres/driver.py \
  src/postgres_fastmcp/postgres/ports.py src/postgres_fastmcp/postgres/security/driver.py \
  tests/unit/postgres/test_sql_executor.py tests/unit/postgres/test_safe_sql_executor.py
git commit -m "feat(postgres): return the command status from execute_statement"
```

---

### Task 2: `execute_sql` отдаёт тег команды и число затронутых строк

**Files:**
- Modify: `src/postgres_fastmcp/tools/rendering.py:18-33` (`rows_result`), новая `statement_result` перед `sections_result`
- Modify: `src/postgres_fastmcp/domains/querying.py` (целиком)
- Modify: `src/postgres_fastmcp/tools/definitions.py:23`, `:33`, `:60-63`
- Modify: `README.md:498` (после пункта про `output`), `src/postgres_fastmcp/tools/AGENTS.md:60`
- Test: `tests/unit/tools/test_rendering.py`, `tests/unit/domains/test_querying.py`, `tests/unit/tools/test_definitions.py`, `tests/unit/test_provider.py`, `tests/unit/app/test_auth_http.py`, `tests/unit/tools/test_registry.py`, `tests/unit/app/test_response_budget.py`, `tests/integration/test_write_mode.py`

**Interfaces:**
- Consumes: `StatementResult`, `SqlDriverPort.execute_statement` (Task 1).
- Produces:
  - `rendering.statement_result(status: str | None, affected_rows: int | None, output: OutputFormat) -> ToolResult`.
  - `rendering.rows_result(rows: list[dict[str, Any]], output: OutputFormat) -> ToolResult` — параметра `title` больше нет.
  - `querying.execute_sql(db: DbAccessPort, sql: str) -> list[dict[str, Any]] | StatementResult`; `querying.SUCCESS_NO_ROWS` удалён.

- [ ] **Step 1: Тесты**

`tests/unit/tools/test_rendering.py`: импорт

```python
from postgres_fastmcp.tools.rendering import rows_result, sections_result, statement_result
```

Удалить `test_title_goes_above_the_table`. В `test_json_empty_rows` вызов без `title`:

```python
    result = rows_result([], "json")
```

Перед `test_sections_table_has_header_lines_and_one_table_per_non_empty_section`:

```python
def test_statement_table_shows_status_and_affected_rows() -> None:
    result = statement_result("UPDATE 3", 3, "table")
    assert _text(result) == "UPDATE 3: 3 rows affected."
    assert result.structured_content is None


def test_statement_table_without_count_says_done() -> None:
    assert _text(statement_result("CREATE TABLE", None, "table")) == "CREATE TABLE: done."


def test_statement_table_with_zero_rows_is_not_hidden() -> None:
    assert _text(statement_result("DELETE 0", 0, "table")) == "DELETE 0: 0 rows affected."


def test_statement_json_keeps_row_keys_and_adds_status() -> None:
    result = statement_result("INSERT 0 5", 5, "json")
    expected = {"rows": [], "row_count": 0, "status": "INSERT 0 5", "affected_rows": 5}
    assert result.structured_content == expected
    assert json.loads(_text(result)) == expected


def test_statement_json_ddl_has_null_affected_rows() -> None:
    result = statement_result("CREATE TABLE", None, "json")
    assert result.structured_content == {"rows": [], "row_count": 0, "status": "CREATE TABLE", "affected_rows": None}
```

`tests/unit/domains/test_querying.py` целиком:

```python
# mypy: ignore-errors
"""Unit tests for domains.querying.execute_sql."""

from unittest.mock import MagicMock

from postgres_fastmcp.domains.querying import execute_sql
from postgres_fastmcp.postgres.models import RowResult, StatementResult


class TestExecuteSql:
    """Tests for querying.execute_sql."""

    async def test_execute_sql_success_returns_decoded_rows(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """Successful execution returns list of decoded row dicts; read-only server uses readonly=True."""
        mock_db_access.write_mode = False
        mock_executor.execute_statement.return_value = StatementResult(
            rows=[RowResult(cells={"id": 1, "name": "a"}), RowResult(cells={"id": 2, "name": "b"})],
            status="SELECT 2",
            affected_rows=2,
        )
        result = await execute_sql(mock_db_access, "SELECT 1")
        assert result == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
        mock_executor.execute_statement.assert_awaited_once_with("SELECT 1", params=None, readonly=True)
        mock_executor.execute.assert_not_called()

    async def test_execute_sql_write_mode_uses_read_write_transaction(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """When write_mode is enabled the driver is called with readonly=False so writes persist."""
        mock_db_access.write_mode = True
        mock_executor.execute_statement.return_value = StatementResult(rows=None, status="INSERT 0 1", affected_rows=1)
        await execute_sql(mock_db_access, "INSERT INTO t (id) VALUES (1)")
        mock_executor.execute_statement.assert_awaited_once_with(
            "INSERT INTO t (id) VALUES (1)", params=None, readonly=False
        )

    async def test_execute_sql_empty_result_set_returns_empty_list(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """SELECT without rows is still a result set: an empty list, not a command status."""
        mock_executor.execute_statement.return_value = StatementResult(rows=[], status="SELECT 0", affected_rows=0)
        result = await execute_sql(mock_db_access, "SELECT 0 WHERE false")
        assert result == []

    async def test_execute_sql_without_result_set_returns_command_status(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """DML without RETURNING and DDL return the StatementResult with the Postgres command tag."""
        mock_db_access.write_mode = True
        status = StatementResult(rows=None, status="UPDATE 500", affected_rows=500)
        mock_executor.execute_statement.return_value = status
        result = await execute_sql(mock_db_access, "UPDATE t SET v = 1")
        assert result is status

    async def test_execute_sql_bytes_decoded(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """Row cells containing bytes are decoded to UTF-8 strings in result."""
        mock_executor.execute_statement.return_value = StatementResult(
            rows=[RowResult(cells={"name": b"hello", "num": 42})], status="SELECT 1", affected_rows=1
        )
        result = await execute_sql(mock_db_access, "SELECT 'hello'")
        assert result == [{"name": "hello", "num": 42}]
```

`tests/unit/tools/test_definitions.py`: удалить импорт `from postgres_fastmcp.domains.querying import SUCCESS_NO_ROWS`, добавить перед импортом `ObjectNotFoundError`:

```python
from postgres_fastmcp.postgres.models import StatementResult
```

Тест `test_execute_sql_statement_without_rows` заменить двумя:

```python
@pytest.mark.asyncio
async def test_execute_sql_statement_without_rows_reports_command_status(monkeypatch, db_mock, toolset) -> None:
    """DML без RETURNING: тег команды и число затронутых строк, а не «0 rows»."""
    fake_querying = mock.AsyncMock()
    fake_querying.execute_sql.return_value = StatementResult(rows=None, status="UPDATE 500", affected_rows=500)
    monkeypatch.setattr(defs, "querying", fake_querying)

    table = await toolset.execute_sql(sql="UPDATE t SET v = 1")
    as_json = await toolset.execute_sql(sql="UPDATE t SET v = 1", output="json")

    assert _text(table) == "UPDATE 500: 500 rows affected."
    assert as_json.structured_content == {"rows": [], "row_count": 0, "status": "UPDATE 500", "affected_rows": 500}


@pytest.mark.asyncio
async def test_execute_sql_ddl_reports_done(monkeypatch, db_mock, toolset) -> None:
    """DDL: в теге нет числа, affected_rows — null."""
    fake_querying = mock.AsyncMock()
    fake_querying.execute_sql.return_value = StatementResult(rows=None, status="CREATE TABLE", affected_rows=None)
    monkeypatch.setattr(defs, "querying", fake_querying)

    table = await toolset.execute_sql(sql="CREATE TABLE t ()")
    as_json = await toolset.execute_sql(sql="CREATE TABLE t ()", output="json")

    assert _text(table) == "CREATE TABLE: done."
    assert as_json.structured_content == {"rows": [], "row_count": 0, "status": "CREATE TABLE", "affected_rows": None}
```

Фейки драйвера: `execute_sql` пойдёт через `execute_statement`, а `list_schemas` по-прежнему через `execute`, поэтому `execute_statement` добавляется рядом с `execute`. Во всех четырёх файлах импорт:

```python
from postgres_fastmcp.postgres.models import RowResult, StatementResult
```

`tests/unit/test_provider.py` и `tests/unit/app/test_auth_http.py`, в `FakeService.__init__` после строки `self.sql_driver.execute = AsyncMock(...)`:

```python
        self.sql_driver.execute_statement = AsyncMock(
            return_value=StatementResult(rows=[RowResult(cells={"n": 1})], status="SELECT 1", affected_rows=1)
        )
```

`tests/unit/tools/test_registry.py`, в `test_execute_sql_output_over_mcp` после `db.sql_driver.execute = AsyncMock(...)`:

```python
    db.sql_driver.execute_statement = AsyncMock(
        return_value=StatementResult(rows=[RowResult(cells={"n": 1})], status="SELECT 1", affected_rows=1)
    )
```

`tests/unit/app/test_response_budget.py`, в `FakeDb.__init__` после `self.sql_driver.execute = AsyncMock(return_value=rows)`:

```python
            self.sql_driver.execute_statement = AsyncMock(
                return_value=StatementResult(rows=rows, status=f"SELECT {len(rows)}", affected_rows=len(rows))
            )
```

`tests/integration/test_write_mode.py`: `test_execute_sql_write_persists` заменить, добавить тест через `SafeSqlExecutor` (BASIC + запись; DDL в BASIC запрещён, поэтому таблицу готовит FULL-сервер из `integration_settings`):

```python
@pytest.mark.asyncio
async def test_execute_sql_write_persists(integration_settings: Settings) -> None:
    """Full + write_mode: DDL/DML apply and report the Postgres command status, not "0 rows"."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        await client.call_tool("execute_sql", {"sql": "DROP TABLE IF EXISTS wm_write_test"})

        create = await client.call_tool(
            "execute_sql",
            {"sql": "CREATE TABLE wm_write_test (id int PRIMARY KEY, v text)"},
        )
        assert create.is_error is False
        assert create.content[0].text == "CREATE TABLE: done."

        insert = await client.call_tool(
            "execute_sql",
            {"sql": "INSERT INTO wm_write_test (id, v) VALUES (1, 'alpha'), (2, 'beta')"},
        )
        assert insert.is_error is False
        assert insert.content[0].text == "INSERT 0 2: 2 rows affected."

        update = await client.call_tool(
            "execute_sql",
            {"sql": "UPDATE wm_write_test SET v = upper(v)", "output": "json"},
        )
        assert update.structured_content == {"rows": [], "row_count": 0, "status": "UPDATE 2", "affected_rows": 2}

        select = await client.call_tool(
            "execute_sql",
            {"sql": "SELECT v FROM wm_write_test WHERE id = 1", "output": "json"},
        )
        assert select.is_error is False
        assert select.structured_content == {"rows": [{"v": "ALPHA"}], "row_count": 1}

        await client.call_tool("execute_sql", {"sql": "DROP TABLE IF EXISTS wm_write_test"})


@pytest.mark.asyncio
async def test_execute_sql_status_through_safe_executor(
    integration_settings: Settings,
    test_postgres_connection_string: tuple[str, str],
) -> None:
    """Basic + write_mode: the SET LOCAL prefix of SafeSqlExecutor does not replace the statement's status."""
    connection_string, _ = test_postgres_connection_string
    basic = DatabaseConfig.from_uri(connection_string, access_mode=AccessMode.BASIC, write_mode=True)
    async with Client(create_server(integration_settings)) as admin:
        await admin.call_tool("execute_sql", {"sql": "DROP TABLE IF EXISTS wm_safe_status"})
        await admin.call_tool("execute_sql", {"sql": "CREATE TABLE wm_safe_status (id int)"})
        async with Client(create_server(Settings(database=basic))) as client:
            insert = await client.call_tool("execute_sql", {"sql": "INSERT INTO wm_safe_status VALUES (1), (2), (3)"})
            delete = await client.call_tool(
                "execute_sql", {"sql": "DELETE FROM wm_safe_status WHERE id > 1", "output": "json"}
            )
        await admin.call_tool("execute_sql", {"sql": "DROP TABLE IF EXISTS wm_safe_status"})
    assert insert.content[0].text == "INSERT 0 3: 3 rows affected."
    assert delete.structured_content == {"rows": [], "row_count": 0, "status": "DELETE 2", "affected_rows": 2}
```

`tests/integration/test_tools_integration.py::test_tools_execute_sql_empty_result` (`SELECT 1 WHERE FALSE` → `0 rows.`) не меняется: это результирующий набор.

- [ ] **Step 2: Тесты падают**

Run: `uv run pytest tests/unit/tools/test_rendering.py tests/unit/domains/test_querying.py tests/unit/tools/test_definitions.py -q`
Expected: ошибка сбора `test_rendering.py` (`ImportError: cannot import name 'statement_result'`); в `test_definitions.py` падают `test_execute_sql_statement_without_rows_reports_command_status` и `test_execute_sql_ddl_reports_done` (тул передаёт `StatementResult` в `rows_result`); в `test_querying.py` падают тесты, которые ждут вызова `execute_statement` (домен ещё зовёт `execute`).

- [ ] **Step 3: Рендер**

`src/postgres_fastmcp/tools/rendering.py`: `rows_result` (`:18-33`) заменить на `rows_result` и `statement_result`:

```python
def rows_result(rows: list[dict[str, Any]], output: OutputFormat) -> ToolResult:
    """Результат тула из списка строк.

    Args:
        rows: Строки результата (одинаковые или разные наборы ключей).
        output: 'table' — Markdown-таблица, 'json' — {"rows": [...], "row_count": N}.

    Returns:
        ToolResult с Markdown-текстом или с JSON-текстом и structured_content.
    """
    if output == "json":
        return _json_result({"rows": rows, "row_count": len(rows)})
    return _text_result(f"{_table(rows)}\n\n{len(rows)} rows." if rows else "0 rows.")


def statement_result(status: str | None, affected_rows: int | None, output: OutputFormat) -> ToolResult:
    """Результат оператора без результирующего набора (DML без RETURNING, DDL): тег команды Postgres.

    Args:
        status: Тег команды, например "UPDATE 3" или "CREATE TABLE".
        affected_rows: Число строк из тега; None, если в теге нет числа.
        output: 'table' — одна строка "UPDATE 3: 3 rows affected." или "CREATE TABLE: done.";
            'json' — {"rows": [], "row_count": 0, "status": ..., "affected_rows": ...}.

    Returns:
        ToolResult с текстом или с JSON-текстом и structured_content.
    """
    if output == "json":
        return _json_result({"rows": [], "row_count": 0, "status": status, "affected_rows": affected_rows})
    outcome = "done." if affected_rows is None else f"{affected_rows} rows affected."
    return _text_result(f"{status or 'Statement executed'}: {outcome}")
```

- [ ] **Step 4: Домен и тул**

`src/postgres_fastmcp/domains/querying.py` целиком:

```python
"""Домен выполнения произвольного SQL (тул execute_sql)."""

from typing import Any

from postgres_fastmcp.domains.db_access import DbAccessPort
from postgres_fastmcp.postgres.models import StatementResult
from postgres_fastmcp.shared.utils import decode_bytes_to_utf8


async def execute_sql(db: DbAccessPort, sql: str) -> list[dict[str, Any]] | StatementResult:
    """Выполнить SQL запрос к базе данных.

    Режим транзакции (только чтение / чтение-запись) определяется правами
    текущего запроса (``db.write_mode``), а не вызывающим кодом: при записи
    транзакция открывается на запись, поэтому DML/DDL реально применяются.
    Без права записи запись блокируется на уровне валидатора и транзакции.

    Оператор без результирующего набора (INSERT/UPDATE/DELETE без RETURNING, DDL)
    возвращает StatementResult с тегом команды Postgres ("UPDATE 3", "CREATE TABLE"):
    агент видит, сколько строк затронуто, а не пустой список.

    Args:
        db: Доступ к БД для текущего запроса (DbAccessPort).
        sql: SQL запрос для выполнения.

    Returns:
        Строки результата (list[dict]) либо StatementResult (rows=None) для оператора без результирующего набора.
    """
    result = await db.sql_driver.execute_statement(sql, params=None, readonly=not db.write_mode)
    if result.rows is None:
        return result
    return [decode_bytes_to_utf8(r.cells) for r in result.rows]
```

`src/postgres_fastmcp/tools/definitions.py`: строку `:23` (`from postgres_fastmcp.domains.querying import SUCCESS_NO_ROWS`) заменить на

```python
from postgres_fastmcp.postgres.models import StatementResult
```

импорт рендера (`:33`):

```python
from postgres_fastmcp.tools.rendering import rows_result, sections_result, statement_result
```

тело `execute_sql` (`:60-63`):

```python
        result = await querying.execute_sql(self._get_db(), sql)
        if isinstance(result, StatementResult):
            return statement_result(result.status, result.affected_rows, output)
        return rows_result(result, output)
```

- [ ] **Step 5: Документация**

`README.md`, раздел «Формат ответа и бюджет»: сразу после пункта, который начинается с `` - `execute_sql`, `list_schemas`, `list_objects`, `get_object_details` и `get_top_queries` принимают `output` ``, новый пункт:

```markdown
- Оператор без результирующего набора (`INSERT`/`UPDATE`/`DELETE`/`MERGE` без `RETURNING`, DDL) `execute_sql` описывает тегом команды PostgreSQL: в `table` — одна строка `UPDATE 3: 3 rows affected.` (для DDL — `CREATE TABLE: done.`), в `json` — `{"rows": [], "row_count": 0, "status": "UPDATE 3", "affected_rows": 3}`; у DDL `affected_rows` равно `null`.
```

`src/postgres_fastmcp/tools/AGENTS.md`, раздел «Tools that return rows»: после пункта `- Never return Markdown and a JSON copy of the same data in one response.`:

```markdown
- A statement without a result set is not an empty table. `execute_sql` gets the Postgres command tag from
  `SqlDriverPort.execute_statement` and returns `statement_result()`: `UPDATE 3: 3 rows affected.` or
  `CREATE TABLE: done.`; in JSON `status` and `affected_rows` (null for DDL) sit next to the empty `rows`.
  Other domains keep calling `execute`.
```

- [ ] **Step 6: Тесты и проверки**

Run: `uv run pytest tests/unit -q`
Expected: `1003 passed`.

Run: `grep -rn "SUCCESS_NO_ROWS\|title=SUCCESS\|Statement executed successfully" src tests README.md`
Expected: пусто.

Run: `uv run pytest tests/integration -q && uv run ruff check tests/integration/test_write_mode.py --select F,ARG --no-fix`
Expected: `98 skipped` без ошибок сбора; `All checks passed!`.

- [ ] **Step 7: Гейты и коммит**

Run: `uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .`
Expected: `All checks passed!`, `Success: no issues found in 78 source files`, `143 files left unchanged`, `All checks passed!`.

```bash
git add src/postgres_fastmcp/tools/rendering.py src/postgres_fastmcp/domains/querying.py \
  src/postgres_fastmcp/tools/definitions.py README.md src/postgres_fastmcp/tools/AGENTS.md \
  tests/unit/tools/test_rendering.py tests/unit/domains/test_querying.py tests/unit/tools/test_definitions.py \
  tests/unit/test_provider.py tests/unit/app/test_auth_http.py tests/unit/tools/test_registry.py \
  tests/unit/app/test_response_budget.py tests/integration/test_write_mode.py
git commit -m "feat(tools): report command status and affected rows in execute_sql"
```

---

### Task 3: `get_object_details` — существование решает каталог

**Files:**
- Modify: `src/postgres_fastmcp/postgres/catalog.py:23` (перед `QUERY_LIST_SEQUENCES`)
- Modify: `src/postgres_fastmcp/domains/catalog/tables.py:7-14`, `:42-45`, `:66-86`
- Modify: `src/postgres_fastmcp/domains/catalog/sequences.py:50-75`, `src/postgres_fastmcp/domains/catalog/extensions.py:42-60`
- Modify: `src/postgres_fastmcp/domains/catalog/service.py:6`, `:90-123`
- Modify: `src/postgres_fastmcp/tools/definitions.py:24`, `:36-37`, `:113-116`
- Test: `tests/unit/domains/test_catalog_service.py`, `tests/unit/tools/test_definitions.py`, `tests/integration/test_tools_integration.py`

**Interfaces:**
- Consumes: `ObjectNotFoundError(schema_name, object_name, object_type)` (без изменений), `SafeSqlExecutor`, `QueryValidator`, `DbAccess`.
- Produces:
  - `postgres_fastmcp.postgres.catalog.QUERY_TABLE_EXISTS` — плейсхолдеры `{}`: схема, имя, `table_type`.
  - `TablesService.get_details(...) -> dict[str, Any] | None`, `SequencesService.get_details(...) -> dict[str, Any] | None`, `ExtensionsService.get_details(...) -> dict[str, Any] | None`: `None` — объекта нет.
  - `CatalogService.get_object_details(...)` бросает `ObjectNotFoundError` и больше не возвращает пустых словарей.
  - `tools.definitions._TOOL_HEADER_KEYS` удалён.

- [ ] **Step 1: Тесты**

`tests/unit/domains/test_catalog_service.py`, импорты и помощники:

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from postgres_fastmcp.domains.catalog.service import CatalogService
from postgres_fastmcp.domains.db_access import DbAccess
from postgres_fastmcp.postgres.catalog import QUERY_TABLE_EXISTS
from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.postgres.security.driver import SafeSqlConfig, SafeSqlExecutor
from postgres_fastmcp.postgres.security.query_validator import QueryValidator
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.shared.errors import (
    ObjectNotFoundError,
    SchemaAccessError,
    TablePrefixAccessError,
    UnsupportedObjectTypeError,
)


_PRESENT = [RowResult(cells={"present": 1})]


def _catalog_rows(*, exists: bool):
    """Подмена execute для деталей таблицы: запрос существования отвечает exists, остальные — пусто."""

    async def execute(query, params=None, *, readonly=True):
        if query is QUERY_TABLE_EXISTS:
            return _PRESENT if exists else []
        return []

    return execute
```

В `test_get_object_details_table_returns_basic_columns_constraints_indexes` у `side_effect` четвёртый элемент (запрос существования идёт последним в `gather`):

```python
        mock_executor.execute.side_effect = [
            [
                RowResult(cells={"column_name": "id", "data_type": "int", "is_nullable": "NO", "column_default": None}),
            ],
            [],
            [],
            _PRESENT,
        ]
```

`test_get_object_details_table_runs_three_queries_in_parallel` переименовать и поправить подмену:

```python
    async def test_get_object_details_table_runs_four_queries_in_parallel(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """columns/constraints/indexes and the existence query run concurrently via asyncio.gather."""
        import asyncio
        import time

        async def slow_execute(query, *args, **kwargs):
            await asyncio.sleep(0.15)
            return _PRESENT if query is QUERY_TABLE_EXISTS else []
```

(остаток теста без изменений). Перед `test_get_object_details_unsupported_type_raises`:

```python
    async def test_empty_table_exists(self, mock_db_access: MagicMock, mock_executor: MagicMock) -> None:
        """CREATE TABLE t(): каталог знает таблицу, разделы пустые — это не «не найдено»."""
        mock_executor.execute.side_effect = _catalog_rows(exists=True)
        service = CatalogService(db=mock_db_access)

        result = await service.get_object_details("public", "t_empty", "table")

        assert result == {
            "basic": {"schema": "public", "name": "t_empty", "type": "table"},
            "columns": [],
            "constraints": [],
            "indexes": [],
        }

    @pytest.mark.parametrize(("object_type", "table_type"), [("table", "BASE TABLE"), ("view", "VIEW")])
    async def test_missing_table_or_view_raises_not_found(
        self, mock_db_access: MagicMock, mock_executor: MagicMock, object_type: str, table_type: str
    ) -> None:
        """Нет строки в information_schema.tables с нужным table_type — ObjectNotFoundError."""
        mock_executor.execute.side_effect = _catalog_rows(exists=False)
        service = CatalogService(db=mock_db_access)

        with pytest.raises(ObjectNotFoundError) as exc_info:
            await service.get_object_details("public", "ghost", object_type)

        assert str(exc_info.value) == (
            f"Object not found: public.ghost ({object_type}). Use list_objects to see existing objects."
        )
        exists_call = next(c for c in mock_executor.execute.await_args_list if c.args[0] is QUERY_TABLE_EXISTS)
        assert exists_call.kwargs["params"] == ["public", "ghost", table_type]

    async def test_missing_sequence_raises_not_found(self, mock_db_access: MagicMock, mock_executor: MagicMock) -> None:
        mock_executor.execute.return_value = []
        service = CatalogService(db=mock_db_access)

        with pytest.raises(ObjectNotFoundError, match=r"Object not found: public\.ghost \(sequence\)"):
            await service.get_object_details("public", "ghost", "sequence")

    async def test_missing_extension_message_has_no_schema(
        self, mock_db_access: MagicMock, mock_executor: MagicMock
    ) -> None:
        """Расширения не принадлежат схеме: в сообщении только имя."""
        mock_executor.execute.return_value = []
        service = CatalogService(db=mock_db_access)

        with pytest.raises(ObjectNotFoundError) as exc_info:
            await service.get_object_details("public", "ghost", "extension")

        assert str(exc_info.value) == "Object not found: ghost (extension). Use list_objects to see existing objects."

    @pytest.mark.parametrize("object_name", ["app_orders", "other_users", "ghost"])
    async def test_basic_table_prefix_error_is_unchanged(self, object_name: str) -> None:
        """BASIC с table_prefix: как и до проверки существования, валидатор отклоняет pg_indexes для любой таблицы."""
        delegate = MagicMock()
        delegate.execute = AsyncMock(return_value=[])
        config = SafeSqlConfig(query_tag="t", allowed_schema="public", table_prefix="app_")
        validator = QueryValidator(allowed_schema="public", table_prefix="app_", read_only=True)
        db = DbAccess(
            sql_driver=SafeSqlExecutor(delegate=delegate, validator=validator, config=config),
            access_mode=AccessMode.BASIC,
            write_mode=False,
            table_prefix="app_",
            connection_id="fake",
        )

        with pytest.raises(TablePrefixAccessError, match="pg_indexes"):
            await CatalogService(db=db).get_object_details("public", object_name, "table")
```

`test_basic_table_prefix_error_is_unchanged` закрепляет поведение `769ad48` и проходит и до правки, и после неё.

`tests/unit/tools/test_definitions.py`: тесты `test_get_object_details_missing_object_raises` (с двумя `parametrize`) и `test_get_object_details_missing_extension_has_no_schema` удалить — тул больше не выводит отсутствие объекта. На их место:

```python
@pytest.mark.asyncio
async def test_get_object_details_empty_table_is_found(monkeypatch, db_mock, toolset) -> None:
    """Таблица без столбцов (CREATE TABLE t()): заголовок и пустые разделы, а не «не найдено»."""
    fake_service = mock.AsyncMock()
    fake_service.get_object_details.return_value = {
        "basic": {"schema": "public", "name": "t_empty", "type": "table"},
        "columns": [],
        "constraints": [],
        "indexes": [],
    }
    monkeypatch.setattr(defs, "CatalogService", lambda **kw: fake_service)

    table = await toolset.get_object_details(schema_name="public", object_name="t_empty")
    as_json = await toolset.get_object_details(schema_name="public", object_name="t_empty", output="json")

    assert _text(table) == "schema: public\nname: t_empty\ntype: table"
    assert as_json.structured_content == {
        "schema": "public",
        "name": "t_empty",
        "type": "table",
        "columns": [],
        "constraints": [],
        "indexes": [],
    }


@pytest.mark.asyncio
async def test_get_object_details_not_found_comes_from_the_catalog(monkeypatch, db_mock, toolset) -> None:
    """Тул не выводит «не найдено» сам: ObjectNotFoundError бросает каталог, тул его пропускает."""
    fake_service = mock.AsyncMock()
    fake_service.get_object_details.side_effect = ObjectNotFoundError("public", "ghost", "table")
    monkeypatch.setattr(defs, "CatalogService", lambda **kw: fake_service)

    with pytest.raises(ObjectNotFoundError, match=r"Object not found: public\.ghost \(table\)\. Use list_objects"):
        await toolset.get_object_details(schema_name="public", object_name="ghost")
```

`tests/integration/test_tools_integration.py`, перед `test_tools_explain_query`:

```python
@pytest.mark.asyncio
async def test_tools_get_object_details_empty_table(integration_settings: Settings) -> None:
    """A table without columns exists: header and empty sections, not "Object not found"."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        await client.call_tool("execute_sql", {"sql": "DROP TABLE IF EXISTS od_empty"})
        await client.call_tool("execute_sql", {"sql": "CREATE TABLE od_empty ()"})
        table = await client.call_tool("get_object_details", {"schema_name": "public", "object_name": "od_empty"})
        as_json = await client.call_tool(
            "get_object_details", {"schema_name": "public", "object_name": "od_empty", "output": "json"}
        )
        as_view = await client.call_tool(
            "get_object_details",
            {"schema_name": "public", "object_name": "od_empty", "object_type": "view"},
            raise_on_error=False,
        )
        await client.call_tool("execute_sql", {"sql": "DROP TABLE IF EXISTS od_empty"})
    assert table.content[0].text == "schema: public\nname: od_empty\ntype: table"
    assert as_json.structured_content == {
        "schema": "public",
        "name": "od_empty",
        "type": "table",
        "columns": [],
        "constraints": [],
        "indexes": [],
    }
    assert as_view.is_error is True
    assert as_view.content[0].text == (
        "Object not found: public.od_empty (view). Use list_objects to see existing objects."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("object_type", "message"),
    [
        ("table", "Object not found: public.od_ghost (table)."),
        ("sequence", "Object not found: public.od_ghost (sequence)."),
        ("extension", "Object not found: od_ghost (extension)."),
    ],
)
async def test_tools_get_object_details_missing(integration_settings: Settings, object_type: str, message: str) -> None:
    """A missing object is an error with a hint; an extension is named without a schema."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_object_details",
            {"schema_name": "public", "object_name": "od_ghost", "object_type": object_type},
            raise_on_error=False,
        )
    assert result.is_error is True
    assert result.content[0].text == f"{message} Use list_objects to see existing objects."
```

`test_tools_get_object_details` (`information_schema.tables` как `view`) не меняется: `information_schema.tables` отдаёт для него `table_type = 'VIEW'`.

- [ ] **Step 2: Тесты падают**

Run: `uv run pytest tests/unit/domains/test_catalog_service.py tests/unit/tools/test_definitions.py -q`
Expected: ошибка сбора `test_catalog_service.py` — `ImportError: cannot import name 'QUERY_TABLE_EXISTS'`; в `test_definitions.py` падает `test_get_object_details_empty_table_is_found` (`ObjectNotFoundError: Object not found: public.t_empty (table)`).

- [ ] **Step 3: Запрос существования**

`src/postgres_fastmcp/postgres/catalog.py`, перед `QUERY_LIST_SEQUENCES`:

```python
# Существование таблицы/представления: тот же источник и тот же table_type, что у QUERY_LIST_TABLES_VIEWS
# ('BASE TABLE' = relkind r/p, 'VIEW' = v). information_schema проходит валидатор BASIC и с table_prefix,
# а pg_class — нет (TablePrefixAccessError без схемы, SchemaNotAllowedError с pg_catalog).
QUERY_TABLE_EXISTS = """
SELECT 1 AS present
FROM information_schema.tables
WHERE table_schema = {} AND table_name = {} AND table_type = {}
"""
```

- [ ] **Step 4: Профильные сервисы возвращают `None`**

`src/postgres_fastmcp/domains/catalog/tables.py`: в импорт из `postgres_fastmcp.postgres.catalog` добавить `QUERY_TABLE_EXISTS` (после `QUERY_LIST_TABLES_VIEWS`); после импортов:

```python
# object_type тула -> information_schema.tables.table_type
_TABLE_TYPES = {"table": "BASE TABLE", "view": "VIEW"}
```

В `list_tables_views` удалить строку `table_type = "BASE TABLE" if object_type == "table" else "VIEW"`, параметры запроса:

```python
            params=[schema_name, _TABLE_TYPES[object_type]],
```

В `get_details` сигнатура, docstring и начало тела до `columns = (`:

```python
    ) -> dict[str, Any] | None:
        """Получить столбцы, ограничения и индексы таблицы или представления.

        Существование решает каталог (QUERY_TABLE_EXISTS), а не пустые разделы: таблица
        без столбцов (CREATE TABLE t()) существует. Запрос существования идёт в том же
        gather, поэтому ошибка валидатора у остальных запросов (BASIC с table_prefix)
        остаётся той же, что до проверки существования.

        Args:
            schema_name: Имя схемы объекта.
            object_name: Имя таблицы или представления.
            object_type: Тип объекта — "table" или "view" (по умолчанию "table").

        Returns:
            Словарь с ключами basic, columns, constraints, indexes; None, если объекта такого типа нет.
        """
        sql_driver = self.db.sql_driver

        col_rows, con_rows, idx_rows, found = await asyncio.gather(
            sql_driver.execute(QUERY_GET_COLUMNS, params=[schema_name, object_name], readonly=True),
            sql_driver.execute(QUERY_GET_CONSTRAINTS, params=[schema_name, object_name], readonly=True),
            sql_driver.execute(QUERY_GET_INDEXES, params=[schema_name, object_name], readonly=True),
            sql_driver.execute(
                QUERY_TABLE_EXISTS, params=[schema_name, object_name, _TABLE_TYPES[object_type]], readonly=True
            ),
        )
        if not found:
            return None
```

`src/postgres_fastmcp/domains/catalog/sequences.py::get_details`: сигнатура `-> dict[str, Any] | None`, в docstring `Returns:` — `Словарь с полями schema, name, data_type, start_value, increment; None, если последовательности нет.`, последняя строка `return None` вместо `return {}`.

`src/postgres_fastmcp/domains/catalog/extensions.py::get_details`: сигнатура `-> dict[str, Any] | None`, в docstring `Returns:` — `Словарь с полями name, version, relocatable; None, если расширение не установлено.`, последняя строка `return None` вместо `return {}`.

- [ ] **Step 5: Фасад бросает, тул не выводит**

`src/postgres_fastmcp/domains/catalog/service.py:6`:

```python
from postgres_fastmcp.shared.errors import ObjectNotFoundError, SchemaAccessError, UnsupportedObjectTypeError
```

В `get_object_details`: в `Raises:` строка

```python
            ObjectNotFoundError: Если объекта такого типа нет в каталоге.
```

тело после docstring:

```python
        schema_name = self._resolve_schema(schema_name)

        result: dict[str, Any] | None
        if object_type in ("table", "view"):
            result = await self._tables.get_details(schema_name, object_name, object_type)
        elif object_type == "sequence":
            result = await self._sequences.get_details(schema_name, object_name)
        elif object_type == "extension":
            result = await self._extensions.get_details(object_name)
        else:
            raise UnsupportedObjectTypeError(object_type)

        if result is None:
            raise ObjectNotFoundError(schema_name, object_name, object_type)
        return cast("dict[str, Any]", decode_bytes_to_utf8(result))
```

`src/postgres_fastmcp/tools/definitions.py`: удалить импорт `from postgres_fastmcp.shared.errors import ObjectNotFoundError`, блок

```python
# Поля заголовка, которые get_object_details добавляет сам, без данных каталога.
_TOOL_HEADER_KEYS = frozenset({"schema", "name", "type"})
```

и в `get_object_details` четыре строки перед `return sections_result(...)`:

```python
        # Каталог не бросает на отсутствующий объект: таблица приходит с пустыми разделами,
        # последовательность и расширение — пустым словарём. Кроме полей самого тула ничего нет — объекта нет.
        if header.keys() <= _TOOL_HEADER_KEYS and not any(sections.values()):
            raise ObjectNotFoundError(header["schema"], object_name, object_type)
```

- [ ] **Step 6: Тесты и проверки**

Run: `uv run pytest tests/unit/domains/test_catalog_service.py tests/unit/tools/test_definitions.py -q && uv run pytest tests/unit -q`
Expected: `44 passed`; `1007 passed`.

Run: `grep -rn "_TOOL_HEADER_KEYS\|return {}" src/postgres_fastmcp/tools src/postgres_fastmcp/domains/catalog`
Expected: пусто.

Run: `uv run pytest tests/integration -q && uv run ruff check tests/integration/test_tools_integration.py --select F,ARG --no-fix`
Expected: `106 skipped` без ошибок сбора; `All checks passed!`.

- [ ] **Step 7: Гейты и коммит**

Run: `uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .`
Expected: `All checks passed!`, `Success: no issues found in 78 source files`, `143 files left unchanged`, `All checks passed!`.

```bash
git add src/postgres_fastmcp/postgres/catalog.py src/postgres_fastmcp/domains/catalog/tables.py \
  src/postgres_fastmcp/domains/catalog/sequences.py src/postgres_fastmcp/domains/catalog/extensions.py \
  src/postgres_fastmcp/domains/catalog/service.py src/postgres_fastmcp/tools/definitions.py \
  tests/unit/domains/test_catalog_service.py tests/unit/tools/test_definitions.py \
  tests/integration/test_tools_integration.py
git commit -m "fix(catalog): decide object existence in the catalog, not from empty sections"
```

---

### Task 4: Полная локальная проверка

**Files:** нет изменений.

**Interfaces:**
- Consumes: результат Task 1–3.
- Produces: подтверждение, что ветка готова к ревью.

- [ ] **Step 1: Полный локальный CI**

Run:
```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src/ && \
uv run pytest tests/unit -q && uv run pytest tests/integration -q
```
Expected: `All checks passed!`, `143 files already formatted`, `Success: no issues found in 78 source files`, юнит-тесты `1007 passed`, интеграция `106 skipped` без ошибок сбора (или PASS при наличии Docker, кроме тестов, которым нужны `hypopg`/`pg_stat_statements` в образе CI — они есть).

- [ ] **Step 2: Инварианты**

Run:
```bash
MCP_DATABASE_HOST=localhost MCP_DATABASE_PORT=5432 MCP_DATABASE_USER=u MCP_DATABASE_PASSWORD=p MCP_DATABASE_NAME=d FASTMCP_MCP_CAMELCASE_COMPAT=false uv run python -c "import asyncio, json, math; from postgres_fastmcp.app.config import Settings; from postgres_fastmcp.app.server import create_server; from postgres_fastmcp.shared.enums import AccessMode; s = Settings(); s.database = s.database.model_copy(update={'access_mode': AccessMode.FULL}); tools = asyncio.run(create_server(s).list_tools()); size = sum(len(json.dumps(t.to_mcp_tool().model_dump(by_alias=True, exclude_none=True))) for t in tools); print(size, math.ceil(size * 1.15))" 2>/dev/null
```
Expected: `9710 11167` — `tools/list` не изменился.

Run: `grep -rn "from __future__ import annotations" src tests`
Expected: пусто.

Run: `grep -rn "execute_statement" src/postgres_fastmcp/domains src/postgres_fastmcp/tools`
Expected: одна строка — `src/postgres_fastmcp/domains/querying.py` (только `execute_sql` зовёт новый метод).

Run: `grep -rn "SUCCESS_NO_ROWS\|_TOOL_HEADER_KEYS\|no rows were returned" src tests README.md`
Expected: пусто.

- [ ] **Step 3: Итог**

Run: `git log --oneline 769ad48..HEAD`
Expected: три коммита плана: `feat(postgres)`, `feat(tools)`, `fix(catalog)`.

---

## Self-Review

**1. Покрытие спеки:**

| Пункт спеки | Задача |
| --- | --- |
| §2–3: тег из `statusmessage`, число из `rowcount`, `StatementResult` | Task 1 |
| §3: `execute_statement` у `SqlExecutor`, `SafeSqlExecutor` (общий код), `SqlDriverPort` | Task 1 |
| §3: тег после префикса `SET LOCAL` и до `COMMIT` | Task 1 (фейковый курсор), Task 2 (интеграция через `SafeSqlExecutor`) |
| §3: вывод `table`/`json`, `SELECT`/`RETURNING` без изменений, удаление `title` и `SUCCESS_NO_ROWS` | Task 2 |
| §3: только `querying.execute_sql` зовёт новый метод | Task 2; проверка `grep` в Task 4 |
| §3: описания и `tools/list` не меняются | Task 4, Step 2 |
| §4: существование решает домен, `ObjectNotFoundError` в фасаде, тул только рисует | Task 3 |
| §4: пустая таблица — заголовок и пустые разделы; таблица как `view` — «не найдено» | Task 3 (юнит и интеграция) |
| §4: последовательности и расширения — прежний запрос, сообщение расширения без схемы | Task 3 |
| §4.1: BASIC+`table_prefix` — прежняя ошибка | Task 3 (`test_basic_table_prefix_error_is_unchanged`) |
| §5: `information_schema.tables` вместо `pg_class` | Task 3, `QUERY_TABLE_EXISTS` |
| §6: интеграционные тесты `test_write_mode.py`, `test_tools_integration.py` | Task 2, Task 3 |
| README и `tools/AGENTS.md` | Task 2 |
| §7: вне объёма | Не входит |

**2. Плейсхолдеры:** нет. Код каждого шага дан целиком или точной правкой. Все числа получены прогоном плана в worktree против `.venv` проекта: 990/998/1003/1007 unit; 96/98/106 skipped; 47 и 44 теста в файлах шагов; 78 файлов mypy; 143 файла `ruff format`; 9710 символов `tools/list`. Интеграционные тесты Task 2–3 прошли на локальном Postgres 16.4.

**3. Согласованность типов:** `StatementResult(rows, status, affected_rows)` (Task 1) возвращают `SqlExecutor.execute_statement`, `SafeSqlExecutor.execute_statement` и `SqlDriverPort.execute_statement`. Его же возвращает `querying.execute_sql` (Task 2), а тул передаёт поля в `statement_result(status, affected_rows, output)`. Фейки в тестах Task 2 подменяют `sql_driver.execute_statement` под этим именем. `QUERY_TABLE_EXISTS` (Task 3) импортируют `tables.py` и `test_catalog_service.py`, а тест узнаёт запрос по идентичности (`query is QUERY_TABLE_EXISTS`). `get_details(...) -> dict[str, Any] | None` у трёх сервисов согласован с проверкой `result is None` в `CatalogService`.
