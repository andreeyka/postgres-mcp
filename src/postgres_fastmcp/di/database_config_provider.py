"""Провайдер текущих прав (DatabaseConfig) — один источник для всех модулей."""

from typing import cast

from fastmcp.dependencies import CurrentContext, Depends
from fastmcp.server.context import Context

from postgres_fastmcp.config.database import DatabaseConfig


def get_database_config(ctx: Context = CurrentContext()) -> DatabaseConfig:  # noqa: B008
    """Получить текущую конфигурацию БД (права: role, access_mode) из lifespan context.

    Единая точка доступа к «текущим правам» для инструментов и сервисов.
    """
    config = ctx.lifespan_context.get("database_config")
    if config is None:
        msg = "Database config (current permissions) not available"
        raise RuntimeError(msg)
    return cast("DatabaseConfig", config)


DatabaseConfigProvider = Depends(get_database_config)
