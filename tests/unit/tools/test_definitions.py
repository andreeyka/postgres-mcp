# mypy: ignore-errors
"""Тесты всех MCP-тулов: тонкие функции из tools.definitions."""

from unittest import mock

import pytest

from postgres_fastmcp.domains.health.database_health import HealthType
from postgres_fastmcp.domains.querying import SUCCESS_NO_ROWS
from postgres_fastmcp.shared.errors import ObjectNotFoundError
from postgres_fastmcp.tools import definitions as defs


def _text(result) -> str:
    """Текст единственного TextContent из ToolResult."""
    [block] = result.content
    return block.text


@pytest.mark.asyncio
async def test_execute_sql_returns_markdown_table_by_default(monkeypatch, db_mock, make_ctx) -> None:
    fake_querying = mock.AsyncMock()
    fake_querying.execute_sql.return_value = [{"col": 1}]
    monkeypatch.setattr(defs, "querying", fake_querying)

    result = await defs.execute_sql(sql="SELECT 1", ctx=make_ctx(db_mock))

    assert _text(result) == "| col |\n| --- |\n| 1 |\n\n1 rows."
    assert result.structured_content is None
    fake_querying.execute_sql.assert_awaited_once_with(db_mock, "SELECT 1")


@pytest.mark.asyncio
async def test_execute_sql_json_output(monkeypatch, db_mock, make_ctx) -> None:
    fake_querying = mock.AsyncMock()
    fake_querying.execute_sql.return_value = [{"col": 1}]
    monkeypatch.setattr(defs, "querying", fake_querying)

    result = await defs.execute_sql(sql="SELECT 1", output="json", ctx=make_ctx(db_mock))

    assert result.structured_content == {"rows": [{"col": 1}], "row_count": 1}


@pytest.mark.asyncio
async def test_execute_sql_statement_without_rows(monkeypatch, db_mock, make_ctx) -> None:
    fake_querying = mock.AsyncMock()
    fake_querying.execute_sql.return_value = None
    monkeypatch.setattr(defs, "querying", fake_querying)

    table = await defs.execute_sql(sql="INSERT INTO t VALUES (1)", ctx=make_ctx(db_mock))
    as_json = await defs.execute_sql(sql="INSERT INTO t VALUES (1)", output="json", ctx=make_ctx(db_mock))

    assert _text(table) == f"{SUCCESS_NO_ROWS}\n\n0 rows."
    assert as_json.structured_content == {"rows": [], "row_count": 0}


@pytest.mark.asyncio
async def test_execute_sql_propagates_errors(monkeypatch, db_mock, make_ctx) -> None:
    fake_querying = mock.AsyncMock()
    fake_querying.execute_sql.side_effect = RuntimeError("boom")
    monkeypatch.setattr(defs, "querying", fake_querying)

    with pytest.raises(RuntimeError, match="boom"):
        await defs.execute_sql(sql="SELECT 1", ctx=make_ctx(db_mock))


@pytest.mark.asyncio
async def test_explain_query_plain(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.explain.return_value = "PLAN"
    monkeypatch.setattr(defs, "ExplainService", lambda **kw: fake_service)

    result = await defs.explain_query(sql="SELECT 1", ctx=make_ctx(db_mock))

    assert result == "PLAN"
    fake_service.explain.assert_awaited_once_with("SELECT 1", analyze=False, hypothetical_indexes=None)


@pytest.mark.asyncio
async def test_explain_query_with_analyze_and_hypothetical(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.explain.return_value = "ANALYZED"
    monkeypatch.setattr(defs, "ExplainService", lambda **kw: fake_service)

    indexes = [{"table": "t", "columns": ["c"]}]
    result = await defs.explain_query(
        sql="SELECT 1",
        analyze=True,
        hypothetical_indexes=indexes,
        ctx=make_ctx(db_mock),
    )

    assert result == "ANALYZED"
    fake_service.explain.assert_awaited_once_with("SELECT 1", analyze=True, hypothetical_indexes=indexes)


@pytest.mark.asyncio
async def test_list_objects_returns_rows(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.list_objects.return_value = [{"name": "users"}]
    monkeypatch.setattr(defs, "CatalogService", lambda **kw: fake_service)

    result = await defs.list_objects(schema_name="public", object_type="table", output="json", ctx=make_ctx(db_mock))

    assert result.structured_content == {"rows": [{"name": "users"}], "row_count": 1}
    fake_service.list_objects.assert_awaited_once_with(schema_name="public", object_type="table")


@pytest.mark.asyncio
async def test_get_object_details_table_sections(monkeypatch, db_mock, make_ctx) -> None:
    """Таблица: basic уходит в заголовок, columns/constraints/indexes — разделы."""
    fake_service = mock.AsyncMock()
    fake_service.get_object_details.return_value = {
        "basic": {"schema": "public", "name": "users", "type": "table"},
        "columns": [{"column": "id", "data_type": "integer", "is_nullable": "NO", "default": None}],
        "constraints": [{"name": "users_pkey", "type": "PRIMARY KEY", "columns": ["id"]}],
        "indexes": [],
    }
    monkeypatch.setattr(defs, "CatalogService", lambda **kw: fake_service)

    table = await defs.get_object_details(schema_name="public", object_name="users", ctx=make_ctx(db_mock))
    as_json = await defs.get_object_details(
        schema_name="public", object_name="users", output="json", ctx=make_ctx(db_mock)
    )

    text = _text(table)
    assert text.startswith("schema: public\nname: users\ntype: table\n\n### columns\n")
    assert "### constraints" in text
    assert "### indexes" not in text
    assert as_json.structured_content == {
        "schema": "public",
        "name": "users",
        "type": "table",
        "columns": [{"column": "id", "data_type": "integer", "is_nullable": "NO", "default": None}],
        "constraints": [{"name": "users_pkey", "type": "PRIMARY KEY", "columns": ["id"]}],
        "indexes": [],
    }
    fake_service.get_object_details.assert_awaited_with(schema_name="public", object_name="users", object_type="table")


@pytest.mark.asyncio
async def test_get_object_details_sequence_is_header_only(monkeypatch, db_mock, make_ctx) -> None:
    """Последовательность: все поля скалярные, поэтому только заголовок."""
    fake_service = mock.AsyncMock()
    fake_service.get_object_details.return_value = {
        "schema": "public",
        "name": "users_id_seq",
        "data_type": "bigint",
        "start_value": 1,
        "increment": 1,
    }
    monkeypatch.setattr(defs, "CatalogService", lambda **kw: fake_service)

    result = await defs.get_object_details(
        schema_name="public", object_name="users_id_seq", object_type="sequence", ctx=make_ctx(db_mock)
    )

    assert _text(result) == (
        "schema: public\nname: users_id_seq\ntype: sequence\ndata_type: bigint\nstart_value: 1\nincrement: 1"
    )


@pytest.mark.parametrize("output", ["table", "json"])
@pytest.mark.parametrize(
    ("object_type", "details"),
    [
        (
            "table",
            {
                "basic": {"schema": "public", "name": "ghost", "type": "table"},
                "columns": [],
                "constraints": [],
                "indexes": [],
            },
        ),
        ("sequence", {}),
    ],
)
@pytest.mark.asyncio
async def test_get_object_details_missing_object_raises(
    monkeypatch, db_mock, make_ctx, object_type: str, details: dict, output: str
) -> None:
    """Каталог вернул только то, что тул добавляет сам, и пустые разделы: объекта нет."""
    fake_service = mock.AsyncMock()
    fake_service.get_object_details.return_value = details
    monkeypatch.setattr(defs, "CatalogService", lambda **kw: fake_service)

    with pytest.raises(
        ObjectNotFoundError, match=rf"Object not found: public\.ghost \({object_type}\)\. Use list_objects"
    ):
        await defs.get_object_details(
            schema_name="public", object_name="ghost", object_type=object_type, output=output, ctx=make_ctx(db_mock)
        )


@pytest.mark.parametrize("output", ["table", "json"])
@pytest.mark.asyncio
async def test_get_object_details_missing_extension_has_no_schema(monkeypatch, db_mock, make_ctx, output: str) -> None:
    """Расширения не принадлежат схеме: в сообщении только имя."""
    fake_service = mock.AsyncMock()
    fake_service.get_object_details.return_value = {}
    monkeypatch.setattr(defs, "CatalogService", lambda **kw: fake_service)

    with pytest.raises(ObjectNotFoundError) as exc_info:
        await defs.get_object_details(
            schema_name="public", object_name="ghost", object_type="extension", output=output, ctx=make_ctx(db_mock)
        )

    assert str(exc_info.value) == "Object not found: ghost (extension). Use list_objects to see existing objects."


@pytest.mark.asyncio
async def test_get_object_details_extension_found(monkeypatch, db_mock, make_ctx) -> None:
    """Расширение с версией — найдено, хотя разделов нет."""
    fake_service = mock.AsyncMock()
    fake_service.get_object_details.return_value = {"name": "hypopg", "version": "1.4", "relocatable": True}
    monkeypatch.setattr(defs, "CatalogService", lambda **kw: fake_service)

    result = await defs.get_object_details(
        schema_name="public", object_name="hypopg", object_type="extension", ctx=make_ctx(db_mock)
    )

    assert "version: 1.4" in _text(result)


@pytest.mark.asyncio
async def test_list_schemas_returns_rows(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.list_schemas.return_value = [{"schema_name": "public"}]
    monkeypatch.setattr(defs, "CatalogService", lambda **kw: fake_service)

    result = await defs.list_schemas(ctx=make_ctx(db_mock))

    assert _text(result) == "| schema_name |\n| --- |\n| public |\n\n1 rows."
    fake_service.list_schemas.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_analyze_db_health_default(monkeypatch, db_mock, make_ctx) -> None:
    fake_tool = mock.AsyncMock()
    fake_tool.health.return_value = "OK"
    monkeypatch.setattr(defs, "DatabaseHealthAnalyzer", lambda sql_driver: fake_tool)

    result = await defs.analyze_db_health(ctx=make_ctx(db_mock))

    assert result == "OK"
    fake_tool.health.assert_awaited_once_with(health_type="all")


@pytest.mark.asyncio
async def test_analyze_db_health_custom_type(monkeypatch, db_mock, make_ctx) -> None:
    fake_tool = mock.AsyncMock()
    fake_tool.health.return_value = "INDEX_REPORT"
    monkeypatch.setattr(defs, "DatabaseHealthAnalyzer", lambda sql_driver: fake_tool)

    result = await defs.analyze_db_health(
        health_type=(HealthType.INDEX, HealthType.VACUUM),
        ctx=make_ctx(db_mock),
    )

    assert result == "INDEX_REPORT"
    fake_tool.health.assert_awaited_once_with(health_type="index,vacuum")


@pytest.mark.asyncio
async def test_get_top_queries_default(monkeypatch, db_mock, make_ctx) -> None:
    fake_top_queries = mock.AsyncMock()
    fake_top_queries.get_top_queries.return_value = [{"query": "SELECT 1", "calls": 3}]
    monkeypatch.setattr(defs, "top_queries", fake_top_queries)

    result = await defs.get_top_queries(ctx=make_ctx(db_mock))

    assert _text(result) == "| query | calls |\n| --- | --- |\n| SELECT 1 | 3 |\n\n1 rows."
    fake_top_queries.get_top_queries.assert_awaited_once_with(db_mock, sort_by="resources", limit=10)


@pytest.mark.asyncio
async def test_get_top_queries_custom_args(monkeypatch, db_mock, make_ctx) -> None:
    fake_top_queries = mock.AsyncMock()
    fake_top_queries.get_top_queries.return_value = []
    monkeypatch.setattr(defs, "top_queries", fake_top_queries)

    result = await defs.get_top_queries(sort_by="total_time", limit=5, output="json", ctx=make_ctx(db_mock))

    assert result.structured_content == {"rows": [], "row_count": 0}
    fake_top_queries.get_top_queries.assert_awaited_once_with(db_mock, sort_by="total_time", limit=5)


@pytest.mark.asyncio
async def test_analyze_workload_indexes_default(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.analyze_workload_indexes.return_value = {"recommendations": []}
    monkeypatch.setattr(defs, "IndexAnalysisService", lambda **kw: fake_service)

    result = await defs.analyze_workload_indexes(ctx=make_ctx(db_mock))

    assert result == {"recommendations": []}
    fake_service.analyze_workload_indexes.assert_awaited_once_with(max_index_size_mb=10000)


@pytest.mark.asyncio
async def test_analyze_query_indexes_default(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.analyze_query_indexes.return_value = {"recommendations": ["idx"]}
    monkeypatch.setattr(defs, "IndexAnalysisService", lambda **kw: fake_service)

    result = await defs.analyze_query_indexes(queries=["SELECT 1"], ctx=make_ctx(db_mock))

    assert result == {"recommendations": ["idx"]}
    fake_service.analyze_query_indexes.assert_awaited_once_with(queries=["SELECT 1"], max_index_size_mb=10000)
