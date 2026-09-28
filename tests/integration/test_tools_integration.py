# mypy: ignore-errors
"""Integration tests for MCP tools with real DB: Client(mcp).call_tool inside server lifespan.

Covers all tools and main call variants. Row tools are called with output="json"
and checked through structured_content ({"rows": [...], "row_count": N}):
- list_schemas (json, default table)
- execute_sql (SELECT with rows, SELECT returning 0 rows)
- list_objects (object_type: table, view, sequence, extension, 'Tables')
- get_object_details
- explain_query (default, analyze=True)
- analyze_db_health (all, list with any case)
- analyze_workload_indexes
- analyze_query_indexes
- get_top_queries (sort_by: total_time, mean_time, resources, avg)
"""

import pytest
from fastmcp import Client

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.server import create_server


def _tool_content(result: object) -> object:
    """Extract content from MCP call_tool result."""
    return result.data if hasattr(result, "data") else getattr(result, "content", None)


def _rows(result: object) -> list[dict]:
    """Rows of a tool called with output='json': structured_content is {'rows': [...], 'row_count': N}."""
    data = result.structured_content
    assert isinstance(data, dict), data
    assert data["row_count"] == len(data["rows"])
    return data["rows"]


@pytest.mark.asyncio
async def test_tools_list_schemas(integration_settings: Settings) -> None:
    """list_schemas returns rows with schema_name; public is present."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool("list_schemas", {"output": "json"})
    assert result.is_error is False
    assert "public" in {row["schema_name"] for row in _rows(result)}


@pytest.mark.asyncio
async def test_tools_list_schemas_table(integration_settings: Settings) -> None:
    """list_schemas default output is a Markdown table without structured content."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool("list_schemas", {})
    assert result.is_error is False
    assert result.structured_content is None
    text = result.content[0].text
    assert text.startswith("| schema_name |")
    assert "public" in text


@pytest.mark.asyncio
async def test_tools_execute_sql(integration_settings: Settings) -> None:
    """execute_sql tool runs SELECT 1 and returns the row."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool("execute_sql", {"sql": "SELECT 1 AS num", "output": "json"})
    assert result.is_error is False
    assert _rows(result) == [{"num": 1}]


@pytest.mark.asyncio
async def test_tools_execute_sql_empty_result(integration_settings: Settings) -> None:
    """execute_sql with query returning 0 rows returns an empty row list / '0 rows.'."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        as_json = await client.call_tool("execute_sql", {"sql": "SELECT 1 WHERE FALSE", "output": "json"})
        table = await client.call_tool("execute_sql", {"sql": "SELECT 1 WHERE FALSE"})
    assert as_json.is_error is False
    assert _rows(as_json) == []
    assert table.content[0].text == "0 rows."


@pytest.mark.asyncio
@pytest.mark.parametrize("object_type", ["table", "view", "sequence", "extension", "Tables"])
async def test_tools_list_objects(integration_settings: Settings, object_type: str) -> None:
    """list_objects returns a row list (may be empty) for each object type, plural/any case accepted."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "list_objects",
            {"schema_name": "public", "object_type": object_type, "output": "json"},
        )
    assert result.is_error is False
    assert isinstance(_rows(result), list)


@pytest.mark.asyncio
async def test_tools_get_object_details(integration_settings: Settings) -> None:
    """get_object_details returns header fields and column rows for information_schema.tables."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_object_details",
            {
                "schema_name": "information_schema",
                "object_name": "tables",
                "object_type": "view",
                "output": "json",
            },
        )
    assert result.is_error is False
    data = result.structured_content
    assert data["schema"] == "information_schema"
    assert data["name"] == "tables"
    assert "table_name" in {column["column"] for column in data["columns"]}


@pytest.mark.asyncio
async def test_tools_get_object_details_empty_table(integration_settings: Settings) -> None:
    """A table without columns exists: header and empty sections, not "Object not found"."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        await client.call_tool("execute_sql", {"sql": "DROP TABLE IF EXISTS od_empty"})
        await client.call_tool("execute_sql", {"sql": "CREATE TABLE od_empty ()"})
        table = await client.call_tool("get_object_details", {"schema_name": "public", "object_name": "od_empty"})
        as_json = await client.call_tool(
            "get_object_details", {"schema_name": "public", "object_name": "od_empty", "output": "json"}
        )
        as_view = await client.call_tool(
            "get_object_details",
            {"schema_name": "public", "object_name": "od_empty", "object_type": "view"},
            raise_on_error=False,
        )
        await client.call_tool("execute_sql", {"sql": "DROP TABLE IF EXISTS od_empty"})
    assert table.content[0].text == "schema: public\nname: od_empty\ntype: table"
    assert as_json.structured_content == {
        "schema": "public",
        "name": "od_empty",
        "type": "table",
        "columns": [],
        "constraints": [],
        "indexes": [],
    }
    assert as_view.is_error is True
    assert as_view.content[0].text == (
        'Object not found: public.od_empty (view). If it is a table, retry with object_type="table"; '
        "use list_objects to see existing objects."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("object_type", "message"),
    [
        (
            "table",
            'Object not found: public.od_ghost (table). If it is a view, retry with object_type="view"; '
            "use list_objects to see existing objects.",
        ),
        ("sequence", "Object not found: public.od_ghost (sequence). Use list_objects to see existing objects."),
        ("extension", "Object not found: od_ghost (extension). Use list_objects to see existing objects."),
    ],
)
async def test_tools_get_object_details_missing(integration_settings: Settings, object_type: str, message: str) -> None:
    """A missing object is an error with a hint; an extension is named without a schema."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_object_details",
            {"schema_name": "public", "object_name": "od_ghost", "object_type": object_type},
            raise_on_error=False,
        )
    assert result.is_error is True
    assert result.content[0].text == message


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
    """analyze_db_health with a list in any case (['Connection']) returns report."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool("analyze_db_health", {"health_type": ["Connection"]})
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
@pytest.mark.parametrize("sort_by", ["total_time", "mean_time", "resources", "avg"])
async def test_tools_get_top_queries(integration_settings: Settings, sort_by: str) -> None:
    """get_top_queries returns at most limit rows, or the install hint when pg_stat_statements is missing."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_top_queries",
            {"sort_by": sort_by, "limit": 5, "output": "json"},
            raise_on_error=False,
        )
    if result.is_error:
        assert "pg_stat_statements" in result.content[0].text
    else:
        assert len(_rows(result)) <= 5
