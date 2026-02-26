from typing import Any, cast

from postgres_fastmcp.common.errors import SchemaAccessError, UnsupportedObjectTypeError
from postgres_fastmcp.common.utils import decode_bytes_to_utf8
from postgres_fastmcp.enums import AccessMode
from postgres_fastmcp.services.db_access_service import DbAccessService

from .extensions import ExtensionsService
from .sequences import SequencesService
from .tables import TablesService


class ObjectsService:
    """Фасад: маршрутизирует вызовы list_objects и get_object_details в отдельные сервисы."""

    def __init__(self, db: DbAccessService) -> None:
        """Инициализация сервиса с подключением к базе данных."""
        self.db = db
        self._tables = TablesService(db)
        self._sequences = SequencesService(db)
        self._extensions = ExtensionsService(db)

    def _resolve_schema(self, schema_name: str) -> str:
        """Разрешить схему для режима пользователя. Возвращает schema_name; выбрасывает исключение при запрете доступа.

        Args:
            schema_name: Имя схемы для разрешения.

        Returns:
            Имя схемы, если доступ разрешен.

        Raises:
            SchemaAccessError: Если доступ к запрошенной схеме запрещен.
        """
        if self.db.access_mode == AccessMode.BASIC:
            if schema_name and schema_name.lower() != "public":
                raise SchemaAccessError(schema_name)
            return "public"
        return schema_name

    async def list_objects(
        self,
        schema_name: str,
        object_type: str = "table",
    ) -> list[dict[str, Any]]:
        """Получить список объектов — делегирует вызовы TablesService, SequencesService или ExtensionsService.

        Args:
            schema_name: Имя схемы для получения объектов.
            object_type: Тип объектов ("table", "view", "sequence", "extension") (по умолчанию "table").

        Returns:
            Список объектов в виде списка словарей.

        Raises:
            SchemaAccessError: Если доступ к запрошенной схеме запрещен.
            UnsupportedObjectTypeError: Если тип объекта не поддерживается.
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
        """Получить детальную информацию об объекте.

         Делегирует вызовы TablesService, SequencesService или ExtensionsService.

        Args:
            schema_name: Имя схемы объекта.
            object_name: Имя объекта.
            object_type: Тип объекта ("table", "view", "sequence", "extension") (по умолчанию "table").

        Returns:
            Словарь с детальной информацией об объекте.

        Raises:
            SchemaAccessError: Если доступ к запрошенной схеме запрещен.
            UnsupportedObjectTypeError: Если тип объекта не поддерживается.
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
