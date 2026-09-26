# mypy: ignore-errors
"""Shared fixtures for unit tests: mock executor and a per-request DbAccess."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.domains.db_access import DbAccess


@pytest.fixture
def mock_executor() -> AsyncMock:
    """Mock QueryExecutorPort (sql_driver) used by all services."""
    executor = AsyncMock()
    executor.execute = AsyncMock(return_value=[])
    executor.render = MagicMock(side_effect=lambda q, p: q)
    return executor


@pytest.fixture
def mock_db_access(mock_executor: AsyncMock) -> MagicMock:
    """Mock DbAccess (DbAccessPort) with preconfigured sql_driver for service tests."""
    db = MagicMock(spec=DbAccess)
    db.sql_driver = mock_executor
    db.connection_id = "test://localhost:5432/testdb"
    db.access_mode = AccessMode.FULL
    db.write_mode = False
    db.table_prefix = None
    return db
