# mypy: ignore-errors
"""Unit tests for logger module."""

import logging

from postgres_fastmcp.shared.logger import configure_logging, get_logger


class TestGetLogger:
    """Tests for get_logger."""

    def test_returns_logger_with_name(self) -> None:
        logger = get_logger("test.module")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test.module"


class TestConfigureLogging:
    """Tests for configure_logging."""

    def test_disable_adds_null_handler(self) -> None:
        configure_logging(disable=True)
        root = logging.getLogger()
        handlers = [h for h in root.handlers if type(h).__name__ == "NullHandler"]
        assert len(handlers) >= 1
        # Restore for other tests
        root.handlers.clear()

    def test_level_set_on_root(self) -> None:
        root = logging.getLogger()
        root.handlers.clear()
        configure_logging(level="DEBUG", disable=False)
        assert root.level == logging.DEBUG
        root.handlers.clear()
