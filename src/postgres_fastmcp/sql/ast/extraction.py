"""Extract tables and columns from SQL query strings or AST."""

import logging

from pglast import parse_sql
from pglast.ast import SelectStmt

from postgres_fastmcp.sql.ast.visitors import ColumnCollector, TableAliasVisitor


logger = logging.getLogger(__name__)


def extract_tables_from_query(query: str) -> set[str]:
    """Extract table names from a SELECT query.

    Args:
        query: SQL query string.

    Returns:
        Set of table names.
    """
    try:
        parsed = parse_sql(query)
        if not parsed:
            return set()
        stmt = parsed[0].stmt
        if not isinstance(stmt, SelectStmt):
            return set()
        visitor = TableAliasVisitor()
        visitor(stmt)
        return visitor.tables
    except Exception:
        logger.debug("Error extracting tables from query: %s", query[:50])
        return set()


def extract_columns(
    query: str,
    column_cache: dict[str, set[str]] | None = None,
) -> dict[str, set[str]]:
    """Extract table -> columns from a SELECT query.

    Args:
        query: SQL query string.
        column_cache: Optional cache for column existence checks.

    Returns:
        Dict mapping table name to set of column names.
    """
    try:
        parsed = parse_sql(query)
        if not parsed:
            return {}
        stmt = parsed[0].stmt
        if not isinstance(stmt, SelectStmt):
            return {}
        return extract_stmt_columns(stmt, column_cache=column_cache)
    except Exception:
        logger.warning("Error extracting columns from query: %s", query[:50])
        return {}


def extract_stmt_columns(
    stmt: SelectStmt,
    column_cache: dict[str, set[str]] | None = None,
) -> dict[str, set[str]]:
    """Extract table -> columns from a SelectStmt node.

    Args:
        stmt: Parsed SelectStmt.
        column_cache: Optional cache for column existence.

    Returns:
        Dict mapping table name to set of column names.
    """
    try:
        collector = ColumnCollector(column_cache=column_cache)
        collector(stmt)
        return collector.columns
    except Exception:
        logger.warning("Error extracting columns from stmt: %s", stmt)
        return {}


def get_table_aliases(query: str, table_name: str) -> list[str]:
    """Return list of aliases for the given table (including the table name itself).

    Args:
        query: SQL query string.
        table_name: Table name to find aliases for.

    Returns:
        List of alias names.
    """
    try:
        parsed = parse_sql(query)
        if not parsed:
            return [table_name]
        stmt = parsed[0].stmt
        if not isinstance(stmt, SelectStmt):
            return [table_name]
        visitor = TableAliasVisitor()
        visitor(stmt)
        aliases = [table_name]
        for alias, tbl in visitor.aliases.items():
            if tbl.lower() == table_name.lower():
                aliases.append(alias)
        return aliases
    except Exception:
        logger.exception("Error extracting table aliases")
        return [table_name]
