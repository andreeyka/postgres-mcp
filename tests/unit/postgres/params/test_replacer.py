# mypy: ignore-errors
"""Unit tests for sql.params.replacer.SqlParamReplacer."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.postgres.params.replacer import REPLACE_PARAMETERS_ERROR, SqlParamReplacer


def _make_executor(execute_result: list | None = None) -> MagicMock:
    """Создаёт мок исполнителя запросов."""
    executor = MagicMock()
    executor.execute = AsyncMock(return_value=execute_result)
    return executor


def _make_template() -> MagicMock:
    """Создаёт мок шаблонизатора запросов."""
    template = MagicMock()
    template.render = MagicMock(side_effect=lambda q, p: q.replace("{}", "%s"))
    return template


class TestSqlParamReplacerInit:
    """Tests for SqlParamReplacer constructor."""

    def test_init_stores_executor_and_template(self) -> None:
        """Constructor stores executor and template; cache is empty."""
        executor = _make_executor()
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        assert replacer._executor is executor
        assert replacer._template is template
        assert replacer._column_stats_cache == {}


class TestSqlParamReplacerReplaceParametersNoParams:
    """Tests for replace_parameters when query has no $N parameters."""

    async def test_no_parameters_returns_query_unchanged(self) -> None:
        """Query without $N is returned unchanged."""
        executor = _make_executor()
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        query = "SELECT 1"
        result = await replacer.replace_parameters(query)
        assert result == query

    async def test_limit_offset_interval_only_removes_all_params(self) -> None:
        """Limit $1, offset $1, interval $1 are replaced so no params remain."""
        executor = _make_executor()
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        query = "SELECT 1 LIMIT $1 OFFSET $2"
        result = await replacer.replace_parameters(query)
        assert "limit 100" in result.lower()
        assert "offset 0" in result.lower()
        assert "$" not in result


class TestSqlParamReplacerReplaceParametersLimitOffsetInterval:
    """Tests for limit/offset/interval substitution."""

    async def test_interval_dollar_replaced(self) -> None:
        """Interval $1 is replaced with interval '2 days'."""
        executor = _make_executor()
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        query = "SELECT 1 WHERE ts > now() - interval $1"
        result = await replacer.replace_parameters(query)
        assert "interval '2 days'" in result.lower()

    async def test_interval_literal_replaced(self) -> None:
        """Interval '1 days' is replaced with interval '2 days' when query has params."""
        executor = _make_executor()
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        query = "SELECT 1 WHERE ts > now() - interval '1 days' AND id = $1"
        result = await replacer.replace_parameters(query)
        assert "interval '2 days'" in result.lower() or "interval '2 day'" in result.lower()


class TestSqlParamReplacerReplaceParametersBetween:
    """Tests for BETWEEN $1 and $2 replacement."""

    @patch("postgres_fastmcp.postgres.params.replacer.get_bound_values")
    async def test_between_uses_stats_when_available(
        self,
        mock_get_bound: MagicMock,
    ) -> None:
        """BETWEEN $1 and $2 uses get_bound_values when stats exist."""
        mock_get_bound.side_effect = [5, 95]
        executor = _make_executor()
        executor.execute = AsyncMock(
            side_effect=[
                [RowResult({"table_name": "t", "column_name": "x"})],
                [
                    RowResult(
                        {
                            "data_type": "int",
                            "common_vals": None,
                            "common_freqs": None,
                            "histogram_bounds": None,
                            "null_frac": None,
                            "n_distinct": None,
                            "correlation": None,
                        }
                    )
                ],
            ]
        )
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        query = "SELECT * FROM t WHERE x BETWEEN $1 AND $2"
        result = await replacer.replace_parameters(query)
        assert "5" in result and "95" in result
        assert "$1" not in result and "$2" not in result

    async def test_between_fallback_without_stats(self) -> None:
        """BETWEEN $1 and $2 uses 10 and 100 when no stats."""
        executor = _make_executor()
        executor.execute = AsyncMock(
            side_effect=[
                [RowResult({"table_name": "t", "column_name": "x"})],
                None,
            ]
        )
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        query = "SELECT * FROM t WHERE x BETWEEN $1 AND $2"
        result = await replacer.replace_parameters(query)
        assert "10" in result and "100" in result

    async def test_between_single_digit_param_does_not_corrupt_two_digit_param(self) -> None:
        """BETWEEN $1 substitution must not also rewrite the $1 inside $15 (data-corruption regression)."""
        executor = _make_executor()  # execute returns None -> default bounds 10/100, no column stats
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        query = "SELECT * FROM t WHERE x BETWEEN $1 AND $2 AND id = $15"
        result = await replacer.replace_parameters(query)
        # Without the (?!\d) guard, $15 becomes "10" + "5" = "105"; with it, $15 is replaced independently.
        assert "105" not in result
        assert "$" not in result

    @patch("postgres_fastmcp.postgres.params.replacer.get_table_aliases")
    @patch("postgres_fastmcp.postgres.params.replacer.extract_columns")
    async def test_between_with_qualified_column_ref_resolves_table_via_alias(
        self,
        mock_extract_columns: MagicMock,
        mock_get_aliases: MagicMock,
    ) -> None:
        """BETWEEN with alias.column (e.g. u.x) resolves table via get_table_aliases."""
        mock_extract_columns.return_value = {"users": {"x"}}
        mock_get_aliases.side_effect = lambda q, tbl: ["users", "u"] if tbl == "users" else [tbl]
        executor = _make_executor()
        executor.execute = AsyncMock(
            side_effect=[
                [RowResult({"table_name": "users", "column_name": "x"})],
                [
                    RowResult(
                        {
                            "data_type": "int",
                            "common_vals": None,
                            "common_freqs": None,
                            "histogram_bounds": None,
                            "null_frac": None,
                            "n_distinct": None,
                            "correlation": None,
                        }
                    )
                ],
            ]
        )
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        query = "SELECT * FROM users u WHERE u.x BETWEEN $1 AND $2"
        result = await replacer.replace_parameters(query)
        assert "$1" not in result and "$2" not in result
        mock_get_aliases.assert_called()


class TestSqlParamReplacerReplaceParametersGeneric:
    """Tests for generic parameter replacement when table_columns empty or no column identified."""

    @patch("postgres_fastmcp.postgres.params.replacer.extract_columns")
    @patch("postgres_fastmcp.postgres.params.replacer.extract_tables_from_query")
    async def test_falls_back_to_generic_when_no_table_columns(
        self,
        mock_extract_tables: MagicMock,
        mock_extract_columns: MagicMock,
    ) -> None:
        """When extract_columns returns empty, _replace_parameters_generic is used."""
        mock_extract_tables.return_value = set()
        mock_extract_columns.return_value = {}
        executor = _make_executor()
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        query = "SELECT * FROM t WHERE name = $1"
        result = await replacer.replace_parameters(query)
        assert "$1" not in result
        assert "sample_value" in result or "45" in result or "'" in result

    @patch("postgres_fastmcp.postgres.params.replacer.extract_columns")
    @patch("postgres_fastmcp.postgres.params.replacer.extract_tables_from_query")
    async def test_identify_column_and_replace_with_stats(
        self,
        mock_extract_tables: MagicMock,
        mock_extract_columns: MagicMock,
    ) -> None:
        """When column is identified and stats returned, replacement uses get_replacement_value."""
        mock_extract_tables.return_value = {"users"}
        mock_extract_columns.return_value = {"users": {"id"}}
        executor = _make_executor()
        executor.execute = AsyncMock(
            side_effect=[
                [RowResult({"table_name": "users", "column_name": "id"})],
                [
                    RowResult(
                        {
                            "data_type": "integer",
                            "common_vals": None,
                            "common_freqs": None,
                            "histogram_bounds": [1, 50, 100],
                            "null_frac": None,
                            "n_distinct": None,
                            "correlation": None,
                        }
                    )
                ],
            ]
        )
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        query = "SELECT * FROM users WHERE id = $1"
        result = await replacer.replace_parameters(query)
        assert "$1" not in result
        # Replacement from stats (histogram_bounds[0]=1, or mid 50, or int fallback 41)
        assert any(c.isdigit() for c in result.split("WHERE")[-1])

    @patch("postgres_fastmcp.postgres.params.replacer.extract_columns")
    @patch("postgres_fastmcp.postgres.params.replacer.extract_tables_from_query")
    async def test_param_in_unrecognized_context_uses_generic_replacement(
        self,
        mock_extract_tables: MagicMock,
        mock_extract_columns: MagicMock,
    ) -> None:
        """When table_columns is set but _identify_parameter_column returns None, use get_generic_replacement."""
        mock_extract_tables.return_value = {"t"}
        mock_extract_columns.return_value = {"t": {"id"}}
        executor = _make_executor()
        executor.execute = AsyncMock(return_value=[RowResult({"table_name": "t", "column_name": "id"})])
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        query = "SELECT * FROM t WHERE func($1) = 1"
        result = await replacer.replace_parameters(query)
        assert "$1" not in result
        assert "sample_value" in result or "43" in result or "44" in result or "'" in result


class TestSqlParamReplacerReplaceParametersRaises:
    """Tests for replace_parameters error handling."""

    @patch("postgres_fastmcp.postgres.params.replacer.extract_tables_from_query")
    async def test_replace_parameters_raises_value_error_on_internal_error(
        self,
        mock_extract_tables: MagicMock,
    ) -> None:
        """replace_parameters raises ValueError with REPLACE_PARAMETERS_ERROR on exception."""
        mock_extract_tables.side_effect = RuntimeError("parse failed")
        executor = _make_executor()
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        query = "SELECT * FROM t WHERE x = $1"
        with pytest.raises(ValueError) as exc_info:
            await replacer.replace_parameters(query)
        assert exc_info.value.args[0] == REPLACE_PARAMETERS_ERROR
        assert isinstance(exc_info.value.__cause__, RuntimeError)


class TestSqlParamReplacerGetColumnStatistics:
    """Tests for _get_column_statistics."""

    async def test_cache_hit_returns_cached(self) -> None:
        """_get_column_statistics returns cached value without calling executor."""
        executor = _make_executor()
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        replacer._column_stats_cache["t.x"] = {"data_type": "int"}
        result = await replacer._get_column_statistics("t", "x")
        assert result == {"data_type": "int"}
        executor.execute.assert_not_called()

    async def test_cache_miss_parses_array_fields(self) -> None:
        """_get_column_statistics parses common_vals/common_freqs/histogram_bounds from pg array."""
        executor = _make_executor()
        executor.execute = AsyncMock(
            return_value=[
                RowResult(
                    {
                        "data_type": "int",
                        "common_vals": "{1,2,3}",
                        "common_freqs": "{0.1,0.2,0.3}",
                        "histogram_bounds": "{10,20,30}",
                        "null_frac": None,
                        "n_distinct": None,
                        "correlation": None,
                    }
                ),
            ]
        )
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        result = await replacer._get_column_statistics("t", "x")
        assert result is not None
        assert result.get("common_vals") == [1, 2, 3]
        assert result.get("common_freqs") == [0.1, 0.2, 0.3]
        assert result.get("histogram_bounds") == [10, 20, 30]

    async def test_cache_miss_empty_result_returns_none(self) -> None:
        """_get_column_statistics returns None when query returns no rows."""
        executor = _make_executor(execute_result=[])
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        result = await replacer._get_column_statistics("t", "x")
        assert result is None
        assert replacer._column_stats_cache["t.x"] is None

    async def test_exception_caches_none_and_returns_none(self) -> None:
        """On exception, _get_column_statistics caches None and returns None."""
        executor = _make_executor()
        executor.execute = AsyncMock(side_effect=RuntimeError("db error"))
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        result = await replacer._get_column_statistics("t", "x")
        assert result is None
        assert replacer._column_stats_cache["t.x"] is None


class TestSqlParamReplacerIdentifyParameterColumn:
    """Tests for _identify_parameter_column."""

    def test_equality_pattern(self) -> None:
        """Identifies column from 'col = $1'."""
        executor = _make_executor()
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        table_columns = {"t": {"id"}}
        ctx = " id = $1"
        assert replacer._identify_parameter_column(ctx, table_columns) == ("t", "id")

    def test_in_pattern(self) -> None:
        """Identifies column from 'col in ($1)'."""
        executor = _make_executor()
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        table_columns = {"t": {"status"}}
        ctx = " status in ($1)"
        assert replacer._identify_parameter_column(ctx, table_columns) == ("t", "status")

    def test_like_pattern(self) -> None:
        """Identifies column from 'col like $1'."""
        executor = _make_executor()
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        table_columns = {"t": {"name"}}
        ctx = " name like $1"
        assert replacer._identify_parameter_column(ctx, table_columns) == ("t", "name")

    def test_greater_than_pattern(self) -> None:
        """Identifies column from 'col > $1'."""
        executor = _make_executor()
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        table_columns = {"t": {"amount"}}
        ctx = " amount > $1"
        assert replacer._identify_parameter_column(ctx, table_columns) == ("t", "amount")

    def test_between_pattern(self) -> None:
        """Identifies column from 'col between $1 and $2'."""
        executor = _make_executor()
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        table_columns = {"t": {"created_at"}}
        ctx = " created_at between $1 and $2"
        assert replacer._identify_parameter_column(ctx, table_columns) == ("t", "created_at")

    def test_returns_none_when_no_match(self) -> None:
        """Returns None when no pattern matches."""
        executor = _make_executor()
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        table_columns = {"t": {"other"}}
        ctx = " unknown = $1"
        assert replacer._identify_parameter_column(ctx, table_columns) is None


class TestSqlParamReplacerReplaceParametersGenericMethod:
    """Tests for _replace_parameters_generic method."""

    def test_generic_replacement_success(self) -> None:
        """_replace_parameters_generic replaces $N with generic values."""
        executor = _make_executor()
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        query = "SELECT * FROM t WHERE name = $1 AND id > $2"
        result = replacer._replace_parameters_generic(query)
        assert "$1" not in result and "$2" not in result

    def test_generic_replacement_exception_returns_original(self) -> None:
        """_replace_parameters_generic returns original query on exception."""
        executor = _make_executor()
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        query = "SELECT * FROM t WHERE x = $1"
        with patch("postgres_fastmcp.postgres.params.replacer.re.sub") as mock_sub:
            mock_sub.side_effect = RuntimeError("re error")
            result = replacer._replace_parameters_generic(query)
        assert result == query


class TestSqlParamReplacerBuildColumnCache:
    """Tests for build_column_cache."""

    async def test_empty_tables_returns_empty(self) -> None:
        """build_column_cache with empty tables returns {}."""
        executor = _make_executor()
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        result = await replacer.build_column_cache(set())
        assert result == {}
        executor.execute.assert_not_called()

    async def test_populates_cache_from_result(self) -> None:
        """build_column_cache fills table -> columns from query result."""
        executor = _make_executor()
        executor.execute = AsyncMock(
            return_value=[
                RowResult({"table_name": "users", "column_name": "id"}),
                RowResult({"table_name": "users", "column_name": "name"}),
                RowResult({"table_name": "orders", "column_name": "id"}),
            ]
        )
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        result = await replacer.build_column_cache({"users", "orders"})
        assert result == {"users": {"id", "name"}, "orders": {"id"}}

    async def test_exception_returns_partial_or_empty(self) -> None:
        """build_column_cache on exception returns empty dict (logs warning)."""
        executor = _make_executor()
        executor.execute = AsyncMock(side_effect=RuntimeError("db error"))
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        result = await replacer.build_column_cache({"t"})
        assert result == {}


class TestSqlParamReplacerExtractDelegation:
    """Tests for extract_columns and extract_stmt_columns delegation."""

    def test_extract_columns_delegates(self) -> None:
        """extract_columns delegates to ast.extraction.extract_columns."""
        executor = _make_executor()
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        with patch("postgres_fastmcp.postgres.params.replacer.extract_columns", return_value={"t": {"a"}}) as mock_ext:
            result = replacer.extract_columns("SELECT a FROM t", column_cache=None)
        mock_ext.assert_called_once_with("SELECT a FROM t", column_cache=None)
        assert result == {"t": {"a"}}

    def test_extract_stmt_columns_delegates(self) -> None:
        """extract_stmt_columns delegates to ast.extraction.extract_stmt_columns."""
        executor = _make_executor()
        template = _make_template()
        replacer = SqlParamReplacer(executor, template)
        stmt = MagicMock()
        with patch(
            "postgres_fastmcp.postgres.params.replacer.extract_stmt_columns",
            return_value={"t": {"a"}},
        ) as mock_ext:
            result = replacer.extract_stmt_columns(stmt, column_cache=None)
        mock_ext.assert_called_once_with(stmt, column_cache=None)
        assert result == {"t": {"a"}}


class TestSqlParamReplacerRunParamQuery:
    """Tests for _run_param_query."""

    async def test_renders_then_executes(self) -> None:
        """_run_param_query renders query with template then executes with executor."""
        executor = _make_executor(execute_result=[RowResult({"x": 1})])
        template = _make_template()
        template.render = MagicMock(return_value="SELECT 1")
        replacer = SqlParamReplacer(executor, template)
        result = await replacer._run_param_query("SELECT {}", [1])
        template.render.assert_called_once_with("SELECT {}", [1])
        executor.execute.assert_called_once()
        assert result == [RowResult({"x": 1})]
