"""Тесты для тула list_schemas."""

from unittest import mock

import pytest

from postgres_fastmcp.tools.full import list_schemas as list_schemas_mod
from postgres_fastmcp.tools.full.list_schemas import list_schemas


@pytest.mark.asyncio
async def test_list_schemas_returns_rows(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.list_schemas.return_value = [{"name": "public"}]
    monkeypatch.setattr(list_schemas_mod, "SchemaService", lambda **kw: fake_service)

    result = await list_schemas(ctx=make_ctx(db_mock))

    assert result == [{"name": "public"}]
    fake_service.list_schemas.assert_awaited_once_with()
