"""GOV-C2-109 — pre_process node: ReportIntake + SensitiveDataDetectAndMinimise (S-1/S-2, pre-LLM).

Accepts a structured JSON intake ``{reports[], schema, period}`` (each report = ``{report_id, source,
pages[{page, text}]}``) or NL text, normalizes it (NFKC + control/HTML strip), enforces S-1/S-2, and extracts
the analysis slots. The agent is read-only: it never mutates the source feed.

Degraded contract (SDK 1.0.0): a caller-instruction injection attempt / oversize / empty never sets
``status=ERROR``. They return ``status=SUCCESS + error_code`` (``INJECTION_REJECTED`` / ``INPUT_TOO_LONG`` /
``INPUT_REJECTED``) and **discard the offending body** so ``main`` / ``post_process`` still run (out-of-scope
safe answer + disclaimer + S-3 + S-4). The ``@final`` framework hook is not invoked by the local stub framework,
so ``execute()`` re-checks the same S-2 conditions itself.

Two DISTINCT injection layers:
  1. Caller-instruction surface (S-2 hard degrade): if the input is NOT a structured reports payload and
     contains injection markers, it is a caller trying to prompt-inject the agent → INJECTION_REJECTED
     (whole body discarded). This is the platform-facing input gate.
  2. Report-prose containment (pre-LLM, F-01, separate from S-3): injection markers inside a *report's* text
     are QUOTED EVIDENCE. They are never rejected here (a legitimate report may quote such text); instead they
     are neutralized + flagged ``needs_officer_review`` downstream at the deterministic extraction step. This
     layer is proof-tested separately from the S-3 output gate.

Pre-LLM S-2 minimise: every report page text is passed through ``_minimise()`` (redacts My-Number (12 digit) /
credential / email / phone) and PII display fields (resident/staff/applicant/officer/contact name, address,
email, phone) are dropped before anything is written to ``validated_input``. Report identifiers are tokenized
to an opaque surrogate; caller ``source`` provenance is resolved once here to a grounded citation or None.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import INJECTION_MARKERS, resolve_provenance, safe_identifier
from src.utils.audit import emit_trace_event

_MAX_INPUT = 300_000  # outcome-evidence intake carries multiple multi-page reports → larger cap than a chat prompt
_REJECT_CODES = frozenset({"INJECTION_REJECTED", "INPUT_TOO_LONG"})
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_HTML_TAG = re.compile(r"<[^>]+>")

# ── input hygiene: minimise resident/staff PII a report may carry before persisting to State ──
_CREDENTIAL = re.compile(r"\b(sk-[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{12,}|eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,})\b")
_MY_NUMBER = re.compile(r"\b\d{12}\b")  # Japanese My-Number / 個人番号
_EMAIL = re.compile(r"\b[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){1,4}\b")
_PHONE = re.compile(r"(?<![\d.])(?:(?:\+81[-\s]?\d{1,4}|0\d{1,4})[-\s]?\d{1,4}[-\s]?\d{3,4})(?![\d.])")
_REDACTED = "[REDACTED]"
# PII display fields dropped entirely — reports are keyed by opaque report_id, not by a person / applicant name.
_PII_DROP_FIELDS = frozenset(
    {
        "resident_name",
        "staff_name",
        "officer_name",
        "applicant_name",
        "citizen_name",
        "contact_name",
        "person_name",
        "name",
        "email",
        "phone",
        "address",
        "contact",
        "my_number",
        "mynumber",
    }
)
# Identifier keys are unconditionally tokenized to an opaque surrogate (see safe_identifier) — no syntactic
# passthrough — so a PII / free-text report_id can never leak into State, a citation, or output.
_ID_FIELDS = frozenset({"report_id", "id"})


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def _minimise(text: str) -> str:
    """Redact My-Number / credential / email / phone patterns from a free-text value (pre-LLM S-2)."""
    out = _CREDENTIAL.sub(_REDACTED, text)
    out = _MY_NUMBER.sub(_REDACTED, out)
    out = _EMAIL.sub(_REDACTED, out)
    out = _PHONE.sub(_REDACTED, out)
    return out


def _minimise_obj(obj: Any) -> Any:
    """Recursively drop PII display fields, tokenize identifiers, resolve provenance, and minimise every string."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            key = k.lower()
            if key in _PII_DROP_FIELDS:
                continue
            if key in _ID_FIELDS:
                text = str(v).strip() if v is not None else ""
                out[k] = safe_identifier(text) if text else None
                continue
            if key == "source":
                # Provenance → grounded citation only if it resolves to an authorized system of record
                # (privacy-tokenized); unverifiable / free-text / forged source → None → S-3 fail-closed.
                out[k] = resolve_provenance(v)
                continue
            out[k] = _minimise_obj(v)
        return out
    if isinstance(obj, list):
        return [_minimise_obj(v) for v in obj]
    if isinstance(obj, str):
        return _minimise(obj)
    return obj


def _looks_like_reports_payload(raw: str) -> bool:
    """True if the raw input parses as a structured reports payload (dict with reports[] or a bare list)."""
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return False
    if isinstance(obj, list):
        return True
    if isinstance(obj, dict):
        return isinstance(obj.get("reports"), list) or isinstance(obj.get("report"), dict)
    return False


class PreProcessNode(FunctionNode):
    """Validate the intake, minimise PII pre-LLM, tokenize/resolve provenance, and extract reports/schema/period."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _reject_code(self, raw: str) -> str | None:
        if len(raw) > _MAX_INPUT:
            return "INPUT_TOO_LONG"
        # Injection reject applies to the CALLER-INSTRUCTION surface only. A structured reports payload is not
        # rejected on markers (report prose is quoted evidence — contained downstream, F-01); plain-text
        # injection (a caller trying to instruct the agent) IS rejected here.
        if _looks_like_reports_payload(raw):
            return None
        if any(marker in _nfkc(raw).lower() for marker in INJECTION_MARKERS):
            return "INJECTION_REJECTED"
        return None

    def _extra_security_gate_input(self, state: dict[str, Any]) -> dict[str, Any]:
        """S-2 domain checks: size cap + caller-instruction prompt-injection markers.

        SDK 1.0.0 contract: MUST NOT raise, and MUST NOT set status=ERROR (that would short-circuit the
        pipeline past post_process). A rejection is surfaced as a degraded ``SUCCESS + error_code``; the
        offending body is discarded by execute().
        """
        raw = state.get("user_input", "") or ""
        code = self._reject_code(raw)
        if code:
            out = dict(state)
            out["error_code"] = code
            return out
        return dict(state)

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        raw = state.get("user_input", "") or ""
        input_context = state.get("input_context", {})  # read-only [C1]
        enriched = json.dumps(
            {"source": "GovernmentAIUseCaseOutcomeAgent", "channel": input_context.get("channel", "unknown")},
            ensure_ascii=False,
        )

        # Degrade on rejection: the S-2 hook may already have set error_code (real SDK); re-detect here because
        # the local stub framework does not invoke the hook. Discard the offending body entirely.
        prior = state.get("error_code")
        code = prior if prior in _REJECT_CODES else self._reject_code(raw)
        if code:
            emit_trace_event("report_intake.rejected", {"reason": code}, state)
            return {
                "validated_input": "{}",
                "input_format": "rejected",
                "enriched_context": enriched,
                "user_input": "",
                "error_code": code,
                "status": AgentStatus.SUCCESS.value,
            }

        if not raw.strip():
            emit_trace_event("report_intake.rejected", {"reason": "empty_input"}, state)
            return {
                "validated_input": "{}",
                "input_format": "empty",
                "enriched_context": enriched,
                "error_code": "INPUT_REJECTED",
                "status": AgentStatus.SUCCESS.value,
            }

        slots, fmt = self._parse(_HTML_TAG.sub(" ", _CONTROL.sub("", _nfkc(raw))))
        emit_trace_event(
            "report_intake.validated",
            {"input_format": fmt, "report_count": len(slots["reports"]), "period": slots.get("period")},
            state,
        )
        return {
            "validated_input": json.dumps(slots, ensure_ascii=False),
            "input_format": fmt,
            "enriched_context": enriched,
            "status": AgentStatus.SUCCESS.value,
        }

    def _parse(self, text: str) -> tuple[dict[str, Any], str]:
        try:
            obj = json.loads(text)
        except (ValueError, TypeError):
            return {"reports": [], "schema": None, "period": None}, "text"
        if isinstance(obj, dict):
            reports = obj.get("reports")
            reports = reports if isinstance(reports, list) else []
            # reports / schema / period are untrusted → minimise them (S-3 re-redacts at output).
            return {
                "reports": _minimise_obj(reports),
                "schema": _minimise_obj(obj.get("schema")),
                "period": _minimise_obj(obj.get("period")),
            }, "json"
        if isinstance(obj, list):  # bare reports array
            return {"reports": _minimise_obj(obj), "schema": None, "period": None}, "json"
        return {"reports": [], "schema": None, "period": None}, "text"
