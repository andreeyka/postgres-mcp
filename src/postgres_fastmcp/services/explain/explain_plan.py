# ruff: noqa: TRY301
import logging
import re
from typing import Any

from postgres_fastmcp.common.errors import ExplainPlanError, ExplainPlanExecutionError
from postgres_fastmcp.sql.extensions import ExtensionInspectorAdapter
from postgres_fastmcp.sql.models import IndexDefinition
from postgres_fastmcp.sql.params.replacer import SqlParamReplacer
from postgres_fastmcp.sql.ports import SqlDriverPort

from .artifacts import ExplainPlanArtifact


logger = logging.getLogger(__name__)

INDEX_KEY_TABLE = "table"
INDEX_KEY_COLUMNS = "columns"

# Шаблоны сообщений ExplainPlanError (сами тексты — контракт с тестами и клиентом).
NO_EXPLAIN_RESULTS = "No results returned from EXPLAIN"
MSG_INDEXES_NOT_LIST = "Expected list of index definitions, got {got}"
MSG_INDEX_DEF_NOT_DICT = "Expected dictionary for index definition, got {got}"
MSG_MISSING_KEY = "Missing '{key}' in index definition"
MSG_COLUMNS_NOT_LIST = "Expected list for 'columns', got {got}: {detail}"
MSG_HYPOTHETICAL_PLAN_FAILED = "Failed to generate a valid explain plan with the hypothetical indexes"
MSG_PLAN_CONVERSION = "Error converting explain plan: {error}"
MSG_PLAN_INTERNAL_CONVERSION = "Internal error converting explain plan - do not retry: {error}"
MSG_EXPLAIN_NOT_LIST = "Expected list from EXPLAIN, got {got}"
MSG_EXPLAIN_ITEM_NOT_DICT = "Expected dict in EXPLAIN result list, got {got} with value {value}"


class ExplainPlanTool:
    """Инструмент для генерации и анализа планов выполнения PostgreSQL."""

    def __init__(
        self,
        sql_driver: SqlDriverPort,
        connection_id: str = "",
    ) -> None:
        """Инициализация инструмента объяснения планов.

        Args:
            sql_driver: SQL-исполнитель для доступа к БД (и как шаблон для подстановки параметров).
            connection_id: Стабильный идентификатор соединения для кэша версии/расширений.
        """
        self.sql_driver = sql_driver
        self._ext_inspector = ExtensionInspectorAdapter(sql_driver, sql_driver, connection_id)
        self._param_replacer = SqlParamReplacer(sql_driver, sql_driver)

    async def replace_query_parameters_if_needed(self, sql_query: str) -> tuple[str, bool]:
        """Подставить вместо плейсхолдеров ($1, $2, …) примерные значения в запросе."""
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
        """Сформировать план EXPLAIN для SQL-запроса.

        Args:
            sql_query: SQL-запрос для объяснения.
            do_analyze: Выполнять ли ANALYZE (по умолчанию False).

        Returns:
            ExplainPlanArtifact.

        Raises:
            ExplainPlanError: При ошибке генерации или преобразования плана.
        """
        modified_sql_query, use_generic_plan = await self.replace_query_parameters_if_needed(sql_query)
        return await self._run_explain_query(modified_sql_query, analyze=do_analyze, generic_plan=use_generic_plan)

    async def explain_analyze(self, sql_query: str) -> ExplainPlanArtifact:
        """Сформировать план EXPLAIN ANALYZE для SQL-запроса.

        Args:
            sql_query: SQL-запрос для объяснения и анализа.

        Returns:
            ExplainPlanArtifact.

        Raises:
            ExplainPlanError: При ошибке генерации или преобразования плана.
        """
        return await self.explain(sql_query, do_analyze=True)

    async def explain_with_hypothetical_indexes(
        self,
        sql_query: str,
        hypothetical_indexes: Any,  # noqa: ANN401
    ) -> ExplainPlanArtifact:
        """Сформировать план объяснения для запроса с учётом гипотетических индексов.

        Args:
            sql_query: SQL-запрос для объяснения.
            hypothetical_indexes: Список определений индексов в виде словарей (проверяется в runtime).

        Returns:
            ExplainPlanArtifact.

        Raises:
            ExplainPlanError: При ошибке валидации, генерации или преобразования плана.
        """
        if not isinstance(hypothetical_indexes, list):
            raise ExplainPlanError(MSG_INDEXES_NOT_LIST.format(got=type(hypothetical_indexes)))

        for idx in hypothetical_indexes:
            if not isinstance(idx, dict):
                raise ExplainPlanError(MSG_INDEX_DEF_NOT_DICT.format(got=type(idx)))
            if INDEX_KEY_TABLE not in idx:
                raise ExplainPlanError(MSG_MISSING_KEY.format(key=INDEX_KEY_TABLE))
            if INDEX_KEY_COLUMNS not in idx:
                raise ExplainPlanError(MSG_MISSING_KEY.format(key=INDEX_KEY_COLUMNS))
            if not isinstance(idx["columns"], list):
                try:
                    idx["columns"] = list(idx["columns"]) if hasattr(idx["columns"], "__iter__") else [idx["columns"]]
                except Exception as e:
                    raise ExplainPlanError(MSG_COLUMNS_NOT_LIST.format(got=type(idx["columns"]), detail=e)) from e

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
            raise ExplainPlanError(MSG_HYPOTHETICAL_PLAN_FAILED)

        try:
            return ExplainPlanArtifact.from_json_data(plan_data)
        except Exception as e:
            raise ExplainPlanError(MSG_PLAN_CONVERSION.format(error=e)) from e

    def _has_bind_variables(self, query: str) -> bool:
        """Проверить, есть ли в запросе плейсхолдеры ($1, $2, …)."""
        return bool(re.search(r"\$\d+", query))

    def _has_like_expressions(self, query: str) -> bool:
        """Проверить, есть ли в запросе выражения LIKE (не совместимы с GENERIC_PLAN)."""
        return bool(re.search(r"\bLIKE\b", query, re.IGNORECASE))

    async def _run_explain_query(
        self, query: str, *, analyze: bool = False, generic_plan: bool = False
    ) -> ExplainPlanArtifact:
        """Выполнить EXPLAIN и вернуть артефакт плана.

        Raises:
            ExplainPlanError: При отсутствии результата, неверном типе, ошибке преобразования или выполнения.
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
                raise ExplainPlanError(NO_EXPLAIN_RESULTS)

            query_plan_data = rows[0].cells["QUERY PLAN"]

            if not isinstance(query_plan_data, list):
                raise ExplainPlanError(MSG_EXPLAIN_NOT_LIST.format(got=type(query_plan_data)))
            if len(query_plan_data) == 0:
                raise ExplainPlanError(NO_EXPLAIN_RESULTS)

            plan_dict = query_plan_data[0]
            if not isinstance(plan_dict, dict):
                raise ExplainPlanError(MSG_EXPLAIN_ITEM_NOT_DICT.format(got=type(plan_dict), value=plan_dict))

            try:
                return ExplainPlanArtifact.from_json_data(plan_dict)
            except Exception as e:
                raise ExplainPlanError(MSG_PLAN_INTERNAL_CONVERSION.format(error=e)) from e
        except ExplainPlanError:
            raise
        except Exception as e:
            raise ExplainPlanExecutionError(e) from e

    async def generate_explain_plan_with_hypothetical_indexes(
        self,
        query_text: str,
        indexes: frozenset[IndexDefinition],
        *,
        use_generic_plan: bool = False,
    ) -> dict[str, Any]:
        """Сформировать план объяснения для запроса с указанным набором индексов.

        Args:
            query_text: SQL-запрос для объяснения.
            indexes: Frozenset объектов IndexDefinition — индексы для включения.
            use_generic_plan: Использовать опцию GENERIC_PLAN (по умолчанию False).

        Returns:
            План объяснения в виде словаря.
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
                logger.debug("plan_data is an empty list with plan_data type: %s", type(plan_data))

            logger.debug("returning empty plan")
            # Return empty plan if no result
            return {"Plan": {"Total Cost": float("inf")}}

        except Exception:
            logger.exception("Error getting explain plan for query: %s", query_text)
            raise
