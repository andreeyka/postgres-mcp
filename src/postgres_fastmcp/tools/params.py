"""Общие типы параметров MCP-тулов: принимают свободный ввод агента и приводят его к каноническому виду.

Правило: JSON-схема описывает всё, что сервер принимает (иначе клиент отклонит
допустимый ввод ещё до вызова), а валидаторы приводят принятое к каноническому
значению. Непоправимый ввод отклоняется UserFacingError с подсказкой.

Алиасы — обычные присваивания, не PEP 695 ``type X = ...``: у такого алиаса
docstring функции перебивает описание Field. Схему расширяем через
``Field(json_schema_extra=callable)``, а не ``WithJsonSchema``: WithJsonSchema
заменяет схему целиком и теряет ``Field(description=...)``.
"""

from typing import Annotated, Any, get_args

from pydantic import AfterValidator, BeforeValidator, Field

from postgres_fastmcp.domains.health.database_health import HealthType
from postgres_fastmcp.domains.index_tuning.models import MAX_NUM_INDEX_TUNING_QUERIES
from postgres_fastmcp.shared.enums import ObjectType, TopQueriesSortBy
from postgres_fastmcp.shared.errors import (
    InvalidHealthTypeError,
    InvalidOutputFormatError,
    InvalidSortCriteriaError,
    UnsupportedObjectTypeError,
)
from postgres_fastmcp.tools.rendering import OutputFormat


TOP_QUERIES_MAX_LIMIT = 100
HEALTH_TYPE_VALUES = ", ".join(sorted(ht.value for ht in HealthType))

_OBJECT_TYPES: tuple[str, ...] = get_args(ObjectType)
_SORT_BY: tuple[str, ...] = get_args(TopQueriesSortBy)
_SORT_BY_SYNONYMS = {"total": "total_time", "mean": "mean_time", "avg": "mean_time", "resource": "resources"}
_HEALTH_TYPES: tuple[str, ...] = tuple(ht.value for ht in HealthType)
_OUTPUT_FORMATS: tuple[str, ...] = get_args(OutputFormat)
_INDEX_QUERIES_EXAMPLE = "Example: ['SELECT * FROM orders WHERE customer_id = 42']."


def _any_string(schema: dict[str, Any]) -> None:
    """Схема «строка»: регистр и синонимы нормализует сервер, поэтому enum клиенту не отдаём."""
    schema.pop("enum", None)
    schema.pop("const", None)
    schema["type"] = "string"


def _string_or_list(schema: dict[str, Any]) -> None:
    """Схема «массив строк или строка через запятую» вместо массива enum-значений."""
    for key in ("type", "items", "minItems", "maxItems"):
        schema.pop(key, None)
    schema["anyOf"] = [{"type": "array", "items": {"type": "string"}, "minItems": 1}, {"type": "string"}]


def _key(value: object) -> object:
    """Строку привести к ключу сравнения: без пробелов по краям, нижний регистр, '-'/' ' -> '_'."""
    if not isinstance(value, str):
        return value
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _normalize_object_type(value: object) -> object:
    """'Tables', 'VIEW', 'sequences' -> каноническое значение ObjectType."""
    key = _key(value)
    if not isinstance(key, str):
        return value
    if key in _OBJECT_TYPES:
        return key
    if key.endswith("s") and key[:-1] in _OBJECT_TYPES:
        return key[:-1]
    raise UnsupportedObjectTypeError(str(value))


def _normalize_sort_by(value: object) -> object:
    """'Total', 'avg', 'resource' -> каноническое значение TopQueriesSortBy."""
    key = _key(value)
    if not isinstance(key, str):
        return value
    key = _SORT_BY_SYNONYMS.get(key, key)
    if key in _SORT_BY:
        return key
    raise InvalidSortCriteriaError(str(value))


def _normalize_health_types(value: object) -> object:
    """Список или CSV-строку привести к списку HealthType; 'all' поглощает остальные значения."""
    items = value.split(",") if isinstance(value, str) else value
    if not isinstance(items, list | tuple):
        return value
    keys = list(dict.fromkeys(key for item in items if isinstance(key := _key(item), str) and key))
    if not keys:
        raise InvalidHealthTypeError(str(value), _HEALTH_TYPES)
    for key in keys:
        if key not in _HEALTH_TYPES:
            raise InvalidHealthTypeError(key, _HEALTH_TYPES)
    if HealthType.ALL.value in keys:
        return [HealthType.ALL.value]
    return keys


def _normalize_output(value: object) -> object:
    """'JSON', ' Table ' -> 'json', 'table'."""
    key = _key(value)
    if not isinstance(key, str):
        return value
    if key in _OUTPUT_FORMATS:
        return key
    raise InvalidOutputFormatError(str(value))


def _cap_top_queries_limit(value: int) -> int:
    """Больше TOP_QUERIES_MAX_LIMIT строк агенту не отдаём: лишнее урезается, а не отклоняется."""
    return min(value, TOP_QUERIES_MAX_LIMIT)


ObjectTypeParam = Annotated[
    ObjectType,
    BeforeValidator(_normalize_object_type),
    Field(
        description=(
            "Object kind: 'table', 'view', 'sequence' or 'extension'. "
            "Any case and plural forms are accepted, e.g. 'Tables'."
        ),
        json_schema_extra=_any_string,
    ),
]
HealthTypesParam = Annotated[
    tuple[HealthType, ...],
    BeforeValidator(_normalize_health_types),
    Field(
        description=(
            f"Health checks to run: {HEALTH_TYPE_VALUES}. A list or a comma-separated string, any case; "
            "'all' runs every check. Example: ['index', 'vacuum'] or 'index,vacuum'."
        ),
        json_schema_extra=_string_or_list,
    ),
]
TopQueriesSortByParam = Annotated[
    TopQueriesSortBy,
    BeforeValidator(_normalize_sort_by),
    Field(
        description=(
            "Ranking criteria: 'resources' (share of time, buffers and WAL), 'total_time' or 'mean_time'. "
            "Synonyms 'total', 'mean', 'avg', 'resource' and any case are accepted. Example: 'mean_time'."
        ),
        json_schema_extra=_any_string,
    ),
]
TopQueriesLimitParam = Annotated[
    int,
    Field(
        ge=1,
        description=(
            f"Number of queries to return, 1-{TOP_QUERIES_MAX_LIMIT}; "
            f"larger values are capped at {TOP_QUERIES_MAX_LIMIT}. Example: 10."
        ),
    ),
    AfterValidator(_cap_top_queries_limit),
]
IndexQueriesParam = Annotated[
    list[str],
    Field(
        min_length=1,
        max_length=MAX_NUM_INDEX_TUNING_QUERIES,
        description=f"SQL queries to analyze, 1-{MAX_NUM_INDEX_TUNING_QUERIES} items. {_INDEX_QUERIES_EXAMPLE}",
    ),
]
OutputParam = Annotated[
    OutputFormat,
    BeforeValidator(_normalize_output),
    Field(
        description=(
            "'table' (default): Markdown table for reading. "
            "'json': {'rows': [...], 'row_count': N} as structured content, for code that processes the result. "
            "Any case is accepted."
        ),
        json_schema_extra=_any_string,
    ),
]
