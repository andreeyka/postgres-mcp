"""Оценка стоимости и размеров конфигураций индексов (what-if анализ через EXPLAIN/hypopg)."""

import json
import logging
from collections.abc import Callable
from typing import Any

from pglast.ast import SelectStmt

from postgres_fastmcp.domains.explain.explain_plan import ExplainPlanBuilder
from postgres_fastmcp.postgres.models import IndexDefinition
from postgres_fastmcp.postgres.ports import SqlDriverPort

from .models import candidate_str


logger = logging.getLogger(__name__)


def extract_cost_from_json_plan(plan_data: dict[str, Any]) -> float:
    """Извлечь общую стоимость из данных JSON EXPLAIN плана.

    Args:
        plan_data: Словарь содержащий данные EXPLAIN плана.

    Returns:
        Общее значение стоимости.

    Raises:
        ValueError: Если стоимость не может быть извлечена.
    """
    try:
        if not plan_data:
            return float("inf")

        # Parse JSON plan
        top_plan = plan_data.get("Plan")
        if not top_plan:
            logger.error("No top plan found in plan data: %s", plan_data)
            return float("inf")

        # Extract total cost from top plan
        total_cost = top_plan.get("Total Cost")
        if total_cost is None:
            logger.error("Total Cost not found in top plan: %s", top_plan)
            return float("inf")

        return float(total_cost)
    except (IndexError, KeyError, ValueError, json.JSONDecodeError) as e:
        error_msg = "Error extracting cost from plan"
        raise ValueError(error_msg) from e


class CostEvaluator:
    """Оценщик стоимости и размеров конфигураций индексов с мемоизацией.

    Инкапсулирует все обращения к EXPLAIN/hypopg/pg_stats для what-if анализа:
    стоимость нагрузки с гипотетическими индексами, оценка размера индексов
    и таблиц. Результаты кэшируются на время жизни оценщика.
    """

    def __init__(
        self,
        sql_driver: SqlDriverPort,
        connection_id: str = "",
        trace: Callable[[str], None] | None = None,
    ) -> None:
        """Инициализация CostEvaluator.

        Args:
            sql_driver: SQL исполнитель для доступа к базе данных.
            connection_id: Стабильный идентификатор соединения для кэша версии/расширения.
            trace: Необязательный обработчик трассировочных сообщений (например, dta_trace).
        """
        self.sql_driver = sql_driver
        self._connection_id = connection_id
        self._trace: Callable[[str], None] = trace if trace is not None else logger.debug

        # Memoization caches
        self.cost_cache: dict[frozenset[IndexDefinition], float] = {}
        self._size_estimate_cache: dict[tuple[str, frozenset[str]], int] = {}
        self._table_size_cache: dict[str, int] = {}
        self._explain_plans_cache: dict[tuple[str, frozenset[IndexDefinition]], dict[str, Any]] = {}

    def reset_size_cache(self) -> None:
        """Очистить кэш оценок размеров индексов (в начале нового анализа)."""
        self._size_estimate_cache = {}

    async def explain_plan_with_indexes(self, query_text: str, indexes: frozenset[IndexDefinition]) -> dict[str, Any]:
        """Получить план объяснения для запроса с заданным набором индексов.

        Результаты кэшируются для избежания повторных операций объяснения.

        Args:
            query_text: SQL запрос для объяснения
            indexes: Frozenset объектов IndexDefinition представляющих включаемые индексы

        Returns:
            План объяснения в виде словаря
        """
        # Create a cache key from the query and indexes
        cache_key = (query_text, indexes)

        # Return cached result if available
        existing_plan = self._explain_plans_cache.get(cache_key)
        if existing_plan:
            return existing_plan

        explain_plan_tool = ExplainPlanBuilder(self.sql_driver, connection_id=self._connection_id)
        plan = await explain_plan_tool.generate_explain_plan_with_hypothetical_indexes(
            query_text, indexes, use_generic_plan=False, trace=self._trace
        )

        # Cache the result
        self._explain_plans_cache[cache_key] = plan
        return plan

    async def evaluate_configuration_cost(
        self,
        weighted_workload: list[tuple[str, SelectStmt, float]],
        indexes: frozenset[IndexDefinition],
    ) -> float:
        """Оценить общую стоимость с выборочным включением и кэшированием.

        Args:
            weighted_workload: Список кортежей с текстом запроса, разобранным оператором и весом.
            indexes: Frozenset определений индексов конфигурации.

        Returns:
            Средневзвешенная стоимость нагрузки для этой конфигурации.

        Raises:
            ValueError: Если оценка конфигурации не удалась.
        """
        # Use indexes as cache key
        if indexes in self.cost_cache:
            self._trace(f"  - Using cached cost for configuration: {candidate_str(indexes)}")
            return self.cost_cache[indexes]

        self._trace(f"  - Evaluating cost for configuration: {candidate_str(indexes)}")

        total_cost = 0.0
        valid_queries = 0

        try:
            # Calculate cost for all queries with this configuration
            for query_text, _stmt, weight in weighted_workload:
                try:
                    # Get the explain plan using our memoized helper
                    plan_data = await self.explain_plan_with_indexes(query_text, indexes)

                    # Extract cost from the plan data
                    cost = extract_cost_from_json_plan(plan_data)
                    total_cost += cost * weight
                    valid_queries += 1
                except Exception as e:
                    error_msg = f"Error executing explain for query: {query_text}"
                    raise ValueError(error_msg) from e

            if valid_queries == 0:
                self._trace("    + no valid queries found for cost evaluation")
                return float("inf")

            avg_cost = total_cost / valid_queries
            self.cost_cache[indexes] = avg_cost
            self._trace(f"    + config cost: {avg_cost:.2f} (from {valid_queries} queries)")

        except Exception as e:
            self._trace(f"    + error evaluating configuration: {e}")
            error_msg = "Error evaluating configuration"
            raise ValueError(error_msg) from e
        else:
            return avg_cost

    async def estimate_index_size(self, table: str, columns: list[str]) -> int:
        """Оценить размер индекса.

        Args:
            table: Имя таблицы.
            columns: Список имен столбцов.

        Returns:
            Оценочный размер в байтах.

        Raises:
            ValueError: Если оценка не удалась.
        """
        # Create a hashable key for the cache
        cache_key = (table, frozenset(columns))

        # Check if we already have a cached result
        if cache_key in self._size_estimate_cache:
            return self._size_estimate_cache[cache_key]

        try:
            # Use parameterized query instead of f-string for security
            stats_query = """
            SELECT COALESCE(SUM(avg_width), 0) AS total_width,
                   COALESCE(SUM(n_distinct), 0) AS total_distinct
            FROM pg_stats
            WHERE tablename = {} AND attname = ANY({})
            """
            result = await self.sql_driver.execute(
                stats_query,
                params=[table, columns],
                readonly=True,
            )
            if result and result[0].cells:
                size_estimate = self._estimate_index_size_internal(dict(result[0].cells))

                # Cache the result
                self._size_estimate_cache[cache_key] = size_estimate
                return size_estimate
        except Exception as e:
            error_msg = "Error estimating index size"
            raise ValueError(error_msg) from e
        else:
            return 0

    def _estimate_index_size_internal(self, stats: dict[str, Any]) -> int:
        """Оценить размер индекса по статистике.

        Args:
            stats: Словарь содержащий статистику столбцов.

        Returns:
            Оценочный размер в байтах.
        """
        width = (stats["total_width"] or 0) + 8  # 8 bytes for the heap TID
        ndistinct = stats["total_distinct"] or 1.0
        ndistinct = ndistinct if ndistinct > 0 else 1.0
        # simplistic formula
        return int(width * ndistinct * 2.0)

    async def get_table_size(self, table: str) -> int:
        """Получить полный размер таблицы включая индексы и toast таблицы.

        Использует мемоизацию для избежания повторных запросов к базе данных.

        Args:
            table: Имя таблицы

        Returns:
            Размер таблицы в байтах
        """
        # Check if we have a cached result
        if table in self._table_size_cache:
            return self._table_size_cache[table]

        # Try to get table size from the database using proper quoting
        try:
            # Use the proper way to calculate table size with quoted identifiers
            query = "SELECT pg_total_relation_size(quote_ident({})) as rel_size"
            result = await self.sql_driver.execute(query, params=[table], readonly=True)

            if result and len(result) > 0 and len(result[0].cells) > 0:
                size = int(result[0].cells["rel_size"])
                # Cache the result
                self._table_size_cache[table] = size
                return size
            # If query fails, use our estimation method
            size = await self._estimate_table_size(table)
            self._table_size_cache[table] = size
        except Exception as e:
            logger.warning("Error getting table size for %s: %s", table, e)
            # Use estimation method
            size = await self._estimate_table_size(table)
            self._table_size_cache[table] = size
            return size
        else:
            return size

    async def _estimate_table_size(self, table: str) -> int:
        """Оценить размер таблицы если мы не можем получить его из базы данных.

        Args:
            table: Имя таблицы.

        Returns:
            Оценочный размер в байтах.
        """
        try:
            # Try a simple query to get row count and then estimate size
            result = await self.sql_driver.execute(
                "SELECT count(*) as row_count FROM {}", params=[table], readonly=True
            )
            if result and len(result) > 0 and len(result[0].cells) > 0:
                row_count = int(result[0].cells["row_count"])
                # Rough estimate: assume 1KB per row
                return row_count * 1024
        except Exception as e:
            logger.warning("Error estimating table size for %s: %s", table, e)

        # Default size if we can't estimate
        return 10 * 1024 * 1024  # 10MB default
