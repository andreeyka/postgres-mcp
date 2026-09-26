"""Тесты общих типов параметров тулов: нормализация ввода агента и отказ с подсказкой."""

from typing import Any

import pytest
from fastmcp import Client, FastMCP
from pydantic import TypeAdapter, ValidationError

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.domains.health.database_health import HealthType
from postgres_fastmcp.domains.index_tuning.models import MAX_NUM_INDEX_TUNING_QUERIES
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.shared.errors import (
    InvalidHealthTypeError,
    InvalidOutputFormatError,
    InvalidSortCriteriaError,
    UnsupportedObjectTypeError,
)
from postgres_fastmcp.tools.params import (
    TOP_QUERIES_MAX_LIMIT,
    HealthTypesParam,
    IndexQueriesParam,
    ObjectTypeParam,
    OutputParam,
    TopQueriesLimitParam,
    TopQueriesSortByParam,
)
from postgres_fastmcp.tools.registry import register_tools


def _validate(param: Any, value: object) -> object:
    return TypeAdapter(param).validate_python(value)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("table", "table"),
        ("Tables", "table"),
        ("TABLE", "table"),
        ("views", "view"),
        ("sequence", "sequence"),
        ("Sequences", "sequence"),
        ("extension", "extension"),
        (" extensions ", "extension"),
    ],
)
def test_object_type_normalized(raw: str, expected: str) -> None:
    assert _validate(ObjectTypeParam, raw) == expected


def test_object_type_rejected_with_hint() -> None:
    with pytest.raises(UnsupportedObjectTypeError, match=r"Did you mean 'table'\?.*'view'"):
        _validate(ObjectTypeParam, "tabel")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("all", (HealthType.ALL,)),
        ("INDEX", (HealthType.INDEX,)),
        ("index, Vacuum", (HealthType.INDEX, HealthType.VACUUM)),
        (["buffer", "BUFFER", "constraint"], (HealthType.BUFFER, HealthType.CONSTRAINT)),
        ("index,all", (HealthType.ALL,)),
        (["connection", ""], (HealthType.CONNECTION,)),
    ],
)
def test_health_types_normalized(raw: object, expected: tuple[HealthType, ...]) -> None:
    assert _validate(HealthTypesParam, raw) == expected


@pytest.mark.parametrize(("raw", "bad"), [("indx", "indx"), (["index", "vacum"], "vacum")])
def test_health_type_rejected_with_hint(raw: object, bad: str) -> None:
    with pytest.raises(InvalidHealthTypeError, match=rf"'{bad}'.*Did you mean") as exc_info:
        _validate(HealthTypesParam, raw)
    assert "'replication'" in str(exc_info.value)


@pytest.mark.parametrize("raw", ["", " , ", []])
def test_empty_health_type_rejected(raw: object) -> None:
    with pytest.raises(InvalidHealthTypeError):
        _validate(HealthTypesParam, raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("resources", "resources"),
        ("resource", "resources"),
        ("total_time", "total_time"),
        ("total", "total_time"),
        ("Mean_Time", "mean_time"),
        ("mean", "mean_time"),
        ("avg", "mean_time"),
        ("mean-time", "mean_time"),
    ],
)
def test_sort_by_normalized(raw: str, expected: str) -> None:
    assert _validate(TopQueriesSortByParam, raw) == expected


def test_sort_by_rejected_with_hint() -> None:
    with pytest.raises(InvalidSortCriteriaError, match=r"Did you mean 'total_time'\?"):
        _validate(TopQueriesSortByParam, "totl_time")


@pytest.mark.parametrize(("raw", "expected"), [(1, 1), (10, 10), (100, 100), (500, TOP_QUERIES_MAX_LIMIT)])
def test_top_queries_limit_capped(raw: int, expected: int) -> None:
    assert _validate(TopQueriesLimitParam, raw) == expected


def test_top_queries_limit_below_one_rejected() -> None:
    with pytest.raises(ValidationError):
        _validate(TopQueriesLimitParam, 0)


def test_index_queries_bounds() -> None:
    assert _validate(IndexQueriesParam, ["SELECT 1"]) == ["SELECT 1"]
    with pytest.raises(ValidationError):
        _validate(IndexQueriesParam, [])
    with pytest.raises(ValidationError):
        _validate(IndexQueriesParam, ["SELECT 1"] * (MAX_NUM_INDEX_TUNING_QUERIES + 1))


@pytest.mark.parametrize(("raw", "expected"), [("table", "table"), ("JSON", "json"), (" Table ", "table")])
def test_output_normalized(raw: str, expected: str) -> None:
    assert _validate(OutputParam, raw) == expected


def test_output_rejected_with_hint() -> None:
    with pytest.raises(InvalidOutputFormatError, match=r"Did you mean 'json'\?"):
        _validate(OutputParam, "jsn")


@pytest.mark.parametrize(
    ("tool", "arguments", "hint"),
    [
        ("list_objects", {"schema_name": "public", "object_type": "tabel"}, "Did you mean 'table'?"),
        ("analyze_db_health", {"health_type": "indx"}, "Did you mean 'index'?"),
        ("get_top_queries", {"sort_by": "totl_time"}, "Did you mean 'total_time'?"),
    ],
)
async def test_rejected_input_reaches_client_with_hint(tool: str, arguments: dict[str, Any], hint: str) -> None:
    """Ошибка нормализации проходит mask_error_details: агент видит текст с подсказкой, а не 'Error calling tool'."""
    mcp = FastMCP(name="t", mask_error_details=True)
    settings = Settings()
    settings.database = settings.database.model_copy(update={"access_mode": AccessMode.FULL})
    register_tools(mcp, settings)
    async with Client(mcp) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error is True
    assert hint in result.content[0].text
