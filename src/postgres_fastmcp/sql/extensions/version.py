"""PostgreSQL version detection and caching."""

import logging
from typing import Any


logger = logging.getLogger(__name__)


class PostgresVersionRegistry:
    """Cache of PostgreSQL major version per connection id."""

    def __init__(self) -> None:
        self._cache: dict[str, int] = {}

    def get(self, connection_id: str) -> int | None:
        return self._cache.get(connection_id)

    def set(self, connection_id: str, version: int) -> None:
        self._cache[connection_id] = version

    def clear(self, connection_id: str | None = None) -> None:
        if connection_id is not None:
            self._cache.pop(connection_id, None)
        else:
            self._cache.clear()


_version_registry = PostgresVersionRegistry()


def reset_postgres_version_cache(connection_id: str | None = None) -> None:
    """Clear version cache (e.g. for tests)."""
    _version_registry.clear(connection_id)


async def get_postgres_version(executor: Any, connection_id: str) -> int:
    """Return major PostgreSQL version (e.g. 16), using cache keyed by connection_id."""
    cached = _version_registry.get(connection_id)
    if cached is not None:
        return cached
    try:
        rows = await executor.execute("SHOW server_version", params=None, readonly=True)
        if not rows:
            logger.warning("Could not determine PostgreSQL version")
            return 0
        version_string = rows[0].cells.get("server_version")
        if version_string is None:
            return 0
        if isinstance(version_string, bytes):
            version_string = version_string.decode("utf-8")
        if not isinstance(version_string, str):
            version_string = str(version_string)
        major = version_string.split(".")[0]
        version = int(major)
        _version_registry.set(connection_id, version)
        return version
    except Exception as e:
        logger.warning("Error determining PostgreSQL version: %s", e)
        return 0
