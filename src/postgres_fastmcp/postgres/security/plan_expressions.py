"""Разбор выражений плана EXPLAIN (VERBOSE): функции, операторы, типы и последовательности, которые они называют.

Выражения плана печатает ruleutils. Ссылки планировщика на подпланы ((SubPlan 1), (hashed SubPlan 1),
(InitPlan 1).col1, EXISTS(SubPlan 1), (ANY ...), (alternatives: ...)) — не SQL: перед разбором они
заменяются на NULL, сами подпланы проверяются как узлы плана. Параметры $N pglast разбирает как есть.
Замены идут только вне строк '...' и имён "..." (ruleutils удваивает кавычки внутри них): имя в кавычках
и литерал nextval проверяются такими, какие они есть. Комментарии, подзапросы и незакрытые кавычки
ruleutils в плане не печатает — такой текст не разбирается (fail closed).
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import pglast
from pglast.ast import (
    A_Const,
    A_Expr,
    FuncCall,
    JsonTable,
    Node,
    RangeTableFunc,
    RangeVar,
    SelectStmt,
    SortBy,
    String,
    TypeCast,
    TypeName,
)
from pglast.enums.parsenodes import SetOperation
from pglast.parser import ParseError
from pglast.visitors import Visitor


# Имя из плана: (схема или None, имя).
QualifiedName = tuple[str | None, str]

# Части SelectStmt, которых в "SELECT <список выражений>" быть не должно: только список целей.
_NON_TARGET_SELECT_PARTS = (
    "fromClause",
    "whereClause",
    "groupClause",
    "havingClause",
    "withClause",
    "distinctClause",
    "sortClause",
    "limitCount",
    "limitOffset",
    "lockingClause",
    "windowClause",
    "valuesLists",
    "intoClause",
)

# Формы ruleutils внутри выражений, которые не SQL (PG 15–17), и их замены по порядку: альтернативы
# подпланов; ссылка на подплан или его колонку (PG 17: (SubPlan 1).col1, (InitPlan 1).col1, EXISTS(SubPlan 1),
# ARRAY(SubPlan 1)); PG 17 (ANY <проверка>)/(ALL <проверка>); PARTIAL у частичного агрегата; OVER (?) у оконной
# функции (в EXPLAIN нет определения окна).
_PLANNER_REFERENCES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\(alternatives: SubPlan \d+ or hashed SubPlan \d+\)"), "NULL"),
    (
        re.compile(r"(?:\b(?:EXISTS|ARRAY|CTE))?\((?:hashed |rescan )?(?:SubPlan|InitPlan) \d+\)(?:\.col\d+)?"),
        "NULL",
    ),
    (re.compile(r"\((?:ANY|ALL) "), "("),
    (re.compile(r"\bPARTIAL (?=[\w\"])"), ""),
    (re.compile(r"\bOVER \(\?\)"), "OVER ()"),
)

# Имя в кавычках или строка ('...', в том числе E'...' с экранированием \\): как их делит лексер Postgres.
_QUOTED = re.compile(r"""(?:(?<![\w$])[eE]'(?:[^'\\]|\\.|'')*'|'(?:[^']|'')*'|"(?:[^"]|"")*")""", re.DOTALL)

# Заглушка вместо строки или имени в кавычках на время замен: первый символ — кавычка (для просмотра вперёд
# PARTIAL), дальше номер между \x00, которые в выражении плана не встречаются.
_PLACEHOLDER = re.compile(r"[\"']\x00(\d+)\x00")

# Вне кавычек: комментарий, dollar-строка ($ не перед цифрой параметра) или незакрытая кавычка.
_UNQUOTED_FORBIDDEN = re.compile(r"--|/\*|\$(?!\d)|[\"']")

# Как regclassout печатает имя отношения: [схема.]имя, каждое — идентификатор без кавычек или в кавычках.
_IDENTIFIER = r'(?:[A-Za-z_\x80-\U0010ffff][\w$]*|"(?:[^"]|"")+")'
_REGCLASS_NAME = re.compile(rf"{_IDENTIFIER}(?:\.{_IDENTIFIER})?")

# nextval('последовательность'::тип): так план печатает DEFAULT serial ('...'::regclass) и identity
# ('...'::bigint). Литерал — имя отношения, проверяется как отношение, а не как вызов функции.
_NEXTVAL: frozenset[QualifiedName] = frozenset({(None, "nextval"), ("pg_catalog", "nextval")})


@dataclass(frozen=True, slots=True)
class ExpressionNames:
    """Имена, которые выражение плана резолвит по каталогу."""

    functions: tuple[QualifiedName, ...] = ()
    operators: tuple[QualifiedName, ...] = ()
    types: tuple[QualifiedName, ...] = ()
    sequences: tuple[QualifiedName, ...] = ()


def _qualified(parts: Iterable[Node]) -> QualifiedName | None:
    """(схема или None, имя) из частей имени; None — части не строки, пустые или их больше двух."""
    names = tuple(part.sval if isinstance(part, String) else None for part in parts)
    match names:
        case (str(name),) if name:
            return None, name
        case (str(schema), str(name)) if schema and name:
            return schema, name
        case _:
            return None


def sequence_name(text: str) -> QualifiedName | None:
    """Имя отношения из литерала nextval: regclassout печатает его в кавычках по правилам идентификаторов.

    Принимается только [схема.]имя без пробелов, комментариев, ONLY и звёздочки наследования.
    """
    if _REGCLASS_NAME.fullmatch(text) is None:
        return None
    # Текст только разбирается pglast, в Postgres не отправляется.
    statement = _single_select(f"SELECT 1 FROM {text}")  # noqa: S608
    relations = statement.fromClause or () if statement is not None else ()
    if statement is None or not _only(statement, "fromClause") or len(relations) != 1:
        return None
    relation = relations[0]
    if (
        not isinstance(relation, RangeVar)
        or relation.catalogname
        or relation.alias
        or not relation.relname
        or not relation.inh
    ):
        return None
    return relation.schemaname, relation.relname


def _literal_argument(call: FuncCall) -> str | None:
    """Строка единственного аргумента вида 'литерал'::тип, иначе None."""
    args = call.args or ()
    if len(args) != 1 or not isinstance(args[0], TypeCast) or not isinstance(args[0].arg, A_Const):
        return None
    value = args[0].arg.val
    return value.sval if isinstance(value, String) else None


class _Names(Visitor):
    """Собирает имена выражения; verifiable=False — встретилось имя, которое не разложить на схему и имя."""

    def __init__(self) -> None:
        """Пустые списки имён."""
        super().__init__()
        self.functions: list[QualifiedName] = []
        self.operators: list[QualifiedName] = []
        self.types: list[QualifiedName] = []
        self.sequences: list[QualifiedName] = []
        self.verifiable = True
        self._selects = 0

    def _add(self, found: list[QualifiedName], parts: Iterable[Node] | None) -> None:
        """Добавить имя; пустое имя (A_Expr без оператора, SortBy без USING) пропускается."""
        if not parts:
            return
        name = _qualified(parts)
        if name is None:
            self.verifiable = False
        else:
            found.append(name)

    def visit_FuncCall(self, _ancestors: object, node: FuncCall) -> None:  # noqa: N802
        """Вызов функции или nextval по литералу последовательности."""
        name = _qualified(node.funcname or ())
        literal = _literal_argument(node) if name in _NEXTVAL else None
        if literal is None:
            self._add(self.functions, node.funcname)
            return
        sequence = sequence_name(literal)
        if sequence is None:
            self.verifiable = False
        else:
            self.sequences.append(sequence)

    def visit_SelectStmt(self, _ancestors: object, _node: SelectStmt) -> None:  # noqa: N802
        """Первый SELECT — обёртка разбора; любой следующий — подзапрос, которого ruleutils в плане не печатает."""
        self._selects += 1
        if self._selects > 1:
            self.verifiable = False

    def visit_RangeVar(self, _ancestors: object, _node: RangeVar) -> None:  # noqa: N802
        """Отношение внутри выражения: его не проверить как узел плана."""
        self.verifiable = False

    def visit_A_Expr(self, _ancestors: object, node: A_Expr) -> None:  # noqa: N802
        """Оператор выражения (OPERATOR(schema.op) — со схемой)."""
        self._add(self.operators, node.name)

    def visit_SortBy(self, _ancestors: object, node: SortBy) -> None:  # noqa: N802
        """Оператор USING ключа сортировки."""
        self._add(self.operators, node.useOp)

    def visit_TypeName(self, _ancestors: object, node: TypeName) -> None:  # noqa: N802
        """Тип приведения или колонки табличной функции."""
        self._add(self.types, node.names)


def _collect(statement: SelectStmt) -> ExpressionNames | None:
    """Имена разобранного выражения; None — среди них есть неразборчивое."""
    names = _Names()
    names(statement)
    if not names.verifiable:
        return None
    return ExpressionNames(
        functions=tuple(names.functions),
        operators=tuple(names.operators),
        types=tuple(names.types),
        sequences=tuple(names.sequences),
    )


def _nullify_planner_references(text: str) -> str | None:
    """Заменить вне кавычек ссылки планировщика на подпланы и формы ruleutils, которые не SQL.

    None — нулевой символ, незакрытая кавычка, комментарий или dollar-строка: так ruleutils не печатает.
    """
    if "\x00" in text:
        return None
    quoted: list[str] = []

    def hide(match: re.Match[str]) -> str:
        quoted.append(match.group())
        quote = "'" if match.group()[0] in "eE" else match.group()[0]
        return f"{quote}\x00{len(quoted) - 1}\x00"

    masked = _QUOTED.sub(hide, text)
    for pattern, replacement in _PLANNER_REFERENCES:
        masked = pattern.sub(replacement, masked)
    if _UNQUOTED_FORBIDDEN.search(_PLACEHOLDER.sub(" ", masked)):
        return None
    return _PLACEHOLDER.sub(lambda match: quoted[int(match.group(1))], masked)


def _single_select(sql: str) -> SelectStmt | None:
    """Ровно один простой SELECT (без UNION и подобного); None — не разбирается или не он."""
    try:
        statements = pglast.parse_sql(sql)
    except ParseError:
        return None
    statement = statements[0].stmt if len(statements) == 1 else None
    if not isinstance(statement, SelectStmt) or statement.op != SetOperation.SETOP_NONE:
        return None
    return statement


def _only(statement: SelectStmt, *allowed: str) -> bool:
    """Кроме списка целей в SELECT есть только части из allowed."""
    return not any(getattr(statement, part) for part in _NON_TARGET_SELECT_PARTS if part not in allowed)


def parse_target_list(text: object) -> SelectStmt | None:
    """Разобрать текст как список целей "SELECT <text>" без замен; None — пусто, не разбирается или не только цели."""
    if not isinstance(text, str) or not text.strip():
        return None
    statement = _single_select(f"SELECT {text}")
    if statement is None or not _only(statement) or not statement.targetList:
        return None
    return statement


def parse_expression(text: str) -> ExpressionNames | None:
    """Выражение (или список выражений через запятую, как Cache Key) плана."""
    substituted = _nullify_planner_references(text)
    statement = None if substituted is None else parse_target_list(substituted)
    return None if statement is None else _collect(statement)


def parse_sort_key(text: str) -> ExpressionNames | None:
    """Ключ сортировки: выражение с COLLATE, DESC, NULLS FIRST/LAST или USING <оператор>."""
    substituted = _nullify_planner_references(text)
    if substituted is None or not substituted.strip():
        return None
    statement = _single_select(f"SELECT 1 ORDER BY {substituted}")
    if (
        statement is None
        or not _only(statement, "sortClause")
        or len(statement.targetList or ()) != 1
        or len(statement.sortClause or ()) != 1
    ):
        return None
    return _collect(statement)


def parse_table_function(text: str) -> ExpressionNames | None:
    """Table Function Call: XMLTABLE(...) или JSON_TABLE(...) — разбирается как элемент FROM."""
    substituted = _nullify_planner_references(text)
    if substituted is None or not substituted.strip():
        return None
    # Текст только разбирается pglast, в Postgres не отправляется.
    statement = _single_select(f"SELECT * FROM {substituted}")  # noqa: S608
    sources = statement.fromClause or () if statement is not None else ()
    if (
        statement is None
        or not _only(statement, "fromClause")
        or len(statement.targetList or ()) != 1
        or len(sources) != 1
        or not isinstance(sources[0], (RangeTableFunc, JsonTable))
    ):
        return None
    return _collect(statement)


def expression_texts(value: object) -> list[str] | None:
    """Строки выражений значения ключа: строка, список или список списков (Group Keys); None — другая форма."""
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        return None
    texts: list[str] = []
    for item in value:
        nested = expression_texts(item)
        if nested is None:
            return None
        texts.extend(nested)
    return texts


# Function Call узла Function Scan: имена его функций проверяет строгий путь PlanGuard (_check_function_calls),
# здесь — только типы, операторы и последовательности.
FUNCTION_CALL_KEY = "Function Call"

# Ключи узла плана с выражениями (explain.c, VERBOSE, PG 15–17) и разбор их строк.
EXPRESSION_PARSERS: dict[str, Callable[[str], ExpressionNames | None]] = {
    **dict.fromkeys(
        (
            "Output",
            "Filter",
            "Join Filter",
            "Hash Cond",
            "Merge Cond",
            "Index Cond",
            "Recheck Cond",
            "TID Cond",
            "One-Time Filter",
            "Run Condition",
            "Order By",
            "Cache Key",
            "Conflict Filter",
            "Group Key",
            "Group Keys",
            "Hash Key",
            "Hash Keys",
            "Sampling Parameters",
            "Repeatable Seed",
            FUNCTION_CALL_KEY,
        ),
        parse_expression,
    ),
    "Sort Key": parse_sort_key,
    "Presorted Key": parse_sort_key,
    "Table Function Call": parse_table_function,
}
