import logging
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
    """Enumeration of available database health check types."""

    INDEX = "index"
    CONNECTION = "connection"
    VACUUM = "vacuum"
    SEQUENCE = "sequence"
    REPLICATION = "replication"
    BUFFER = "buffer"
    CONSTRAINT = "constraint"
    ALL = "all"


class DatabaseHealthTool:
    """Tool for analyzing database health metrics."""

    def __init__(self, sql_driver: SqlExecutor | SafeSqlExecutor) -> None:
        """Initialize the database health tool.

        Args:
            sql_driver: SQL driver instance for database access.
        """
        self.sql_driver = sql_driver

    async def health(self, health_type: str) -> str:  # noqa: C901
        """Run database health checks for the specified components.

        Args:
            health_type: Comma-separated list of health check types to perform.
                Valid values: index, connection, vacuum, sequence, replication, buffer, constraint, all.

        Returns:
            String with the health check results.
        """
        try:
            result = ""
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

            if HealthType.INDEX in health_types:
                index_health = IndexHealthCalc(self.sql_driver)
                result += "Invalid index check: " + await index_health.invalid_index_check() + "\n"
                result += "Duplicate index check: " + await index_health.duplicate_index_check() + "\n"
                result += "Index bloat: " + await index_health.index_bloat() + "\n"
                result += "Unused index check: " + await index_health.unused_indexes() + "\n"

            if HealthType.CONNECTION in health_types:
                connection_health = ConnectionHealthCalc(self.sql_driver)
                result += "Connection health: " + await connection_health.connection_health_check() + "\n"

            if HealthType.VACUUM in health_types:
                vacuum_health = VacuumHealthCalc(self.sql_driver)
                result += "Vacuum health: " + await vacuum_health.transaction_id_danger_check() + "\n"

            if HealthType.SEQUENCE in health_types:
                sequence_health = SequenceHealthCalc(self.sql_driver)
                result += "Sequence health: " + await sequence_health.sequence_danger_check() + "\n"

            if HealthType.REPLICATION in health_types:
                replication_health = ReplicationCalc(self.sql_driver)
                result += "Replication health: " + await replication_health.replication_health_check() + "\n"

            if HealthType.BUFFER in health_types:
                buffer_health = BufferHealthCalc(self.sql_driver)
                result += "Buffer health for indexes: " + await buffer_health.index_hit_rate() + "\n"
                result += "Buffer health for tables: " + await buffer_health.table_hit_rate() + "\n"

            if HealthType.CONSTRAINT in health_types:
                constraint_health = ConstraintHealthCalc(self.sql_driver)
                result += "Constraint health: " + await constraint_health.invalid_constraints_check() + "\n"

        except Exception as e:
            logger.exception("Error calculating database health")
            return f"Error calculating database health: {e}"
        else:
            return result or "No health checks were performed."
