# Права роли для basic — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Администратор видит в README, какие права выдать роли для `access_mode=basic`, а сервер при старте basic пишет одну строку `WARNING`, если роль может больше, чем `public`.

**Architecture:** Четыре новых шаблона каталога (`postgres/catalog.py`, в `CATALOG_QUERIES`) выполняются через канал сервера (`CatalogSqlExecutor`). Логика находок — модуль `domains/role_check.py`. `PostgresProvider.lifespan` запускает проверку фоновой задачей, если basic достижим (потолок basic, свой `access_resolver` или `access_policy.enforced`), и отменяет её при остановке. Спека: `docs/superpowers/specs/2026-09-28-basic-followups-design.md`, §2 (PR 1).

**Tech Stack:** Python 3.12, uv, FastMCP 4.0.10, psycopg 3.3 + psycopg_pool, pytest (asyncio auto).

## Spec corrections

- `basic_role_findings` возвращает `RoleFindings(role, findings)`, а не `list[str]`: текст предупреждения называет роль (`Database role '<role>' ...`), и имя приходит тем же запросом `QUERY_ROLE_ATTRIBUTES`.
- Для суперпользователя находка одна — `superuser`: `pg_has_role` и `has_*_privilege` у него всегда true, остальные запросы ничего бы не добавили, кроме шума (перечень всех схем и предопределённых ролей). Интеграционный тест спеки (`superuser` в находках) это сохраняет.
- Следствие фоновой проверки: в basic пул открывается при старте, а не при первом запросе агента. README («Управление жизненным циклом») и §5 спеки это фиксируют.

## Global Constraints

- Всё, что видит агент или внешняя система (ошибки, логи, коммиты, описания тулов), — на английском; docstring и комментарии — по-русски. README — по-русски.
- Нет `from __future__ import annotations`.
- Ломающие изменения разрешены; никаких шимов, deprecation-предупреждений и упоминаний старого поведения в коде. Ломающие изменения — только в заметках к PR (спека §5).
- Перед каждым коммитом: `uv run ruff check --fix --show-fixes`, `uv run mypy src/`, `uv run ruff format`; затем `uv run ruff check .` без ошибок.
- Юнит-тесты: `uv run pytest tests/unit -q`. Интеграция: `uv run pytest tests/integration -q` — должна собираться; локально пропускается (нет Docker), в CI — Postgres 15/16 с hypopg. Сверять интеграционные тесты статически с реальным кодом.
- Коммиты: `type(scope): message`, повелительное наклонение, английский.
- **Никогда не запускать `git config` и `git stash`.** Не пушить.
- `_run_concurrently` в `domains/catalog/tables.py` не заменять на TaskGroup.
- Ветка: `claude/basic-role-guidance` (от `main` `9396e6d` + коммит спеки `6b1eeef` и коммит планов).

---

### Task 1: шаблоны каталога и находки по роли

**Files:**
- Modify: `src/postgres_fastmcp/postgres/catalog.py`
- Create: `src/postgres_fastmcp/domains/role_check.py`
- Test: `tests/unit/domains/test_role_check.py` (create)
- Test (runs unchanged, now also over the new templates): `tests/unit/postgres/test_catalog_driver.py`

**Interfaces:**
- Produces (in `postgres_fastmcp.postgres.catalog`, all four in `CATALOG_QUERIES`): `QUERY_ROLE_ATTRIBUTES`, `QUERY_ROLE_PREDEFINED_MEMBERSHIPS`, `QUERY_ROLE_FOREIGN_SCHEMAS`, `QUERY_ROLE_UNPREFIXED_TABLES` (one `{}` placeholder — the prefix, a `str`).
- Produces (in `postgres_fastmcp.domains.role_check`):
  - `MAX_LISTED_SCHEMAS: int = 10`
  - `@dataclass(frozen=True, slots=True) class RoleFindings: role: str; findings: list[str]`
  - `async def basic_role_findings(catalog: QueryExecutorPort, table_prefix: str | None) -> RoleFindings`
  - `async def warn_about_basic_role(catalog: QueryExecutorPort, table_prefix: str | None) -> None`
- Facts checked against the installed code: all four templates pass `QueryValidator(read_only=True)` (the catalog executor's validator) — `pg_has_role`, `has_schema_privilege`, `has_table_privilege`, `starts_with`, `lower`, `count` are in `ALLOWED_FUNCTIONS`; every relation is qualified with `pg_catalog`, so `test_catalog_queries_qualify_every_relation` passes. Templates without parameters are sent as-is (no `SQL.format`), so `'pg\_%'` reaches Postgres unchanged; `QUERY_ROLE_UNPREFIXED_TABLES` has no `%`/braces besides `{}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/domains/test_role_check.py`:

```python
"""Тесты проверки прав роли при старте basic: находки по шаблонам каталога, текст WARNING, пропуск при ошибке."""

import logging
from typing import Any

import pytest
from psycopg import OperationalError

from postgres_fastmcp.domains.role_check import RoleFindings, basic_role_findings, warn_about_basic_role
from postgres_fastmcp.postgres.catalog import (
    QUERY_ROLE_ATTRIBUTES,
    QUERY_ROLE_FOREIGN_SCHEMAS,
    QUERY_ROLE_PREDEFINED_MEMBERSHIPS,
    QUERY_ROLE_UNPREFIXED_TABLES,
)
from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.shared.errors import ConnectionFailedError, QueryTimeoutError


_LOGGER = "postgres_fastmcp.domains.role_check"


class _Catalog:
    """Исполнитель каталога в миниатюре: строки по тексту шаблона, журнал вызовов."""

    def __init__(self, answers: dict[str, list[dict[str, Any]]]) -> None:
        self._answers = answers
        self.calls: list[tuple[str, list[Any] | None]] = []

    async def execute(
        self, query: str, params: list[Any] | None = None, *, readonly: bool = True
    ) -> list[RowResult] | None:
        self.calls.append((query, params))
        return [RowResult(cells=row) for row in self._answers.get(query, [])]


class _FailingCatalog:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def execute(
        self, query: str, params: list[Any] | None = None, *, readonly: bool = True
    ) -> list[RowResult] | None:
        raise self._error


def _role(*, superuser: bool = False, bypassrls: bool = False) -> dict[str, list[dict[str, Any]]]:
    return {QUERY_ROLE_ATTRIBUTES: [{"role_name": "mcp", "rolsuper": superuser, "rolbypassrls": bypassrls}]}


async def test_role_limited_to_public_has_no_findings() -> None:
    catalog = _Catalog({**_role(), QUERY_ROLE_UNPREFIXED_TABLES: [{"unprefixed": 0}]})

    result = await basic_role_findings(catalog, "app_")

    assert result == RoleFindings(role="mcp", findings=[])
    assert [query for query, _ in catalog.calls] == [
        QUERY_ROLE_ATTRIBUTES,
        QUERY_ROLE_PREDEFINED_MEMBERSHIPS,
        QUERY_ROLE_FOREIGN_SCHEMAS,
        QUERY_ROLE_UNPREFIXED_TABLES,
    ]
    assert catalog.calls[-1][1] == ["app_"]


async def test_superuser_is_the_only_finding() -> None:
    """Суперпользователю has_*_privilege всегда true: остальные запросы ничего не добавили бы."""
    catalog = _Catalog(_role(superuser=True, bypassrls=True))

    result = await basic_role_findings(catalog, "app_")

    assert result == RoleFindings(role="mcp", findings=["superuser"])
    assert [query for query, _ in catalog.calls] == [QUERY_ROLE_ATTRIBUTES]


async def test_every_kind_of_finding_in_order() -> None:
    catalog = _Catalog(
        {
            **_role(bypassrls=True),
            QUERY_ROLE_PREDEFINED_MEMBERSHIPS: [{"rolname": "pg_monitor"}, {"rolname": "pg_read_all_data"}],
            QUERY_ROLE_FOREIGN_SCHEMAS: [{"nspname": "billing"}, {"nspname": "secret"}],
            QUERY_ROLE_UNPREFIXED_TABLES: [{"unprefixed": 3}],
        }
    )

    result = await basic_role_findings(catalog, "app_")

    assert result.findings == [
        "BYPASSRLS",
        "member of pg_monitor, pg_read_all_data",
        "USAGE on schemas: billing, secret",
        "SELECT on 3 public tables without prefix 'app_'",
    ]


async def test_one_unprefixed_table_is_singular() -> None:
    catalog = _Catalog({**_role(), QUERY_ROLE_UNPREFIXED_TABLES: [{"unprefixed": 1}]})

    result = await basic_role_findings(catalog, "app_")

    assert result.findings == ["SELECT on 1 public table without prefix 'app_'"]


async def test_schema_list_is_capped() -> None:
    schemas = [{"nspname": f"s{i:02d}"} for i in range(12)]
    catalog = _Catalog({**_role(), QUERY_ROLE_FOREIGN_SCHEMAS: schemas})

    result = await basic_role_findings(catalog, None)

    listed = ", ".join(f"s{i:02d}" for i in range(10))
    assert result.findings == [f"USAGE on schemas: {listed}, …"]


async def test_without_prefix_unprefixed_tables_are_not_queried() -> None:
    catalog = _Catalog(_role())

    await basic_role_findings(catalog, None)

    assert QUERY_ROLE_UNPREFIXED_TABLES not in [query for query, _ in catalog.calls]


async def test_warning_names_the_role_and_every_finding(caplog: pytest.LogCaptureFixture) -> None:
    catalog = _Catalog(
        {
            **_role(),
            QUERY_ROLE_PREDEFINED_MEMBERSHIPS: [{"rolname": "pg_read_all_data"}],
            QUERY_ROLE_FOREIGN_SCHEMAS: [{"nspname": "a"}, {"nspname": "b"}],
            QUERY_ROLE_UNPREFIXED_TABLES: [{"unprefixed": 3}],
        }
    )

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        await warn_about_basic_role(catalog, "app_")

    [record] = [r for r in caplog.records if r.name == _LOGGER]
    assert record.levelname == "WARNING"
    assert record.getMessage() == (
        "Database role 'mcp' has privileges beyond basic mode: member of pg_read_all_data; "
        "USAGE on schemas: a, b; SELECT on 3 public tables without prefix 'app_'. "
        "In basic mode the SQL validator is then the only barrier; grant the role access to 'public' only "
        "(see README)."
    )


async def test_no_findings_no_log(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        await warn_about_basic_role(_Catalog(_role()), None)

    assert [r for r in caplog.records if r.name == _LOGGER] == []


@pytest.mark.parametrize(
    "error",
    [
        OperationalError("connection to server at postgresql://u:hunter2@db/d failed"),
        ConnectionFailedError("postgresql://u:****@db/d refused"),
        QueryTimeoutError(30),
        TimeoutError(),
    ],
)
async def test_database_error_skips_the_check_with_info(
    caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        await warn_about_basic_role(_FailingCatalog(error), "app_")

    [record] = [r for r in caplog.records if r.name == _LOGGER]
    assert record.levelname == "INFO"
    assert record.getMessage().startswith("Basic role check skipped: ")
    assert "hunter2" not in record.getMessage()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/domains/test_role_check.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'postgres_fastmcp.domains.role_check'` (collection error).

- [ ] **Step 3: Add the catalog templates**

In `src/postgres_fastmcp/postgres/catalog.py`, after `QUERY_SERVER_VERSION`:

```python
# Права роли подключения для предупреждения при старте basic (domains/role_check.py).
QUERY_ROLE_ATTRIBUTES = """
SELECT current_user AS role_name, r.rolsuper, r.rolbypassrls
FROM pg_catalog.pg_roles AS r
WHERE r.rolname = current_user
"""

QUERY_ROLE_PREDEFINED_MEMBERSHIPS = """
SELECT r.rolname
FROM pg_catalog.pg_roles AS r
WHERE r.rolname IN ('pg_read_all_data', 'pg_write_all_data', 'pg_read_all_settings', 'pg_read_all_stats',
                    'pg_read_server_files', 'pg_write_server_files', 'pg_execute_server_program', 'pg_monitor')
  AND pg_catalog.pg_has_role(current_user, r.oid, 'MEMBER')
ORDER BY r.rolname
"""

QUERY_ROLE_FOREIGN_SCHEMAS = r"""
SELECT n.nspname
FROM pg_catalog.pg_namespace AS n
WHERE n.nspname NOT IN ('public', 'information_schema')
  AND n.nspname NOT LIKE 'pg\_%'
  AND pg_catalog.has_schema_privilege(n.oid, 'USAGE')
ORDER BY n.nspname
"""

# Параметр — table_prefix (str): сравнение без учёта регистра, как у валидатора.
QUERY_ROLE_UNPREFIXED_TABLES = """
SELECT count(*) AS unprefixed
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind IN ('r', 'v', 'm', 'p', 'f')
  AND NOT starts_with(lower(c.relname), lower({}))
  AND pg_catalog.has_table_privilege(c.oid, 'SELECT')
"""
```

and add the four names to `CATALOG_QUERIES` (after `QUERY_SERVER_VERSION`):

```python
        QUERY_ROLE_ATTRIBUTES,
        QUERY_ROLE_PREDEFINED_MEMBERSHIPS,
        QUERY_ROLE_FOREIGN_SCHEMAS,
        QUERY_ROLE_UNPREFIXED_TABLES,
```

- [ ] **Step 4: Write `domains/role_check.py`**

Create `src/postgres_fastmcp/domains/role_check.py`:

```python
"""Проверка прав роли БД при старте basic: basic — защита в глубину, граница доступа — права роли.

Запросы идут через канал сервера (catalog_driver), находки — английские фразы для одной строки WARNING.
"""

from dataclasses import dataclass

from psycopg import Error as PsycopgError

from postgres_fastmcp.postgres.catalog import (
    QUERY_ROLE_ATTRIBUTES,
    QUERY_ROLE_FOREIGN_SCHEMAS,
    QUERY_ROLE_PREDEFINED_MEMBERSHIPS,
    QUERY_ROLE_UNPREFIXED_TABLES,
)
from postgres_fastmcp.postgres.ports import QueryExecutorPort
from postgres_fastmcp.shared.errors import ConnectionFailedError, QueryCancelledError, QueryTimeoutError
from postgres_fastmcp.shared.logger import get_logger
from postgres_fastmcp.shared.utils import obfuscate_password


logger = get_logger(__name__)

# Длинный список схем в одной строке лога не читается: первые MAX_LISTED_SCHEMAS и многоточие.
MAX_LISTED_SCHEMAS = 10

# БД недоступна, запрос отменён или не успел: проверка пропускается, сервер работает дальше.
_SKIP_ERRORS = (PsycopgError, ConnectionFailedError, QueryTimeoutError, QueryCancelledError, TimeoutError)


@dataclass(frozen=True, slots=True)
class RoleFindings:
    """Роль подключения и её права сверх basic (английские фразы для лога)."""

    role: str
    findings: list[str]


async def basic_role_findings(catalog: QueryExecutorPort, table_prefix: str | None) -> RoleFindings:
    """Права роли подключения, которых basic не ожидает: всё, что шире таблиц public (с префиксом).

    Args:
        catalog: Исполнитель шаблонов каталога (канал сервера).
        table_prefix: Префикс таблиц basic; без него таблицы public без префикса не проверяются.

    Returns:
        Имя роли и находки; пустой список — роль ограничена public.
    """
    rows = await catalog.execute(QUERY_ROLE_ATTRIBUTES) or []
    if not rows:
        return RoleFindings(role="", findings=[])
    attributes = rows[0].cells
    role = str(attributes["role_name"])
    # Суперпользователю pg_has_role и has_*_privilege всегда отвечают true: остальное подразумевается.
    if attributes["rolsuper"]:
        return RoleFindings(role=role, findings=["superuser"])
    findings = ["BYPASSRLS"] if attributes["rolbypassrls"] else []
    findings += await _membership_findings(catalog)
    findings += await _schema_findings(catalog)
    if table_prefix:
        findings += await _unprefixed_table_findings(catalog, table_prefix)
    return RoleFindings(role=role, findings=findings)


async def _membership_findings(catalog: QueryExecutorPort) -> list[str]:
    """Членство в предопределённых ролях, открывающих данные или сервер целиком."""
    rows = await catalog.execute(QUERY_ROLE_PREDEFINED_MEMBERSHIPS) or []
    names = [str(row.cells["rolname"]) for row in rows]
    return [f"member of {', '.join(names)}"] if names else []


async def _schema_findings(catalog: QueryExecutorPort) -> list[str]:
    """USAGE на схемы, кроме public, information_schema и pg_*."""
    rows = await catalog.execute(QUERY_ROLE_FOREIGN_SCHEMAS) or []
    names = [str(row.cells["nspname"]) for row in rows]
    if not names:
        return []
    listed = ", ".join(names[:MAX_LISTED_SCHEMAS])
    if len(names) > MAX_LISTED_SCHEMAS:
        listed += ", …"
    return [f"USAGE on schemas: {listed}"]


async def _unprefixed_table_findings(catalog: QueryExecutorPort, table_prefix: str) -> list[str]:
    """SELECT на отношения public без префикса: basic их отклоняет, но роль может их читать."""
    rows = await catalog.execute(QUERY_ROLE_UNPREFIXED_TABLES, [table_prefix]) or []
    count = int(rows[0].cells["unprefixed"]) if rows else 0
    if not count:
        return []
    noun = "table" if count == 1 else "tables"
    return [f"SELECT on {count} public {noun} without prefix '{table_prefix}'"]


async def warn_about_basic_role(catalog: QueryExecutorPort, table_prefix: str | None) -> None:
    """Одна строка WARNING, если роль может больше, чем basic; ошибка БД — одна строка INFO.

    Args:
        catalog: Исполнитель шаблонов каталога (канал сервера).
        table_prefix: Префикс таблиц basic из конфигурации.
    """
    try:
        result = await basic_role_findings(catalog, table_prefix)
    except _SKIP_ERRORS as e:
        logger.info("Basic role check skipped: %s", obfuscate_password(str(e) or type(e).__name__))
    else:
        if result.findings:
            logger.warning(
                "Database role '%s' has privileges beyond basic mode: %s. In basic mode the SQL validator is then "
                "the only barrier; grant the role access to 'public' only (see README).",
                result.role,
                "; ".join(result.findings),
            )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/domains/test_role_check.py tests/unit/postgres/test_catalog_driver.py -q`
Expected: PASS (the catalog-driver tests are parametrized over `CATALOG_QUERIES` and now include the four new templates).

Run: `uv run pytest tests/unit -q`
Expected: PASS.

- [ ] **Step 6: Lint, type-check, commit**

```bash
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add src/postgres_fastmcp/postgres/catalog.py src/postgres_fastmcp/domains/role_check.py tests/unit/domains/test_role_check.py
git commit -m "feat(access): report database role privileges beyond basic mode"
```

---

### Task 2: фоновая проверка в `PostgresProvider.lifespan`

**Files:**
- Modify: `src/postgres_fastmcp/domains/db_access.py` (`DbAccessService.catalog_driver`)
- Modify: `src/postgres_fastmcp/provider.py`
- Modify: `tests/unit/conftest.py` (autouse no-op for the check), `tests/unit/test_provider.py` (`FakeService.catalog_driver`, new tests), `tests/unit/app/test_auth_http.py` (`FakeService.catalog_driver`), `tests/unit/app/test_response_budget.py` (`FakeDb.catalog_driver`)
- Test: `tests/unit/domains/test_db_access.py`, `tests/integration/test_role_check.py` (create)

**Interfaces:**
- Consumes (Task 1): `warn_about_basic_role(catalog, table_prefix)`, `basic_role_findings(catalog, table_prefix) -> RoleFindings`.
- Produces: `DbAccessService.catalog_driver -> QueryExecutorPort` (property, the same `CatalogSqlExecutor` every `view()` carries). `PostgresProvider` starts `warn_about_basic_role(self._db.catalog_driver, database.table_prefix)` as a task in `lifespan` when basic is reachable: `database.access_mode == AccessMode.BASIC`, or `access_resolver is not None`, or `access_policy is not None and access_policy.enforced`. `postgres_fastmcp.provider.warn_about_basic_role` is the patch point for tests.
- Note: `create_server` always passes `access_policy=settings.auth.access_policy` (an `AccessPolicy`, `enforced=False` by default), so the condition must read `.enforced`, not `is not None`.

- [ ] **Step 1: Write the failing tests**

1. `tests/unit/conftest.py` — unit tests must not reach a database from the provider's lifespan (several tests build a real `PostgresProvider` with a basic ceiling and host `h`). Append:

```python
@pytest.fixture(autouse=True)
def _no_basic_role_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Фоновая проверка роли при старте basic в юнит-тестах не ходит в БД; её тесты подменяют функцию сами."""

    async def _skip(*_args: object) -> None:
        return None

    monkeypatch.setattr("postgres_fastmcp.provider.warn_about_basic_role", _skip)
```

(This fixture fails until Task 2 Step 3 adds the import to `provider.py` — `monkeypatch.setattr` raises `AttributeError` on a missing attribute. That is the expected failure of this step for every unit test.)

2. `tests/unit/test_provider.py`:
- in `FakeService.__init__`, after `self.sql_driver.execute_statement = ...`, add:

```python
        self.catalog_driver = MagicMock()
        self.catalog_driver.execute = AsyncMock(return_value=[])
```

- append:

```python
@pytest.mark.parametrize(
    ("ceiling", "options", "reachable"),
    [
        (AccessMode.BASIC, {}, True),
        (AccessMode.FULL, {}, False),
        (AccessMode.FULL, {"access_policy": AccessPolicy()}, False),
        (AccessMode.FULL, {"access_policy": AccessPolicy(enforced=True)}, True),
        (AccessMode.FULL, {"access_resolver": lambda _token: EffectiveAccess(AccessMode.FULL, write_mode=False)}, True),
    ],
)
async def test_basic_role_check_runs_only_when_basic_is_reachable(
    fake_service: type[FakeService],
    monkeypatch: pytest.MonkeyPatch,
    ceiling: AccessMode,
    options: dict[str, object],
    *,
    reachable: bool,
) -> None:
    check = AsyncMock()
    monkeypatch.setattr("postgres_fastmcp.provider.warn_about_basic_role", check)
    provider = PostgresProvider(_database(ceiling), **options)

    async with Client(FastMCP("t", providers=[provider])) as client:
        await client.list_tools()

    if reachable:
        check.assert_called_once_with(fake_service.instances[0].catalog_driver, None)
    else:
        check.assert_not_called()


async def test_basic_role_check_gets_the_table_prefix(
    fake_service: type[FakeService], monkeypatch: pytest.MonkeyPatch
) -> None:
    check = AsyncMock()
    monkeypatch.setattr("postgres_fastmcp.provider.warn_about_basic_role", check)
    database = DatabaseConfig(host="h", user="u", password="p", name="d", table_prefix="app_")

    async with Client(FastMCP("t", providers=[PostgresProvider(database)])) as client:
        await client.list_tools()

    check.assert_called_once_with(fake_service.instances[0].catalog_driver, "app_")


async def test_slow_basic_role_check_is_cancelled_at_shutdown(
    fake_service: type[FakeService], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проверка не задерживает старт и не переживает остановку: задача отменяется, пул закрывается."""
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_check(*_args: object) -> None:
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr("postgres_fastmcp.provider.warn_about_basic_role", slow_check)

    async with Client(FastMCP("t", providers=[PostgresProvider(_database(AccessMode.BASIC))])) as client:
        await client.list_tools()
        await asyncio.wait_for(started.wait(), timeout=1)

    assert cancelled.is_set()
    assert fake_service.instances[0].closed == 1
```

(`asyncio`, `AsyncMock`, `MagicMock`, `AccessPolicy`, `EffectiveAccess`, `DatabaseConfig` are already imported in this module.)

3. The other two fakes that replace `postgres_fastmcp.provider.DbAccessService` (found with `grep -rn "provider.DbAccessService" tests`) need the attribute too — the provider reads `self._db.catalog_driver` when it creates the task, even with the no-op check:
- `tests/unit/app/test_auth_http.py`, `FakeService.__init__`, after the `self.sql_driver.execute_statement = ...` statement: `self.catalog_driver = MagicMock()`;
- `tests/unit/app/test_response_budget.py`, `FakeDb.__init__` (nested in the server-building helper), after the `self.sql_driver.execute_statement = ...` statement: `self.catalog_driver = MagicMock()`.

4. `tests/unit/domains/test_db_access.py`, append:

```python
def test_service_exposes_the_catalog_driver_of_its_views() -> None:
    """Проверка роли при старте идёт через тот же канал сервера, что и каталог в view()."""
    service = _service(access_mode=AccessMode.BASIC)
    assert service.catalog_driver is service.view(EffectiveAccess(AccessMode.BASIC, write_mode=False)).catalog_driver
    assert isinstance(service.catalog_driver, CatalogSqlExecutor)
```

5. Create `tests/integration/test_role_check.py`:

```python
# mypy: ignore-errors
"""Проверка прав роли basic на живом Postgres: суперпользователь CI и роль с лишними правами."""

import pytest

from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.domains.db_access import DbAccess, DbAccessService
from postgres_fastmcp.domains.role_check import basic_role_findings
from postgres_fastmcp.shared.enums import AccessMode


# Роли кластерные, контейнер живёт на класс тестов: создание идемпотентно.
_PROBE_SETUP = """
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'mcp_role_probe') THEN
    CREATE ROLE mcp_role_probe LOGIN PASSWORD 'probe-pw';
  END IF;
END $$;
GRANT pg_read_all_stats TO mcp_role_probe;
CREATE SCHEMA IF NOT EXISTS probe_other;
GRANT USAGE ON SCHEMA probe_other TO mcp_role_probe;
CREATE TABLE IF NOT EXISTS public.probe_plain (id int);
GRANT SELECT ON public.probe_plain TO mcp_role_probe;
"""


@pytest.mark.asyncio
async def test_ci_superuser_is_reported(db_full: DbAccess) -> None:
    result = await basic_role_findings(db_full.catalog_driver, "app_")

    assert result.role == "postgres"
    assert result.findings == ["superuser"]


@pytest.mark.asyncio
async def test_role_with_extra_privileges_is_reported(
    db_full: DbAccess, test_postgres_connection_string: tuple[str, str]
) -> None:
    await db_full.sql_driver.execute(_PROBE_SETUP, readonly=False)
    connection_string, _ = test_postgres_connection_string
    config = DatabaseConfig.from_uri(
        connection_string,
        user="mcp_role_probe",
        password="probe-pw",
        access_mode=AccessMode.BASIC,
        table_prefix="app_",
    )
    service = DbAccessService(config)
    try:
        result = await basic_role_findings(service.catalog_driver, "app_")
    finally:
        await service.close()

    assert result.role == "mcp_role_probe"
    assert "superuser" not in result.findings
    assert "member of pg_read_all_stats" in result.findings
    assert any(f.startswith("USAGE on schemas: ") and "probe_other" in f for f in result.findings), result.findings
    assert any(f.endswith("without prefix 'app_'") for f in result.findings), result.findings
```

Check statically and record in the report: the official postgres image enables password auth for TCP (`POSTGRES_PASSWORD` set in `tests/utils.py`), so the probe role can log in; `db_full` uses the unrestricted `SqlExecutor`, which sends the multi-statement string without formatting (`$$` and `'` reach Postgres as written).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_provider.py tests/unit/domains/test_db_access.py -q`
Expected: FAIL — every test errors in the autouse fixture with `AttributeError: <module 'postgres_fastmcp.provider'> has no attribute 'warn_about_basic_role'`; `test_service_exposes_the_catalog_driver_of_its_views` fails with `AttributeError: 'DbAccessService' object has no attribute 'catalog_driver'`.

- [ ] **Step 3: Implement**

1. `src/postgres_fastmcp/domains/db_access.py`, in `DbAccessService` after `view()`:

```python
    @property
    def catalog_driver(self) -> QueryExecutorPort:
        """Исполнитель шаблонов каталога (канал сервера), тот же, что в каждом view(); для проверок при старте."""
        return self._catalog
```

2. `src/postgres_fastmcp/provider.py`:
- module docstring, first paragraph: replace «закрывается в ``lifespan``» with «закрывается в ``lifespan``; там же при достижимом basic запускается фоновая проверка прав роли».
- imports: add `import asyncio` (top, stdlib) and `from postgres_fastmcp.domains.role_check import warn_about_basic_role`.
- in `__init__`, as the first lines after `super().__init__(on_duplicate="error")` (before `access_resolver` is reassigned):

```python
        # basic достижим: потолок basic или права сужаются по токену до basic
        self._basic_reachable = (
            database.access_mode == AccessMode.BASIC
            or access_resolver is not None
            or (access_policy is not None and access_policy.enforced)
        )
        self._table_prefix = database.table_prefix
```

- replace `lifespan`:

```python
    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """Фоновая проверка прав роли для basic на старте; при остановке — отменить её и закрыть пул.

        Проверка не задерживает старт: одна строка WARNING о правах шире basic или INFO, если БД недоступна.
        """
        role_check = (
            asyncio.create_task(warn_about_basic_role(self._db.catalog_driver, self._table_prefix))
            if self._basic_reachable
            else None
        )
        try:
            yield
        finally:
            if role_check is not None:
                role_check.cancel()
                await asyncio.wait({role_check})
            await self._db.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit -q`
Expected: PASS.

Run: `uv run pytest tests/integration -q`
Expected: collected; skipped locally (no Docker).

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add src/postgres_fastmcp/domains/db_access.py src/postgres_fastmcp/provider.py tests/unit/conftest.py tests/unit/test_provider.py tests/unit/app/test_auth_http.py tests/unit/app/test_response_budget.py tests/unit/domains/test_db_access.py tests/integration/test_role_check.py
git commit -m "feat(provider): warn at startup when the basic-mode role can reach beyond public"
```

---

### Task 3: README и спека

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-09-28-basic-followups-design.md`

**Interfaces:**
- Consumes: warning text of Task 1 (`Database role '<role>' has privileges beyond basic mode: ...`), start condition of Task 2.

- [ ] **Step 1: README — раздел «Роль для basic»**

In `README.md`, right after the paragraph starting `**Что закрыто в access_mode=basic для SQL агента:**` (end of «Контроль доступа», before `### Транспорты`), insert:

````markdown
#### Роль для basic

`access_mode=basic` — защита в глубину: валидатор SQL отклоняет обращения вне `public`, но настоящая граница — права роли, под которой сервер подключается к БД. Роли для basic достаточно `SELECT` на таблицы `public` (и DML на них при `write_mode=true`):

```sql
CREATE ROLE mcp_basic LOGIN PASSWORD '...' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
GRANT CONNECT ON DATABASE mydb TO mcp_basic;
GRANT USAGE ON SCHEMA public TO mcp_basic;
-- все таблицы public (без table_prefix):
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_basic;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO mcp_basic;
-- с table_prefix = 'app_': только таблицы с префиксом
DO $$
DECLARE t record;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'app\_%' LOOP
    EXECUTE format('GRANT SELECT ON public.%I TO mcp_basic', t.tablename);
  END LOOP;
END $$;
-- write_mode = true: добавить INSERT, UPDATE, DELETE на те же таблицы и USAGE на их последовательности
```

Чего роли для basic не давать:

- членства в `pg_read_all_data`, `pg_read_all_settings`, `pg_read_all_stats`, `pg_monitor` (и других предопределённых ролях с доступом к данным или серверу);
- прав на `pg_stat_statements`: расширение лучше ставить в отдельную схему, на которую у роли нет `USAGE`;
- `SELECT` на представления в `public` поверх таблиц других схем, если эти данные агенту не нужны: представление отдаёт данные схемы, над которой построено, каждому, кому выдан `SELECT` на само представление.

При старте с достижимым basic (потолок `basic`, свой `access_resolver` или `access_policy.enforced=true`) сервер в фоне проверяет права роли и пишет одну строку WARNING, если роль может больше, например:

```text
Database role 'app' has privileges beyond basic mode: superuser; member of pg_read_all_data; USAGE on schemas: billing, secret; SELECT on 3 public tables without prefix 'app_'. In basic mode the SQL validator is then the only barrier; grant the role access to 'public' only (see README).
```

Сервер при этом стартует. Если БД на старте недоступна, проверка пропускается с одной строкой INFO `Basic role check skipped: ...`.
````

- [ ] **Step 2: README — предупреждения на старте и жизненный цикл**

1. In `### Предупреждения на старте`, append a bullet to the list:

```markdown
- basic достижим, а роль БД может больше, чем таблицы `public` (суперпользователь, `BYPASSRLS`, предопределённые роли вроде `pg_read_all_data`, `USAGE` на другие схемы, таблицы `public` без `table_prefix`) — см. «Роль для basic».
```

2. In `### Управление жизненным циклом`, replace the bullet `- Пул открывается при первом запросе к БД` with:

```markdown
- Пул открывается при первом запросе к БД; если достижим basic — сразу при старте, фоновой проверкой прав роли (она не задерживает старт и отменяется при остановке)
```

- [ ] **Step 3: Spec**

In `docs/superpowers/specs/2026-09-28-basic-followups-design.md`:
1. Status line (line 3): `Статус: согласовано (...), к реализации.` → `Статус: согласовано (ответа на вопросы по дизайну не было — приняты рекомендуемые варианты); PR 1 реализован.`
2. §2.2: replace the sentence `- Логика — модуль \`domains/role_check.py\`: функция \`async def basic_role_findings(catalog: QueryExecutorPort, table_prefix: str | None) -> list[str]\` возвращает список находок (английские фразы), ...` so that it reads: `- Логика — модуль \`domains/role_check.py\`: функция \`async def basic_role_findings(catalog: QueryExecutorPort, table_prefix: str | None) -> RoleFindings\` возвращает имя роли и список находок (английские фразы); у суперпользователя находка одна — \`superuser\` (\`has_*_privilege\` у него всегда true). Функция \`async def warn_about_basic_role(catalog, table_prefix)\` пишет одну строку \`WARNING\`:` (keep the example line and the rest of the bullet list as is).
3. §5, PR 1 bullet → `- PR 1: при старте basic с широкими правами роли — \`WARNING\` в лог; в basic пул открывается при старте (фоновая проверка), а не при первом запросе; README — раздел «Роль для basic».`

- [ ] **Step 4: Full verification, commit**

```bash
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add README.md docs/superpowers/specs/2026-09-28-basic-followups-design.md
git commit -m "docs(access): describe the database role for basic mode"
```

---

## Self-review

- Spec §2.1 (README SQL, what not to grant, views over foreign schemas) → Task 3 Step 1. §2.2 start condition, background task, cancellation → Task 2; templates in `CATALOG_QUERIES` → Task 1 Step 3; `role_check.py`, warning text, 10-schema cap, INFO on `psycopg.Error`/`ConnectionFailedError`/timeout with masked error → Task 1 Step 4; tests (findings, text, skip, provider condition, CI superuser) → Task 1 Step 1, Task 2 Step 1. §5 PR 1 → Task 3 Step 3.
- Names used across tasks: `RoleFindings`, `basic_role_findings`, `warn_about_basic_role`, `DbAccessService.catalog_driver`, `postgres_fastmcp.provider.warn_about_basic_role` (patch point) — consistent.
