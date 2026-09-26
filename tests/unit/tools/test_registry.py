"""Тесты для tools/registry.py: проверка регистрации 9 тулов через add_tool."""

from __future__ import annotations

import asyncio

from fastmcp import FastMCP

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.tools.registry import (
    DESTRUCTIVE,
    READ_ONLY_IDEMPOTENT,
    READ_ONLY_NON_IDEMPOTENT,
    register_tools,
)


def _registered_tool_names(mcp: FastMCP) -> set[str]:
    """Извлечь имена зарегистрированных тулов через публичный async API."""
    result = asyncio.run(mcp.list_tools())
    if isinstance(result, dict):
        return set(result.keys())
    return {t.name for t in result}


def _build_settings(access_mode: AccessMode, *, write_mode: bool = False) -> Settings:
    settings = Settings()
    settings.database = settings.database.model_copy(
        update={"access_mode": access_mode, "write_mode": write_mode}
    )
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
