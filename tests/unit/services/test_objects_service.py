# mypy: ignore-errors
"""Unit tests for ObjectsService (facade: list_objects, get_object_details)."""

from unittest.mock import MagicMock

import pytest

from postgres_fastmcp.common.errors import SchemaAccessError, UnsupportedObjectTypeError
from postgres_fastmcp.enums import AccessMode
from postgres_fastmcp.services.objects.service import ObjectsService
from postgres_fastmcp.sql.models.row_result import RowResult


class TestObjectsServiceListObjects:
    """Tests for ObjectsService.list_objects."""

    async def test_list_objects_table_returns_list_with_schema_name_type(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """object_type=table returns list of dicts with schema, name, type."""
        mock_executor.execute.return_value = [
            RowResult(cells={"table_schema": "public", "table_name": "users", "table_type": "BASE TABLE"}),
        ]
        service = ObjectsService(db=mock_db_access)
        result = await service.list_objects(schema_name="public", object_type="table")
        assert len(result) == 1
        assert result[0]["schema"] == "public"
        assert result[0]["name"] == "users"
        assert result[0]["type"] == "BASE TABLE"

    async def test_list_objects_view_returns_list_with_type(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """object_type=view returns list with type VIEW."""
        mock_executor.execute.return_value = [
            RowResult(cells={"table_schema": "public", "table_name": "v1", "table_type": "VIEW"}),
        ]
        service = ObjectsService(db=mock_db_access)
        result = await service.list_objects(schema_name="public", object_type="view")
        assert len(result) == 1
        assert result[0]["type"] == "VIEW"

    async def test_list_objects_sequence_returns_list_with_name_and_data_type(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """object_type=sequence returns list with name and data_type."""
        mock_executor.execute.return_value = [
            RowResult(cells={"sequence_schema": "public", "sequence_name": "seq1", "data_type": "bigint"}),
        ]
        service = ObjectsService(db=mock_db_access)
        result = await service.list_objects(schema_name="public", object_type="sequence")
        assert len(result) == 1
        assert result[0]["name"] == "seq1"
        assert result[0]["data_type"] == "bigint"

    async def test_list_objects_extension_returns_list_with_name(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """object_type=extension returns list with extension name."""
        mock_executor.execute.return_value = [
            RowResult(cells={"extname": "plpgsql", "extversion": "1.0", "extrelocatable": False}),
        ]
        service = ObjectsService(db=mock_db_access)
        result = await service.list_objects(schema_name="public", object_type="extension")
        assert len(result) == 1
        assert result[0]["name"] == "plpgsql"

    async def test_list_objects_unsupported_type_raises(
        self,
        mock_db_access: MagicMock,
    ) -> None:
        """Unsupported object_type raises UnsupportedObjectTypeError with that type."""
        service = ObjectsService(db=mock_db_access)
        with pytest.raises(UnsupportedObjectTypeError) as exc_info:
            await service.list_objects(schema_name="public", object_type="trigger")
        assert exc_info.value.object_type == "trigger"

    async def test_list_objects_basic_access_mode_non_public_schema_raises(
        self,
        mock_db_access: MagicMock,
    ) -> None:
        """access_mode=basic requesting non-public schema raises SchemaAccessError."""
        mock_db_access.access_mode = AccessMode.BASIC
        service = ObjectsService(db=mock_db_access)
        with pytest.raises(SchemaAccessError) as exc_info:
            await service.list_objects(schema_name="other", object_type="table")
        assert exc_info.value.schema_name == "other"

    async def test_list_objects_basic_access_mode_public_schema_returns_catalog_result(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """access_mode=basic with schema_name public returns catalog result (e.g. empty list when no tables)."""
        mock_db_access.access_mode = AccessMode.BASIC
        mock_executor.execute.return_value = []
        service = ObjectsService(db=mock_db_access)
        result = await service.list_objects(schema_name="public", object_type="table")
        assert result == []


class TestObjectsServiceGetObjectDetails:
    """Tests for ObjectsService.get_object_details."""

    async def test_get_object_details_table_returns_basic_columns_constraints_indexes(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """get_object_details for table returns dict with basic, columns, constraints, indexes."""
        mock_executor.execute.side_effect = [
            [
                RowResult(cells={"column_name": "id", "data_type": "int", "is_nullable": "NO", "column_default": None}),
            ],
            [],
            [],
        ]
        service = ObjectsService(db=mock_db_access)
        result = await service.get_object_details("public", "users", "table")
        assert result["basic"]["schema"] == "public"
        assert result["basic"]["name"] == "users"
        assert result["basic"]["type"] == "table"
        assert len(result["columns"]) == 1
        assert result["columns"][0]["column"] == "id"
        assert result["constraints"] == []
        assert result["indexes"] == []

    async def test_get_object_details_unsupported_type_raises(
        self,
        mock_db_access: MagicMock,
    ) -> None:
        """Unsupported object_type raises UnsupportedObjectTypeError."""
        service = ObjectsService(db=mock_db_access)
        with pytest.raises(UnsupportedObjectTypeError) as exc_info:
            await service.get_object_details("public", "x", "trigger")
        assert exc_info.value.object_type == "trigger"

    async def test_get_object_details_basic_access_mode_non_public_schema_raises(
        self,
        mock_db_access: MagicMock,
    ) -> None:
        """access_mode=basic requesting non-public schema raises SchemaAccessError."""
        mock_db_access.access_mode = AccessMode.BASIC
        service = ObjectsService(db=mock_db_access)
        with pytest.raises(SchemaAccessError):
            await service.get_object_details("other", "t", "table")
