"""Общие фикстуры для тестов тулов."""

from unittest import mock

import pytest

from postgres_fastmcp.domains.db_access import DbAccess
from postgres_fastmcp.tools.definitions import ToolSet


@pytest.fixture
def db_mock() -> mock.AsyncMock:
    """Готовый AsyncMock на роль DbAccessPort; spec=DbAccess ловит обращения к полям, которых у порта нет."""
    return mock.AsyncMock(spec=DbAccess)


@pytest.fixture
def toolset(db_mock: mock.AsyncMock) -> ToolSet:
    """ToolSet, который на каждый вызов тула отдаёт db_mock."""
    return ToolSet(get_db=lambda: db_mock)
