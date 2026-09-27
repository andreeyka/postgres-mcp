# mypy: ignore-errors
"""Integration test for library usage: two PostgresProvider instances in one host FastMCP server."""

import pytest
from fastmcp import Client, FastMCP
from fastmcp.utilities.tests import asgi_server

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.app.server import create_server
from postgres_fastmcp.provider import PostgresProvider
from postgres_fastmcp.shared.enums import AccessMode


@pytest.mark.asyncio
async def test_host_server_with_basic_and_namespaced_full_provider(
    test_postgres_connection_string: tuple[str, str],
) -> None:
    """Basic provider without namespace and a full provider under 'analytics' share one host server."""
    connection_string, _ = test_postgres_connection_string
    host = FastMCP("host")
    host.add_provider(PostgresProvider(DatabaseConfig.from_uri(connection_string, access_mode=AccessMode.BASIC)))
    host.add_provider(
        PostgresProvider(DatabaseConfig.from_uri(connection_string, access_mode=AccessMode.FULL)),
        namespace="analytics",
    )
    async with Client(host) as client:
        names = {tool.name for tool in await client.list_tools()}
        basic = await client.call_tool("execute_sql", {"sql": "SELECT 1 AS n", "output": "json"})
        schemas = await client.call_tool("analytics_list_schemas", {"output": "json"})
        blocked = await client.call_tool("list_schemas", {}, raise_on_error=False)

    assert "execute_sql" in names
    assert "analytics_list_schemas" in names
    assert "list_schemas" not in names
    assert basic.structured_content == {"rows": [{"n": 1}], "row_count": 1}
    assert "public" in {row["schema_name"] for row in schemas.structured_content["rows"]}
    assert blocked.is_error is True


@pytest.mark.asyncio
async def test_health_is_ok_on_a_real_database(test_postgres_connection_string: tuple[str, str]) -> None:
    """GET /health по настоящему HTTP-стеку: SELECT 1 на реальной БД даёт 200."""
    connection_string, _ = test_postgres_connection_string
    settings = Settings(database=DatabaseConfig.from_uri(connection_string))
    async with asgi_server(create_server(settings)) as running, running.http_client() as http:
        response = await http.get("http://127.0.0.1/health")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})
