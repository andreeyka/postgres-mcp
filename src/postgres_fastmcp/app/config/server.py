"""Конфигурация сервера."""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from postgres_fastmcp.shared.enums import TransportConfig


class ServerSettings(BaseSettings):
    """Настройки сервера: env ``MCP_SERVER_*``, секция ``server``."""

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="MCP_SERVER_", extra="ignore", hide_input_in_errors=True
    )

    host: str = Field(default="127.0.0.1", description="Хост для привязки сервера")
    port: int = Field(default=8000, description="Порт для привязки сервера")
    transport: TransportConfig = Field(
        default=TransportConfig.HTTP, description="Глобальный тип транспорта: 'http' или 'stdio'"
    )
    endpoint: str = Field(default="/mcp", description="Путь MCP endpoint для HTTP (ведущий '/' добавляется сам)")
    health_endpoint_enabled: bool = Field(
        default=True, description="Включает endpoint проверки состояния /health (авторизация не требуется)"
    )
    response_max_tokens: int = Field(
        default=20000,
        ge=1000,
        description="Предел ответа тула в токенах (MCP_SERVER_RESPONSE_MAX_TOKENS); больший ответ заменяется ошибкой",
    )

    @field_validator("endpoint")
    @classmethod
    def _leading_slash(cls, value: str) -> str:
        """Starlette принимает только путь с ведущим '/': 'mcp' из старых конфигов становится '/mcp'.

        Путь '/health' (в любом написании: 'health', '/health/') занят проверкой состояния — ошибка.
        """
        value = value.strip()
        if value.strip("/") == "health":
            msg = "server.endpoint must not be /health: that path is reserved for the health check"
            raise ValueError(msg)
        return value if value.startswith("/") else f"/{value}"
