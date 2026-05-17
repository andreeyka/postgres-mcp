"""Константы для обработчиков MCP-инструментов."""

from postgres_fastmcp.services.health.database_health import HealthType


PG_STAT_STATEMENTS = "pg_stat_statements"

HEALTH_TYPE_VALUES = ", ".join(sorted(ht.value for ht in HealthType))

ERROR_DB_NOT_INITIALIZED = "Database connection is not initialized"
ERROR_DB_URL_NOT_SET = "Database connection URL is not set"
