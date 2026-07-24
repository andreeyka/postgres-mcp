"""Программная регистрация тулов в FastMCP через Tool.from_function + add_tool.

Описания строятся в момент регистрации с учётом Settings (access_mode, write_mode),
поэтому модуль `tools/definitions` может импортироваться без инициализации конфига.
"""

from __future__ import annotations

from importlib.metadata import (
    PackageNotFoundError,
    version as _pkg_version,
)
from typing import TYPE_CHECKING, Any

from fastmcp.tools import Tool
from mcp.types import ToolAnnotations

from postgres_fastmcp.enums import AccessMode, ToolTag
from postgres_fastmcp.tools.definitions import (
    HEALTH_TYPE_VALUES,
    analyze_db_health,
    analyze_query_indexes,
    analyze_workload_indexes,
    execute_sql,
    explain_query,
    get_object_details,
    get_top_queries,
    list_objects,
    list_schemas,
)


if TYPE_CHECKING:
    from fastmcp import FastMCP

    from postgres_fastmcp.config import Settings


try:
    _VERSION = _pkg_version("postgres-fastmcp")
except PackageNotFoundError:
    _VERSION = "0.0.0"

_META: dict[str, Any] = {"version": _VERSION}

# Annotation presets for Tool.from_function(annotations={...})
# See https://gofastmcp.com/servers/tools — ToolAnnotations fields.
READ_ONLY_IDEMPOTENT: dict[str, bool] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
READ_ONLY_NON_IDEMPOTENT: dict[str, bool] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
DESTRUCTIVE: dict[str, bool] = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
}


def _ann(title: str, preset: dict[str, bool]) -> ToolAnnotations:
    return ToolAnnotations(title=title, **preset)


def register_tools(mcp: FastMCP, settings: Settings) -> None:
    """Зарегистрировать все 9 тулов на сервере.

    Visibility-фильтр (basic/full) по тегам применяется снаружи через FastMCP.
    """
    for spec in _all_tool_specs(settings):
        mcp.add_tool(Tool.from_function(**spec))


def _all_tool_specs(settings: Settings) -> list[dict[str, Any]]:
    return _basic_specs(settings) + _full_specs()


def _basic_specs(settings: Settings) -> list[dict[str, Any]]:
    db = settings.database
    unrestricted = db.access_mode == AccessMode.FULL and db.write_mode
    execute_preset = DESTRUCTIVE if unrestricted else READ_ONLY_NON_IDEMPOTENT
    return [
        {
            "fn": execute_sql,
            "name": "execute_sql",
            "description": _execute_sql_desc(unrestricted=unrestricted),
            "tags": {ToolTag.BASIC.value},
            "annotations": _ann("Execute SQL", execute_preset),
            "timeout": 30.0,
            "meta": _META,
        },
        {
            "fn": list_objects,
            "name": "list_objects",
            "description": _list_objects_desc(db.access_mode),
            "tags": {ToolTag.BASIC.value},
            "annotations": _ann("List Objects", READ_ONLY_IDEMPOTENT),
            "timeout": 30.0,
            "meta": _META,
        },
        {
            "fn": get_object_details,
            "name": "get_object_details",
            "description": _get_object_details_desc(db.access_mode),
            "tags": {ToolTag.BASIC.value},
            "annotations": _ann("Get Object Details", READ_ONLY_IDEMPOTENT),
            "timeout": 30.0,
            "meta": _META,
        },
        {
            "fn": explain_query,
            "name": "explain_query",
            "description": _explain_query_desc(),
            "tags": {ToolTag.BASIC.value},
            "annotations": _ann("Explain Query", READ_ONLY_IDEMPOTENT),
            "timeout": 30.0,
            "meta": _META,
        },
    ]


def _full_specs() -> list[dict[str, Any]]:
    return [
        {
            "fn": list_schemas,
            "name": "list_schemas",
            "description": (
                "Lists all schemas in the PostgreSQL database. Use this first to discover "
                "available namespaces before listing objects in a specific schema."
            ),
            "tags": {ToolTag.FULL.value},
            "annotations": _ann("List Schemas", READ_ONLY_IDEMPOTENT),
            "timeout": 30.0,
            "meta": _META,
        },
        {
            "fn": analyze_db_health,
            "name": "analyze_db_health",
            "description": (
                "Comprehensive PostgreSQL health audit across multiple independent dimensions: "
                f"{HEALTH_TYPE_VALUES}. Returns a structured report with actionable findings "
                "and recommendations. Use periodically to detect issues early. "
                "Pass 'all' for the full audit or a comma-separated subset such as 'index,connection'."
            ),
            "tags": {ToolTag.FULL.value},
            "annotations": _ann("Analyze DB Health", READ_ONLY_IDEMPOTENT),
            "timeout": 60.0,
            "meta": _META,
        },
        {
            "fn": get_top_queries,
            "name": "get_top_queries",
            "description": (
                "Report the slowest or most resource-intensive queries from pg_stat_statements. "
                "The pg_stat_statements extension must be enabled. Workflow: get_top_queries -> "
                "explain_query -> analyze_query_indexes."
            ),
            "tags": {ToolTag.FULL.value},
            "annotations": _ann("Get Top Queries", READ_ONLY_NON_IDEMPOTENT),
            "timeout": 30.0,
            "meta": _META,
        },
        {
            "fn": analyze_query_indexes,
            "name": "analyze_query_indexes",
            "description": (
                "Recommend optimal indexes for a given list of SQL queries. "
                "method='dta' uses cost-based analysis with hypothetical indexes (hypopg required); "
                "method='llm' uses LLM-driven pattern analysis. "
                "Use analyze_workload_indexes instead if you want to optimize aggregate workload."
            ),
            "tags": {ToolTag.FULL.value},
            "annotations": _ann("Analyze Query Indexes", READ_ONLY_IDEMPOTENT),
            "timeout": 60.0,
            "meta": _META,
        },
        {
            "fn": analyze_workload_indexes,
            "name": "analyze_workload_indexes",
            "description": (
                "Recommend indexes based on the actual workload captured in pg_stat_statements. "
                "method='dta' uses cost-based analysis (hypopg required); method='llm' uses LLM-driven "
                "analysis. Use periodically to find missing indexes. "
                "Use analyze_query_indexes instead if you want to optimize specific queries."
            ),
            "tags": {ToolTag.FULL.value},
            "annotations": _ann("Analyze Workload Indexes", READ_ONLY_NON_IDEMPOTENT),
            "timeout": 60.0,
            "meta": _META,
        },
    ]


def _execute_sql_desc(*, unrestricted: bool) -> str:
    if unrestricted:
        return (
            "Execute ANY SQL statement (DDL, DML, DCL). Server is in FULL access with write_mode=True. "
            "Use with caution; prefer explain_query first for non-trivial SELECTs. "
            "Workflow: 1) list_objects, 2) get_object_details, 3) execute_sql."
        )
    return (
        "Execute a read-only SELECT query. DDL/DML/DCL statements are blocked. "
        "Workflow: 1) list_objects, 2) get_object_details, 3) execute_sql."
    )


def _list_objects_desc(access_mode: AccessMode) -> str:
    if access_mode == AccessMode.BASIC:
        return (
            "List objects (tables/views/sequences/extensions) in the 'public' schema. "
            "BASIC mode restricts access to 'public'. After listing, use get_object_details "
            "to examine structure before writing SQL."
        )
    return (
        "List objects (tables/views/sequences/extensions) in a specified schema. "
        "Use list_schemas to discover available schemas first."
    )


def _get_object_details_desc(access_mode: AccessMode) -> str:
    if access_mode == AccessMode.BASIC:
        return (
            "Show details (columns, constraints, indexes, metadata) for an object in the 'public' schema. "
            "Use this before writing SQL so you know the exact structure of the target object."
        )
    return (
        "Show details (columns, constraints, indexes, metadata) for a database object. "
        "Workflow: list_schemas -> list_objects -> get_object_details -> execute_sql."
    )


def _explain_query_desc() -> str:
    return (
        "Explain the execution plan of a SQL query. analyze=True actually runs the query to collect "
        "real statistics; use cautiously on large tables. hypothetical_indexes simulates index impact "
        "via hypopg without creating them. Workflow: explain_query -> add indexes / rewrite query -> execute_sql."
    )
