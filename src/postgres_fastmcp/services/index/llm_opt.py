"""Имплементация оптимизации индексов на основе LLM."""

import json
import logging
from dataclasses import dataclass
from typing import Any, override

from fastmcp import Context
from pglast.ast import SelectStmt
from pydantic import BaseModel, ValidationError

from postgres_fastmcp.services.explain.explain_plan import ExplainPlanTool
from postgres_fastmcp.sql.ast.visitors import TableAliasVisitor
from postgres_fastmcp.sql.driver.base import SqlExecutor
from postgres_fastmcp.sql.models.index_definition import IndexDefinition
from postgres_fastmcp.sql.security.driver import SafeSqlExecutor

from .index_opt_base import IndexRecommendation, IndexTuningBase


logger = logging.getLogger(__name__)


# We introduce a Pydantic index class to facilitate communication with the LLM
# via MCP Context sampling.
class Index(BaseModel):
    """Модель Pydantic для представления индекса при общении с LLM.

    Этот класс используется для структурированного общения с LLM
    через выборку контекста MCP для рекомендаций индексов.
    """

    table_name: str
    columns: tuple[str, ...]

    def __hash__(self) -> int:
        """Вычислить хеш для индекса.

        Returns:
            Значение хеша на основе имени таблицы и столбцов.
        """
        return hash((self.table_name, self.columns))

    def __eq__(self, other: object) -> bool:
        """Проверить равенство с другим объектом.

        Args:
            other: Объект для сравнения.

        Returns:
            True если объекты равны, False иначе.
        """
        if not isinstance(other, Index):
            return False
        return self.table_name == other.table_name and self.columns == other.columns

    def to_index_recommendation(self) -> IndexRecommendation:
        """Преобразовать в IndexRecommendation.

        Returns:
            Экземпляр IndexRecommendation.
        """
        return IndexRecommendation(table=self.table_name, columns=self.columns)

    def to_index_definition(self) -> IndexDefinition:
        """Преобразовать в IndexDefinition.

        Returns:
            Экземпляр IndexDefinition.
        """
        return IndexDefinition(table=self.table_name, columns=self.columns)


class IndexingAlternative(BaseModel):
    """Модель Pydantic для ответа LLM с альтернативными конфигурациями индексов.

    Эта модель представляет структурированный ответ от LLM, содержащий
    несколько альтернативных наборов индексов для оценки.
    """

    alternatives: list[set[Index]]


@dataclass
class ScoredIndexes:
    """Класс данных для представления оценённой конфигурации индексов.

    Содержит индексы, стоимость выполнения, размер индексов и вычисленную
    целевую оценку для конкретной конфигурации индексов.
    """

    indexes: set[Index]
    execution_cost: float
    index_size: float
    objective_score: float


class LLMOptimizerTool(IndexTuningBase):
    """Инструмент оптимизации индексов на основе LLM."""

    def __init__(
        self,
        sql_driver: SqlExecutor | SafeSqlExecutor,
        ctx: Context,
        connection_id: str = "",
        max_no_progress_attempts: int = 5,
        pareto_alpha: float = 2.0,
    ) -> None:
        """Инициализация LLMOptimizerTool.

        Args:
            sql_driver: SQL executor for database access.
            ctx: MCP Context for LLM sampling via ctx.sample().
            connection_id: Stable connection id for extension/version cache.
            max_no_progress_attempts: Maximum number of attempts without progress.
            pareto_alpha: Pareto optimization alpha parameter.
        """
        super().__init__(sql_driver, connection_id=connection_id, pareto_alpha=pareto_alpha)
        self.sql_driver = sql_driver
        self.ctx = ctx
        self.max_no_progress_attempts = max_no_progress_attempts
        logger.info(
            "Initialized LLMOptimizerTool with max_no_progress_attempts=%d",
            max_no_progress_attempts,
        )

    def score(self, execution_cost: float, index_size: float) -> float:
        """Calculate objective score for a configuration.

        Args:
            execution_cost: Query execution cost.
            index_size: Total index size.

        Returns:
            Objective score value.
        """
        return self._pareto_objective(execution_cost, index_size)

    async def _get_recommendations_via_context(self, user_prompt: str) -> str:
        """Get index recommendations using MCP Context sampling.

        Args:
            user_prompt: User prompt with query and context information.

        Returns:
            JSON string response from LLM.
        """
        system_prompt = (
            "You are a helpful assistant that generates index recommendations for a given workload. "
            "Always respond with valid JSON only, no additional text."
        )

        response = await self.ctx.sample(
            messages=user_prompt,
            system_prompt=system_prompt,
            temperature=1.2,
            max_tokens=2000,
        )

        # ctx.sample() returns TextContent | ImageContent | AudioContent
        # For our use case, we expect TextContent
        if not hasattr(response, "text"):
            error_msg = f"Unexpected response type from ctx.sample(): {type(response)}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        return response.text or ""

    def _parse_index_alternatives_from_json(self, json_text: str) -> list[set[Index]]:  # noqa: C901
        """Parse JSON response from LLM into IndexingAlternative structure.

        Args:
            json_text: JSON string response from LLM.

        Returns:
            List of sets of Index objects representing alternative index configurations.

        Raises:
            ValueError: If JSON parsing or validation fails.
        """
        # Try to extract JSON from markdown code blocks if present
        json_text_clean = json_text.strip()
        if "```json" in json_text_clean:
            # Extract JSON from markdown code block
            start = json_text_clean.find("```json") + 7
            end = json_text_clean.find("```", start)
            if end != -1:
                json_text_clean = json_text_clean[start:end].strip()
        elif "```" in json_text_clean:
            # Extract JSON from generic code block
            start = json_text_clean.find("```") + 3
            end = json_text_clean.find("```", start)
            if end != -1:
                json_text_clean = json_text_clean[start:end].strip()

        try:
            # Parse JSON
            data = json.loads(json_text_clean)
        except json.JSONDecodeError as e:
            error_msg = f"Invalid JSON response from LLM: {e}"
            logger.error("Failed to parse JSON response from LLM: %s. Response: %s", e, json_text_clean[:500])
            raise ValueError(error_msg) from e

        try:
            # Validate and convert to IndexingAlternative
            indexing_alt = IndexingAlternative(**data)
        except ValidationError as e:
            error_msg = f"Invalid IndexingAlternative structure: {e}"
            logger.error("Failed to validate IndexingAlternative structure: %s. Data: %s", e, data)
            raise ValueError(error_msg) from e

        # Convert to list of sets of Index objects
        result: list[set[Index]] = []
        for alt_list in indexing_alt.alternatives:
            index_set: set[Index] = set()
            for idx_dict in alt_list:
                if isinstance(idx_dict, dict):
                    table_name = idx_dict.get("table_name", "")
                    columns = idx_dict.get("columns", [])
                    if table_name and columns:
                        index_set.add(Index(table_name=table_name, columns=tuple(columns)))
                elif isinstance(idx_dict, Index):
                    index_set.add(idx_dict)
            if index_set:
                result.append(index_set)

        return result

    @override
    async def _generate_recommendations(  # noqa: C901
        self, query_weights: list[tuple[str, SelectStmt, float]]
    ) -> tuple[set[IndexRecommendation], float]:
        """Generate index tuning queries using optimization by LLM.

        Args:
            query_weights: List of tuples containing query text, parsed statement, and weight.

        Returns:
            Tuple of recommended indexes and final cost.

        Raises:
            ValueError: If LLM optimization fails or only one query is supported.
        """
        # For now we support only one table at a time
        if len(query_weights) > 1:
            error_msg = "Optimization by LLM supports only one query at a time."
            logger.error("LLM optimization currently supports only one query at a time")
            raise ValueError(error_msg)

        query = query_weights[0][0]
        parsed_query = query_weights[0][1]
        logger.info("Generating index recommendations for query: %s", query)

        # Extract tables from the parsed query
        table_visitor = TableAliasVisitor()
        table_visitor(parsed_query)
        tables = table_visitor.tables
        logger.info("Extracted tables from query: %s", tables)

        # Get the size of the tables
        table_sizes = {}
        for table in tables:
            table_sizes[table] = await self._get_table_size(table)
        total_table_size = sum(table_sizes.values())
        logger.info("Total table size: %s", total_table_size)

        # Generate explain plan for the query (raises ExplainPlanError on failure)
        explain_tool = ExplainPlanTool(self.sql_driver, connection_id=self._connection_id)
        explain_result = await explain_tool.explain(query)

        # Get the explain plan JSON
        explain_plan_json_str = explain_result.value
        explain_plan_json = json.loads(explain_plan_json_str)
        logger.debug("Generated explain plan: %s", explain_plan_json_str)

        # Extract indexes used in the explain plan
        indexes_used: set[Index] = await self._extract_indexes_from_explain_plan_with_columns(explain_plan_json)

        # Get the current cost
        original_cost = await self._evaluate_configuration_cost(query_weights, frozenset())
        logger.info("Original query cost: %f", original_cost)

        original_config = ScoredIndexes(
            indexes=indexes_used,
            execution_cost=original_cost,
            index_size=total_table_size,
            objective_score=self.score(original_cost, total_table_size),
        )

        best_config = original_config

        # Initialize attempt history for this run
        attempt_history: list[ScoredIndexes] = [original_config]

        no_progress_count = 0

        # Starting cost
        # Note: Should include the size of the starting indexes in future improvements
        score = self.score(original_cost, total_table_size)
        logger.info("Starting score: %f", score)

        while no_progress_count < self.max_no_progress_attempts:
            logger.info("Requesting index recommendations from LLM")

            # Build history of past attempts
            history_prompt = ""
            if attempt_history:
                history_prompt = "\nPrevious attempts and their costs:\n"
                for attempt in attempt_history:
                    indexes_str = ";".join(idx.to_index_definition().definition for idx in attempt.indexes)
                    history_prompt += (
                        f"- Indexes: {indexes_str}, Cost: {attempt.execution_cost}, Index Size: {attempt.index_size}, "
                    )
                    history_prompt += f"Objective Score: {attempt.objective_score}\n"

            if no_progress_count > 0:
                remaining_attempts_prompt = f"You have made {no_progress_count} attempts without progress. "
                if self.max_no_progress_attempts - no_progress_count < self.max_no_progress_attempts / 2:
                    remaining_attempts_prompt += "Get creative and suggest indexes that are not obvious."
            else:
                remaining_attempts_prompt = ""

            existing_indexes_str = ";".join(idx.to_index_definition().definition for idx in indexes_used)
            user_prompt = (
                f"Here is the query we are optimizing: {query}\n"
                f"Here is the explain plan: {explain_plan_json_str}\n"
                f"Here are the existing indexes: {existing_indexes_str}\n"
                f"{history_prompt}\n"
                "Each indexing suggestion that you provide is a combination of indexes. "
                "You can provide multiple alternative suggestions. "
                "We will evaluate each alternative using hypopg to see how the optimizer "
                "will behave with those indexes in place. "
                "The overall score is based on a combination of execution cost and "
                "index size. In all cases, lower is better. "
                "Prefer fewer indexes to more indexes. "
                "Prefer indexes with fewer columns to indexes with more columns. "
                f"{remaining_attempts_prompt}\n\n"
                "Please respond with a JSON object in the following format:\n"
                '{"alternatives": [\n'
                '  [{"table_name": "table1", "columns": ["col1", "col2"]}, '
                '{"table_name": "table2", "columns": ["col3"]}],\n'
                '  [{"table_name": "table1", "columns": ["col1"]}]\n'
                "]}\n"
                "Each inner array represents one alternative set of indexes. "
                "Each object in the array represents one index with table_name and columns."
            )

            # Use ctx.sample() to get recommendations from client's LLM
            response_text = await self._get_recommendations_via_context(user_prompt)
            index_alternatives = self._parse_index_alternatives_from_json(response_text)
            logger.info("Received %d alternative index configurations from LLM", len(index_alternatives))

            # If no alternatives were generated, break the loop
            if not index_alternatives:
                logger.warning("No index alternatives were generated by the LLM")
                break

            # Try each alternative
            found_improvement = False
            for i, index_set in enumerate(index_alternatives):
                try:
                    logger.info(
                        "Evaluating alternative %d/%d with %d indexes", i + 1, len(index_alternatives), len(index_set)
                    )
                    # Evaluate this index configuration
                    execution_cost_estimate = await self._evaluate_configuration_cost(
                        query_weights, frozenset({index.to_index_definition() for index in index_set})
                    )
                    logger.info(
                        "Alternative %d cost: %f (reduction: %.2f%%)",
                        i + 1,
                        execution_cost_estimate,
                        ((best_config.execution_cost - execution_cost_estimate) / best_config.execution_cost) * 100,
                    )

                    # Estimate the size of the indexes
                    index_size_estimate = await self._estimate_index_size_2(
                        {index.to_index_definition() for index in index_set}, 1024 * 1024
                    )
                    logger.info("Estimated index size: %f", index_size_estimate)

                    # Score based on a balance of size and performance
                    score = self.score(execution_cost_estimate, total_table_size + index_size_estimate)

                    # Record this attempt in history
                    latest_config = ScoredIndexes(
                        indexes={Index(table_name=index.table_name, columns=index.columns) for index in index_set},
                        execution_cost=execution_cost_estimate,
                        index_size=index_size_estimate,
                        objective_score=score,
                    )
                    attempt_history.append(latest_config)
                    logger.info("Latest config: %s", latest_config)

                    # If this is better than what we've seen so far, update our best
                    # Minimum 2% improvement required
                    if latest_config.objective_score < best_config.objective_score:
                        best_config = latest_config
                        found_improvement = True
                except Exception as e:
                    # We discard the alternative. We are seeing this happen due to invalid index definitions.
                    logger.error("Error evaluating alternative %d/%d: %s", i + 1, len(index_alternatives), str(e))

            # Keep only the 5 best results in the attempt history
            attempt_history.sort(key=lambda x: x.objective_score)
            attempt_history = attempt_history[:5]

            if found_improvement:
                no_progress_count = 0
            else:
                no_progress_count += 1
                logger.info(
                    "No improvement found in this iteration. Attempts without progress: %d/%d",
                    no_progress_count,
                    self.max_no_progress_attempts,
                )

        if best_config != original_config:
            logger.info(
                "Selected best index configuration with %d indexes, cost reduction: %.2f%%, indexes: %s",
                len(best_config.indexes),
                ((original_cost - best_config.execution_cost) / original_cost) * 100,
                ", ".join(f"{idx.table_name}.({','.join(idx.columns)})" for idx in best_config.indexes),
            )
        else:
            logger.info("No better index configuration found")

        # Convert Index objects to IndexConfig objects for return
        best_index_config_set = {index.to_index_recommendation() for index in best_config.indexes}
        return (best_index_config_set, best_config.execution_cost)

    async def _estimate_index_size_2(
        self, index_set: set[IndexDefinition], min_size_penalty: float = 1024 * 1024
    ) -> float:
        """Estimate the size of a set of indexes using hypopg.

        Args:
            index_set: Set of IndexDefinition objects representing the indexes to estimate.
            min_size_penalty: Minimum size penalty in bytes.

        Returns:
            Total estimated size of all indexes in bytes.
        """
        if not index_set:
            return 0.0

        total_size = 0.0

        for index_config in index_set:
            try:
                # Create a hypothetical index using hypopg
                # Using a tuple to avoid LiteralString type error
                create_index_query = (
                    "WITH hypo_index AS (SELECT indexrelid FROM hypopg_create_index({})) "
                    "SELECT hypopg_relation_size(indexrelid) as size, hypopg_drop_index(indexrelid) FROM hypo_index;"
                )

                # Execute the query to get the index size
                result = await self.sql_driver.execute(
                    create_index_query,
                    params=[index_config.definition],
                    readonly=True,
                )

                if result and len(result) > 0:
                    # Extract the size from the result
                    size = result[0].cells.get("size", 0)
                    total_size += max(float(size), min_size_penalty)
                    logger.debug("Estimated size for index %s: %s bytes", index_config.name, size)
                else:
                    logger.warning("Failed to estimate size for index %s", index_config.name)

            except Exception as e:
                logger.error("Error estimating size for index %s: %s", index_config.name, e)

        return total_size

    def _extract_indexes_from_explain_plan(self, explain_plan_json: dict[str, Any]) -> set[tuple[str, str]]:
        """Extract indexes used in the explain plan JSON.

        Args:
            explain_plan_json: The explain plan JSON from PostgreSQL.

        Returns:
            A set of tuples (table_name, index_name) representing the indexes used in the plan.
        """
        indexes_used = set()
        if isinstance(explain_plan_json, dict):
            plan_data = explain_plan_json.get("Plan")
            if plan_data is not None:

                def extract_indexes_from_node(node: dict[str, Any]) -> None:
                    """Extract indexes from a plan node recursively.

                    Args:
                        node: Plan node dictionary to process.
                    """
                    # Check if this is an index scan node
                    node_type = node.get("Node Type")
                    if (
                        node_type in ["Index Scan", "Index Only Scan", "Bitmap Index Scan"]
                        and "Index Name" in node
                        and "Relation Name" in node
                    ):
                        # Add the table name and index name
                        indexes_used.add((node["Relation Name"], node["Index Name"]))

                    # Recursively process child plans
                    if "Plans" in node:
                        for child in node["Plans"]:
                            extract_indexes_from_node(child)

                # Start extraction from the root plan
                extract_indexes_from_node(plan_data)
                logger.info("Extracted %d indexes from explain plan", len(indexes_used))

        return indexes_used

    async def _extract_indexes_from_explain_plan_with_columns(self, explain_plan_json: dict[str, Any]) -> set[Index]:
        """Extract indexes used in the explain plan JSON and populate their columns.

        Args:
            explain_plan_json: The explain plan JSON from PostgreSQL.

        Returns:
            A set of Index objects representing the indexes used in the plan with their columns.
        """
        # First extract the indexes without columns
        index_tuples = self._extract_indexes_from_explain_plan(explain_plan_json)

        # Now populate the columns for each index
        indexes_with_columns = set()
        for table_name, index_name in index_tuples:
            # Get the columns for this index
            columns = await self._get_index_columns(index_name)

            # Create a new Index object with the columns
            index_with_columns = Index(table_name=table_name, columns=columns)
            indexes_with_columns.add(index_with_columns)

        return indexes_with_columns

    async def _get_index_columns(self, index_name: str) -> tuple[str, ...]:
        """Get the columns for a specific index by querying the database.

        Args:
            index_name: The name of the index.

        Returns:
            A tuple of column names in the index.
        """
        try:
            # Query to get index columns
            query = """
            SELECT a.attname
            FROM pg_index i
            JOIN pg_class c ON c.oid = i.indexrelid
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE c.relname = {}
            ORDER BY array_position(i.indkey, a.attnum)
            """

            result = await self.sql_driver.execute(query, params=[index_name], readonly=True)

            if result and len(result) > 0:
                # Extract column names from the result
                columns = [row.cells.get("attname", "") for row in result if row.cells.get("attname")]
                return tuple(columns)
            logger.warning("No columns found for index %s", index_name)

        except Exception as e:
            logger.error("Error getting columns for index %s: %s", index_name, e)
            return ()
        else:
            return ()
