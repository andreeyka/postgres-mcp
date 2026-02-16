# mypy: ignore-errors
"""Fixtures for integration tests: settings and DbAccessService from Docker PostgreSQL."""

from typing import Generator

import pytest

from postgres_fastmcp.config import Settings, get_settings
from postgres_fastmcp.config.database import DatabaseConfig
from postgres_fastmcp.enums import AccessMode, UserRole
from postgres_fastmcp.services.db_access_service import DbAccessService


@pytest.fixture
def integration_settings(
    test_postgres_connection_string: tuple[str, str],
) -> Settings:
    """Settings with database pointing to the test PostgreSQL (full role, unrestricted)."""
    connection_string, _ = test_postgres_connection_string
    database = DatabaseConfig.from_uri(
        connection_string,
        role=UserRole.ADMIN,
        access_mode=AccessMode.UNRESTRICTED,
    )
    return get_settings(database=database)


@pytest.fixture
async def db_service_full(
    integration_settings: Settings,
) -> Generator[DbAccessService, None, None]:
    """DbAccessService with full/unrestricted access for DDL and setup."""
    service = DbAccessService(integration_settings.database)
    async with service:
        yield service


@pytest.fixture
async def db_service_user_prefix(
    test_postgres_connection_string: tuple[str, str],
) -> Generator[DbAccessService, None, None]:
    """DbAccessService with user role and table_prefix=app_ for table_prefix tests."""
    connection_string, _ = test_postgres_connection_string
    config = DatabaseConfig.from_uri(
        connection_string,
        role=UserRole.USER,
        access_mode=AccessMode.RESTRICTED,
        table_prefix="app_",
    )
    service = DbAccessService(config)
    async with service:
        yield service
