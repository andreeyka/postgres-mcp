# mypy: ignore-errors
"""Unit tests for SchemaService."""

from unittest.mock import MagicMock

from postgres_fastmcp.enums import UserRole
from postgres_fastmcp.services.schema.service import SchemaService
from postgres_fastmcp.sql.models.row_result import RowResult


class TestSchemaService:
    """Tests for SchemaService.list_schemas."""

    async def test_list_schemas_user_role_returns_public_only(
        self,
        mock_db_access: MagicMock,
    ) -> None:
        """USER role returns hardcoded public schema without calling DB."""
        mock_db_access.role = UserRole.USER
        service = SchemaService(db=mock_db_access)
        result = await service.list_schemas()
        assert result == [
            {
                "schema_name": "public",
                "schema_owner": "postgres",
                "schema_type": "User Schema",
            },
        ]
        mock_db_access.sql_driver.execute.assert_not_called()

    async def test_list_schemas_full_role_returns_decoded_catalog_rows(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """FULL role returns list of dicts with schema_name, schema_owner, schema_type; driver called once with readonly."""
        mock_executor.execute.return_value = [
            RowResult(cells={"schema_name": "public", "schema_owner": "postgres", "schema_type": "User Schema"}),
            RowResult(cells={"schema_name": "ext", "schema_owner": "postgres", "schema_type": "User Schema"}),
        ]
        service = SchemaService(db=mock_db_access)
        result = await service.list_schemas()
        assert len(result) == 2
        assert result[0]["schema_name"] == "public"
        assert result[0]["schema_owner"] == "postgres"
        assert result[1]["schema_name"] == "ext"
        mock_executor.execute.assert_called_once()
        call_kw = mock_executor.execute.call_args[1]
        assert call_kw.get("readonly") is True

    async def test_list_schemas_full_role_empty_result(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """FULL role when catalog returns no rows returns empty list."""
        mock_executor.execute.return_value = []
        service = SchemaService(db=mock_db_access)
        result = await service.list_schemas()
        assert result == []
        mock_executor.execute.assert_called_once()
