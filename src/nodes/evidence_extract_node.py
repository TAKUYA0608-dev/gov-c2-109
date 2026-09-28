"""GOV-C2-109 — inner workflow step 2: evidence_extract.

Deterministic bounded extraction of each of the 7 schema fields from the anchored spans (no LLM). Each value
is bounded to its anchored doc/page/span — never fabricated. Fields with no anchor → ``missing_data``.
Prompt-like / schema-external span content is **contained** (neutralized, treated as quoted evidence) and
flagged ``needs_officer_review`` (pre-LLM containment, F-01 — distinct from the S-3 output gate). Skips (no-op)
on rejected / 0-report input after emitting a skip audit event.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import OutcomeEvidenceService
from src.utils.audit import emit_trace_event


class EvidenceExtractNode(FunctionNode):
    """Bounded, deterministic extraction of the 7 schema fields per located report."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("error_code") or state.get("report_count", 0) == 0:
            emit_trace_event("evidence_extract.skip", {"reason": state.get("error_code") or "no_reports"}, state)
            return {}

        located = json.loads(state.get("located_evidence") or "[]")
        extracted = [OutcomeEvidenceService.extract(loc) for loc in located]
        contained = sum(1 for e in extracted for f in e["fields"].values() if f.get("contained"))
        missing = sum(1 for e in extracted for f in e["fields"].values() if f.get("missing_data"))
        emit_trace_event(
            "evidence_extract.complete",
            {"reports": len(extracted), "contained_fields": contained, "missing_fields": missing},
            state,
        )
        return {"extracted_fields": json.dumps(extracted, ensure_ascii=False), "status": AgentStatus.SUCCESS.value}
