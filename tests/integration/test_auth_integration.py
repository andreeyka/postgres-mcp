# mypy: ignore-errors
"""Интеграция: HTTP-сервер со StaticTokenVerifier и access_policy.enforced=true на реальной БД.

Сервер работает в процессе поверх настоящего HTTP-стека FastMCP (fastmcp.utilities.tests.asgi_server):
без сокета и uvicorn, пул БД закрывается в lifespan сервера при выходе из asgi_server.
Потолок базы — full + запись; каждый токен сужает его через claim scope или groups.
"""

import pytest
from fastmcp.utilities.tests import asgi_server

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.config.auth import AuthSettings
from postgres_fastmcp.app.server import create_server
from postgres_fastmcp.domains.db_access import DbAccess


_BASIC_TOOLS = {"execute_sql", "list_objects", "get_object_details", "explain_query"}
_ALL_TOOLS = _BASIC_TOOLS | {
    "list_schemas",
    "analyze_db_health",
    "get_top_queries",
    "analyze_query_indexes",
    "analyze_workload_indexes",
}

_SCOPE_AUTH = AuthSettings(
    mode="static",
    access_policy={"enforced": True},
    tokens={
        "tok-reader": {"client_id": "reader"},
        "tok-writer": {"client_id": "writer", "scopes": ["pg:write"]},
        "tok-admin": {"client_id": "admin", "scopes": ["pg:full", "pg:write"]},
    },
)
_GROUPS_AUTH = AuthSettings(
    mode="static",
    access_policy={
        "enforced": True,
        "claim": "groups",
        "write_values": ["dba", "backend-writers"],
        "full_values": ["dba"],
    },
    tokens={
        "tok-reader": {"client_id": "reader", "claims": {"groups": ["analyst"]}},
        "tok-writer": {"client_id": "writer", "claims": {"groups": ["backend-writers"]}},
        "tok-admin": {"client_id": "admin", "claims": {"groups": ["dba", "analyst"]}},
    },
)


@pytest.fixture
async def auth_tables(db_full: DbAccess):
    """auth_items для INSERT; auth_created не должна существовать до CREATE TABLE."""
    for sql in (
        "DROP TABLE IF EXISTS auth_created",
        "DROP TABLE IF EXISTS auth_items",
        "CREATE TABLE auth_items (id int)",
    ):
        await db_full.sql_driver.execute(sql, readonly=False)
    yield
    for sql in ("DROP TABLE IF EXISTS auth_created", "DROP TABLE IF EXISTS auth_items"):
        await db_full.sql_driver.execute(sql, readonly=False)


def _text(result) -> str:
    return result.content[0].text if result.content else ""


@pytest.mark.asyncio
@pytest.mark.parametrize("auth", [_SCOPE_AUTH, _GROUPS_AUTH], ids=["scope", "groups"])
async def test_reader_token_is_read_only_basic(
    integration_settings: Settings, auth_tables, auth: AuthSettings, db_full: DbAccess
) -> None:
    integration_settings.auth = auth
    async with asgi_server(create_server(integration_settings)) as running, running.client(auth="tok-reader") as client:
        names = {tool.name for tool in await client.list_tools()}
        create = await client.call_tool(
            "execute_sql", {"sql": "CREATE TABLE auth_created (id int)"}, raise_on_error=False
        )
        insert = await client.call_tool(
            "execute_sql", {"sql": "INSERT INTO auth_items (id) VALUES (1)"}, raise_on_error=False
        )
        schemas = await client.call_tool("list_schemas", {}, raise_on_error=False)
    assert names == _BASIC_TOOLS
    assert create.is_error is True
    assert "read-only mode" in _text(create)
    assert insert.is_error is True
    assert "read-only mode" in _text(insert)
    assert schemas.is_error is True
    assert "Unknown tool" in _text(schemas)
    # Читаем напрямую через db_full: денайд reader'а не должен был оставить строку в auth_items
    remaining = await db_full.sql_driver.execute("SELECT count(*) AS n FROM auth_items", readonly=True)
    assert remaining[0].cells["n"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("auth", [_SCOPE_AUTH, _GROUPS_AUTH], ids=["scope", "groups"])
async def test_writer_token_writes_dml_but_not_ddl(
    integration_settings: Settings, auth_tables, auth: AuthSettings
) -> None:
    integration_settings.auth = auth
    async with asgi_server(create_server(integration_settings)) as running, running.client(auth="tok-writer") as client:
        names = {tool.name for tool in await client.list_tools()}
        create = await client.call_tool(
            "execute_sql", {"sql": "CREATE TABLE auth_created (id int)"}, raise_on_error=False
        )
        insert = await client.call_tool(
            "execute_sql", {"sql": "INSERT INTO auth_items (id) VALUES (1)"}, raise_on_error=False
        )
        count = await client.call_tool(
            "execute_sql",
            {"sql": "SELECT count(*) AS n FROM auth_items", "output": "json"},
            raise_on_error=False,
        )
        schemas = await client.call_tool("list_schemas", {}, raise_on_error=False)
    assert names == _BASIC_TOOLS
    assert create.is_error is True
    assert "DDL operations (CREATE, DROP, ALTER)" in _text(create)
    assert insert.is_error is False, _text(insert)
    assert count.is_error is False, _text(count)
    assert count.structured_content == {"rows": [{"n": 1}], "row_count": 1}
    assert schemas.is_error is True
    assert "Unknown tool" in _text(schemas)


@pytest.mark.asyncio
@pytest.mark.parametrize("auth", [_SCOPE_AUTH, _GROUPS_AUTH], ids=["scope", "groups"])
async def test_admin_token_gets_the_full_ceiling(
    integration_settings: Settings, auth_tables, auth: AuthSettings
) -> None:
    integration_settings.auth = auth
    async with asgi_server(create_server(integration_settings)) as running, running.client(auth="tok-admin") as client:
        names = {tool.name for tool in await client.list_tools()}
        create = await client.call_tool(
            "execute_sql", {"sql": "CREATE TABLE auth_created (id int)"}, raise_on_error=False
        )
        insert = await client.call_tool(
            "execute_sql", {"sql": "INSERT INTO auth_items (id) VALUES (1)"}, raise_on_error=False
        )
        exists = await client.call_tool(
            "execute_sql",
            {"sql": "SELECT to_regclass('public.auth_created') IS NOT NULL AS ok", "output": "json"},
            raise_on_error=False,
        )
        count = await client.call_tool(
            "execute_sql",
            {"sql": "SELECT count(*) AS n FROM auth_items", "output": "json"},
            raise_on_error=False,
        )
        schemas = await client.call_tool("list_schemas", {"output": "json"}, raise_on_error=False)
    assert names == _ALL_TOOLS
    assert create.is_error is False, _text(create)
    assert insert.is_error is False, _text(insert)
    # Читаем обратно: CREATE и INSERT выше не просто прошли без ошибки, а реально осели в базе
    assert exists.is_error is False, _text(exists)
    assert exists.structured_content == {"rows": [{"ok": True}], "row_count": 1}
    assert count.is_error is False, _text(count)
    assert count.structured_content == {"rows": [{"n": 1}], "row_count": 1}
    assert schemas.is_error is False, _text(schemas)
    assert "public" in {row["schema_name"] for row in schemas.structured_content["rows"]}


@pytest.mark.asyncio
async def test_request_without_token_is_rejected(integration_settings: Settings) -> None:
    integration_settings.auth = _SCOPE_AUTH
    async with asgi_server(create_server(integration_settings)) as running, running.http_client() as http:
        response = await http.post(running.url, json={}, headers={"Accept": "application/json, text/event-stream"})
    assert response.status_code == 401
