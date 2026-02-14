from dataclasses import dataclass

from postgres_fastmcp.sql.driver.base import SqlExecutor
from postgres_fastmcp.sql.security.driver import SafeSqlExecutor


@dataclass
class ConstraintMetrics:
    """Metrics for database constraint health check.

    Attributes:
        schema: Schema name of the constraint.
        table: Table name of the constraint.
        name: Constraint name.
        referenced_schema: Referenced schema name if foreign key, None otherwise.
        referenced_table: Referenced table name if foreign key, None otherwise.
    """

    schema: str
    table: str
    name: str
    referenced_schema: str | None
    referenced_table: str | None


class ConstraintHealthCalc:
    """Calculator for database constraint health checks."""

    def __init__(self, sql_driver: SqlExecutor | SafeSqlExecutor) -> None:
        self.sql_driver = sql_driver

    async def invalid_constraints_check(self) -> str:
        """Check for any invalid constraints in the database.

        Returns:
            String describing any invalid constraints found.
        """
        metrics = await self._get_invalid_constraints()

        if not metrics:
            return "No invalid constraints found."

        result = ["Invalid constraints found:"]
        for metric in metrics:
            if metric.referenced_table:
                result.append(
                    f"Constraint '{metric.name}' on table '{metric.schema}.{metric.table}' "
                    f"referencing '{metric.referenced_schema}.{metric.referenced_table}' is invalid"
                )
            else:
                result.append(f"Constraint '{metric.name}' on table '{metric.schema}.{metric.table}' is invalid")
        return "\n".join(result)

    async def _get_invalid_constraints(self) -> list[ConstraintMetrics]:
        """Get all invalid constraints in the database.

        Returns:
            List of ConstraintMetrics for invalid constraints.
        """
        results = await self.sql_driver.execute(
            """
            SELECT
                nsp.nspname AS schema,
                rel.relname AS table,
                con.conname AS name,
                fnsp.nspname AS referenced_schema,
                frel.relname AS referenced_table
            FROM
                pg_catalog.pg_constraint con
            INNER JOIN
                pg_catalog.pg_class rel ON rel.oid = con.conrelid
            LEFT JOIN
                pg_catalog.pg_class frel ON frel.oid = con.confrelid
            LEFT JOIN
                pg_catalog.pg_namespace nsp ON nsp.oid = con.connamespace
            LEFT JOIN
                pg_catalog.pg_namespace fnsp ON fnsp.oid = frel.relnamespace
            WHERE
                con.convalidated = 'f'
        """,
            params=None,
            readonly=True,
        )

        if not results:
            return []

        result_list = [dict(x.cells) for x in results]

        return [
            ConstraintMetrics(
                schema=row["schema"],
                table=row["table"],
                name=row["name"],
                referenced_schema=row["referenced_schema"],
                referenced_table=row["referenced_table"],
            )
            for row in result_list
        ]

    async def _get_total_constraints(self) -> int:
        """Get the total number of constraints.

        Returns:
            Total number of constraints in the database.
        """
        result = await self.sql_driver.execute(
            """
            SELECT COUNT(*) as count
            FROM information_schema.table_constraints
        """,
            params=None,
            readonly=True,
        )
        if not result:
            return 0
        result_list = [dict(x.cells) for x in result]
        return result_list[0]["count"] if result_list else 0

    async def _get_active_constraints(self) -> int:
        """Get the number of active constraints.

        Returns:
            Number of active (non-deferrable) constraints.
        """
        result = await self.sql_driver.execute(
            """
            SELECT COUNT(*) as count
            FROM information_schema.table_constraints
            WHERE is_deferrable = 'NO'
        """,
            params=None,
            readonly=True,
        )
        if not result:
            return 0
        result_list = [dict(x.cells) for x in result]
        return result_list[0]["count"] if result_list else 0
