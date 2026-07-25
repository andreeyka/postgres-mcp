# mypy: ignore-errors
"""Unit tests for LLMOptimizerTool and related models (Index, ScoredIndexes, etc.)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from postgres_fastmcp.domains.index_tuning.index_opt_base import IndexRecommendation
from postgres_fastmcp.domains.index_tuning.llm_opt import Index, LLMOptimizerTool, ScoredIndexes
from postgres_fastmcp.postgres.models import IndexDefinition
from postgres_fastmcp.postgres.models import RowResult


class TestIndexModel:
    """Tests for Index Pydantic model."""

    def test_to_index_recommendation(self) -> None:
        idx = Index(table_name="users", columns=("id", "name"))
        rec = idx.to_index_recommendation()
        assert isinstance(rec, IndexRecommendation)
        assert rec.index_definition.table == "users"
        assert rec.index_definition.columns == ("id", "name")

    def test_to_index_definition(self) -> None:
        idx = Index(table_name="orders", columns=("user_id",))
        defn = idx.to_index_definition()
        assert defn.table == "orders"
        assert defn.columns == ("user_id",)

    def test_eq(self) -> None:
        a = Index(table_name="t", columns=("c1",))
        b = Index(table_name="t", columns=("c1",))
        c = Index(table_name="t", columns=("c2",))
        assert a == b
        assert a != c
        assert a != "not an index"

    def test_hash(self) -> None:
        idx = Index(table_name="t", columns=("a", "b"))
        s = {idx}
        assert idx in s


class TestScoredIndexes:
    """Tests for ScoredIndexes dataclass."""

    def test_fields(self) -> None:
        idx = Index(table_name="t", columns=("x",))
        sc = ScoredIndexes(
            indexes={idx},
            execution_cost=100.0,
            index_size=200.0,
            objective_score=5.0,
        )
        assert sc.execution_cost == 100.0
        assert sc.index_size == 200.0
        assert len(sc.indexes) == 1


class TestLLMOptimizerToolScore:
    """Tests for LLMOptimizerTool.score."""

    def test_score_formula(self) -> None:
        mock_driver = AsyncMock()
        mock_ctx = MagicMock()
        tool = LLMOptimizerTool(mock_driver, mock_ctx, pareto_alpha=2.0)
        import math

        expected = math.log(100) + 2.0 * math.log(50)
        assert tool.score(100.0, 50.0) == pytest.approx(expected)


class TestLLMOptimizerToolParseIndexAlternatives:
    """Tests for _parse_index_alternatives_from_json."""

    @pytest.fixture
    def tool(self) -> LLMOptimizerTool:
        return LLMOptimizerTool(AsyncMock(), MagicMock())

    def test_valid_json_list_of_alternatives(self, tool: LLMOptimizerTool) -> None:
        json_text = '{"alternatives": [[{"table_name": "users", "columns": ["id"]}]]}'
        result = tool._parse_index_alternatives_from_json(json_text)
        assert len(result) == 1
        assert len(result[0]) == 1
        idx = next(iter(result[0]))
        assert idx.table_name == "users"
        assert idx.columns == ("id",)

    def test_json_in_markdown_code_block(self, tool: LLMOptimizerTool) -> None:
        json_text = '```json\n{"alternatives": [[{"table_name": "t", "columns": ["a"]}]]}\n```'
        result = tool._parse_index_alternatives_from_json(json_text)
        assert len(result) == 1
        assert next(iter(result[0])).table_name == "t"

    def test_json_in_generic_code_block(self, tool: LLMOptimizerTool) -> None:
        """Generic ``` block (no 'json' label) is stripped before parsing."""
        json_text = '```\n{"alternatives": [[{"table_name": "x", "columns": ["y"]}]]}\n```'
        result = tool._parse_index_alternatives_from_json(json_text)
        assert len(result) == 1
        idx = next(iter(result[0]))
        assert idx.table_name == "x"
        assert idx.columns == ("y",)

    def test_invalid_json_raises(self, tool: LLMOptimizerTool) -> None:
        with pytest.raises(ValueError, match="Invalid JSON"):
            tool._parse_index_alternatives_from_json("not json {")

    def test_invalid_structure_raises(self, tool: LLMOptimizerTool) -> None:
        with pytest.raises(ValueError, match="Invalid IndexingAlternative"):
            tool._parse_index_alternatives_from_json('{"wrong": "structure"}')

    def test_empty_alternatives_filtered(self, tool: LLMOptimizerTool) -> None:
        json_text = '{"alternatives": [[{"table_name": "t", "columns": ["a"]}], []]}'
        result = tool._parse_index_alternatives_from_json(json_text)
        assert len(result) == 1

    def test_alternatives_with_multiple_indexes_in_set(self, tool: LLMOptimizerTool) -> None:
        """Multiple indexes in one alternative form a single set."""
        json_text = (
            '{"alternatives": [[{"table_name": "t1", "columns": ["a"]}, {"table_name": "t2", "columns": ["b", "c"]}]]}'
        )
        result = tool._parse_index_alternatives_from_json(json_text)
        assert len(result) == 1
        assert len(result[0]) == 2
        tables = {idx.table_name for idx in result[0]}
        assert tables == {"t1", "t2"}


class TestLLMOptimizerToolExtractIndexesFromPlan:
    """Tests for _extract_indexes_from_explain_plan (sync, no DB)."""

    @pytest.fixture
    def tool(self) -> LLMOptimizerTool:
        return LLMOptimizerTool(AsyncMock(), MagicMock())

    def test_empty_plan_returns_empty(self, tool: LLMOptimizerTool) -> None:
        assert tool._extract_indexes_from_explain_plan({}) == set()

    def test_no_plan_key_returns_empty(self, tool: LLMOptimizerTool) -> None:
        assert tool._extract_indexes_from_explain_plan({"Other": 1}) == set()

    def test_index_scan_extracted(self, tool: LLMOptimizerTool) -> None:
        plan = {
            "Plan": {
                "Node Type": "Index Scan",
                "Index Name": "users_pkey",
                "Relation Name": "users",
                "Plans": [],
            }
        }
        result = tool._extract_indexes_from_explain_plan(plan)
        assert result == {("users", "users_pkey")}

    def test_nested_plans_traversed(self, tool: LLMOptimizerTool) -> None:
        plan = {
            "Plan": {
                "Node Type": "Seq Scan",
                "Plans": [
                    {
                        "Node Type": "Index Only Scan",
                        "Index Name": "ix_a",
                        "Relation Name": "a",
                        "Plans": [],
                    }
                ],
            }
        }
        result = tool._extract_indexes_from_explain_plan(plan)
        assert result == {("a", "ix_a")}

    def test_bitmap_index_scan_extracted(self, tool: LLMOptimizerTool) -> None:
        plan = {
            "Plan": {
                "Node Type": "Bitmap Index Scan",
                "Index Name": "idx_b",
                "Relation Name": "b",
                "Plans": [],
            }
        }
        result = tool._extract_indexes_from_explain_plan(plan)
        assert result == {("b", "idx_b")}

    def test_plan_none_returns_empty(self, tool: LLMOptimizerTool) -> None:
        assert tool._extract_indexes_from_explain_plan({"Plan": None}) == set()

    def test_node_without_index_fields_skipped(self, tool: LLMOptimizerTool) -> None:
        """Seq Scan and nodes without Index Name / Relation Name do not add to result."""
        plan = {
            "Plan": {
                "Node Type": "Seq Scan",
                "Relation Name": "t",
                "Plans": [],
            }
        }
        assert tool._extract_indexes_from_explain_plan(plan) == set()


class TestLLMOptimizerToolExtractIndexesWithColumns:
    """Tests for _extract_indexes_from_explain_plan_with_columns."""

    @pytest.mark.asyncio
    async def test_returns_indexes_with_columns_from_mocked_get_columns(self) -> None:
        mock_driver = AsyncMock()
        tool = LLMOptimizerTool(mock_driver, MagicMock())
        plan = {
            "Plan": {
                "Node Type": "Index Scan",
                "Index Name": "ix_a",
                "Relation Name": "t",
                "Plans": [],
            }
        }
        with patch.object(tool, "_get_index_columns", new_callable=AsyncMock, return_value=("col1", "col2")):
            result = await tool._extract_indexes_from_explain_plan_with_columns(plan)
        assert len(result) == 1
        idx = next(iter(result))
        assert idx.table_name == "t"
        assert idx.columns == ("col1", "col2")

    @pytest.mark.asyncio
    async def test_empty_plan_returns_empty_set(self) -> None:
        tool = LLMOptimizerTool(AsyncMock(), MagicMock())
        result = await tool._extract_indexes_from_explain_plan_with_columns({})
        assert result == set()


class TestLLMOptimizerToolGetIndexColumns:
    """Tests for _get_index_columns."""

    @pytest.mark.asyncio
    async def test_returns_columns_from_result_rows(self) -> None:
        mock_driver = AsyncMock()
        mock_driver.execute = AsyncMock(
            return_value=[
                RowResult(cells={"attname": "a"}),
                RowResult(cells={"attname": "b"}),
            ]
        )
        tool = LLMOptimizerTool(mock_driver, MagicMock())
        result = await tool._get_index_columns("ix_test")
        assert result == ("a", "b")

    @pytest.mark.asyncio
    async def test_empty_result_returns_empty_tuple(self) -> None:
        mock_driver = AsyncMock()
        mock_driver.execute = AsyncMock(return_value=[])
        tool = LLMOptimizerTool(mock_driver, MagicMock())
        result = await tool._get_index_columns("ix_empty")
        assert result == ()

    @pytest.mark.asyncio
    async def test_exception_returns_empty_tuple(self) -> None:
        mock_driver = AsyncMock()
        mock_driver.execute = AsyncMock(side_effect=ValueError("db error"))
        tool = LLMOptimizerTool(mock_driver, MagicMock())
        result = await tool._get_index_columns("ix_fail")
        assert result == ()


class TestLLMOptimizerToolGenerateRecommendations:
    """Tests for _generate_recommendations."""

    @pytest.mark.asyncio
    async def test_multiple_queries_raises(self) -> None:
        from pglast import parse_sql
        from pglast.ast import RawStmt

        parsed = parse_sql("SELECT 1")
        stmt = parsed[0].stmt if isinstance(parsed[0], RawStmt) else parsed[0]
        query_weights = [
            ("SELECT 1", stmt, 1.0),
            ("SELECT 2", stmt, 1.0),
        ]
        tool = LLMOptimizerTool(AsyncMock(), MagicMock())
        with pytest.raises(ValueError, match="only one query at a time"):
            await tool._generate_recommendations(query_weights)

    @pytest.mark.asyncio
    async def test_no_alternatives_from_llm_returns_original_config(self) -> None:
        """When LLM returns empty alternatives, loop breaks and returns original config."""
        from pglast import parse_sql
        from pglast.ast import RawStmt

        parsed = parse_sql("SELECT 1 FROM t")
        stmt = parsed[0].stmt if isinstance(parsed[0], RawStmt) else parsed[0]
        query_weights = [("SELECT 1 FROM t", stmt, 1.0)]

        mock_driver = AsyncMock()
        mock_ctx = MagicMock()
        tool = LLMOptimizerTool(mock_driver, mock_ctx, max_no_progress_attempts=2)

        mock_visitor = MagicMock()
        mock_visitor.tables = ["t"]

        with (
            patch(
                "postgres_fastmcp.domains.index_tuning.llm_opt.TableAliasVisitor",
                return_value=mock_visitor,
            ),
            patch.object(tool, "_get_table_size", new_callable=AsyncMock, return_value=100.0),
            patch(
                "postgres_fastmcp.domains.index_tuning.llm_opt.ExplainPlanBuilder",
                return_value=MagicMock(
                    explain=AsyncMock(return_value=MagicMock(value='{"Plan": {"Node Type": "Seq Scan", "Plans": []}}'))
                ),
            ),
            patch.object(
                tool,
                "_extract_indexes_from_explain_plan_with_columns",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch.object(
                tool,
                "_evaluate_configuration_cost",
                new_callable=AsyncMock,
                return_value=100.0,
            ),
            patch.object(
                tool,
                "_get_recommendations_via_context",
                new_callable=AsyncMock,
                return_value='{"alternatives": []}',
            ),
        ):
            rec_set, cost = await tool._generate_recommendations(query_weights)

        assert rec_set == set()
        assert cost == 100.0


class TestLLMOptimizerToolGetRecommendationsViaContext:
    """Tests for _get_recommendations_via_context."""

    @pytest.mark.asyncio
    async def test_returns_text_from_ctx_sample(self) -> None:
        mock_driver = AsyncMock()
        mock_ctx = MagicMock()
        mock_ctx.sample = AsyncMock(
            return_value=MagicMock(text='{"alternatives":[[{"table_name":"t","columns":["a"]}]]}')
        )
        tool = LLMOptimizerTool(mock_driver, mock_ctx)
        result = await tool._get_recommendations_via_context("prompt")
        assert "alternatives" in result

    @pytest.mark.asyncio
    async def test_raises_when_response_has_no_text_attr(self) -> None:
        mock_driver = AsyncMock()
        mock_ctx = MagicMock()

        # Response without .text attribute (e.g. ImageContent)
        class NoTextResponse:
            pass

        mock_ctx.sample = AsyncMock(return_value=NoTextResponse())
        tool = LLMOptimizerTool(mock_driver, mock_ctx)
        with pytest.raises(ValueError, match="Unexpected response type"):
            await tool._get_recommendations_via_context("prompt")

    @pytest.mark.asyncio
    async def test_returns_empty_string_when_text_is_none(self) -> None:
        mock_driver = AsyncMock()
        mock_ctx = MagicMock()
        mock_ctx.sample = AsyncMock(return_value=MagicMock(text=None))
        tool = LLMOptimizerTool(mock_driver, mock_ctx)
        result = await tool._get_recommendations_via_context("prompt")
        assert result == ""


class TestLLMOptimizerToolEstimateIndexSize2:
    """Tests for _estimate_index_size_2."""

    @pytest.mark.asyncio
    async def test_empty_set_returns_zero(self) -> None:
        tool = LLMOptimizerTool(AsyncMock(), MagicMock())
        result = await tool._estimate_index_size_2(set())
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_returns_size_from_hypopg_result(self) -> None:
        mock_driver = AsyncMock()
        mock_driver.execute = AsyncMock(return_value=[RowResult(cells={"size": 2048})])
        tool = LLMOptimizerTool(mock_driver, MagicMock())
        idx = IndexDefinition(table="t", columns=("a",))
        result = await tool._estimate_index_size_2({idx}, min_size_penalty=1024)
        assert result >= 1024
        assert result >= 2048

    @pytest.mark.asyncio
    async def test_empty_result_logs_warning_returns_penalty(self) -> None:
        """When execute returns empty list, no size is added but loop continues; next index can add size."""
        mock_driver = AsyncMock()
        mock_driver.execute = AsyncMock(return_value=[])
        tool = LLMOptimizerTool(mock_driver, MagicMock())
        idx = IndexDefinition(table="t", columns=("a",))
        result = await tool._estimate_index_size_2({idx}, min_size_penalty=100.0)
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_exception_during_execute_logged_index_still_processed(self) -> None:
        """When execute raises, error is logged and that index contributes 0; other indexes still run."""
        mock_driver = AsyncMock()
        call_count = 0

        async def side_effect(*args: object, **kwargs: object) -> list:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("hypopg failed")
            return [RowResult(cells={"size": 512})]

        mock_driver.execute = AsyncMock(side_effect=side_effect)
        tool = LLMOptimizerTool(mock_driver, MagicMock())
        idx1 = IndexDefinition(table="t1", columns=("a",))
        idx2 = IndexDefinition(table="t2", columns=("b",))
        result = await tool._estimate_index_size_2({idx1, idx2}, min_size_penalty=64)
        assert result >= 512
        assert call_count == 2
