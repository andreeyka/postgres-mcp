"""Список разрешённых функций не содержит функций, выполняющих SQL из строки-аргумента."""

from postgres_fastmcp.postgres.security.policies import ALLOWED_FUNCTIONS


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
