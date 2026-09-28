"""Константы SQL-запросов каталога БД (information_schema, pg_catalog) и список разрешённых шаблонов."""

QUERY_LIST_SCHEMAS = """
SELECT
    schema_name,
    schema_owner,
    CASE
        WHEN schema_name LIKE 'pg_%' THEN 'System Schema'
        WHEN schema_name = 'information_schema' THEN 'System Information Schema'
        ELSE 'User Schema'
    END as schema_type
FROM information_schema.schemata
ORDER BY schema_type, schema_name
"""

QUERY_LIST_TABLES_VIEWS = """
SELECT table_schema, table_name, table_type
FROM information_schema.tables
WHERE table_schema = {} AND table_type = {}
ORDER BY table_name
"""

# Существование таблицы/представления: тот же источник и тот же table_type, что у QUERY_LIST_TABLES_VIEWS
# ('BASE TABLE' = relkind r/p, 'VIEW' = v).
QUERY_TABLE_EXISTS = """
SELECT 1 AS present
FROM information_schema.tables
WHERE table_schema = {} AND table_name = {} AND table_type = {}
"""

QUERY_LIST_SEQUENCES = """
SELECT sequence_schema, sequence_name, data_type
FROM information_schema.sequences
WHERE sequence_schema = {}
ORDER BY sequence_name
"""

QUERY_LIST_EXTENSIONS = """
SELECT extname, extversion, extrelocatable
FROM pg_catalog.pg_extension
ORDER BY extname
"""

QUERY_GET_COLUMNS = """
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = {} AND table_name = {}
ORDER BY ordinal_position
"""

QUERY_GET_CONSTRAINTS = """
SELECT tc.constraint_name, tc.constraint_type, kcu.column_name
FROM information_schema.table_constraints AS tc
LEFT JOIN information_schema.key_column_usage AS kcu
  ON tc.constraint_name = kcu.constraint_name
 AND tc.table_schema = kcu.table_schema
WHERE tc.table_schema = {} AND tc.table_name = {}
"""

QUERY_GET_INDEXES = """
SELECT indexname, indexdef
FROM pg_catalog.pg_indexes
WHERE schemaname = {} AND tablename = {}
"""

QUERY_GET_SEQUENCE_DETAILS = """
SELECT sequence_schema, sequence_name, data_type, start_value, increment
FROM information_schema.sequences
WHERE sequence_schema = {} AND sequence_name = {}
"""

QUERY_GET_EXTENSION_DETAILS = """
SELECT extname, extversion, extrelocatable
FROM pg_catalog.pg_extension
WHERE extname = {}
"""

# Единственные шаблоны, которые выполняет CatalogSqlExecutor: сравнение по тексту до подстановки параметров.
CATALOG_QUERIES: frozenset[str] = frozenset(
    {
        QUERY_LIST_SCHEMAS,
        QUERY_LIST_TABLES_VIEWS,
        QUERY_TABLE_EXISTS,
        QUERY_LIST_SEQUENCES,
        QUERY_LIST_EXTENSIONS,
        QUERY_GET_COLUMNS,
        QUERY_GET_CONSTRAINTS,
        QUERY_GET_INDEXES,
        QUERY_GET_SEQUENCE_DETAILS,
        QUERY_GET_EXTENSION_DETAILS,
    }
)
