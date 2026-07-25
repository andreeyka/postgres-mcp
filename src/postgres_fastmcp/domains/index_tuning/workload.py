"""Источники и подготовка нагрузки запросов для настройки индексов."""

import logging
from pathlib import Path
from typing import Any

from pglast import parse_sql
from pglast.ast import Node, SelectStmt

from postgres_fastmcp.postgres.ast.visitors import TableAliasVisitor
from postgres_fastmcp.postgres.params.replacer import SqlParamReplacer
from postgres_fastmcp.postgres.ports import SqlDriverPort


logger = logging.getLogger(__name__)


def load_workload_from_file(file_path: str) -> list[dict[str, Any]]:
    """Загрузить запросы из SQL файла.

    Args:
        file_path: Путь к SQL файлу.

    Returns:
        Список словарей нагрузки.

    Raises:
        ValueError: Если файл не может быть прочитан.
    """
    try:
        with Path(file_path).open() as f:
            content = f.read()

        # Split the file content by semicolons to get individual queries
        query_texts = [q.strip() for q in content.split(";") if q.strip()]
        queries = []

        for i, text in enumerate(query_texts):
            queries.append(
                {
                    "queryid": i,
                    "query": text,
                }
            )

    except Exception as e:
        error_msg = f"Error loading queries from file {file_path}"
        raise ValueError(error_msg) from e
    else:
        return queries


async def get_query_stats(
    sql_driver: SqlDriverPort, min_calls: int = 50, min_avg_time_ms: float = 5.0, limit: int = 100
) -> list[dict[str, Any]]:
    """Получить статистику запросов из pg_stat_statements.

    Args:
        sql_driver: SQL исполнитель для доступа к базе данных.
        min_calls: Минимальное количество вызовов.
        min_avg_time_ms: Минимальное среднее время выполнения в миллисекундах.
        limit: Максимальное количество возвращаемых запросов.

    Returns:
        Список словарей статистики запросов.
    """
    query = """
    SELECT queryid, query, calls, total_exec_time/calls as avg_exec_time
    FROM pg_stat_statements
    WHERE calls >= {}
    AND total_exec_time/calls >= {}
    ORDER BY total_exec_time DESC
    LIMIT {}
    """
    result = await sql_driver.execute(
        query,
        params=[min_calls, min_avg_time_ms, limit],
        readonly=True,
    )
    return [dict(row.cells) for row in result] if result else []


async def validate_and_parse_workload(
    workload: list[dict[str, Any]], param_replacer: SqlParamReplacer
) -> list[dict[str, Any]]:
    """Валидировать нагрузку для обеспечения ее аналитичности.

    Args:
        workload: Список словарей нагрузки с ключом "query".
        param_replacer: Заменитель параметров для подстановки фиктивных значений.

    Returns:
        Список словарей нагрузки с разобранными операторами в ключе "stmt".
    """
    validated_workload = []
    for q in workload:
        query_text = q["query"]
        if not query_text:
            logger.debug("Skipping empty query")
            continue
        query_text = query_text.strip().lower()

        # Replace parameter placeholders with dummy values
        query_text = await param_replacer.replace_parameters(query_text)

        parsed = parse_sql(query_text)
        if not parsed:
            logger.debug("Skipping non-parseable query: %s...", query_text[:50])
            continue
        stmt = parsed[0].stmt
        if not is_analyzable_stmt(stmt):
            logger.debug("Skipping non-analyzable query: %s...", query_text[:50])
            continue

        q["query"] = query_text
        q["stmt"] = stmt
        validated_workload.append(q)
    return validated_workload


def workload_to_query_weights(workload: list[dict[str, Any]]) -> list[tuple[str, SelectStmt, float]]:
    """Преобразовать нагрузку в веса запросов на основе частоты запросов.

    Args:
        workload: Список словарей нагрузки с ключами "query" и "stmt".

    Returns:
        Список кортежей с текстом запроса, разобранным оператором и весом.
    """
    return [(q["query"], q["stmt"], query_info_to_weight(q)) for q in workload]


def query_info_to_weight(query_info: dict[str, Any]) -> float:
    """Преобразовать информацию о запросе в вес на основе частоты запросов.

    Args:
        query_info: Словарь с информацией о запросе (calls, avg_exec_time).

    Returns:
        Вес запроса как число с плавающей точкой.
    """
    calls_value: Any = query_info.get("calls", 1.0)
    avg_exec_time_value: Any = query_info.get("avg_exec_time", 1.0)
    calls = float(calls_value) if calls_value is not None else 1.0
    avg_exec_time = float(avg_exec_time_value) if avg_exec_time_value is not None else 1.0
    return calls * avg_exec_time


def is_analyzable_stmt(stmt: Node) -> bool:
    """Проверить, может ли оператор быть проанализирован для рекомендаций индексов.

    Args:
        stmt: Разобранный узел AST оператора.

    Returns:
        True если оператор может быть проанализирован, False иначе.
    """
    # It should be a SelectStmt
    if not isinstance(stmt, SelectStmt):
        return False

    visitor = TableAliasVisitor()
    visitor(stmt)

    # Skip queries that only access system tables
    return not all(table.startswith(("pg_", "aurora_")) for table in visitor.tables)
