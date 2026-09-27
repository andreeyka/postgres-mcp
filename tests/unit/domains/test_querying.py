# mypy: ignore-errors
"""Unit tests for domains.querying.execute_sql."""

from unittest.mock import MagicMock

from postgres_fastmcp.domains.querying import execute_sql
from postgres_fastmcp.postgres.models import RowResult, StatementResult


class TestExecuteSql:
    """Tests for querying.execute_sql."""

    async def test_execute_sql_success_returns_decoded_rows(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """Successful execution returns list of decoded row dicts; read-only server uses readonly=True."""
        mock_db_access.write_mode = False
        mock_executor.execute_statement.return_value = StatementResult(
            rows=[RowResult(cells={"id": 1, "name": "a"}), RowResult(cells={"id": 2, "name": "b"})],
            status="SELECT 2",
            affected_rows=2,
        )
        result = await execute_sql(mock_db_access, "SELECT 1")
        assert result == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
        mock_executor.execute_statement.assert_awaited_once_with("SELECT 1", params=None, readonly=True)
        mock_executor.execute.assert_not_called()

    async def test_execute_sql_write_mode_uses_read_write_transaction(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """When write_mode is enabled the driver is called with readonly=False so writes persist."""
        mock_db_access.write_mode = True
        mock_executor.execute_statement.return_value = StatementResult(rows=None, status="INSERT 0 1", affected_rows=1)
        await execute_sql(mock_db_access, "INSERT INTO t (id) VALUES (1)")
        mock_executor.execute_statement.assert_awaited_once_with(
            "INSERT INTO t (id) VALUES (1)", params=None, readonly=False
        )

    async def test_execute_sql_empty_result_set_returns_empty_list(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """SELECT without rows is still a result set: an empty list, not a command status."""
        mock_executor.execute_statement.return_value = StatementResult(rows=[], status="SELECT 0", affected_rows=0)
        result = await execute_sql(mock_db_access, "SELECT 0 WHERE false")
        assert result == []

    async def test_execute_sql_without_result_set_returns_command_status(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """DML without RETURNING and DDL return the StatementResult with the Postgres command tag."""
        mock_db_access.write_mode = True
        status = StatementResult(rows=None, status="UPDATE 500", affected_rows=500)
        mock_executor.execute_statement.return_value = status
        result = await execute_sql(mock_db_access, "UPDATE t SET v = 1")
        assert result is status

    async def test_execute_sql_bytes_decoded(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """Row cells containing bytes are decoded to UTF-8 strings in result."""
        mock_executor.execute_statement.return_value = StatementResult(
            rows=[RowResult(cells={"name": b"hello", "num": 42})], status="SELECT 1", affected_rows=1
        )
        result = await execute_sql(mock_db_access, "SELECT 'hello'")
        assert result == [{"name": "hello", "num": 42}]
