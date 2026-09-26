"""Тесты create_server: visibility BASIC/FULL, расширения, базовая корректность."""

import pytest
from fastmcp import FastMCP
from fastmcp.server.auth import AuthProvider
from fastmcp.server.auth.oidc_proxy import OIDCProxy
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.middleware.logging import LoggingMiddleware
from fastmcp.server.middleware.timing import TimingMiddleware
from fastmcp.server.providers import LocalProvider

from postgres_fastmcp.access import EffectiveAccess
from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.config.auth import AuthSettings
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


_STATIC_AUTH = AuthSettings(mode="static", tokens={"tok-secret-value": {"client_id": "c"}})


def _auth_settings(*, transport: str = "http", host: str = "127.0.0.1", auth: AuthSettings | None = None) -> Settings:
    s = _settings()
    s.server = s.server.model_copy(update={"transport": transport, "host": host})
    s.auth = auth or AuthSettings()
    return s


def _auth_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage() for r in caplog.records if r.name == "postgres_fastmcp.app.server" and r.levelname == "WARNING"
    ]


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_no_warning_for_loopback_http_without_auth(caplog: pytest.LogCaptureFixture, host: str) -> None:
    create_server(_auth_settings(host=host))
    assert _auth_warnings(caplog) == []


def test_warns_when_http_is_exposed_without_auth(caplog: pytest.LogCaptureFixture) -> None:
    create_server(_auth_settings(host="0.0.0.0"))
    [message] = _auth_warnings(caplog)
    assert "HTTP server on 0.0.0.0 has no authentication" in message


def test_no_warning_when_exposed_http_has_auth(caplog: pytest.LogCaptureFixture) -> None:
    create_server(_auth_settings(host="0.0.0.0", auth=_STATIC_AUTH))
    create_server(_auth_settings(host="0.0.0.0"), auth=AuthProvider())
    assert _auth_warnings(caplog) == []


def test_stdio_skips_building_the_configured_auth_provider(caplog: pytest.LogCaptureFixture) -> None:
    """settings.auth.mode=static в stdio не должен строить провайдер: discovery/mkdir не должны выполняться."""
    server = create_server(_auth_settings(transport="stdio", auth=_STATIC_AUTH))
    assert server.auth is None
    [message] = _auth_warnings(caplog)
    assert "mode=static" in message
    assert "stdio" in message
    assert "tok-secret-value" not in caplog.text


def test_stdio_skips_oidc_discovery_for_an_unreachable_idp(monkeypatch: pytest.MonkeyPatch) -> None:
    """Регрессия: недоступный IdP не должен мешать stdio-серверу стартовать (discovery не вызывается)."""

    def discovery(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("OIDC discovery must not run when the provider is skipped for stdio")

    monkeypatch.setattr(OIDCProxy, "get_oidc_configuration", discovery)
    oidc_auth = AuthSettings(
        mode="oidc",
        oidc_config_url="https://sso.example.com/.well-known/openid-configuration",
        oidc_client_id="postgres-mcp",
        oidc_client_secret="oidc-client-secret",
        base_url="https://mcp.example.com",
    )
    server = create_server(_auth_settings(transport="stdio", auth=oidc_auth))
    assert server.auth is None


def test_explicit_auth_in_stdio_still_warns_as_before(caplog: pytest.LogCaptureFixture) -> None:
    """auth= явно передан в stdio: провайдер не строится из настроек, но предупреждение — как раньше."""
    explicit = StaticTokenVerifier(tokens={"tok-secret-value": {"client_id": "c"}})
    server = create_server(_auth_settings(transport="stdio"), auth=explicit)
    assert server.auth is explicit
    [message] = _auth_warnings(caplog)
    assert "Authentication (StaticTokenVerifier) applies only to the HTTP transport" in message
    assert "tok-secret-value" not in caplog.text


def test_http_still_builds_the_auth_provider_from_settings() -> None:
    server = create_server(_auth_settings(auth=_STATIC_AUTH))
    assert isinstance(server.auth, StaticTokenVerifier)


def test_no_warning_for_stdio_without_auth(caplog: pytest.LogCaptureFixture) -> None:
    create_server(_auth_settings(transport="stdio"))
    assert _auth_warnings(caplog) == []


def test_warns_that_enforced_policy_needs_auth(caplog: pytest.LogCaptureFixture) -> None:
    create_server(_auth_settings(auth=AuthSettings(access_policy={"enforced": True})))
    [message] = _auth_warnings(caplog)
    assert "access_policy.enforced=true has no effect without authentication" in message


def test_no_policy_warning_with_custom_resolver(caplog: pytest.LogCaptureFixture) -> None:
    create_server(
        _auth_settings(auth=AuthSettings(access_policy={"enforced": True})),
        access_resolver=lambda _token: EffectiveAccess(AccessMode.BASIC, write_mode=False),
    )
    assert _auth_warnings(caplog) == []
