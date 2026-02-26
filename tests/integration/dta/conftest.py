# mypy: ignore-errors
"""Fixtures for DTA integration tests: hypopg check, DbAccessService."""

import logging
from typing import AsyncGenerator

import pytest

from postgres_fastmcp.services.db_access_service import DbAccessService


logger = logging.getLogger(__name__)


@pytest.fixture
async def db_service_with_hypopg(
    db_service_full: DbAccessService,
) -> AsyncGenerator[DbAccessService, None]:
    """DbAccessService with hypopg extension; skip if hypopg not available."""
    sql = db_service_full.sql_driver
    try:
        # pg_stat_statements optional for DTA query_list mode
        await sql.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements", readonly=False)
    except Exception as e:
        logger.warning("pg_stat_statements: %s", e)
    try:
        await sql.execute("CREATE EXTENSION IF NOT EXISTS hypopg", readonly=False)
    except Exception as e:
        logger.warning("hypopg not available: %s", e)
        pytest.skip("hypopg extension is not available - required for DTA tests")
    yield db_service_full
