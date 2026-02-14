from dataclasses import dataclass

from postgres_fastmcp.sql.driver.base import SqlExecutor
from postgres_fastmcp.sql.security.driver import SafeSqlExecutor


@dataclass
class TransactionIdMetrics:
    """Метрики для проверки переполнения идентификатора транзакции.

    Attributes:
        schema: Имя схемы таблицы.
        table: Имя таблицы.
        transactions_left: Количество транзакций, оставшихся до переполнения.
        is_healthy: Имеет ли таблица здоровый возраст идентификатора транзакции.
    """

    schema: str
    table: str
    transactions_left: int
    is_healthy: bool


class VacuumHealthCalc:
    """Калькулятор для проверок вакуума и состояния идентификатора транзакции."""

    def __init__(
        self,
        sql_driver: SqlExecutor | SafeSqlExecutor,
        threshold: int = 10000000,
        max_value: int = 2146483648,
    ) -> None:
        self.sql_driver = sql_driver
        self.threshold = threshold
        self.max_value = max_value

    async def transaction_id_danger_check(self) -> str:
        """Проверка таблиц, приближающихся к переполнению идентификатора транзакции.

        Returns:
            Строка с описанием таблиц приближающихся к переполнению идентификатора транзакции.
        """
        metrics = await self._get_transaction_id_metrics()

        if not metrics:
            return "No tables found with transaction ID wraparound danger."

        # Sort by transactions left ascending to show most critical first
        metrics.sort(key=lambda x: x.transactions_left)

        unhealthy = [m for m in metrics if not m.is_healthy]
        if not unhealthy:
            return "All tables have healthy transaction ID age."

        result = ["Tables approaching transaction ID wraparound:"]
        result.extend(
            f"Table '{metric.schema}.{metric.table}' has {metric.transactions_left:,} transactions "
            f"remaining before wraparound (threshold: {self.threshold:,})"
            for metric in unhealthy
        )
        return "\n".join(result)

    async def _get_transaction_id_metrics(self) -> list[TransactionIdMetrics]:
        """Получение метрик идентификатора транзакции для всех таблиц.

        Returns:
            Список TransactionIdMetrics для таблиц приближающихся к переполнению.
        """
        results = await self.sql_driver.execute(
            """
            SELECT
                n.nspname AS schema,
                c.relname AS table,
                {} - GREATEST(AGE(c.relfrozenxid), AGE(t.relfrozenxid)) AS transactions_left
            FROM
                pg_class c
            INNER JOIN
                pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN
                pg_class t ON c.reltoastrelid = t.oid
            WHERE
                c.relkind = 'r'
                AND ({} - GREATEST(AGE(c.relfrozenxid), AGE(t.relfrozenxid))) < {}
            ORDER BY
                3, 1, 2
        """,
            params=[self.max_value, self.max_value, self.threshold],
            readonly=True,
        )

        if not results:
            return []

        result_list = [dict(x.cells) for x in results]

        return [
            TransactionIdMetrics(
                schema=row["schema"],
                table=row["table"],
                transactions_left=row["transactions_left"],
                is_healthy=row["transactions_left"] >= self.threshold,
            )
            for row in result_list
        ]

    async def _get_vacuum_stats(self) -> dict[str, dict[str, str | None]]:
        """Получение статистики вакуума для базы данных.

        Returns:
            Словарь сопоставления имен таблиц с их статистикой вакуума.
        """
        result = await self.sql_driver.execute(
            """
            SELECT relname, last_vacuum, last_autovacuum
            FROM pg_stat_user_tables
        """,
            params=None,
            readonly=True,
        )
        if not result:
            return {}
        result_list = [dict(x.cells) for x in result]
        return {
            row["relname"]: {
                "last_vacuum": row["last_vacuum"],
                "last_autovacuum": row["last_autovacuum"],
            }
            for row in result_list
        }
