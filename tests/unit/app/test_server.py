"""Тесты create_server: visibility BASIC/FULL, расширения, базовая корректность."""

import pytest
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.app.server import create_server


def _settings(*, access_mode: AccessMode = AccessMode.BASIC, write_mode: bool = False) -> Settings:
    s = Settings()
    s.database = s.database.model_copy(update={"access_mode": access_mode, "write_mode": write_mode})
    return s


@pytest.mark.asyncio
async def test_create_server_returns_fastmcp_instance() -> None:
    server = create_server(_settings())
    assert isinstance(server, FastMCP)


@pytest.mark.asyncio
async def test_create_server_basic_mode_hides_full_tools() -> None:
    server = create_server(_settings(access_mode=AccessMode.BASIC))
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert {"execute_sql", "list_objects", "get_object_details", "explain_query"} <= names
    assert "list_schemas" not in names
    assert "analyze_db_health" not in names


@pytest.mark.asyncio
async def test_create_server_full_mode_shows_all_tools() -> None:
    server = create_server(_settings(access_mode=AccessMode.FULL))
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert {
        "execute_sql",
        "list_objects",
        "get_object_details",
        "explain_query",
        "list_schemas",
        "analyze_db_health",
        "get_top_queries",
        "analyze_query_indexes",
        "analyze_workload_indexes",
    } <= names


def test_create_server_attaches_extra_middleware() -> None:
    class M(Middleware):
        async def on_request(self, ctx: MiddlewareContext, call_next):  # type: ignore[no-untyped-def]
            return await call_next(ctx)

    extra = M()
    server = create_server(_settings(), extra_middleware=[extra])
    assert server is not None


def test_create_server_passes_auth_to_fastmcp() -> None:
    server = create_server(_settings(), auth=None)
    assert server is not None
