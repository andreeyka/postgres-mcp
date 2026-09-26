"""Тесты рендера результата тулов: Markdown-таблица и JSON."""

import datetime as dt
import json
from decimal import Decimal

from mcp.types import TextContent

from postgres_fastmcp.tools.rendering import rows_result, sections_result


def _text(result: object) -> str:
    content = result.content  # type: ignore[attr-defined]
    assert len(content) == 1
    assert isinstance(content[0], TextContent)
    return content[0].text


def test_table_lists_columns_once_and_counts_rows() -> None:
    result = rows_result([{"id": 1, "name": "a"}, {"id": 2, "name": "b"}], "table")
    assert _text(result) == "| id | name |\n| --- | --- |\n| 1 | a |\n| 2 | b |\n\n2 rows."
    assert result.structured_content is None


def test_table_escapes_pipe_and_flattens_newlines() -> None:
    text = _text(rows_result([{"v": "a|b\nc\r\nd"}], "table"))
    assert "| a\\|b c d |" in text


def test_table_renders_none_as_empty_cell() -> None:
    text = _text(rows_result([{"a": None, "b": 1}], "table"))
    assert "|  | 1 |" in text


def test_table_uses_union_of_keys_in_first_seen_order() -> None:
    text = _text(rows_result([{"a": 1}, {"b": 2}], "table"))
    assert text.splitlines()[0] == "| a | b |"
    assert "| 1 |  |" in text
    assert "|  | 2 |" in text


def test_empty_rows_render_without_table() -> None:
    assert _text(rows_result([], "table")) == "0 rows."


def test_title_goes_above_the_table() -> None:
    text = _text(rows_result([], "table", title="Statement executed."))
    assert text == "Statement executed.\n\n0 rows."


def test_json_has_rows_and_row_count_without_markdown() -> None:
    rows = [{"id": 1, "amount": Decimal("1.50"), "at": dt.date(2026, 9, 26), "note": None}]
    result = rows_result(rows, "json")
    text = _text(result)
    assert "|" not in text
    expected = {"rows": [{"id": 1, "amount": "1.50", "at": "2026-09-26", "note": None}], "row_count": 1}
    assert json.loads(text) == expected
    assert result.structured_content == expected


def test_json_empty_rows() -> None:
    result = rows_result([], "json", title="ignored in json")
    assert result.structured_content == {"rows": [], "row_count": 0}
    assert json.loads(_text(result)) == {"rows": [], "row_count": 0}


def test_json_keeps_non_ascii_readable() -> None:
    assert "имя" in _text(rows_result([{"name": "имя"}], "json"))


def test_sections_table_has_header_lines_and_one_table_per_non_empty_section() -> None:
    result = sections_result(
        {
            "columns": [{"column": "id", "data_type": "integer"}],
            "constraints": [{"name": "pk", "type": "PRIMARY KEY", "columns": ["id", "tenant"]}],
            "indexes": [],
        },
        "table",
        header={"schema": "public", "name": "users", "type": "table"},
    )
    text = _text(result)
    assert text.startswith("schema: public\nname: users\ntype: table\n\n### columns\n| column | data_type |")
    assert "### constraints" in text
    assert "| pk | PRIMARY KEY | id, tenant |" in text
    assert "### indexes" not in text
    assert result.structured_content is None


def test_sections_json_merges_header_and_sections() -> None:
    result = sections_result(
        {"columns": [{"column": "id"}], "indexes": []},
        "json",
        header={"schema": "public", "name": "users"},
    )
    expected = {"schema": "public", "name": "users", "columns": [{"column": "id"}], "indexes": []}
    assert result.structured_content == expected
    assert json.loads(_text(result)) == expected


def test_sections_without_header_and_rows_is_zero_rows() -> None:
    assert _text(sections_result({"columns": []}, "table")) == "0 rows."
