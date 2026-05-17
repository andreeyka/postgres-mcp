"""Точка входа в MCP сервер."""

import sys
from typing import Literal

from cyclopts import App

from postgres_fastmcp import __version__
from postgres_fastmcp.config import build_settings_from_cli
from postgres_fastmcp.enums import AccessMode
from postgres_fastmcp.logger import get_logger
from postgres_fastmcp.server import create_server

from .logger import configure_logging


logger = get_logger(__name__)

app = App(
    help="PostgreSQL Tuning and Analysis MCP server.",
    version=f"postgres-fastmcp version {__version__}",
)


@app.default
def main(  # noqa: PLR0913
    *,
    database_uri: str | None = None,
    transport: Literal["http", "stdio"] | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
    workers: int = 1,
    write_mode: bool = False,
    access_mode: AccessMode = AccessMode.BASIC,
) -> None:
    """Основная функция для запуска сервера.

    Использует mcp.run(transport=...) для обоих режимов: stdio и HTTP.
    Менеджер жизненного цикла автоматически закрывает ресурсы через AsyncExitStack.

    Parameters
    ----------
    database_uri : str | None
        Database connection URI (if specified, runs single server mode; database and server from CLI).
    transport : Literal['http', 'stdio'] | None
        Transport type: 'http' or 'stdio'. If not specified, uses environment variables.
    host : str
        Host to bind the server to.
    port : int
        Port to bind the server to.
    workers : int
        Number of workers to run.
    write_mode : bool
        Allow DML/DDL (read-write).
    access_mode : AccessMode
        Access mode: 'basic' (public schema, 4 tools) or 'full' (all schemas, 9 tools).
    """
    configure_logging(level="INFO", omit_repeated_times=False)

    settings = build_settings_from_cli(
        database_uri=database_uri,
        transport=transport,
        host=host,
        port=port,
        workers=workers,
        write_mode=write_mode,
        access_mode=access_mode,
    )
    actual_transport = transport if transport is not None else settings.server.transport
    if actual_transport == "stdio":
        configure_logging(disable=True)

    mcp = create_server(settings)
    try:
        if actual_transport == "http":
            mcp.run(
                transport="http",
                host=settings.server.host,
                port=settings.server.port,
                uvicorn_config={"ws": "websockets-sansio", "log_config": None},
            )
        else:
            mcp.run(transport="stdio")
    except KeyboardInterrupt:
        logger.info("Application interrupted by user")
        sys.exit(0)
    except Exception:
        logger.exception("Fatal error")
        sys.exit(1)


if __name__ == "__main__":
    app()
