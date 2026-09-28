"""HTTP-тесты auth без Docker: настоящий ASGI-стек FastMCP в процессе, БД подменена.

asgi_server из fastmcp.utilities.tests запускает http_app() сервера со всеми middleware
и проверкой токенов, но без сокета и uvicorn. Проверяется, что create_server берёт auth и
политику прав из settings.auth, а права запроса доходят до DbAccessService.view().
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.server.auth.providers.jwt import RSAKeyPair, StaticTokenVerifier
from fastmcp.utilities.tests import asgi_server

from postgres_fastmcp.access import EffectiveAccess
from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.config.auth import AuthSettings
from postgres_fastmcp.app.server import create_server
from postgres_fastmcp.domains.db_access import DbAccess
from postgres_fastmcp.postgres.models import RowResult, StatementResult
from postgres_fastmcp.shared.enums import AccessMode


_BASIC_TOOLS = {"execute_sql", "list_objects", "get_object_details", "explain_query"}
_ALL_TOOLS = _BASIC_TOOLS | {
    "list_schemas",
    "analyze_db_health",
    "get_top_queries",
    "analyze_query_indexes",
    "analyze_workload_indexes",
}
_READ_ONLY = EffectiveAccess(AccessMode.BASIC, write_mode=False)
_BASIC_WRITE = EffectiveAccess(AccessMode.BASIC, write_mode=True)
_FULL_WRITE = EffectiveAccess(AccessMode.FULL, write_mode=True)


class FakeService:
    """Подмена DbAccessService: запоминает права каждого запроса."""

    instances: list["FakeService"] = []

    def __init__(self, config: object) -> None:
        self.views: list[EffectiveAccess] = []
        self.sql_driver = MagicMock()
        self.sql_driver.execute = AsyncMock(return_value=[RowResult(cells={"n": 1})])
        self.sql_driver.execute_statement = AsyncMock(
            return_value=StatementResult(rows=[RowResult(cells={"n": 1})], status="SELECT 1", affected_rows=1)
        )
        FakeService.instances.append(self)

    def view(self, access: EffectiveAccess) -> DbAccess:
        self.views.append(access)
        return DbAccess(
            sql_driver=self.sql_driver,
            catalog_driver=self.sql_driver,
            access_mode=access.access_mode,
            write_mode=access.write_mode,
            table_prefix=None,
            connection_id="fake",
        )

    async def close(self) -> None:
        return None


@pytest.fixture(autouse=True)
def fake_service(monkeypatch: pytest.MonkeyPatch) -> type[FakeService]:
    FakeService.instances = []
    monkeypatch.setattr("postgres_fastmcp.provider.DbAccessService", FakeService)
    return FakeService


def _settings(auth: AuthSettings) -> Settings:
    """Потолок FULL + запись: сужение видно только по токену."""
    settings = Settings()
    settings.database = settings.database.model_copy(update={"access_mode": AccessMode.FULL, "write_mode": True})
    settings.auth = auth
    return settings


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
_CASES = [
    ("tok-reader", _BASIC_TOOLS, _READ_ONLY),
    ("tok-writer", _BASIC_TOOLS, _BASIC_WRITE),
    ("tok-admin", _ALL_TOOLS, _FULL_WRITE),
]


@pytest.mark.parametrize("auth", [_SCOPE_AUTH, _GROUPS_AUTH], ids=["scope", "groups"])
@pytest.mark.parametrize(("token", "tools", "access"), _CASES, ids=["reader", "writer", "admin"])
async def test_token_claims_narrow_tools_and_access(
    fake_service: type[FakeService],
    auth: AuthSettings,
    token: str,
    tools: set[str],
    access: EffectiveAccess,
) -> None:
    server = create_server(_settings(auth))
    assert isinstance(server.auth, StaticTokenVerifier)
    async with asgi_server(server) as running, running.client(auth=token) as client:
        names = {tool.name for tool in await client.list_tools()}
        await client.call_tool("execute_sql", {"sql": "SELECT 1 AS n"})
        schemas = await client.call_tool("list_schemas", {}, raise_on_error=False)
    assert names == tools
    # list_schemas у admin тоже берёт view: у каждого вызова те же права
    assert set(fake_service.instances[0].views) == {access}
    assert schemas.is_error is (access.access_mode == AccessMode.BASIC)
    if schemas.is_error:
        assert "Unknown tool" in schemas.content[0].text


@pytest.mark.parametrize("header", [None, "Bearer tok-unknown"])
async def test_missing_or_unknown_token_is_401(header: str | None) -> None:
    server = create_server(_settings(_SCOPE_AUTH))
    headers = {"Accept": "application/json, text/event-stream"}
    if header is not None:
        headers["Authorization"] = header
    async with asgi_server(server) as running, running.http_client() as http:
        response = await http.post(running.url, json={}, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")


async def test_token_without_required_scope_is_401() -> None:
    """Верификаторы FastMCP отбрасывают токен без required_scopes целиком: 401, а не 403."""
    auth = _SCOPE_AUTH.model_copy(update={"required_scopes": ["mcp"]})
    server = create_server(_settings(auth))
    async with asgi_server(server) as running, running.http_client() as http:
        response = await http.post(
            running.url,
            json={},
            headers={"Authorization": "Bearer tok-admin", "Accept": "application/json, text/event-stream"},
        )
    assert response.status_code == 401


async def test_jwt_mode_over_http(fake_service: type[FakeService]) -> None:
    keys = RSAKeyPair.generate()
    auth = AuthSettings(
        mode="jwt",
        jwt_public_key=keys.public_key,
        jwt_issuer="https://sso.example.com",
        jwt_audience="postgres-mcp",
        access_policy={"enforced": True, "claim": "realm_access.roles", "write_values": ["writer"]},
    )
    token = keys.create_token(
        issuer="https://sso.example.com",
        audience="postgres-mcp",
        additional_claims={"realm_access": {"roles": ["writer"]}},
    )
    async with asgi_server(create_server(_settings(auth))) as running, running.client(auth=token) as client:
        names = {tool.name for tool in await client.list_tools()}
        await client.call_tool("execute_sql", {"sql": "SELECT 1 AS n"})
    assert names == _BASIC_TOOLS
    assert fake_service.instances[0].views == [_BASIC_WRITE]


async def test_mode_none_over_http_keeps_the_ceiling(fake_service: type[FakeService]) -> None:
    """mode=none: как до auth-шага — без токена, все тулы потолка, права = потолок."""
    server = create_server(_settings(AuthSettings()))
    assert server.auth is None
    async with asgi_server(server) as running, running.client() as client:
        names = {tool.name for tool in await client.list_tools()}
        await client.call_tool("execute_sql", {"sql": "SELECT 1 AS n"})
    assert names == _ALL_TOOLS
    assert fake_service.instances[0].views == [_FULL_WRITE]


async def test_explicit_auth_argument_wins_over_settings(fake_service: type[FakeService]) -> None:
    explicit = StaticTokenVerifier(tokens={"tok-explicit": {"client_id": "x", "scopes": ["pg:write"]}})
    server = create_server(_settings(_SCOPE_AUTH), auth=explicit)
    assert server.auth is explicit
    async with asgi_server(server) as running, running.client(auth="tok-explicit") as client:
        await client.call_tool("execute_sql", {"sql": "SELECT 1 AS n"})
    assert fake_service.instances[0].views == [_BASIC_WRITE]
