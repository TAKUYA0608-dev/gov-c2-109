"""GOV-C2-109 — Agent state (Government AI Use-Case Outcome Evidence Extractor, Cat 2).

ADR-005: State is a flat TypedDict — never a validation/BaseModel instance. Complex fields are stored
as JSON strings (``NotRequired[str]`` + ``# JSON:``); nodes ``json.dumps`` on write / ``json.loads`` on read.

Read-only / advisory: the agent ingests authorized, redaction-applied government AI use-case reports plus a
government-owned outcome-evidence schema and a reporting period, and produces a candidate **Outcome Evidence
Register** deliverable (per schema field: value + doc/page citation + confidence + missing-data flag +
needs-officer-review flag). It never approves an AI use-case, publishes a claim, scores a vendor, exposes
protected data, or makes any procurement/administrative decision. The verdict on outcomes and any external
reuse are always the programme owner's (an authorized human), gated by a mandatory officer-review gate.

No PII in State: resident/staff PII and My-Number are detected and minimised **pre-LLM (S-2)** in
pre_process before anything is written to ``validated_input``; the S-3 output gate re-redacts as
defense-in-depth. All agent-specific fields are NotRequired (populated progressively; absent at empty-start
invoke).
"""

from __future__ import annotations


from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Agent state for the outcome-evidence extraction workflow."""

    # ── pre_process (ReportIntake + SensitiveDataDetectAndMinimise — S-1/S-2) ──
    validated_input: str  # JSON: {reports[], schema, period} (PII minimised, source resolved)
    input_format: str  # "json" | "text" | "empty" | "rejected"
    enriched_context: str  # JSON: {source, channel} (read-only caller context)

    # ── inner workflow (schema_field_locate → evidence_extract → citation_provenance_check → officer_review_gate) ─
    located_evidence: str  # JSON: [{report_id, source, anchors[{field, page, span, raw_value}]}]
    report_count: int  # reports located (0 → out-of-scope safe answer)
    extracted_fields: str  # JSON: [{report_id, source, fields{field: {value, page, span, confidence, missing_data, needs_officer_review}}}]
    result: str  # JSON: assembled Outcome Evidence Register (candidate)
    human_review_required: bool  # True once the officer-review gate flags fields needing review
    review_status: str  # "pending_officer_review" | "not_required"

    # ── post_process (EvidenceRegisterCompose — S-3 gate + S-4 audit) ─────────
    formatted_output: str  # JSON: final response envelope (register + disclaimer)
    disclaimer: str  # mandatory candidate / advisory-only disclaimer
    audit_logged: bool  # True once the terminal audit event is emitted

    # ── degraded-path signalling (SUCCESS + error_code, never status=ERROR) ───
    # INPUT_REJECTED | INJECTION_REJECTED | INPUT_TOO_LONG | NO_REPORTS | CITATION_INCOMPLETE
    error_code: str
    error_message: str  # operator-facing detail
