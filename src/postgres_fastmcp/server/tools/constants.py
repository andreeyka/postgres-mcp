"""Constants for MCP tool handlers (descriptions, errors, log messages)."""

from postgres_fastmcp.services.health.database_health import HealthType


# PostgreSQL extension names
PG_STAT_STATEMENTS = "pg_stat_statements"
HYPOPG_EXTENSION = "hypopg"

# List of valid values for health_type
HEALTH_TYPE_VALUES = ", ".join(sorted([ht.value for ht in HealthType]))

# Error messages
ERROR_PREFIX = "Error: "
ERROR_DB_NOT_INITIALIZED = "Database connection is not initialized"
ERROR_DB_URL_NOT_SET = "Database connection URL is not set"
ERROR_DB_CONNECTION_FAILED = "Failed to establish database connection: {0}"
ERROR_UNSUPPORTED_OBJECT_TYPE = "Unsupported object type: {0}"
ERROR_NO_RESULTS = "No results"
ERROR_EMPTY_QUERIES = "Please provide a non-empty list of queries to analyze."
ERROR_INVALID_SORT_CRITERIA = "Invalid sort criteria. Please use 'resources' or 'mean_time' or 'total_time'."
ERROR_CANNOT_USE_ANALYZE_WITH_HYPOTHETICAL = "Cannot use analyze and hypothetical indexes together"
ERROR_PROCESSING_EXPLAIN_PLAN = "Error processing explain plan"

# Log messages
LOG_ATTEMPTING_CONNECTION = "Attempting to establish database connection"
LOG_SAFE_SQL_DRIVER = "Using SafeSqlDriver with restrictions (RESTRICTED mode, timeout={0}s)"
LOG_UNRESTRICTED_SQL_DRIVER = "Using unrestricted SqlDriver (UNRESTRICTED mode)"
LOG_ERROR_LISTING_SCHEMAS = "Error listing schemas: {0}"
LOG_ERROR_LISTING_OBJECTS = "Error listing objects: {0}"
LOG_ERROR_GETTING_OBJECT_DETAILS = "Error getting object details: {0}"
LOG_ERROR_EXPLAINING_QUERY = "Error explaining query: {0}"
LOG_ERROR_EXECUTING_QUERY = "Error executing query: {0}"
LOG_ERROR_ANALYZING_WORKLOAD = "Error analyzing workload: {0}"
LOG_ERROR_ANALYZING_QUERIES = "Error analyzing queries: {0}"
LOG_ERROR_GETTING_SLOW_QUERIES = "Error getting slow queries: {0}"
LOG_CREATED_TOOLS = "Created {0} tools"

# Query limit messages
QUERIES_LIMIT_MESSAGE = "Please provide a list of up to {0} queries to analyze."
