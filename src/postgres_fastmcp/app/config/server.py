"""Конфигурация сервера."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from postgres_fastmcp.shared.enums import TransportConfig


class ServerSettings(BaseSettings):
    """Настройки сервера."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="MCP_", extra="ignore")

    host: str = Field(default="127.0.0.1", description="Хост для привязки сервера")
    port: int = Field(default=8000, description="Порт для привязки сервера")
    transport: TransportConfig = Field(
        default=TransportConfig.HTTP, description="Глобальный тип транспорта: 'http' или 'stdio'"
    )
    endpoint: str = Field(default="mcp", description="Путь endpoint по умолчанию")
    workers: int = Field(default=1, description="Количество запускаемых рабочих процессов")
    health_endpoint_enabled: bool = Field(
        default=True, description="Включает endpoint проверки состояния /health (авторизация не требуется)"
    )
    response_max_tokens: int = Field(
        default=20000,
        ge=1000,
        description="Предел ответа тула в токенах (MCP_RESPONSE_MAX_TOKENS); больший ответ заменяется ошибкой",
    )
