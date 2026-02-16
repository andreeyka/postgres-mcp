"""Регистрация MCP-инструментов работы со схемами."""

from typing import Any

from fastmcp.server.providers import LocalProvider

from postgres_fastmcp.di.schema_provider import SchemaServiceProvider
from postgres_fastmcp.enums import ToolTag
from postgres_fastmcp.services.schema.service import SchemaService
from postgres_fastmcp.tools.common import ToolDescriptions


def register_schema_tools(provider: LocalProvider, descriptions: ToolDescriptions) -> None:
    """Зарегистрировать инструменты работы со схемами.

    Args:
        provider: Провайдер для регистрации инструментов.
        descriptions: Описания инструментов.
    """

    @provider.tool(
        description=descriptions.list_schemas,
        tags={ToolTag.FULL},
        annotations={"readOnlyHint": True},
    )
    async def list_schemas(schema_service: SchemaService = SchemaServiceProvider) -> list[dict[str, Any]]:
        """Список всех схем в базе данных.

        Returns:
            Список схем. FastMCP преобразует в ответ. При ошибке — исключение наружу.
        """
        return await schema_service.list_schemas()
