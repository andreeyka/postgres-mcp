"""Точка входа в MCP сервер."""

import sys

import click

from postgres_fastmcp import __version__
from postgres_fastmcp.config import build_settings_from_cli
from postgres_fastmcp.logger import get_logger
from postgres_fastmcp.server import compose_mcp

from .logger import configure_logging


logger = get_logger(__name__)


@click.command()
@click.option("--version", is_flag=True, default=False, help="Show version and exit")
@click.option(
    "--database-uri",
    type=str,
    help="Database connection URI (if specified, runs single server mode ignoring config.json)",
)
@click.option(
    "--transport",
    type=click.Choice(["http", "stdio"], case_sensitive=False),
    default=None,
    help="Transport type: 'http' or 'stdio'. If not specified, uses config.json or environment variables.",
)
@click.option("--host", type=str, default="127.0.0.1", help="Host to bind the server to")
@click.option("--port", type=int, default=8000, help="Port to bind the server to")
@click.option("--workers", type=int, default=1, help="Number of workers to run")
@click.option(
    "--access-mode",
    type=click.Choice(["restricted", "unrestricted"], case_sensitive=False),
    default=None,
    help="SQL access mode: 'restricted' (read-only, SELECT only) or 'unrestricted' (read-write, DML/DDL). "
    "Used only with --database-uri. Default: 'restricted'.",
)
@click.option(
    "--role",
    type=click.Choice(["user", "full"], case_sensitive=False),
    default=None,
    help="User role: 'user' (basic role, only public schema, 4 tools) or 'full' (all schemas, 9 tools). "
    "Used only with --database-uri. Default: 'user'.",
)
def main(  # noqa: PLR0913
    *,
    version: bool = False,
    database_uri: str | None = None,
    transport: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
    workers: int = 1,
    access_mode: str | None = None,
    role: str | None = None,
) -> None:
    """Основная функция для запуска сервера.

    Использует mcp.run(transport=...) для обоих режимов: stdio и HTTP.
    Менеджер жизненного цикла автоматически закрывает ресурсы через AsyncExitStack.
    """
    configure_logging(level="INFO", omit_repeated_times=False)

    if version:
        click.echo(f"postgres-fastmcp version {__version__}")
        return

    settings = build_settings_from_cli(
        database_uri=database_uri,
        transport=transport,
        host=host,
        port=port,
        workers=workers,
        access_mode=access_mode,
        role=role,
    )
    actual_transport = transport if transport is not None else settings.server.transport
    if actual_transport == "stdio":
        configure_logging(disable=True)

    mcp = compose_mcp(settings)
    try:
        if actual_transport == "http":
            mcp.run(
                transport="http",
                host=settings.server.host,
                port=settings.server.port,
                uvicorn_config={"ws": "websockets-sansio"},
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
    main()
