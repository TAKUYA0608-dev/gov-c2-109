"""GOV-C2-109 — inner domain workflow graph (Cat 2).

Instantiated by OutcomeEvidenceWorkflowGraphNode.get_subgraph() in graph.py. Linear topology with per-node skip
guards (the portable Cat 2 form; conditional edges don't propagate across the subgraph boundary):

    START → schema_field_locate → evidence_extract → citation_provenance_check → officer_review_gate → END

On rejected / 0-report input, schema_field_locate sets report_count=0 (+error_code); evidence_extract and
officer_review_gate no-op and citation_provenance_check emits the out-of-scope safe answer — no fabricated
outcome evidence.
"""

from __future__ import annotations
from typing import Any

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState

from src.nodes.citation_provenance_check_node import CitationProvenanceCheckNode
from src.nodes.evidence_extract_node import EvidenceExtractNode
from src.nodes.officer_review_gate_node import OfficerReviewGateNode
from src.nodes.schema_field_locate_node import SchemaFieldLocateNode
from src.schemas.state import State


class OutcomeEvidenceWorkflow(BaseGraph):
    """Inner graph: schema_field_locate → evidence_extract → citation_provenance_check → officer_review_gate."""

    @property
    def name(self) -> str:
        return "OutcomeEvidenceWorkflow"

    @property
    def state_schema(self) -> type:
        return State

    def _validate_config(self) -> None:
        pass

    def register_nodes(self) -> None:
        # No super() — BaseGraph.register_nodes() is abstract.
        self._nodes["schema_field_locate"] = SchemaFieldLocateNode()
        self._nodes["evidence_extract"] = EvidenceExtractNode()
        self._nodes["citation_provenance_check"] = CitationProvenanceCheckNode()
        self._nodes["officer_review_gate"] = OfficerReviewGateNode()

    def add_edges(self) -> None:
        # Static linear backbone; the 0-report / rejected skip is handled by per-node guards.
        self._sg.add_edge(START, "schema_field_locate")
        self._sg.add_edge("schema_field_locate", "evidence_extract")
        self._sg.add_edge("evidence_extract", "citation_provenance_check")
        self._sg.add_edge("citation_provenance_check", "officer_review_gate")
        self._sg.add_edge("officer_review_gate", END)

    def route(self, state: AgentState) -> str:
        """Required by the BaseGraph ABC. Linear topology → not wired to a conditional edge."""
        if state.get("error_code") or state.get("report_count", 0) == 0:
            return "citation_provenance_check"
        return "evidence_extract"

    def get_output(self, state: AgentState) -> dict[str, Any]:
        return {
            "output": state.get("result"),
            "status": state.get("status"),
            "report_count": state.get("report_count", 0),
            "human_review_required": state.get("human_review_required", False),
            "error_code": state.get("error_code"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
