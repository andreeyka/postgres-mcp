"""Сервис объяснения планов выполнения запросов (фасад модуля explain)."""

from typing import Any, Literal

from postgres_fastmcp.common.errors import (
    ExplainAnalyzeNotSupportedError,
    ExplainPlanExecutionError,
    HypopgNotInstalledError,
)
from postgres_fastmcp.services.db_access_service import DbAccessService
from postgres_fastmcp.services.explain.explain_plan import ExplainPlanTool
from postgres_fastmcp.sql.extensions.checker import ExtensionInspectorAdapter


ExplainMode = Literal["plain", "analyze", "hypothetical"]


class ExplainService:
    """Сервис для объяснения планов выполнения SQL запросов."""

    def __init__(self, db: DbAccessService, mode: ExplainMode = "plain") -> None:
        """Инициализация сервиса с подключением к базе данных и режимом объяснения."""
        self.db = db
        self._mode = mode

    async def explain_query(
        self,
        sql: str,
        *,
        hypothetical_indexes: list[dict[str, Any]] | None = None,
    ) -> str:
        """Объяснить план выполнения для SQL запроса.

        Args:
            sql: SQL запрос для объяснения.
            hypothetical_indexes: Список гипотетических индексов (только для режима hypothetical).

        Returns:
            Строка с объяснением плана выполнения.

        Raises:
            ExplainAnalyzeWithHypotheticalError: Если analyze и hypothetical_indexes использованы вместе.
            HypopgNotInstalledError: Если HypoPG не установлен при использовании hypothetical_indexes.
            ExplainPlanError: При ошибке генерации плана от ExplainPlanTool.
        """
        sql_driver = self.db.sql_driver
        connection_id = self.db.connection_id
        explain_tool = ExplainPlanTool(
            sql_driver=sql_driver,
            connection_id=connection_id,
        )
        hypothetical_indexes = hypothetical_indexes or []

        if self._mode == "hypothetical":
            if not hypothetical_indexes:
                result = await explain_tool.explain(sql)
            else:
                ext_inspector = ExtensionInspectorAdapter(sql_driver, sql_driver, connection_id)
                is_hypopg_installed, hypopg_message = await ext_inspector.check_hypopg_installation_status()
                if not is_hypopg_installed:
                    raise HypopgNotInstalledError(hypopg_message)
                result = await explain_tool.explain_with_hypothetical_indexes(sql, hypothetical_indexes)
        elif self._mode == "analyze":
            try:
                result = await explain_tool.explain_analyze(sql)
            except ExplainPlanExecutionError as e:
                cause = getattr(e, "__cause__", None) or getattr(e, "inner", None)
                if isinstance(cause, ExplainAnalyzeNotSupportedError):
                    result = await explain_tool.explain(sql)
                    return result.to_text() + (
                        "\n\n(Note: EXPLAIN ANALYZE is not supported in this environment; "
                        "plain EXPLAIN result is shown above.)"
                    )
                raise
        else:
            result = await explain_tool.explain(sql)

        return result.to_text()
