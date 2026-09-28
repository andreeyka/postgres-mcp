# Слой подключения: параметры libpq, max_idle, сброс hypopg — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Параметры libpq из URI и конфига доходят до подключения (`connect_options`), `max_inactive_connection_lifetime` работает как `max_idle` пула, а гипотетические индексы hypopg сбрасываются на том же соединении при его возврате в пул.

**Architecture:** `DatabaseConfig.connect_options: dict[str, str]` заменяет неиспользуемый `extra_kwargs`; `uri_fields` кладёт туда параметры query string, `database_uri` возвращает их в URI. `DbConnPool` получает `max_idle` и `reset`-callback: соединение, на котором `SqlExecutor` выполнял SQL с `hypopg_create_index`, помечается (`weakref.WeakSet`) и при возврате в пул получает `SELECT hypopg_reset()`. Спека: `docs/superpowers/specs/2026-09-28-basic-followups-design.md`, §3 (PR 2).

**Tech Stack:** Python 3.12, uv, pydantic 2 + pydantic-settings 2.14, psycopg 3.3.4 + psycopg_pool 3.3.1, pytest (asyncio auto).

## Spec corrections

- `database_uri` должен кодировать query string через `urlencode(..., quote_via=quote)`: по умолчанию `urlencode` превращает пробел в `+`, а libpq `+` не декодирует — `options=-c statement_timeout=5000` дошёл бы до сервера как `-c+statement_timeout=5000` (проверено `psycopg.conninfo.conninfo_to_dict`). Спека про кодирование молчит; намерение (параметры доходят до подключения как есть) сохраняется.
- `connect_options` попадает в результат `uri_fields` только если в query string есть параметры кроме `sslmode`/`client_encoding`: иначе URI без параметров стёр бы `connect_options` из `config.json` (спека: «словарь из URI заменяет словарь конфига целиком (как остальные поля URI)» — остальные поля URI тоже задаются, только если они в URI есть).
- psycopg_pool при заданном `reset` возвращает каждое соединение в пул через рабочую задачу (`ReturnConnection`), а не синхронно; при исключении из `reset` пул сам пишет `WARNING error resetting connection` и закрывает соединение (проверено по `psycopg_pool/pool_async.py`, `_reset_connection`). Наш `WARNING` перед повторным подъёмом остаётся — он называет причину (hypopg).
- Если hypopg не установлен, помеченное соединение (запрос с `hypopg_create_index` уже упал) при возврате не сбросится и будет закрыто пулом — пул откроет новое. Это и есть «устойчивость к отсутствию расширения»: сервер работает, остатка нет.

## Global Constraints

- Всё, что видит агент или внешняя система (ошибки, логи, коммиты, описания тулов), — на английском; docstring и комментарии — по-русски. README — по-русски.
- Нет `from __future__ import annotations`.
- Ломающие изменения разрешены; никаких шимов, deprecation-предупреждений и упоминаний старого поведения в коде (удалённый `extra_kwargs` не упоминается нигде, кроме теста на отсутствие поля). Ломающие изменения — только в заметках к PR (спека §5).
- Перед каждым коммитом: `uv run ruff check --fix --show-fixes`, `uv run mypy src/`, `uv run ruff format`; затем `uv run ruff check .` без ошибок.
- Юнит-тесты: `uv run pytest tests/unit -q`. Интеграция: `uv run pytest tests/integration -q` — должна собираться; локально пропускается (нет Docker), в CI — Postgres 15/16 с hypopg. Сверять интеграционные тесты статически с реальным кодом.
- Коммиты: `type(scope): message`, повелительное наклонение, английский.
- **Никогда не запускать `git config` и `git stash`.** Не пушить.
- `_run_concurrently` в `domains/catalog/tables.py` не заменять на TaskGroup.
- Ветка: `claude/connection-options`, создать от вершины `claude/basic-role-guidance` перед Task 1: `git switch claude/basic-role-guidance && git switch -c claude/connection-options`.

---

### Task 1: `connect_options` — параметры libpq из URI и конфига

**Files:**
- Modify: `src/postgres_fastmcp/app/config/database.py`
- Modify: `src/postgres_fastmcp/app/config/__init__.py` (docstring `build_settings_from_cli`)
- Test: `tests/unit/app/test_settings.py`, `tests/unit/app/test_cli_settings.py`

**Interfaces:**
- Produces: `DatabaseConfig.connect_options: dict[str, str]` (default `{}`); `DatabaseConfig.extra_kwargs` removed. `DatabaseConfig.uri_fields(uri)` returns key `"connect_options"` only when the query string has parameters besides `sslmode`/`client_encoding` (repeated key → last value). `DatabaseConfig.database_uri` query string = `connect_options`, then `client_encoding`, then `sslmode`, percent-encoded (`quote_via=quote`).
- Validation (`ValueError` → pydantic `ValidationError`; `hide_input_in_errors=True` already hides values): keys `host`, `hostaddr`, `port`, `dbname`, `user`, `password`, `sslmode`, `client_encoding` → message `connect_options cannot set '<key>': use the database field '<field>'`; keys `sslpassword`, `passfile` → message `connect_options cannot set '<key>': the connection URI reaches logs and error messages, so it must not carry secrets or paths to them`.
- Facts checked: `DatabaseSettings` (pydantic-settings 2.14.2) parses `MCP_DATABASE_CONNECT_OPTIONS='{"application_name": "x"}'` into `dict[str, str]` (complex field → JSON); `parse_qs` decodes `%20` and keeps repeated keys as lists; `conninfo_to_dict` keeps `+` literally.

- [ ] **Step 1: Write the failing tests**

1. Append to `tests/unit/app/test_settings.py` (add `from psycopg.conninfo import conninfo_to_dict` to the imports at the top of the module):

```python
def test_uri_query_parameters_go_to_connect_options() -> None:
    """Параметры libpq из query string доходят до подключения; у sslmode и client_encoding свои поля."""
    fields = DatabaseConfig.uri_fields(
        "postgresql://u:p@h/d?sslmode=require&client_encoding=LATIN1&target_session_attrs=read-write"
        "&options=-c%20statement_timeout%3D5000&connect_timeout=5&connect_timeout=7"
    )
    assert fields["connect_options"] == {
        "target_session_attrs": "read-write",
        "options": "-c statement_timeout=5000",
        "connect_timeout": "7",
    }
    assert (fields["sslmode"], fields["client_encoding"]) == ("require", "LATIN1")


def test_uri_without_libpq_parameters_sets_no_connect_options() -> None:
    """URI без лишних параметров не стирает connect_options из config.json."""
    assert "connect_options" not in DatabaseConfig.uri_fields("postgresql://u:p@h/d?sslmode=require")


def test_database_uri_carries_connect_options_to_libpq() -> None:
    config = DatabaseConfig(
        **_CONNECTION,
        connect_options={
            "options": "-c statement_timeout=5000",
            "target_session_attrs": "read-write",
            "sslrootcert": "/etc/ssl/ca.pem",
        },
    )
    params = conninfo_to_dict(config.database_uri)
    assert params["options"] == "-c statement_timeout=5000"
    assert params["target_session_attrs"] == "read-write"
    assert params["sslrootcert"] == "/etc/ssl/ca.pem"
    assert params["client_encoding"] == "UTF8"


def test_connect_options_round_trip_through_from_uri() -> None:
    config = DatabaseConfig(**_CONNECTION, connect_options={"application_name": "mcp x&y=z"})
    assert DatabaseConfig.from_uri(config.database_uri).connect_options == {"application_name": "mcp x&y=z"}


@pytest.mark.parametrize(
    ("key", "field"),
    [
        ("host", "host"),
        ("hostaddr", "host"),
        ("port", "port"),
        ("dbname", "name"),
        ("user", "user"),
        ("password", "password"),
        ("sslmode", "sslmode"),
        ("client_encoding", "client_encoding"),
    ],
)
def test_connect_options_reject_keys_that_have_their_own_field(key: str, field: str) -> None:
    with pytest.raises(ValidationError, match=f"use the database field '{field}'") as exc_info:
        DatabaseConfig(**_CONNECTION, connect_options={key: _SECRET_PASSWORD})
    assert _SECRET_PASSWORD not in str(exc_info.value)


@pytest.mark.parametrize("key", ["sslpassword", "passfile"])
def test_connect_options_reject_secrets(key: str) -> None:
    with pytest.raises(ValidationError, match="must not carry secrets") as exc_info:
        DatabaseConfig(**_CONNECTION, connect_options={key: _SECRET_PASSWORD})
    assert _SECRET_PASSWORD not in str(exc_info.value)


def test_uri_password_parameter_is_rejected_without_leaking_it() -> None:
    with pytest.raises(ValidationError, match="use the database field 'password'") as exc_info:
        DatabaseConfig.from_uri(f"postgresql://u:p@h/d?password={_SECRET_PASSWORD}")
    assert _SECRET_PASSWORD not in str(exc_info.value)


def test_database_settings_reads_connect_options_json_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_DATABASE_NAME", "d")
    monkeypatch.setenv("MCP_DATABASE_CONNECT_OPTIONS", '{"application_name": "from-env", "connect_timeout": "5"}')
    assert DatabaseSettings().connect_options == {"application_name": "from-env", "connect_timeout": "5"}


def test_extra_kwargs_field_is_gone() -> None:
    assert "extra_kwargs" not in DatabaseConfig.model_fields
```

`_CONNECTION` (already in this module) is `{"host": "h", "user": "u", "password": "p", "name": "n"}`.

2. Append to `tests/unit/app/test_cli_settings.py`:

```python
def test_database_uri_parameters_replace_config_connect_options(tmp_path: Path) -> None:
    """Словарь из URI заменяет connect_options config.json целиком; URI без параметров его не трогает."""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"database": {"connect_options": {"application_name": "cfg", "connect_timeout": "3"}}}),
        encoding="utf-8",
    )

    kept = build_settings_from_cli(database_uri="postgresql://a:b@db/app", config_path=path)
    replaced = build_settings_from_cli(database_uri="postgresql://a:b@db/app?application_name=uri", config_path=path)

    assert kept.database.connect_options == {"application_name": "cfg", "connect_timeout": "3"}
    assert replaced.database.connect_options == {"application_name": "uri"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/test_settings.py tests/unit/app/test_cli_settings.py -q`
Expected: FAIL — `KeyError: 'connect_options'` in `test_uri_query_parameters_go_to_connect_options`; `ValidationError` "Extra inputs are not permitted" for `connect_options=` in `DatabaseConfig(...)`; `test_extra_kwargs_field_is_gone` fails; `test_uri_without_libpq_parameters_sets_no_connect_options` passes already.

- [ ] **Step 3: Implement in `app/config/database.py`**

1. Imports: `from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator`.
2. After `ERROR_DATABASE_URI_NOT_SET`:

```python
# Параметры libpq, у которых есть свои поля DatabaseConfig: в connect_options они дали бы второе значение.
_CONNECT_OPTION_FIELDS: dict[str, str] = {
    "host": "host",
    "hostaddr": "host",
    "port": "port",
    "dbname": "name",
    "user": "user",
    "password": "password",
    "sslmode": "sslmode",
    "client_encoding": "client_encoding",
}

# Секрет или путь к секретам: URI подключения попадает в логи и тексты ошибок.
_SECRET_CONNECT_OPTIONS = frozenset({"sslpassword", "passfile"})

# Параметры query string URI со своими полями: в connect_options не попадают.
_URI_FIELD_PARAMETERS = frozenset({"sslmode", "client_encoding"})
```

3. Class docstring: `URI формируется из компонентов: host, port, user, password, name.` → `URI формируется из компонентов: host, port, user, password, name; параметры libpq — из connect_options.`
4. Replace the `extra_kwargs` field with:

```python
    connect_options: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Параметры libpq в query string URI подключения (target_session_attrs, options, connect_timeout, "
            "sslrootcert, application_name и т.д.). Ключи со своими полями (host, hostaddr, port, dbname, user, "
            "password, sslmode, client_encoding) и секреты (sslpassword, passfile) запрещены; неизвестный "
            "параметр отклонит libpq при подключении."
        ),
    )
```

5. `max_inactive_connection_lifetime` description → `"Через сколько секунд простоя пул закрывает соединение сверх pool_min_size (max_idle пула)"`.
6. In `uri_fields`, docstring: replace `а из query string — sslmode и client_encoding.` with `а из query string — sslmode, client_encoding и остальные параметры libpq (connect_options; повтор ключа — последнее значение).`; before `return fields` add:

```python
        # Остальные параметры query string — параметры libpq (target_session_attrs, options, connect_timeout, ...).
        # Ключ connect_options — только если они есть: иначе URI стёр бы connect_options из config.json.
        connect_options = {key: values[-1] for key, values in qs.items() if key not in _URI_FIELD_PARAMETERS}
        if connect_options:
            fields["connect_options"] = connect_options
```

7. Validator (after `from_uri`, before `is_configured`):

```python
    @field_validator("connect_options")
    @classmethod
    def _check_connect_options(cls, value: dict[str, str]) -> dict[str, str]:
        """Параметры libpq без дублей полей конфига и без секретов; остальное проверит libpq при подключении."""
        for key in value:
            if key in _CONNECT_OPTION_FIELDS:
                msg = f"connect_options cannot set {key!r}: use the database field {_CONNECT_OPTION_FIELDS[key]!r}"
                raise ValueError(msg)
            if key in _SECRET_CONNECT_OPTIONS:
                msg = (
                    f"connect_options cannot set {key!r}: the connection URI reaches logs and error messages, "
                    "so it must not carry secrets or paths to them"
                )
                raise ValueError(msg)
        return value
```

8. `_connection_query_params`:

```python
    def _connection_query_params(self) -> dict[str, str]:
        """Параметры query string URI подключения: connect_options, client_encoding и sslmode."""
        params: dict[str, str] = {**self.connect_options, "client_encoding": self.client_encoding}
        if self.sslmode is not None:
            params["sslmode"] = self.sslmode
        return params
```

9. `database_uri`: docstring first line → `URI подключения к базе данных (connect_options, sslmode и client_encoding в query string).`; replace `query = urlencode(self._connection_query_params())` with:

```python
        # quote, а не quote_plus: libpq не декодирует '+' как пробел (options=-c statement_timeout=5000)
        query = urlencode(self._connection_query_params(), quote_via=quote)
```

10. In `src/postgres_fastmcp/app/config/__init__.py`, `build_settings_from_cli` docstring: `database_uri задаёт только поля подключения (host, port, user, password, name, sslmode и client_encoding, если они есть в URI)` → `database_uri задаёт только поля подключения (host, port, user, password, name, sslmode, client_encoding и connect_options — если они есть в URI; connect_options из URI заменяет словарь config.json целиком)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit -q`
Expected: PASS (existing credential and IPv6 round-trip tests keep passing: with `quote_via=quote` `urlencode` passes `safe=''`, so the query encodes like before except that a space becomes `%20` instead of `+`).

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add src/postgres_fastmcp/app/config tests/unit/app/test_settings.py tests/unit/app/test_cli_settings.py
git commit -m "feat(config): pass libpq parameters from the URI and config to the connection"
```

---

### Task 2: пул — `max_idle` и сброс hypopg на том же соединении

**Files:**
- Modify: `src/postgres_fastmcp/postgres/connection.py` (`DbConnPool`)
- Modify: `src/postgres_fastmcp/postgres/driver.py` (`SqlExecutor._execute_with_connection`)
- Modify: `src/postgres_fastmcp/domains/db_access.py` (`DatabaseConfigPort`, `DbAccessService.__init__`)
- Test: `tests/unit/postgres/test_db_conn_pool.py`, `tests/unit/postgres/test_sql_executor.py`, `tests/unit/domains/test_db_access.py`

**Interfaces:**
- Produces: `DbConnPool(connection_url: str | None = None, min_size: int = 1, max_size: int = 5, max_idle: float = 600.0)`; attribute `max_idle`; `DbConnPool.mark_hypopg_used(connection: AsyncConnection[Any]) -> None`; private `DbConnPool._reset_connection(connection)` passed as `AsyncConnectionPool(reset=...)` together with `max_idle=self.max_idle`.
- Produces: `DatabaseConfigPort.max_inactive_connection_lifetime: int`; `DbAccessService` builds `DbConnPool(..., max_idle=config.max_inactive_connection_lifetime)`.
- Behavior: `SqlExecutor._execute_with_connection` marks the connection **before** executing when `self.conn` is a `DbConnPool` and `"hypopg_create_index"` is in `query.lower()` — so an index created by a batch that fails later (`SELECT hypopg_create_index(...); EXPLAIN <bad>`) is reset too. Covers `explain_query` (`ExplainPlanBuilder` sends `SELECT hypopg_reset();SELECT hypopg_create_index(...);EXPLAIN ...` in one string) and a direct agent call.
- Facts checked in `psycopg_pool/pool_async.py` 3.3.1: `reset` is awaited after the pool rolls back an `INTRANS` connection; any exception from it is logged and the connection is closed; `max_idle` default is `600.0`.

- [ ] **Step 1: Write the failing tests**

1. `tests/unit/postgres/test_db_conn_pool.py` (add `import logging`, `from collections.abc import Awaitable, Callable` and `from psycopg.errors import UndefinedFunction` to the imports), append:

```python
async def _reset_callback(pool_mgr: DbConnPool) -> Callable[[MagicMock], Awaitable[None]]:
    """reset-callback, который DbConnPool передаёт в AsyncConnectionPool."""
    with patch("postgres_fastmcp.postgres.connection.AsyncConnectionPool") as mock_pool_cls:
        mock_pool_cls.return_value = _make_mock_pool()
        await pool_mgr.pool_connect()
    return mock_pool_cls.call_args.kwargs["reset"]


def _returned_connection(*, autocommit: bool = True) -> MagicMock:
    connection = MagicMock()
    connection.autocommit = autocommit
    connection.set_autocommit = AsyncMock()
    connection.execute = AsyncMock()
    return connection


class TestDbConnPoolOptions:
    """max_idle и reset-callback доходят до AsyncConnectionPool."""

    @patch("postgres_fastmcp.postgres.connection.AsyncConnectionPool")
    async def test_max_idle_and_reset_reach_the_pool(self, mock_pool_cls: MagicMock) -> None:
        mock_pool_cls.return_value = _make_mock_pool()
        pool_mgr = DbConnPool(connection_url="postgresql://localhost/test", max_idle=42)

        await pool_mgr.pool_connect()

        kwargs = mock_pool_cls.call_args.kwargs
        assert kwargs["max_idle"] == 42
        assert kwargs["reset"] == pool_mgr._reset_connection


class TestHypopgReset:
    """Гипотетические индексы живут в сессии: помеченное соединение сбрасывается при возврате в пул."""

    async def test_marked_connection_gets_hypopg_reset_once(self) -> None:
        pool_mgr = DbConnPool(connection_url="postgresql://localhost/test")
        reset = await _reset_callback(pool_mgr)
        connection = _returned_connection()
        pool_mgr.mark_hypopg_used(connection)

        await reset(connection)
        await reset(connection)

        connection.execute.assert_awaited_once_with("SELECT hypopg_reset()")
        connection.set_autocommit.assert_not_awaited()

    async def test_unmarked_connection_is_left_alone(self) -> None:
        pool_mgr = DbConnPool(connection_url="postgresql://localhost/test")
        reset = await _reset_callback(pool_mgr)
        connection = _returned_connection()

        await reset(connection)

        connection.execute.assert_not_awaited()
        connection.set_autocommit.assert_not_awaited()

    async def test_reset_switches_to_autocommit_first(self) -> None:
        """Pool требует от reset вернуть соединение в IDLE: hypopg_reset() — вне транзакции."""
        pool_mgr = DbConnPool(connection_url="postgresql://localhost/test")
        reset = await _reset_callback(pool_mgr)
        connection = _returned_connection(autocommit=False)
        pool_mgr.mark_hypopg_used(connection)

        await reset(connection)

        connection.set_autocommit.assert_awaited_once()
        assert connection.set_autocommit.await_args.args == (True,)
        connection.execute.assert_awaited_once_with("SELECT hypopg_reset()")

    async def test_reset_failure_is_logged_and_raised(self, caplog: pytest.LogCaptureFixture) -> None:
        """Состояние hypopg неизвестно: ошибка поднимается, и psycopg_pool закрывает соединение."""
        pool_mgr = DbConnPool(connection_url="postgresql://localhost/test")
        reset = await _reset_callback(pool_mgr)
        connection = _returned_connection()
        connection.execute = AsyncMock(side_effect=UndefinedFunction("function hypopg_reset() does not exist"))
        pool_mgr.mark_hypopg_used(connection)

        with caplog.at_level(logging.WARNING, logger="postgres_fastmcp.postgres.connection"):
            with pytest.raises(UndefinedFunction):
                await reset(connection)

        assert any("Failed to reset hypothetical indexes" in r.getMessage() for r in caplog.records)
        await reset(connection)
        connection.execute.assert_awaited_once()
```

2. `tests/unit/postgres/test_sql_executor.py`, append at the end of the module (after `_executor_on` and `TestSqlExecutorExecuteStatement`):

```python
def _pooled_executor(cursor: _FakeCursor) -> tuple[SqlExecutor, MagicMock, MagicMock]:
    """SqlExecutor на пуле (DbConnPool) с одним соединением, которое отдаёт cursor."""
    connection = MagicMock()
    connection.set_autocommit = AsyncMock()
    connection.cursor.return_value = cursor
    checkout = MagicMock()
    checkout.__aenter__ = AsyncMock(return_value=connection)
    checkout.__aexit__ = AsyncMock(return_value=None)
    inner = MagicMock()
    inner.connection.return_value = checkout
    pool = MagicMock(spec=DbConnPool)
    pool.pool_connect = AsyncMock(return_value=inner)
    return SqlExecutor(conn=pool), pool, connection


class TestSqlExecutorMarksHypopgConnections:
    """Соединение с гипотетическими индексами помечается для сброса при возврате в пул."""

    async def test_hypothetical_index_marks_the_pooled_connection(self) -> None:
        cursor = _FakeCursor(
            ("SELECT 1", 1, [{"hypopg_reset": None}]),
            ("SELECT 1", 1, [{"hypopg_create_index": 1}]),
            ("EXPLAIN", -1, [{"QUERY PLAN": []}]),
        )
        executor, pool, connection = _pooled_executor(cursor)

        await executor.execute(
            "SELECT hypopg_reset();SELECT HYPOPG_CREATE_INDEX('CREATE INDEX ON t (a)');EXPLAIN (FORMAT JSON) SELECT 1"
        )

        pool.mark_hypopg_used.assert_called_once_with(connection)

    async def test_plain_query_does_not_mark(self) -> None:
        executor, pool, _ = _pooled_executor(_FakeCursor(("SELECT 1", 1, [{"a": 1}])))

        await executor.execute("SELECT 1 AS a")

        pool.mark_hypopg_used.assert_not_called()

    async def test_connection_is_marked_even_if_the_statement_fails(self) -> None:
        """Индекс мог создаться до ошибки в том же пакете: пометка — до выполнения."""
        executor, pool, connection = _pooled_executor(_FakeCursor())

        with pytest.raises(IndexError):
            await executor.execute("SELECT hypopg_create_index('CREATE INDEX ON t (a)')")

        pool.mark_hypopg_used.assert_called_once_with(connection)
```

(`_FakeCursor()` without results raises `IndexError` on the user query — a stand-in for a failing statement; `ROLLBACK` still runs.)

3. `tests/unit/domains/test_db_access.py`, append:

```python
def test_inactive_connection_lifetime_becomes_pool_max_idle() -> None:
    assert _service(max_inactive_connection_lifetime=42)._pool.max_idle == 42
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/postgres/test_db_conn_pool.py tests/unit/postgres/test_sql_executor.py tests/unit/domains/test_db_access.py -q`
Expected: FAIL — `TypeError: DbConnPool.__init__() got an unexpected keyword argument 'max_idle'`; `KeyError: 'reset'`; `AttributeError: Mock object has no attribute 'mark_hypopg_used'` (spec mock of `DbConnPool`); `AttributeError: 'DbConnPool' object has no attribute 'max_idle'`.

- [ ] **Step 3: `DbConnPool`**

In `src/postgres_fastmcp/postgres/connection.py`:
1. Imports: add `import weakref` and `from typing import Any`.
2. After `logger = ...`:

```python
# psycopg_pool: через сколько секунд простоя закрывается соединение сверх min_size
DEFAULT_MAX_IDLE_SECONDS = 600.0
```

3. `__init__` signature gains `max_idle: float = DEFAULT_MAX_IDLE_SECONDS` (last parameter); docstring Args: `max_idle: Через сколько секунд простоя пул закрывает соединение сверх min_size.`; body after `self.max_size = max_size`:

```python
        self.max_idle = max_idle
        # Соединения, на которых создавались гипотетические индексы hypopg: индексы живут в памяти сессии
        # и переживают ROLLBACK, поэтому сбрасываются при возврате соединения в пул (reset-callback).
        self._hypopg_connections: weakref.WeakSet[AsyncConnection[Any]] = weakref.WeakSet()
```

4. In `_open_pool`, the `AsyncConnectionPool(...)` call gains `max_idle=self.max_idle,` and `reset=self._reset_connection,` (after `max_size=self.max_size,`).
5. New methods after `mark_invalid`:

```python
    def mark_hypopg_used(self, connection: AsyncConnection[Any]) -> None:
        """Пометить соединение: при возврате в пул на нём выполнится hypopg_reset().

        Args:
            connection: Соединение пула, на котором выполнялся hypopg_create_index.
        """
        self._hypopg_connections.add(connection)

    async def _reset_connection(self, connection: AsyncConnection[Any]) -> None:
        """reset-callback пула: сбросить гипотетические индексы на помеченном соединении.

        Непомеченное соединение не трогается (без лишнего запроса). Ошибка сброса поднимается дальше:
        psycopg_pool закрывает такое соединение, состояние hypopg на нём неизвестно.
        """
        if connection not in self._hypopg_connections:
            return
        self._hypopg_connections.discard(connection)
        try:
            # Пул требует вернуть соединение в IDLE: hypopg_reset() выполняется вне транзакции.
            if not connection.autocommit:
                await connection.set_autocommit(True)
            await connection.execute("SELECT hypopg_reset()")
        except Exception as e:
            logger.warning("Failed to reset hypothetical indexes on a returned connection: %s", e)
            raise
```

- [ ] **Step 4: `SqlExecutor` marks the connection**

In `src/postgres_fastmcp/postgres/driver.py`, `_execute_with_connection`: docstring gains a paragraph `SQL с hypopg_create_index помечает соединение пула до выполнения: пул сбросит гипотетические индексы при возврате соединения, даже если пакет упал после их создания.`; first lines of the body, before `async with connection.cursor(...)`:

```python
        if isinstance(self.conn, DbConnPool) and "hypopg_create_index" in str(query).lower():
            self.conn.mark_hypopg_used(connection)
```

- [ ] **Step 5: `DbAccessService` passes `max_idle`**

In `src/postgres_fastmcp/domains/db_access.py`:
- `DatabaseConfigPort`: add `max_inactive_connection_lifetime: int` after `pool_max_size: int`.
- `DbAccessService.__init__`: the `DbConnPool(...)` call gains `max_idle=config.max_inactive_connection_lifetime,`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit -q`
Expected: PASS.

- [ ] **Step 7: Lint, type-check, commit**

```bash
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add src/postgres_fastmcp/postgres/connection.py src/postgres_fastmcp/postgres/driver.py src/postgres_fastmcp/domains/db_access.py tests/unit/postgres/test_db_conn_pool.py tests/unit/postgres/test_sql_executor.py tests/unit/domains/test_db_access.py
git commit -m "fix(postgres): reset hypothetical indexes when a connection returns to the pool"
```

---

### Task 3: интеграция, README, env.example, спеки

**Files:**
- Test: `tests/integration/test_connection_options.py` (create)
- Modify: `README.md`, `env.example`
- Modify: `docs/superpowers/specs/2026-09-28-basic-followups-design.md`, `docs/superpowers/specs/2026-09-28-basic-confinement-design.md`

**Interfaces:**
- Consumes: `DatabaseConfig(connect_options=..., pool_max_size=...)` (Task 1), `DbConnPool` reset (Task 2), `ExplainService(db).explain(sql, hypothetical_indexes=[{"table": ..., "columns": [...]}])`, `DbAccessService`, `EffectiveAccess`.

- [ ] **Step 1: Integration tests**

Create `tests/integration/test_connection_options.py`:

```python
# mypy: ignore-errors
"""Слой подключения на живом Postgres: параметры libpq доходят до сессии, hypopg сбрасывается на том же соединении."""

import logging

import pytest

from postgres_fastmcp.access import EffectiveAccess
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.domains.db_access import DbAccessService
from postgres_fastmcp.domains.explain.service import ExplainService
from postgres_fastmcp.shared.enums import AccessMode


logger = logging.getLogger(__name__)

_FULL_WRITE = EffectiveAccess(AccessMode.FULL, write_mode=True)


@pytest.mark.asyncio
async def test_connect_options_reach_the_session(test_postgres_connection_string: tuple[str, str]) -> None:
    connection_string, _ = test_postgres_connection_string
    config = DatabaseConfig.from_uri(
        connection_string,
        access_mode=AccessMode.FULL,
        write_mode=True,
        connect_options={"application_name": "mcp-test"},
    )
    service = DbAccessService(config)
    try:
        rows = await service.view(_FULL_WRITE).sql_driver.execute(
            "SELECT current_setting('application_name') AS app", readonly=True
        )
    finally:
        await service.close()

    assert rows[0].cells["app"] == "mcp-test"


@pytest.mark.asyncio
async def test_uri_query_parameters_reach_the_session(test_postgres_connection_string: tuple[str, str]) -> None:
    """Параметр options с пробелом доходит до сервера как есть: пробел кодируется %20, а не '+'."""
    connection_string, _ = test_postgres_connection_string
    uri = f"{connection_string}?application_name=mcp-uri&options=-c%20statement_timeout%3D4321"
    service = DbAccessService(DatabaseConfig.from_uri(uri, access_mode=AccessMode.FULL, write_mode=True))
    try:
        rows = await service.view(_FULL_WRITE).sql_driver.execute(
            "SELECT current_setting('application_name') AS app, current_setting('statement_timeout') AS st",
            readonly=True,
        )
    finally:
        await service.close()

    assert (rows[0].cells["app"], rows[0].cells["st"]) == ("mcp-uri", "4321ms")


@pytest.mark.asyncio
async def test_hypothetical_indexes_are_reset_when_the_connection_returns(
    test_postgres_connection_string: tuple[str, str],
) -> None:
    """Пул из одного соединения: следующий запрос получает то же соединение, и гипотетических индексов на нём нет."""
    connection_string, _ = test_postgres_connection_string
    config = DatabaseConfig.from_uri(
        connection_string, access_mode=AccessMode.FULL, write_mode=True, pool_min_size=1, pool_max_size=1
    )
    service = DbAccessService(config)
    db = service.view(_FULL_WRITE)
    try:
        await db.sql_driver.execute("CREATE TABLE IF NOT EXISTS hypo_reset_t (id int, v text)", readonly=False)
        try:
            await db.sql_driver.execute("CREATE EXTENSION IF NOT EXISTS hypopg", readonly=False)
        except Exception as e:
            logger.warning("hypopg not available: %s", e)
            pytest.skip("hypopg extension is not available")

        await ExplainService(db).explain(
            "SELECT * FROM hypo_reset_t WHERE v = 'x'",
            hypothetical_indexes=[{"table": "hypo_reset_t", "columns": ["v"]}],
        )
        after_explain = await db.sql_driver.execute("SELECT count(*) AS n FROM hypopg_list_indexes", readonly=True)

        await db.sql_driver.execute("SELECT hypopg_create_index('CREATE INDEX ON hypo_reset_t (id)')", readonly=True)
        after_direct_call = await db.sql_driver.execute(
            "SELECT count(*) AS n FROM hypopg_list_indexes", readonly=True
        )
    finally:
        await service.close()

    assert after_explain[0].cells["n"] == 0
    assert after_direct_call[0].cells["n"] == 0
```

Check statically and record in the report: `test_postgres_connection_string` has no query string (`tests/utils.py`: `postgresql://postgres:<pw>@localhost:<port>/<db>`), so appending `?...` is valid; `from_uri` keeps `application_name`/`options` in `connect_options`; with `pool_max_size=1` the second `execute` waits for the first connection to come back through the pool's `ReturnConnection` worker (which runs `reset`), so it sees the same, reset connection; `hypopg_list_indexes` is a view in the extension schema (`public`), reachable by the unrestricted full executor.

- [ ] **Step 2: README**

1. In `#### 2. Конфигурационный файл`, the line `Подключение к БД задаётся полями (...). Опционально: \`access_mode\`, ...` → append `connect_options`, `max_inactive_connection_lifetime` to the optional list and add a sentence after it:

```markdown
`connect_options` — параметры libpq, которые попадают в query string URI подключения: `{"target_session_attrs": "read-write", "options": "-c statement_timeout=5000", "sslrootcert": "/etc/ssl/ca.pem", "application_name": "mcp"}`. Значения — строки. Ключи, у которых есть свои поля (`host`, `hostaddr`, `port`, `dbname`, `user`, `password`, `sslmode`, `client_encoding`), и секреты (`sslpassword`, `passfile`) отклоняются: URI подключения попадает в логи и тексты ошибок. В env — JSON-объект: `MCP_DATABASE_CONNECT_OPTIONS='{"application_name": "mcp"}'`. `max_inactive_connection_lifetime` (секунды, по умолчанию 300) — через сколько простоя пул закрывает соединения сверх `pool_min_size`.
```

2. In `Особенности --database-uri`, first bullet → `- Задаёт только то, что есть в URI: \`host\`, \`port\`, \`user\`, \`password\`, \`name\`, \`sslmode\` и \`client_encoding\` из query string, а остальные параметры query string (\`target_session_attrs\`, \`options\`, \`connect_timeout\`, \`sslrootcert\`, …) — как \`connect_options\`; словарь из URI заменяет \`connect_options\` из \`config.json\` целиком. Остальные поля секции \`database\` (например \`table_prefix\`) берутся из \`config.json\`/env как обычно.`
3. In `### Управление жизненным циклом`, add a bullet after the pool-opening bullet:

```markdown
- Гипотетические индексы hypopg (`explain_query` с `hypothetical_indexes`, `hypopg_create_index` в `execute_sql`) сбрасываются, когда соединение возвращается в пул: следующий запрос на том же соединении их не видит
```

- [ ] **Step 3: env.example**

In `env.example`, database section: after `# MCP_DATABASE_CLIENT_ENCODING=UTF8` and its comment line add:

```bash
# MCP_DATABASE_CONNECT_OPTIONS={}
#   Параметры libpq (JSON-объект строк) в query string URI: {"target_session_attrs": "read-write", "connect_timeout": "5"}.
#   host, port, user, password, dbname, sslmode, client_encoding, sslpassword, passfile — запрещены.
```

and after `# MCP_DATABASE_MAX_INACTIVE_CONNECTION_LIFETIME=300` add `#   Секунды простоя, после которых пул закрывает соединение сверх POOL_MIN_SIZE.`

- [ ] **Step 4: Specs**

1. `docs/superpowers/specs/2026-09-28-basic-followups-design.md`:
- status line → `Статус: согласовано (ответа на вопросы по дизайну не было — приняты рекомендуемые варианты); PR 1–2 реализованы.`
- §3.1, after the `uri_fields` bullet add: `- \`database_uri\` кодирует query string через \`quote\` (пробел — \`%20\`): \`urlencode\` по умолчанию даёт \`+\`, который libpq не декодирует. \`connect_options\` попадает в поля URI, только если в query string есть параметры кроме \`sslmode\`/\`client_encoding\`.`
- §3.3, last-but-one bullet (about the reset error) → append: `При заданном \`reset\` psycopg_pool возвращает соединения в пул рабочей задачей; без hypopg помеченное соединение закрывается пулом и заменяется новым.`
2. `docs/superpowers/specs/2026-09-28-basic-confinement-design.md`, §6: replace the bullet starting `- (i) Очистка гипотетических индексов на том же соединении после \`EXPLAIN\` отложена:` with:

```markdown
- (/) Гипотетические индексы сбрасываются на том же соединении: `SqlExecutor` помечает соединение пула, на котором выполнялся `hypopg_create_index`, и `reset`-хук пула вызывает на нём `hypopg_reset()` при возврате (спека `2026-09-28-basic-followups-design.md`, §3.3).
```

- [ ] **Step 5: Full verification, commit**

```bash
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add tests/integration/test_connection_options.py README.md env.example docs/superpowers/specs/2026-09-28-basic-followups-design.md docs/superpowers/specs/2026-09-28-basic-confinement-design.md
git commit -m "docs(config): describe connect_options and the pool-level hypopg reset"
```

---

## Self-review

- Spec §3.1 (`extra_kwargs` removed, `connect_options`, `uri_fields` excluding `sslmode`/`client_encoding`, last value wins, forbidden keys with a field hint, secrets, env JSON, URI replaces config dict) → Task 1. §3.2 `max_idle` → Task 2 Steps 3, 5. §3.3 (`WeakSet`, `mark_hypopg_used`, marking in `_execute_with_connection`, `reset` only for marked, autocommit, WARNING + re-raise, confinement §6 marked done) → Task 2 Steps 3–4, Task 3 Step 4. §3.4 unit tests → Task 1 Step 1, Task 2 Step 1; integration (`application_name` in full, `hypopg_list_indexes` empty with `max_size=1`) → Task 3 Step 1. §5 PR 2 bullet already matches the plan.
- Names across tasks: `connect_options`, `DbConnPool(max_idle=...)`, `DbConnPool.max_idle`, `mark_hypopg_used`, `_reset_connection`, `DatabaseConfigPort.max_inactive_connection_lifetime` — consistent.
