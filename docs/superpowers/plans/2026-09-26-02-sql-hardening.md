# SQL Validator Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Закрыть DDL-обход через `CREATE EXTENSION`, убрать ложные срабатывания валидатора, перенести таймаут запроса на сторону Postgres и зафиксировать всё тестовым корпусом.

**Architecture:** Шаг 2 из пяти по спеке `docs/superpowers/specs/2026-09-26-auth-fastmcp4-hardening-design.md` (раздел 4). Меняются только `postgres/security/*` и тесты. Двухслойная схема (валидатор AST + `READ ONLY` транзакция) сохраняется; `SafeSqlExecutor` дополнительно выставляет `statement_timeout` в той же транзакции, где уже ставит `search_path`.

**Tech Stack:** Python 3.12+, uv, pglast 8, psycopg 3, FastMCP 4, pytest (asyncio_mode=auto), Docker-Postgres 15/16 с hypopg для интеграции.

## Global Constraints

- Зависит от плана `2026-09-26-01-fastmcp4-upgrade.md`: ветка от `main` после его вливания.
- Все команды Python только через `uv run ...`; перед коммитом `uv run ruff check --fix --show-fixes`, `uv run mypy src/`, `uv run ruff format` с нулём ошибок (AGENTS.md).
- Юнит-тесты требуют `<env>` = `MCP_DATABASE_HOST=localhost MCP_DATABASE_PORT=5432 MCP_DATABASE_USER=u MCP_DATABASE_PASSWORD=p MCP_DATABASE_NAME=d`.
- `CREATE EXTENSION` разрешён только для `hypopg` и `pg_stat_statements` и только при `read_only=False` (спека 4.1).
- Ошибки пользователю наследуют `UserFacingError`; сообщения на английском (AGENTS.md).
- Ветка работы: `claude/sql-hardening`.

---

### Task 1: `CREATE EXTENSION` только в write-режиме и только из короткого allowlist

**Files:**
- Modify: `src/postgres_fastmcp/postgres/security/statement_policies.py:73-83, 142-146`
- Modify: `src/postgres_fastmcp/postgres/security/policies.py:7-120`
- Modify: `src/postgres_fastmcp/postgres/security/query_validator.py:22-24, 180-187`
- Modify: `src/postgres_fastmcp/shared/errors.py:130-141` (текст `StatementTypeNotAllowedError`)
- Test: `tests/unit/postgres/test_query_validator.py` (класс `TestQueryValidatorCreateExtension`)

**Interfaces:**
- Consumes: `QueryValidator(allowed_schema, table_prefix, read_only, allow_explain_analyze).validate(query)`.
- Produces: `WRITE_STMT_TYPES: set[type]` в `statement_policies.py` (заменяет `DML_STMT_TYPES`); `ALLOWED_EXTENSIONS == frozenset({"hypopg", "pg_stat_statements"})`. Task 5 (корпус) полагается на это поведение.

- [ ] **Step 1: Переписать тесты CREATE EXTENSION**

В `tests/unit/postgres/test_query_validator.py` заменить класс `TestQueryValidatorCreateExtension` на:

```python
class TestQueryValidatorCreateExtension:
    """CREATE EXTENSION: only in write mode, only hypopg / pg_stat_statements."""

    @pytest.mark.parametrize("extname", ["hypopg", "pg_stat_statements", "dblink"])
    def test_read_only_rejects_any_create_extension(self, extname: str) -> None:
        """In read-only mode CREATE EXTENSION is rejected by statement type before touching the DB."""
        v = QueryValidator(read_only=True)
        with pytest.raises(StatementTypeNotAllowedError):
            v.validate(f"CREATE EXTENSION {extname}")

    @pytest.mark.parametrize("extname", ["hypopg", "pg_stat_statements"])
    def test_write_mode_allows_whitelisted_extension(self, extname: str) -> None:
        """Write mode allows the two extensions the server itself relies on."""
        v = QueryValidator(read_only=False)
        v.validate(f"CREATE EXTENSION IF NOT EXISTS {extname}")

    @pytest.mark.parametrize("extname", ["dblink", "file_fdw", "plpython3u", "unknown_ext"])
    def test_write_mode_rejects_other_extensions(self, extname: str) -> None:
        """Any other extension is rejected with CreateExtensionNotSupportedError."""
        v = QueryValidator(read_only=False)
        with pytest.raises(CreateExtensionNotSupportedError) as exc_info:
            v.validate(f"CREATE EXTENSION {extname}")
        assert extname in str(exc_info.value)
```

- [ ] **Step 2: Запустить и убедиться, что падают**

Run: `<env> uv run pytest tests/unit/postgres/test_query_validator.py -q -k CreateExtension`
Expected: `test_read_only_rejects_any_create_extension[hypopg]` и `[pg_stat_statements]` FAIL (`DID NOT RAISE`), `test_write_mode_rejects_other_extensions[dblink]`, `[file_fdw]`, `[plpython3u]` FAIL (`DID NOT RAISE`, они сейчас в allowlist). Остальные проходят.

- [ ] **Step 3: statement_policies.py — вынести CreateExtensionStmt в write-набор**

В `src/postgres_fastmcp/postgres/security/statement_policies.py`:

Заменить `ALLOWED_STMT_TYPES` на:

```python
ALLOWED_STMT_TYPES: set[type] = {
    SelectStmt,
    ExplainStmt,
    VariableShowStmt,
    PrepareStmt,
    DeallocateStmt,
    DeclareCursorStmt,
    ClosePortalStmt,
    FetchStmt,
}
```

Заменить блок `DML_STMT_TYPES` и комментарий в конце файла на:

```python
# Statements that are allowed only when read_only=False. CreateExtensionStmt is here on purpose:
# in a READ ONLY transaction it fails anyway, and in write mode the extension name is checked
# against ALLOWED_EXTENSIONS (see query_validator).
WRITE_STMT_TYPES: set[type] = {InsertStmt, UpdateStmt, DeleteStmt, CreateExtensionStmt}

# Note: VACUUM/ANALYZE (VacuumStmt) are intentionally NOT allowed in any mode — they write
# to disk and cannot run inside the executor's wrapped transaction block. They are rejected
# with StatementTypeNotAllowedError rather than failing later at execution time.
```

`CreateExtensionStmt` остаётся в списке импортов (он используется в `WRITE_STMT_TYPES`).

- [ ] **Step 4: policies.py — короткий allowlist**

Заменить содержимое `src/postgres_fastmcp/postgres/security/policies.py` целиком:

```python
"""Сводные политики валидации: типы операторов, функции, расширения."""

from postgres_fastmcp.postgres.security._allowed_functions import ALLOWED_FUNCTIONS
from postgres_fastmcp.postgres.security.statement_policies import ALLOWED_NODE_TYPES


# Единственные расширения, которые сервер сам просит установить (hypothetical indexes и топ запросов).
# Всё остальное (dblink, file_fdw, procedural languages, ...) даёт побочные эффекты за пределами БД.
ALLOWED_EXTENSIONS: frozenset[str] = frozenset({"hypopg", "pg_stat_statements"})

__all__ = ["ALLOWED_EXTENSIONS", "ALLOWED_FUNCTIONS", "ALLOWED_NODE_TYPES"]
```

- [ ] **Step 5: query_validator.py — использовать WRITE_STMT_TYPES**

В `src/postgres_fastmcp/postgres/security/query_validator.py`:

Импорт:

```python
from postgres_fastmcp.postgres.security.statement_policies import ALLOWED_STMT_TYPES, WRITE_STMT_TYPES
```

В `QueryValidator.validate` блок:

```python
        allowed_stmt_types = set(ALLOWED_STMT_TYPES)
        allowed_node_types = set(ALLOWED_NODE_TYPES)
        if not self.read_only:
            allowed_stmt_types |= WRITE_STMT_TYPES
            allowed_node_types |= WRITE_STMT_TYPES
```

Проверка DDL по имени (`"Create" in stmt_type_name ... and not isinstance(stmt_node, CreateExtensionStmt)`) остаётся: в write-режиме `CreateExtensionStmt` проходит её и дальше проверяется визитором по `ALLOWED_EXTENSIONS`.

- [ ] **Step 6: Исправить текст ошибки: он обещает ANALYZE и VACUUM, которые запрещены**

В `src/postgres_fastmcp/shared/errors.py` в `StatementTypeNotAllowedError.__init__` заменить обе ветки сообщения на:

```python
        if read_only:
            message = (
                "Only SELECT, EXPLAIN, SHOW and other read-only statements are allowed "
                f"in read-only mode. Received: {stmt_type_name}"
            )
        else:
            message = (
                "Only SELECT, INSERT, UPDATE, DELETE, EXPLAIN, SHOW and CREATE EXTENSION "
                "(hypopg, pg_stat_statements) are allowed. "
                "DDL operations (CREATE, DROP, ALTER), VACUUM and ANALYZE are not allowed. "
                f"Received: {stmt_type_name}"
            )
```

Существующие тесты проверяют подстроку `read-only` и имя оператора, они остаются зелёными.

- [ ] **Step 7: Запустить тесты валидатора и ошибок**

Run: `<env> uv run pytest tests/unit/postgres/test_query_validator.py tests/unit/shared/test_errors.py -q`
Expected: все PASS.

Run: `grep -rn "DML_STMT_TYPES" src tests`
Expected: пусто.

- [ ] **Step 8: Линтеры и коммит**

Run: `uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format`
Expected: ноль ошибок.

```bash
git add src/postgres_fastmcp/postgres/security/statement_policies.py src/postgres_fastmcp/postgres/security/policies.py src/postgres_fastmcp/postgres/security/query_validator.py src/postgres_fastmcp/shared/errors.py tests/unit/postgres/test_query_validator.py
git commit -m "fix(security): allow CREATE EXTENSION only in write mode for hypopg and pg_stat_statements"
```

---

### Task 2: Ложные срабатывания: `generate_series`, `AT TIME ZONE`, `SIMILAR TO`, табличные функции с описанием колонок

**Files:**
- Modify: `src/postgres_fastmcp/postgres/security/_allowed_functions.py`
- Modify: `src/postgres_fastmcp/postgres/security/statement_policies.py` (импорты `ColumnDef`, `ReturningClause`; `ALLOWED_NODE_TYPES`; новый `WRITE_NODE_TYPES`)
- Modify: `src/postgres_fastmcp/postgres/security/query_validator.py:22-24, 180-187`
- Test: `tests/unit/postgres/test_query_validator.py` (классы `TestQueryValidatorFunctions`, `TestQueryValidatorDmlMode`)

**Interfaces:**
- Consumes: `QueryValidator.validate`.
- Produces: allowlist функций с `generate_series`, `generate_subscripts`, `timezone`, `similar_to_escape`, `similar_escape`; `ColumnDef` в `ALLOWED_NODE_TYPES`; `WRITE_NODE_TYPES = WRITE_STMT_TYPES | {ReturningClause}` в `statement_policies.py`, валидатор расширяет узлы именно им.

Справка для исполнителя: `a AT TIME ZONE 'UTC'` парсер превращает в вызов `pg_catalog.timezone(...)`, `a SIMILAR TO 'x'` — в `pg_catalog.similar_to_escape(...)`; префикс `pg_catalog.` валидатор уже срезает. `json_to_recordset('[]') AS x(a int)` падает не на функции, а на узле `ColumnDef` внутри `RangeFunction`. `INSERT ... RETURNING *` в write-режиме падает на узле `ReturningClause` (грамматика pglast 8): его разрешаем только вместе с write-операторами.

- [ ] **Step 1: Добавить тесты в класс TestQueryValidatorFunctions**

В `tests/unit/postgres/test_query_validator.py` добавить в класс `TestQueryValidatorFunctions` методы:

```python
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM generate_series(1, 10)",
            "SELECT generate_subscripts(ARRAY[1,2], 1)",
            "SELECT created_at AT TIME ZONE 'UTC' FROM t",
            "SELECT * FROM t WHERE name SIMILAR TO 'a%'",
            "SELECT * FROM json_to_recordset('[{\"a\":1}]') AS x(a int)",
            "SELECT * FROM jsonb_to_recordset('[]'::jsonb) AS x(a int, b text)",
        ],
    )
    def test_allows_common_read_only_constructs(self, sql: str) -> None:
        """Everyday SELECT constructs must not be rejected as unsafe."""
        v = QueryValidator(read_only=True)
        v.validate(sql)

    def test_column_def_does_not_unlock_create_table(self) -> None:
        """ColumnDef is allowed as an AST node, but CREATE TABLE is still rejected at statement level."""
        v = QueryValidator(read_only=False)
        with pytest.raises((StatementTypeNotAllowedError, DdlNotAllowedError)):
            v.validate("CREATE TABLE t (id int)")
```

В класс `TestQueryValidatorDmlMode` добавить:

```python
    @pytest.mark.parametrize(
        "sql",
        [
            "INSERT INTO t (a) VALUES (1) RETURNING id",
            "UPDATE t SET a = 1 WHERE id = 1 RETURNING *",
            "WITH w AS (INSERT INTO t VALUES (1) RETURNING *) SELECT * FROM w",
        ],
    )
    def test_returning_allowed_in_write_mode(self, sql: str) -> None:
        """RETURNING is part of DML and must pass when DML is allowed."""
        QueryValidator(read_only=False).validate(sql)

    def test_returning_still_blocked_in_read_only(self) -> None:
        """A data-modifying CTE stays rejected in read-only mode."""
        with pytest.raises(UserFacingError):
            QueryValidator(read_only=True).validate("WITH w AS (INSERT INTO t VALUES (1) RETURNING *) SELECT * FROM w")
```

и добавить `UserFacingError` в импорт из `postgres_fastmcp.shared.errors` в шапке файла.

- [ ] **Step 2: Запустить и убедиться, что падают**

Run: `<env> uv run pytest tests/unit/postgres/test_query_validator.py -q -k "common_read_only or column_def or returning"`
Expected: шесть параметризованных FAIL в `common_read_only` (`FunctionNotAllowedError` или `DisallowedNodeTypeError`), три FAIL в `returning_allowed_in_write_mode` (`DisallowedNodeTypeError: ReturningClause`); `test_column_def_does_not_unlock_create_table` и `test_returning_still_blocked_in_read_only` PASS.

- [ ] **Step 3: Добавить функции в allowlist**

В `src/postgres_fastmcp/postgres/security/_allowed_functions.py` внутри кортежа `ALLOWED_FUNCTIONS` добавить (в алфавитном порядке рядом с соседями по букве; если точное место неочевидно, добавить перед закрывающей скобкой кортежа):

```python
        "generate_series",
        "generate_subscripts",
        "timezone",
        "similar_to_escape",
        "similar_escape",
```

- [ ] **Step 4: Добавить ColumnDef в узлы и ReturningClause в write-набор**

В `src/postgres_fastmcp/postgres/security/statement_policies.py`:
- в импорт `from pglast.ast import (...)` добавить `ColumnDef,` (алфавитно после `CollateClause,`) и `ReturningClause,` (после `ResTarget,`);
- в `ALLOWED_NODE_TYPES` добавить `ColumnDef,` сразу после `CollateClause,`;
- после `WRITE_STMT_TYPES` добавить:

```python
# AST nodes that exist only inside write statements (RETURNING ...). Allowed together with them.
WRITE_NODE_TYPES: set[type] = WRITE_STMT_TYPES | {ReturningClause}
```

В `src/postgres_fastmcp/postgres/security/query_validator.py` импорт заменить на:

```python
from postgres_fastmcp.postgres.security.statement_policies import ALLOWED_STMT_TYPES, WRITE_NODE_TYPES, WRITE_STMT_TYPES
```

и в `validate` строку `allowed_node_types |= WRITE_STMT_TYPES` заменить на `allowed_node_types |= WRITE_NODE_TYPES`.

- [ ] **Step 5: Запустить тесты валидатора**

Run: `<env> uv run pytest tests/unit/postgres/test_query_validator.py -q`
Expected: все PASS, включая `test_blocks_disallowed_function` (`pg_sleep` по-прежнему заблокирован).

- [ ] **Step 6: Линтеры и коммит**

Run: `uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format`

```bash
git add src/postgres_fastmcp/postgres/security/_allowed_functions.py src/postgres_fastmcp/postgres/security/statement_policies.py src/postgres_fastmcp/postgres/security/query_validator.py tests/unit/postgres/test_query_validator.py
git commit -m "fix(security): stop rejecting generate_series, AT TIME ZONE, SIMILAR TO, column-defined table functions and RETURNING"
```

---

### Task 3: `statement_timeout` на стороне Postgres в `SafeSqlExecutor`

**Files:**
- Modify: `src/postgres_fastmcp/postgres/security/driver.py` (переписывается целиком)
- Test: `tests/unit/postgres/test_safe_sql_executor.py`

**Interfaces:**
- Consumes: `QueryTimeoutError(timeout_seconds: float)` из `shared/errors.py`; `psycopg.errors.QueryCanceled`.
- Produces: `SafeSqlConfig` с новым полем `client_timeout_grace: float = 5.0`; `SafeSqlExecutor.execute` добавляет `SET LOCAL statement_timeout = <ms>;` перед `SET LOCAL search_path` и превращает `QueryCanceled` в `QueryTimeoutError`. `DbAccessService` не меняется (использует значения по умолчанию).

- [ ] **Step 1: Обновить и дополнить тесты исполнителя**

В `tests/unit/postgres/test_safe_sql_executor.py`:

Дополнить импорты:

```python
from psycopg.errors import QueryCanceled
```

В классе `TestSafeSqlExecutorSearchPathAndTag` добавить:

```python
    async def test_statement_timeout_prepended_before_search_path(self) -> None:
        """statement_timeout (ms) is set inside the transaction and precedes search_path."""
        mock_delegate = AsyncMock(return_value=[])
        config = SafeSqlConfig(query_tag="t", allowed_schema="public", timeout=30)
        executor = _make_executor(mock_delegate, config=config)
        await executor.execute("SELECT 1")
        sent = mock_delegate.execute.call_args[0][0]
        assert sent.startswith("SET LOCAL statement_timeout = 30000; SET LOCAL search_path = public; ")
        assert sent.endswith("/* t */ SELECT 1")

    async def test_no_statement_timeout_when_timeout_is_none(self) -> None:
        """Without a configured timeout no statement_timeout is sent."""
        mock_delegate = AsyncMock(return_value=[])
        executor = _make_executor(mock_delegate, config=SafeSqlConfig(query_tag="t"))
        await executor.execute("SELECT 1")
        assert "statement_timeout" not in mock_delegate.execute.call_args[0][0]
```

В классе `TestSafeSqlExecutorTimeout` заменить `test_timeout_raises_when_delegate_slow` на:

```python
    async def test_client_timeout_raises_after_grace(self) -> None:
        """When Postgres never answers, the client-side guard (timeout + grace) raises QueryTimeoutError."""

        async def slow_execute(*args: object, **kwargs: object) -> list[RowResult]:
            await asyncio.sleep(1.0)
            return []

        mock_delegate = MagicMock()
        mock_delegate.execute = AsyncMock(side_effect=slow_execute)
        config = SafeSqlConfig(query_tag="t", timeout=0.01, client_timeout_grace=0.0)
        executor = _make_executor(mock_delegate, config=config)
        with pytest.raises(QueryTimeoutError) as exc_info:
            await executor.execute("SELECT 1")
        assert exc_info.value.timeout_seconds == 0.01

    async def test_server_side_cancel_maps_to_query_timeout_error(self) -> None:
        """psycopg QueryCanceled (statement_timeout fired in Postgres) becomes QueryTimeoutError."""
        mock_delegate = MagicMock()
        mock_delegate.execute = AsyncMock(side_effect=QueryCanceled("canceling statement due to statement timeout"))
        config = SafeSqlConfig(query_tag="t", timeout=30)
        executor = _make_executor(mock_delegate, config=config)
        with pytest.raises(QueryTimeoutError) as exc_info:
            await executor.execute("SELECT 1")
        assert exc_info.value.timeout_seconds == 30
        assert isinstance(exc_info.value.__cause__, QueryCanceled)
```

- [ ] **Step 2: Запустить и убедиться, что падают**

Run: `<env> uv run pytest tests/unit/postgres/test_safe_sql_executor.py -q`
Expected: FAIL `test_statement_timeout_prepended_before_search_path` (AssertionError), `test_client_timeout_raises_after_grace` (TypeError: unexpected keyword `client_timeout_grace`), `test_server_side_cancel_maps_to_query_timeout_error` (QueryCanceled пролетает как есть).

- [ ] **Step 3: Переписать driver.py**

Заменить содержимое `src/postgres_fastmcp/postgres/security/driver.py` целиком:

```python
"""Исполнитель безопасного SQL: валидация + statement_timeout + search_path вокруг делегирующего исполнителя."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, cast

from psycopg.errors import QueryCanceled
from psycopg.sql import SQL, Composable, Literal

from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.postgres.security.query_validator import QueryValidator
from postgres_fastmcp.shared.errors import QueryTimeoutError


logger = logging.getLogger(__name__)

MS_PER_SECOND = 1000


@dataclass(frozen=True, slots=True)
class SafeSqlConfig:
    """Конфигурация SafeSqlExecutor.

    Attributes:
        query_tag: Тег добавляется к запросам для логирования/мониторинга.
        timeout: Таймаут выполнения в секундах; выставляется как statement_timeout в Postgres.
        allowed_schema: Разрешенная схема (например, 'public'); None означает все.
        read_only: Если True, только операторы чтения; если False, разрешен DML.
        table_prefix: Если задан вместе с allowed_schema, только таблицы с этим префиксом.
        client_timeout_grace: Запас в секундах для клиентской страховки поверх statement_timeout.
            Обычно срабатывает Postgres; клиентский таймаут ловит зависшее соединение.
    """

    query_tag: str = "postgres-fastmcp"
    timeout: float | None = None
    allowed_schema: str | None = None
    read_only: bool = True
    table_prefix: str | None = None
    client_timeout_grace: float = 5.0


class SafeSqlExecutor:
    """Композиционная обертка: валидация SQL, установка statement_timeout/search_path, делегирование выполнения."""

    def __init__(
        self,
        delegate: Any,  # noqa: ANN401
        validator: QueryValidator,
        config: SafeSqlConfig,
    ) -> None:
        """Инициализация с делегирующим исполнителем, валидатором и конфигурацией.

        Args:
            delegate: Исполнитель с асинхронным execute(query, params=..., readonly=...) -> list[RowResult]|None.
            validator: Валидатор, используемый для валидации каждого запроса перед выполнением.
            config: Конфигурация безопасного SQL (тег, таймаут, схема, read_only, префикс).
        """
        self._delegate = delegate
        self._validator = validator
        self._config = config

    async def execute(
        self,
        query: str,
        params: list[Any] | None = None,
        *,
        readonly: bool = True,  # noqa: ARG002 — part of QueryExecutorPort; effective value from config
    ) -> list[RowResult] | None:
        """Валидация запроса, затем выполнение через делегата в транзакции с statement_timeout и search_path.

        Args:
            query: SQL для выполнения.
            params: Необязательные параметры (будут встроены в запрос перед выполнением).
            readonly: Игнорируется; используется self._config.read_only.

        Returns:
            Строки или None для операторов без результата.

        Raises:
            QueryTimeoutError: Postgres отменил запрос по statement_timeout либо сработала клиентская страховка.
        """
        query = self.render(query, params) if params else f"/* {self._config.query_tag} */ {query}"
        self._validator.validate(query)
        query = self._with_session_settings(query)
        if self._config.timeout is None:
            return await self._run(query)
        try:
            async with asyncio.timeout(self._config.timeout + self._config.client_timeout_grace):
                return await self._run(query)
        except TimeoutError as e:
            logger.warning("Client-side timeout after %ss: %s...", self._config.timeout, query[:100])
            raise QueryTimeoutError(self._config.timeout) from e

    async def _run(self, query: str) -> list[RowResult] | None:
        """Выполнить через делегата; отмену по statement_timeout превратить в QueryTimeoutError."""
        try:
            return cast(
                "list[RowResult] | None",
                await self._delegate.execute(query, params=None, readonly=self._config.read_only),
            )
        except QueryCanceled as e:
            logger.warning("Postgres cancelled the statement (statement_timeout=%ss): %s...", self._config.timeout, query[:100])
            raise QueryTimeoutError(self._config.timeout or 0.0) from e

    def _with_session_settings(self, query: str) -> str:
        """Добавить SET LOCAL statement_timeout и search_path; порядок важен для читаемости логов."""
        prefix: list[str] = []
        if self._config.timeout is not None:
            prefix.append(f"SET LOCAL statement_timeout = {int(self._config.timeout * MS_PER_SECOND)};")
        if self._config.allowed_schema:
            prefix.append(f"SET LOCAL search_path = {self._config.allowed_schema};")
        return " ".join([*prefix, query])

    def render(self, query: str, params: list[Any]) -> str:
        """Рендер параметризованного запроса в одну строку (для выполнения без параметров на стороне сервера).

        Args:
            query: Запрос с {} плейсхолдерами (стиль psycopg).
            params: Значения для подстановки.

        Returns:
            Строка запроса с встроенными значениями (с тегом).
        """
        composables = [p if isinstance(p, Composable) else Literal(p) for p in params]
        rendered = SQL(query).format(*composables).as_string()
        return f"/* {self._config.query_tag} */ {rendered}"
```

- [ ] **Step 4: Запустить тесты исполнителя и всего слоя postgres**

Run: `<env> uv run pytest tests/unit/postgres -q`
Expected: все PASS. Тест `test_search_path_prepended_when_allowed_schema_set` проверяет только подстроку и остаётся зелёным.

- [ ] **Step 5: Линтеры и коммит**

Run: `uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format`
Expected: ноль ошибок (если ruff ругается на длину строки в `logger.warning`, разбить аргументы на строки).

```bash
git add src/postgres_fastmcp/postgres/security/driver.py tests/unit/postgres/test_safe_sql_executor.py
git commit -m "feat(security): enforce statement_timeout in Postgres and map QueryCanceled to QueryTimeoutError"
```

---

### Task 4: Не пересоздавать пул на ошибках SQL

**Files:**
- Modify: `src/postgres_fastmcp/postgres/driver.py:891-910` (`except Exception` в `SqlExecutor.execute`)
- Test: `tests/unit/postgres/test_sql_executor.py` (класс `TestSqlExecutorExecuteWithPool`)

**Interfaces:**
- Consumes: `DbConnPool.mark_invalid(error)`; иерархия `psycopg.Error` (`OperationalError`, `InterfaceError`, `DatabaseError`, `psycopg.errors.QueryCanceled`).
- Produces: `SqlExecutor` помечает пул невалидным только при ошибках соединения; ошибки самого SQL (синтаксис, отсутствующая таблица, `statement_timeout`) пул не трогают.

Контекст: сейчас `SqlExecutor.execute` вызывает `mark_invalid` на любом исключении, а `DbConnPool.pool_connect` при невалидном пуле закрывает и пересоздаёт его. Итог: каждая ошибочная команда агента (опечатка в SQL, `statement_timeout` из Task 3) роняет и заново открывает пул. `QueryCanceled` в psycopg наследует `OperationalError`, поэтому нужен явный исключающий случай.

- [ ] **Step 1: Добавить тесты**

В `tests/unit/postgres/test_sql_executor.py` дополнить импорты:

```python
from psycopg.errors import AdminShutdown, QueryCanceled, UndefinedTable
```

В класс `TestSqlExecutorExecuteWithPool` добавить:

```python
    @pytest.mark.parametrize(
        "error",
        [UndefinedTable("relation \"t\" does not exist"), QueryCanceled("canceling statement due to statement timeout")],
        ids=["programming-error", "statement-timeout"],
    )
    async def test_sql_errors_do_not_invalidate_pool(self, error: Exception) -> None:
        """A failed statement is the client's problem, not the pool's: the pool stays valid."""
        mock_pool = self._mock_pool_for_execute()
        executor = SqlExecutor(conn=mock_pool)
        with patch.object(executor, "_execute_with_connection", new_callable=AsyncMock) as mock_exec:
            mock_exec.side_effect = error
            with pytest.raises(type(error)):
                await executor.execute("SELECT 1", readonly=True)
            mock_pool.mark_invalid.assert_not_called()

    async def test_connection_errors_invalidate_pool(self) -> None:
        """A server shutdown (OperationalError that is not QueryCanceled) invalidates the pool."""
        mock_pool = self._mock_pool_for_execute()
        executor = SqlExecutor(conn=mock_pool)
        with patch.object(executor, "_execute_with_connection", new_callable=AsyncMock) as mock_exec:
            mock_exec.side_effect = AdminShutdown("terminating connection due to administrator command")
            with pytest.raises(AdminShutdown):
                await executor.execute("SELECT 1", readonly=True)
            mock_pool.mark_invalid.assert_called_once()
```

Существующий `test_execute_exception_marks_pool_invalid` (с `RuntimeError`) оставить: не-psycopg исключения по-прежнему считаются проблемой соединения.

- [ ] **Step 2: Запустить и убедиться, что падают**

Run: `<env> uv run pytest tests/unit/postgres/test_sql_executor.py -q`
Expected: два параметризованных `test_sql_errors_do_not_invalidate_pool` FAIL (`mark_invalid` вызван), остальные PASS.

- [ ] **Step 3: Добавить предикат в driver.py**

В `src/postgres_fastmcp/postgres/driver.py` дополнить импорты:

```python
from psycopg import Error as PsycopgError, InterfaceError, OperationalError
from psycopg.errors import QueryCanceled
```

Добавить модульную функцию перед `class SqlExecutor`:

```python
def _is_connection_error(error: Exception) -> bool:
    """Отличить ошибку соединения (пул надо пересоздать) от ошибки самого SQL (пул исправен).

    Не-psycopg исключения считаем проблемой соединения (консервативно). Среди psycopg-ошибок
    только InterfaceError/OperationalError говорят о соединении, но QueryCanceled наследует
    OperationalError и означает лишь statement_timeout или pg_cancel_backend.
    """
    if not isinstance(error, PsycopgError):
        return True
    if isinstance(error, QueryCanceled):
        return False
    return isinstance(error, (InterfaceError, OperationalError))
```

В `SqlExecutor.execute` блок `except Exception as e:` заменить на:

```python
        except Exception as e:
            if not _is_connection_error(e):
                raise
            if self.conn and self._is_pool and isinstance(self.conn, DbConnPool):
                self.conn.mark_invalid(str(e))
            elif self.conn and not self._is_pool:
                self.conn = None
            raise
```

- [ ] **Step 4: Запустить тесты слоя postgres**

Run: `<env> uv run pytest tests/unit/postgres -q`
Expected: все PASS.

- [ ] **Step 5: Линтеры и коммит**

Run: `uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format`
Expected: ноль ошибок.

```bash
git add src/postgres_fastmcp/postgres/driver.py tests/unit/postgres/test_sql_executor.py
git commit -m "fix(postgres): keep the pool on SQL errors, invalidate only on connection errors"
```

---

### Task 5: Тестовый корпус валидатора

**Files:**
- Create: `tests/unit/postgres/test_query_validator_corpus.py`

**Interfaces:**
- Consumes: `QueryValidator` после Task 1–2.
- Produces: регрессионный набор, который фиксирует границы валидатора для трёх режимов.

- [ ] **Step 1: Создать файл корпуса**

```python
# mypy: ignore-errors
"""Регрессионный корпус QueryValidator: что обязан блокировать и что обязан пропускать.

Наборы собраны при аудите 2026-09-26. Любое изменение политики должно
отразиться здесь осознанно, а не сломать тест случайно.
"""

import pytest

from postgres_fastmcp.postgres.security.query_validator import QueryValidator
from postgres_fastmcp.shared.errors import UserFacingError


FULL_READ_ONLY = QueryValidator(read_only=True, allow_explain_analyze=True)
BASIC_READ_ONLY = QueryValidator(allowed_schema="public", table_prefix="app_", read_only=True)
BASIC_WRITE = QueryValidator(allowed_schema="public", read_only=False)

# Попытки записи или побочных эффектов: блокируются в ЛЮБОМ ограниченном режиме.
MUST_BLOCK_EVERYWHERE = [
    "SELECT pg_sleep(10)",
    "SELECT pg_terminate_backend(123)",
    "SELECT pg_cancel_backend(123)",
    "SELECT set_config('work_mem', '1GB', false)",
    "SELECT pg_reload_conf()",
    "SELECT pg_advisory_lock(1)",
    "SELECT lo_import('/etc/passwd')",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT nextval('s')",
    "SELECT setval('s', 100)",
    "SELECT * INTO new_t FROM t",
    "CREATE TABLE t AS SELECT 1",
    "CREATE TABLE t (id int)",
    "DROP TABLE t",
    "ALTER TABLE t ADD COLUMN x int",
    "TRUNCATE t",
    "SELECT * FROM t FOR UPDATE",
    "CREATE EXTENSION dblink",
    "CREATE EXTENSION file_fdw",
    "SELECT * FROM dblink('dbname=x', 'DROP TABLE t') AS t(a int)",
    "SET search_path = evil",
    "COMMIT",
    "BEGIN; INSERT INTO t VALUES (1); COMMIT",
    "COPY t TO '/tmp/x'",
    "COPY t FROM PROGRAM 'id'",
    "DO $$ BEGIN PERFORM 1; END $$",
    "CALL proc()",
    "VACUUM t",
    "ANALYZE t",
    "LISTEN c",
    "NOTIFY c",
    "LOCK TABLE t",
    "REFRESH MATERIALIZED VIEW mv",
    "GRANT ALL ON t TO PUBLIC",
    "MERGE INTO t USING u ON t.a = u.a WHEN MATCHED THEN DELETE",
    "SELECT txid_current()",
    "SELECT pg_notify('c', 'p')",
    "SELECT query_to_xml('select 1', true, false, '')",
]

# Запись: блокируется в read-only, разрешена в BASIC_WRITE.
DML_STATEMENTS = [
    "INSERT INTO t VALUES (1)",
    "UPDATE t SET a = 1",
    "DELETE FROM t",
    "INSERT INTO t VALUES (1) RETURNING id",
    "WITH w AS (INSERT INTO t VALUES (1) RETURNING *) SELECT * FROM w",
    "CREATE EXTENSION IF NOT EXISTS hypopg",
]

# Обычный read-only SQL: обязан проходить во всех режимах (нет ссылок на таблицы, чтобы не задеть prefix).
MUST_ALLOW_EVERYWHERE = [
    "SELECT 1",
    "SELECT (SELECT 1)",
    "SELECT 1 UNION ALL SELECT 2",
    "VALUES (1), (2)",
    "SELECT ARRAY[1,2]",
    "SELECT ROW(1,2)",
    "SELECT EXISTS (SELECT 1)",
    "SELECT GREATEST(1, 2)",
    "SELECT extract(epoch FROM now())",
    "SELECT interval '1 day'",
    "SELECT $1",
    "SELECT * FROM generate_series(1, 10)",
    "SELECT * FROM json_to_recordset('[]') AS x(a int)",
    "SELECT jsonb_path_query('{}', '$')",
    "SELECT now() AT TIME ZONE 'UTC'",
    "SELECT 'abc' SIMILAR TO 'a%'",
    "SELECT current_setting('server_version')",
    "SHOW search_path",
    "PREPARE p AS SELECT 1",
    "DECLARE c CURSOR FOR SELECT 1",
    "EXPLAIN SELECT 1",
]

# Read-only SQL с таблицами: проходит в full, в basic требует префикс app_.
TABLE_QUERIES_FULL = [
    "SELECT * FROM secret.t",
    "SELECT * FROM pg_catalog.pg_class",
    "SELECT * FROM information_schema.schemata",
    "SELECT a::int FROM t",
    "SELECT string_agg(a, ',' ORDER BY a) FROM t",
    "SELECT count(*) FILTER (WHERE a > 1) FROM t",
    "SELECT a, row_number() OVER (PARTITION BY b) FROM t",
    "SELECT * FROM t GROUP BY GROUPING SETS ((a), ())",
    "SELECT * FROM t, LATERAL (SELECT 1) x",
    "SELECT 1 FROM t TABLESAMPLE SYSTEM (10)",
    "SELECT * FROM t WHERE a IN (SELECT b FROM u)",
    "EXPLAIN ANALYZE SELECT * FROM t",
]

TABLE_QUERIES_BASIC_ALLOWED = [
    "SELECT * FROM app_users",
    "SELECT * FROM public.app_users WHERE id = 1",
    "SELECT * FROM information_schema.tables",
]

TABLE_QUERIES_BASIC_BLOCKED = [
    "SELECT * FROM users",
    "SELECT * FROM secret.app_users",
    "SELECT * FROM pg_catalog.pg_class",
    "SELECT * FROM information_schema.schemata",
    "EXPLAIN ANALYZE SELECT * FROM app_users",
    "SELECT * FROM app_users WHERE name LIKE other_col",
]


@pytest.mark.parametrize("validator", [FULL_READ_ONLY, BASIC_READ_ONLY, BASIC_WRITE], ids=["full-ro", "basic-ro", "basic-rw"])
@pytest.mark.parametrize("sql", MUST_BLOCK_EVERYWHERE)
def test_blocks_side_effects_in_every_mode(validator: QueryValidator, sql: str) -> None:
    with pytest.raises(UserFacingError):
        validator.validate(sql)


@pytest.mark.parametrize("validator", [FULL_READ_ONLY, BASIC_READ_ONLY], ids=["full-ro", "basic-ro"])
@pytest.mark.parametrize("sql", DML_STATEMENTS)
def test_read_only_blocks_dml(validator: QueryValidator, sql: str) -> None:
    with pytest.raises(UserFacingError):
        validator.validate(sql)


@pytest.mark.parametrize("sql", DML_STATEMENTS)
def test_write_mode_allows_dml(sql: str) -> None:
    QueryValidator(read_only=False).validate(sql)


@pytest.mark.parametrize("validator", [FULL_READ_ONLY, BASIC_READ_ONLY, BASIC_WRITE], ids=["full-ro", "basic-ro", "basic-rw"])
@pytest.mark.parametrize("sql", MUST_ALLOW_EVERYWHERE)
def test_allows_plain_read_only_sql(validator: QueryValidator, sql: str) -> None:
    validator.validate(sql)


@pytest.mark.parametrize("sql", TABLE_QUERIES_FULL)
def test_full_allows_any_schema(sql: str) -> None:
    FULL_READ_ONLY.validate(sql)


@pytest.mark.parametrize("sql", TABLE_QUERIES_BASIC_ALLOWED)
def test_basic_allows_prefixed_public_tables(sql: str) -> None:
    BASIC_READ_ONLY.validate(sql)


@pytest.mark.parametrize("sql", TABLE_QUERIES_BASIC_BLOCKED)
def test_basic_blocks_foreign_schemas_and_prefix_mismatch(sql: str) -> None:
    with pytest.raises(UserFacingError):
        BASIC_READ_ONLY.validate(sql)
```

- [ ] **Step 2: Запустить корпус**

Run: `<env> uv run pytest tests/unit/postgres/test_query_validator_corpus.py -q`
Expected: все PASS. Если какой-то кейс падает, это либо ошибка в корпусе (проверьте SQL на опечатку), либо реальная дыра/ложное срабатывание: в первом случае поправьте SQL, во втором остановитесь и вынесите в отдельное решение, не ослабляйте политику молча.

- [ ] **Step 3: Линтеры и коммит**

Run: `uv run ruff check --fix --show-fixes && uv run ruff format`
Expected: ноль ошибок (tests исключены из mypy).

```bash
git add tests/unit/postgres/test_query_validator_corpus.py
git commit -m "test(security): add regression corpus for the SQL validator"
```

---

### Task 6: Интеграционные тесты на реальной БД

**Files:**
- Create: `tests/integration/test_sql_hardening.py`

**Interfaces:**
- Consumes: фикстура `test_postgres_connection_string` (Docker Postgres 15/16 с hypopg), `DatabaseConfig.from_uri`, `DbAccessService(config).sql_driver`, `DbConnPool.is_valid`, `create_server(Settings)`, `Client`.
- Produces: end-to-end подтверждение `statement_timeout` и политики `CREATE EXTENSION`.

- [ ] **Step 1: Создать файл**

```python
# mypy: ignore-errors
"""Integration tests for SQL hardening: server-side statement_timeout and CREATE EXTENSION policy."""

import pytest
from fastmcp import Client

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.app.server import create_server
from postgres_fastmcp.domains.db_access import DbAccessService
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.shared.errors import QueryTimeoutError


def _text(result: object) -> str:
    """Собрать текст ответа тула для assert."""
    data = result.data if hasattr(result, "data") else None
    return str(data if data is not None else getattr(result, "content", ""))


@pytest.mark.asyncio
async def test_statement_timeout_cancels_long_query(test_postgres_connection_string: tuple[str, str]) -> None:
    """A query longer than safe_sql_timeout is cancelled by Postgres and surfaces as QueryTimeoutError."""
    connection_string, _ = test_postgres_connection_string
    config = DatabaseConfig.from_uri(
        connection_string,
        access_mode=AccessMode.FULL,
        write_mode=False,
        safe_sql_timeout=1,
    )
    service = DbAccessService(config)
    try:
        with pytest.raises(QueryTimeoutError):
            await service.sql_driver.execute("SELECT count(*) FROM generate_series(1, 10000000000)")
        # Пул после statement_timeout остаётся валидным (Task 4): следующий запрос идёт без пересоздания.
        assert service.db_connection.is_valid is True
        rows = await service.sql_driver.execute("SELECT 1 AS one")
        assert rows is not None and rows[0].cells["one"] == 1
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_create_extension_rejected_in_read_only(test_postgres_connection_string: tuple[str, str]) -> None:
    """read-only: CREATE EXTENSION is rejected by the validator with a read-only message."""
    connection_string, _ = test_postgres_connection_string
    database = DatabaseConfig.from_uri(connection_string, access_mode=AccessMode.FULL, write_mode=False)
    mcp = create_server(Settings(database=database))
    async with Client(mcp) as client:
        result = await client.call_tool(
            "execute_sql",
            {"sql": "CREATE EXTENSION IF NOT EXISTS hypopg"},
            raise_on_error=False,
        )
    assert result.is_error is True
    assert "read-only" in _text(result).lower()


@pytest.mark.asyncio
async def test_create_extension_hypopg_allowed_in_basic_write(
    test_postgres_connection_string: tuple[str, str],
) -> None:
    """basic + write_mode: hypopg may be created (SafeSqlExecutor path, not the unrestricted executor)."""
    connection_string, _ = test_postgres_connection_string
    database = DatabaseConfig.from_uri(connection_string, access_mode=AccessMode.BASIC, write_mode=True)
    mcp = create_server(Settings(database=database))
    async with Client(mcp) as client:
        result = await client.call_tool("execute_sql", {"sql": "CREATE EXTENSION IF NOT EXISTS hypopg"})
        assert result.is_error is False
        blocked = await client.call_tool(
            "execute_sql",
            {"sql": "CREATE EXTENSION IF NOT EXISTS dblink"},
            raise_on_error=False,
        )
    assert blocked.is_error is True
    assert "dblink" in _text(blocked)
```

- [ ] **Step 2: Запустить локально, если есть Docker**

Run: `<env> uv run pytest tests/integration/test_sql_hardening.py -v --timeout=180`
Expected: 6 PASS (3 теста × Postgres 15/16), либо `skipped` без Docker (тогда проверяет CI). Тест таймаута должен завершаться примерно за 1–2 секунды на каждый образ: если он идёт дольше 6 секунд, значит `statement_timeout` не выставился и сработала клиентская страховка; это провал, разберитесь с `_with_session_settings`.

- [ ] **Step 3: Обновить tests/README.md**

В таблицу интеграционного покрытия `tests/README.md` (раздел «Integration Tests») добавить строку списка файлов:

```markdown
  - `test_sql_hardening.py`: server-side statement_timeout and CREATE EXTENSION policy
```

- [ ] **Step 4: Линтеры и коммит**

Run: `uv run ruff check --fix --show-fixes && uv run ruff format`

```bash
git add tests/integration/test_sql_hardening.py tests/README.md
git commit -m "test(integration): cover statement_timeout and CREATE EXTENSION policy end-to-end"
```

---

### Task 7: README и финальная проверка

**Files:**
- Modify: `README.md` (раздел «Безопасное выполнение SQL» в «Технические детали» и раздел «Защищённое выполнение SQL» в «Технические заметки»)

- [ ] **Step 1: Обновить README**

В разделе «### Безопасное выполнение SQL» заменить нумерованный список на:

```markdown
1. **Разбор SQL** — библиотека `pglast` анализирует SQL перед выполнением; разрешён только allowlist типов операторов, узлов AST и функций
2. **Транзакции только для чтения** — в режимах только чтение используются read-only транзакции PostgreSQL
3. **Проверки COMMIT/ROLLBACK** — блокируются попытки обойти режим только чтение
4. **Таймауты** — `safe_sql_timeout` выставляется как `statement_timeout` внутри транзакции, запрос отменяет сам PostgreSQL
5. **Расширения** — `CREATE EXTENSION` допускается только для `hypopg` и `pg_stat_statements` и только при `write_mode=true`
```

В разделе «### Защищённое выполнение SQL» («Технические заметки») заменить список на:

```markdown
- Разбор SQL через `pglast` для выявления и отклонения небезопасных операторов
- Транзакции только для чтения в ограниченных режимах
- `statement_timeout` на стороне PostgreSQL в ограниченных режимах
- Ограничения по схемам для режима `basic`
- `CREATE EXTENSION` только для `hypopg` и `pg_stat_statements` в режиме записи
```

- [ ] **Step 2: Полный локальный CI**

Run:
```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src/ && \
FASTMCP_MCP_CAMELCASE_COMPAT=false <env> uv run pytest tests/unit -q
```
Expected: ноль ошибок, все тесты зелёные.

- [ ] **Step 3: Commit, push, PR**

```bash
git add README.md
git commit -m "docs(readme): describe statement_timeout and CREATE EXTENSION policy"
git push -u origin claude/sql-hardening
gh pr create --title "Harden SQL validator: extension policy, statement_timeout, validator corpus" --body "Step 2 of docs/superpowers/specs/2026-09-26-auth-fastmcp4-hardening-design.md: CREATE EXTENSION only for hypopg/pg_stat_statements in write mode, fewer false positives (generate_series, AT TIME ZONE, SIMILAR TO, ColumnDef, RETURNING), statement_timeout enforced in Postgres, pool no longer recreated on SQL errors, regression corpus."
```

Expected: CI зелёный, включая интеграционную джобу.
