"""Модуль для настройки логирования с использованием Rich."""

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
    """Настроить логирование с использованием Rich.

    Args:
        level: Уровень логирования.
        omit_repeated_times: Не отображать повторяющиеся метки времени.
        show_path: Показывать путь файла в логах.
        rich_tracebacks: Включить красивые трассировки.
        tracebacks_max_frames: Максимальное количество кадров в трассировке.
        disable: Если True, отключить все логирование (полезно для режима stdio,
                 чтобы не мешать протоколу MCP).
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
    """Получить логгер с указанным именем.

    Args:
        name: Имя логгера (обычно __name__ модуля).

    Returns:
        Настроенный логгер.
    """
    return logging.getLogger(name)
