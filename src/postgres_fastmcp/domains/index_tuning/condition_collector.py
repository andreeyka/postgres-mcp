"""AST-визитор для сбора столбцов, используемых в условиях запросов."""

import logging
from typing import Any

from pglast.ast import ColumnRef, JoinExpr, Node, SelectStmt

from postgres_fastmcp.postgres.ast.visitors import ColumnCollector, TableAliasVisitor


logger = logging.getLogger(__name__)


class ConditionColumnCollector(ColumnCollector):
    """Specialized ColumnCollector for condition columns.

    A specialized version of ColumnCollector that only collects columns used in
    WHERE, JOIN, HAVING conditions, and properly resolves column aliases.
    """

    # Constants for column reference field counts
    UNQUALIFIED_COLUMN_FIELDS = 1  # Single column name (e.g., "name")
    QUALIFIED_COLUMN_FIELDS = 2  # Table.column format (e.g., "users.name")

    def __init__(self, column_cache: dict[str, set[str]] | None = None) -> None:
        """Initialize ConditionColumnCollector.

        Args:
            column_cache: Optional cache of table -> set of column names.
                If provided, used to verify column existence. If None, falls back to
                permissive behavior (returns True for all checks).
        """
        super().__init__(column_cache=column_cache)
        self.column_cache: dict[str, set[str]] | None = column_cache  # type: ignore[assignment]
        self.condition_columns: dict[str, set[str]] = {}  # Specifically for columns in conditions
        self.in_condition = False  # Flag to track if we're inside a condition

    def __call__(self, node: Any) -> dict[str, set[str]]:  # noqa: ANN401
        """Call the collector on a node.

        Args:
            node: AST node to process.

        Returns:
            Dictionary mapping table names to sets of column names.
        """
        super().__call__(node)
        return self.condition_columns

    def visit_SelectStmt(self, _ancestors: list[Node], node: Node) -> None:  # noqa: C901, N802
        """Visit a SelectStmt node focusing on condition-related clauses.

        Focuses on condition-related clauses while still collecting column aliases.

        Args:
            ancestors: List of ancestor nodes.
            node: SelectStmt node to visit.
        """
        if isinstance(node, SelectStmt):
            self.inside_select = True
            self.current_query_level += 1
            query_level = self.current_query_level

            # Get table aliases first
            alias_visitor = TableAliasVisitor()
            if hasattr(node, "fromClause") and node.fromClause:
                for from_item in node.fromClause:
                    alias_visitor(from_item)
            tables = alias_visitor.tables
            aliases = alias_visitor.aliases

            # Store the context for this query
            self.context_stack.append((tables, aliases))

            # First pass: collect column aliases from targetList
            if hasattr(node, "targetList") and node.targetList:
                self.target_list = node.targetList
                target_list = node.targetList
                if target_list is not None:
                    for target_entry in target_list:
                        if hasattr(target_entry, "name") and target_entry.name:
                            # This is a column alias
                            col_alias = target_entry.name
                            # Store the expression node for this alias
                            if hasattr(target_entry, "val"):
                                self.column_aliases[col_alias] = {
                                    "node": target_entry.val,
                                    "level": query_level,
                                }

            # Process WHERE clause
            if node.whereClause:
                in_condition_cache = self.in_condition
                self.in_condition = True
                self(node.whereClause)
                self.in_condition = in_condition_cache

            # Process JOIN conditions in fromClause
            if node.fromClause:
                for item in node.fromClause:
                    if isinstance(item, JoinExpr) and item.quals:
                        in_condition_cache = self.in_condition
                        self.in_condition = True
                        self(item.quals)
                        self.in_condition = in_condition_cache

            # Process HAVING clause - may reference aliases
            if node.havingClause:
                in_condition_cache = self.in_condition
                self.in_condition = True
                self._process_having_with_aliases(node.havingClause)
                self.in_condition = in_condition_cache

            # Process ORDER BY clause - also important for indexes
            if hasattr(node, "sortClause") and node.sortClause:
                in_condition_cache = self.in_condition
                self.in_condition = True
                for sort_item in node.sortClause:
                    self._process_node_with_aliases(sort_item.node)
                self.in_condition = in_condition_cache

            # Clean up the context stack
            self.context_stack.pop()
            self.inside_select = False
            self.current_query_level -= 1

    def _process_having_with_aliases(self, having_clause: Any) -> None:  # noqa: ANN401
        """Process HAVING clause with special handling for column aliases.

        Args:
            having_clause: HAVING clause node to process.
        """
        self._process_node_with_aliases(having_clause)

    def _process_node_with_aliases(self, node: Node | None) -> None:
        """Process a node, resolving any column aliases it contains.

        Args:
            node: AST node to process.
        """
        if node is None:
            return

        # If node is a column reference, it might be an alias
        if isinstance(node, ColumnRef) and hasattr(node, "fields") and node.fields:
            fields = [f.sval for f in node.fields if hasattr(f, "sval")] if node.fields else []
            if len(fields) == 1:
                col_name = fields[0]
                # Check if this is a known alias
                if col_name in self.column_aliases:
                    # Process the original expression instead
                    alias_info = self.column_aliases[col_name]
                    if alias_info["level"] == self.current_query_level:
                        self(alias_info["node"])
                        return

        # For non-alias nodes, process normally
        self(node)

    def visit_ColumnRef(self, _ancestors: list[Node], node: Node) -> None:  # noqa: C901, N802
        """Process column references in condition context.

        Process column references, but only if we're in a condition context.
        Skip known column aliases but process their underlying expressions.

        Args:
            ancestors: List of ancestor nodes.
            node: ColumnRef node to visit.
        """
        if not self.in_condition:
            return  # Skip if not in a condition context

        if not isinstance(node, ColumnRef) or not self.context_stack:
            return

        # Get the current query context
        tables, aliases = self.context_stack[-1]

        # Extract table and column names
        fields = [f.sval for f in node.fields if hasattr(f, "sval")] if node.fields else []

        # Check if this is a reference to a column alias
        if len(fields) == 1 and fields[0] in self.column_aliases:
            # Process the original expression node instead
            alias_info = self.column_aliases[fields[0]]
            if alias_info["level"] == self.current_query_level:
                self.in_condition = True  # Ensure we collect from the aliased expression
                self(alias_info["node"])
            return

        if len(fields) == self.QUALIFIED_COLUMN_FIELDS:  # Table.column format
            table_or_alias, column = fields
            # Resolve alias to actual table
            table = aliases.get(table_or_alias, table_or_alias)

            # Add to condition columns
            if table not in self.condition_columns:
                self.condition_columns[table] = set()
            self.condition_columns[table].add(column)

        elif len(fields) == self.UNQUALIFIED_COLUMN_FIELDS:  # Unqualified column
            column = fields[0]

            # For unqualified columns, check all tables in context
            found_match = False
            for table_name in tables:
                # Skip schema qualification if present
                actual_table = table_name
                if "." in table_name:
                    _, actual_table = table_name.split(".", 1)

                # Add column to all tables that have it
                if self._column_exists(actual_table, column):
                    if actual_table not in self.condition_columns:
                        self.condition_columns[actual_table] = set()
                    self.condition_columns[actual_table].add(column)
                    found_match = True

            if not found_match:
                logger.debug("Could not resolve unqualified column '%s' to any table", column)

    def _column_exists(self, table: str, column: str) -> bool:
        """Check if column exists in table.

        Args:
            table: Table name.
            column: Column name.

        Returns:
            True if column exists in the table according to column_cache.
            If column_cache is None, returns True (permissive mode).
            If column_cache is empty dict, returns False (strict mode).
        """
        if self.column_cache is None:
            # If cache is not provided (None), use permissive behavior
            # This allows the collector to work without a cache
            return True
        if not self.column_cache:
            # If cache is empty dict, return False for safety
            # This prevents adding non-existent columns when cache was attempted but failed
            return False

        # Check if table exists in cache
        table_columns = self.column_cache.get(table.lower())
        if table_columns is None:
            return False

        # Check if column exists in table
        return column.lower() in {col.lower() for col in table_columns}
