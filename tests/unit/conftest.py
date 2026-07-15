# mypy: ignore-errors
"""Shared fixtures for unit tests: mock executor and DbAccessService."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from postgres_fastmcp.enums import AccessMode
from postgres_fastmcp.services.db_access_service import DbAccessService


@pytest.fixture
def mock_executor() -> AsyncMock:
    """Mock QueryExecutorPort (sql_driver) used by all services."""
    executor = AsyncMock()
    executor.execute = AsyncMock(return_value=[])
    executor.render = MagicMock(side_effect=lambda q, p: q)
    return executor


@pytest.fixture
def mock_db_access(mock_executor: AsyncMock) -> MagicMock:
    """Mock DbAccessService with preconfigured sql_driver for service tests."""
    db = MagicMock(spec=DbAccessService)
    db.sql_driver = mock_executor
    db.connection_id = "test://localhost:5432/testdb"
    db.access_mode = AccessMode.FULL
    db.write_mode = False
    db.table_prefix = None
    db.config = MagicMock()
    db.config.table_prefix = None
    return db
