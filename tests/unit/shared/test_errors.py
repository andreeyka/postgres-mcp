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
        ("ObjectNotFoundError", "Use list_objects to see existing objects."),
        ("ExtensionStatusUnavailableError", "then retry"),
    ],
)
def test_correctable_error_ends_with_hint(name: str, hint: str) -> None:
    """Ошибка, которую агент может исправить сам, подсказывает, что сделать вместо этого."""
    assert hint in str(_SAMPLES[name]())


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
