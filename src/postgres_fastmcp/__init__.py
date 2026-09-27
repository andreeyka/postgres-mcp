"""postgres-fastmcp: PostgreSQL Tuning and Analysis MCP server, also usable as a library.

Public API:
    PostgresProvider — источник тулов одной базы для своего FastMCP (``add_provider``, ``namespace``).
    DatabaseConfig — подключение и серверный потолок прав (access_mode, write_mode); env не читает.
    AccessPolicy, EffectiveAccess, AccessResolver, full_access_check — права одного запроса.
    ResponseBudgetMiddleware — бюджет ответа тула в токенах; ``create_server`` подключает его сам,
        на своём сервере добавьте его вручную первым middleware.
    Settings, create_server — готовый сервер с конфигурацией из env/.env/config.json.

Base-класс ``Middleware``, ``LocalProvider`` и прочие классы FastMCP импортируются из ``fastmcp``.
"""

from importlib.metadata import (
    PackageNotFoundError,
    version as _pkg_version,
)


try:
    __version__ = _pkg_version("postgres-fastmcp")
except PackageNotFoundError:
    __version__ = "0.0.0"

from postgres_fastmcp.access import AccessPolicy, AccessResolver, EffectiveAccess, full_access_check
from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.app.middleware.response_budget import ResponseBudgetMiddleware
from postgres_fastmcp.app.server import create_server
from postgres_fastmcp.provider import PostgresProvider


__all__ = [
    "AccessPolicy",
    "AccessResolver",
    "DatabaseConfig",
    "EffectiveAccess",
    "PostgresProvider",
    "ResponseBudgetMiddleware",
    "Settings",
    "__version__",
    "create_server",
    "full_access_check",
]
