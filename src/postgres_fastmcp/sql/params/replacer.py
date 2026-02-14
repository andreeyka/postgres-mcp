"""Parameter replacer: replace $1, $2, ... with values from pg_stats or context."""

import logging
import re
from typing import TYPE_CHECKING, Any

from postgres_fastmcp.sql.ast.extraction import (
    extract_columns,
    extract_stmt_columns,
    extract_tables_from_query,
    get_table_aliases,
)
from postgres_fastmcp.sql.models.row_result import RowResult
from postgres_fastmcp.sql.params.strategies import (
    context_replace,
    get_bound_values,
    get_generic_replacement,
    get_replacement_value,
    parse_pg_array_value,
)


if TYPE_CHECKING:
    from postgres_fastmcp.services.ports.executor import QueryExecutorPort, QueryTemplatePort

logger = logging.getLogger(__name__)

REPLACE_PARAMETERS_ERROR = "Error replacing parameters"

PG_STATS_QUERY = """
SELECT
    data_type,
    most_common_vals as common_vals,
    most_common_freqs as common_freqs,
    histogram_bounds,
    null_frac,
    n_distinct,
    correlation
FROM pg_stats
JOIN information_schema.columns
    ON pg_stats.tablename = information_schema.columns.table_name
    AND pg_stats.attname = information_schema.columns.column_name
WHERE pg_stats.tablename = {}
AND pg_stats.attname = {}
"""

COLUMNS_CACHE_QUERY = """
SELECT table_name, column_name
FROM information_schema.columns
WHERE table_schema = {}
AND table_name = ANY({})
ORDER BY table_name, ordinal_position
"""


class SqlParamReplacer:
    """Replaces $N placeholders using column statistics and context. Implements ParamReplacerPort."""

    def __init__(
        self,
        executor: "QueryExecutorPort",
        template: "QueryTemplatePort",
    ) -> None:
        """Initialize with executor and template (for running param queries).

        Args:
            executor: Execute queries (execute(query, params=None, readonly=True)).
            template: Render parameterized query (render(query, params) -> str).
        """
        self._executor = executor
        self._template = template
        self._column_stats_cache: dict[str, dict[str, Any] | None] = {}

    async def _run_param_query(self, query: str, params: list[Any]) -> list[RowResult] | None:
        """Execute a parameterized query (render then execute)."""
        rendered = self._template.render(query, params)
        return await self._executor.execute(rendered, params=None, readonly=True)

    async def _get_column_statistics(self, table_name: str, column_name: str) -> dict[str, Any] | None:
        """Fetch column stats from pg_stats; cache result."""
        cache_key = f"{table_name}.{column_name}"
        if cache_key in self._column_stats_cache:
            return self._column_stats_cache[cache_key]
        try:
            result = await self._run_param_query(PG_STATS_QUERY, [table_name, column_name])
            if not result or not result[0]:
                self._column_stats_cache[cache_key] = None
                return None
            stats = dict(result[0].cells)
            for key in ["common_vals", "common_freqs", "histogram_bounds"]:
                if key in stats and stats[key] is not None and isinstance(stats[key], str):
                    array_str = stats[key].strip("{}")
                    stats[key] = [parse_pg_array_value(v) for v in array_str.split(",")] if array_str else []
            self._column_stats_cache[cache_key] = stats
        except Exception as e:
            logger.warning("Error getting column statistics for %s.%s: %s", table_name, column_name, e)
            self._column_stats_cache[cache_key] = None
            return None
        else:
            return self._column_stats_cache[cache_key]

    def _identify_parameter_column(self, context: str, table_columns: dict[str, set[str]]) -> tuple[str, str] | None:
        """Identify (table, column) for a parameter from surrounding context."""
        for table, columns in table_columns.items():
            for column in columns:
                patterns = [
                    rf"{re.escape(column)}\s*=\s*\$\d+",
                    rf"{re.escape(column)}\s+in\s+\([^)]*\$\d+[^)]*\)",
                    rf"{re.escape(column)}\s+like\s+\$\d+",
                    rf"{re.escape(column)}\s*>\s*\$\d+",
                    rf"{re.escape(column)}\s*<\s*\$\d+",
                    rf"{re.escape(column)}\s*>=\s*\$\d+",
                    rf"{re.escape(column)}\s*<=\s*\$\d+",
                    rf"{re.escape(column)}\s+between\s+\$\d+\s+and\s+\$\d+",
                ]
                for pattern in patterns:
                    if re.search(pattern, context, re.IGNORECASE):
                        return (table, column)
        return None

    def _replace_parameters_generic(self, query: str) -> str:
        """Fallback replacement when we cannot resolve columns."""
        try:
            modified = re.sub(r"like \$\d+", "like '%'", query)
            modified = re.sub(r"(\w+)\s*=\s*\$\d+", lambda m: context_replace(m, "="), modified)
            modified = re.sub(r"(\w+)\s*<\s*\$\d+", lambda m: context_replace(m, "<"), modified)
            modified = re.sub(r"(\w+)\s*>\s*\$\d+", lambda m: context_replace(m, ">"), modified)
            modified = re.sub(r"(\d+) and \$\d+", r"\1 and 100", modified)
            modified = re.sub(r"\$\d+ and (\d+)", r"1 and \1", modified)
            modified = re.sub(r">\s*\$\d+", "> 1", modified)
            modified = re.sub(r"<\s*\$\d+", "< 100", modified)
            modified = re.sub(r"=\s*\$\d+\b", "= 45", modified)
            modified = re.sub(r"\$\d+", "'sample_value'", modified)
        except Exception:
            logger.exception("Error in generic parameter replacement")
            return query
        else:
            return modified

    async def replace_parameters(self, query: str) -> str:  # noqa: C901
        """Replace $N placeholders with values from stats or context. Raises ValueError on error."""
        try:
            modified_query = query
            param_matches = list(re.finditer(r"\$\d+", query))
            if not param_matches:
                logger.debug("No parameters found for query: %s...", query[:50])
                return query

            tables = extract_tables_from_query(query)
            column_cache = await self.build_column_cache(tables) if tables else None

            modified_query = re.sub(r"limit\s+\$(\d+)", "limit 100", modified_query, flags=re.IGNORECASE)
            modified_query = re.sub(
                r"interval\s+'(\d+)\s+([a-z]+)'",
                lambda m: f"interval '2 {m.group(2)}'",
                modified_query,
                flags=re.IGNORECASE,
            )
            modified_query = re.sub(r"interval\s+\$(\d+)", "interval '2 days'", modified_query, flags=re.IGNORECASE)
            modified_query = re.sub(r"offset\s+\$(\d+)", "offset 0", modified_query, flags=re.IGNORECASE)

            param_matches = list(re.finditer(r"\$\d+", modified_query))
            if not param_matches:
                return modified_query

            between_pattern = re.compile(r"(\w+(?:\.\w+)?)\s+between\s+\$(\d+)\s+and\s+\$(\d+)", re.IGNORECASE)
            for match in between_pattern.finditer(query):
                column_ref, param1, param2 = match.groups()
                if "." in column_ref:
                    alias, col_name = column_ref.split(".", 1)
                    table_columns = extract_columns(query, column_cache=column_cache)
                    table_name = None
                    for tbl in table_columns:
                        if alias in get_table_aliases(query, tbl):
                            table_name = tbl
                            break
                else:
                    col_name = column_ref
                    table_columns = extract_columns(query, column_cache=column_cache)
                    table_name = None
                    for tbl, cols in table_columns.items():
                        if col_name in cols:
                            table_name = tbl
                            break

                lower_bound, upper_bound = 10, 100
                if table_name and col_name:
                    stats = await self._get_column_statistics(table_name, col_name)
                    if stats:
                        lower_bound = get_bound_values(stats, is_lower=True)
                        upper_bound = get_bound_values(stats, is_lower=False)
                modified_query = re.sub(r"\$" + param1, str(lower_bound), modified_query)
                modified_query = re.sub(r"\$" + param2, str(upper_bound), modified_query)

            param_matches = list(re.finditer(r"\$\d+", modified_query))
            if not param_matches:
                return modified_query

            table_columns = extract_columns(query, column_cache=column_cache)
            if not table_columns:
                return self._replace_parameters_generic(modified_query)

            for match in reversed(param_matches):
                pos = match.start()
                clause_start = max(
                    modified_query.rfind(" where ", 0, pos),
                    modified_query.rfind(" and ", 0, pos),
                    modified_query.rfind(" or ", 0, pos),
                    modified_query.rfind(",", 0, pos),
                    modified_query.rfind("(", 0, pos),
                    -1,
                )
                if clause_start == -1:
                    clause_start = max(0, pos - 100)
                preceding = modified_query[clause_start : pos + 2]
                column_info = self._identify_parameter_column(preceding, table_columns)
                if column_info:
                    tname, cname = column_info
                    stats = await self._get_column_statistics(tname, cname)
                    replacement = (
                        get_replacement_value(stats, preceding) if stats else get_generic_replacement(preceding)
                    )
                else:
                    replacement = get_generic_replacement(preceding)
                modified_query = modified_query[: match.start()] + replacement + modified_query[match.end() :]

        except Exception as e:
            raise ValueError(REPLACE_PARAMETERS_ERROR) from e
        else:
            return modified_query

    def extract_columns(self, query: str, column_cache: dict[str, set[str]] | None = None) -> dict[str, set[str]]:
        """Extract table -> columns from query (delegate to ast.extraction)."""
        return extract_columns(query, column_cache=column_cache)

    def extract_stmt_columns(
        self,
        stmt: Any,  # noqa: ANN401  SelectStmt from pglast
        column_cache: dict[str, set[str]] | None = None,
    ) -> dict[str, set[str]]:
        """Extract table -> columns from SelectStmt (delegate to ast)."""
        return extract_stmt_columns(stmt, column_cache=column_cache)

    async def build_column_cache(self, tables: set[str], schema: str = "public") -> dict[str, set[str]]:
        """Build table -> set of column names from information_schema."""
        if not tables:
            return {}
        column_cache: dict[str, set[str]] = {}
        try:
            result = await self._run_param_query(COLUMNS_CACHE_QUERY, [schema, list(tables)])
            if result:
                for row in result:
                    tname = row.cells["table_name"].lower()
                    cname = row.cells["column_name"].lower()
                    if tname not in column_cache:
                        column_cache[tname] = set()
                    column_cache[tname].add(cname)
        except Exception as e:
            logger.warning("Error building column cache: %s. Continuing without cache.", e)
        return column_cache
