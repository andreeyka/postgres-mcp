"""Конфигурация базы данных: DatabaseConfig для кода библиотеки, DatabaseSettings для env/.env."""

from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from postgres_fastmcp.shared.enums import AccessMode, SslMode


ERROR_DATABASE_URI_NOT_SET = (
    "Database connection URL is not set. "
    "Set MCP_DATABASE_HOST, MCP_DATABASE_PORT, MCP_DATABASE_USER, MCP_DATABASE_PASSWORD, MCP_DATABASE_NAME "
    "or pass --database-uri, or ensure config.json 'database' section has full connection params."
)

# Параметры libpq, у которых есть свои поля DatabaseConfig: в connect_options они дали бы второе значение.
_CONNECT_OPTION_FIELDS: dict[str, str] = {
    "host": "host",
    "hostaddr": "host",
    "port": "port",
    "dbname": "name",
    "user": "user",
    "password": "password",
    "sslmode": "sslmode",
    "client_encoding": "client_encoding",
}

# Секрет или путь к секретам: URI подключения попадает в логи и тексты ошибок.
_SECRET_CONNECT_OPTIONS = frozenset({"sslpassword", "passfile"})

# Параметры query string URI со своими полями: в connect_options не попадают.
_URI_FIELD_PARAMETERS = frozenset({"sslmode", "client_encoding"})


class DatabaseConfig(BaseModel):
    """Конфигурация одной базы данных (одна БД на MCP-сервер) для кода библиотеки.

    Берёт только переданные значения: env и .env не читаются, поэтому окружение хоста не может
    молча поднять потолок прав (access_mode, write_mode). Неизвестное поле — ошибка.
    URI формируется из компонентов: host, port, user, password, name; параметры libpq — из connect_options.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

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
        default=10,
        description="Максимальное количество соединений в пуле",
        ge=1,
    )
    max_inactive_connection_lifetime: int = Field(
        default=300,
        description="Через сколько секунд простоя пул закрывает соединение сверх pool_min_size (max_idle пула)",
        ge=1,
    )

    connect_options: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Параметры libpq в query string URI подключения (target_session_attrs, options, connect_timeout, "
            "sslrootcert, application_name и т.д.). Ключи со своими полями (host, hostaddr, port, dbname, user, "
            "password, sslmode, client_encoding) и секреты (sslpassword, passfile) запрещены; неизвестный "
            "параметр отклонит libpq при подключении."
        ),
    )
    write_mode: bool = Field(
        default=False,
        description=(
            "Если True — разрешены DML/DDL (INSERT, UPDATE, DELETE, CREATE и т.д.) при access_mode=full. "
            "Если False — только чтение (SELECT). По умолчанию False."
        ),
    )
    access_mode: AccessMode = Field(
        default=AccessMode.BASIC,
        description=(
            "Уровень доступа: 'basic' (только схема public, 4 инструмента), 'full' (все схемы, 9 инструментов)."
        ),
    )
    safe_sql_timeout: int = Field(
        default=30,
        ge=1,
        description=(
            "Таймаут в секундах для SafeSqlDriver. "
            "Используется для всех режимов кроме access_mode=full с write_mode=True."
        ),
    )
    table_prefix: str | None = Field(
        default=None,
        description=(
            "Необязательный префикс имён таблиц для access_mode=basic. "
            "Если задан, доступны только таблицы/представления/последовательности с именами, "
            "начинающимися с этого префикса. Игнорируется для access_mode=full."
        ),
    )
    query_tag: str | None = Field(
        default=None,
        description=(
            "Необязательный тег для SafeSqlDriver (например, имя сервера). "
            "Устанавливается при сборке MCP, если не предоставлен."
        ),
    )

    @staticmethod
    def uri_fields(uri: str) -> dict[str, Any]:
        """Поля подключения из URI (postgresql:// или postgresql+asyncpg://).

        Возвращает только то, что в URI есть: host, port (5432, если не указан), user, password,
        name, а из query string — sslmode, client_encoding и остальные параметры libpq
        (connect_options; повтор ключа — последнее значение). Отсутствующее (например пароль) не
        попадает в словарь, и его дополняют config.json или env. Остальные поля URI не задаёт.
        """
        parsed = urlparse(uri)
        qs = parse_qs(parsed.query)
        candidates: dict[str, Any] = {
            "host": parsed.hostname,
            "port": parsed.port or 5432,
            "user": unquote(parsed.username) if parsed.username else None,
            "password": SecretStr(unquote(parsed.password)) if parsed.password else None,
            "name": unquote((parsed.path or "").lstrip("/")) or None,
        }
        fields = {key: value for key, value in candidates.items() if value is not None}
        # sslmode и client_encoding — только если они есть в URI: иначе у DatabaseSettings
        # явное значение перебило бы MCP_DATABASE_SSLMODE / MCP_DATABASE_CLIENT_ENCODING
        # Неизвестный sslmode — ошибка: иначе libpq молча откатился бы на prefer. Значение не секрет.
        raw_sslmode = qs.get("sslmode", [None])[0]
        if raw_sslmode is not None:
            allowed = [mode.value for mode in SslMode]
            if raw_sslmode not in allowed:
                msg = f"Unknown sslmode {raw_sslmode!r} in database URI; expected one of: {', '.join(allowed)}"
                raise ValueError(msg)
            fields["sslmode"] = SslMode(raw_sslmode)
        client_encoding = qs.get("client_encoding", [None])[0]
        if client_encoding:
            fields["client_encoding"] = client_encoding
        # Остальные параметры query string — параметры libpq (target_session_attrs, options, connect_timeout, ...).
        # Ключ connect_options — только если они есть: иначе URI стёр бы connect_options из config.json.
        connect_options = {key: values[-1] for key, values in qs.items() if key not in _URI_FIELD_PARAMETERS}
        if connect_options:
            fields["connect_options"] = connect_options
        return fields

    @classmethod
    def from_uri(cls, uri: str, **overrides: Any) -> "DatabaseConfig":
        """Собирает конфиг из URI (postgresql:// или postgresql+asyncpg://).

        Args:
            uri: Строка подключения.
            **overrides: Переопределения полей (write_mode, access_mode, host и т.д.); перекрывают поля URI
                и проверяются Pydantic при создании.

        Returns:
            Экземпляр DatabaseConfig с заполненными host, port, user, password, name.
        """
        return cls(**{**cls.uri_fields(uri), **overrides})

    @field_validator("connect_options")
    @classmethod
    def _check_connect_options(cls, value: dict[str, str]) -> dict[str, str]:
        """Параметры libpq без дублей полей конфига и без секретов; остальное проверит libpq при подключении."""
        for key in value:
            if key in _CONNECT_OPTION_FIELDS:
                msg = f"connect_options cannot set {key!r}: use the database field {_CONNECT_OPTION_FIELDS[key]!r}"
                raise ValueError(msg)
            if key in _SECRET_CONNECT_OPTIONS:
                msg = (
                    f"connect_options cannot set {key!r}: the connection URI reaches logs and error messages, "
                    "so it must not carry secrets or paths to them"
                )
                raise ValueError(msg)
        return value

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
        """Параметры query string URI подключения: connect_options, client_encoding и sslmode."""
        params: dict[str, str] = {**self.connect_options, "client_encoding": self.client_encoding}
        if self.sslmode is not None:
            params["sslmode"] = self.sslmode
        return params

    @property
    def database_uri(self) -> str | None:
        """URI подключения к базе данных (connect_options, sslmode и client_encoding в query string).

        Returns:
            URI подключения (postgresql://) или None если БД не настроена.
        """
        if self.user is None or self.password is None or self.host is None or self.port is None or self.name is None:
            return None
        # Percent-encoding, а не quote_plus: libpq не декодирует '+' как пробел
        user = quote(self.user, safe="")
        password = quote(self.password.get_secret_value(), safe="")
        # IPv6-адрес без скобок libpq прочитал бы как host:port
        host = f"[{self.host}]" if ":" in self.host and not self.host.startswith("[") else self.host
        name = quote(self.name, safe="")
        # quote, а не quote_plus: libpq не декодирует '+' как пробел (options=-c statement_timeout=5000)
        query = urlencode(self._connection_query_params(), quote_via=quote)
        return f"postgresql://{user}:{password}@{host}:{self.port}/{name}?{query}"

    @model_validator(mode="after")
    def _check_database_uri(self) -> "DatabaseConfig":
        """Проверяет, что заданы все поля для подключения (host, port, user, password, name)."""
        if self.user is None or self.password is None or self.host is None or self.port is None or self.name is None:
            raise ValueError(ERROR_DATABASE_URI_NOT_SET)
        return self


class DatabaseSettings(DatabaseConfig, BaseSettings):
    """DatabaseConfig с чтением env и .env: префикс MCP_DATABASE_ (без вложенного delimiter).

    Блок ``database`` в Settings (CLI, config.json): недостающие в словаре поля берутся из
    MCP_DATABASE_HOST, MCP_DATABASE_PORT, MCP_DATABASE_USER и т.д. Чужие ключи .env игнорируются.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_prefix="MCP_DATABASE_",
        hide_input_in_errors=True,
    )
