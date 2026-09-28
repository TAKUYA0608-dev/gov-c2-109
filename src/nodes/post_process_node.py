"""GOV-C2-109 — post_process node: EvidenceRegisterCompose (S-3 output gate + S-4 audit).

S-3 (fail-closed): **enforce** per-report citation completeness — a grounded outcome-evidence register whose
reports are not all cited to a resolved source is never presented; it degrades to a safe ``needs_review``
answer with the register body withheld (``error_code=CITATION_INCOMPLETE``, still SUCCESS so post/S-4/disclaimer
run). Re-redact any credential / My-Number / email / phone / resident-name leakage (defense-in-depth),
neutralize any injection marker in the output (defense-in-depth over the pre-LLM containment), and append the
mandatory candidate / advisory disclaimer — the register is a decision aid, not an outcome verdict; the verdict
on outcomes and any external reuse are the programme owner's (an authorized human), gated by the officer-review
gate. S-4: emit an audit event (counts / grounded-field totals / review flag / error_code only — never a raw
outcome value, resident name, or My-Number). Runs on the full register, the citation-blocked branch, and the
out-of-scope safe branch.
"""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar, cast

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import INJECTION_MARKERS
from src.utils.audit import emit_trace_event

_DISCLAIMER = (
    "本レポートは提供された認可済み・redaction 済みの行政 AI 利活用報告書に基づく参考用の DRAFT 成果エビデンス"
    "register（候補）であり、AI 利活用の成果の是非・公表・調達・行政判断を確定するものではありません。"
    "各 field は候補であり、未引用・低 confidence・要確認の項目は needs-officer-review として明示しています。"
    "最終的な成果の判断・外部再利用の可否は、必ず認可された programme owner（人手）の確認・sign-off"
    "（officer-review gate）を経てください。本エージェントは助言用の candidate register を生成するのみで、"
    "承認・公表・調達・行政判断の実行は行いません。"
)

_CITATION_INCOMPLETE_MSG = (
    "register の一部に検証可能な出典（provenance）が確認できなかったため、根拠不十分な成果エビデンス"
    "register の提示を差し控えました。各報告に認可された system of record の source を付与のうえ再実行してください。"
)
_NEEDS_REVIEW_NOTE = (
    "Grounding could not be verified for every report; the candidate register is withheld pending valid "
    "provenance and authorized officer review."
)

# S-3 defense-in-depth: re-redact secrets / contact info / resident names that could leak into any free-text
# field of the register (applied to the whole serialized report before it becomes the output envelope).
_SECRET = re.compile(r"\b(?:sk-[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{12,}|eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}|\d{12})\b")
_EMAIL = re.compile(r"[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){1,4}")
_PHONE = re.compile(r"(?<![\d.])(?:(?:\+81[-\s]?\d{1,4}|0\d{1,4})[-\s]?\d{1,4}[-\s]?\d{3,4})(?![\d.])")
# Resident / staff person names carried with a Japanese honorific suffix (X 様 / X 氏 / X さん).
_PERSON = re.compile(r"[^\s\"',、。:：]{1,12}(?:様|氏|さん)")
_REDACTORS = (_SECRET, _EMAIL, _PHONE, _PERSON)


def _redact_report(report: dict[str, Any]) -> dict[str, Any]:
    """Serialize → redact secret / contact / person-name patterns + neutralize injection markers → deserialize."""
    text = json.dumps(report, ensure_ascii=False)
    for pattern in _REDACTORS:
        text = pattern.sub("[REDACTED]", text)
    for marker in INJECTION_MARKERS:
        text = re.sub(re.escape(marker), "[CONTAINED]", text, flags=re.IGNORECASE)
    return cast(dict[str, Any], json.loads(text))


class PostProcessNode(FunctionNode):
    """Verify citations, redact leakage, neutralize injection markers, append the disclaimer, emit audit."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_output(self, result: dict[str, Any]) -> dict[str, Any]:
        """S-3 preservation check: the DRAFT / candidate advisory disclaimer must be present in the envelope.

        SDK 1.0.0 contract: receives the **result dict from `execute()`**; returns the (possibly filtered)
        result. MAY raise to block an output missing the mandatory disclaimer.
        """
        out = result.get("formatted_output", "")
        if out and "参考" not in out and "DRAFT" not in out:
            raise ValueError("S-3: DRAFT / candidate advisory disclaimer missing from output")
        return dict(result)

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        report: dict[str, Any] = _redact_report(json.loads(state.get("result", "{}") or "{}"))

        grounded = report.get("status_kind") == "outcome_evidence_register"
        citations = report.get("citations", [])
        register = report.get("register", [])
        # S-3 per-entry authoritative correspondence: every register entry must carry BOTH its own local
        # source_citation AND an exact top-level {report_id, source} citation for the SAME report (not merely a
        # non-empty citation list — a partially ungrounded entry, or a top-level citation belonging to a
        # different report, must fail closed).
        cited_sources = {c.get("report_id"): c.get("source") for c in citations if c.get("source")}
        citation_complete = (not grounded) or (
            bool(register)
            and all(e.get("source_citation") for e in register)
            and all(cited_sources.get(e.get("report_id")) == e.get("source_citation") for e in register)
        )

        # S-3 fail-closed: an ungrounded register (any report missing a verifiable source citation) is never
        # presented. Degrade to a safe needs-review answer (SUCCESS + error_code), withhold the register body,
        # and still run the disclaimer + terminal S-4 audit.
        if grounded and not citation_complete:
            error_code = state.get("error_code") or "CITATION_INCOMPLETE"
            blocked: dict[str, Any] = {
                "status_kind": "needs_review",
                "period": report.get("period"),
                "summary": {},
                "register": [],  # incomplete register body withheld
                "officer_review": {"required": True, "status": "pending_officer_review", "note": _NEEDS_REVIEW_NOTE},
                "citations": [],
                "citation_complete": False,
                "message": _CITATION_INCOMPLETE_MSG,
                "disclaimer": _DISCLAIMER,
            }
            emit_trace_event(
                "post_process.citation_blocked",
                {"report_count": len(register), "error_code": error_code},
                state,
            )
            return {
                "formatted_output": json.dumps(blocked, ensure_ascii=False),
                "disclaimer": _DISCLAIMER,
                "audit_logged": True,
                "error_code": error_code,
                "status": AgentStatus.SUCCESS.value,
            }

        officer_review = report.get("officer_review", {"required": False, "status": "not_required"})

        formatted = {
            "status_kind": report.get("status_kind"),
            "period": report.get("period"),
            "summary": report.get("summary", {}),
            "register": register,
            "officer_review": officer_review,
            "citations": citations,
            "citation_complete": citation_complete,
            "message": report.get("message"),
            "disclaimer": _DISCLAIMER,
        }
        emit_trace_event(
            "post_process.complete",
            {
                "status_kind": report.get("status_kind"),
                "report_count": len(register),
                "review_required": officer_review.get("required", False),
                "citation_complete": citation_complete,
                "error_code": state.get("error_code"),
            },
            state,
        )
        return {
            "formatted_output": json.dumps(formatted, ensure_ascii=False),
            "disclaimer": _DISCLAIMER,
            "audit_logged": True,
            "status": AgentStatus.SUCCESS.value,
        }
