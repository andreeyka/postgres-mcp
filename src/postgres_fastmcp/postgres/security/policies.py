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

# Представления information_schema, которые раскрывают секреты и исходники: опции user mapping (в том числе
# пароли), user mapping, опции серверов и обёрток (хосты, пути), тексты функций, представлений и триггеров.
# Фильтр по правам роли их не прячет — владелец объекта видит своё в любой схеме. Структуру объектов агент
# получает через list_objects/get_object_details.
BASIC_BLOCKED_INFORMATION_SCHEMA_VIEWS: frozenset[str] = frozenset(
    {
        "user_mapping_options",
        "user_mappings",
        "foreign_server_options",
        "foreign_data_wrapper_options",
        "routines",
        "views",
        "triggers",
    }
)

# Опции EXPLAIN, открытые в basic: форма и объём плана. SETTINGS показывает параметры сервера с
# нестандартными значениями; WAL и SERIALIZE имеют смысл только с ANALYZE; незнакомые (будущие) опции
# закрыты по умолчанию. ANALYZE сюда не входит: его разрешает отдельный флаг allow_explain_analyze.
BASIC_EXPLAIN_OPTIONS: frozenset[str] = frozenset(
    {"format", "verbose", "costs", "summary", "timing", "buffers", "generic_plan", "memory"}
)

# Типы, чья функция ввода резолвит имена объектов по каталогу: 'other.t'::regclass сообщает о существовании
# объекта без прав на него, 'secretrole=r/postgres'::aclitem — о существовании роли. Массивы (_regclass,
# _aclitem) валидатор сводит к имени элемента до проверки.
NAME_LOOKUP_TYPES: frozenset[str] = frozenset(
    {
        "aclitem",
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

# Скалярные встроенные типы с префиксом pg_, открытые в basic. Остальные pg_*/_pg_* имена типов
# отклоняются: это строковые типы системных отношений (pg_authid, pg_class — их закрывает R1) и
# внутренние скалярные типы (pg_node_tree, pg_ndistinct, pg_mcv_list, pg_dependencies, pg_brin_*),
# которые агенту не нужны, так что их отказ безвреден.
BASIC_PG_SCALAR_TYPES: frozenset[str] = frozenset({"pg_lsn", "pg_snapshot"})

__all__ = [
    "ALLOWED_EXTENSIONS",
    "ALLOWED_FUNCTIONS",
    "ALLOWED_NODE_TYPES",
    "BASIC_ALLOWED_FUNCTIONS",
    "BASIC_BLOCKED_INFORMATION_SCHEMA_VIEWS",
    "BASIC_EXPLAIN_OPTIONS",
    "BASIC_PG_SCALAR_TYPES",
    "BASIC_SHOW_PARAMETERS",
    "INTROSPECTION_FUNCTIONS",
    "NAME_LOOKUP_TYPES",
]
