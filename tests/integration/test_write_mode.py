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
