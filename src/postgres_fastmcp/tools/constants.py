"""Константы для обработчиков MCP-инструментов."""

from postgres_fastmcp.services.health.database_health import HealthType


PG_STAT_STATEMENTS = "pg_stat_statements"

HEALTH_TYPE_VALUES = ", ".join(sorted(ht.value for ht in HealthType))

ERROR_DB_NOT_INITIALIZED = "Database connection is not initialized"
ERROR_DB_URL_NOT_SET = "Database connection URL is not set"

# Annotation presets for Tool.from_function(annotations={...})
# See https://gofastmcp.com/servers/tools — ToolAnnotations fields.
READ_ONLY_IDEMPOTENT: dict[str, bool] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
READ_ONLY_NON_IDEMPOTENT: dict[str, bool] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
DESTRUCTIVE: dict[str, bool] = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
}
