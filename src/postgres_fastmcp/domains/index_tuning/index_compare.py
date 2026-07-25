"""Структурное сравнение определений индексов через разбор pglast."""

import logging
from typing import Any

from pglast import parser
from pglast.ast import ColumnRef, FuncCall, Node

from .models import IndexRecommendation


logger = logging.getLogger(__name__)


def index_exists(index: IndexRecommendation, existing_defs: set[str]) -> bool:
    """Check if an index with the same table, columns, and type already exists.

    Uses pglast to parse index definitions and compare their structure rather than
    doing simple string matching.

    Args:
        index: Index recommendation to check.
        existing_defs: Set of existing index definitions.

    Returns:
        True if index exists, False otherwise.
    """
    try:
        # Parse the candidate index
        candidate_stmt = parser.parse_sql(index.definition)[0]
        candidate_node = candidate_stmt.stmt

        # Extract key information from candidate index
        candidate_info = _extract_index_info(candidate_node)

        # If we couldn't parse the candidate index, fall back to string comparison
        if not candidate_info:
            return index.definition in existing_defs

        # Check each existing index
        for existing_def in existing_defs:
            try:
                # Skip if it's obviously not an index
                if not ("CREATE INDEX" in existing_def.upper() or "CREATE UNIQUE INDEX" in existing_def.upper()):
                    continue

                # Parse the existing index
                existing_stmt = parser.parse_sql(existing_def)[0]
                existing_node = existing_stmt.stmt

                # Extract key information
                existing_info = _extract_index_info(existing_node)

                # Compare the key components
                if existing_info and _is_same_index(candidate_info, existing_info):
                    return True
            except Exception as e:
                error_msg = "Error parsing existing index"
                raise ValueError(error_msg) from e

    except Exception as e:
        error_msg = "Error in robust index comparison"
        raise ValueError(error_msg) from e
    else:
        return False


def _extract_index_info(node: Any) -> dict[str, Any] | None:  # noqa: ANN401
    """Extract key information from a parsed index node.

    Args:
        node: Parsed index AST node.

    Returns:
        Dictionary with index information or None if extraction fails.
    """
    try:
        # Handle differences in node structure between pglast versions
        index_stmt = node.IndexStmt if hasattr(node, "IndexStmt") else node

        # Extract table name
        if hasattr(index_stmt.relation, "relname"):
            table_name = index_stmt.relation.relname
        else:
            # Extract from RangeVar
            table_name = index_stmt.relation.RangeVar.relname

        # Extract columns
        columns = []
        for idx_elem in index_stmt.indexParams:
            if hasattr(idx_elem, "name") and idx_elem.name:
                columns.append(idx_elem.name)
            elif hasattr(idx_elem, "IndexElem") and idx_elem.IndexElem:
                columns.append(idx_elem.IndexElem.name)
            elif hasattr(idx_elem, "expr") and idx_elem.expr:
                # Convert the expression to a proper string representation
                expr_str = _ast_expr_to_string(idx_elem.expr)
                columns.append(expr_str)
        # Extract index type
        index_type = "btree"  # default
        if hasattr(index_stmt, "accessMethod") and index_stmt.accessMethod:
            index_type = index_stmt.accessMethod

        # Check if unique
        is_unique = False
        if hasattr(index_stmt, "unique"):
            is_unique = index_stmt.unique

        return {
            "table": table_name.lower(),
            "columns": [col.lower() for col in columns],
            "type": index_type.lower(),
            "unique": is_unique,
        }
    except Exception as e:
        logger.debug("Error extracting index info: %s", e)
        error_msg = "Error extracting index info"
        raise ValueError(error_msg) from e


def _ast_expr_to_string(expr: Node) -> str:  # noqa: PLR0911
    """Convert an AST expression (like FuncCall) to a proper string representation.

    For example, converts a FuncCall node representing lower(name) to "lower(name)"
    """
    try:
        # Check for FuncCall type directly
        if isinstance(expr, FuncCall):
            # Extract function name
            if hasattr(expr, "funcname") and expr.funcname:
                func_name = ".".join([name.sval for name in expr.funcname if hasattr(name, "sval")])
            else:
                func_name = "unknown_func"

            # Extract arguments
            args = []
            if hasattr(expr, "args") and expr.args:
                args.extend([_ast_expr_to_string(arg) for arg in expr.args])

            # Format as function call
            return f"{func_name}({','.join(args)})"

        # Check for ColumnRef type directly
        if isinstance(expr, ColumnRef):
            if hasattr(expr, "fields") and expr.fields:
                return ".".join([field.sval for field in expr.fields if hasattr(field, "sval")])
            return "unknown_column"

        # Try to handle direct values
        if hasattr(expr, "sval"):  # String value
            return str(expr.sval)
        if hasattr(expr, "ival"):  # Integer value
            return str(expr.ival)
        if hasattr(expr, "fval"):  # Float value
            return str(expr.fval)

        # Fallback for other expression types
        return str(expr)
    except Exception as e:
        error_msg = "Error converting expression to string"
        raise ValueError(error_msg) from e


def _is_same_index(index1: dict[str, Any], index2: dict[str, Any]) -> bool:
    """Check if two indexes are functionally equivalent.

    Args:
        index1: First index information dictionary.
        index2: Second index information dictionary.

    Returns:
        True if indexes are equivalent, False otherwise.
    """
    if not index1 or not index2:
        return False

    # Same table?
    if index1["table"] != index2["table"]:
        return False

    # Same index type?
    if index1["type"] != index2["type"]:
        return False

    # Same columns (order matters for most index types)?
    if index1["columns"] != index2["columns"]:
        # For hash indexes, order doesn't matter
        return bool(index1["type"] == "hash" and set(index1["columns"]) == set(index2["columns"]))

    # If one is unique and the other is not, they're different
    # Except when a primary key (which is unique) exists and we're considering a non-unique index on same column
    # Same core definition
    return not (index1["unique"] and not index2["unique"])
