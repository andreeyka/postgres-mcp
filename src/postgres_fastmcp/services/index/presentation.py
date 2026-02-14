"""Инструмент Database Tuning Advisor (DTA) для Postgres MCP."""

import logging
import os
from typing import Any

import humanize

from postgres_fastmcp.common.utils import calculate_improvement_multiple
from postgres_fastmcp.services.explain.artifacts import ExplainPlanArtifact
from postgres_fastmcp.sql.driver.base import SqlExecutor
from postgres_fastmcp.sql.models.index_definition import IndexDefinition
from postgres_fastmcp.sql.security.driver import SafeSqlExecutor

from .index_opt_base import IndexTuningBase, IndexTuningResult


logger = logging.getLogger(__name__)


class TextPresentation:
    """Текстовое представление рекомендаций по настройке индексов."""

    def __init__(
        self,
        sql_driver: SqlExecutor | SafeSqlExecutor,
        index_tuning: IndexTuningBase,
    ) -> None:
        """Инициализация представления.

        Args:
            sql_driver: Объект SQL-драйвера PostgreSQL.
            index_tuning: Экземпляр инструмента настройки индексов.
        """
        self.sql_driver = sql_driver
        self.index_tuning = index_tuning

    async def analyze_workload(self, max_index_size_mb: int = 10000) -> dict[str, Any]:
        """Проанализировать нагрузку SQL и рекомендовать индексы.

        Анализирует запросы из истории БД (часто выполняемые и затратные)
        и рекомендует наиболее полезные индексы.

        Args:
            max_index_size_mb: Максимальный суммарный размер рекомендуемых индексов в МБ.

        Returns:
            Словарь с рекомендациями или с ключом error.
        """
        return await self._execute_analysis(
            min_calls=50,
            min_avg_time_ms=5.0,
            limit=100,
            max_index_size_mb=max_index_size_mb,
        )

    async def analyze_queries(self, queries: list[str], max_index_size_mb: int = 10000) -> dict[str, Any]:
        """Проанализировать список SQL-запросов и рекомендовать индексы.

        Анализирует переданные запросы и рекомендует индексы для улучшения их выполнения.

        Args:
            queries: Список SQL-запросов для анализа.
            max_index_size_mb: Максимальный суммарный размер рекомендуемых индексов в МБ.

        Returns:
            Словарь с рекомендациями или с ключом error.
        """
        if not queries:
            return {"error": "No queries provided for analysis"}

        return await self._execute_analysis(
            query_list=queries,
            min_calls=0,  # Ignore min calls for explicit query list
            min_avg_time_ms=0,  # Ignore min time for explicit query list
            limit=0,  # Ignore limit for explicit query list
            max_index_size_mb=max_index_size_mb,
        )

    async def analyze_single_query(self, query: str, max_index_size_mb: int = 10000) -> dict[str, Any]:
        """Проанализировать один SQL-запрос и рекомендовать индексы.

        Анализирует переданный запрос и рекомендует индексы для улучшения его выполнения.

        Args:
            query: SQL-запрос для анализа.
            max_index_size_mb: Максимальный суммарный размер рекомендуемых индексов в МБ.

        Returns:
            Словарь с рекомендациями или с ключом error.
        """
        return await self._execute_analysis(
            query_list=[query],
            min_calls=0,  # Ignore min calls for explicit query
            min_avg_time_ms=0,  # Ignore min time for explicit query
            limit=0,  # Ignore limit for explicit query
            max_index_size_mb=max_index_size_mb,
        )

    async def _execute_analysis(
        self,
        query_list: list[str] | None = None,
        min_calls: int = 50,
        min_avg_time_ms: float = 5.0,
        limit: int = 100,
        max_index_size_mb: int = 10000,
    ) -> dict[str, Any]:
        """Выполнить анализ настройки индексов.

        Args:
            query_list: Необязательный список SQL-запросов для анализа.
            min_calls: Минимальное число вызовов для учёта запроса.
            min_avg_time_ms: Минимальное среднее время выполнения в мс.
            limit: Максимальное число запросов для анализа.
            max_index_size_mb: Максимальный суммарный размер рекомендуемых индексов в МБ.

        Returns:
            Словарь с рекомендациями или с ключом error.
        """
        try:
            # Run the index tuning analysis
            session = await self.index_tuning.analyze_workload(
                query_list=query_list,
                min_calls=min_calls,
                min_avg_time_ms=min_avg_time_ms,
                limit=limit,
                max_index_size_mb=max_index_size_mb,
            )

            # Prepare the response to send back to the caller
            include_langfuse_trace = os.environ.get("POSTGRES_FASTMCP_INCLUDE_LANGFUSE_TRACE", "true").lower() == "true"
            langfuse_trace = {"_langfuse_trace": session.dta_traces} if include_langfuse_trace else {}

            if session.error:
                return {
                    "error": session.error,
                    **langfuse_trace,
                }

            if not session.recommendations:
                return {
                    "recommendations": "No index recommendations found.",
                    **langfuse_trace,
                }

            # Calculate overall statistics
            total_size_bytes = sum(rec.estimated_size_bytes for rec in session.recommendations)

            # Calculate overall performance improvement
            initial_cost = session.recommendations[0].progressive_base_cost if session.recommendations else 0
            new_cost = session.recommendations[-1].progressive_recommendation_cost if session.recommendations else 1.0
            improvement_multiple = calculate_improvement_multiple(initial_cost, new_cost)

            # Build recommendations list
            recommendations = self._build_recommendations_list(session)

            # Generate query impact section using helper function
            query_impact = await self._generate_query_impact(session)

            # Create the result JSON object with summary, recommendations, and query impact
            return {
                "summary": {
                    "total_recommendations": len(session.recommendations),
                    "base_cost": f"{initial_cost:.1f}",
                    "new_cost": f"{new_cost:.1f}",
                    "total_size_bytes": humanize.naturalsize(total_size_bytes),
                    "improvement_multiple": f"{improvement_multiple:.1f}",
                },
                "recommendations": recommendations,
                "query_impact": query_impact,
                **langfuse_trace,
            }
        except Exception as e:
            logger.exception("Error analyzing queries")
            return {"error": f"Error analyzing queries: {e}"}

    def _build_recommendations_list(self, session: IndexTuningResult) -> list[dict[str, Any]]:
        """Собрать список рекомендаций из сессии.

        Args:
            session: Сессия IndexTuningResult.

        Returns:
            Список словарей с рекомендациями.
        """
        recommendations = []
        for index_apply_order, rec in enumerate(session.recommendations):
            rec_dict = {
                "index_apply_order": index_apply_order + 1,
                "index_target_table": rec.table,
                "index_target_columns": rec.columns,
                "benefit_of_this_index_only": {
                    "improvement_multiple": f"{rec.individual_improvement_multiple:.1f}",
                    "base_cost": f"{rec.individual_base_cost:.1f}",
                    "new_cost": f"{rec.individual_recommendation_cost:.1f}",
                },
                "benefit_after_previous_indexes": {
                    "improvement_multiple": f"{rec.progressive_improvement_multiple:.1f}",
                    "base_cost": f"{rec.progressive_base_cost:.1f}",
                    "new_cost": f"{rec.progressive_recommendation_cost:.1f}",
                },
                "index_estimated_size": humanize.naturalsize(rec.estimated_size_bytes),
                "index_definition": rec.definition,
            }
            if rec.potential_problematic_reason == "long_text_column":
                rec_dict["warning"] = (
                    "This index is potentially problematic because it includes a long text column. "
                    "You might not be able to create this index if the index row size becomes too large "
                    "(i.e., more than 8191 bytes)."
                )
            elif rec.potential_problematic_reason:
                rec_dict["warning"] = (
                    f"This index is potentially problematic because it includes a "
                    f"{rec.potential_problematic_reason} column."
                )
            recommendations.append(rec_dict)
        return recommendations

    async def _generate_query_impact(self, session: IndexTuningResult) -> list[dict[str, Any]]:
        """Сформировать секцию влияния запросов (план до/после).

        Args:
            session: IndexTuningResult с рекомендациями.

        Returns:
            Список словарей с запросом и планами объяснения.
        """
        query_impact: list[dict[str, Any]] = []

        # Get workload queries from the first recommendation
        # (All recommendations have the same queries)
        if not session.recommendations:
            return query_impact

        workload_queries = session.recommendations[0].queries

        # Remove duplicates while preserving order
        seen = set()
        unique_queries = []
        for q in workload_queries:
            if q not in seen:
                seen.add(q)
                unique_queries.append(q)

        # Get before and after plans for each query
        if unique_queries and self.index_tuning:
            for query in unique_queries:
                # Get plan with no indexes
                before_plan = await self.index_tuning.get_explain_plan_with_indexes(query, frozenset())

                # Get plan with all recommended indexes
                index_configs = frozenset(
                    IndexDefinition(rec.table, rec.columns, rec.using) for rec in session.recommendations
                )
                after_plan = await self.index_tuning.get_explain_plan_with_indexes(query, index_configs)

                # Extract costs from plans
                base_cost = self.index_tuning.extract_cost_from_json_plan(before_plan)
                new_cost = self.index_tuning.extract_cost_from_json_plan(after_plan)

                # Calculate improvement multiple
                improvement_multiple = "∞"  # Default for cases where new_cost is zero
                if new_cost > 0 and base_cost > 0:
                    improvement_multiple = f"{calculate_improvement_multiple(base_cost, new_cost):.1f}"

                before_plan_text = ExplainPlanArtifact.format_plan_summary(before_plan)
                after_plan_text = ExplainPlanArtifact.format_plan_summary(after_plan)
                diff_text = ExplainPlanArtifact.create_plan_diff(
                    before_plan, after_plan, calculate_improvement_multiple
                )

                # Add to query impact with costs and improvement
                query_impact.append(
                    {
                        "query": query,
                        "base_cost": f"{base_cost:.1f}",
                        "new_cost": f"{new_cost:.1f}",
                        "improvement_multiple": improvement_multiple,
                        "before_explain_plan": "```\n" + before_plan_text + "\n```",
                        "after_explain_plan": "```\n" + after_plan_text + "\n```",
                        "explain_plan_diff": "```\n" + diff_text + "\n```",
                    }
                )

        return query_impact
