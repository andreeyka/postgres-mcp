"""Конфигурация FastMCP."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class FastMCPSettings(BaseSettings):
    """Настройки FastMCP: env ``MCP_FASTMCP_*``, секция ``fastmcp``."""

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="MCP_FASTMCP_", extra="ignore", hide_input_in_errors=True
    )

    server_name: str = Field(default="PostgreSQL MCP", description="Имя MCP-сервера (MCP_FASTMCP_SERVER_NAME)")
    instructions: str = Field(
        default=(
            "PostgreSQL MCP server: schema discovery, SQL execution, EXPLAIN analysis, "
            "index recommendations and database health checks."
        ),
        description="Инструкции о назначении сервера для LLM-клиентов (MCP_FASTMCP_INSTRUCTIONS)",
    )
