# PostgreSQL MCP Tests

This directory contains tests for the PostgreSQL MCP package.

## Running Tests

To run all tests:

```bash
uv run pytest
```

To run a specific test file:

```bash
uv run pytest tests/unit/test_obfuscate_password.py
```

To run a specific test:

```bash
uv run pytest tests/unit/test_db_conn_pool.py::test_pool_connect_success
```

## Test Structure

- **Unit Tests** (`tests/unit/`): Tests for individual components and functions
  - `test_obfuscate_password.py`: Tests for password obfuscation functionality
  - `test_db_conn_pool.py`: Tests for database connection pool
  - `test_sql_driver.py`: Tests for SQL driver and transaction handling
- **Integration Tests** (`tests/integration/`): End-to-end with real DB / MCP client
  - `test_tools_integration.py`: All MCP tools via `Client(mcp).call_tool` (see coverage below)
  - `test_table_prefix.py`: Table prefix and access_mode behavior
  - `test_top_queries_integration.py`: Top queries service and pg_stat_statements
  - `test_sql_hardening.py`: server-side statement_timeout and CREATE EXTENSION policy
  - `dta/test_dta_calc_integration.py`: DTA (hypopg) and index analysis service

### Integration coverage: tools and variants

| Tool | Variants covered |
|------|------------------|
| `list_schemas` | output=json, default table |
| `execute_sql` | SELECT with rows, SELECT returning 0 rows (json and table) |
| `list_objects` | object_type: table, view, sequence, extension, 'Tables' |
| `get_object_details` | information_schema.tables view (json) |
| `explain_query` | default (plain), analyze=True |
| `analyze_db_health` | health_type=all, health_type=['Connection'] |
| `analyze_workload_indexes` | max_index_size_mb |
| `analyze_query_indexes` | queries list |
| `get_top_queries` | sort_by: total_time, mean_time, resources, avg; limit=5; output=json |

## Testing Conventions

- **Behavior over implementation**: Unit tests describe business or functional behavior; test names and docstrings should read as specifications (e.g. "access_mode=basic returns only public schema", "invalid sort_by raises InvalidSortCriteriaError").
- **Assert on outcome and contract**: Prefer asserting on return value, response shape, and exceptions; avoid asserting on internal query constants or exact internal method call signatures.
- **One mock at the boundary**: Prefer a single mock at the external boundary (DB driver, pool); avoid "mock on mock" (e.g. patching both a facade and its internal tool). For facades, focus tests on return value and exceptions rather than delegation call details.
- **Integration tests**: Cover end-to-end tool invocation with a real DB where possible; use helpers such as `_rows`, which reads `structured_content` of an `output="json"` call, to assert against a clear response contract.
