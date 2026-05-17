"""Тесты: user-facing исключения наследуют ToolError, чтобы сообщения доходили до клиента."""

import pytest
from fastmcp.exceptions import ToolError

from postgres_fastmcp.common import errors


@pytest.mark.parametrize(
    ("cls_name", "ctor_args"),
    [
        ("SchemaAccessError", ("private",)),
        ("SchemaNotAllowedError", ("private", "public")),
        ("TablePrefixAccessError", ("foo", "bar_")),
        ("SchemataTableAccessError", ("information_schema", "schemata")),
        ("SqlParseError", ()),
        ("LikePatternNotConstantError", ()),
        ("FunctionNotAllowedError", ("now",)),
        ("LockingClauseProhibitedError", ()),
        ("ExplainAnalyzeNotSupportedError", ()),
        ("CreateExtensionNotSupportedError", ("hypopg",)),
        ("DdlNotAllowedError", ("CreateStmt",)),
        ("DisallowedNodeTypeError", (int,)),
        ("UnsupportedObjectTypeError", ("widget",)),
        ("ExplainAnalyzeWithHypotheticalError", ()),
        ("EmptyQueriesError", ()),
        ("QueriesLimitError", (50,)),
        ("ContextRequiredError", ()),
        ("InvalidSortCriteriaError", ()),
        ("InvalidHealthTypeError", ("foo", "a,b,c")),
        ("QueryTimeoutError", (5.0,)),
        ("HypopgNotInstalledError", ("not installed",)),
    ],
)
def test_user_facing_errors_are_tool_errors(cls_name: str, ctor_args: tuple) -> None:
    """User-facing классы должны наследовать ToolError."""
    cls = getattr(errors, cls_name)
    assert issubclass(cls, ToolError), f"{cls_name} must inherit ToolError"
    # StatementTypeNotAllowedError uses kwargs separately; skipping smoke for positional-only here.
    cls(*ctor_args)


def test_statement_type_not_allowed_is_tool_error() -> None:
    """StatementTypeNotAllowedError также user-facing (kw-only ctor)."""
    assert issubclass(errors.StatementTypeNotAllowedError, ToolError)
    errors.StatementTypeNotAllowedError(read_only=True, stmt_type_name="CreateStmt")


@pytest.mark.parametrize(
    "cls_name",
    [
        "SqlExecutionError",
        "ConnectionNotEstablishedError",
        "SettingsNotInitializedError",
        "ExplainPlanError",
        "ExplainPlanNoResultsError",
    ],
)
def test_internal_errors_do_not_inherit_tool_error(cls_name: str) -> None:
    """Internal классы не должны наследовать ToolError (будут маскированы)."""
    cls = getattr(errors, cls_name)
    assert not issubclass(cls, ToolError)
