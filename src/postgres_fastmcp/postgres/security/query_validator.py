"""Валидатор SQL запросов: разбор и валидация AST для безопасного выполнения."""

import logging
import re

import pglast
from pglast.ast import (
    A_Const,
    A_Expr,
    CreateExtensionStmt,
    DefElem,
    ExplainStmt,
    FuncCall,
    Node,
    RangeVar,
    RawStmt,
    SelectStmt,
    TypeName,
    VariableShowStmt,
)
from pglast.enums import A_Expr_Kind
from pglast.visitors import Ancestor, Visitor

from postgres_fastmcp.postgres.security.policies import (
    ALLOWED_EXTENSIONS,
    ALLOWED_FUNCTIONS,
    ALLOWED_NODE_TYPES,
    BASIC_ALLOWED_FUNCTIONS,
    BASIC_SHOW_PARAMETERS,
    REG_TYPES,
)
from postgres_fastmcp.postgres.security.schema_guard import validate_schema_access
from postgres_fastmcp.postgres.security.statement_policies import ALLOWED_STMT_TYPES, WRITE_NODE_TYPES, WRITE_STMT_TYPES
from postgres_fastmcp.shared.errors import (
    CreateExtensionNotSupportedError,
    DdlNotAllowedError,
    DisallowedNodeTypeError,
    ExplainAnalyzeNotSupportedError,
    FunctionNotAllowedError,
    LikePatternNotConstantError,
    LockingClauseProhibitedError,
    ShowParameterNotAllowedError,
    SqlParseError,
    StatementTypeNotAllowedError,
    TypeCastNotAllowedError,
)


logger = logging.getLogger(__name__)

PG_CATALOG_PATTERN = re.compile(r"^pg_catalog\.(.+)$")


def _type_name_of(type_name: TypeName) -> str:
    """Имя типа без схемы, в нижнем регистре ('pg_catalog.regclass[]' -> 'regclass')."""
    names = type_name.names or ()
    last = names[-1] if names else None
    return str(getattr(last, "sval", "") or "").lower()


class _NodeValidationVisitor(Visitor):
    """Проверяет каждый узел AST по политикам безопасности.

    Обход дерева выполняет pglast.visitors.Visitor: все узлы попадают в
    generic-метод visit, включая узлы во вложенных последовательностях
    (VALUES-списки, табличные функции), которые самописный обход по
    __slots__ пропускал.
    """

    def __init__(
        self,
        *,
        allowed_node_types: tuple[type, ...],
        allowed_schema: str | None,
        table_prefix: str | None,
        allow_explain_analyze: bool,
        allowed_functions: frozenset[str],
    ) -> None:
        """Инициализация визитора с политиками валидации.

        Args:
            allowed_node_types: Допустимые типы узлов AST.
            allowed_schema: Если задана, разрешена только эта схема.
            table_prefix: Если задан вместе со схемой, имена таблиц должны начинаться с него.
            allow_explain_analyze: Разрешён ли EXPLAIN (ANALYZE).
            allowed_functions: Разрешённые имена функций (для basic — без интроспекции).
        """
        super().__init__()
        self._allowed_node_types = allowed_node_types
        self._allowed_schema = allowed_schema
        self._table_prefix = table_prefix
        self._allow_explain_analyze = allow_explain_analyze
        self._allowed_functions = allowed_functions
        self._basic = allowed_schema is not None

    def visit(self, _ancestors: Ancestor, node: Node) -> None:
        """Валидация одного узла AST; при нарушении политики вызывает исключение.

        Raises:
            DisallowedNodeTypeError: Тип узла AST не разрешён.
            SystemRelationAccessError: Доступ к системному отношению (pg_*, _pg_*) в basic.
            TablePrefixAccessError: Доступ к таблице не разрешён (префикс).
            SchemaNotAllowedError: Доступ к схеме не разрешён.
            SchemataTableAccessError: Доступ к information_schema.schemata в user mode.
            LikePatternNotConstantError: LIKE-паттерн не константа.
            FunctionNotAllowedError: Функция не разрешена.
            LockingClauseProhibitedError: Блокирующее предложение в SELECT.
            ExplainAnalyzeNotSupportedError: EXPLAIN ANALYZE не поддерживается.
            CreateExtensionNotSupportedError: Расширение не разрешено.
            ShowParameterNotAllowedError: Параметр SHOW вне разрешённого списка basic.
            TypeCastNotAllowedError: reg*-тип в любой позиции TypeName (каст, колонка
                табличной функции, аргумент PREPARE) в basic.
        """
        if not isinstance(node, self._allowed_node_types):
            raise DisallowedNodeTypeError(type(node))

        if isinstance(node, RangeVar):
            validate_schema_access(
                node,
                allowed_schema=self._allowed_schema,
                table_prefix=self._table_prefix,
            )

        if (
            isinstance(node, A_Expr)
            and node.kind
            in (
                A_Expr_Kind.AEXPR_LIKE,
                A_Expr_Kind.AEXPR_ILIKE,
            )
            and not (
                isinstance(node.rexpr, A_Const)
                and node.rexpr.val is not None
                and hasattr(node.rexpr.val, "sval")
                and node.rexpr.val.sval is not None
            )
        ):
            raise LikePatternNotConstantError

        if isinstance(node, FuncCall):
            func_name = ".".join([str(n.sval) for n in node.funcname]).lower() if node.funcname else ""
            match = PG_CATALOG_PATTERN.match(func_name)
            unqualified = match.group(1) if match else func_name
            if unqualified not in self._allowed_functions:
                raise FunctionNotAllowedError(func_name)

        if self._basic and isinstance(node, VariableShowStmt):
            name = node.name or ""
            if name.lower() not in BASIC_SHOW_PARAMETERS:
                raise ShowParameterNotAllowedError(name, sorted(BASIC_SHOW_PARAMETERS))

        if self._basic and isinstance(node, TypeName):
            type_name = _type_name_of(node)
            if type_name in REG_TYPES:
                raise TypeCastNotAllowedError(type_name)

        if isinstance(node, SelectStmt) and getattr(node, "lockingClause", None):
            raise LockingClauseProhibitedError

        if isinstance(node, ExplainStmt) and not self._allow_explain_analyze:
            for option in node.options or []:
                if isinstance(option, DefElem) and option.defname == "analyze":
                    raise ExplainAnalyzeNotSupportedError

        if isinstance(node, CreateExtensionStmt):
            self._validate_create_extension(node)

    def _validate_create_extension(self, node: CreateExtensionStmt) -> None:
        """Разрешить только расширения из allowlist, без CASCADE и без SCHEMA при ограничении схемы.

        CASCADE может доустановить зависимости вне allowlist; SCHEMA при заданной
        allowed_schema размещает объекты расширения за её пределами.

        Raises:
            CreateExtensionNotSupportedError: Расширение или опция не разрешены.
        """
        extname = node.extname or ""
        if extname not in ALLOWED_EXTENSIONS:
            raise CreateExtensionNotSupportedError(extname)
        for option in node.options or []:
            if not isinstance(option, DefElem):
                continue
            if option.defname == "cascade":
                raise CreateExtensionNotSupportedError(extname, "CASCADE")
            if option.defname == "schema" and self._allowed_schema is not None:
                raise CreateExtensionNotSupportedError(extname, "SCHEMA")


class QueryValidator:
    """Валидация строк SQL запросов для безопасного выполнения (только чтение или контролируемый DML)."""

    def __init__(
        self,
        *,
        allowed_schema: str | None = None,
        table_prefix: str | None = None,
        read_only: bool = True,
        allow_explain_analyze: bool = False,
    ) -> None:
        """Initialize validator with schema/prefix and read-only policy.

        Args:
            allowed_schema: If set, only this schema is allowed.
            table_prefix: If set with allowed_schema, table names must start with this.
            read_only: If True, only read statements allowed; if False, DML allowed too.
            allow_explain_analyze: If True, EXPLAIN (ANALYZE) is allowed (e.g. when access_mode=full).
        """
        self.allowed_schema = allowed_schema
        self.table_prefix = table_prefix
        self.read_only = read_only
        self.allow_explain_analyze = allow_explain_analyze

    def validate(self, query: str) -> None:
        """Валидация запроса; при небезопасном запросе вызывает исключение.

        Args:
            query: Строка SQL запроса.

        Raises:
            SqlParseError: Не удалось разобрать SQL.
            StatementTypeNotAllowedError: Тип оператора не разрешён.
            DdlNotAllowedError: DDL-операция не разрешена.
            DisallowedNodeTypeError: Тип узла AST не разрешён.
            SystemRelationAccessError: Доступ к системному отношению (pg_*, _pg_*) в basic.
            TablePrefixAccessError: Доступ к таблице не разрешён (префикс).
            SchemaNotAllowedError: Доступ к схеме не разрешён.
            SchemataTableAccessError: Доступ к information_schema.schemata в user mode.
            LikePatternNotConstantError: LIKE-паттерн не константа.
            FunctionNotAllowedError: Функция не разрешена.
            LockingClauseProhibitedError: Блокирующее предложение в SELECT.
            ExplainAnalyzeNotSupportedError: EXPLAIN ANALYZE не поддерживается.
            CreateExtensionNotSupportedError: Расширение не разрешено.
            ShowParameterNotAllowedError: Параметр SHOW вне разрешённого списка basic.
            TypeCastNotAllowedError: reg*-тип в любой позиции TypeName (каст, колонка
                табличной функции, аргумент PREPARE) в basic.
        """
        try:
            parsed = pglast.parse_sql(query)
        except pglast.parser.ParseError as e:
            raise SqlParseError from e

        allowed_stmt_types = set(ALLOWED_STMT_TYPES)
        allowed_node_types = set(ALLOWED_NODE_TYPES)
        if not self.read_only:
            allowed_stmt_types |= WRITE_STMT_TYPES
            allowed_node_types |= WRITE_NODE_TYPES

        node_validator = _NodeValidationVisitor(
            allowed_node_types=tuple(allowed_node_types),
            allowed_schema=self.allowed_schema,
            table_prefix=self.table_prefix,
            allow_explain_analyze=self.allow_explain_analyze,
            allowed_functions=BASIC_ALLOWED_FUNCTIONS if self.allowed_schema is not None else ALLOWED_FUNCTIONS,
        )

        for stmt in parsed:
            stmt_node = stmt.stmt if isinstance(stmt, RawStmt) else stmt
            if not isinstance(stmt_node, tuple(allowed_stmt_types)):
                raise StatementTypeNotAllowedError(read_only=self.read_only, stmt_type_name=type(stmt_node).__name__)

            stmt_type_name = type(stmt_node).__name__
            if ("Create" in stmt_type_name or "Drop" in stmt_type_name or "Alter" in stmt_type_name) and not isinstance(
                stmt_node, CreateExtensionStmt
            ):
                raise DdlNotAllowedError(stmt_type_name)

            node_validator(stmt)
