# mypy: ignore-errors
"""Shared test configuration: async mode, Docker fixtures for integration, version cache reset."""

import asyncio
from typing import Generator

import pytest
from dotenv import load_dotenv

from postgres_fastmcp.postgres.extensions import reset_postgres_version_cache


try:
    from utils import create_postgres_container
except ImportError:
    create_postgres_container = None  # type: ignore[assignment]

load_dotenv()

# pytest-asyncio: run async tests without explicit @pytest.mark.asyncio when asyncio_mode is auto
pytest_plugins = ["pytest_asyncio"]


def pytest_configure(config: pytest.Config) -> None:
    """Set asyncio_mode to auto so async test functions are detected automatically."""
    config.option.asyncio_mode = "auto"


@pytest.fixture(scope="session")
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    """Create and return a custom event loop policy for tests."""
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture(scope="class", params=["postgres:15", "postgres:16"])
def test_postgres_connection_string(request: pytest.FixtureRequest) -> Generator[tuple[str, str], None, None]:
    """Provide PostgreSQL connection string from Docker (for integration tests)."""
    if create_postgres_container is None:
        pytest.skip("Docker utils not available")
    yield from create_postgres_container(request.param)


@pytest.fixture(autouse=True)
def reset_pg_version_cache() -> Generator[None, None, None]:
    """Reset the PostgreSQL version cache before each test."""
    reset_postgres_version_cache()
    yield
