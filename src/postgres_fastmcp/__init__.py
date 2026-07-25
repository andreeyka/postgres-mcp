"""postgres-fastmcp: PostgreSQL Tuning and Analysis MCP server, also usable as a library.

Public API:
    create_server(settings, *, auth, extra_providers, extra_middleware) -> FastMCP
    Settings — главный класс настроек.

Re-exports for convenience (so consumers don't dig into fastmcp.* submodules):
    LocalProvider, FileSystemProvider — источники компонентов.
    Middleware — базовый класс для своих middleware.
"""

from importlib.metadata import (
    PackageNotFoundError,
    version as _pkg_version,
)


try:
    __version__ = _pkg_version("postgres-fastmcp")
except PackageNotFoundError:
    __version__ = "0.0.0"

from fastmcp.server.middleware import Middleware
from fastmcp.server.providers import FileSystemProvider, LocalProvider

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.server import create_server


__all__ = [
    "FileSystemProvider",
    "LocalProvider",
    "Middleware",
    "Settings",
    "__version__",
    "create_server",
]
