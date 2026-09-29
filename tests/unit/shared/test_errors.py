"""Тесты: user-facing исключения доходят до клиента и написаны по-английски с подсказкой."""

import re
from collections.abc import Callable

import pytest
from fastmcp.exceptions import ToolError

from postgres_fastmcp.shared import errors


# Пример каждого подкласса UserFacingError с тестовыми аргументами.
# Новый подкласс без записи здесь роняет test_every_user_facing_error_has_a_sample.
_SAMPLES: dict[str, Callable[[], errors.UserFacingError]] = {
    "SchemaAccessError": lambda: errors.SchemaAccessError("private"),
    "SchemaNotAllowedError": lambda: errors.SchemaNotAllowedError("private", "public"),
    "TablePrefixAccessError": lambda: errors.TablePrefixAccessError("foo", "bar_"),
    "SchemataTableAccessError": lambda: errors.SchemataTableAccessError("information_schema", "schemata"),
    "PlanAccessError": lambda: errors.PlanAccessError(
        "relation", "secret.accounts", allowed_schema="public", table_prefix=None
    ),
    "PlanUnverifiableError": errors.PlanUnverifiableError,
    "SqlParseError": errors.SqlParseError,
    "StatementTypeNotAllowedError": lambda: errors.StatementTypeNotAllowedError(
        read_only=True, stmt_type_name="CreateStmt"
    ),
    "DdlNotAllowedError": lambda: errors.DdlNotAllowedError("CreateStmt"),
    "DisallowedNodeTypeError": lambda: errors.DisallowedNodeTypeError(int),
    "LikePatternNotConstantError": errors.LikePatternNotConstantError,
    "FunctionNotAllowedError": lambda: errors.FunctionNotAllowedError("pg_sleep"),
    "LockingClauseProhibitedError": errors.LockingClauseProhibitedError,
    "ExplainAnalyzeNotSupportedError": errors.ExplainAnalyzeNotSupportedError,
    "CreateExtensionNotSupportedError": lambda: errors.CreateExtensionNotSupportedError("dblink"),
    "UnsupportedObjectTypeError": lambda: errors.UnsupportedObjectTypeError("tabel"),
    "ExplainAnalyzeWithHypotheticalError": errors.ExplainAnalyzeWithHypotheticalError,
    "EmptyQueriesError": errors.EmptyQueriesError,
    "QueriesLimitError": lambda: errors.QueriesLimitError(10),
    "InvalidSortCriteriaError": lambda: errors.InvalidSortCriteriaError("mean"),
    "InvalidHealthTypeError": lambda: errors.InvalidHealthTypeError("indx", ["all", "index", "vacuum"]),
    "InvalidOutputFormatError": lambda: errors.InvalidOutputFormatError("jsn"),
    "PgStatStatementsNotInstalledError": errors.PgStatStatementsNotInstalledError,
    "UnsupportedServerVersionError": lambda: errors.UnsupportedServerVersionError(
        "sort_by='resources'", 13, 12, hint="Use sort_by='total_time' or 'mean_time' instead."
    ),
    "HypopgNotInstalledError": lambda: errors.HypopgNotInstalledError("The hypopg extension is not installed."),
    "QueryTimeoutError": lambda: errors.QueryTimeoutError(5.0),
    "QueryCancelledError": errors.QueryCancelledError,
    "ResponseTooLargeError": lambda: errors.ResponseTooLargeError(25000, 20000),
    "ResponseTooLargeAfterWriteError": lambda: errors.ResponseTooLargeAfterWriteError(25000, 20000),
    "ObjectNotFoundError": lambda: errors.ObjectNotFoundError("public", "ghost", "table"),
    "ExtensionStatusUnavailableError": lambda: errors.ExtensionStatusUnavailableError(
        "pg_stat_statements", "Unable to determine extension status."
    ),
    "SystemRelationAccessError": lambda: errors.SystemRelationAccessError("pg_stats"),
    "ShowParameterNotAllowedError": lambda: errors.ShowParameterNotAllowedError(
        "app.secret", ["search_path", "timezone"]
    ),
    "ExplainOptionNotAllowedError": lambda: errors.ExplainOptionNotAllowedError("settings", ["format", "costs"]),
    "TypeNotAllowedError": lambda: errors.TypeNotAllowedError("regclass"),
}

_CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def _all_subclasses(cls: type) -> set[type]:
    direct = set(cls.__subclasses__())
    return direct.union(*(_all_subclasses(sub) for sub in direct))


def test_every_user_facing_error_has_a_sample() -> None:
    """Каждый подкласс UserFacingError попадает в проверку языка."""
    assert {cls.__name__ for cls in _all_subclasses(errors.UserFacingError)} == set(_SAMPLES)


@pytest.mark.parametrize("name", sorted(_SAMPLES))
def test_user_facing_error_is_tool_error_in_english(name: str) -> None:
    """Сообщение доходит до клиента (ToolError) и не содержит кириллицы."""
    error = _SAMPLES[name]()
    assert isinstance(error, ToolError)
    assert str(error)
    assert not _CYRILLIC.search(str(error)), str(error)


@pytest.mark.parametrize(
    ("name", "hint"),
    [
        ("SqlParseError", "Check the SQL syntax"),
        ("DdlNotAllowedError", "Use SELECT"),
        ("DisallowedNodeTypeError", "Rewrite the query"),
        ("LikePatternNotConstantError", "string literal"),
        ("FunctionNotAllowedError", "Rewrite the query"),
        ("LockingClauseProhibitedError", "Remove FOR UPDATE"),
        ("ExplainAnalyzeNotSupportedError", "explain_query"),
        ("CreateExtensionNotSupportedError", "hypopg and pg_stat_statements"),
        ("UnsupportedObjectTypeError", "Did you mean 'table'?"),
        ("ExplainAnalyzeWithHypotheticalError", "Call explain_query twice"),
        ("EmptyQueriesError", "at least one SQL query"),
        ("QueriesLimitError", "Split the list"),
        ("InvalidSortCriteriaError", "Did you mean 'mean_time'?"),
        ("InvalidHealthTypeError", "Did you mean 'index'?"),
        ("InvalidOutputFormatError", "Did you mean 'json'?"),
        ("PgStatStatementsNotInstalledError", "CREATE EXTENSION pg_stat_statements"),
        ("ResponseTooLargeError", "Refine the request: add WHERE or LIMIT"),
        ("ResponseTooLargeAfterWriteError", "query the affected rows with a narrower SELECT"),
        ("UnsupportedServerVersionError", "Use sort_by='total_time'"),
        ("ObjectNotFoundError", 'If it is a view, retry with object_type="view"'),
        ("ExtensionStatusUnavailableError", "then retry"),
        ("SystemRelationAccessError", "Use list_objects and get_object_details"),
        ("ShowParameterNotAllowedError", "Allowed parameters: search_path, timezone."),
        ("ExplainOptionNotAllowedError", "Allowed options: COSTS, FORMAT."),
        ("TypeNotAllowedError", "Rewrite the query without object identifier types"),
        ("PlanAccessError", "Only tables in 'public' are permitted."),
        ("PlanUnverifiableError", "Rewrite the query to read the permitted tables directly."),
    ],
)
def test_correctable_error_ends_with_hint(name: str, hint: str) -> None:
    """Ошибка, которую агент может исправить сам, подсказывает, что сделать вместо этого."""
    assert hint in str(_SAMPLES[name]())


@pytest.mark.parametrize(
    ("node_type", "reason"),
    [
        ("Seq Scan", "an expression in Filter of a Seq Scan node cannot be verified"),
        (None, "an expression in Filter of a plan node cannot be verified"),
    ],
)
def test_plan_unverifiable_expression_names_the_key_not_the_text(node_type: str | None, reason: str) -> None:
    error = errors.PlanUnverifiableError(node_type, key="Filter")
    assert str(error) == (
        f"The query plan cannot be verified in basic mode: {reason}. "
        "Rewrite the query to read the permitted tables directly."
    )
    assert error.key == "Filter"


def test_plan_access_message_names_the_object_without_guessing_the_path() -> None:
    """Сообщение называет объект и не утверждает, что он достигнут через представление (есть секции, RLS)."""
    error = errors.PlanAccessError("relation", "secret.x", allowed_schema="public", table_prefix=None)
    assert str(error) == (
        "Access to relation 'secret.x' is not allowed in basic mode: the query plan reads it. "
        "Only tables in 'public' are permitted."
    )


@pytest.mark.parametrize(
    ("kind", "table_prefix", "hint"),
    [
        ("relation", None, "Only tables in 'main' are permitted."),
        ("relation", "app_", "Only tables in 'main' starting with 'app_' are permitted."),
        ("function", "app_", "Only functions from 'main' or built-in functions allowed in basic mode are permitted."),
        ("type", "app_", "Only types from 'main' or built-in types are permitted."),
    ],
)
def test_plan_access_hint_follows_kind_and_rules(kind: str, table_prefix: str | None, hint: str) -> None:
    """Подсказка называет разрешённую схему, префикс таблиц и различает отношения и функции."""
    error = errors.PlanAccessError(kind, "secret.x", allowed_schema="main", table_prefix=table_prefix)
    assert str(error).endswith(hint)


@pytest.mark.parametrize(
    ("object_type", "message"),
    [
        (
            "table",
            'Object not found: public.v (table). If it is a view, retry with object_type="view"; '
            "use list_objects to see existing objects.",
        ),
        (
            "view",
            'Object not found: public.v (view). If it is a table, retry with object_type="table"; '
            "use list_objects to see existing objects.",
        ),
        ("sequence", "Object not found: public.v (sequence). Use list_objects to see existing objects."),
        ("extension", "Object not found: v (extension). Use list_objects to see existing objects."),
    ],
)
def test_object_not_found_hints_at_the_other_relation_type(object_type: str, message: str) -> None:
    """Таблица и представление легко перепутать: подсказка называет другой тип; у sequence/extension её нет."""
    assert str(errors.ObjectNotFoundError("public", "v", object_type)) == message


@pytest.mark.parametrize(
    "cls_name",
    [
        "ConnectionNotEstablishedError",
        "ExplainPlanError",
        "ExplainPlanExecutionError",
        "ConnectionFailedError",
    ],
)
def test_internal_errors_do_not_inherit_tool_error(cls_name: str) -> None:
    """Internal классы не должны наследовать ToolError (будут маскированы)."""
    cls = getattr(errors, cls_name)
    assert not issubclass(cls, ToolError)


def test_explain_option_message_names_the_option_in_upper_case() -> None:
    """Опция и список — заглавными, как их пишут в EXPLAIN; список отсортирован."""
    error = errors.ExplainOptionNotAllowedError("wal", ["verbose", "costs"])
    assert str(error) == "EXPLAIN option WAL is not allowed in basic mode. Allowed options: COSTS, VERBOSE."
    assert error.option == "wal"
