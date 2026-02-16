"""Регистрация MCP-инструментов метаданных объектов БД."""

from typing import Annotated, Any

from fastmcp.server.providers import LocalProvider
from pydantic import Field

from postgres_fastmcp.di.objects_provider import ObjectsServiceProvider
from postgres_fastmcp.enums import ToolTag
from postgres_fastmcp.services.objects.service import ObjectsService
from postgres_fastmcp.tools.common import ToolDescriptions


def register_objects_tools(provider: LocalProvider, descriptions: ToolDescriptions) -> None:
    """Зарегистрировать инструменты списка объектов и деталей объектов.

    Args:
        provider: Провайдер для регистрации инструментов.
        descriptions: Описания инструментов.
    """

    @provider.tool(
        description=descriptions.list_objects,
        tags={ToolTag.BASIC},
        annotations={"readOnlyHint": True},
    )
    async def list_objects(
        schema_name: Annotated[
            str,
            Field(description="Schema name as string value for filtering objects by database schema location"),
        ],
        object_type: Annotated[
            str,
            Field(
                default="table",
                description=(
                    "Object type as string value: 'table' for tables, 'view' for views, "
                    "'sequence' for sequences, or 'extension' for PostgreSQL extensions"
                ),
            ),
        ] = "table",
        objects_service: ObjectsService = ObjectsServiceProvider,
    ) -> list[dict[str, Any]]:
        """Список объектов в схеме.

        Returns:
            Список объектов. FastMCP преобразует в ответ. При ошибке — исключение наружу.
        """
        return await objects_service.list_objects(schema_name=schema_name, object_type=object_type)

    @provider.tool(
        description=descriptions.get_object_details,
        tags={ToolTag.BASIC},
        annotations={"readOnlyHint": True},
    )
    async def get_object_details(
        schema_name: Annotated[
            str,
            Field(description="Schema name as string value for identifying the database schema containing the object"),
        ],
        object_name: Annotated[
            str,
            Field(
                description=(
                    "Object name as string value for identifying the specific database object "
                    "(table, view, sequence, or extension)"
                )
            ),
        ],
        object_type: Annotated[
            str,
            Field(
                default="table",
                description=(
                    "Object type as string value: 'table' for tables, 'view' for views, "
                    "'sequence' for sequences, or 'extension' for PostgreSQL extensions"
                ),
            ),
        ] = "table",
        objects_service: ObjectsService = ObjectsServiceProvider,
    ) -> dict[str, Any]:
        """Получить детальную информацию об объекте.

        Returns:
            Детали объекта. FastMCP преобразует в ответ. При ошибке — исключение наружу.
        """
        return await objects_service.get_object_details(
            schema_name=schema_name,
            object_name=object_name,
            object_type=object_type,
        )
