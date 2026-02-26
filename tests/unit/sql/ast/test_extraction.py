# mypy: ignore-errors
"""Unit tests for sql.ast.extraction."""

from postgres_fastmcp.sql.ast.extraction import extract_columns, extract_tables_from_query, get_table_aliases


class TestExtractTablesFromQuery:
    """Tests for extract_tables_from_query."""

    def test_single_table(self) -> None:
        assert extract_tables_from_query("SELECT * FROM users") == {"users"}

    def test_multiple_tables_join(self) -> None:
        q = "SELECT a.id, b.name FROM users a JOIN orders b ON a.id = b.user_id"
        tables = extract_tables_from_query(q)
        assert "users" in tables
        assert "orders" in tables

    def test_subquery(self) -> None:
        q = "SELECT * FROM (SELECT id FROM users) sub"
        assert "users" in extract_tables_from_query(q)

    def test_empty_or_invalid_returns_empty(self) -> None:
        assert extract_tables_from_query("") == set()
        assert extract_tables_from_query("INSERT INTO t VALUES (1)") == set()

    def test_invalid_sql_returns_empty(self) -> None:
        assert extract_tables_from_query("SELECT FROM") == set()


class TestExtractColumns:
    """Tests for extract_columns."""

    def test_simple_select(self) -> None:
        result = extract_columns("SELECT id, name FROM users")
        assert "users" in result
        assert "id" in result["users"] or "name" in result["users"]

    def test_empty_cache(self) -> None:
        result = extract_columns("SELECT x FROM t", column_cache=None)
        assert isinstance(result, dict)

    def test_non_select_returns_empty(self) -> None:
        assert extract_columns("INSERT INTO t (a) VALUES (1)") == {}

    def test_invalid_sql_returns_empty(self) -> None:
        assert extract_columns("not sql") == {}


class TestGetTableAliases:
    """Tests for get_table_aliases."""

    def test_table_name_included(self) -> None:
        aliases = get_table_aliases("SELECT * FROM users", "users")
        assert "users" in aliases

    def test_alias_found(self) -> None:
        aliases = get_table_aliases("SELECT * FROM users u", "users")
        assert "users" in aliases
        assert "u" in aliases

    def test_non_select_returns_table_name_only(self) -> None:
        assert get_table_aliases("INSERT INTO t VALUES (1)", "t") == ["t"]

    def test_invalid_sql_returns_table_name_only(self) -> None:
        assert get_table_aliases("invalid", "mytable") == ["mytable"]
