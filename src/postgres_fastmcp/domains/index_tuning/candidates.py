"""Генерация и фильтрация кандидатов индексов для Database Tuning Advisor."""

import logging
from collections.abc import Callable
from itertools import combinations
from typing import Any

from pglast.ast import SelectStmt

from postgres_fastmcp.postgres.params.replacer import SqlParamReplacer
from postgres_fastmcp.postgres.ports import SqlDriverPort

from .condition_collector import ConditionColumnCollector
from .index_compare import index_exists
from .models import IndexRecommendation


logger = logging.getLogger(__name__)


class CandidateGenerator:
    """Генератор кандидатов индексов из нагрузки запросов.

    Извлекает столбцы из условий запросов (WHERE/JOIN/HAVING/ORDER BY),
    строит комбинации столбцов до заданной ширины индекса и отфильтровывает
    дубликаты существующих индексов и проблемные (длинные текстовые) столбцы.
    """

    def __init__(
        self,
        sql_driver: SqlDriverPort,
        param_replacer: SqlParamReplacer,
        *,
        max_index_width: int = 3,
        min_column_usage: int = 1,
        trace: Callable[[str], None] | None = None,
    ) -> None:
        """Инициализация CandidateGenerator.

        Args:
            sql_driver: SQL исполнитель для доступа к базе данных.
            param_replacer: Заменитель параметров (используется для извлечения столбцов).
            max_index_width: Максимальное количество столбцов в индексе.
            min_column_usage: Пропускать столбцы используемые в меньшем числе запросов.
            trace: Необязательный обработчик трассировочных сообщений (например, dta_trace).
        """
        self.sql_driver = sql_driver
        self._param_replacer = param_replacer
        self.max_index_width = max_index_width
        self.min_column_usage = min_column_usage
        self._trace: Callable[[str], None] = trace if trace is not None else logger.debug

    async def get_existing_indexes(self) -> list[dict[str, Any]]:
        """Получить все существующие индексы.

        Returns:
            List of dictionaries containing index information.

        TODO: We should get the indexes that are relevant to the query.
        """
        query = """
        SELECT schemaname as schema,
               tablename as table,
               indexname as name,
               indexdef as definition
        FROM pg_indexes
        WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
        ORDER BY schemaname, tablename, indexname
        """
        result = await self.sql_driver.execute(query, params=None, readonly=True)
        if result is not None:
            return [dict(row.cells) for row in result]
        return []

    async def generate(  # noqa: C901
        self, workload: list[tuple[str, SelectStmt, float]], existing_defs: set[str]
    ) -> list[IndexRecommendation]:
        """Generate index candidates from queries with batch creation.

        Args:
            workload: List of tuples containing query text, parsed statement, and weight.
            existing_defs: Set of existing index definitions to filter out.

        Returns:
            List of index recommendations.
        """
        table_columns_usage: dict[str, dict[str, int]] = {}  # table -> {col -> usage_count}
        # Extract columns from all queries
        for _q, stmt, _ in workload:
            columns_per_table = self._param_replacer.extract_stmt_columns(stmt)
            for tbl, cols in columns_per_table.items():
                if tbl not in table_columns_usage:
                    table_columns_usage[tbl] = {}
                for c in cols:
                    table_columns_usage[tbl][c] = table_columns_usage[tbl].get(c, 0) + 1

        # Filter out rarely used columns
        # e.g. skip columns that appear in fewer than self.min_column_usage queries
        table_columns: dict[str, set[str]] = {}
        for tbl, usage_map in table_columns_usage.items():
            kept_cols = {c for c, usage in usage_map.items() if usage >= self.min_column_usage}
            if kept_cols:
                table_columns[tbl] = kept_cols

        # Build column cache for accurate column existence checks
        all_tables = set(table_columns.keys())
        column_cache = await self._build_column_cache(all_tables)

        # Extract columns used in conditions (WHERE/JOIN/HAVING/ORDER BY) for optimization
        # This allows us to generate only relevant index candidates instead of all combinations
        condition_columns = self._collect_condition_columns(workload, column_cache)

        # Generate candidates only from columns used in conditions (optimized approach)
        # Intersect with table_columns to ensure we only use frequently used columns
        candidates = []
        for table, cols in table_columns.items():
            # Use intersection: columns that are both frequently used AND in conditions
            condition_cols = condition_columns.get(table, set())
            relevant_cols = cols & condition_cols  # Intersection

            # If no condition columns found, fall back to all columns (safety fallback)
            # This handles edge cases where conditions might not be detected
            if not relevant_cols and cols:
                relevant_cols = cols

            if relevant_cols:
                col_list = list(relevant_cols)
                for width in range(1, min(self.max_index_width, len(col_list)) + 1):
                    candidates.extend(
                        [
                            IndexRecommendation(table=table, columns=tuple(combo))
                            for combo in combinations(col_list, width)
                        ]
                    )

        # filter out duplicates with existing indexes
        filtered_candidates = [c for c in candidates if not index_exists(c, existing_defs)]

        # Note: Filtering by query conditions is no longer needed since we already
        # generate candidates only from condition columns, but we keep it for safety
        condition_filtered1 = await self._filter_candidates_by_query_conditions(workload, filtered_candidates)

        # filter out long text columns
        condition_filtered = await self._filter_long_text_columns(condition_filtered1)

        self._trace(f"Generated {len(candidates)} total candidates")
        self._trace(f"Filtered to {len(filtered_candidates)} after removing existing indexes.")
        self._trace(f"Filtered to {len(condition_filtered1)} after removing unused columns.")
        self._trace(f"Filtered to {len(condition_filtered)} after removing long text columns.")
        await self._estimate_hypothetical_index_sizes(condition_filtered)
        return condition_filtered

    async def _estimate_hypothetical_index_sizes(self, candidates: list[IndexRecommendation]) -> None:
        """Создать гипотетические индексы и прочитать их размеры одним запросом на одном соединении.

        hypopg-индексы живут в памяти сессии, а пул сбрасывает их на соединении сразу после того,
        как оно освобождается (reset-callback DbConnPool): раздельные execute для создания индексов
        и для чтения hypopg_list_indexes получили бы для чтения уже другое, чистое соединение из
        пула, и estimated_size_bytes остался бы 0. Один SQL с обоими операторами гарантирует одно
        и то же соединение для обоих; сбрасывать индексы явно здесь не нужно — это сделает пул при
        возврате соединения (SqlExecutor помечает его, видя hypopg_create_index в запросе).

        Args:
            candidates: Кандидаты индексов; у совпавших по имени с hypopg_list_indexes проставляется
                estimated_size_bytes.
        """
        if not candidates:
            return
        query = "SELECT hypopg_create_index({});" * len(candidates)
        query += "SELECT index_name, hypopg_relation_size(indexrelid) AS index_size FROM hypopg_list_indexes;"
        result = await self.sql_driver.execute(
            query,
            params=[idx.definition for idx in candidates],
            readonly=True,
        )
        if result is None:
            return
        index_map = {r.cells["index_name"]: r.cells["index_size"] for r in result}
        for idx in candidates:
            if idx.name in index_map:
                idx.estimated_size_bytes = index_map[idx.name]

    def _collect_condition_columns(
        self, workload: list[tuple[str, SelectStmt, float]], column_cache: dict[str, set[str]]
    ) -> dict[str, set[str]]:
        """Собрать столбцы, используемые в условиях, по всем запросам нагрузки.

        Args:
            workload: Список кортежей с текстом запроса, разобранным оператором и весом.
            column_cache: Кэш таблица -> множество имен столбцов.

        Returns:
            Словарь таблица -> множество столбцов, используемых в условиях.

        Raises:
            ValueError: Если извлечение столбцов условия не удалось.
        """
        condition_columns: dict[str, set[str]] = {}
        for _, stmt, _ in workload:
            try:
                collector = ConditionColumnCollector(column_cache=column_cache)
                collector(stmt)
                query_condition_columns = collector.condition_columns
                for table, cols in query_condition_columns.items():
                    if table not in condition_columns:
                        condition_columns[table] = set()
                    condition_columns[table].update(cols)
            except Exception as e:
                error_msg = "Error extracting condition columns from query"
                raise ValueError(error_msg) from e
        return condition_columns

    async def _build_column_cache(self, tables: set[str]) -> dict[str, set[str]]:
        """Build a cache of table -> set of column names from the database.

        Args:
            tables: Set of table names to cache columns for.

        Returns:
            Dictionary mapping table names (lowercase) to sets of column names (lowercase).
        """
        if not tables:
            return {}

        column_cache: dict[str, set[str]] = {}

        # Build query to get all columns for the tables
        tables_list = list(tables)

        query = """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
            AND table_name = ANY({})
            ORDER BY table_name, ordinal_position
        """

        try:
            result = await self.sql_driver.execute(query, params=[tables_list], readonly=True)
            if result:
                for row in result:
                    table_name = row.cells["table_name"].lower()
                    column_name = row.cells["column_name"].lower()
                    if table_name not in column_cache:
                        column_cache[table_name] = set()
                    column_cache[table_name].add(column_name)
        except Exception as e:
            logger.warning("Error building column cache: %s. Continuing without cache.", e)
            return {}

        return column_cache

    async def _filter_candidates_by_query_conditions(
        self, workload: list[tuple[str, SelectStmt, float]], candidates: list[IndexRecommendation]
    ) -> list[IndexRecommendation]:
        """Filter out index candidates that contain columns not used in query conditions.

        Args:
            workload: List of tuples containing query text, parsed statement, and weight.
            candidates: List of candidate indexes to filter.

        Returns:
            Filtered list of index recommendations.
        """
        if not workload or not candidates:
            return candidates

        # Build column cache for accurate column existence checks
        all_tables = set()
        for candidate in candidates:
            all_tables.add(candidate.table)
        column_cache = await self._build_column_cache(all_tables)

        # Extract all columns used in conditions across all queries
        condition_columns = self._collect_condition_columns(workload, column_cache)

        # Filter candidates - keep only those where all columns are in condition_columns
        filtered_candidates = []
        for candidate in candidates:
            table = candidate.table
            if table not in condition_columns:
                continue

            # Check if all columns in the index are used in conditions
            all_columns_used = all(col in condition_columns[table] for col in candidate.columns)
            if all_columns_used:
                filtered_candidates.append(candidate)

        return filtered_candidates

    async def _filter_long_text_columns(  # noqa: C901
        self, candidates: list[IndexRecommendation], max_text_length: int = 100
    ) -> list[IndexRecommendation]:
        """Filter out indexes that contain long text columns based on catalog information.

        Args:
            candidates: List of candidate indexes
            max_text_length: Maximum allowed text length (default: 100)

        Returns:
            Filtered list of indexes
        """
        if not candidates:
            return []

        # First, get all unique table.column combinations
        table_columns = set()
        for candidate in candidates:
            for column in candidate.columns:
                table_columns.add((candidate.table, column))

        # Parameterized lists of table / column names (names come from parsed user queries,
        # so they must never be string-interpolated into the SQL).
        pairs = list(table_columns)
        tables = [table for table, _ in pairs]
        columns = [col for _, col in pairs]

        # Query to get column types and their length limits from catalog
        type_query = """
            SELECT
                c.table_name,
                c.column_name,
                c.data_type,
                c.character_maximum_length,
                pg_stats.avg_width,
                CASE
                    WHEN c.data_type = 'text' THEN true
                    WHEN (c.data_type = 'character varying' OR c.data_type = 'varchar' OR
                         c.data_type = 'character' OR c.data_type = 'char') AND
                         (c.character_maximum_length IS NULL OR c.character_maximum_length > {})
                    THEN true
                    ELSE false
                END as potential_long_text
            FROM information_schema.columns c
            LEFT JOIN pg_stats ON
                pg_stats.tablename = c.table_name AND
                pg_stats.attname = c.column_name
            WHERE c.table_name = ANY({})
            AND c.column_name = ANY({})
        """

        result = await self.sql_driver.execute(type_query, params=[max_text_length, tables, columns], readonly=True)

        logger.debug("Column types and length limits: %s", result)

        if not result:
            logger.debug("No column types and length limits found")
            return []

        # Process results and identify problematic columns
        problematic_columns = set()
        potential_problematic_columns = set()

        for row in result:
            table = row.cells["table_name"]
            column = row.cells["column_name"]
            potential_long = row.cells["potential_long_text"]
            avg_width = row.cells.get("avg_width")

            # Use avg_width from pg_stats as a heuristic - if it's high, likely contains long text
            if potential_long and (avg_width is None or avg_width > max_text_length * 0.4):
                problematic_columns.add((table, column))
                logger.debug("Identified potentially long text column: %s.%s (avg_width: %s)", table, column, avg_width)
            elif potential_long:
                potential_problematic_columns.add((table, column))

        # Filter candidates based on column information
        filtered_candidates = []
        for candidate in candidates:
            valid = True
            for column in candidate.columns:
                if (candidate.table, column) in problematic_columns:
                    valid = False
                    logger.debug("Skipping index candidate with long text column: %s.%s", candidate.table, column)
                    break
                if (candidate.table, column) in potential_problematic_columns:
                    candidate.potential_problematic_reason = "long_text_column"

            if valid:
                filtered_candidates.append(candidate)

        return filtered_candidates
