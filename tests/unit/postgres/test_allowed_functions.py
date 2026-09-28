"""Список разрешённых функций не содержит функций, выполняющих SQL из строки-аргумента."""

from postgres_fastmcp.postgres.security.policies import (
    ALLOWED_FUNCTIONS,
    BASIC_ALLOWED_FUNCTIONS,
    INTROSPECTION_FUNCTIONS,
)


# Функции, которые выполняют SQL, переданный строкой: валидатор эту строку не разбирает,
# поэтому такая функция обходит и проверку схемы, и список разрешённых функций.
SQL_EXECUTING_FUNCTIONS = frozenset(
    {
        "ts_stat",
        "ts_rewrite",
        "query_to_xml",
        "query_to_xmlschema",
        "query_to_xml_and_xmlschema",
        "cursor_to_xml",
        "cursor_to_xmlschema",
        "table_to_xml",
        "table_to_xmlschema",
        "table_to_xml_and_xmlschema",
        "schema_to_xml",
        "schema_to_xmlschema",
        "schema_to_xml_and_xmlschema",
        "database_to_xml",
        "database_to_xmlschema",
        "database_to_xml_and_xmlschema",
        "dblink",
        "dblink_exec",
        "dblink_open",
        "dblink_fetch",
        "dblink_send_query",
    }
)


def test_no_function_runs_sql_from_a_string() -> None:
    assert ALLOWED_FUNCTIONS.isdisjoint(SQL_EXECUTING_FUNCTIONS), sorted(ALLOWED_FUNCTIONS & SQL_EXECUTING_FUNCTIONS)


# Единственные функции pg_* в basic: работают со значениями, не с объектами сервера.
# Новая pg_*-функция в общем списке без решения по basic роняет тест.
BASIC_PG_FUNCTIONS = frozenset(
    {
        "pg_column_size",
        "pg_column_compression",
        "pg_size_pretty",
        "pg_size_bytes",
        "pg_client_encoding",
        "pg_encoding_to_char",
        "pg_char_to_encoding",
        "pg_get_keywords",
        "pg_trigger_depth",
    }
)


def test_basic_pg_functions_are_an_explicit_decision() -> None:
    assert {f for f in BASIC_ALLOWED_FUNCTIONS if f.startswith("pg_")} == BASIC_PG_FUNCTIONS


def test_introspection_functions_exist_in_the_full_list() -> None:
    """Опечатка в списке исключений молча оставила бы функцию в basic."""
    assert INTROSPECTION_FUNCTIONS <= ALLOWED_FUNCTIONS, sorted(INTROSPECTION_FUNCTIONS - ALLOWED_FUNCTIONS)


def test_basic_list_is_full_list_without_introspection() -> None:
    assert BASIC_ALLOWED_FUNCTIONS == ALLOWED_FUNCTIONS - INTROSPECTION_FUNCTIONS
    assert {"current_setting", "pg_get_functiondef", "to_regclass", "pg_input_is_valid"} <= INTROSPECTION_FUNCTIONS
    assert {"current_user", "version", "hypopg_create_index", "lower"} <= BASIC_ALLOWED_FUNCTIONS
