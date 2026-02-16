"""Модели конфигурации для базы данных (одна БД на сервер)."""

from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlencode, urlparse

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from postgres_fastmcp.enums import AccessMode, SslMode, UserRole


ERROR_DATABASE_URI_NOT_SET = (
    "Database connection URL is not set. "
    "Set MCP_DATABASE_HOST, MCP_DATABASE_PORT, MCP_DATABASE_USER, MCP_DATABASE_PASSWORD, MCP_DATABASE_NAME "
    "or pass --database-uri, or ensure config.json 'database' section has full connection params."
)


class DatabaseConfig(BaseSettings):
    """Конфигурация одной базы данных (одна БД на MCP-сервер).

    Загружается из env с префиксом MCP_DATABASE_ (без вложенного delimiter):
    MCP_DATABASE_HOST, MCP_DATABASE_PORT, MCP_DATABASE_USER и т.д.
    URI формируется из компонентов: host, port, user, password, name.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_prefix="MCP_DATABASE_",
    )

    host: str | None = Field(default=None, description="Хост базы данных")
    port: int = Field(default=5432, description="Порт базы данных")
    user: str | None = Field(default=None, description="Пользователь базы данных")
    password: SecretStr | None = Field(default=None, description="Пароль базы данных")
    name: str | None = Field(default=None, description="Имя базы данных")

    sslmode: SslMode | None = Field(
        default=None,
        description=(
            "Режим SSL для подключения: disable, allow, prefer, require, verify-ca, verify-full. "
            "Не добавляется в URI, если не задан."
        ),
    )
    client_encoding: str = Field(
        default="UTF8",
        description="Кодировка клиента в query string URI (например UTF8).",
    )

    pool_min_size: int = Field(
        default=1,
        description="Минимальное количество соединений в пуле",
        ge=1,
    )
    pool_max_size: int = Field(
        default=5,
        description="Максимальное количество соединений в пуле",
        ge=1,
    )
    max_inactive_connection_lifetime: int = Field(
        default=300,
        description="Максимальное время жизни неактивного соединения в секундах",
        ge=1,
    )

    extra_kwargs: dict[str, str] = Field(
        default_factory=dict,
        description="Дополнительные именованные аргументы",
    )
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
            "'admin' (все схемы, все инструменты - 9 инструментов, расширенные привилегии)."
        ),
    )
    safe_sql_timeout: int = Field(
        default=30,
        description=(
            "Таймаут в секундах для SafeSqlDriver. "
            "Используется для всех режимов кроме роли 'admin' с 'unrestricted' access_mode."
        ),
    )
    table_prefix: str | None = Field(
        default=None,
        description=(
            "Необязательный префикс имен таблиц для роли 'user'. "
            "Если задан, доступны только "
            "таблицы/представления/последовательности с именами, "
            "начинающимися с этого префикса. "
            "Работает только для роли 'user'. Игнорируется для роли 'admin'."
        ),
    )
    query_tag: str | None = Field(
        default=None,
        description=(
            "Необязательный тег для SafeSqlDriver (например, имя сервера). "
            "Устанавливается при сборке MCP, если не предоставлен."
        ),
    )

    @classmethod
    def from_uri(cls, uri: str, **overrides: Any) -> "DatabaseConfig":
        """Собирает конфиг из URI (postgresql:// или postgresql+asyncpg://).

        Args:
            uri: Строка подключения.
            **overrides: Переопределения полей (access_mode, role и т.д.); проверяются Pydantic при создании.

        Returns:
            Экземпляр DatabaseConfig с заполненными host, port, user, password, name.
        """
        parsed = urlparse(uri)
        db_name = (parsed.path or "").lstrip("/") or None
        qs = parse_qs(parsed.query)
        raw_sslmode = qs.get("sslmode", [None])[0] if qs else None
        try:
            sslmode = SslMode(raw_sslmode) if raw_sslmode else None
        except (ValueError, TypeError):
            sslmode = None
        client_encoding = (qs.get("client_encoding", ["UTF8"])[0] or "UTF8") if qs else "UTF8"
        return cls(
            host=parsed.hostname,
            port=parsed.port or 5432,
            user=unquote(parsed.username) if parsed.username else None,
            password=SecretStr(unquote(parsed.password)) if parsed.password else None,
            name=db_name,
            sslmode=sslmode,
            client_encoding=client_encoding,
            **overrides,
        )

    @property
    def is_configured(self) -> bool:
        """Проверяет, настроена ли база данных.

        Returns:
            True если все обязательные поля заполнены, иначе False.
        """
        return (
            self.host is not None
            and self.port is not None
            and self.user is not None
            and self.password is not None
            and self.name is not None
        )

    def _connection_query_params(self) -> dict[str, str]:
        """Параметры query string для URI подключения (sslmode, client_encoding)."""
        params: dict[str, str] = {"client_encoding": self.client_encoding}
        if self.sslmode is not None:
            params["sslmode"] = self.sslmode
        return params

    @property
    def database_uri(self) -> str | None:
        """URI подключения к базе данных (с sslmode и client_encoding из настроек).

        Returns:
            URI подключения (postgresql://) или None если БД не настроена.
        """
        if self.user is None or self.password is None or self.host is None or self.port is None or self.name is None:
            return None
        user = quote_plus(self.user)
        password = quote_plus(self.password.get_secret_value())
        query = urlencode(self._connection_query_params())
        return f"postgresql://{user}:{password}@{self.host}:{self.port}/{self.name}?{query}"

    @model_validator(mode="after")
    def _check_database_uri(self) -> "DatabaseConfig":
        """Проверяет, что заданы все поля для подключения (host, port, user, password, name)."""
        if self.user is None or self.password is None or self.host is None or self.port is None or self.name is None:
            raise ValueError(ERROR_DATABASE_URI_NOT_SET)
        return self
