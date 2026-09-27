"""Точка входа в MCP сервер."""

import sys
from typing import Literal

from cyclopts import App

from postgres_fastmcp import __version__
from postgres_fastmcp.app.config import build_settings_from_cli
from postgres_fastmcp.app.server import create_server
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.shared.logger import configure_logging, get_logger


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
    host: str | None = None,
    port: int | None = None,
    write_mode: bool | None = None,
    access_mode: AccessMode | None = None,
) -> None:
    """Основная функция для запуска сервера.

    Использует mcp.run(transport=...) для обоих режимов: stdio и HTTP.
    Менеджер жизненного цикла автоматически закрывает ресурсы через AsyncExitStack.

    Parameters
    ----------
    database_uri : str | None
        Database connection URI; overrides the connection fields of config.json and env.
    transport : Literal['http', 'stdio'] | None
        Transport type: 'http' or 'stdio'. Default: config.json, env, then 'http'.
    host : str | None
        Host to bind the HTTP server to. Default: config.json, env, then 127.0.0.1.
    port : int | None
        Port to bind the HTTP server to. Default: config.json, env, then 8000.
    write_mode : bool | None
        Allow DML/DDL (read-write); --no-write-mode forces read-only. Default: config.json, env, then read-only.
    access_mode : AccessMode | None
        Access mode: 'basic' (public schema, 4 tools) or 'full' (all schemas, 9 tools). Default: config.json, env,
        then 'basic'.
    """
    configure_logging(level="INFO", omit_repeated_times=False)

    settings = build_settings_from_cli(
        database_uri=database_uri,
        transport=transport,
        host=host,
        port=port,
        write_mode=write_mode,
        access_mode=access_mode,
    )
    actual_transport = transport if transport is not None else settings.server.transport
    # Сервер собирается до отключения логов в stdio: предупреждения auth на старте уходят в stderr.
    # build_auth=False только здесь и только для реального stdio-запуска этого же процесса (actual_transport,
    # а не settings.server.transport — это лишь конфигурация и не обязана совпадать с тем, что ниже уйдёт в
    # mcp.run(transport=...)): в stdio auth всё равно не действует, и не нужно трогать сеть/диск ради oidc.
    mcp = create_server(settings, build_auth=actual_transport != "stdio")
    if actual_transport == "stdio":
        configure_logging(disable=True)

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
