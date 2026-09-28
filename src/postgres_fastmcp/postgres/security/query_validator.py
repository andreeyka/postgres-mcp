"""Валидатор SQL запросов: разбор и валидация AST для безопасного выполнения."""

import logging
import re

import pglast
from pglast.ast import (
    A_Const,
    A_Expr,
    CollateClause,
    CreateExtensionStmt,
    DefElem,
    ExplainStmt,
    FuncCall,
    IndexElem,
    IndexStmt,
    Node,
    RangeTableSample,
    RangeVar,
    RawStmt,
    SelectStmt,
    SortBy,
    String,
    SubLink,
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
    BASIC_EXPLAIN_OPTIONS,
    BASIC_PG_SCALAR_TYPES,
    BASIC_SHOW_PARAMETERS,
    NAME_LOOKUP_TYPES,
)
from postgres_fastmcp.postgres.security.schema_guard import is_system_relation_name, validate_schema_access
from postgres_fastmcp.postgres.security.statement_policies import ALLOWED_STMT_TYPES, WRITE_NODE_TYPES, WRITE_STMT_TYPES
from postgres_fastmcp.shared.errors import (
    CreateExtensionNotSupportedError,
    DdlNotAllowedError,
    DisallowedNodeTypeError,
    ExplainAnalyzeNotSupportedError,
    ExplainOptionNotAllowedError,
    FunctionNotAllowedError,
    LikePatternNotConstantError,
    LockingClauseProhibitedError,
    SchemaNotAllowedError,
    ShowParameterNotAllowedError,
    SqlParseError,
    StatementTypeNotAllowedError,
    SystemRelationAccessError,
    TypeNotAllowedError,
)


logger = logging.getLogger(__name__)

PG_CATALOG_PATTERN = re.compile(r"^pg_catalog\.(.+)$")


def _name_parts(names: tuple[Node, ...] | None) -> list[str]:
    """Части составного имени (тип, collation) как строки: ('pg_catalog', 'int4')."""
    return [str(getattr(part, "sval", "") or "") for part in names or ()]


def _is_plain_index_elem(elem: object) -> bool:
    """Элемент индекса — простой столбец: только имя, без выражения, collation и opclass."""
    return (
        isinstance(elem, IndexElem)
        and elem.name is not None
        and elem.expr is None
        and not elem.collation
        and not elem.opclass
        and not elem.opclassopts
    )


def _is_plain_index(index: IndexStmt) -> bool:
    """Индекс состоит только из простых столбцов, без выражений, WHERE, opclass и табличного пространства.

    hypopg сам прогоняет CREATE INDEX через transformIndexStmt: входные функции литеральных
    приведений в выражениях и WHERE вычисляются, а имена типов, функций, классов операторов
    и табличных пространств резолвятся против каталога — это раскрывает существование чужих
    объектов даже когда сама таблица индекса разрешена. USING <метод> и сортировка
    (ASC/DESC, NULLS FIRST/LAST) на резолв объектов не влияют и остаются разрешены.
    """
    if index.whereClause is not None or index.tableSpace is not None or index.options or index.excludeOpNames:
        return False
    elems = (*(index.indexParams or ()), *(index.indexIncludingParams or ()))
    return all(_is_plain_index_elem(elem) for elem in elems)


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
            SystemRelationAccessError: Доступ к системному отношению (pg_*, _pg_*) в basic,
                или закрытое представление information_schema.
            TablePrefixAccessError: Доступ к таблице не разрешён (префикс).
            SchemaNotAllowedError: Доступ к схеме не разрешён.
            SchemataTableAccessError: Доступ к information_schema.schemata в user mode.
            LikePatternNotConstantError: LIKE-паттерн не константа.
            FunctionNotAllowedError: Функция не разрешена.
            LockingClauseProhibitedError: Блокирующее предложение в SELECT.
            ExplainAnalyzeNotSupportedError: EXPLAIN ANALYZE не поддерживается.
            ExplainOptionNotAllowedError: Опция EXPLAIN вне списка basic (SETTINGS, WAL, SERIALIZE, незнакомые).
            CreateExtensionNotSupportedError: Расширение не разрешено.
            ShowParameterNotAllowedError: Параметр SHOW вне разрешённого списка basic.
            TypeNotAllowedError: Тип reg*/aclitem (и их массивы) в любой позиции TypeName
                (каст, колонка табличной функции, аргумент PREPARE) в basic.
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
            if self._basic and unqualified == "hypopg_create_index":
                self._validate_hypopg_create_index(node)

        if self._basic and isinstance(node, VariableShowStmt):
            name = node.name or ""
            if name.lower() not in BASIC_SHOW_PARAMETERS:
                raise ShowParameterNotAllowedError(name, sorted(BASIC_SHOW_PARAMETERS))

        if self._basic and isinstance(node, TypeName):
            self._validate_type_name(node)

        if self._basic and isinstance(node, CollateClause):
            self._validate_name_qualifier(_name_parts(node.collname))

        # Операторы и методы TABLESAMPLE резолвятся по имени, как типы: OPERATOR(secret.+),
        # a OPERATOR(secret.=) ANY (SELECT ...), ORDER BY ... USING OPERATOR(secret.<), TABLESAMPLE secret.m(1).
        # У SubLink для IN и EXISTS operName пуст.
        if self._basic and isinstance(node, A_Expr):
            self._validate_name_qualifier(_name_parts(node.name))
        if self._basic and isinstance(node, SubLink):
            self._validate_name_qualifier(_name_parts(node.operName))
        if self._basic and isinstance(node, SortBy):
            self._validate_name_qualifier(_name_parts(node.useOp))
        if self._basic and isinstance(node, RangeTableSample):
            self._validate_name_qualifier(_name_parts(node.method))

        if isinstance(node, SelectStmt) and getattr(node, "lockingClause", None):
            raise LockingClauseProhibitedError

        if isinstance(node, ExplainStmt):
            self._validate_explain_options(node)

        if isinstance(node, CreateExtensionStmt):
            self._validate_create_extension(node)

    def _validate_explain_options(self, node: ExplainStmt) -> None:
        """ANALYZE — по флагу allow_explain_analyze и первым; в basic прочие опции — только из BASIC_EXPLAIN_OPTIONS.

        Имя сравнивается без учёта регистра: pglast уже свернул имена без кавычек, а имя в кавычках
        ("SETTINGS") Postgres не распознаёт — отказ такому имени ничего не ломает и обхода не даёт.

        Raises:
            ExplainAnalyzeNotSupportedError: ANALYZE при allow_explain_analyze=False.
            ExplainOptionNotAllowedError: В basic опция вне BASIC_EXPLAIN_OPTIONS.
        """
        names = [
            option.defname.lower() for option in node.options or () if isinstance(option, DefElem) and option.defname
        ]
        if not self._allow_explain_analyze and "analyze" in names:
            raise ExplainAnalyzeNotSupportedError
        if not self._basic:
            return
        for name in names:
            if name != "analyze" and name not in BASIC_EXPLAIN_OPTIONS:
                raise ExplainOptionNotAllowedError(name, sorted(BASIC_EXPLAIN_OPTIONS))

    def _validate_type_name(self, node: TypeName) -> None:
        """R4 в basic: имя типа не резолвит объекты по имени и не выводит за allowed_schema.

        Функция ввода reg*-типов и aclitem ищет объекты по имени из строки; строковый тип системного
        отношения (NULL::pg_authid) — то же отношение, что закрывает R1; тип или домен чужой схемы
        (NULL::secret.accounts, enum_range(NULL::secret.status)) раскрывает её объекты. Массив
        (_regclass, pg_catalog._aclitem, secret.t[]) проверяется по имени элемента.

        Raises:
            TypeNotAllowedError: Тип reg* или aclitem (в том числе массив).
            SystemRelationAccessError: Имя pg_*/_pg_* вне скалярных BASIC_PG_SCALAR_TYPES.
            SchemaNotAllowedError: Схема типа — не allowed_schema и не pg_catalog.
        """
        names = _name_parts(node.names)
        if not names:
            return
        type_name = names[-1].lower()
        element = type_name.removeprefix("_")
        if element in NAME_LOOKUP_TYPES:
            raise TypeNotAllowedError(type_name)
        if is_system_relation_name(element) and element not in BASIC_PG_SCALAR_TYPES:
            raise SystemRelationAccessError(type_name)
        self._validate_name_qualifier(names)

    def _validate_name_qualifier(self, names: list[str]) -> None:
        """Схема составного имени (тип, collation, оператор, метод TABLESAMPLE) — allowed_schema или pg_catalog.

        Неквалифицированное имя не проверяется: без каталога встроенный тип (int4) не отличить
        от строкового типа таблицы без префикса (спека basic-confinement §6). Сравнение точное:
        pglast уже свернул имена без кавычек, а "PUBLIC" в кавычках — другая схема.

        Raises:
            SchemaNotAllowedError: Явная схема — не allowed_schema и не pg_catalog.
        """
        allowed_schema = self._allowed_schema
        qualifiers = names[:-1]
        if allowed_schema is None or not qualifiers:
            return
        schema = qualifiers[-1]
        if schema not in (allowed_schema, "pg_catalog"):
            raise SchemaNotAllowedError(schema, allowed_schema)

    def _validate_hypopg_create_index(self, node: FuncCall) -> None:
        """В basic аргумент hypopg_create_index — CREATE INDEX по разрешённой таблице из простых столбцов.

        Строку hypopg разбирает сам, валидатор её иначе не видит: без проверки relation агент узнавал бы,
        существуют ли таблицы и колонки чужих схем, и получал бы оценку их размера; без проверки
        `_is_plain_index` — то же самое через выражения, WHERE, opclass и TABLESPACE, даже когда сама
        таблица индекса разрешена.

        Raises:
            FunctionNotAllowedError: Аргумент не строковая константа, не ровно один CREATE INDEX,
                или индекс не сводится к простым столбцам.
            SystemRelationAccessError: Индекс на системном отношении.
            SchemaNotAllowedError: Индекс на таблице любой схемы, кроме allowed_schema (в том числе
                information_schema, которую validate_schema_access пропускает для чтения).
            TablePrefixAccessError: Имя таблицы не соответствует префиксу.
        """
        func_name = "hypopg_create_index"
        args = node.args or ()
        value = args[0].val if len(args) == 1 and isinstance(args[0], A_Const) else None
        if not isinstance(value, String) or value.sval is None:
            raise FunctionNotAllowedError(func_name)
        try:
            statements = pglast.parse_sql(value.sval)
        except pglast.parser.ParseError as e:
            raise FunctionNotAllowedError(func_name) from e
        index = statements[0].stmt if len(statements) == 1 else None
        if not isinstance(index, IndexStmt) or index.relation is None:
            raise FunctionNotAllowedError(func_name)
        validate_schema_access(index.relation, allowed_schema=self._allowed_schema, table_prefix=self._table_prefix)
        schema = index.relation.schemaname
        allowed_schema = self._allowed_schema
        if schema is not None and allowed_schema is not None and schema != allowed_schema:
            raise SchemaNotAllowedError(schema, allowed_schema)
        if not _is_plain_index(index):
            raise FunctionNotAllowedError(func_name)

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
            SystemRelationAccessError: Доступ к системному отношению (pg_*, _pg_*) в basic,
                или закрытое представление information_schema.
            TablePrefixAccessError: Доступ к таблице не разрешён (префикс).
            SchemaNotAllowedError: Доступ к схеме не разрешён.
            SchemataTableAccessError: Доступ к information_schema.schemata в user mode.
            LikePatternNotConstantError: LIKE-паттерн не константа.
            FunctionNotAllowedError: Функция не разрешена.
            LockingClauseProhibitedError: Блокирующее предложение в SELECT.
            ExplainAnalyzeNotSupportedError: EXPLAIN ANALYZE не поддерживается.
            ExplainOptionNotAllowedError: Опция EXPLAIN вне списка basic (SETTINGS, WAL, SERIALIZE, незнакомые).
            CreateExtensionNotSupportedError: Расширение не разрешено.
            ShowParameterNotAllowedError: Параметр SHOW вне разрешённого списка basic.
            TypeNotAllowedError: Тип reg*/aclitem (и их массивы) в любой позиции TypeName
                (каст, колонка табличной функции, аргумент PREPARE) в basic.
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
