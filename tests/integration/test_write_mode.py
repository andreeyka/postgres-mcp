# mypy: ignore-errors
"""Integration tests for write_mode / read-only execution through the execute_sql tool.

Regression coverage: previously execute_sql hardcoded readonly=True, so in
full + write_mode a DML/DDL statement either ran inside a READ ONLY transaction
(and failed) or, when it succeeded, was reported as the error "No results".
These tests drive the tool end-to-end to lock in the corrected behaviour.
"""

import pytest
from fastmcp import Client

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.app.server import create_server
from postgres_fastmcp.shared.enums import AccessMode


def _content(result: object) -> object:
    """Extract content from an MCP call_tool result."""
    return result.data if hasattr(result, "data") else getattr(result, "content", None)


@pytest.mark.asyncio
async def test_execute_sql_write_persists(integration_settings: Settings) -> None:
    """Full + write_mode: DDL/DML apply and a successful write reports success (not an error)."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        await client.call_tool("execute_sql", {"sql": "DROP TABLE IF EXISTS wm_write_test"})

        create = await client.call_tool(
            "execute_sql",
            {"sql": "CREATE TABLE wm_write_test (id int PRIMARY KEY, v text)"},
        )
        assert create.is_error is False

        insert = await client.call_tool(
            "execute_sql",
            {"sql": "INSERT INTO wm_write_test (id, v) VALUES (1, 'alpha')"},
        )
        assert insert.is_error is False
        assert "success" in str(_content(insert)).lower()

        select = await client.call_tool("execute_sql", {"sql": "SELECT v FROM wm_write_test WHERE id = 1"})
        assert select.is_error is False
        assert "alpha" in str(_content(select))

        await client.call_tool("execute_sql", {"sql": "DROP TABLE IF EXISTS wm_write_test"})


@pytest.mark.asyncio
async def test_execute_sql_readonly_blocks_write(
    test_postgres_connection_string: tuple[str, str],
) -> None:
    """write_mode=False: a write statement is rejected end-to-end via the tool surface."""
    connection_string, _ = test_postgres_connection_string
    database = DatabaseConfig.from_uri(
        connection_string,
        access_mode=AccessMode.FULL,
        write_mode=False,
    )
    settings = Settings(database=database)
    mcp = create_server(settings)
    async with Client(mcp) as client:
        # raise_on_error=False: current fastmcp raises ToolError by default on
        # isError results; here the error result itself is the expected outcome.
        result = await client.call_tool(
            "execute_sql",
            {"sql": "CREATE TABLE ro_write_test (id int)"},
            raise_on_error=False,
        )
    assert result.is_error is True
    assert "read-only" in str(_content(result) or result.content).lower()
