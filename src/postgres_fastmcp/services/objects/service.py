"""Objects facade — routes list_objects and get_object_details to tables, sequences, extensions."""

from typing import Any, cast

from postgres_fastmcp.common.errors import SchemaAccessError, UnsupportedObjectTypeError
from postgres_fastmcp.common.utils import decode_bytes_to_utf8
from postgres_fastmcp.enums import UserRole
from postgres_fastmcp.services.db_access_service import DbAccessService

from .extensions import ExtensionsService
from .sequences import SequencesService
from .tables import TablesService


class ObjectsService:
    """Facade: routes list_objects and get_object_details to discrete services."""

    def __init__(self, db: DbAccessService) -> None:
        self.db = db
        self._tables = TablesService(db)
        self._sequences = SequencesService(db)
        self._extensions = ExtensionsService(db)

    def _resolve_schema(self, schema_name: str) -> str:
        """Resolve schema for user mode. Returns schema_name; raises if access denied."""
        if self.db.role == UserRole.USER:
            if schema_name and schema_name.lower() != "public":
                raise SchemaAccessError(schema_name)
            return "public"
        return schema_name

    async def list_objects(
        self,
        schema_name: str,
        object_type: str = "table",
    ) -> list[dict[str, Any]]:
        """List objects — delegates to TablesService, SequencesService, or ExtensionsService.

        Raises:
            SchemaAccessError: Access to the requested schema is not allowed.
            UnsupportedObjectTypeError: Object type is not supported.
        """
        schema_name = self._resolve_schema(schema_name)

        if object_type in ("table", "view"):
            return await self._tables.list_tables_views(schema_name, object_type)
        if object_type == "sequence":
            return await self._sequences.list_sequences(schema_name)
        if object_type == "extension":
            return await self._extensions.list_extensions()
        raise UnsupportedObjectTypeError(object_type)

    async def get_object_details(
        self,
        schema_name: str,
        object_name: str,
        object_type: str = "table",
    ) -> dict[str, Any]:
        """Get object details — delegates to TablesService, SequencesService, or ExtensionsService.

        Raises:
            SchemaAccessError: Access to the requested schema is not allowed.
            UnsupportedObjectTypeError: Object type is not supported.
        """
        schema_name = self._resolve_schema(schema_name)

        if object_type in ("table", "view"):
            result = await self._tables.get_details(schema_name, object_name, object_type)
        elif object_type == "sequence":
            result = await self._sequences.get_details(schema_name, object_name)
        elif object_type == "extension":
            result = await self._extensions.get_details(object_name)
        else:
            raise UnsupportedObjectTypeError(object_type)

        return cast("dict[str, Any]", decode_bytes_to_utf8(result))
