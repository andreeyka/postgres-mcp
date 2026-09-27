"""Конфигурация приложения и настройки (один MCP-сервер = одна база данных).

Формат config.json (в текущей директории):

{
  "server": {
    "host": "127.0.0.1",
    "port": 8000,
    "transport": "http",
    "endpoint": "mcp",
    "workers": 1,
    "health_endpoint_enabled": true,
    "response_max_tokens": 20000
  },
  "fastmcp": {
    "server_name": "PostgreSQL MCP",
    "instructions": "...",
    "return_errors_as_strings": true,
    "error_traceback_in_strings": false
  },
  "database": {
    "host": "localhost",
    "port": 5432,
    "user": "user",
    "password": "secret",
    "name": "mydb",
    "write_mode": false,
    "access_mode": "basic",
    "sslmode": "prefer",
    "table_prefix": null,
    "query_tag": null
  },
  "auth": {
    "mode": "static",
    "required_scopes": [],
    "access_policy": {"enforced": true, "claim": "scope", "write_values": ["pg:write"], "full_values": ["pg:full"]},
    "tokens": {"<token>": {"client_id": "alice", "scopes": ["pg:write"], "claims": {}}}
  }
}

transport: "http" | "stdio". access_mode: "basic" | "full". auth.mode: "none" | "static" | "jwt" | "oidc"
(поля режимов jwt/oidc — в app/config/auth.py).
sslmode: "disable" | "allow" | "prefer" | "require" | "verify-ca" | "verify-full".
Все поля опциональны; недостающие берутся из env/.env или значений по умолчанию.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from postgres_fastmcp.app.config.auth import AuthSettings
from postgres_fastmcp.app.config.database import DatabaseConfig, DatabaseSettings
from postgres_fastmcp.app.config.fastmcp import FastMCPSettings
from postgres_fastmcp.app.config.server import ServerSettings
from postgres_fastmcp.shared.enums import AccessMode


__all__ = ["Settings", "build_settings_from_cli", "load_json_config"]


class Settings(BaseSettings):
    """Настройки приложения (одна база данных на сервер).

    Загружаются из: переменных окружения, .env, config.json, значений по умолчанию.
    Потребители используют вложенную конфигурацию через DI или прямой доступ:
    settings.server, settings.fastmcp, settings.database, settings.auth.

    Примеры переменных окружения: MCP_HOST=0.0.0.0, MCP_DATABASE_HOST=localhost, MCP_AUTH_MODE=static, ...
    """

    # Прикрывает только собственные ошибки Settings; ошибка model_validator внутри вложенного
    # блока (DatabaseConfig, ServerSettings, FastMCPSettings, AuthSettings) несёт input_value
    # этого блока и печатает его целиком, если у блока нет своего hide_input_in_errors=True
    model_config = SettingsConfigDict(hide_input_in_errors=True)

    server: ServerSettings = Field(default_factory=ServerSettings)
    fastmcp: FastMCPSettings = Field(default_factory=FastMCPSettings)
    database: DatabaseConfig = Field(default_factory=DatabaseSettings, description="Single database configuration")
    auth: AuthSettings = Field(default_factory=AuthSettings, description="HTTP authentication and access policy")

    @field_validator("database", mode="before")
    @classmethod
    def _database_from_env(cls, value: object) -> object:
        """Словарь (config.json, CLI) дополняется из env через DatabaseSettings; готовый DatabaseConfig — как есть.

        Неизвестный ключ словаря — ошибка: DatabaseSettings игнорирует чужие ключи (нужно для .env),
        и опечатка вроде table_prefx молча сняла бы ограничение. В тексте ошибки только имена ключей.
        """
        if isinstance(value, dict):
            unknown = sorted(str(key) for key in value if key not in DatabaseConfig.model_fields)
            if unknown:
                msg = f"Unknown database settings keys: {', '.join(unknown)}"
                raise ValueError(msg)
            return DatabaseSettings(**value)
        return value

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],  # noqa: ARG003
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Только аргументы конструктора: env и .env читает каждый вложенный блок со своим префиксом.

        Без этого Settings читает переменные без префикса с именами полей (AUTH, DATABASE, SERVER,
        FASTMCP) как целый блок: AUTH=basic роняет старт, DATABASE='{"host": ...}' подменяет подключение.
        """
        return (init_settings,)


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


def _merge_sections(json_config: dict[str, Any], overrides: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Наложить overrides CLI на секции json-конфига поле за полем, а не секцию целиком."""
    merged = dict(json_config)
    for section, values in overrides.items():
        if not values:
            continue
        base = json_config.get(section)
        merged[section] = {**base, **values} if isinstance(base, dict) else values
    return merged


def build_settings_from_cli(  # noqa: PLR0913
    *,
    database_uri: str | None = None,
    transport: str | None = None,
    host: str | None = None,
    port: int | None = None,
    write_mode: bool | None = None,
    access_mode: AccessMode | None = None,
    config_path: Path | None = None,
) -> Settings:
    """Формирование Settings из аргументов CLI (единый источник текущих прав/конфигурации).

    Приоритет: явно заданный флаг > config.json > env/.env > значения по умолчанию. None означает
    «флаг не задан» и ничего не переопределяет. Флаги накладываются на секции config.json поле
    за полем; database_uri задаёт только поля подключения (host, port, user, password, name,
    sslmode и client_encoding, если они есть в URI), они перекрывают те же поля config.json,
    а остальная секция database из config.json сохраняется. JSON-конфиг подхватывается только при
    использовании как CLI: по умолчанию — `./config.json` из CWD; для библиотечного использования
    стройте Settings напрямую или передавайте явный путь.
    """
    json_config = load_json_config(config_path if config_path is not None else Path("config.json")) or {}
    server = {
        key: value for key, value in {"transport": transport, "host": host, "port": port}.items() if value is not None
    }
    database: dict[str, Any] = DatabaseConfig.uri_fields(database_uri) if database_uri else {}
    database.update(
        {
            key: value
            for key, value in {"access_mode": access_mode, "write_mode": write_mode}.items()
            if value is not None
        }
    )
    return Settings(**_merge_sections(json_config, {"server": server, "database": database}))
