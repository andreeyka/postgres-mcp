"""Тесты create_server: visibility BASIC/FULL, расширения, базовая корректность."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, FastMCP
from fastmcp.server.auth import AuthProvider
from fastmcp.server.auth.oidc_proxy import OIDCProxy
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.middleware.logging import LoggingMiddleware
from fastmcp.server.middleware.timing import TimingMiddleware
from fastmcp.server.providers import LocalProvider
from fastmcp.utilities.tests import asgi_server

from postgres_fastmcp import __version__
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
@pytest.mark.asyncio
async def test_server_reports_the_package_version() -> None:
    """ServerInfo и баннер FastMCP несут версию postgres-fastmcp, а не версию FastMCP."""
    async with Client(create_server(_settings())) as client:
        assert client.server_info is not None
        assert client.server_info.version == __version__


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


async def test_create_server_check_basic_role_defaults_to_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """По умолчанию (как для HTTP) фоновая проверка роли запускается."""
    called = asyncio.Event()

    async def fake_check(*_args: object) -> None:
        called.set()

    monkeypatch.setattr("postgres_fastmcp.provider.warn_about_basic_role", fake_check)
    server = create_server(_settings(access_mode=AccessMode.BASIC))
    async with Client(server) as client:
        await client.list_tools()
        await asyncio.wait_for(called.wait(), timeout=1)


async def test_create_server_check_basic_role_false_skips_the_role_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """check_basic_role=False (используется для stdio) не запускает фоновую проверку роли."""
    check = AsyncMock()
    monkeypatch.setattr("postgres_fastmcp.provider.warn_about_basic_role", check)
    server = create_server(_settings(access_mode=AccessMode.BASIC), check_basic_role=False)
    async with Client(server) as client:
        await client.list_tools()
    check.assert_not_called()


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


async def test_create_server_builds_auth_by_default_regardless_of_configured_transport(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fail-closed: settings.server.transport — конфигурационное значение, не то, чем вызывающий
    реально запустит сервер (mcp.run(transport=...)). Библиотечный create_server(settings) должен
    строить провайдер по умолчанию независимо от него, иначе settings.server.transport="stdio" +
    mcp.run(transport="http") обслуживал бы HTTP вовсе без аутентификации.
    """
    server = create_server(_auth_settings(transport="stdio", auth=_STATIC_AUTH))
    assert isinstance(server.auth, StaticTokenVerifier)
    # Провайдер построен и реально enforce'ится, даже раз конфиг говорит "stdio" (см. запрос ниже);
    # предупреждение о stdio здесь — не про безопасность, а про то, что over-stdio auth не действует.
    assert "applies only to the HTTP transport" in _auth_warnings(caplog)[0]
    async with asgi_server(server) as running, running.http_client() as http:
        response = await http.post(running.url, json={}, headers={"Accept": "application/json, text/event-stream"})
    assert response.status_code == 401


def test_build_auth_false_skips_the_configured_provider_and_warns(caplog: pytest.LogCaptureFixture) -> None:
    """build_auth=False — единственный способ пропустить сборку; предупреждение объясняет, почему."""
    server = create_server(_auth_settings(transport="stdio", auth=_STATIC_AUTH), build_auth=False)
    assert server.auth is None
    [message] = _auth_warnings(caplog)
    assert "mode=static" in message
    assert "build_auth=False" in message
    assert "tok-secret-value" not in caplog.text


def test_build_auth_false_skips_oidc_discovery_for_an_unreachable_idp(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Регрессия: build_auth=False (CLI stdio) не должен трогать сеть за недоступным IdP, но должен предупредить."""

    def discovery(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("OIDC discovery must not run when build_auth=False")

    monkeypatch.setattr(OIDCProxy, "get_oidc_configuration", discovery)
    oidc_auth = AuthSettings(
        mode="oidc",
        oidc_config_url="https://sso.example.com/.well-known/openid-configuration",
        oidc_client_id="postgres-mcp",
        oidc_client_secret="oidc-client-secret",
        base_url="https://mcp.example.com",
    )
    server = create_server(_auth_settings(transport="stdio", auth=oidc_auth), build_auth=False)
    assert server.auth is None
    [message] = _auth_warnings(caplog)
    assert "mode=oidc" in message
    assert "build_auth=False" in message


def test_explicit_auth_in_stdio_still_warns_as_before(caplog: pytest.LogCaptureFixture) -> None:
    """auth= явно передан в stdio: провайдер не строится из настроек, но предупреждение — как раньше."""
    explicit = StaticTokenVerifier(tokens={"tok-secret-value": {"client_id": "c"}})
    server = create_server(_auth_settings(transport="stdio"), auth=explicit)
    assert server.auth is explicit
    [message] = _auth_warnings(caplog)
    assert "Authentication (StaticTokenVerifier) applies only to the HTTP transport" in message
    assert "tok-secret-value" not in caplog.text


@pytest.mark.parametrize("build_auth", [True, False])
def test_explicit_auth_overrides_build_auth_flag(build_auth: bool) -> None:
    explicit = StaticTokenVerifier(tokens={"tok-secret-value": {"client_id": "c"}})
    server = create_server(_auth_settings(transport="stdio"), auth=explicit, build_auth=build_auth)
    assert server.auth is explicit


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
