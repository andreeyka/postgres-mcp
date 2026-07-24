"""Определение и кэширование версии PostgreSQL."""

import logging

from postgres_fastmcp.sql.ports import QueryExecutorPort


logger = logging.getLogger(__name__)


class PostgresVersionRegistry:
    """Кэш основной версии PostgreSQL по идентификатору подключения."""

    def __init__(self) -> None:
        self._cache: dict[str, int] = {}

    def get(self, connection_id: str) -> int | None:
        """Получение версии PostgreSQL из кэша по идентификатору подключения."""
        return self._cache.get(connection_id)

    def set(self, connection_id: str, version: int) -> None:
        """Установка версии PostgreSQL в кэш по идентификатору подключения."""
        self._cache[connection_id] = version

    def clear(self, connection_id: str | None = None) -> None:
        """Очистка кэша версий (например, для тестов)."""
        if connection_id is not None:
            self._cache.pop(connection_id, None)
        else:
            self._cache.clear()


_version_registry = PostgresVersionRegistry()


def reset_postgres_version_cache(connection_id: str | None = None) -> None:
    """Очистка кэша версий (например, для тестов)."""
    _version_registry.clear(connection_id)


async def get_postgres_version(executor: QueryExecutorPort, connection_id: str) -> int:
    """Возвращает основную версию PostgreSQL (например, 16), используя кэш по идентификатору подключения."""
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
    except Exception as e:
        logger.warning("Error determining PostgreSQL version: %s", e)
        return 0
    else:
        return version
