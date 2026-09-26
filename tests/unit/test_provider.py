"""Тесты PostgresProvider: видимость тулов, права запроса, lifespan и namespace."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import Client, FastMCP
from fastmcp.server.auth import AccessToken

from postgres_fastmcp.access import AccessPolicy, EffectiveAccess
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.domains.db_access import DbAccess
from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.provider import PostgresProvider
from postgres_fastmcp.shared.enums import AccessMode, ToolTag


_BASIC_TOOLS = {"execute_sql", "list_objects", "get_object_details", "explain_query"}
_FULL_TOOLS = {
    "list_schemas",
    "analyze_db_health",
    "get_top_queries",
    "analyze_query_indexes",
    "analyze_workload_indexes",
}


def _database(access_mode: AccessMode = AccessMode.FULL, *, write_mode: bool = False) -> DatabaseConfig:
    return DatabaseConfig(host="h", user="u", password="p", name="d", access_mode=access_mode, write_mode=write_mode)


class FakeService:
    """Подмена DbAccessService: запоминает запрошенные права и число закрытий."""

    instances: list["FakeService"] = []

    def __init__(self, config: object) -> None:
        self.config = config
        self.views: list[EffectiveAccess] = []
        self.closed = 0
        self.sql_driver = MagicMock()
        self.sql_driver.execute = AsyncMock(return_value=[RowResult(cells={"n": 1})])
        FakeService.instances.append(self)

    def view(self, access: EffectiveAccess) -> DbAccess:
        self.views.append(access)
        return DbAccess(
            sql_driver=self.sql_driver,
            access_mode=access.access_mode,
            write_mode=access.write_mode,
            table_prefix=None,
            connection_id="fake",
        )

    async def close(self) -> None:
        self.closed += 1


@pytest.fixture
def fake_service(monkeypatch: pytest.MonkeyPatch) -> type[FakeService]:
    FakeService.instances = []
    monkeypatch.setattr("postgres_fastmcp.provider.DbAccessService", FakeService)
    return FakeService


async def _tool_names(server: FastMCP) -> set[str]:
    async with Client(server) as client:
        return {tool.name for tool in await client.list_tools()}


async def test_basic_ceiling_lists_basic_tools_only() -> None:
    server = FastMCP("t", providers=[PostgresProvider(_database(AccessMode.BASIC))])
    assert await _tool_names(server) == _BASIC_TOOLS
    async with Client(server) as client:
        result = await client.call_tool("list_schemas", {}, raise_on_error=False)
    assert result.is_error is True
    assert "Unknown tool" in result.content[0].text


async def test_full_ceiling_lists_all_nine_tools() -> None:
    server = FastMCP("t", providers=[PostgresProvider(_database(AccessMode.FULL))])
    assert await _tool_names(server) == _BASIC_TOOLS | _FULL_TOOLS


async def test_full_tools_carry_access_check() -> None:
    provider = PostgresProvider(_database(AccessMode.FULL))
    for tool in await provider.list_tools():
        assert (tool.auth is not None) is (ToolTag.FULL.value in tool.tags), tool.name


async def test_lifespan_closes_the_service(fake_service: type[FakeService]) -> None:
    server = FastMCP("t", providers=[PostgresProvider(_database())])
    async with Client(server) as client:
        await client.call_tool("execute_sql", {"sql": "SELECT 1 AS n"})
        assert fake_service.instances[0].closed == 0
    assert fake_service.instances[0].closed == 1


async def test_lifespan_closes_the_service_even_if_the_client_body_raises(
    fake_service: type[FakeService],
) -> None:
    """close() должен сработать и когда тело ``async with Client(...)`` падает исключением."""
    server = FastMCP("t", providers=[PostgresProvider(_database())])
    with pytest.raises(RuntimeError, match="boom"):
        async with Client(server) as client:
            await client.call_tool("execute_sql", {"sql": "SELECT 1 AS n"})
            raise RuntimeError("boom")
    assert fake_service.instances[0].closed == 1


async def test_tool_call_gets_access_from_resolver_and_token(fake_service: type[FakeService]) -> None:
    """get_db: токен текущего запроса (None без auth) -> резолвер -> view(права)."""
    tokens: list[AccessToken | None] = []
    narrowed = EffectiveAccess(AccessMode.FULL, write_mode=False)

    def resolver(token: AccessToken | None) -> EffectiveAccess:
        tokens.append(token)
        return narrowed

    provider = PostgresProvider(_database(AccessMode.FULL, write_mode=True), access_resolver=resolver)
    async with Client(FastMCP("t", providers=[provider])) as client:
        await client.call_tool("execute_sql", {"sql": "SELECT 1 AS n"})
    assert tokens
    assert set(tokens) == {None}
    assert fake_service.instances[0].views == [narrowed]


async def test_default_resolver_uses_the_ceiling(fake_service: type[FakeService]) -> None:
    provider = PostgresProvider(_database(AccessMode.BASIC, write_mode=True))
    async with Client(FastMCP("t", providers=[provider])) as client:
        await client.call_tool("execute_sql", {"sql": "SELECT 1 AS n"})
    assert fake_service.instances[0].views == [EffectiveAccess(AccessMode.BASIC, write_mode=True)]


async def test_custom_resolver_cannot_exceed_the_ceiling(fake_service: type[FakeService]) -> None:
    provider = PostgresProvider(
        _database(AccessMode.BASIC, write_mode=False),
        access_resolver=lambda _token: EffectiveAccess(AccessMode.FULL, write_mode=True),
    )
    async with Client(FastMCP("t", providers=[provider])) as client:
        await client.call_tool("execute_sql", {"sql": "SELECT 1 AS n"})
    assert fake_service.instances[0].views == [EffectiveAccess(AccessMode.BASIC, write_mode=False)]


async def test_resolver_narrowing_to_basic_hides_full_tools() -> None:
    """При потолке FULL резолвер, вернувший BASIC, скрывает full-тулы через проверку прав."""
    provider = PostgresProvider(
        _database(AccessMode.FULL),
        access_resolver=lambda _token: EffectiveAccess(AccessMode.BASIC, write_mode=False),
    )
    assert await _tool_names(FastMCP("t", providers=[provider])) == _BASIC_TOOLS


def test_enforced_policy_without_resolver_is_rejected() -> None:
    with pytest.raises(ValueError, match="AccessPolicy.enforced"):
        PostgresProvider(_database(), access_policy=AccessPolicy(enforced=True))


async def test_access_resolver_takes_priority_over_policy(fake_service: type[FakeService]) -> None:
    """access_resolver не просто не отвергается вместе с enforced policy: тул реально его вызывает."""
    narrowed = EffectiveAccess(AccessMode.BASIC, write_mode=False)
    provider = PostgresProvider(
        _database(),
        access_policy=AccessPolicy(enforced=True),
        access_resolver=lambda _token: narrowed,
    )
    async with Client(FastMCP("t", providers=[provider])) as client:
        await client.call_tool("execute_sql", {"sql": "SELECT 1 AS n"})
    assert fake_service.instances[0].views == [narrowed]


async def test_two_databases_in_one_host_via_namespace() -> None:
    host = FastMCP("host")
    host.add_provider(PostgresProvider(_database(AccessMode.BASIC)))
    host.add_provider(PostgresProvider(_database(AccessMode.FULL)), namespace="analytics")
    names = await _tool_names(host)
    assert names == _BASIC_TOOLS | {f"analytics_{name}" for name in _BASIC_TOOLS | _FULL_TOOLS}
