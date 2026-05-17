"""Тесты для тула explain_query."""

from unittest import mock

import pytest

from postgres_fastmcp.tools.basic import explain_query as explain_query_mod
from postgres_fastmcp.tools.basic.explain_query import explain_query


@pytest.mark.asyncio
async def test_explain_query_plain(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.explain.return_value = "PLAN"
    monkeypatch.setattr(explain_query_mod, "ExplainService", lambda **kw: fake_service)

    result = await explain_query(sql="SELECT 1", ctx=make_ctx(db_mock))

    assert result == "PLAN"
    fake_service.explain.assert_awaited_once_with(
        "SELECT 1", analyze=False, hypothetical_indexes=None
    )


@pytest.mark.asyncio
async def test_explain_query_with_analyze_and_hypothetical(
    monkeypatch, db_mock, make_ctx
) -> None:
    fake_service = mock.AsyncMock()
    fake_service.explain.return_value = "ANALYZED"
    monkeypatch.setattr(explain_query_mod, "ExplainService", lambda **kw: fake_service)

    indexes = [{"table": "t", "columns": ["c"]}]
    result = await explain_query(
        sql="SELECT 1",
        analyze=True,
        hypothetical_indexes=indexes,
        ctx=make_ctx(db_mock),
    )

    assert result == "ANALYZED"
    fake_service.explain.assert_awaited_once_with(
        "SELECT 1", analyze=True, hypothetical_indexes=indexes
    )
