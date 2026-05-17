"""Тесты для тула get_top_queries."""

from unittest import mock

import pytest

from postgres_fastmcp.tools.full import get_top_queries as get_top_queries_mod
from postgres_fastmcp.tools.full.get_top_queries import get_top_queries


@pytest.mark.asyncio
async def test_get_top_queries_default(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.get_top_queries.return_value = "REPORT"
    monkeypatch.setattr(
        get_top_queries_mod, "TopQueriesService", lambda **kw: fake_service
    )

    result = await get_top_queries(ctx=make_ctx(db_mock))

    assert result == "REPORT"
    fake_service.get_top_queries.assert_awaited_once_with(
        sort_by="resources", limit=10
    )


@pytest.mark.asyncio
async def test_get_top_queries_custom_args(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.get_top_queries.return_value = "REPORT2"
    monkeypatch.setattr(
        get_top_queries_mod, "TopQueriesService", lambda **kw: fake_service
    )

    result = await get_top_queries(
        sort_by="total_time", limit=5, ctx=make_ctx(db_mock)
    )

    assert result == "REPORT2"
    fake_service.get_top_queries.assert_awaited_once_with(
        sort_by="total_time", limit=5
    )
