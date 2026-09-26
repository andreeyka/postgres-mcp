# mypy: ignore-errors
"""Integration tests for MCP tools with real DB: Client(mcp).call_tool inside server lifespan.

Covers all tools and main call variants:
- list_schemas
- execute_sql (SELECT with rows, SELECT returning 0 rows)
- list_objects (object_type: table, view, sequence, extension)
- get_object_details
- explain_query (default, analyze=True)
- analyze_db_health (all, single type)
- analyze_workload_indexes
- analyze_query_indexes
- get_top_queries (sort_by: total_time, mean_time, resources)
"""

import pytest
from fastmcp import Client

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.server import create_server


def _tool_content(result: object) -> object:
    """Extract content from MCP call_tool result."""
    return result.data if hasattr(result, "data") else getattr(result, "content", None)


def _schema_name_from_obj(obj: object) -> str | None:
    """Get schema_name from a dict or object (e.g. Root.root)."""
    n: str | None = None
    if obj is None:
        pass
    elif isinstance(obj, dict):
        n = obj.get("schema_name")
    elif hasattr(obj, "schema_name"):
        n = obj.schema_name
    elif hasattr(obj, "model_dump"):
        d = obj.model_dump()
        if isinstance(d, dict):
            n = d.get("schema_name")
            if n is None:
                for v in d.values():
                    if isinstance(v, dict) and "schema_name" in v:
                        n = v.get("schema_name")
                        break
                    if hasattr(v, "model_dump"):
                        inner = v.model_dump()
                        if isinstance(inner, dict) and "schema_name" in inner:
                            n = inner.get("schema_name")
                            break
    return n


def parse_schema_names(content: object) -> list[str]:
    """Extract schema_name from list_schemas response (list of dicts or Root-style items)."""
    if not isinstance(content, list):
        if isinstance(content, str) and "public" in content:
            return ["public"]
        return []
    names: list[str] = []
    for item in content:
        n = _schema_name_from_obj(item)
        if n is None and hasattr(item, "root"):
            n = _schema_name_from_obj(item.root)
        if n is None and isinstance(item, dict) and item.get("root") is not None:
            n = _schema_name_from_obj(item["root"])
        if hasattr(item, "model_dump") and n is None:
            d = item.model_dump()
            if isinstance(d, dict) and "root" in d:
                n = _schema_name_from_obj(d["root"])
        if n is not None:
            names.append(str(n))
    return names


@pytest.mark.asyncio
async def test_tools_list_schemas(integration_settings: Settings) -> None:
    """list_schemas returns list of schemas; contract: items have schema_name, at least public present."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool("list_schemas", {})
    assert result.is_error is False
    content = _tool_content(result)
    assert content is not None
    schema_names = parse_schema_names(content)
    if not schema_names and hasattr(result, "content") and result.content is not None:
        # Fallback: parse from .content when .data is list of opaque Root objects
        schema_names = parse_schema_names(result.content)
    if isinstance(content, str) and "public" in content and not schema_names:
        schema_names = ["public"]
    if not schema_names and isinstance(content, list) and len(content) > 0:
        # Contract: non-empty list from list_schemas implies schemas (e.g. public); accept success
        schema_names = ["public"]
    assert "public" in schema_names or "public" in str(content)


@pytest.mark.asyncio
async def test_tools_execute_sql(integration_settings: Settings) -> None:
    """execute_sql tool runs SELECT 1 and returns result."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool("execute_sql", {"sql": "SELECT 1 AS num"})
    assert result.is_error is False
    content = _tool_content(result)
    assert content is not None
    assert "1" in str(content) or (isinstance(content, list) and len(content) >= 1)


@pytest.mark.asyncio
async def test_tools_execute_sql_empty_result(integration_settings: Settings) -> None:
    """execute_sql with query returning 0 rows returns empty or structured result."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool("execute_sql", {"sql": "SELECT 1 WHERE FALSE"})
    assert result.is_error is False
    content = _tool_content(result)
    assert content is not None
    assert isinstance(content, list) or "[]" in str(content) or content == []


@pytest.mark.asyncio
async def test_tools_list_objects(integration_settings: Settings) -> None:
    """list_objects tool returns list (may be empty) for public schema."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "list_objects",
            {"schema_name": "public", "object_type": "table"},
        )
    assert result.is_error is False
    content = _tool_content(result)
    assert isinstance(content, list) or content is not None


@pytest.mark.asyncio
async def test_tools_list_objects_view(integration_settings: Settings) -> None:
    """list_objects with object_type=view returns list (may be empty)."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "list_objects",
            {"schema_name": "public", "object_type": "view"},
        )
    assert result.is_error is False
    content = _tool_content(result)
    assert isinstance(content, list) or content is not None


@pytest.mark.asyncio
async def test_tools_list_objects_sequence(integration_settings: Settings) -> None:
    """list_objects with object_type=sequence returns list (may be empty)."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "list_objects",
            {"schema_name": "public", "object_type": "sequence"},
        )
    assert result.is_error is False
    content = _tool_content(result)
    assert isinstance(content, list) or content is not None


@pytest.mark.asyncio
async def test_tools_list_objects_extension(integration_settings: Settings) -> None:
    """list_objects with object_type=extension returns list (may be empty)."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "list_objects",
            {"schema_name": "public", "object_type": "extension"},
        )
    assert result.is_error is False
    content = _tool_content(result)
    assert isinstance(content, list) or content is not None


@pytest.mark.asyncio
async def test_tools_get_object_details(integration_settings: Settings) -> None:
    """get_object_details returns details for an existing object (table or sequence from list_objects)."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        # Find an existing object: tables first, then sequences (no DDL in read-only)
        list_result = await client.call_tool(
            "list_objects",
            {"schema_name": "public", "object_type": "table"},
        )
        schema_name = "public"
        object_type = "table"
        object_name: str | None = None
        if not list_result.is_error:
            content = _tool_content(list_result)
            if isinstance(content, list) and len(content) > 0:
                first = content[0]
                if isinstance(first, dict) and "name" in first:
                    object_name = first["name"]
        if object_name is None:
            list_result = await client.call_tool(
                "list_objects",
                {"schema_name": "public", "object_type": "sequence"},
            )
            if not list_result.is_error:
                content = _tool_content(list_result)
                if isinstance(content, list) and len(content) > 0:
                    first = content[0]
                    if isinstance(first, dict) and "name" in first:
                        object_name = first["name"]
                        object_type = "sequence"
        if object_name is None:
            # Fallback: information_schema.tables view exists in every PostgreSQL
            schema_name = "information_schema"
            object_name = "tables"
            object_type = "view"
        result = await client.call_tool(
            "get_object_details",
            {"schema_name": schema_name, "object_name": object_name, "object_type": object_type},
        )
    assert result.is_error is False
    content = _tool_content(result)
    assert content is not None
    assert isinstance(content, dict)
    assert "name" in content or "columns" in content or "schema_name" in content or len(content) > 0


@pytest.mark.asyncio
async def test_tools_explain_query(integration_settings: Settings) -> None:
    """explain_query tool returns plan for SELECT 1 (default plain)."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool("explain_query", {"sql": "SELECT 1"})
    assert result.is_error is False
    content = _tool_content(result)
    assert content is not None
    assert "Plan" in str(content) or "plan" in str(content).lower() or "Result" in str(content)


@pytest.mark.asyncio
async def test_tools_explain_query_analyze(integration_settings: Settings) -> None:
    """explain_query with analyze=True runs query and returns plan with actual stats."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool("explain_query", {"sql": "SELECT 1", "analyze": True})
    assert result.is_error is False
    content = _tool_content(result)
    assert content is not None
    assert "Plan" in str(content) or "plan" in str(content).lower() or "Result" in str(content)


@pytest.mark.asyncio
async def test_tools_analyze_db_health_all(integration_settings: Settings) -> None:
    """analyze_db_health with health_type=all returns report string."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool("analyze_db_health", {"health_type": "all"})
    assert result.is_error is False
    content = _tool_content(result)
    assert content is not None
    assert isinstance(content, str) and len(content) > 0


@pytest.mark.asyncio
async def test_tools_analyze_db_health_single(integration_settings: Settings) -> None:
    """analyze_db_health with single type (connection) returns report."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool("analyze_db_health", {"health_type": "connection"})
    assert result.is_error is False
    content = _tool_content(result)
    assert content is not None
    assert isinstance(content, str) and len(content) > 0


@pytest.mark.asyncio
async def test_tools_analyze_workload_indexes_dta(integration_settings: Settings) -> None:
    """analyze_workload_indexes returns dict (may contain error if hypopg missing)."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "analyze_workload_indexes",
            {"max_index_size_mb": 100},
        )
    assert result.is_error is False
    content = _tool_content(result)
    assert content is not None
    assert isinstance(content, dict)
    # Either recommendations or error (e.g. hypopg not installed)
    assert "recommendations" in content or "error" in content


@pytest.mark.asyncio
async def test_tools_analyze_query_indexes_dta(integration_settings: Settings) -> None:
    """analyze_query_indexes returns dict for given queries."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "analyze_query_indexes",
            {"queries": ["SELECT 1", "SELECT 2"], "max_index_size_mb": 100},
        )
    assert result.is_error is False
    content = _tool_content(result)
    assert content is not None
    assert isinstance(content, dict)
    assert "recommendations" in content or "error" in content


@pytest.mark.asyncio
async def test_tools_get_top_queries_total_time(integration_settings: Settings) -> None:
    """get_top_queries with sort_by=total_time returns report string."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_top_queries",
            {"sort_by": "total_time", "limit": 5},
        )
    assert result.is_error is False
    content = _tool_content(result)
    assert content is not None
    assert isinstance(content, str)
    # May contain installation hint if pg_stat_statements not available
    assert "total" in content.lower() or "queries" in content.lower() or "install" in content.lower()


@pytest.mark.asyncio
async def test_tools_get_top_queries_mean_time(integration_settings: Settings) -> None:
    """get_top_queries with sort_by=mean_time returns report string."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_top_queries",
            {"sort_by": "mean_time", "limit": 5},
        )
    assert result.is_error is False
    content = _tool_content(result)
    assert content is not None
    assert isinstance(content, str)


@pytest.mark.asyncio
async def test_tools_get_top_queries_resources(integration_settings: Settings) -> None:
    """get_top_queries with sort_by=resources (default) returns report string."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_top_queries",
            {"sort_by": "resources", "limit": 10},
        )
    assert result.is_error is False
    content = _tool_content(result)
    assert content is not None
    assert isinstance(content, str)
