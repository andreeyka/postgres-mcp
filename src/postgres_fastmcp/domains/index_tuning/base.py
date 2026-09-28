"""Базовый класс алгоритмов настройки индексов: оркестрация анализа нагрузки."""

import logging
import math
import time
from abc import ABC, abstractmethod
from typing import Any

from pglast.ast import SelectStmt

from postgres_fastmcp.postgres.extensions import ExtensionInspectorAdapter
from postgres_fastmcp.postgres.params.replacer import SqlParamReplacer
from postgres_fastmcp.postgres.ports import QueryExecutorPort, SqlDriverPort

from .cost_eval import CostEvaluator
from .models import (
    MAX_NUM_INDEX_TUNING_QUERIES,
    IndexRecommendation,
    IndexRecommendationAnalysis,
    IndexTuningResult,
    candidate_str,
    pp_list,
)
from .workload import get_query_stats, load_workload_from_file, validate_and_parse_workload, workload_to_query_weights


logger = logging.getLogger(__name__)


class IndexTuningBase(ABC):
    """Базовый класс для алгоритмов настройки индексов."""

    def __init__(
        self,
        sql_driver: SqlDriverPort,
        *,
        catalog_driver: QueryExecutorPort,
        connection_id: str = "",
        pareto_alpha: float = 2.0,
        budget_mb: int = -1,
    ) -> None:
        """Инициализация IndexTuningBase.

        Args:
            sql_driver: SQL исполнитель для доступа к базе данных.
            catalog_driver: Исполнитель служебных запросов (проверка расширений и версии).
            connection_id: Стабильный идентификатор соединения для кэша версии/расширения.
            pareto_alpha: Вес размера индексов в целевой функции Парето.
            budget_mb: Бюджет хранилища в МБ (-1 без ограничения).
        """
        self.sql_driver = sql_driver
        self._connection_id = connection_id
        self.pareto_alpha = pareto_alpha
        self.budget_mb = budget_mb
        self._analysis_start_time = 0.0

        self._param_replacer = SqlParamReplacer(sql_driver, sql_driver)
        self._ext_inspector = ExtensionInspectorAdapter(catalog_driver, connection_id)
        self.cost_eval = CostEvaluator(
            sql_driver, catalog_driver=catalog_driver, connection_id=connection_id, trace=self.dta_trace
        )

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

        # Clear the size cache at the beginning of each analysis
        self.cost_eval.reset_size_cache()

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
                session.workload = load_workload_from_file(sql_file)

            # Finally fall back to query stats
            else:
                logger.debug("Using query statistics from the database")
                session.workload_source = "query_store"
                session.workload = await get_query_stats(self.sql_driver, min_calls, min_avg_time_ms, limit)

            if not session.workload:
                logger.warning("No workload to analyze")
                return session

            session.workload = await validate_and_parse_workload(session.workload, self._param_replacer)

            query_weights = workload_to_query_weights(session.workload)

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
        """Сбросить гипотетические индексы hypopg, подавляя ошибки (расширение может быть недоступно).

        Подстраховка на случай, если sql_driver не поверх DbConnPool (например прямое соединение
        в библиотечном вызове или в тестах): тогда пул не помечает и не сбрасывает соединение сам,
        и без этого вызова индексы, созданные за время анализа, остались бы в сессии. Если исполнитель
        поверх пула, соединение уже сброшено пулом при возврате — этот вызов на нём не находит ничего
        и является no-op.
        """
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

    def dta_trace(self, message: str) -> None:
        """Удобная функция для логирования процесса мышления DTA.

        Args:
            message: Сообщение для логирования.
        """
        # Always log to debug
        logger.debug(message)

        self._dta_traces.append(message)

    def _pareto_objective(self, execution_cost: float, total_size_bytes: float) -> float:
        """Целевая функция Парето: log(стоимость) + alpha * log(размер).

        Единая формула для всех алгоритмов настройки индексов (DTA):
        меньше — лучше, alpha задаёт вес размера относительно стоимости.

        Args:
            execution_cost: Стоимость выполнения нагрузки (по EXPLAIN).
            total_size_bytes: Полный размер конфигурации (таблицы + индексы) в байтах.

        Returns:
            Значение целевой функции; float("inf") при неположительных аргументах.
        """
        if execution_cost <= 0 or total_size_bytes <= 0:
            return float("inf")
        return math.log(execution_cost) + self.pareto_alpha * math.log(total_size_bytes)

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
        individual_base_cost = await self.cost_eval.evaluate_configuration_cost(query_weights, frozenset()) or 1.0
        progressive_base_cost = individual_base_cost
        indexes_so_far: list[IndexRecommendation] = []
        for index_config in best_config[0]:
            indexes_so_far.append(index_config)
            # Calculate the cost with only this index
            progressive_cost = await self.cost_eval.evaluate_configuration_cost(
                query_weights,
                frozenset(idx.index_definition for idx in indexes_so_far),  # Indexes so far
            )
            individual_cost = await self.cost_eval.evaluate_configuration_cost(
                query_weights,
                frozenset([index_config.index_definition]),  # Only this index
            )

            size = await self.cost_eval.estimate_index_size(index_config.table, list(index_config.columns))
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

    @abstractmethod
    async def _generate_recommendations(
        self, query_weights: list[tuple[str, SelectStmt, float]]
    ) -> tuple[set[IndexRecommendation], float]:
        """Генерировать запросы настройки индексов."""
