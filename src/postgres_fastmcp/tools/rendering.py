"""Рендер строк результата тула: Markdown-таблица (output='table') или JSON (output='json').

Модуль не зависит от регистрации тулов: функции получают готовые строки и
возвращают ToolResult, поэтому переживают перенос тулов в ToolSet/PostgresProvider.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from fastmcp.tools import ToolResult
from mcp.types import TextContent


OutputFormat = Literal["table", "json"]


def rows_result(rows: list[dict[str, Any]], output: OutputFormat) -> ToolResult:
    """Результат тула из списка строк.

    Args:
        rows: Строки результата (одинаковые или разные наборы ключей).
        output: 'table' — Markdown-таблица, 'json' — {"rows": [...], "row_count": N}.

    Returns:
        ToolResult с Markdown-текстом или с JSON-текстом и structured_content.
    """
    if output == "json":
        return _json_result({"rows": rows, "row_count": len(rows)})
    return _text_result(f"{_table(rows)}\n\n{len(rows)} rows." if rows else "0 rows.")


def statement_result(status: str | None, affected_rows: int | None, output: OutputFormat) -> ToolResult:
    """Результат оператора без результирующего набора (DML без RETURNING, DDL): тег команды Postgres.

    Args:
        status: Тег команды, например "UPDATE 3" или "CREATE TABLE".
        affected_rows: Число строк из тега; None, если в теге нет числа.
        output: 'table' — одна строка "UPDATE 3: 3 rows affected." или "CREATE TABLE: done.";
            'json' — {"rows": [], "row_count": 0, "status": ..., "affected_rows": ...}.

    Returns:
        ToolResult с текстом или с JSON-текстом и structured_content.
    """
    if output == "json":
        return _json_result({"rows": [], "row_count": 0, "status": status, "affected_rows": affected_rows})
    outcome = "done." if affected_rows is None else f"{affected_rows} rows affected."
    return _text_result(f"{status or 'Statement executed'}: {outcome}")


def sections_result(
    sections: Mapping[str, list[dict[str, Any]]],
    output: OutputFormat,
    *,
    header: Mapping[str, Any] | None = None,
) -> ToolResult:
    """Результат тула из нескольких таблиц (разделов) и скалярного заголовка.

    Args:
        sections: Имя раздела -> строки раздела.
        output: 'table' — строки 'key: value' и по таблице на непустой раздел; 'json' — один словарь.
        header: Скалярные поля объекта (schema, name, type, ...).

    Returns:
        ToolResult с Markdown-текстом или с JSON-текстом и structured_content.
    """
    head = dict(header or {})
    if output == "json":
        return _json_result({**head, **{name: list(rows) for name, rows in sections.items()}})
    blocks = ["\n".join(f"{key}: {_cell(value)}" for key, value in head.items())] if head else []
    blocks.extend(f"### {name}\n{_table(rows)}" for name, rows in sections.items() if rows)
    return _text_result("\n\n".join(blocks) if blocks else "0 rows.")


def _table(rows: Sequence[Mapping[str, Any]]) -> str:
    """GFM-таблица: колонки один раз в шапке, в порядке первого появления ключа."""
    columns = list(dict.fromkeys(key for row in rows for key in row))
    lines = [
        _line([_cell(column) for column in columns]),
        _line(["---"] * len(columns)),
        *(_line([_cell(row.get(column)) for column in columns]) for row in rows),
    ]
    return "\n".join(lines)


def _line(cells: Sequence[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _cell(value: object) -> str:
    """Значение ячейки: None — пусто, список — через запятую, '|' экранирован, переводы строк — пробел."""
    if value is None:
        return ""
    if isinstance(value, list | tuple):
        text = ", ".join(_cell(item) for item in value)
    elif isinstance(value, dict):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    return text.replace("|", "\\|").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def _text_result(text: str) -> ToolResult:
    return ToolResult(content=[TextContent(type="text", text=text)])


def _json_result(data: dict[str, Any]) -> ToolResult:
    """JSON-текст и structured_content из одной сериализации: Decimal, datetime и т.п. совпадают в обоих."""
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return ToolResult(content=[TextContent(type="text", text=payload)], structured_content=json.loads(payload))
