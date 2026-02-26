from dataclasses import dataclass

from psycopg.sql import Identifier

from postgres_fastmcp.sql.driver.base import SqlExecutor
from postgres_fastmcp.sql.security.driver import SafeSqlExecutor


@dataclass
class SequenceMetrics:
    """Метрики для проверки состояния последовательностей базы данных.

    Attributes:
        schema: Имя схемы последовательности.
        table: Имя таблицы использующей последовательность.
        column: Имя столбца использующего последовательность.
        sequence: Имя последовательности.
        column_type: Тип столбца (integer или bigint).
        last_value: Последнее значение используемое последовательностью.
        max_value: Максимальное значение для типа последовательности.
        is_healthy: Допустим ли уровень использования последовательности.
        readable: Доступна ли последовательность для чтения.
    """

    schema: str
    table: str
    column: str
    sequence: str
    column_type: str
    last_value: int
    max_value: int
    is_healthy: bool
    readable: bool = True

    @property
    def percent_used(self) -> float:
        """Вычислить какой процент от последовательности использован.

        Returns:
            Процент использованных значений последовательности (0-100).
        """
        return (self.last_value / self.max_value) * 100 if self.max_value else 0


class SequenceHealthCalc:
    """Калькулятор для проверок состояния последовательностей базы данных."""

    def __init__(self, sql_driver: SqlExecutor | SafeSqlExecutor, threshold: float = 0.9) -> None:
        """Инициализация калькулятора состояния последовательностей.

        Args:
            sql_driver: SQL драйвер для доступа к базе данных
            threshold: Процент (в виде десятичной дроби) использования последовательности,
                который вызывает предупреждение
        """
        self.sql_driver = sql_driver
        self.threshold = threshold

    async def sequence_danger_check(self) -> str:
        """Проверка последовательностей, приближающихся к их максимальным значениям.

        Returns:
            Строка с описанием последовательностей, приближающихся к максимальным значениям.
        """
        metrics = await self._get_sequence_metrics()

        if not metrics:
            return "No sequences found in the database."

        # Сортировка по оставшимся значениям по возрастанию для показа наиболее критичных первыми

        unhealthy = [m for m in metrics if not m.is_healthy]
        if not unhealthy:
            return "All sequences have healthy usage levels."

        result = ["Sequences approaching maximum value:"]
        for metric in unhealthy:
            remaining = metric.max_value - metric.last_value
            result.append(
                f"Sequence '{metric.schema}.{metric.sequence}' used for {metric.table}.{metric.column} "
                f"has used {metric.percent_used:.1f}% of available values "
                f"({metric.last_value:,} of {metric.max_value:,}, {remaining:,} remaining)"
            )
        return "\n".join(result)

    async def _get_sequence_metrics(self) -> list[SequenceMetrics]:
        """Получение метрик для последовательностей в базе данных.

        Returns:
            Список SequenceMetrics для всех последовательностей в базе данных.
        """
        # First get all sequences used as default values
        sequences = await self.sql_driver.execute(
            """
            SELECT
                n.nspname AS table_schema,
                c.relname AS table,
                attname AS column,
                format_type(a.atttypid, a.atttypmod) AS column_type,
                pg_get_expr(d.adbin, d.adrelid) AS default_value
            FROM
                pg_catalog.pg_attribute a
            INNER JOIN
                pg_catalog.pg_class c ON c.oid = a.attrelid
            INNER JOIN
                pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            INNER JOIN
                pg_catalog.pg_attrdef d ON (a.attrelid, a.attnum) = (d.adrelid, d.adnum)
            WHERE
                NOT a.attisdropped
                AND a.attnum > 0
                AND pg_get_expr(d.adbin, d.adrelid) LIKE 'nextval%'
                AND n.nspname NOT LIKE 'pg\\_temp\\_%'
        """,
            params=None,
            readonly=True,
        )

        if not sequences:
            return []

        result_list = [dict(x.cells) for x in sequences]

        # Process each sequence
        sequence_metrics = []
        for seq in result_list:
            # Parse the sequence name from default value
            schema, sequence = self._parse_sequence_name(seq["default_value"])
            if not sequence:
                continue

            # Determine max value based on column type
            max_value = 2147483647 if seq["column_type"] == "integer" else 9223372036854775807

            # Get sequence attributes
            attrs = await self.sql_driver.execute(
                """
                SELECT
                    has_sequence_privilege({}, 'SELECT') AS readable,
                    last_value
                FROM {}
                """,
                params=[Identifier(schema, sequence), Identifier(schema, sequence)],
                readonly=True,
            )

            if not attrs:
                continue

            result_list = [dict(x.cells) for x in attrs]

            attr = result_list[0]
            sequence_metrics.append(
                SequenceMetrics(
                    schema=schema,
                    table=seq["table"],
                    column=seq["column"],
                    sequence=sequence,
                    column_type=seq["column_type"],
                    last_value=attr["last_value"],
                    max_value=max_value,
                    readable=attr["readable"],
                    is_healthy=attr["last_value"] / max_value <= self.threshold,
                )
            )

        return sequence_metrics

    def _parse_sequence_name(self, default_value: str) -> tuple[str, str]:
        """Разбор имени схемы и последовательности из выражения значения по умолчанию.

        Args:
            default_value: Выражение значения по умолчанию, содержащее вызов nextval().

        Returns:
            Кортеж из (schema, sequence_name).
        """
        # Handle both formats:
        # nextval('id_seq'::regclass)
        # nextval(('id_seq'::text)::regclass)

        # Remove nextval and cast parts
        clean_value = default_value.replace("nextval('", "").replace("'::regclass)", "")
        clean_value = clean_value.replace("('", "").replace("'::text)", "")

        # Split into schema and sequence
        parts = clean_value.split(".")
        if len(parts) == 1:
            return "public", parts[0]  # Default to public schema
        return parts[0], parts[1]
