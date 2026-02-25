# mypy: ignore-errors
"""Unit tests for database_config_provider."""

from unittest.mock import MagicMock

import pytest

from postgres_fastmcp.config.database import DatabaseConfig
from postgres_fastmcp.providers.database_config_provider import get_database_config


class TestGetDatabaseConfig:
    """Tests for get_database_config."""

    def test_returns_config_when_present(self) -> None:
        """Returns DatabaseConfig when lifespan_context has database_config."""
        config = DatabaseConfig.from_uri("postgresql://user:pass@localhost/test")
        ctx = MagicMock()
        ctx.lifespan_context = {"database_config": config}

        result = get_database_config(ctx)

        assert result is config
        assert result.database_uri is not None
        assert "user" in result.database_uri and "localhost" in result.database_uri and "/test" in result.database_uri

    def test_raises_when_config_absent(self) -> None:
        """Raises RuntimeError when database_config is not in lifespan_context."""
        ctx = MagicMock()
        ctx.lifespan_context = {}

        with pytest.raises(RuntimeError, match=r"Database config.*not available"):
            get_database_config(ctx)

    def test_raises_when_config_is_none(self) -> None:
        """Raises RuntimeError when database_config is explicitly None."""
        ctx = MagicMock()
        ctx.lifespan_context = {"database_config": None}

        with pytest.raises(RuntimeError, match=r"Database config.*not available"):
            get_database_config(ctx)
