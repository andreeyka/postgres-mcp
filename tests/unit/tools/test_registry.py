"""Тесты для tools/registry.py: регистрация 9 тулов ToolSet в LocalProvider."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import Client, FastMCP
from fastmcp.server.auth import AuthContext
from fastmcp.server.providers import LocalProvider

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.postgres.models import RowResult, StatementResult
from postgres_fastmcp.postgres.security.driver import CLIENT_TIMEOUT_GRACE_SECONDS
from postgres_fastmcp.shared.enums import AccessMode, ToolTag
from postgres_fastmcp.tools.definitions import ToolSet
from postgres_fastmcp.tools.registry import (
    _TOOL_TIMEOUT_MARGIN,
    DESTRUCTIVE,
    READ_ONLY_IDEMPOTENT,
    READ_ONLY_NON_IDEMPOTENT,
    WRITE_NON_DESTRUCTIVE,
    register_tools,
)


def _database(access_mode: AccessMode, *, write_mode: bool = False, safe_sql_timeout: int = 30) -> DatabaseConfig:
    return Settings().database.model_copy(
        update={"access_mode": access_mode, "write_mode": write_mode, "safe_sql_timeout": safe_sql_timeout}
    )


def _provider(database: DatabaseConfig, db: object | None = None, **kwargs: object) -> LocalProvider:
    """LocalProvider с зарегистрированными тулами; get_db отдаёт db (или MagicMock)."""
    provider = LocalProvider()
    register_tools(provider, ToolSet(get_db=lambda: db or MagicMock()), ceiling=database, **kwargs)
    return provider


def _registered_tool_names(provider: LocalProvider) -> set[str]:
    return {t.name for t in asyncio.run(provider.list_tools())}


def test_register_tools_basic_contains_basic_four() -> None:
    names = _registered_tool_names(_provider(_database(AccessMode.BASIC)))
    assert {"execute_sql", "list_objects", "get_object_details", "explain_query"} <= names


def test_register_tools_full_includes_all_nine() -> None:
    names = _registered_tool_names(_provider(_database(AccessMode.FULL, write_mode=True)))
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


def test_register_tools_unique_names_total_nine() -> None:
    """Регистрация не зависит от режима: все 9 тулов есть всегда, видимость настраивает провайдер."""
    names = _registered_tool_names(_provider(_database(AccessMode.BASIC)))
    assert len(names) == 9


def test_annotation_presets_use_snake_case_keys() -> None:
    """Пресеты аннотаций задают snake_case-поля SDK v2, а не camelCase-алиасы v1."""
    expected = {"read_only_hint", "destructive_hint", "idempotent_hint", "open_world_hint"}
    for preset in (READ_ONLY_IDEMPOTENT, READ_ONLY_NON_IDEMPOTENT, WRITE_NON_DESTRUCTIVE, DESTRUCTIVE):
        assert set(preset) == expected


async def test_registered_tools_expose_snake_case_annotations() -> None:
    """Аннотации тулов заданы snake_case-полями SDK v2 и доходят до зарегистрированного Tool."""
    provider = _provider(_database(AccessMode.FULL))

    list_objects = await provider.get_tool("list_objects")
    execute_sql = await provider.get_tool("execute_sql")

    assert list_objects is not None and list_objects.annotations is not None
    assert list_objects.annotations.read_only_hint is True
    assert list_objects.annotations.destructive_hint is False
    assert list_objects.annotations.idempotent_hint is True
    assert list_objects.annotations.open_world_hint is True

    assert execute_sql is not None and execute_sql.annotations is not None
    assert execute_sql.annotations.read_only_hint is True  # FULL без write_mode: read-only
    assert execute_sql.annotations.idempotent_hint is False


@pytest.mark.parametrize(
    ("access_mode", "write_mode", "read_only", "destructive"),
    [
        (AccessMode.BASIC, False, True, False),
        (AccessMode.FULL, False, True, False),
        (AccessMode.BASIC, True, False, False),
        (AccessMode.FULL, True, False, True),
    ],
)
async def test_execute_sql_annotations_follow_write_mode(
    access_mode: AccessMode, *, write_mode: bool, read_only: bool, destructive: bool
) -> None:
    """С write_mode execute_sql пишет в любом режиме: read_only_hint=False; destructive только FULL+write."""
    execute_sql = await _provider(_database(access_mode, write_mode=write_mode)).get_tool("execute_sql")

    assert execute_sql is not None and execute_sql.annotations is not None
    assert execute_sql.annotations.read_only_hint is read_only
    assert execute_sql.annotations.destructive_hint is destructive
    assert execute_sql.annotations.idempotent_hint is False
    assert execute_sql.annotations.open_world_hint is True


# Таймауты тулов до выравнивания со statement_timeout: ни один не должен стать короче.
_PRE_CHANGE_TIMEOUTS: dict[str, float] = {
    "execute_sql": 30.0,
    "list_objects": 30.0,
    "get_object_details": 30.0,
    "explain_query": 30.0,
    "list_schemas": 30.0,
    "analyze_db_health": 60.0,
    "get_top_queries": 30.0,
    "analyze_query_indexes": 60.0,
    "analyze_workload_indexes": 60.0,
}


def _registered_timeouts(database: DatabaseConfig) -> dict[str, float | None]:
    return {t.name: t.timeout for t in asyncio.run(_provider(database).list_tools())}


def _derived_timeout(safe_sql_timeout: float) -> float:
    return safe_sql_timeout + CLIENT_TIMEOUT_GRACE_SECONDS + _TOOL_TIMEOUT_MARGIN


@pytest.mark.parametrize(
    ("access_mode", "write_mode"),
    [(AccessMode.FULL, False), (AccessMode.BASIC, False), (AccessMode.BASIC, True), (AccessMode.FULL, True)],
    ids=["full-ro", "basic-ro", "basic-rw", "full-rw"],
)
def test_tool_timeouts_outlast_statement_timeout(access_mode: AccessMode, *, write_mode: bool) -> None:
    """Таймаут тула длиннее statement_timeout + клиентской страховки при любом потолке.

    Даже FULL + write_mode считается: свой access_resolver может сузить запрос до read-only,
    и тогда он получит SafeSqlExecutor со statement_timeout, о котором тул-таймаут обязан знать.
    """
    timeouts = _registered_timeouts(_database(access_mode, write_mode=write_mode, safe_sql_timeout=30))
    assert set(timeouts) == set(_PRE_CHANGE_TIMEOUTS)
    for name, timeout in timeouts.items():
        assert timeout is not None
        assert timeout > 30 + 5, name
        assert timeout >= _PRE_CHANGE_TIMEOUTS[name], name


def test_tool_timeouts_follow_larger_safe_sql_timeout() -> None:
    """Таймаут тула выводится из safe_sql_timeout, а не только из констант."""
    timeouts = _registered_timeouts(_database(AccessMode.FULL, write_mode=False, safe_sql_timeout=120))
    for name, timeout in timeouts.items():
        assert timeout is not None
        assert timeout > 120 + 5, name


def test_full_write_mode_timeouts_also_follow_safe_sql_timeout() -> None:
    """FULL + write_mode больше не фиксирован на базовом значении.

    Сервер сам не использует SafeSqlExecutor в этом режиме, но резолвер запроса может сузить
    права до read-only, и тогда исполнитель у запроса будет SafeSqlExecutor со
    statement_timeout = safe_sql_timeout; тул-таймаут должен быть длиннее в любом случае.
    """
    timeouts = _registered_timeouts(_database(AccessMode.FULL, write_mode=True, safe_sql_timeout=120))
    derived = _derived_timeout(120)
    assert timeouts == dict.fromkeys(_PRE_CHANGE_TIMEOUTS, derived)


async def test_tool_timeout_is_not_part_of_the_tools_list_wire() -> None:
    """Timeout — параметр вызова FastMCP, а не поле протокольного Tool: формула таймаута
    не может изменить то, что клиент видит в tools/list."""
    tool = await _provider(_database(AccessMode.BASIC, safe_sql_timeout=999)).get_tool("execute_sql")
    wire = tool.to_mcp_tool().model_dump(by_alias=True, exclude_none=True)
    assert "timeout" not in wire


_ROW_TOOLS = ("execute_sql", "list_objects", "get_object_details", "list_schemas", "get_top_queries")


async def test_row_tools_have_no_output_schema() -> None:
    """Тулы со строками отдают ToolResult сами: FastMCP не должен заворачивать ответ в {'result': ...}."""
    provider = _provider(_database(AccessMode.FULL))
    for name in _ROW_TOOLS:
        tool = await provider.get_tool(name)
        assert tool is not None
        assert tool.output_schema is None, name
        assert "output" in tool.parameters["properties"], name


async def test_execute_sql_output_over_mcp() -> None:
    """По MCP: 'table' — только Markdown, 'JSON' (любой регистр) — JSON-текст и structured_content."""
    db = MagicMock()
    db.write_mode = False
    db.sql_driver.execute = AsyncMock(return_value=[RowResult(cells={"n": 1})])
    db.sql_driver.execute_statement = AsyncMock(
        return_value=StatementResult(rows=[RowResult(cells={"n": 1})], status="SELECT 1", affected_rows=1)
    )
    mcp = FastMCP(name="test", providers=[_provider(_database(AccessMode.FULL), db)])
    async with Client(mcp) as client:
        table = await client.call_tool("execute_sql", {"sql": "SELECT 1 AS n"})
        as_json = await client.call_tool("execute_sql", {"sql": "SELECT 1 AS n", "output": "JSON"})

    assert [block.text for block in table.content] == ["| n |\n| --- |\n| 1 |\n\n1 rows."]
    assert table.structured_content is None
    assert [block.text for block in as_json.content] == ['{"rows": [{"n": 1}], "row_count": 1}']
    assert as_json.structured_content == {"rows": [{"n": 1}], "row_count": 1}


_FULL_TOOLS = (
    "list_schemas",
    "analyze_db_health",
    "get_top_queries",
    "analyze_query_indexes",
    "analyze_workload_indexes",
)


async def test_full_tool_auth_is_attached_to_full_tools_only() -> None:
    """Проверка прав навешивается только на full-тулы; basic-тулы без auth."""

    def check(ctx: AuthContext) -> bool:
        return True

    provider = _provider(_database(AccessMode.FULL), full_tool_auth=check)
    for tool in await provider.list_tools():
        if ToolTag.FULL.value in tool.tags:
            assert tool.name in _FULL_TOOLS
            assert tool.auth is check, tool.name
        else:
            assert tool.auth is None, tool.name


async def test_without_full_tool_auth_no_tool_has_auth() -> None:
    assert all(tool.auth is None for tool in await _provider(_database(AccessMode.FULL)).list_tools())


async def test_denied_full_tool_auth_hides_full_tools() -> None:
    """Отказ проверки убирает full-тул из списка и делает вызов Unknown tool."""
    provider = _provider(_database(AccessMode.FULL), full_tool_auth=lambda ctx: False)
    async with Client(FastMCP(name="test", providers=[provider])) as client:
        names = {t.name for t in await client.list_tools()}
        denied = await client.call_tool("list_schemas", {}, raise_on_error=False)
    assert names == {"execute_sql", "list_objects", "get_object_details", "explain_query"}
    assert denied.is_error is True
    assert "Unknown tool" in denied.content[0].text
