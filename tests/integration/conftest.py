# mypy: ignore-errors
"""Fixtures for integration tests: settings and DbAccessService from Docker PostgreSQL."""

from typing import Generator

import pytest

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.domains.db_access import DbAccessService
from postgres_fastmcp.shared.enums import AccessMode


@pytest.fixture
def integration_settings(
    test_postgres_connection_string: tuple[str, str],
) -> Settings:
    """Settings with database pointing to the test PostgreSQL (access_mode=full, write_mode=True)."""
    connection_string, _ = test_postgres_connection_string
    database = DatabaseConfig.from_uri(
        connection_string,
        access_mode=AccessMode.FULL,
        write_mode=True,
    )
    return Settings(database=database)


@pytest.fixture
async def db_service_full(
    integration_settings: Settings,
) -> Generator[DbAccessService, None, None]:
    """DbAccessService with access_mode=full and write_mode=True for DDL and setup."""
    service = DbAccessService(integration_settings.database)
    try:
        yield service
    finally:
        await service.close()


@pytest.fixture
async def db_service_user_prefix(
    test_postgres_connection_string: tuple[str, str],
) -> Generator[DbAccessService, None, None]:
    """DbAccessService with access_mode=basic and table_prefix=app_ for table_prefix tests."""
    connection_string, _ = test_postgres_connection_string
    config = DatabaseConfig.from_uri(
        connection_string,
        access_mode=AccessMode.BASIC,
        write_mode=False,
        table_prefix="app_",
    )
    service = DbAccessService(config)
    try:
        yield service
    finally:
        await service.close()
