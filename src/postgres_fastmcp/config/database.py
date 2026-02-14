"""Модели конфигурации для базы данных (одна БД на сервер)."""

from pydantic import BaseModel, Field, SecretStr

from postgres_fastmcp.enums import AccessMode, ToolName, UserRole


# Convenience constants for tool tagging (basic vs admin)
AVAILABLE_TOOLS: list[ToolName] = ToolName.available_tools()
BASIC_TOOLS: list[ToolName] = ToolName.basic_tools()
ADMIN_TOOLS: list[ToolName] = ToolName.admin_tools()


class DatabaseConfig(BaseModel):
    """Конфигурация одной базы данных (одна БД на MCP-сервер)."""

    database_uri: SecretStr = Field(description="URL подключения к базе данных")
    extra_kwargs: dict[str, str] = Field(default_factory=dict, description="Дополнительные именованные аргументы")
    access_mode: AccessMode = Field(
        default=AccessMode.RESTRICTED,
        description=(
            "Уровень доступа к SQL. "
            "Доступные режимы: 'restricted' (только чтение, SELECT), "
            "'unrestricted' (чтение-запись, DML: INSERT/UPDATE/DELETE или полный доступ с DDL для полной роли)."
        ),
    )
    role: UserRole = Field(
        default=UserRole.USER,
        description=(
            "Роль пользователя, определяющая доступ к схемам и доступные инструменты. "
            "Доступные роли: 'user' (только схема public, базовые инструменты - 4 инструмента), "
            "'full' (все схемы, все инструменты - 9 инструментов, расширенные привилегии)."
        ),
    )
    # Параметры пула подключений
    pool_min_size: int = Field(default=1, description="Минимальное количество соединений в пуле")
    pool_max_size: int = Field(default=5, description="Максимальное количество соединений в пуле")
    safe_sql_timeout: int = Field(
        default=30,
        description=(
            "Таймаут в секундах для SafeSqlDriver. "
            "Используется для всех режимов кроме 'full' роли с 'unrestricted' access_mode."
        ),
    )
    table_prefix: str | None = Field(
        default=None,
        description=(
            "Необязательный префикс имен таблиц для роли 'user'. "
            "Если задан, доступны только таблицы/представления/последовательности с именами, начинающимися с этого префикса. "
            "Работает только для роли 'user'. Игнорируется для роли 'full'."
        ),
    )
    query_tag: str | None = Field(
        default=None,
        description="Необязательный тег для SafeSqlDriver (например, имя сервера). Устанавливается при сборке MCP, если не предоставлен.",
    )
