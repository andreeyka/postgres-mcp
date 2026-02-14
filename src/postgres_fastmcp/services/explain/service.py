"""Explain query execution plan service (facade for explain module)."""

from typing import Any, Literal

from postgres_fastmcp.common.errors import HypopgNotInstalledError
from postgres_fastmcp.services.db_access_service import DbAccessService
from postgres_fastmcp.services.explain.explain_plan import ExplainPlanTool
from postgres_fastmcp.sql.extensions.checker import ExtensionInspectorAdapter


ExplainMode = Literal["plain", "analyze", "hypothetical"]


class ExplainService:
    """Service for explaining SQL query execution plans."""

    def __init__(self, db: DbAccessService, mode: ExplainMode = "plain") -> None:
        """Initialize with database access service and explain mode."""
        self.db = db
        self._mode = mode

    async def explain_query(
        self,
        sql: str,
        *,
        hypothetical_indexes: list[dict[str, Any]] | None = None,
    ) -> str:
        """Explain the execution plan for a SQL query.

        Mode is fixed by provider (plain / analyze / hypothetical).
        For hypothetical mode, pass hypothetical_indexes; otherwise ignored.

        Raises:
            ExplainAnalyzeWithHypotheticalError: analyze and hypothetical_indexes used together.
            HypopgNotInstalledError: HypoPG not installed when hypothetical_indexes given.
            ExplainPlanError: From ExplainPlanTool on plan generation failure.
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
            result = await explain_tool.explain_analyze(sql)
        else:
            result = await explain_tool.explain(sql)

        return result.to_text()
