"""Конфигурация приложения и настройки (один MCP-сервер = одна база данных)."""

import json
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings

from postgres_fastmcp.config.database import DatabaseConfig
from postgres_fastmcp.config.fastmcp import FastMCPSettings
from postgres_fastmcp.config.server import ServerSettings
from postgres_fastmcp.enums import AccessMode, UserRole


__all__ = ["Settings", "build_settings_from_cli", "get_settings", "settings"]


class Settings(BaseSettings):
    """Настройки приложения (одна база данных на сервер).

    Загружаются из: переменных окружения, .env, config.json, значений по умолчанию.
    Потребители используют вложенную конфигурацию через DI или прямой доступ:
    settings.server, settings.fastmcp, settings.database.

    Примеры переменных окружения: MCP_SERVER_HOST=0.0.0.0, MCP_DATABASE_HOST=localhost, MCP_DATABASE_PORT=5432, ...
    """

    server: ServerSettings = Field(default_factory=ServerSettings)
    fastmcp: FastMCPSettings = Field(default_factory=FastMCPSettings)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig, description="Single database configuration")


def load_json_config(json_path: Path) -> dict[str, Any] | None:
    """Загрузка конфигурации из JSON-файла.

    Args:
        json_path: Путь к JSON-файлу.

    Returns:
        Словарь конфигурации или None если файл не найден.
    """
    if not json_path.exists():
        return None

    try:
        with json_path.open(encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
            return data
    except (json.JSONDecodeError, OSError):
        return None


def get_settings(**overrides: Any) -> Settings:
    """Функция-фабрика для создания экземпляра настроек.

    Загружает конфигурацию в следующем порядке приоритета:
    1. Параметры overrides (наивысший приоритет)
    2. Файл config.json (если существует)
    3. Переменные окружения
    4. Файл .env (если существует)
    5. Значения по умолчанию из класса

    Args:
        **overrides: Параметры для переопределения значений по умолчанию.

    Returns:
        Экземпляр Settings с загруженной конфигурацией.

    Examples:
        >>> settings = get_settings()
        >>> test_settings = get_settings(server={"host": "127.0.0.1", "port": 9000})
    """
    # Try to find config.json in current directory
    json_config = load_json_config(Path("config.json"))

    if overrides:
        if json_config:
            merged_config = {**json_config, **dict(overrides)}
            return Settings(**merged_config)
        return Settings(**overrides)

    if json_config:
        return Settings(**json_config)

    # Otherwise use standard BaseSettings loading (env, .env, defaults)
    return Settings()


def build_settings_from_cli(  # noqa: PLR0913
    *,
    database_uri: str | None = None,
    transport: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
    workers: int = 1,
    access_mode: str | None = None,
    role: str | None = None,
) -> Settings:
    """Формирование Settings из аргументов CLI (единый источник текущих прав/конфигурации).

    Инкапсулирует ветвление: database_uri из CLI разбирается в компоненты (host, port, user, password, name),
    переопределение transport. Используйте returned settings.database как единственный "текущий набор прав".
    """
    if database_uri:
        database_config = DatabaseConfig.from_uri(database_uri)
        overrides_db: dict[str, Any] = {}
        if access_mode is not None:
            overrides_db["access_mode"] = AccessMode(access_mode)
        if role is not None:
            overrides_db["role"] = UserRole(role)
        if overrides_db:
            database_config = database_config.model_copy(update=overrides_db)
        server_overrides: dict[str, Any] = {"host": host, "port": port, "workers": workers}
        if transport is not None:
            server_overrides["transport"] = transport
        return get_settings(database=database_config, server=server_overrides)
    if transport is not None:
        return get_settings(server={"transport": transport})
    return get_settings()


settings = get_settings()
