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
    """Basic + write_mode: hypopg may be created (SafeSqlExecutor path, not the unrestricted executor)."""
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
