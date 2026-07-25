# mypy: ignore-errors
"""Тесты всех MCP-тулов: тонкие функции из tools.definitions."""

from unittest import mock

import pytest

from postgres_fastmcp.tools import definitions as defs


@pytest.mark.asyncio
async def test_execute_sql_returns_rows(monkeypatch, db_mock, make_ctx) -> None:
    fake_querying = mock.AsyncMock()
    fake_querying.execute_sql.return_value = [{"col": 1}]
    monkeypatch.setattr(defs, "querying", fake_querying)

    result = await defs.execute_sql(sql="SELECT 1", ctx=make_ctx(db_mock))

    assert result == [{"col": 1}]
    fake_querying.execute_sql.assert_awaited_once_with(db_mock, "SELECT 1")


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

    result = await defs.list_objects(schema_name="public", object_type="table", ctx=make_ctx(db_mock))

    assert result == [{"name": "users"}]
    fake_service.list_objects.assert_awaited_once_with(schema_name="public", object_type="table")


@pytest.mark.asyncio
async def test_get_object_details_returns_details(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.get_object_details.return_value = {"columns": []}
    monkeypatch.setattr(defs, "CatalogService", lambda **kw: fake_service)

    result = await defs.get_object_details(
        schema_name="public",
        object_name="users",
        object_type="table",
        ctx=make_ctx(db_mock),
    )

    assert result == {"columns": []}
    fake_service.get_object_details.assert_awaited_once_with(
        schema_name="public", object_name="users", object_type="table"
    )


@pytest.mark.asyncio
async def test_list_schemas_returns_rows(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.list_schemas.return_value = [{"name": "public"}]
    monkeypatch.setattr(defs, "CatalogService", lambda **kw: fake_service)

    result = await defs.list_schemas(ctx=make_ctx(db_mock))

    assert result == [{"name": "public"}]
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

    result = await defs.analyze_db_health(health_type="index", ctx=make_ctx(db_mock))

    assert result == "INDEX_REPORT"
    fake_tool.health.assert_awaited_once_with(health_type="index")


@pytest.mark.asyncio
async def test_get_top_queries_default(monkeypatch, db_mock, make_ctx) -> None:
    fake_top_queries = mock.AsyncMock()
    fake_top_queries.get_top_queries.return_value = "REPORT"
    monkeypatch.setattr(defs, "top_queries", fake_top_queries)

    result = await defs.get_top_queries(ctx=make_ctx(db_mock))

    assert result == "REPORT"
    fake_top_queries.get_top_queries.assert_awaited_once_with(db_mock, sort_by="resources", limit=10)


@pytest.mark.asyncio
async def test_get_top_queries_custom_args(monkeypatch, db_mock, make_ctx) -> None:
    fake_top_queries = mock.AsyncMock()
    fake_top_queries.get_top_queries.return_value = "REPORT2"
    monkeypatch.setattr(defs, "top_queries", fake_top_queries)

    result = await defs.get_top_queries(sort_by="total_time", limit=5, ctx=make_ctx(db_mock))

    assert result == "REPORT2"
    fake_top_queries.get_top_queries.assert_awaited_once_with(db_mock, sort_by="total_time", limit=5)


@pytest.mark.asyncio
async def test_analyze_workload_indexes_default(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.analyze_workload_indexes.return_value = {"recommendations": []}
    monkeypatch.setattr(defs, "IndexAnalysisService", lambda **kw: fake_service)

    ctx = make_ctx(db_mock)
    result = await defs.analyze_workload_indexes(ctx=ctx)

    assert result == {"recommendations": []}
    fake_service.analyze_workload_indexes.assert_awaited_once_with(method="dta", max_index_size_mb=10000, ctx=ctx)


@pytest.mark.asyncio
async def test_analyze_query_indexes_default(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.analyze_query_indexes.return_value = {"recommendations": ["idx"]}
    monkeypatch.setattr(defs, "IndexAnalysisService", lambda **kw: fake_service)

    ctx = make_ctx(db_mock)
    result = await defs.analyze_query_indexes(queries=["SELECT 1"], ctx=ctx)

    assert result == {"recommendations": ["idx"]}
    fake_service.analyze_query_indexes.assert_awaited_once_with(
        method="dta",
        queries=["SELECT 1"],
        max_index_size_mb=10000,
        ctx=ctx,
    )
