"""Программная регистрация тулов ToolSet в LocalProvider через Tool.from_function + add_tool.

Описания, аннотации и таймауты строятся в момент регистрации по серверному потолку
(access_mode, write_mode, safe_sql_timeout из конфигурации БД).
"""

from importlib.metadata import (
    PackageNotFoundError,
    version as _pkg_version,
)
from typing import Any

from fastmcp.server.auth import AuthCheck
from fastmcp.server.providers import LocalProvider
from fastmcp.tools import Tool
from mcp.types import ToolAnnotations

from postgres_fastmcp.domains.db_access import DatabaseConfigPort
from postgres_fastmcp.postgres.security.driver import CLIENT_TIMEOUT_GRACE_SECONDS
from postgres_fastmcp.shared.enums import AccessMode, ToolTag
from postgres_fastmcp.tools.definitions import ToolSet
from postgres_fastmcp.tools.params import HEALTH_TYPE_VALUES


try:
    _VERSION = _pkg_version("postgres-fastmcp")
except PackageNotFoundError:
    _VERSION = "0.0.0"

_META: dict[str, Any] = {"version": _VERSION}

# Annotation presets for Tool.from_function(annotations=...)
# See https://gofastmcp.com/servers/tools — ToolAnnotations fields (snake_case since MCP SDK v2).
READ_ONLY_IDEMPOTENT: dict[str, bool] = {
    "read_only_hint": True,
    "destructive_hint": False,
    "idempotent_hint": True,
    "open_world_hint": True,
}
READ_ONLY_NON_IDEMPOTENT: dict[str, bool] = {
    "read_only_hint": True,
    "destructive_hint": False,
    "idempotent_hint": False,
    "open_world_hint": True,
}
# BASIC + write_mode: DML в public коммитится, но DDL отклоняется — запись без разрушения схемы.
WRITE_NON_DESTRUCTIVE: dict[str, bool] = {
    "read_only_hint": False,
    "destructive_hint": False,
    "idempotent_hint": False,
    "open_world_hint": True,
}
DESTRUCTIVE: dict[str, bool] = {
    "read_only_hint": False,
    "destructive_hint": True,
    "idempotent_hint": False,
    "open_world_hint": True,
}


# Запас поверх statement_timeout + клиентской страховки SafeSqlExecutor: первым должен
# срабатывать Postgres (QueryTimeoutError), а не таймаут тула в FastMCP.
_TOOL_TIMEOUT_MARGIN = 5.0


def _ann(title: str, preset: dict[str, bool]) -> ToolAnnotations:
    return ToolAnnotations(title=title, **preset)


def register_tools(
    provider: LocalProvider,
    toolset: ToolSet,
    *,
    ceiling: DatabaseConfigPort,
    full_tool_auth: AuthCheck | None = None,
) -> None:
    """Зарегистрировать все 9 тулов в провайдере.

    Видимость basic/full по тегам настраивает владелец провайдера (``disable(tags=...)``).

    Args:
        provider: Провайдер, в который добавляются тулы.
        toolset: Методы-тулы с доступом к БД.
        ceiling: Серверный потолок: от него зависят описания, аннотации и таймауты.
        full_tool_auth: Проверка прав на full-тулах (None — без проверки).
    """
    for spec in _all_tool_specs(toolset, ceiling, full_tool_auth):
        provider.add_tool(Tool.from_function(**spec))


def _all_tool_specs(
    toolset: ToolSet, ceiling: DatabaseConfigPort, full_tool_auth: AuthCheck | None
) -> list[dict[str, Any]]:
    specs = _basic_specs(toolset, ceiling) + _full_specs(toolset, full_tool_auth)
    for spec in specs:
        spec["timeout"] = _tool_timeout(spec["timeout"], ceiling)
    return specs


def _tool_timeout(base: float, ceiling: DatabaseConfigPort) -> float:
    """Таймаут тула: не короче базового и строго длиннее statement_timeout + страховки.

    Считается от серверного потолка независимо от режима: даже при FULL + write_mode свой
    ``access_resolver`` может сузить конкретный запрос до read-only, и тот получит
    SafeSqlExecutor со statement_timeout = safe_sql_timeout — тул-таймаут должен это пережить.
    """
    derived = ceiling.safe_sql_timeout + CLIENT_TIMEOUT_GRACE_SECONDS + _TOOL_TIMEOUT_MARGIN
    return max(base, derived)


def _basic_specs(toolset: ToolSet, ceiling: DatabaseConfigPort) -> list[dict[str, Any]]:
    # Аннотации execute_sql отражают потолок: клиент решает, спрашивать ли подтверждение.
    if ceiling.access_mode == AccessMode.FULL and ceiling.write_mode:
        execute_preset = DESTRUCTIVE
    elif ceiling.write_mode:
        execute_preset = WRITE_NON_DESTRUCTIVE
    else:
        execute_preset = READ_ONLY_NON_IDEMPOTENT
    return [
        {
            "fn": toolset.execute_sql,
            "name": "execute_sql",
            "output_schema": None,
            "description": _execute_sql_desc(),
            "tags": {ToolTag.BASIC.value},
            "annotations": _ann("Execute SQL", execute_preset),
            "timeout": 30.0,
            "meta": _META,
        },
        {
            "fn": toolset.list_objects,
            "name": "list_objects",
            "output_schema": None,
            "description": _list_objects_desc(ceiling.access_mode),
            "tags": {ToolTag.BASIC.value},
            "annotations": _ann("List Objects", READ_ONLY_IDEMPOTENT),
            "timeout": 30.0,
            "meta": _META,
        },
        {
            "fn": toolset.get_object_details,
            "name": "get_object_details",
            "output_schema": None,
            "description": _get_object_details_desc(ceiling.access_mode),
            "tags": {ToolTag.BASIC.value},
            "annotations": _ann("Get Object Details", READ_ONLY_IDEMPOTENT),
            "timeout": 30.0,
            "meta": _META,
        },
        {
            "fn": toolset.explain_query,
            "name": "explain_query",
            "description": _explain_query_desc(),
            "tags": {ToolTag.BASIC.value},
            "annotations": _ann("Explain Query", READ_ONLY_IDEMPOTENT),
            "timeout": 30.0,
            "meta": _META,
        },
    ]


def _full_specs(toolset: ToolSet, full_tool_auth: AuthCheck | None) -> list[dict[str, Any]]:
    return [
        {
            "fn": toolset.list_schemas,
            "name": "list_schemas",
            "output_schema": None,
            "description": (
                "Lists all schemas in the PostgreSQL database. Use this first to discover "
                "available namespaces before listing objects in a specific schema."
            ),
            "tags": {ToolTag.FULL.value},
            "annotations": _ann("List Schemas", READ_ONLY_IDEMPOTENT),
            "timeout": 30.0,
            "meta": _META,
            "auth": full_tool_auth,
        },
        {
            "fn": toolset.analyze_db_health,
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
            "auth": full_tool_auth,
        },
        {
            "fn": toolset.get_top_queries,
            "name": "get_top_queries",
            "output_schema": None,
            "description": (
                "Report the slowest or most resource-intensive queries from pg_stat_statements. "
                "The pg_stat_statements extension must be enabled. Workflow: get_top_queries -> "
                "explain_query -> analyze_query_indexes."
            ),
            "tags": {ToolTag.FULL.value},
            "annotations": _ann("Get Top Queries", READ_ONLY_NON_IDEMPOTENT),
            "timeout": 30.0,
            "meta": _META,
            "auth": full_tool_auth,
        },
        {
            "fn": toolset.analyze_query_indexes,
            "name": "analyze_query_indexes",
            "description": (
                "Recommend optimal indexes for a given list of SQL queries using cost-based analysis "
                "with hypothetical indexes (the hypopg extension is required). "
                "Use analyze_workload_indexes instead if you want to optimize aggregate workload."
            ),
            "tags": {ToolTag.FULL.value},
            "annotations": _ann("Analyze Query Indexes", READ_ONLY_IDEMPOTENT),
            "timeout": 60.0,
            "meta": _META,
            "auth": full_tool_auth,
        },
        {
            "fn": toolset.analyze_workload_indexes,
            "name": "analyze_workload_indexes",
            "description": (
                "Recommend indexes based on the actual workload captured in pg_stat_statements, "
                "using cost-based analysis with hypothetical indexes (hypopg required). "
                "Use periodically to find missing indexes. "
                "Use analyze_query_indexes instead if you want to optimize specific queries."
            ),
            "tags": {ToolTag.FULL.value},
            "annotations": _ann("Analyze Workload Indexes", READ_ONLY_NON_IDEMPOTENT),
            "timeout": 60.0,
            "meta": _META,
            "auth": full_tool_auth,
        },
    ]


def _execute_sql_desc() -> str:
    # Описание не зависит от потолка: права запроса могут быть уже серверных (сужение по токену).
    return (
        "Execute a SQL statement. The server enforces the access policy of the current request: "
        "in read-only mode only SELECT, EXPLAIN and SHOW are accepted; with basic write access "
        "INSERT, UPDATE and DELETE on the public schema are also accepted and committed, and DDL is "
        "rejected except CREATE EXTENSION hypopg / pg_stat_statements; with full write access any "
        "statement runs. A rejected statement returns an explicit error. "
        "Workflow: list_objects -> get_object_details -> execute_sql."
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
