"""Базовые классы и утилиты для оптимизации индексов."""

import json
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pglast import parse_sql
from pglast.ast import Node, SelectStmt

from postgres_fastmcp.common.utils import calculate_improvement_multiple
from postgres_fastmcp.services.explain.explain_plan import ExplainPlanTool
from postgres_fastmcp.sql.ast.visitors import TableAliasVisitor
from postgres_fastmcp.sql.driver.base import SqlExecutor
from postgres_fastmcp.sql.extensions.checker import ExtensionInspectorAdapter
from postgres_fastmcp.sql.models.index_definition import IndexDefinition
from postgres_fastmcp.sql.params.replacer import SqlParamReplacer
from postgres_fastmcp.sql.security.driver import SafeSqlExecutor


logger = logging.getLogger(__name__)

MAX_NUM_INDEX_TUNING_QUERIES = 10


def pp_list(lst: list[Any]) -> str:
    """Красиво вывести список для отладки.

    Args:
        lst: Список для красивого вывода.

    Returns:
        Форматированная строка с представлением списка.
    """
    return ("\n  - " if len(lst) > 0 else "") + "\n  - ".join([str(item) for item in lst])


@dataclass
class IndexRecommendation:
    """Представляет индекс базы данных с оценкой размера и определением."""

    _definition: IndexDefinition
    estimated_size_bytes: int = 0
    potential_problematic_reason: str | None = None

    def __init__(
        self,
        table: str,
        columns: tuple[str, ...],
        using: str = "btree",
        estimated_size_bytes: int = 0,
        potential_problematic_reason: str | None = None,
    ) -> None:
        """Инициализация IndexRecommendation.

        Args:
            table: Имя таблицы.
            columns: Кортеж имен столбцов.
            using: Тип индекса (по умолчанию "btree").
            estimated_size_bytes: Оценочный размер в байтах.
            potential_problematic_reason: Причина если индекс потенциально проблематичен.
        """
        self._definition = IndexDefinition(table, columns, using)
        self.estimated_size_bytes = estimated_size_bytes
        self.potential_problematic_reason = potential_problematic_reason

    @property
    def index_definition(self) -> IndexDefinition:
        """Получить объект определения индекса.

        Returns:
            Объект IndexDefinition с именем таблицы, столбцами и типом индекса.
        """
        return self._definition

    @property
    def definition(self) -> str:
        """Получить строку SQL определения для этого индекса.

        Returns:
            Строка с SQL CREATE INDEX.
        """
        return self._definition.definition

    @property
    def name(self) -> str:
        """Получить сгенерированное имя индекса.

        Returns:
            Строка с именем индекса.
        """
        return self._definition.name

    @property
    def columns(self) -> tuple[str, ...]:
        """Получить имена столбцов для этого индекса.

        Returns:
            Кортеж имен столбцов.
        """
        return self._definition.columns

    @property
    def table(self) -> str:
        """Получить имя таблицы для этого индекса.

        Returns:
            Строка с именем таблицы.
        """
        return self._definition.table

    @property
    def using(self) -> str:
        """Получить тип индекса (например, 'btree', 'hash').

        Returns:
            Строка с типом индекса.
        """
        return self._definition.using

    def __hash__(self) -> int:
        return self._definition.__hash__()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, IndexRecommendation):
            return False
        return self._definition.__eq__(other.index_definition)

    def __str__(self) -> str:
        return self._definition.__str__() + f" (estimated_size_bytes: {self.estimated_size_bytes})"

    def __repr__(self) -> str:
        return self._definition.__repr__() + f" (estimated_size_bytes: {self.estimated_size_bytes})"


@dataclass
class IndexRecommendationAnalysis:
    """Представляет рекомендованный индекс с оценкой выгоды."""

    index_recommendation: IndexRecommendation

    progressive_base_cost: float
    progressive_recommendation_cost: float
    individual_base_cost: float
    individual_recommendation_cost: float
    queries: list[str]
    definition: str

    @property
    def table(self) -> str:
        """Получить имя таблицы для этой рекомендации индекса.

        Returns:
            Строка с именем таблицы.
        """
        return self.index_recommendation.table

    @property
    def columns(self) -> tuple[str, ...]:
        """Получить имена столбцов для этой рекомендации индекса.

        Returns:
            Кортеж имен столбцов.
        """
        return self.index_recommendation.columns

    @property
    def using(self) -> str:
        """Получить тип индекса для этой рекомендации.

        Returns:
            Строка с типом индекса (например, 'btree', 'hash').
        """
        return self.index_recommendation.using

    @property
    def progressive_improvement_multiple(self) -> float:
        """Вычислить процентное улучшение по прогрессивной рекомендации.

        Returns:
            Множитель улучшения как число с плавающей точкой.
        """
        return calculate_improvement_multiple(self.progressive_base_cost, self.progressive_recommendation_cost)

    @property
    def potential_problematic_reason(self) -> str | None:
        """Получить причину если индекс потенциально проблематичен.

        Returns:
            Строка с описанием проблемы или None если нет проблем.
        """
        return self.index_recommendation.potential_problematic_reason

    @property
    def estimated_size_bytes(self) -> int:
        """Получить оценочный размер этого индекса в байтах.

        Returns:
            Оценочный размер в байтах.
        """
        return self.index_recommendation.estimated_size_bytes

    @property
    def individual_improvement_multiple(self) -> float:
        """Вычислить процентное улучшение по индивидуальной рекомендации.

        Returns:
            Множитель улучшения как число с плавающей точкой.
        """
        return calculate_improvement_multiple(self.individual_base_cost, self.individual_recommendation_cost)

    def to_index(self) -> IndexRecommendation:
        """Преобразовать этот анализ в IndexRecommendation.

        Returns:
            Объект IndexRecommendation.
        """
        return self.index_recommendation


@dataclass
class IndexTuningResult:
    """Результаты анализа настройки индексов."""

    # Session ID for tracing
    session_id: str

    # Input parameters
    budget_mb: int  # Tuning budget in MB
    workload_source: str = "n/a"  # 'args', 'query_list', 'query_store', 'sql_file'
    workload: list[dict[str, Any]] | None = None

    # Output results
    recommendations: list[IndexRecommendationAnalysis] = field(default_factory=list)
    error: str | None = None
    dta_traces: list[str] = field(default_factory=list)


def candidate_str(
    indexes: Iterable[IndexDefinition] | Iterable[IndexRecommendation] | Iterable[IndexRecommendationAnalysis],
) -> str:
    """Преобразовать индексы в строковое представление.

    Args:
        indexes: Итерируемый объект определений или рекомендаций индексов.

    Returns:
        Строковое представление индексов.
    """
    return ", ".join(f"{idx.table}({','.join(idx.columns)})" for idx in indexes) if indexes else "(no indexes)"


class IndexTuningBase(ABC):
    """Базовый класс для алгоритмов настройки индексов."""

    def __init__(
        self,
        sql_driver: SqlExecutor | SafeSqlExecutor,
        connection_id: str = "",
    ) -> None:
        """Инициализация IndexTuningBase.

        Args:
            sql_driver: SQL исполнитель для доступа к базе данных.
            connection_id: Стабильный идентификатор соединения для кэша версии/расширения.
        """
        self.sql_driver = sql_driver
        self._connection_id = connection_id

        # Add memoization caches
        self.cost_cache: dict[frozenset[IndexDefinition], float] = {}
        self._size_estimate_cache: dict[tuple[str, frozenset[str]], int] = {}
        self._table_size_cache: dict[str, int] = {}
        self._estimate_table_size_cache: dict[str, int] = {}
        self._explain_plans_cache: dict[tuple[str, frozenset[IndexDefinition]], dict[str, Any]] = {}
        self._param_replacer = SqlParamReplacer(sql_driver, sql_driver)
        self._ext_inspector = ExtensionInspectorAdapter(sql_driver, sql_driver, connection_id)

        # Add trace accumulator
        self._dta_traces: list[str] = []

    async def analyze_workload(  # noqa: PLR0913, C901
        self,
        workload: list[dict[str, Any]] | None = None,
        sql_file: str | None = None,
        query_list: list[str] | None = None,
        min_calls: int = 50,
        min_avg_time_ms: float = 5.0,
        limit: int = MAX_NUM_INDEX_TUNING_QUERIES,
        max_index_size_mb: int = -1,
    ) -> IndexTuningResult:
        """Анализировать нагрузку запросов и рекомендовать индексы.

        Этот метод может анализировать нагрузку из трёх разных источников (в порядке приоритета):
        1. Явная нагрузка переданная как параметр
        2. Прямой список SQL запросов переданный как query_list
        3. SQL файл с запросами
        4. Статистика запросов из pg_stat_statements

        Args:
            workload: Необязательные явные данные нагрузки
            sql_file: Необязательный путь к файлу с SQL запросами
            query_list: Необязательный список строк SQL запросов для анализа
            min_calls: Минимальное количество вызовов для учета запроса (для pg_stat_statements)
            min_avg_time_ms: Минимальное среднее время выполнения в мс (для pg_stat_statements)
            limit: Максимальное количество запросов для анализа (для pg_stat_statements)
            max_index_size_mb: Максимальный общий размер рекомендуемых индексов в МБ

        Returns:
            IndexTuningResult с результатами анализа
        """
        session_id = str(int(time.time()))
        self._analysis_start_time = time.time()
        self._dta_traces = []  # Reset traces at start of analysis

        # Clear the cache at the beginning of each analysis
        self._size_estimate_cache = {}

        if max_index_size_mb > 0:
            self.budget_mb = max_index_size_mb

        session = IndexTuningResult(
            session_id=session_id,
            budget_mb=max_index_size_mb,
        )

        prechecks_passed = False
        try:
            # Run pre-checks
            precheck_result = await self._run_prechecks(session)
            if precheck_result:
                return precheck_result
            prechecks_passed = True

            # First try to use explicit workload if provided
            if workload:
                logger.debug("Using explicit workload with %d queries", len(workload))
                session.workload_source = "args"
                session.workload = workload
            # Then try direct query list if provided
            elif query_list:
                logger.debug("Using provided query list with %d queries", len(query_list))
                session.workload_source = "query_list"
                session.workload = []
                for i, query in enumerate(query_list):
                    # Create a synthetic workload entry for each query
                    session.workload.append(
                        {
                            "query": query,
                            "queryid": f"direct-{i}",
                        }
                    )

            # Then try SQL file if provided
            elif sql_file:
                logger.debug("Reading queries from file: %s", sql_file)
                session.workload_source = "sql_file"
                session.workload = self._get_workload_from_file(sql_file)

            # Finally fall back to query stats
            else:
                logger.debug("Using query statistics from the database")
                session.workload_source = "query_store"
                session.workload = await self._get_query_stats(min_calls, min_avg_time_ms, limit)

            if not session.workload:
                logger.warning("No workload to analyze")
                return session

            session.workload = await self._validate_and_parse_workload(session.workload)

            query_weights = self._covert_workload_to_query_weights(session.workload)

            if query_weights is None or len(query_weights) == 0:
                self.dta_trace("No query provided")
                session.recommendations = []
            else:
                # Gather queries as strings
                workload_queries = [q for q, _, _ in query_weights]

                self.dta_trace(f"Workload queries ({len(workload_queries)}): {pp_list(workload_queries)}")

                # Generate and evaluate index recommendations
                recommendations: tuple[set[IndexRecommendation], float] = await self._generate_recommendations(
                    query_weights
                )
                session.recommendations = await self._format_recommendations(query_weights, recommendations)

        except Exception as e:
            logger.exception("Error in workload analysis")
            session.error = f"Error in workload analysis: {e}"
        finally:
            # Always drop the hypothetical indexes created during analysis, even on error,
            # so they do not leak into the connection's session state.
            if prechecks_passed:
                await self._reset_hypopg_quietly()

        session.dta_traces = self._dta_traces
        return session

    async def _reset_hypopg_quietly(self) -> None:
        """Сбросить гипотетические индексы hypopg, подавляя ошибки (расширение может быть недоступно)."""
        try:
            await self.sql_driver.execute("SELECT hypopg_reset();", params=None, readonly=True)
        except Exception:
            logger.debug("hypopg_reset failed (extension may be unavailable)", exc_info=True)

    async def _run_prechecks(self, session: IndexTuningResult) -> IndexTuningResult | None:
        """Выполнить предварительные проверки перед анализом.

        Вернуть сессию с ошибкой, если какая-либо проверка не пройдена.

        Args:
            session: Текущий объект DTASession

        Returns:
            DTASession с информацией об ошибке если какая-либо проверка не пройдена, None если все проверки пройдены
        """
        # Pre-check 1: Check HypoPG with more granular feedback
        # Use our new utility function to check HypoPG status
        is_hypopg_installed, hypopg_message = await self._ext_inspector.check_hypopg_installation_status()

        # If hypopg is not installed or not available, add error to session
        if not is_hypopg_installed:
            session.error = hypopg_message
            return session

        # Pre-check 2: Check if ANALYZE has been run at least once
        result = await self.sql_driver.execute(
            "SELECT s.last_analyze FROM pg_stat_user_tables s ORDER BY s.last_analyze LIMIT 1;",
            params=None,
            readonly=True,
        )
        if not result or not any(row.cells.get("last_analyze") is not None for row in result):
            error_message = (
                "Statistics are not up-to-date. The database needs to be analyzed first. "
                "Please run 'ANALYZE;' on your database before using the tuning advisor. "
                "Without up-to-date statistics, the index recommendations may be inaccurate."
            )
            session.error = error_message
            logger.error(error_message)
            return session

        # All checks passed
        return None

    async def _validate_and_parse_workload(self, workload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Валидировать нагрузку для обеспечения ее аналитичности."""
        validated_workload = []
        for q in workload:
            query_text = q["query"]
            if not query_text:
                logger.debug("Skipping empty query")
                continue
            query_text = query_text.strip().lower()

            # Replace parameter placeholders with dummy values
            query_text = await self._param_replacer.replace_parameters(query_text)

            parsed = parse_sql(query_text)
            if not parsed:
                logger.debug("Skipping non-parseable query: %s...", query_text[:50])
                continue
            stmt = parsed[0].stmt
            if not self._is_analyzable_stmt(stmt):
                logger.debug("Skipping non-analyzable query: %s...", query_text[:50])
                continue

            q["query"] = query_text
            q["stmt"] = stmt
            validated_workload.append(q)
        return validated_workload

    def _covert_workload_to_query_weights(self, workload: list[dict[str, Any]]) -> list[tuple[str, SelectStmt, float]]:
        """Преобразовать нагрузку в веса запросов на основе частоты запросов."""
        return [(q["query"], q["stmt"], self.convert_query_info_to_weight(q)) for q in workload]

    def convert_query_info_to_weight(self, query_info: dict[str, Any]) -> float:
        """Преобразовать информацию о запросе в вес на основе частоты запросов."""
        calls_value: Any = query_info.get("calls", 1.0)
        avg_exec_time_value: Any = query_info.get("avg_exec_time", 1.0)
        calls = float(calls_value) if calls_value is not None else 1.0
        avg_exec_time = float(avg_exec_time_value) if avg_exec_time_value is not None else 1.0
        return calls * avg_exec_time

    async def get_explain_plan_with_indexes(
        self, query_text: str, indexes: frozenset[IndexDefinition]
    ) -> dict[str, Any]:
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

        explain_plan_tool = ExplainPlanTool(self.sql_driver, connection_id=self._connection_id)
        plan = await explain_plan_tool.generate_explain_plan_with_hypothetical_indexes(
            query_text, indexes, use_generic_plan=False
        )

        # Cache the result
        self._explain_plans_cache[cache_key] = plan
        return plan

    def _get_workload_from_file(self, file_path: str) -> list[dict[str, Any]]:
        """Загрузить запросы из SQL файла.

        Args:
            file_path: Путь к SQL файлу.

        Returns:
            Список словарей нагрузки.

        Raises:
            ValueError: Если файл не может быть прочитан.
        """
        try:
            with Path(file_path).open() as f:
                content = f.read()

            # Split the file content by semicolons to get individual queries
            query_texts = [q.strip() for q in content.split(";") if q.strip()]
            queries = []

            for i, text in enumerate(query_texts):
                queries.append(
                    {
                        "queryid": i,
                        "query": text,
                    }
                )

        except Exception as e:
            error_msg = f"Error loading queries from file {file_path}"
            raise ValueError(error_msg) from e
        else:
            return queries

    async def _get_query_stats(self, min_calls: int, min_avg_time_ms: float, limit: int) -> list[dict[str, Any]]:
        """Получить статистику запросов из pg_stat_statements.

        Args:
            min_calls: Минимальное количество вызовов.
            min_avg_time_ms: Минимальное среднее время выполнения в миллисекундах.
            limit: Максимальное количество возвращаемых запросов.

        Returns:
            Список словарей статистики запросов.
        """
        # Reference to original implementation
        return await self._get_query_stats_direct(min_calls, min_avg_time_ms, limit)

    async def _get_query_stats_direct(
        self, min_calls: int = 50, min_avg_time_ms: float = 5.0, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Прямая реализация сбора статистики запросов.

        Args:
            min_calls: Минимальное количество вызовов.
            min_avg_time_ms: Минимальное среднее время выполнения в миллисекундах.
            limit: Максимальное количество возвращаемых запросов.

        Returns:
            Список словарей статистики запросов.
        """
        query = """
        SELECT queryid, query, calls, total_exec_time/calls as avg_exec_time
        FROM pg_stat_statements
        WHERE calls >= {}
        AND total_exec_time/calls >= {}
        ORDER BY total_exec_time DESC
        LIMIT {}
        """
        result = await self.sql_driver.execute(
            query,
            params=[min_calls, min_avg_time_ms, limit],
            readonly=True,
        )
        return [dict(row.cells) for row in result] if result else []

    def _is_analyzable_stmt(self, stmt: Node) -> bool:
        """Проверить, может ли оператор быть проанализирован для рекомендаций индексов.

        Args:
            stmt: Разобранный узел AST оператора.

        Returns:
            True если оператор может быть проанализирован, False иначе.
        """
        # It should be a SelectStmt
        if not isinstance(stmt, SelectStmt):
            return False

        visitor = TableAliasVisitor()
        visitor(stmt)

        # Skip queries that only access system tables
        return not all(table.startswith(("pg_", "aurora_")) for table in visitor.tables)

    def dta_trace(self, message: str) -> None:
        """Удобная функция для логирования процесса мышления DTA.

        Args:
            message: Сообщение для логирования.
        """
        # Always log to debug
        logger.debug(message)

        self._dta_traces.append(message)

    async def _evaluate_configuration_cost(
        self,
        weighted_workload: list[tuple[str, SelectStmt, float]],
        indexes: frozenset[IndexDefinition],
    ) -> float:
        """Оценить общую стоимость с выборочным включением и кэшированием."""
        # Use indexes as cache key
        if indexes in self.cost_cache:
            self.dta_trace(f"  - Using cached cost for configuration: {candidate_str(indexes)}")
            return self.cost_cache[indexes]

        self.dta_trace(f"  - Evaluating cost for configuration: {candidate_str(indexes)}")

        total_cost = 0.0
        valid_queries = 0

        try:
            # Calculate cost for all queries with this configuration
            for query_text, _stmt, weight in weighted_workload:
                try:
                    # Get the explain plan using our memoized helper
                    plan_data = await self.get_explain_plan_with_indexes(query_text, indexes)

                    # Extract cost from the plan data
                    cost = self.extract_cost_from_json_plan(plan_data)
                    total_cost += cost * weight
                    valid_queries += 1
                except Exception as e:
                    error_msg = f"Error executing explain for query: {query_text}"
                    raise ValueError(error_msg) from e

            if valid_queries == 0:
                self.dta_trace("    + no valid queries found for cost evaluation")
                return float("inf")

            avg_cost = total_cost / valid_queries
            self.cost_cache[indexes] = avg_cost
            self.dta_trace(f"    + config cost: {avg_cost:.2f} (from {valid_queries} queries)")

        except Exception as e:
            self.dta_trace(f"    + error evaluating configuration: {e}")
            error_msg = "Error evaluating configuration"
            raise ValueError(error_msg) from e
        else:
            return avg_cost

    async def _estimate_index_size(self, table: str, columns: list[str]) -> int:
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

    async def _format_recommendations(
        self, query_weights: list[tuple[str, SelectStmt, float]], best_config: tuple[set[IndexRecommendation], float]
    ) -> list[IndexRecommendationAnalysis]:
        """Форматировать рекомендации в список объектов IndexRecommendationAnalysis.

        Args:
            query_weights: Список кортежей с текстом запроса, разобранным оператором и весом.
            best_config: Кортеж лучшего набора индексов и стоимости.

        Returns:
            Список отформатированных анализов рекомендаций индексов.
        """
        # build final recommendations from best_config
        recommendations: list[IndexRecommendationAnalysis] = []
        total_size = 0
        budget_bytes = self.budget_mb * 1024 * 1024
        individual_base_cost = await self._evaluate_configuration_cost(query_weights, frozenset()) or 1.0
        progressive_base_cost = individual_base_cost
        indexes_so_far: list[IndexRecommendation] = []
        for index_config in best_config[0]:
            indexes_so_far.append(index_config)
            # Calculate the cost with only this index
            progressive_cost = await self._evaluate_configuration_cost(
                query_weights,
                frozenset(idx.index_definition for idx in indexes_so_far),  # Indexes so far
            )
            individual_cost = await self._evaluate_configuration_cost(
                query_weights,
                frozenset([index_config.index_definition]),  # Only this index
            )

            size = await self._estimate_index_size(index_config.table, list(index_config.columns))
            if budget_bytes < 0 or total_size + size <= budget_bytes:
                self.dta_trace(f"Adding index: {candidate_str([index_config])}")
                rec = IndexRecommendationAnalysis(
                    index_recommendation=IndexRecommendation(
                        table=index_config.table,
                        columns=index_config.columns,
                        using=index_config.using,
                        potential_problematic_reason=index_config.potential_problematic_reason,
                        estimated_size_bytes=size,
                    ),
                    progressive_base_cost=progressive_base_cost,
                    progressive_recommendation_cost=progressive_cost,
                    individual_base_cost=individual_base_cost,
                    individual_recommendation_cost=individual_cost,
                    queries=[q for q, _, _ in query_weights],
                    definition=index_config.definition,
                )
                progressive_base_cost = progressive_cost
                recommendations.append(rec)
                total_size += size
            else:
                self.dta_trace(f"Skipping index: {candidate_str([index_config])} because it exceeds budget")

        return recommendations

    @staticmethod
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

    async def _get_table_size(self, table: str) -> int:
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

    @abstractmethod
    async def _generate_recommendations(
        self, query_weights: list[tuple[str, SelectStmt, float]]
    ) -> tuple[set[IndexRecommendation], float]:
        """Генерировать запросы настройки индексов."""
