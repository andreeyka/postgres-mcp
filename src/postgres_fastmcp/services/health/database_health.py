import asyncio
import logging
from collections.abc import Awaitable
from enum import StrEnum

from mcp import types

from postgres_fastmcp.services.health.buffer_health_calc import BufferHealthCalc
from postgres_fastmcp.services.health.connection_health_calc import ConnectionHealthCalc
from postgres_fastmcp.services.health.constraint_health_calc import ConstraintHealthCalc
from postgres_fastmcp.services.health.index_health_calc import IndexHealthCalc
from postgres_fastmcp.services.health.replication_calc import ReplicationCalc
from postgres_fastmcp.services.health.sequence_health_calc import SequenceHealthCalc
from postgres_fastmcp.services.health.vacuum_health_calc import VacuumHealthCalc
from postgres_fastmcp.sql.driver.base import SqlExecutor
from postgres_fastmcp.sql.security.driver import SafeSqlExecutor


ResponseType = list[types.TextContent | types.ImageContent | types.EmbeddedResource]

logger = logging.getLogger(__name__)


class HealthType(StrEnum):
    """Перечисление доступных типов проверок состояния базы данных."""

    INDEX = "index"
    CONNECTION = "connection"
    VACUUM = "vacuum"
    SEQUENCE = "sequence"
    REPLICATION = "replication"
    BUFFER = "buffer"
    CONSTRAINT = "constraint"
    ALL = "all"


class DatabaseHealthTool:
    """Инструмент для анализа метрик состояния базы данных."""

    def __init__(self, sql_driver: SqlExecutor | SafeSqlExecutor) -> None:
        """Инициализация инструмента проверки состояния базы данных.

        Args:
            sql_driver: Экземпляр SQL драйвера для доступа к базе данных.
        """
        self.sql_driver = sql_driver

    def _build_tasks(self, health_types: set[HealthType]) -> list[tuple[str, Awaitable[str]]]:
        """Собирает список (label, coroutine) задач для запрошенных типов проверок."""
        tasks: list[tuple[str, Awaitable[str]]] = []

        if HealthType.INDEX in health_types:
            index_health = IndexHealthCalc(self.sql_driver)
            tasks.append(("Invalid index check", index_health.invalid_index_check()))
            tasks.append(("Duplicate index check", index_health.duplicate_index_check()))
            tasks.append(("Index bloat", index_health.index_bloat()))
            tasks.append(("Unused index check", index_health.unused_indexes()))

        if HealthType.CONNECTION in health_types:
            connection_health = ConnectionHealthCalc(self.sql_driver)
            tasks.append(("Connection health", connection_health.connection_health_check()))

        if HealthType.VACUUM in health_types:
            vacuum_health = VacuumHealthCalc(self.sql_driver)
            tasks.append(("Vacuum health", vacuum_health.transaction_id_danger_check()))

        if HealthType.SEQUENCE in health_types:
            sequence_health = SequenceHealthCalc(self.sql_driver)
            tasks.append(("Sequence health", sequence_health.sequence_danger_check()))

        if HealthType.REPLICATION in health_types:
            replication_health = ReplicationCalc(self.sql_driver)
            tasks.append(("Replication health", replication_health.replication_health_check()))

        if HealthType.BUFFER in health_types:
            buffer_health = BufferHealthCalc(self.sql_driver)
            tasks.append(("Buffer health for indexes", buffer_health.index_hit_rate()))
            tasks.append(("Buffer health for tables", buffer_health.table_hit_rate()))

        if HealthType.CONSTRAINT in health_types:
            constraint_health = ConstraintHealthCalc(self.sql_driver)
            tasks.append(("Constraint health", constraint_health.invalid_constraints_check()))

        return tasks

    async def health(self, health_type: str) -> str:
        """Запуск проверок состояния базы данных для указанных компонентов.

        Args:
            health_type: Список типов проверок состояния через запятую.
                Допустимые значения: index, connection, vacuum, sequence, replication, buffer, constraint, all.

        Returns:
            Строка с результатами проверок состояния.
        """
        try:
            try:
                health_types = {HealthType(x.strip()) for x in health_type.split(",")}
            except ValueError:
                return (
                    f"Invalid health types provided: '{health_type}'. "
                    f"Valid values are: {', '.join(sorted([t.value for t in HealthType]))}. "
                    "Please try again with a comma-separated list of valid health types."
                )

            if HealthType.ALL in health_types:
                health_types = {t for t in HealthType if t != HealthType.ALL}

            tasks = self._build_tasks(health_types)
            if not tasks:
                return "No health checks were performed."

            labels = [label for label, _ in tasks]
            coros = [coro for _, coro in tasks]
            results = await asyncio.gather(*coros, return_exceptions=True)

            lines: list[str] = []
            for label, res in zip(labels, results, strict=True):
                if isinstance(res, BaseException):
                    logger.exception(
                        "Health check '%s' failed",
                        label,
                        exc_info=(type(res), res, res.__traceback__),
                    )
                    lines.append(f"{label}: check failed: {res}")
                else:
                    lines.append(f"{label}: {res}")
            return "\n".join(lines) + "\n"

        except Exception as e:
            logger.exception("Error calculating database health")
            return f"Error calculating database health: {e}"
