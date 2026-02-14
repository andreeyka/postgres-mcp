"""Artifacts for PostgreSQL explain plans."""

import difflib
import json
from typing import Any

from attrs import define, field


# Maximum filter text length before truncation
MAX_FILTER_TEXT_LENGTH = 100


@define
class PlanNode:
    """PostgreSQL query execution plan node.

    Represents a single node in the execution plan tree with performance metrics.
    """

    node_type: str
    total_cost: float
    startup_cost: float
    plan_rows: int
    plan_width: int

    # Actual metrics from ANALYZE
    actual_total_time: float | None = field(default=None)
    actual_startup_time: float | None = field(default=None)
    actual_rows: int | None = field(default=None)
    actual_loops: int | None = field(default=None)

    # Buffer info
    shared_hit_blocks: int | None = field(default=None)
    shared_read_blocks: int | None = field(default=None)
    shared_written_blocks: int | None = field(default=None)

    # Other common fields
    relation_name: str | None = field(default=None)
    filter: str | None = field(default=None)
    children: list["PlanNode"] = field(factory=list)

    @classmethod
    def from_json_data(cls, json_node: dict[str, Any]) -> "PlanNode":
        """Create plan node from JSON data.

        Args:
            json_node: Dictionary with plan node data from PostgreSQL EXPLAIN.

        Returns:
            PlanNode instance with filled data.
        """
        # Extract basic fields
        node = cls(
            node_type=json_node["Node Type"],
            total_cost=json_node["Total Cost"],
            startup_cost=json_node["Startup Cost"],
            plan_rows=json_node["Plan Rows"],
            plan_width=json_node["Plan Width"],
        )

        # Optional ANALYZE fields
        if "Actual Total Time" in json_node:
            node.actual_total_time = json_node["Actual Total Time"]
            node.actual_startup_time = json_node["Actual Startup Time"]
            node.actual_rows = json_node["Actual Rows"]
            node.actual_loops = json_node["Actual Loops"]

        # Optional BUFFERS fields
        if "Shared Hit Blocks" in json_node:
            node.shared_hit_blocks = json_node["Shared Hit Blocks"]
            node.shared_read_blocks = json_node["Shared Read Blocks"]
            node.shared_written_blocks = json_node["Shared Written Blocks"]

        # Common optional fields
        if "Relation Name" in json_node:
            node.relation_name = json_node["Relation Name"]
        if "Filter" in json_node:
            node.filter = json_node["Filter"]

        # Recursively process child plans
        if "Plans" in json_node:
            node.children = [cls.from_json_data(child) for child in json_node["Plans"]]

        return node


@define
class ExplainPlanArtifact:
    """PostgreSQL query execution plan artifact.

    Contains execution plan tree, timing metrics, and text representation.
    """

    value: str
    plan_tree: PlanNode
    planning_time: float | None = field(default=None)
    execution_time: float | None = field(default=None)

    def __init__(
        self,
        value: str,
        plan_tree: PlanNode,
        planning_time: float | None = None,
        execution_time: float | None = None,
    ) -> None:
        """Initialize execution plan artifact.

        Args:
            value: JSON representation of the execution plan.
            plan_tree: Execution plan node tree.
            planning_time: Planning time in milliseconds.
            execution_time: Execution time in milliseconds.
        """
        self.value = value
        self.plan_tree = plan_tree
        self.planning_time = planning_time
        self.execution_time = execution_time

    def to_text(self) -> str:
        """Convert the explain plan to a text representation.

        Returns:
            str: A string representation of the execution plan with timing information.
        """
        result = []

        # Add timing information if available
        if self.planning_time is not None:
            result.append(f"Planning Time: {self.planning_time:.3f} ms")
        if self.execution_time is not None:
            result.append(f"Execution Time: {self.execution_time:.3f} ms")

        # Add plan tree representation
        result.append(self._format_plan_node(self.plan_tree))

        return "\n".join(result)

    @staticmethod
    def _format_plan_node(node: PlanNode, level: int = 0) -> str:
        """Recursively format a plan node and its children.

        Args:
            node: The plan node to format
            level: The current indentation level

        Returns:
            str: A formatted string representation of the node and its children
        """
        indent = "  " * level
        output = f"{indent}→ {node.node_type} (Cost: {node.startup_cost:.2f}..{node.total_cost:.2f})"

        # Add table name if present
        if node.relation_name:
            output += f" on {node.relation_name}"

        # Add rows information
        output += f" [Rows: {node.plan_rows}]"

        # Add actual metrics if available in a compact form
        if node.actual_total_time is not None:
            actual_startup = node.actual_startup_time
            actual_total = node.actual_total_time
            actual_rows = node.actual_rows
            actual_loops = node.actual_loops
            output += (
                f" [Actual: {actual_startup:.2f}..{actual_total:.2f} ms, Rows: {actual_rows}, Loops: {actual_loops}]"
            )

        # Add filter if present
        if node.filter:
            filter_text = node.filter
            # Truncate long filters for readability
            if len(filter_text) > MAX_FILTER_TEXT_LENGTH:
                filter_text = filter_text[: MAX_FILTER_TEXT_LENGTH - 3] + "..."
            output += f"\n{indent}  Filter: {filter_text}"

        # Add buffer information if available in a compact form
        if node.shared_hit_blocks is not None:
            hit_blocks = node.shared_hit_blocks
            read_blocks = node.shared_read_blocks
            written_blocks = node.shared_written_blocks
            output += f"\n{indent}  Buffers - hit: {hit_blocks}, read: {read_blocks}, written: {written_blocks}"

        # Recursively format children
        if node.children:
            for child in node.children:
                output += "\n" + ExplainPlanArtifact._format_plan_node(child, level + 1)

        return output

    @classmethod
    def from_json_data(cls, plan_data: dict[str, Any]) -> "ExplainPlanArtifact":
        """Create execution plan artifact from JSON data.

        Args:
            plan_data: Dictionary with execution plan data from PostgreSQL EXPLAIN.

        Returns:
            ExplainPlanArtifact instance with filled data.

        Raises:
            ValueError: If required 'Plan' field is missing in the data.
        """
        error_missing_plan = "Missing 'Plan' field in explain plan data"
        if "Plan" not in plan_data:
            raise ValueError(error_missing_plan)

        # Create plan tree from the "Plan" field
        plan_tree = PlanNode.from_json_data(plan_data["Plan"])

        # Extract optional timing information
        planning_time = plan_data.get("Planning Time")
        execution_time = plan_data.get("Execution Time")

        return cls(
            value=json.dumps(plan_data, indent=2),
            plan_tree=plan_tree,
            planning_time=planning_time,
            execution_time=execution_time,
        )

    @staticmethod
    def format_plan_summary(plan_data: dict[str, Any] | None) -> str:
        """Extract and format key information from a raw plan data.

        Args:
            plan_data: Raw plan data dictionary or None.

        Returns:
            Formatted plan summary string.
        """
        if not plan_data:
            return "No plan data available"

        try:
            # Create a PlanNode from the raw JSON data
            if "Plan" in plan_data:
                plan_node = PlanNode.from_json_data(plan_data["Plan"])

                # Use _format_plan_node to format the output
                plan_tree = ExplainPlanArtifact._format_plan_node(plan_node, 0)

                return f"{plan_tree}"

        except Exception as e:
            return f"Error summarizing plan: {e}"
        else:
            return "Invalid plan data (missing Plan field)"

    @staticmethod
    def create_plan_diff(  # noqa: C901
        before_plan: dict[str, Any],
        after_plan: dict[str, Any],
        calculate_improvement_multiple: Any,  # noqa: ANN401
    ) -> str:
        """Generate a textual diff between two explain plans.

        Args:
            before_plan: The explain plan before changes
            after_plan: The explain plan after changes
            calculate_improvement_multiple: Function to calculate improvement multiple

        Returns:
            A string containing a readable diff between the two plans
        """
        if not before_plan or not after_plan:
            return "Cannot generate diff: Missing plan data"

        try:
            # Create PlanNode objects from the plans
            before_tree = PlanNode.from_json_data(before_plan["Plan"]) if "Plan" in before_plan else None
            after_tree = PlanNode.from_json_data(after_plan["Plan"]) if "Plan" in after_plan else None

            if not before_tree or not after_tree:
                return "Cannot generate diff: Invalid plan structure"

            # Format the plans as text
            before_lines = ExplainPlanArtifact._format_plan_node(before_tree).split("\n")
            after_lines = ExplainPlanArtifact._format_plan_node(after_tree).split("\n")

            # Generate a readable diff with context
            diff_lines = []
            diff_lines.append("PLAN CHANGES:")
            diff_lines.append("------------")

            # Extract cost information for a summary
            before_cost = before_tree.total_cost
            after_cost = after_tree.total_cost
            improvement = calculate_improvement_multiple(before_cost, after_cost)

            diff_lines.append(f"Cost: {before_cost:.2f} → {after_cost:.2f} ({improvement:.1f}x improvement)")
            diff_lines.append("")

            # Node type changes - a simplified structural diff
            diff_lines.append("Operation Changes:")

            # Helper function to extract node types with indentation
            def extract_node_types(node: PlanNode, level: int = 0, result: list[str] | None = None) -> list[str]:
                if result is None:
                    result = []
                indent = "  " * level
                node_info = f"{indent}→ {node.node_type}"
                if node.relation_name:
                    node_info += f" on {node.relation_name}"
                result.append(node_info)
                for child in node.children:
                    extract_node_types(child, level + 1, result)
                return result

            before_structure = extract_node_types(before_tree)
            after_structure = extract_node_types(after_tree)

            # Generate the structural diff
            structure_diff = list(
                difflib.unified_diff(
                    before_structure,
                    after_structure,
                    n=1,  # Context lines
                    lineterm="",
                )
            )

            # Add structural diff to output
            if structure_diff:
                diff_lines.extend(structure_diff)
            else:
                diff_lines.append("No structural changes detected")

            # Add more specific details about key changes
            diff_lines.append("")
            diff_lines.append("Major Changes:")

            # Look for significant changes like seq scan to index scan, changed filters, etc.
            # This requires traversing both trees and comparing nodes

            # For simplicity, we'll just list key changes in cost and rows
            if before_tree.node_type != after_tree.node_type:
                diff_lines.append(f"- Root operation changed: {before_tree.node_type} → {after_tree.node_type}")

            # Compare scan methods used
            before_scans = [line for line in before_lines if "Seq Scan" in line]
            after_scans = [line for line in after_lines if "Seq Scan" in line]
            if len(before_scans) > len(after_scans):
                scans_replaced = len(before_scans) - len(after_scans)
                diff_lines.append(f"- {scans_replaced} sequential scans replaced with more efficient access methods")

            # Look for new index scans
            before_idx_scans = [line for line in before_lines if "Index Scan" in line]
            after_idx_scans = [line for line in after_lines if "Index Scan" in line]
            if len(after_idx_scans) > len(before_idx_scans):
                diff_lines.append(f"- {len(after_idx_scans) - len(before_idx_scans)} new index scans used")

            return "\n".join(diff_lines)

        except Exception as e:
            return f"Error generating plan diff: {e}"
