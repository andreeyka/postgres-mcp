"""Регрессионные тесты схем MCP-тулов, как их видит клиент (tools/list)."""

import inspect
import json
import re
from typing import Annotated, get_args, get_origin

import pytest
from fastmcp.tools import FunctionTool, Tool
from pydantic.fields import FieldInfo

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.server import create_server
from postgres_fastmcp.domains.index_tuning.models import MAX_NUM_INDEX_TUNING_QUERIES
from postgres_fastmcp.shared.enums import AccessMode


# Бюджет на весь tools/list в режиме FULL: размер после изменений плюс 15 %.
# Пересчёт: команда в docs/superpowers/plans/2026-09-26-03-agent-output-budget.md, Task 6.
_TOOLS_LIST_BUDGET_CHARS = 10_590

_ROW_TOOLS = ("execute_sql", "list_objects", "get_object_details", "list_schemas", "get_top_queries")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")


async def _list_tools() -> list[Tool]:
    settings = Settings()
    settings.database = settings.database.model_copy(update={"access_mode": AccessMode.FULL})
    return list(await create_server(settings).list_tools())


def _wire(tool: Tool) -> dict:
    return tool.to_mcp_tool().model_dump(by_alias=True, exclude_none=True)


def _field_description(annotation: object, default: object) -> str | None:
    """Описание из Field(), заданного по умолчанию или в Annotated."""
    if isinstance(default, FieldInfo):
        return default.description
    if get_origin(annotation) is Annotated:
        for meta in get_args(annotation)[1:]:
            if isinstance(meta, FieldInfo) and meta.description:
                return meta.description
    return None


@pytest.fixture
async def tools() -> dict[str, Tool]:
    return {tool.name: tool for tool in await _list_tools()}


async def test_field_descriptions_reach_input_schema(tools: dict[str, Tool]) -> None:
    for tool in tools.values():
        assert isinstance(tool, FunctionTool), tool.name
        properties = tool.parameters.get("properties", {})
        for name, param in inspect.signature(tool.fn).parameters.items():
            expected = _field_description(param.annotation, param.default)
            if expected is not None:
                assert properties[name].get("description") == expected, f"{tool.name}.{name}"


async def test_queries_bounds_in_schema(tools: dict[str, Tool]) -> None:
    queries = tools["analyze_query_indexes"].parameters["properties"]["queries"]
    assert queries["minItems"] == 1
    assert queries["maxItems"] == MAX_NUM_INDEX_TUNING_QUERIES


async def test_health_type_accepts_string_or_list(tools: dict[str, Tool]) -> None:
    health_type = tools["analyze_db_health"].parameters["properties"]["health_type"]
    assert {branch["type"] for branch in health_type["anyOf"]} == {"array", "string"}


@pytest.mark.parametrize(
    ("tool", "param"),
    [
        ("list_objects", "object_type"),
        ("get_object_details", "object_type"),
        ("get_top_queries", "sort_by"),
        *((name, "output") for name in _ROW_TOOLS),
    ],
)
async def test_normalized_params_are_not_enums(tools: dict[str, Tool], tool: str, param: str) -> None:
    """Сервер принимает любой регистр и синонимы, поэтому схема не должна быть строже: без enum."""
    schema = tools[tool].parameters["properties"][param]
    assert schema["type"] == "string"
    assert "enum" not in schema


async def test_top_queries_limit_has_lower_bound_only(tools: dict[str, Tool]) -> None:
    """limit > 100 урезается сервером, поэтому maximum в схеме нет."""
    limit = tools["get_top_queries"].parameters["properties"]["limit"]
    assert limit["minimum"] == 1
    assert "maximum" not in limit


async def test_row_tools_do_not_wrap_output(tools: dict[str, Tool]) -> None:
    for name in _ROW_TOOLS:
        assert tools[name].output_schema is None, name


async def test_everything_the_agent_sees_is_english(tools: dict[str, Tool]) -> None:
    for tool in tools.values():
        assert not _CYRILLIC.search(json.dumps(_wire(tool), ensure_ascii=False)), tool.name
        assert isinstance(tool, FunctionTool)
        assert not _CYRILLIC.search(tool.fn.__doc__ or ""), f"{tool.name} docstring"


async def test_tools_list_fits_budget(tools: dict[str, Tool]) -> None:
    size = sum(len(json.dumps(_wire(tool))) for tool in tools.values())
    assert size <= _TOOLS_LIST_BUDGET_CHARS, size
