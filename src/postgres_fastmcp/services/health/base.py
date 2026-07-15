"""Базовый класс health-калькуляторов: общий драйвер и распаковка строк результата."""

from typing import Any

from postgres_fastmcp.sql.driver.base import SqlExecutor
from postgres_fastmcp.sql.security.driver import SafeSqlExecutor


HealthSqlDriver = SqlExecutor | SafeSqlExecutor


class BaseHealthCalc:
    """Общая основа health-калькуляторов: хранит SQL-драйвер и разворачивает строки в dict.

    Убирает повторяющийся паттерн ``[dict(x.cells) for x in result] if result else []``,
    который иначе дублируется в каждом калькуляторе.
    """

    def __init__(self, sql_driver: SqlExecutor | SafeSqlExecutor) -> None:
        """Инициализация с SQL-драйвером (обычным или безопасным)."""
        self.sql_driver = sql_driver

    async def _rows(
        self,
        query: str,
        params: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Выполнить read-only запрос и вернуть строки как список словарей (пустой при отсутствии данных)."""
        result = await self.sql_driver.execute(query, params=params, readonly=True)
        return [dict(row.cells) for row in result] if result else []
