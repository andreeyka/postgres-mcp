"""Тесты для тулов list_objects и get_object_details."""

from unittest import mock

import pytest

from postgres_fastmcp.tools.basic import (
    get_object_details as get_object_details_mod,
    list_objects as list_objects_mod,
)
from postgres_fastmcp.tools.basic.get_object_details import get_object_details
from postgres_fastmcp.tools.basic.list_objects import list_objects


@pytest.mark.asyncio
async def test_list_objects_returns_rows(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.list_objects.return_value = [{"name": "users"}]
    monkeypatch.setattr(list_objects_mod, "ObjectsService", lambda **kw: fake_service)

    result = await list_objects(
        schema_name="public", object_type="table", ctx=make_ctx(db_mock)
    )

    assert result == [{"name": "users"}]
    fake_service.list_objects.assert_awaited_once_with(
        schema_name="public", object_type="table"
    )


@pytest.mark.asyncio
async def test_get_object_details_returns_details(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.get_object_details.return_value = {"columns": []}
    monkeypatch.setattr(
        get_object_details_mod, "ObjectsService", lambda **kw: fake_service
    )

    result = await get_object_details(
        schema_name="public",
        object_name="users",
        object_type="table",
        ctx=make_ctx(db_mock),
    )

    assert result == {"columns": []}
    fake_service.get_object_details.assert_awaited_once_with(
        schema_name="public", object_name="users", object_type="table"
    )
