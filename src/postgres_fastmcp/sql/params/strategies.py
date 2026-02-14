"""Стратегии замены параметров $N конкретными значениями из статистики или контекста."""

import re
from typing import Any


MIN_HISTOGRAM_BOUNDS = 3


def parse_pg_array_value(value: str) -> Any:  # noqa: ANN401
    """Разбор одного значения из PostgreSQL массива (например, из pg_stats)."""
    value = value.strip()
    if value == "null":
        return None
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def get_bound_values(stats: dict[str, Any], *, is_lower: bool) -> Any:  # noqa: ANN401
    """Возвращает граничное значение для BETWEEN из статистики столбца (нижнее или верхнее)."""
    data_type = (stats.get("data_type") or "").lower()
    common_vals = stats.get("common_vals")
    common_freqs = stats.get("common_freqs")
    if common_vals and common_freqs and len(common_vals) == len(common_freqs) and len(common_vals) > 0:
        common_vals_list = list(common_vals)
        common_freqs_list = list(common_freqs)
        max_freq_idx = common_freqs_list.index(max(common_freqs_list))
        most_common = common_vals_list[max_freq_idx]
        try:
            if isinstance(most_common, float):
                adj = abs(most_common) * 0.05 if most_common != 0 else 1
                return most_common - adj if is_lower else most_common + adj
            if isinstance(most_common, int):
                adj = abs(most_common) * 0.05 if most_common != 0 else 1
                return int(most_common - adj) if is_lower else int(most_common + adj)
            if isinstance(most_common, str) and most_common.isdigit():
                num_val = float(most_common)
                adj = abs(num_val) * 0.05 if num_val != 0 else 1
                return str(int(num_val - adj)) if is_lower else str(int(num_val + adj))
        except (TypeError, ValueError):
            pass
        return most_common

    histogram_bounds = stats.get("histogram_bounds")
    if histogram_bounds and len(histogram_bounds) >= MIN_HISTOGRAM_BOUNDS:
        median_idx = len(histogram_bounds) // 2
        idx_offset = max(1, len(histogram_bounds) // 10)
        if is_lower:
            bound_idx = max(0, median_idx - idx_offset)
        else:
            bound_idx = min(len(histogram_bounds) - 1, median_idx + idx_offset)
        return histogram_bounds[bound_idx]

    most_common = (stats.get("most_common_vals") or [None])[0]
    if most_common is not None:
        return most_common

    if "int" in data_type or data_type in ["smallint", "integer", "bigint"]:
        return 10 if is_lower else 20
    if data_type in ["numeric", "decimal", "real", "double precision", "float"]:
        return 10.0 if is_lower else 20.0
    if "date" in data_type or "time" in data_type:
        return "'2023-01-01'" if is_lower else "'2023-01-31'"
    if data_type == "boolean":
        return "true"
    return "'m'" if is_lower else "'n'"


def get_replacement_value(stats: dict[str, Any], context: str) -> str:
    """Возвращает строку замены из статистики столбца и контекста запроса."""
    data_type = (stats.get("data_type") or "").lower()
    common_vals = stats.get("common_vals")
    histogram_bounds = stats.get("histogram_bounds")
    is_equality = "=" in context and "!=" not in context and "<>" not in context
    is_range = any(op in context for op in [">", "<", ">=", "<=", "between"])
    is_like = "like" in context

    if "char" in data_type or data_type == "text":
        if is_like:
            return "'%test%'"
        if common_vals:
            sample = common_vals[0]
            return f"'{sample}'"
        return "'sample_value'"

    if "int" in data_type or data_type in ["numeric", "decimal", "real", "double"]:
        if histogram_bounds and is_range and isinstance(histogram_bounds, list) and len(histogram_bounds) > 1:
            return str(histogram_bounds[len(histogram_bounds) // 2])
        if common_vals and is_equality:
            return str(common_vals[0])
        if histogram_bounds and isinstance(histogram_bounds, list) and len(histogram_bounds) > 0:
            return str(histogram_bounds[0])
        return "41" if "int" in data_type else "41.5"

    if "date" in data_type or "time" in data_type:
        return "'2023-01-15'" if is_range else "'2023-01-01'"
    if data_type == "boolean":
        return "true"
    return "'sample_value'"


def get_generic_replacement(context: str) -> str:
    """Возвращает общую замену когда тип столбца неизвестен."""
    ctx = context.lower()
    if any(w in ctx.split() for w in ["date", "timestamp", "time"]):
        return "'2023-01-01'"
    if any(w in ctx for w in ["id", "key", "code", "num"]):
        return "43"
    if "like" in ctx:
        return "'%sample%'"
    if any(w in ctx for w in ["amount", "price", "cost", "fee"]):
        return "99.99"
    if any(op in ctx for op in ["=", ">", "<", ">=", "<="]):
        return "44"
    return "'sample_value'"


def context_replace(match: re.Match[str], op: str) -> str:
    """Замена одного $N на основе контекста имени столбца."""
    col_name = match.group(1).lower()
    if col_name.endswith(("id", "_id")) or col_name == "id":
        return f"{col_name} {op} 46"
    if any(w in col_name for w in ["date", "time", "created", "updated"]):
        return f"{col_name} {op} '2023-01-01'"
    if any(w in col_name for w in ["amount", "price", "cost", "count", "num", "qty"]):
        return f"{col_name} {op} 46.5"
    if "status" in col_name or "type" in col_name or "state" in col_name:
        return f"{col_name} {op} 'active'"
    return f"{col_name} {op} 'sample_value'"
