"""Извлечение таблиц и столбцов из строк SQL запросов или AST."""

import logging

from pglast import parse_sql
from pglast.ast import SelectStmt

from postgres_fastmcp.sql.ast.visitors import ColumnCollector, TableAliasVisitor


logger = logging.getLogger(__name__)


def extract_tables_from_query(query: str) -> set[str]:
    """Извлечение имен таблиц из SQL SELECT запроса.

    Args:
        query: Строка SQL запроса.

    Returns:
        Множество имен таблиц.
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
    """Извлечение таблицы -> столбцов из SQL SELECT запроса.

    Args:
        query: Строка SQL запроса.
        column_cache: Необязательный кэш для проверок существования столбцов.

    Returns:
        Словарь, сопоставляющий имя таблицы с множеством имен столбцов.
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
    """Извлечение таблицы -> столбцов из узла SelectStmt.

    Args:
        stmt: Разобранный SelectStmt.
        column_cache: Необязательный кэш для существования столбцов.

    Returns:
        Словарь, сопоставляющий имя таблицы с множеством имен столбцов.
    """
    try:
        collector = ColumnCollector(column_cache=column_cache)
        collector(stmt)
        return collector.columns
    except Exception:
        logger.warning("Error extracting columns from stmt: %s", stmt)
        return {}


def get_table_aliases(query: str, table_name: str) -> list[str]:
    """Возвращает список алиасов для заданной таблицы (включая само имя таблицы).

    Args:
        query: Строка SQL запроса.
        table_name: Имя таблицы для поиска алиасов.

    Returns:
        Список имен алиасов.
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
