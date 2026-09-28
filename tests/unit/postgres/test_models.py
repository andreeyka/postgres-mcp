# mypy: ignore-errors
"""Unit tests for postgres.models (IndexDefinition)."""

import pglast
from pglast.ast import IndexStmt

from postgres_fastmcp.postgres.models import IndexDefinition


def test_definition_on_a_schema_qualified_table_parses() -> None:
    definition = IndexDefinition(table="secret.accounts", columns=("id",)).definition
    statements = pglast.parse_sql(definition)
    assert len(statements) == 1
    assert isinstance(statements[0].stmt, IndexStmt)
    assert (statements[0].stmt.relation.schemaname, statements[0].stmt.relation.relname) == ("secret", "accounts")
    assert statements[0].stmt.idxname == "dba_idx_secret_accounts_id_1"


def test_name_of_an_unqualified_table_is_unchanged() -> None:
    assert IndexDefinition(table="app_users", columns=("name",)).name == "dba_idx_app_users_name_1"
