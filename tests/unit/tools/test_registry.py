"""Тесты для tools/registry.py: проверка регистрации 9 тулов через add_tool."""

from __future__ import annotations

import asyncio

import pytest
from fastmcp import FastMCP

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.tools.registry import DESTRUCTIVE, READ_ONLY_IDEMPOTENT, READ_ONLY_NON_IDEMPOTENT, register_tools


def _registered_tool_names(mcp: FastMCP) -> set[str]:
    """Извлечь имена зарегистрированных тулов через публичный async API."""
    result = asyncio.run(mcp.list_tools())
    if isinstance(result, dict):
        return set(result.keys())
    return {t.name for t in result}


def _build_settings(access_mode: AccessMode, *, write_mode: bool = False) -> Settings:
    settings = Settings()
    settings.database = settings.database.model_copy(update={"access_mode": access_mode, "write_mode": write_mode})
    return settings


def test_register_tools_basic_contains_basic_four() -> None:
    mcp = FastMCP(name="test")
    register_tools(mcp, _build_settings(AccessMode.BASIC))
    names = _registered_tool_names(mcp)
    assert {"execute_sql", "list_objects", "get_object_details", "explain_query"} <= names


def test_register_tools_full_includes_all_nine() -> None:
    mcp = FastMCP(name="test")
    register_tools(mcp, _build_settings(AccessMode.FULL, write_mode=True))
    names = _registered_tool_names(mcp)
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
    mcp = FastMCP(name="test")
    register_tools(mcp, _build_settings(AccessMode.BASIC))
    names = _registered_tool_names(mcp)
    assert len(names) >= 9


def test_annotation_presets_use_snake_case_keys() -> None:
    """Пресеты аннотаций задают snake_case-поля SDK v2, а не camelCase-алиасы v1."""
    expected = {"read_only_hint", "destructive_hint", "idempotent_hint", "open_world_hint"}
    for preset in (READ_ONLY_IDEMPOTENT, READ_ONLY_NON_IDEMPOTENT, DESTRUCTIVE):
        assert set(preset) == expected


async def test_registered_tools_expose_snake_case_annotations() -> None:
    """Аннотации тулов заданы snake_case-полями SDK v2 и доходят до зарегистрированного Tool."""
    mcp = FastMCP(name="test")
    register_tools(mcp, _build_settings(AccessMode.FULL))

    list_objects = await mcp.get_tool("list_objects")
    execute_sql = await mcp.get_tool("execute_sql")

    assert list_objects is not None and list_objects.annotations is not None
    assert list_objects.annotations.read_only_hint is True
    assert list_objects.annotations.destructive_hint is False
    assert list_objects.annotations.idempotent_hint is True
    assert list_objects.annotations.open_world_hint is True

    assert execute_sql is not None and execute_sql.annotations is not None
    assert execute_sql.annotations.read_only_hint is True  # FULL без write_mode: read-only
    assert execute_sql.annotations.idempotent_hint is False


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


def _registered_timeouts(settings: Settings) -> dict[str, float | None]:
    mcp = FastMCP(name="test")
    register_tools(mcp, settings)
    return {t.name: t.timeout for t in asyncio.run(mcp.list_tools())}


def _build_timeout_settings(access_mode: AccessMode, *, write_mode: bool, safe_sql_timeout: int) -> Settings:
    settings = Settings()
    settings.database = settings.database.model_copy(
        update={"access_mode": access_mode, "write_mode": write_mode, "safe_sql_timeout": safe_sql_timeout}
    )
    return settings


@pytest.mark.parametrize(
    ("access_mode", "write_mode"),
    [(AccessMode.FULL, False), (AccessMode.BASIC, False), (AccessMode.BASIC, True)],
    ids=["full-ro", "basic-ro", "basic-rw"],
)
def test_tool_timeouts_outlast_statement_timeout(access_mode: AccessMode, *, write_mode: bool) -> None:
    """При SafeSqlExecutor таймаут тула длиннее statement_timeout + клиентской страховки."""
    timeouts = _registered_timeouts(_build_timeout_settings(access_mode, write_mode=write_mode, safe_sql_timeout=30))
    assert set(timeouts) == set(_PRE_CHANGE_TIMEOUTS)
    for name, timeout in timeouts.items():
        assert timeout is not None
        assert timeout > 30 + 5, name
        assert timeout >= _PRE_CHANGE_TIMEOUTS[name], name


def test_tool_timeouts_follow_larger_safe_sql_timeout() -> None:
    """Таймаут тула выводится из safe_sql_timeout, а не только из констант."""
    timeouts = _registered_timeouts(_build_timeout_settings(AccessMode.FULL, write_mode=False, safe_sql_timeout=120))
    for name, timeout in timeouts.items():
        assert timeout is not None
        assert timeout > 120 + 5, name


def test_unrestricted_mode_keeps_existing_tool_timeouts() -> None:
    """FULL + write_mode идёт мимо SafeSqlExecutor: таймауты тулов остаются прежними."""
    timeouts = _registered_timeouts(_build_timeout_settings(AccessMode.FULL, write_mode=True, safe_sql_timeout=120))
    assert timeouts == _PRE_CHANGE_TIMEOUTS
