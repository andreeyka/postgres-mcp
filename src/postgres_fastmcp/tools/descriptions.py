# ruff: noqa: E501, S608
"""Descriptions for MCP tool handlers (used in tool registration)."""

from postgres_fastmcp.services.index.index_opt_base import MAX_NUM_INDEX_TUNING_QUERIES
from postgres_fastmcp.tools.constants import PG_STAT_STATEMENTS


# ============================================================================
# Basic Database Exploration Tools
# ============================================================================

DESC_LIST_SCHEMAS = (
    "List all schemas in the PostgreSQL database. "
    "Output: JSON array with schema names, owners, and types (System Schema, User Schema, etc.). "
    "Use this tool first to discover available schemas before exploring tables and other objects. "
    "Example workflow: 1) list_schemas, 2) list_objects with schema_name, 3) get_object_details for specific objects."
)

# Descriptions for user role (only public schema)
DESC_LIST_OBJECTS_USER = (
    "List objects (tables, views, sequences, or extensions) in the 'public' schema. "
    "Input: schema_name (optional, will be automatically set to 'public') and object_type (optional: 'table', 'view', 'sequence', or 'extension', default: 'table'). "
    "Output: JSON array of object names with their types. "
    "\n\nIMPORTANT: You have access only to the 'public' schema. The schema_name parameter is automatically set to 'public' if not specified. "
    "After listing objects, use get_object_details to examine the structure of specific objects. "
    "Example Input: object_type='table' (schema_name will be 'public' automatically)"
)

DESC_GET_OBJECT_DETAILS_USER = (
    "Show detailed information about a database object (table, view, sequence, or extension) in the 'public' schema. "
    "Input: schema_name (optional, will be automatically set to 'public'), object_name (required), and object_type (optional: 'table', 'view', 'sequence', or 'extension', default: 'table'). "
    "Output: Detailed object information including columns, constraints, indexes (for tables), and other metadata. "
    "\n\nIMPORTANT: You have access only to the 'public' schema. The schema_name parameter is automatically set to 'public' if not specified. "
    "Use this tool after list_objects to understand the structure of tables before writing SQL queries. "
    "This tool shows column names, data types, constraints, and indexes - essential information for writing correct SQL. "
    "Example workflow: 1) list_objects, 2) get_object_details, 3) execute_sql with proper column names."
)

# Descriptions for full role (all schemas)
DESC_LIST_OBJECTS_FULL = (
    "List objects (tables, views, sequences, or extensions) in a specified schema. "
    "Input: schema_name (required) and object_type (optional: 'table', 'view', 'sequence', or 'extension', default: 'table'). "
    "Output: JSON array of object names with their types. "
    "\n\nIMPORTANT: Use this tool after list_schemas to discover what objects exist in a schema. "
    "Then use get_object_details to examine the structure of specific objects. "
    "Example Input: schema_name='public', object_type='table'"
)

DESC_GET_OBJECT_DETAILS_FULL = (
    "Show detailed information about a database object (table, view, sequence, or extension). "
    "Input: schema_name (required), object_name (required), and object_type (optional: 'table', 'view', 'sequence', or 'extension', default: 'table'). "
    "Output: Detailed object information including columns, constraints, indexes (for tables), and other metadata. "
    "\n\nIMPORTANT: Use this tool after list_objects to understand the structure of tables before writing SQL queries. "
    "This tool shows column names, data types, constraints, and indexes - essential information for writing correct SQL. "
    "Example workflow: 1) list_schemas, 2) list_objects, 3) get_object_details, 4) execute_sql with proper column names."
)

# ============================================================================
# SQL Query Tools
# ============================================================================

DESC_EXPLAIN_QUERY = (
    "Explains the execution plan for a SQL query, showing how PostgreSQL will execute it "
    "and provides detailed cost estimates, index usage, and join strategies. "
    "Input: sql (required) - a SQL SELECT query, analyze (optional, default: False) - if True, actually runs the query "
    "to show real execution statistics instead of estimates, hypothetical_indexes (optional) - list of hypothetical indexes to test. "
    "Output: Detailed execution plan with costs, actual times (if analyze=True), and recommendations. "
    "\n\nIMPORTANT: Use this tool to optimize queries before executing them. "
    "If analyze=True, the query will actually run - use with caution on large tables. "
    "You can test hypothetical indexes without creating them using the hypothetical_indexes parameter. "
    "Example workflow: 1) explain_query to check plan, 2) optimize query or add indexes, 3) execute_sql to run the final query."
)

DESC_EXECUTE_SQL_UNRESTRICTED = (
    "Execute any SQL query against the database (DDL, DML, DCL allowed). "
    "Input: sql - any valid PostgreSQL SQL statement. "
    "Output: Query results (for SELECT) or execution status (for DDL/DML). "
    "IMPORTANT: This tool has full database access. Use with caution. "
    "For read-only operations, prefer the restricted version if available. "
    "Always use explain_query first for SELECT queries to understand the execution plan. "
    "Example: Use for CREATE TABLE, INSERT, UPDATE, DELETE, ALTER, and other DDL/DML operations."
)

# Restricted execute_sql description (used for both user and full roles with restricted access_mode)
DESC_EXECUTE_SQL_RESTRICTED = (
    "Execute a read-only SQL query against the database. "
    "Input: sql - a SQL SELECT query (read-only operations only). "
    "Output: Query results as JSON array of rows. "
    "\n\nIMPORTANT: Only SELECT queries are allowed. DDL (CREATE, ALTER, DROP), DML (INSERT, UPDATE, DELETE), "
    "and DCL (GRANT, REVOKE) operations are blocked. "
    "This is the MAIN tool to use after you have explored the schema with list_objects and get_object_details. "
    "Always use existing columns and tables from the schema - do not invent fields. "
    "Example workflow: 1) list_objects, 2) get_object_details, 3) execute_sql (THIS TOOL) to get actual data."
)

# ============================================================================
# Index Analysis Tools
# ============================================================================

DESC_ANALYZE_WORKLOAD_INDEXES = (
    "Analyze frequently executed queries in the database and recommend optimal indexes. "
    "Uses data from the '" + PG_STAT_STATEMENTS + "' extension to identify slow queries and suggest indexes. "
    "Input: max_index_size_mb (optional, default: 10000) - maximum size for recommended indexes in MB, "
    "method (optional, default: 'dta') - 'dta' for Database Tuning Advisor algorithm or 'llm' for LLM-based optimization. "
    "Output: List of recommended indexes with estimated benefits, sizes, and SQL statements to create them. "
    "\n\nIMPORTANT: The '"
    + PG_STAT_STATEMENTS
    + "' extension must be enabled and the database must have query statistics. "
    "This tool analyzes actual query patterns from pg_stat_statements, not hypothetical queries. "
    "Use analyze_query_indexes if you want to analyze specific queries instead of the workload. "
    "Example: Use this tool periodically to identify missing indexes that would improve query performance."
)

DESC_ANALYZE_QUERY_INDEXES = (
    "Analyze a list of SQL queries (up to " + str(MAX_NUM_INDEX_TUNING_QUERIES) + ") and recommend optimal indexes. "
    "Input: queries (required) - list of SQL SELECT query strings to analyze, "
    "max_index_size_mb (optional, default: 10000) - maximum size for recommended indexes in MB, "
    "method (optional, default: 'dta') - 'dta' for Database Tuning Advisor algorithm or 'llm' for LLM-based optimization. "
    "Output: List of recommended indexes with estimated benefits, sizes, and SQL statements to create them. "
    "\n\nIMPORTANT: Provide actual SQL queries that you want to optimize. "
    "The 'dta' method uses cost-based analysis with hypothetical indexes (requires hypopg extension). "
    "The 'llm' method uses LLM to analyze query patterns and suggest indexes. "
    "Use analyze_workload_indexes if you want to analyze the entire database workload instead of specific queries. "
    "Example Input: queries=['SELECT * FROM users WHERE email = $1', 'SELECT * FROM orders WHERE user_id = $1 AND status = $2']"
)

# ============================================================================
# Database Health and Monitoring Tools
# ============================================================================

DESC_ANALYZE_DB_HEALTH = (
    "Analyzes database health across multiple dimensions. "
    "Input: health_type (optional, default: 'all') - single check or comma-separated list: "
    "'index' (invalid, duplicate, bloated, unused indexes), "
    "'connection' (connection count and utilization), "
    "'vacuum' (vacuum health for transaction ID wraparound), "
    "'sequence' (sequences at risk of exceeding maximum value), "
    "'replication' (replication lag and slots), "
    "'buffer' (buffer cache hit rates), "
    "'constraint' (invalid constraints), "
    "'all' (runs all checks). "
    "Output: Detailed health report with issues found and recommendations. "
    "IMPORTANT: Use this tool regularly to monitor database health and identify potential problems. "
    "Each health check provides actionable recommendations. "
    "Example: Use 'all' for comprehensive health check, or specify individual checks like 'index,connection' for targeted analysis."
)

DESC_GET_TOP_QUERIES = (
    "Reports the slowest or most resource-intensive queries using data from the '"
    + PG_STAT_STATEMENTS
    + "' extension. "
    "Input: sort_by (optional, default: 'resources') - ranking criteria: "
    "'total_time' (total execution time across all calls), "
    "'mean_time' (average execution time per call), "
    "'resources' (resource-intensive queries based on I/O, WAL, and execution time). "
    "limit (optional, default: 10) - number of queries to return. "
    "Output: List of top queries with execution statistics, resource usage, and query text. "
    "\n\nIMPORTANT: The '" + PG_STAT_STATEMENTS + "' extension must be enabled. "
    "Use this tool to identify slow queries that need optimization. "
    "After identifying slow queries, use explain_query to understand why they're slow, "
    "then use analyze_query_indexes or analyze_workload_indexes to get index recommendations. "
    "Example workflow: 1) get_top_queries to find slow queries, 2) explain_query to analyze plan, 3) analyze_query_indexes to get recommendations."
)

# ============================================================================
# Parameter Descriptions
# ============================================================================

DESC_HYPOTHETICAL_INDEXES = (
    "A list of hypothetical indexes to simulate when explaining a query. "
    "Each index must be a dictionary with these keys:\n"
    "    - 'table': The table name to add the index to (e.g., 'users')\n"
    "    - 'columns': List of column names to include in the index "
    "(e.g., ['email'] or ['last_name', 'first_name'])\n"
    "    - 'using': Optional index method (default: 'btree', other options include 'hash', 'gist', 'gin', etc.)\n\n"
    "Examples: [\n"
    '    {"table": "users", "columns": ["email"], "using": "btree"},\n'
    '    {"table": "orders", "columns": ["user_id", "created_at"]}\n'
    "]"
    "\n\nIMPORTANT: Hypothetical indexes are created using the hypopg extension and are automatically cleaned up. "
    "They allow you to test index impact without actually creating indexes. "
    "If there are no hypothetical indexes to test, pass an empty list []."
)
