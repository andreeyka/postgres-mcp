"""Тесты проверки прав роли при старте basic: находки по шаблонам каталога, текст WARNING, пропуск при ошибке."""

import logging
from typing import Any

import pytest
from psycopg import OperationalError
from psycopg.errors import InsufficientPrivilege, UndefinedFunction

from postgres_fastmcp.domains.role_check import RoleFindings, basic_role_findings, warn_about_basic_role
from postgres_fastmcp.postgres.catalog import (
    QUERY_ROLE_ATTRIBUTES,
    QUERY_ROLE_FOREIGN_SCHEMAS,
    QUERY_ROLE_PREDEFINED_MEMBERSHIPS,
    QUERY_ROLE_UNPREFIXED_TABLES,
)
from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.shared.errors import (
    ConnectionFailedError,
    ConnectionNotEstablishedError,
    QueryCancelledError,
    QueryTimeoutError,
)


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


def _role(
    *, superuser: bool = False, bypassrls: bool = False, createrole: bool = False
) -> dict[str, list[dict[str, Any]]]:
    return {
        QUERY_ROLE_ATTRIBUTES: [
            {"role_name": "mcp", "rolsuper": superuser, "rolbypassrls": bypassrls, "rolcreaterole": createrole}
        ]
    }


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
            **_role(bypassrls=True, createrole=True),
            QUERY_ROLE_PREDEFINED_MEMBERSHIPS: [{"rolname": "pg_monitor"}, {"rolname": "pg_read_all_data"}],
            QUERY_ROLE_FOREIGN_SCHEMAS: [{"nspname": "billing"}, {"nspname": "secret"}],
            QUERY_ROLE_UNPREFIXED_TABLES: [{"unprefixed": 3}],
        }
    )

    result = await basic_role_findings(catalog, "app_")

    assert result.findings == [
        "BYPASSRLS",
        "CREATEROLE",
        "member of pg_monitor, pg_read_all_data",
        "USAGE on schemas: billing, secret",
        "SELECT on 3 public tables without prefix 'app_'",
    ]


async def test_createrole_is_a_finding_without_bypassrls() -> None:
    """CREATEROLE находится независимо от BYPASSRLS: на PG ≤15 роль может выдать себе предопределённые роли."""
    catalog = _Catalog(_role(createrole=True))

    result = await basic_role_findings(catalog, None)

    assert result.findings == ["CREATEROLE"]


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
        ConnectionNotEstablishedError(),
        QueryTimeoutError(30),
        QueryCancelledError(),
        TimeoutError(),
    ],
)
async def test_database_error_skips_the_check_with_info(caplog: pytest.LogCaptureFixture, error: Exception) -> None:
    """БД недоступна, запрос отменён или не успел: тихий пропуск, сервер стартует дальше."""
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        await warn_about_basic_role(_FailingCatalog(error), "app_")

    [record] = [r for r in caplog.records if r.name == _LOGGER]
    assert record.levelname == "INFO"
    assert record.getMessage().startswith("Basic role check skipped: ")
    assert "hunter2" not in record.getMessage()


async def test_no_access_to_the_catalog_skips_the_check_with_info(caplog: pytest.LogCaptureFixture) -> None:
    """Роли закрыли чтение каталога (REVOKE SELECT ON pg_roles FROM PUBLIC): проверить нечего, это не сбой."""
    error = InsufficientPrivilege("permission denied for view pg_roles")

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        await warn_about_basic_role(_FailingCatalog(error), "app_")

    [record] = [r for r in caplog.records if r.name == _LOGGER]
    assert record.levelname == "INFO"
    assert record.getMessage() == (
        "Basic role check skipped: the role cannot read the catalog (permission denied for view pg_roles)"
    )


async def test_other_database_error_fails_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Ошибка не в доступности БД, а в самом запросе (например, баг в шаблоне каталога) — не тихий пропуск."""
    error = UndefinedFunction("function starts_with(text, text) does not exist")

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        await warn_about_basic_role(_FailingCatalog(error), "app_")

    [record] = [r for r in caplog.records if r.name == _LOGGER]
    assert record.levelname == "WARNING"
    assert record.getMessage() == ("Basic role check failed: function starts_with(text, text) does not exist")


async def test_programming_bug_propagates() -> None:
    """ValueError/TypeError CatalogSqlExecutor — ошибка кода, не БД: должна дойти до вызывающего, а не потеряться."""
    with pytest.raises(ValueError, match="boom"):
        await warn_about_basic_role(_FailingCatalog(ValueError("boom")), "app_")
