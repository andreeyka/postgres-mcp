"""Тесты для tools/registry.py: проверка регистрации 9 тулов через add_tool."""

from __future__ import annotations

import asyncio

from fastmcp import FastMCP

from postgres_fastmcp.config import Settings
from postgres_fastmcp.enums import AccessMode
from postgres_fastmcp.tools.registry import register_tools


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
