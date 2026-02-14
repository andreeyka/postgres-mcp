import logging
import re
from typing import Any

from postgres_fastmcp.common.errors import ExplainPlanError
from postgres_fastmcp.services.index.dta_calc import DatabaseTuningAdvisor
from postgres_fastmcp.sql.driver.base import SqlExecutor
from postgres_fastmcp.sql.extensions.checker import ExtensionInspectorAdapter
from postgres_fastmcp.sql.models.index_definition import IndexDefinition
from postgres_fastmcp.sql.params.replacer import SqlParamReplacer
from postgres_fastmcp.sql.security.driver import SafeSqlExecutor

from .artifacts import ExplainPlanArtifact


logger = logging.getLogger(__name__)


class ExplainPlanTool:
    """Tool for generating and analyzing PostgreSQL explain plans."""

    def __init__(
        self,
        sql_driver: SqlExecutor | SafeSqlExecutor,
        connection_id: str = "",
    ) -> None:
        """Initialize the explain plan tool.

        Args:
            sql_driver: SQL executor for database access (and as template for param replacement).
            connection_id: Stable connection id for version/extension cache.
        """
        self.sql_driver = sql_driver
        self._ext_inspector = ExtensionInspectorAdapter(sql_driver, sql_driver, connection_id)
        self._param_replacer = SqlParamReplacer(sql_driver, sql_driver)

    async def replace_query_parameters_if_needed(self, sql_query: str) -> tuple[str, bool]:
        """Replace bind variables with sample values in a query."""
        use_generic_plan = False
        has_bind_variables = self._has_bind_variables(sql_query)

        # If query has bind variables, check PostgreSQL version for generic plan support
        if has_bind_variables:
            has_like = self._has_like_expressions(sql_query)

            meets_pg_version_requirement, _message = await self._ext_inspector.check_postgres_version_requirement(
                min_version=16,
                feature_name="Generic plan with bind variables ($1, $2, etc.)",
            )

            # If PostgreSQL < 16 or the query has LIKE expressions (which don't work with GENERIC_PLAN)
            if not meets_pg_version_requirement or has_like:
                # Replace bind variables with sample values
                logger.debug("Replacing bind variables with sample values in query")
                if meets_pg_version_requirement and has_like:
                    logger.debug("LIKE expressions detected, using parameter replacement instead of GENERIC_PLAN")
                modified_query = await self._param_replacer.replace_parameters(sql_query)
                logger.debug("Original query: %s", sql_query)
                logger.debug("Modified query: %s", modified_query)
                sql_query = modified_query
            else:
                use_generic_plan = True

        return sql_query, use_generic_plan

    async def explain(self, sql_query: str, *, do_analyze: bool = False) -> ExplainPlanArtifact:
        """Generate an EXPLAIN plan for a SQL query.

        Args:
            sql_query: The SQL query to explain.
            do_analyze: Whether to run ANALYZE (default: False).

        Returns:
            ExplainPlanArtifact.

        Raises:
            ExplainPlanError: On plan generation or conversion failure.
        """
        modified_sql_query, use_generic_plan = await self.replace_query_parameters_if_needed(sql_query)
        return await self._run_explain_query(modified_sql_query, analyze=do_analyze, generic_plan=use_generic_plan)

    async def explain_analyze(self, sql_query: str) -> ExplainPlanArtifact:
        """Generate an EXPLAIN ANALYZE plan for a SQL query.

        Args:
            sql_query: The SQL query to explain and analyze.

        Returns:
            ExplainPlanArtifact.

        Raises:
            ExplainPlanError: On plan generation or conversion failure.
        """
        return await self.explain(sql_query, do_analyze=True)

    async def explain_with_hypothetical_indexes(
        self, sql_query: str, hypothetical_indexes: list[dict[str, Any]]
    ) -> ExplainPlanArtifact:
        """Generate an explain plan for a query as if certain indexes existed.

        Args:
            sql_query: The SQL query to explain.
            hypothetical_indexes: List of index definitions as dictionaries.

        Returns:
            ExplainPlanArtifact.

        Raises:
            ExplainPlanError: On validation, plan generation, or conversion failure.
        """
        # Validate index definitions format
        if not isinstance(hypothetical_indexes, list):
            raise ExplainPlanError(f"Expected list of index definitions, got {type(hypothetical_indexes)}")

        for idx in hypothetical_indexes:
            if not isinstance(idx, dict):
                raise ExplainPlanError(f"Expected dictionary for index definition, got {type(idx)}")
            if "table" not in idx:
                raise ExplainPlanError("Missing 'table' in index definition")
            if "columns" not in idx:
                raise ExplainPlanError("Missing 'columns' in index definition")
            if not isinstance(idx["columns"], list):
                try:
                    idx["columns"] = list(idx["columns"]) if hasattr(idx["columns"], "__iter__") else [idx["columns"]]
                except Exception as e:
                    raise ExplainPlanError(f"Expected list for 'columns', got {type(idx['columns'])}: {e}") from e

        indexes = frozenset(
            IndexDefinition(
                table=idx["table"],
                columns=tuple(idx["columns"]),
                using=idx.get("using", "btree"),
            )
            for idx in hypothetical_indexes
        )

        modified_sql_query, use_generic_plan = await self.replace_query_parameters_if_needed(sql_query)
        plan_data = await self.generate_explain_plan_with_hypothetical_indexes(
            modified_sql_query, indexes, use_generic_plan=use_generic_plan
        )

        if not plan_data or not isinstance(plan_data, dict) or "Plan" not in plan_data:
            raise ExplainPlanError("Failed to generate a valid explain plan with the hypothetical indexes")

        try:
            return ExplainPlanArtifact.from_json_data(plan_data)
        except Exception as e:
            raise ExplainPlanError(f"Error converting explain plan: {e}") from e

    def _has_bind_variables(self, query: str) -> bool:
        """Check if a query contains bind variables ($1, $2, etc)."""
        return bool(re.search(r"\$\d+", query))

    def _has_like_expressions(self, query: str) -> bool:
        """Check if a query contains LIKE expressions, which don't work with GENERIC_PLAN."""
        return bool(re.search(r"\bLIKE\b", query, re.IGNORECASE))

    async def _run_explain_query(
        self, query: str, *, analyze: bool = False, generic_plan: bool = False
    ) -> ExplainPlanArtifact:
        """Run EXPLAIN query and return artifact.

        Raises:
            ExplainPlanError: On missing result, wrong type, conversion or execution failure.
        """
        try:
            explain_options = ["FORMAT JSON"]
            if analyze:
                explain_options.append("ANALYZE")
            if generic_plan:
                explain_options.append("GENERIC_PLAN")

            explain_q = f"EXPLAIN ({', '.join(explain_options)}) {query}"
            logger.debug("RUNNING EXPLAIN QUERY: %s", explain_q)
            rows = await self.sql_driver.execute(explain_q, params=None, readonly=True)
            if rows is None:
                raise ExplainPlanError("No results returned from EXPLAIN")

            query_plan_data = rows[0].cells["QUERY PLAN"]

            if not isinstance(query_plan_data, list):
                raise ExplainPlanError(f"Expected list from EXPLAIN, got {type(query_plan_data)}")
            if len(query_plan_data) == 0:
                raise ExplainPlanError("No results returned from EXPLAIN")

            plan_dict = query_plan_data[0]
            if not isinstance(plan_dict, dict):
                raise ExplainPlanError(
                    f"Expected dict in EXPLAIN result list, got {type(plan_dict)} with value {plan_dict}"
                )

            try:
                return ExplainPlanArtifact.from_json_data(plan_dict)
            except Exception as e:
                raise ExplainPlanError(f"Internal error converting explain plan - do not retry: {e}") from e
        except ExplainPlanError:
            raise
        except Exception as e:
            raise ExplainPlanError(f"Error executing explain plan: {e}") from e

    async def generate_explain_plan_with_hypothetical_indexes(
        self,
        query_text: str,
        indexes: frozenset[IndexDefinition],
        *,
        use_generic_plan: bool = False,
        dta: DatabaseTuningAdvisor | None = None,
    ) -> dict[str, Any]:
        """Generate an explain plan for a query with specified indexes.

        Args:
            query_text: The SQL query to explain.
            indexes: A frozenset of IndexDefinition objects representing the indexes to enable.
            use_generic_plan: Whether to use GENERIC_PLAN option (default: False).
            dta: Optional DatabaseTuningAdvisor instance for tracing (default: None).

        Returns:
            The explain plan as a dictionary.
        """
        try:
            # Create the indexes query
            create_indexes_query = "SELECT hypopg_reset();"
            if len(indexes) > 0:
                create_indexes_query += self.sql_driver.render(
                    "SELECT hypopg_create_index({});" * len(indexes),
                    [idx.definition for idx in indexes],
                )

            # Execute explain with the indexes
            explain_options = ["FORMAT JSON"]
            if use_generic_plan:
                explain_options.append("GENERIC_PLAN")
            if indexes:
                explain_options.append("COSTS TRUE")

            explain_plan_query = f"{create_indexes_query}EXPLAIN ({', '.join(explain_options)}) {query_text}"
            plan_result = await self.sql_driver.execute(explain_plan_query, params=None, readonly=True)

            # Extract the plan
            if plan_result and plan_result[0].cells.get("QUERY PLAN"):
                plan_data = plan_result[0].cells.get("QUERY PLAN")
                if isinstance(plan_data, list) and len(plan_data) > 0:
                    plan_dict: dict[str, Any] = plan_data[0]
                    return plan_dict
                if dta:
                    dta.dta_trace(f"      - plan_data is an empty list with plan_data type: {type(plan_data)}")

            if dta:
                dta.dta_trace("      - returning empty plan")
            # Return empty plan if no result
            return {"Plan": {"Total Cost": float("inf")}}

        except Exception:
            logger.exception("Error getting explain plan for query: %s", query_text)
            raise
