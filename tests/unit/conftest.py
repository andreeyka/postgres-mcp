# mypy: ignore-errors
"""Shared fixtures for unit tests: mock executor and a per-request DbAccess."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from postgres_fastmcp.domains.db_access import DbAccess
from postgres_fastmcp.shared.enums import AccessMode


_DATABASE_ENV = {
    "MCP_DATABASE_HOST": "localhost",
    "MCP_DATABASE_PORT": "5432",
    "MCP_DATABASE_USER": "u",
    "MCP_DATABASE_PASSWORD": "p",
    "MCP_DATABASE_NAME": "d",
}


@pytest.fixture(autouse=True)
def _database_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тестовое подключение MCP_DATABASE_* не зависит от окружения shell.

    Тест переопределяет значения своим monkeypatch.setenv/delenv: этот fixture выполняется раньше.
    """
    for name, value in _DATABASE_ENV.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def mock_executor() -> AsyncMock:
    """Mock QueryExecutorPort (sql_driver) used by all services."""
    executor = AsyncMock()
    executor.execute = AsyncMock(return_value=[])
    executor.render = MagicMock(side_effect=lambda q, p: q)
    return executor


@pytest.fixture
def mock_db_access(mock_executor: AsyncMock) -> MagicMock:
    """Mock DbAccess (DbAccessPort): sql_driver и catalog_driver — один mock_executor."""
    db = MagicMock(spec=DbAccess)
    db.sql_driver = mock_executor
    db.catalog_driver = mock_executor
    db.connection_id = "test://localhost:5432/testdb"
    db.access_mode = AccessMode.FULL
    db.write_mode = False
    db.table_prefix = None
    return db
