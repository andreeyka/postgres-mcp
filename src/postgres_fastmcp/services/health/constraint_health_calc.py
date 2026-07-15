"""Проверка недействительных (NOT VALID) ограничений в базе данных."""

from dataclasses import dataclass

from postgres_fastmcp.services.health.base import BaseHealthCalc


QUERY_INVALID_CONSTRAINTS = """
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
"""


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


class ConstraintHealthCalc(BaseHealthCalc):
    """Calculator for database constraint health checks."""

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
        return [
            ConstraintMetrics(
                schema=row["schema"],
                table=row["table"],
                name=row["name"],
                referenced_schema=row["referenced_schema"],
                referenced_table=row["referenced_table"],
            )
            for row in await self._rows(QUERY_INVALID_CONSTRAINTS)
        ]
