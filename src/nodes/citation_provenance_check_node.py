"""GOV-C2-109 — inner workflow step 3: citation_provenance_check.

Deterministic per-field citation completeness + source-span entailment verification, then composition of the
candidate **Outcome Evidence Register** deliverable: a register-level summary plus a per-report block (7 schema
fields each with value / doc-page-span citation / confidence / missing-data / needs-officer-review), each cited
to its resolved source. The register is advisory only — it never approves an outcome, publishes a claim, or
makes an administrative decision. On the 0-report / rejected branch it emits the out-of-scope safe answer.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import OutcomeEvidenceService
from src.utils.audit import emit_trace_event

_OUT_OF_SCOPE = (
    "分析可能な行政 AI 利活用報告書が入力に見つかりませんでした。"
    "reports 配列に report_id・source・pages（各ページの text）を含む JSON をご指定いただくか、"
    "対象の成果エビデンス schema と reporting period を明確にしてください。"
)


class CitationProvenanceCheckNode(FunctionNode):
    """Verify citation completeness + entailment and compose the candidate register (or safe answer)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        extracted = json.loads(state.get("extracted_fields") or "[]")
        if state.get("error_code") or not extracted:
            emit_trace_event(
                "citation_provenance_check.safe", {"reason": state.get("error_code") or "no_reports"}, state
            )
            report: dict[str, Any] = {
                "status_kind": "out_of_scope",
                "message": _OUT_OF_SCOPE,
                "period": None,
                "register": [],
                "summary": {},
                "citations": [],
            }
            return {"result": json.dumps(report, ensure_ascii=False), "status": AgentStatus.SUCCESS.value}

        verified = [OutcomeEvidenceService.verify_report(e) for e in extracted]
        citations: list[dict[str, str]] = [{"report_id": v["report_id"], "source": v["source"]} for v in verified]
        summary = OutcomeEvidenceService.register_summary(verified)
        register: list[dict[str, Any]] = [
            {
                "report_id": v["report_id"],
                "source_citation": v["source"],
                "fields": v["fields"],
                "review_fields": v["review_fields"],
            }
            for v in verified
        ]
        report = {
            "status_kind": "outcome_evidence_register",
            "period": self._period(state),
            "summary": summary,
            "register": register,
            "citations": citations,
        }
        emit_trace_event(
            "citation_provenance_check.complete",
            {
                "reports": len(register),
                "grounded_fields": summary["grounded_fields"],
                "reports_needing_review": len(summary["reports_needing_review"]),
            },
            state,
        )
        return {"result": json.dumps(report, ensure_ascii=False), "status": AgentStatus.SUCCESS.value}

    @staticmethod
    def _period(state: dict[str, Any]) -> Any:
        slots = json.loads(state.get("validated_input") or "{}")
        return slots.get("period") if isinstance(slots, dict) else None
