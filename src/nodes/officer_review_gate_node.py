"""GOV-C2-109 — inner workflow step 4: officer_review_gate (mandatory human gate).

Deterministic human-in-the-loop gate. It does **not** approve any outcome, publish any claim, or assign a real
owner — it flags the register entries that require an authorized officer's sign-off before external reuse
(uncited / low-confidence / contained / missing-data fields, and any report with review items), records the
review status into the register, and sets ``human_review_required``. The final verdict on outcomes and any
external reuse are the programme owner's. Skips (no-op) on the rejected / 0-report safe-answer branch after
emitting a skip audit event.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.utils.audit import emit_trace_event


class OfficerReviewGateNode(FunctionNode):
    """Flag register entries requiring authorized officer review; set human_review_required."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        report = json.loads(state.get("result") or "{}")
        if (
            state.get("error_code")
            or state.get("report_count", 0) == 0
            or report.get("status_kind") != "outcome_evidence_register"
        ):
            emit_trace_event("officer_review_gate.skip", {"reason": state.get("error_code") or "no_register"}, state)
            return {
                "human_review_required": False,
                "review_status": "not_required",
                "status": AgentStatus.SUCCESS.value,
            }

        material: list[dict[str, Any]] = []
        for entry in report.get("register", []):
            for field in entry.get("review_fields", []):
                material.append(
                    {
                        "report_id": entry["report_id"],
                        "field": field,
                        "reason": "Uncited / low-confidence / contained / missing outcome-evidence field — "
                        "requires authorized officer review before external reuse",
                    }
                )

        required = bool(material)
        review = {
            "required": required,
            "status": "pending_officer_review" if required else "not_required",
            "note": "The verdict on outcomes and any external reuse must be confirmed by the programme owner "
            "(an authorized human). This agent produces a candidate register only.",
            "review_items": material,
        }
        report["officer_review"] = review
        emit_trace_event(
            "officer_review_gate.complete", {"review_required": required, "review_item_count": len(material)}, state
        )
        return {
            "result": json.dumps(report, ensure_ascii=False),
            "human_review_required": required,
            "review_status": review["status"],
            "status": AgentStatus.SUCCESS.value,
        }
