"""Тесты create_server: visibility BASIC/FULL, расширения, базовая корректность."""

import pytest
from fastmcp import FastMCP
from fastmcp.server.auth import AuthProvider
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.middleware.logging import LoggingMiddleware
from fastmcp.server.middleware.timing import TimingMiddleware
from fastmcp.server.providers import LocalProvider

from postgres_fastmcp.access import EffectiveAccess
from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.middleware.response_budget import ResponseBudgetMiddleware
from postgres_fastmcp.app.server import create_server
from postgres_fastmcp.provider import PostgresProvider
from postgres_fastmcp.shared.enums import AccessMode


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
    stub = AuthProvider()
    server = create_server(_settings(), auth=stub)
    assert server.auth is stub


def test_create_server_builds_on_postgres_provider() -> None:
    server = create_server(_settings())
    assert sum(isinstance(p, PostgresProvider) for p in server.providers) == 1


def test_create_server_keeps_budget_outermost_and_extra_middleware_after_builtin() -> None:
    class M(Middleware):
        pass

    extra = M()
    server = create_server(_settings(), extra_middleware=[extra])
    assert [type(m) for m in server.middleware[:4]] == [
        ResponseBudgetMiddleware,
        TimingMiddleware,
        LoggingMiddleware,
        M,
    ]


async def test_create_server_adds_extra_providers() -> None:
    extra = LocalProvider()

    def ping() -> str:
        return "pong"

    extra.add_tool(ping)
    server = create_server(_settings(), extra_providers=[extra])
    names = {t.name for t in await server.list_tools()}
    assert {"ping", "execute_sql"} <= names


async def test_create_server_passes_access_resolver_to_provider() -> None:
    """Резолвер, сужающий FULL до BASIC, прячет full-тулы сервера."""
    server = create_server(
        _settings(access_mode=AccessMode.FULL),
        access_resolver=lambda _token: EffectiveAccess(AccessMode.BASIC, write_mode=False),
    )
    names = {t.name for t in await server.list_tools()}
    assert names == {"execute_sql", "list_objects", "get_object_details", "explain_query"}
