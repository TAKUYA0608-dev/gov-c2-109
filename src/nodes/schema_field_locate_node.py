"""GOV-C2-109 — inner workflow step 1: schema_field_locate.

Deterministic ingest of the validated, PII-minimised reports and anchoring of the 7 government schema fields
to their doc/page/span provenance (seeded schema-label lexicon, no LLM). Sets ``report_count``. **0 valid
reports (rejected input, non-JSON text, or all rows missing report_id) routes to the out-of-scope safe
answer** — the agent never fabricates outcome evidence for reports it did not receive.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import OutcomeEvidenceService
from src.utils.audit import emit_trace_event


class SchemaFieldLocateNode(FunctionNode):
    """Ingest minimised reports and anchor the 7 schema fields to doc/page/span provenance."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        # Reports arrive already validated + PII-minimised + provenance-resolved by pre_process (S-1): each
        # `source` is a grounded citation `src:<sha8>` or None (a forged surrogate was dropped at S-1). We do
        # not re-run provenance here — locate trusts that single upstream resolution.
        slots = json.loads(state.get("validated_input") or state.get("user_input") or "{}")
        if not isinstance(slots, dict):
            slots = {}
        canonical = json.dumps(slots, ensure_ascii=False)
        reports = slots.get("reports") if isinstance(slots.get("reports"), list) else []

        if state.get("error_code") or not reports:
            emit_trace_event("schema_field_locate.skip", {"reason": state.get("error_code") or "no_reports"}, state)
            return {
                "validated_input": canonical,
                "located_evidence": "[]",
                "report_count": 0,
                "error_code": state.get("error_code") or "NO_REPORTS",
                "status": AgentStatus.SUCCESS.value,
            }

        located = [
            loc
            for r in reports
            if isinstance(r, dict)
            for loc in (OutcomeEvidenceService.locate(r),)
            if loc is not None
        ]
        if not located:
            emit_trace_event("schema_field_locate.skip", {"reason": "all_malformed"}, state)
            return {
                "validated_input": canonical,
                "located_evidence": "[]",
                "report_count": 0,
                "error_code": "NO_REPORTS",
                "status": AgentStatus.SUCCESS.value,
            }

        anchor_total = sum(len(loc["anchors"]) for loc in located)
        emit_trace_event(
            "schema_field_locate.complete",
            {"supplied": len(reports), "located": len(located), "anchors": anchor_total},
            state,
        )
        return {
            "validated_input": canonical,
            "located_evidence": json.dumps(located, ensure_ascii=False),
            "report_count": len(located),
            "status": AgentStatus.SUCCESS.value,
        }
