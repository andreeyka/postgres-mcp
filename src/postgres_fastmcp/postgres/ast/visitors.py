"""Посетители AST для извлечения таблиц, алиасов и столбцов из SQL."""

from typing import Any

from pglast.ast import A_Expr, ColumnRef, JoinExpr, Node, RangeVar, SelectStmt, SortBy, SortGroupClause
from pglast.visitors import Visitor


TARGET_LIST_NONE_ERROR = "targetList cannot be None after validation"
QUALIFIED_COLUMN_FIELDS = 2


class TableAliasVisitor(Visitor):
    """Извлекает имена таблиц и алиасы из AST SQL."""

    def __init__(self) -> None:
        super().__init__()
        self.aliases: dict[str, str] = {}
        self.tables: set[str] = set()

    def __call__(self, node: Node | tuple[Any, ...]) -> tuple[dict[str, str], set[str]]:
        """Обход узла AST и возврат собранных алиасов и имён таблиц.

        Args:
            node: Корневой узел AST для обхода.

        Returns:
            Кортеж (алиасы: имя_алиаса -> имя_таблицы, множество имён таблиц).
        """
        super().__call__(node)
        return self.aliases, self.tables

    def visit_RangeVar(self, _ancestors: list[Node], node: Node) -> None:  # noqa: N802
        """Обработка узла RangeVar: сбор имени таблицы и алиаса."""
        if isinstance(node, RangeVar):
            if node.relname is not None:
                self.tables.add(node.relname)
            if node.alias and node.alias.aliasname is not None:
                self.aliases[node.alias.aliasname] = str(node.relname)

    def visit_JoinExpr(self, _ancestors: list[Node], node: Node) -> None:  # noqa: N802
        """Обработка узла JOIN: рекурсивный обход левой и правой части."""
        if isinstance(node, JoinExpr):
            if node.larg is not None:
                self(node.larg)
            if node.rarg is not None:
                self(node.rarg)


class ColumnCollector(Visitor):
    """Собирает столбцы из SELECT, WHERE, JOIN, ORDER BY, GROUP BY, HAVING."""

    def __init__(self, column_cache: dict[str, set[str]] | None = None) -> None:
        super().__init__()
        self.context_stack: list[tuple[set[str], dict[str, str]]] = []
        self.columns: dict[str, set[str]] = {}
        self.target_list: tuple[Any, ...] | None = None
        self.inside_select = False
        self.column_aliases: dict[str, dict[str, Any]] = {}
        self.current_query_level = 0
        self.column_cache: dict[str, set[str]] = column_cache or {}

    def __call__(self, node: Node | tuple[Any, ...]) -> dict[str, set[str]]:
        """Обход узла AST и возврат собранных столбцов по контексту.

        Args:
            node: Корневой узел AST для обхода.

        Returns:
            Словарь: ключ контекста -> множество имён столбцов.
        """
        super().__call__(node)
        return self.columns

    def _column_exists(self, table: str, column: str) -> bool:
        """Проверка существования столбца в кэше."""
        if not self.column_cache:
            return True
        table_columns = self.column_cache.get(table.lower())
        if table_columns is None:
            return False
        return column.lower() in {c.lower() for c in table_columns}

    def visit_SelectStmt(self, _ancestors: list[Node], node: Node) -> None:  # noqa: N802
        """Обработка SELECT: контекст таблиц/алиасов, targetList, обход предложений запроса."""
        if not isinstance(node, SelectStmt):
            return
        self.inside_select = True
        self.current_query_level += 1
        query_level = self.current_query_level

        alias_visitor = TableAliasVisitor()
        if hasattr(node, "fromClause") and node.fromClause:
            for from_item in node.fromClause:
                alias_visitor(from_item)
        scope_tables = alias_visitor.tables
        scope_aliases = alias_visitor.aliases
        self.context_stack.append((scope_tables, scope_aliases))

        if hasattr(node, "targetList") and node.targetList:
            self.target_list = node.targetList
            if self.target_list is None:
                raise ValueError(TARGET_LIST_NONE_ERROR)
            for target_entry in self.target_list:
                if hasattr(target_entry, "name") and target_entry.name:
                    col_alias = target_entry.name
                    if hasattr(target_entry, "val"):
                        self.column_aliases[col_alias] = {"node": target_entry.val, "level": query_level}

        self._process_query_clauses(node)
        self.context_stack.pop()
        self.inside_select = False
        self.current_query_level -= 1

    def _process_query_clauses(self, node: SelectStmt) -> None:  # noqa: C901
        """Обработка предложений запроса: targetList, GROUP BY, WHERE, FROM, HAVING, ORDER BY."""
        if hasattr(node, "targetList") and node.targetList:
            self.target_list = node.targetList
            if self.target_list is None:
                raise ValueError(TARGET_LIST_NONE_ERROR)
            for target_entry in self.target_list:
                if hasattr(target_entry, "val"):
                    self(target_entry.val)

        if hasattr(node, "groupClause") and node.groupClause:
            for group_item in node.groupClause:
                if isinstance(group_item, SortGroupClause) and isinstance(group_item.tleSortGroupRef, int):
                    ref_index = group_item.tleSortGroupRef
                    if self.target_list is not None and ref_index <= len(self.target_list):
                        target_entry = self.target_list[ref_index - 1]
                        if hasattr(target_entry, "val"):
                            self(target_entry.val)
                        if hasattr(target_entry, "expr"):
                            self(target_entry.expr)

        if hasattr(node, "whereClause") and node.whereClause:
            self(node.whereClause)
        if hasattr(node, "fromClause") and node.fromClause:
            for from_item in node.fromClause:
                self(from_item)
        if hasattr(node, "havingClause") and node.havingClause:
            self(node.havingClause)
        if hasattr(node, "sortClause") and node.sortClause:
            for sort_item in node.sortClause:
                self._process_sort_item(sort_item)

    def _process_sort_item(self, sort_item: SortBy) -> None:
        """Обработка элемента ORDER BY: раскрытие алиаса или обход узла сортировки."""
        if not hasattr(sort_item, "node") or sort_item.node is None:
            return
        if isinstance(sort_item.node, ColumnRef) and hasattr(sort_item.node, "fields") and sort_item.node.fields:
            fields = [f.sval for f in sort_item.node.fields if hasattr(f, "sval")]
            if len(fields) == 1 and fields[0] in self.column_aliases:
                alias_info = self.column_aliases[fields[0]]
                if alias_info["level"] == self.current_query_level:
                    self(alias_info["node"])
                    return
        self(sort_item.node)

    def visit_ColumnRef(self, _ancestors: list[Node], node: Node) -> None:  # noqa: C901, N802
        """Обработка ссылки на столбец: учёт квалификации (таблица.столбец) и контекста."""
        if not isinstance(node, ColumnRef) or not self.inside_select:
            return
        if not hasattr(node, "fields") or not node.fields:
            return
        fields = [f.sval if hasattr(f, "sval") else "*" for f in node.fields]
        if len(fields) == 1 and (fields[0] == "*" or fields[0] in self.column_aliases):
            return
        if len(fields) == QUALIFIED_COLUMN_FIELDS and fields[1] == "*":
            return

        current_tables, current_aliases = self.context_stack[-1] if self.context_stack else (set(), {})
        if len(fields) == QUALIFIED_COLUMN_FIELDS:
            table_or_alias, column = fields
            table = current_aliases.get(table_or_alias, table_or_alias)
            if table not in self.columns:
                self.columns[table] = set()
            self.columns[table].add(column)
        elif len(fields) == 1:
            column = fields[0]
            if len(current_tables) == 1:
                table = next(iter(current_tables))
                if table not in self.columns:
                    self.columns[table] = set()
                self.columns[table].add(column)
            else:
                for table in current_tables:
                    if self._column_exists(table, column):
                        if table not in self.columns:
                            self.columns[table] = set()
                        self.columns[table].add(column)
                        break

    def visit_A_Expr(self, _ancestors: list[Node], node: Node) -> None:  # noqa: N802
        """Обработка выражения (оператор/функция): обход lexpr/rexpr, в т.ч. подзапросов."""
        if isinstance(node, A_Expr) and self.inside_select:
            if hasattr(node, "lexpr") and node.lexpr:
                self(node.lexpr)
                if isinstance(node.lexpr, SelectStmt):
                    av = TableAliasVisitor()
                    av(node.lexpr)
                    self.context_stack.append((av.tables, av.aliases))
                    self(node.lexpr)
                    self.context_stack.pop()
            if hasattr(node, "rexpr") and node.rexpr:
                if isinstance(node.rexpr, SelectStmt):
                    av = TableAliasVisitor()
                    av(node.rexpr)
                    self.context_stack.append((av.tables, av.aliases))
                    self(node.rexpr)
                    self.context_stack.pop()
                else:
                    self(node.rexpr)
            if (
                hasattr(node, "kind")
                and node.kind == 0
                and hasattr(node, "rexpr")
                and node.rexpr
                and isinstance(node.rexpr, SelectStmt)
            ):
                av = TableAliasVisitor()
                av(node.rexpr)
                self.context_stack.append((av.tables, av.aliases))
                self(node.rexpr)
                self.context_stack.pop()

    def visit_JoinExpr(self, _ancestors: list[Node], node: Node) -> None:  # noqa: N802
        """Обработка JOIN: обход larg, rarg и условия quals для сбора столбцов."""
        if isinstance(node, JoinExpr) and self.inside_select:
            if hasattr(node, "larg") and node.larg:
                self(node.larg)
            if hasattr(node, "rarg") and node.rarg:
                self(node.rarg)
            if hasattr(node, "quals") and node.quals:
                self(node.quals)

    def visit_SortBy(self, _ancestors: list[Node], node: Node) -> None:  # noqa: N802
        """Обработка элемента ORDER BY: обход узла выражения сортировки."""
        if isinstance(node, SortBy) and self.inside_select and hasattr(node, "node") and node.node:
            self(node.node)
