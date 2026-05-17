"""Тесты для тулов analyze_workload_indexes и analyze_query_indexes."""

from unittest import mock

import pytest

from postgres_fastmcp.tools.full import (
    analyze_query_indexes as analyze_query_indexes_mod,
    analyze_workload_indexes as analyze_workload_indexes_mod,
)
from postgres_fastmcp.tools.full.analyze_query_indexes import analyze_query_indexes
from postgres_fastmcp.tools.full.analyze_workload_indexes import analyze_workload_indexes


@pytest.mark.asyncio
async def test_analyze_workload_indexes_default(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.analyze_workload_indexes.return_value = {"recommendations": []}
    monkeypatch.setattr(
        analyze_workload_indexes_mod,
        "IndexAnalysisService",
        lambda **kw: fake_service,
    )

    ctx = make_ctx(db_mock)
    result = await analyze_workload_indexes(ctx=ctx)

    assert result == {"recommendations": []}
    fake_service.analyze_workload_indexes.assert_awaited_once_with(
        method="dta", max_index_size_mb=10000, ctx=ctx
    )


@pytest.mark.asyncio
async def test_analyze_query_indexes_default(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.analyze_query_indexes.return_value = {"recommendations": ["idx"]}
    monkeypatch.setattr(
        analyze_query_indexes_mod,
        "IndexAnalysisService",
        lambda **kw: fake_service,
    )

    ctx = make_ctx(db_mock)
    result = await analyze_query_indexes(queries=["SELECT 1"], ctx=ctx)

    assert result == {"recommendations": ["idx"]}
    fake_service.analyze_query_indexes.assert_awaited_once_with(
        method="dta",
        queries=["SELECT 1"],
        max_index_size_mb=10000,
        ctx=ctx,
    )
