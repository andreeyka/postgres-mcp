# mypy: ignore-errors
"""Unit tests for CatalogService (facade: list_schemas, list_objects, get_object_details)."""

from unittest.mock import MagicMock

import pytest

from postgres_fastmcp.domains.catalog.service import CatalogService
from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.shared.errors import SchemaAccessError, UnsupportedObjectTypeError


class TestCatalogServiceListObjects:
    """Tests for CatalogService.list_objects."""

    async def test_list_objects_table_returns_list_with_schema_name_type(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """object_type=table returns list of dicts with schema, name, type."""
        mock_executor.execute.return_value = [
            RowResult(cells={"table_schema": "public", "table_name": "users", "table_type": "BASE TABLE"}),
        ]
        service = CatalogService(db=mock_db_access)
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
        service = CatalogService(db=mock_db_access)
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
        service = CatalogService(db=mock_db_access)
        result = await service.list_objects(schema_name="public", object_type="sequence")
        assert len(result) == 1
        assert result[0]["name"] == "seq1"
        assert result[0]["data_type"] == "bigint"

    async def test_list_objects_sequence_basic_filters_by_table_prefix(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """BASIC: sequences are filtered by the table_prefix of the request's DbAccess."""
        mock_db_access.access_mode = AccessMode.BASIC
        mock_db_access.table_prefix = "app_"
        mock_executor.execute.return_value = [
            RowResult(cells={"sequence_schema": "public", "sequence_name": "app_seq", "data_type": "bigint"}),
            RowResult(cells={"sequence_schema": "public", "sequence_name": "other_seq", "data_type": "bigint"}),
        ]
        service = CatalogService(db=mock_db_access)
        result = await service.list_objects(schema_name="public", object_type="sequence")
        assert [row["name"] for row in result] == ["app_seq"]

    async def test_list_objects_extension_returns_list_with_name(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """object_type=extension returns list with extension name."""
        mock_executor.execute.return_value = [
            RowResult(cells={"extname": "plpgsql", "extversion": "1.0", "extrelocatable": False}),
        ]
        service = CatalogService(db=mock_db_access)
        result = await service.list_objects(schema_name="public", object_type="extension")
        assert len(result) == 1
        assert result[0]["name"] == "plpgsql"

    async def test_list_objects_unsupported_type_raises(
        self,
        mock_db_access: MagicMock,
    ) -> None:
        """Unsupported object_type raises UnsupportedObjectTypeError with that type."""
        service = CatalogService(db=mock_db_access)
        with pytest.raises(UnsupportedObjectTypeError) as exc_info:
            await service.list_objects(schema_name="public", object_type="trigger")
        assert exc_info.value.object_type == "trigger"

    async def test_list_objects_basic_access_mode_non_public_schema_raises(
        self,
        mock_db_access: MagicMock,
    ) -> None:
        """access_mode=basic requesting non-public schema raises SchemaAccessError."""
        mock_db_access.access_mode = AccessMode.BASIC
        service = CatalogService(db=mock_db_access)
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
        service = CatalogService(db=mock_db_access)
        result = await service.list_objects(schema_name="public", object_type="table")
        assert result == []


class TestCatalogServiceGetObjectDetails:
    """Tests for CatalogService.get_object_details."""

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
        service = CatalogService(db=mock_db_access)
        result = await service.get_object_details("public", "users", "table")
        assert result["basic"]["schema"] == "public"
        assert result["basic"]["name"] == "users"
        assert result["basic"]["type"] == "table"
        assert len(result["columns"]) == 1
        assert result["columns"][0]["column"] == "id"
        assert result["constraints"] == []
        assert result["indexes"] == []

    async def test_get_object_details_table_runs_three_queries_in_parallel(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """columns/constraints/indexes queries must run concurrently via asyncio.gather."""
        import asyncio
        import time

        async def slow_execute(*args, **kwargs):
            await asyncio.sleep(0.15)
            return []

        mock_executor.execute.side_effect = slow_execute
        service = CatalogService(db=mock_db_access)
        start = time.perf_counter()
        await service.get_object_details("public", "users", "table")
        elapsed = time.perf_counter() - start
        assert elapsed < 0.3, f"Expected parallel execution (<0.3s), got {elapsed:.3f}s"

    async def test_get_object_details_unsupported_type_raises(
        self,
        mock_db_access: MagicMock,
    ) -> None:
        """Unsupported object_type raises UnsupportedObjectTypeError."""
        service = CatalogService(db=mock_db_access)
        with pytest.raises(UnsupportedObjectTypeError) as exc_info:
            await service.get_object_details("public", "x", "trigger")
        assert exc_info.value.object_type == "trigger"

    async def test_get_object_details_basic_access_mode_non_public_schema_raises(
        self,
        mock_db_access: MagicMock,
    ) -> None:
        """access_mode=basic requesting non-public schema raises SchemaAccessError."""
        mock_db_access.access_mode = AccessMode.BASIC
        service = CatalogService(db=mock_db_access)
        with pytest.raises(SchemaAccessError):
            await service.get_object_details("other", "t", "table")


class TestCatalogServiceListSchemas:
    """Tests for CatalogService.list_schemas."""

    async def test_list_schemas_basic_access_mode_returns_public_only(
        self,
        mock_db_access: MagicMock,
    ) -> None:
        """access_mode=basic returns hardcoded public schema without calling DB."""
        mock_db_access.access_mode = AccessMode.BASIC
        service = CatalogService(db=mock_db_access)
        result = await service.list_schemas()
        assert result == [
            {
                "schema_name": "public",
                "schema_owner": "postgres",
                "schema_type": "User Schema",
            },
        ]
        mock_db_access.sql_driver.execute.assert_not_called()

    async def test_list_schemas_full_access_mode_returns_decoded_catalog_rows(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """access_mode=full returns list of dicts from the catalog; driver called once with readonly."""
        mock_executor.execute.return_value = [
            RowResult(cells={"schema_name": "public", "schema_owner": "postgres", "schema_type": "User Schema"}),
            RowResult(cells={"schema_name": "ext", "schema_owner": "postgres", "schema_type": "User Schema"}),
        ]
        service = CatalogService(db=mock_db_access)
        result = await service.list_schemas()
        assert len(result) == 2
        assert result[0]["schema_name"] == "public"
        assert result[0]["schema_owner"] == "postgres"
        assert result[1]["schema_name"] == "ext"
        mock_executor.execute.assert_called_once()
        call_kw = mock_executor.execute.call_args[1]
        assert call_kw.get("readonly") is True

    async def test_list_schemas_full_access_mode_empty_result(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """access_mode=full when catalog returns no rows returns empty list."""
        mock_executor.execute.return_value = []
        service = CatalogService(db=mock_db_access)
        result = await service.list_schemas()
        assert result == []
        mock_executor.execute.assert_called_once()
