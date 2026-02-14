"""Module for configuring logging with Rich."""

import logging
from typing import Literal

from rich.console import Console
from rich.logging import RichHandler


def configure_logging(  # noqa: PLR0913
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] | int = "INFO",
    *,
    omit_repeated_times: bool = False,
    show_path: bool = True,
    rich_tracebacks: bool = True,
    tracebacks_max_frames: int = 3,
    disable: bool = False,
) -> None:
    """Configure logging with Rich.

    Args:
        level: Log level.
        omit_repeated_times: Omit repeated timestamps.
        show_path: Show file path in logs.
        rich_tracebacks: Enable rich tracebacks.
        tracebacks_max_frames: Maximum number of frames in traceback.
        disable: If True, disable all logging (useful for stdio mode to avoid interfering with MCP protocol).
    """
    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove all existing handlers
    root_logger.handlers.clear()

    if disable:
        # For stdio mode: completely disable logging to avoid interfering with MCP protocol
        # Use NullHandler to suppress all log output
        root_logger.addHandler(logging.NullHandler())
        return

    # Create console for output
    console = Console(stderr=True)

    # Create handler for regular logs (without tracebacks)
    handler = RichHandler(
        console=console,
        show_time=True,
        omit_repeated_times=omit_repeated_times,
        show_level=True,
        show_path=show_path,
        rich_tracebacks=False,
    )
    handler.setLevel(level)
    # Filter: only records without traceback
    handler.addFilter(lambda record: record.exc_info is None)
    root_logger.addHandler(handler)

    # Create handler for tracebacks
    traceback_handler = RichHandler(
        console=console,
        show_time=True,
        omit_repeated_times=omit_repeated_times,
        show_level=True,
        show_path=show_path,
        rich_tracebacks=rich_tracebacks,
        tracebacks_max_frames=tracebacks_max_frames,
    )
    traceback_handler.setLevel(level)
    # Filter: only records with traceback
    traceback_handler.addFilter(lambda record: record.exc_info is not None)
    root_logger.addHandler(traceback_handler)


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the specified name.

    Args:
        name: Logger name (usually __name__ of the module).

    Returns:
        Configured logger.
    """
    return logging.getLogger(name)
