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
