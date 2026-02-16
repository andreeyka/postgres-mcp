"""Конфигурация FastMCP."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class FastMCPSettings(BaseSettings):
    """Настройки FastMCP."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="MCP_", extra="ignore")

    server_name: str = Field(default="postgres-fastmcp", description="Имя MCP-сервера (MCP_SERVER_NAME)")
    instructions: str = Field(
        default=(
            "MCP-сервер для PostgreSQL: обнаружение схемы, выполнение запросов, "
            "анализ EXPLAIN, рекомендации по индексам и проверка состояния базы данных."
        ),
        description="Инструкции, описывающие назначение сервера для LLM-клиентов (MCP_INSTRUCTIONS)",
    )
    return_errors_as_strings: bool = Field(
        default=True,
        description="Возвращает ошибки как строки в ответах LLM вместо стандартных ошибок MCP",
    )
    error_traceback_in_strings: bool = Field(
        default=False, description="Включает traceback в строках ошибок при return_errors_as_strings=True"
    )
