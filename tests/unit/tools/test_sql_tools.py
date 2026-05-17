"""Тесты для тула execute_sql."""

from unittest import mock

import pytest

from postgres_fastmcp.tools.basic import execute_sql as execute_sql_mod
from postgres_fastmcp.tools.basic.execute_sql import execute_sql


@pytest.mark.asyncio
async def test_execute_sql_returns_rows(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.execute_sql.return_value = [{"col": 1}]
    monkeypatch.setattr(
        execute_sql_mod, "SqlExecutionService", lambda **kw: fake_service
    )

    result = await execute_sql(sql="SELECT 1", ctx=make_ctx(db_mock))

    assert result == [{"col": 1}]
    fake_service.execute_sql.assert_awaited_once_with("SELECT 1")


@pytest.mark.asyncio
async def test_execute_sql_propagates_errors(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.execute_sql.side_effect = RuntimeError("boom")
    monkeypatch.setattr(
        execute_sql_mod, "SqlExecutionService", lambda **kw: fake_service
    )

    with pytest.raises(RuntimeError, match="boom"):
        await execute_sql(sql="SELECT 1", ctx=make_ctx(db_mock))
