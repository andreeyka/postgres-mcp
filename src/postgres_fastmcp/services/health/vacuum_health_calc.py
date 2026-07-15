"""Проверка приближения таблиц к переполнению идентификатора транзакции (wraparound)."""

from dataclasses import dataclass

from postgres_fastmcp.services.health.base import BaseHealthCalc, HealthSqlDriver


QUERY_TXID_WRAPAROUND = """
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
"""


@dataclass
class TransactionIdMetrics:
    """Метрики для проверки переполнения идентификатора транзакции.

    Attributes:
        schema: Имя схемы таблицы.
        table: Имя таблицы.
        transactions_left: Количество транзакций, оставшихся до переполнения.
    """

    schema: str
    table: str
    transactions_left: int


class VacuumHealthCalc(BaseHealthCalc):
    """Калькулятор для проверок вакуума и состояния идентификатора транзакции."""

    def __init__(
        self,
        sql_driver: HealthSqlDriver,
        threshold: int = 10000000,
        max_value: int = 2146483648,
    ) -> None:
        super().__init__(sql_driver)
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

        result = ["Tables approaching transaction ID wraparound:"]
        result.extend(
            f"Table '{metric.schema}.{metric.table}' has {metric.transactions_left:,} transactions "
            f"remaining before wraparound (threshold: {self.threshold:,})"
            for metric in metrics
        )
        return "\n".join(result)

    async def _get_transaction_id_metrics(self) -> list[TransactionIdMetrics]:
        """Получение метрик идентификатора транзакции для таблиц ниже порога (уже отсортированных SQL-ом)."""
        return [
            TransactionIdMetrics(
                schema=row["schema"],
                table=row["table"],
                transactions_left=row["transactions_left"],
            )
            for row in await self._rows(
                QUERY_TXID_WRAPAROUND,
                params=[self.max_value, self.max_value, self.threshold],
            )
        ]
