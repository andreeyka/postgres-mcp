# mypy: ignore-errors
"""Unit tests for SqlExecutionService."""

from unittest.mock import MagicMock

import pytest

from postgres_fastmcp.common.errors import SqlExecutionError
from postgres_fastmcp.services.sql_execution.service import SqlExecutionService
from postgres_fastmcp.sql.models.row_result import RowResult


class TestSqlExecutionService:
    """Tests for SqlExecutionService.execute_sql."""

    async def test_execute_sql_success_returns_decoded_rows(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """Successful execution returns list of decoded row dicts; driver called with readonly=True."""
        mock_executor.execute.return_value = [
            RowResult(cells={"id": 1, "name": "a"}),
            RowResult(cells={"id": 2, "name": "b"}),
        ]
        service = SqlExecutionService(db=mock_db_access)
        result = await service.execute_sql("SELECT 1")
        assert len(result) == 2
        assert result[0]["id"] == 1 and result[0]["name"] == "a"
        assert result[1]["id"] == 2 and result[1]["name"] == "b"
        mock_executor.execute.assert_called_once()
        assert mock_executor.execute.call_args[1].get("readonly") is True

    async def test_execute_sql_empty_list_returns_empty(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """When driver returns empty rows, service returns empty list."""
        mock_executor.execute.return_value = []
        service = SqlExecutionService(db=mock_db_access)
        result = await service.execute_sql("SELECT 0")
        assert result == []
        mock_executor.execute.assert_called_once()

    async def test_execute_sql_none_raises_sql_execution_error(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """When driver returns None (e.g. DDL), service raises SqlExecutionError."""
        mock_executor.execute.return_value = None
        service = SqlExecutionService(db=mock_db_access)
        with pytest.raises(SqlExecutionError) as exc_info:
            await service.execute_sql("CREATE TABLE t (id int)")
        assert "No results" in str(exc_info.value.message)

    async def test_execute_sql_bytes_decoded(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """Row cells containing bytes are decoded to UTF-8 strings in result."""
        mock_executor.execute.return_value = [
            RowResult(cells={"name": b"hello", "num": 42}),
        ]
        service = SqlExecutionService(db=mock_db_access)
        result = await service.execute_sql("SELECT 'hello'")
        assert result[0]["name"] == "hello"
        assert result[0]["num"] == 42
