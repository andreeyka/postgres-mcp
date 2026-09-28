"""Сводные политики валидации: типы операторов, функции, расширения."""

from postgres_fastmcp.postgres.security._allowed_functions import (
    ALLOWED_FUNCTIONS,
    BASIC_ALLOWED_FUNCTIONS,
    INTROSPECTION_FUNCTIONS,
)
from postgres_fastmcp.postgres.security.statement_policies import ALLOWED_NODE_TYPES


# Единственные расширения, которые сервер сам просит установить (hypothetical indexes и топ запросов).
# Всё остальное (dblink, file_fdw, procedural languages, ...) даёт побочные эффекты за пределами БД.
ALLOWED_EXTENSIONS: frozenset[str] = frozenset({"hypopg", "pg_stat_statements"})

# Параметры, которые basic разрешает читать через SHOW: формат, кодировки, версия, свои лимиты сессии.
# Остальные (в том числе пользовательские app.*, где приложения держат секреты) закрыты, как current_setting.
BASIC_SHOW_PARAMETERS: frozenset[str] = frozenset(
    {
        "server_version",
        "server_version_num",
        "timezone",
        "datestyle",
        "intervalstyle",
        "search_path",
        "client_encoding",
        "server_encoding",
        "standard_conforming_strings",
        "statement_timeout",
        "transaction_isolation",
        "transaction_read_only",
    }
)

# Типы идентификаторов объектов: приведение 'other.t'::regclass сообщает о существовании объекта без прав на него.
REG_TYPES: frozenset[str] = frozenset(
    {
        "regclass",
        "regproc",
        "regprocedure",
        "regoper",
        "regoperator",
        "regtype",
        "regrole",
        "regnamespace",
        "regconfig",
        "regdictionary",
        "regcollation",
    }
)

__all__ = [
    "ALLOWED_EXTENSIONS",
    "ALLOWED_FUNCTIONS",
    "ALLOWED_NODE_TYPES",
    "BASIC_ALLOWED_FUNCTIONS",
    "BASIC_SHOW_PARAMETERS",
    "INTROSPECTION_FUNCTIONS",
    "REG_TYPES",
]
