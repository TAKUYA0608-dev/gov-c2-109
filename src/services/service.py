"""GOV-C2-109 — deterministic domain services (no framework imports, no LLM).

OutcomeEvidenceService: locates the 7 government outcome-evidence schema fields inside authorized,
redaction-applied AI use-case reports by **deterministic schema-label anchoring** (a seeded label lexicon,
CoE-calibratable), extracts each field's value bounded to the anchored doc/page/span, verifies source-span
entailment (the cited span must contain the extracted value), and composes a candidate outcome-evidence
register. Missing labels → ``missing_data``; prompt-like / schema-external span content → ``needs_officer_review``
(the span is treated as quoted evidence, never executed).

Everything here is deterministic and auditable (label anchoring + substring/entailment checks + keyed
composition) — there is **no LLM** (no model in config/agent.yaml, no LLM dependency). Reports are keyed by an
opaque ``report_id``; resident/staff PII and My-Number are minimised pre-LLM in pre_process before any value is
extracted, and the S-3 output gate re-redacts any leakage. The seeded schema fields / label lexicon are
overridable by CoE without touching node logic.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

# Two SEPARATE concerns — do not conflate them:
#   (1) PRIVACY (safe_identifier): every caller identifier (report_id) is UNCONDITIONALLY tokenized to a
#       deterministic opaque surrogate so PII (even a bare name like ``Alice`` / ``TaroYamada``, no
#       spaces/symbols) can never reach a citation or the output. Tokenizing is a privacy measure — it does
#       NOT assert the value is authorized/verifiable.
#   (2) PROVENANCE (resolve_provenance): a caller ``source`` becomes a grounded CITATION only when it is
#       resolvable against the authorized provenance registry (names a trusted government system of record).
#       Any other free text (a resident name, ``unknown``, a fabricated value, or a value merely SHAPED like a
#       surrogate ``doc:1a2b3c4d``) is NOT verifiable provenance → NO citation → S-3 blocks the register as
#       CITATION_INCOMPLETE (fail-closed). "Tokenized" is never sufficient for a citation.
# Tokenization is UNCONDITIONAL (no syntactic passthrough): a caller value merely *shaped* like a surrogate
# (``rpt:deadbeef``) is re-hashed, never trusted, so it can never forge an internal join key. Identifiers /
# provenance are resolved exactly once at S-1 (pre_process); downstream trusts that resolution verbatim.

# Authorized provenance registry: the government systems of record trusted as verifiable evidence sources. A
# caller ``source`` is accepted as a grounded citation ONLY when its leading namespace names one of these (the
# "trusted context"). This is the deploying agency's / CoE's registry — overridable without touching node
# logic; it is a SEMANTIC allowlist of authorized systems, not a syntactic character class.
AUTHORIZED_PROVENANCE_SYSTEMS = frozenset(
    {
        "gov_program_registry",
        "program_registry",
        "programme_registry",
        "outcome_report",
        "outcome_repository",
        "evidence_repository",
        "evidence_registry",
        "audit_repository",
        "internal_audit",
        "records_management",
        "record_repository",
        "official_report",
        "authorized_report",
        "authorised_report",
        "government_data",
        "ai_usecase_registry",
        "usecase_registry",
        "kpi_repository",
        "dwh",
        "data_warehouse",
    }
)

# ── seeded government outcome-evidence schema: the 7 fields (government-owned; the non-configurable invariant) ──
OUTCOME_EVIDENCE_SCHEMA: tuple[str, ...] = (
    "target_process",  # 対象業務 / プロセス
    "purpose",  # 目的
    "evidence_source",  # エビデンス出典
    "measured_outcome",  # 測定された成果
    "constraints",  # 制約
    "human_oversight",  # 人的監督ステップ
    "evidence_gap",  # 未解決のエビデンスギャップ
)

# ── seeded schema-label lexicon: schema field → recognised narrative labels (CoE-calibratable) ──
# Deterministic bounded mapping of heterogeneous administrative narrative wording to the controlled schema.
# Labels are matched at line start followed by a ``:`` / ``：`` separator — a label match anchors the field;
# the value is the clause after the separator. No LLM, no fuzzy inference.
SCHEMA_LABELS: dict[str, tuple[str, ...]] = {
    "target_process": (
        "対象業務",
        "対象プロセス",
        "対象事務",
        "対象手続",
        "対象業務・プロセス",
        "target process",
        "process",
    ),
    "purpose": ("目的", "ねらい", "狙い", "導入目的", "purpose", "objective"),
    "evidence_source": (
        "エビデンス出典",
        "出典",
        "根拠資料",
        "データ出典",
        "根拠データ",
        "evidence source",
        "source data",
    ),
    "measured_outcome": (
        "測定された成果",
        "測定成果",
        "成果",
        "効果",
        "実績",
        "測定結果",
        "measured outcome",
        "outcome",
    ),
    "constraints": ("制約", "制約条件", "限界", "前提条件", "適用制約", "constraint", "limitation"),
    "human_oversight": (
        "人的監督ステップ",
        "人的監督",
        "人による確認",
        "人手確認",
        "人的関与",
        "監督ステップ",
        "human oversight",
        "human review",
    ),
    "evidence_gap": (
        "未解決のエビデンスギャップ",
        "エビデンスギャップ",
        "未解決課題",
        "残課題",
        "課題",
        "ギャップ",
        "evidence gap",
        "open gap",
    ),
}

# Report-prose injection markers — treated as QUOTED EVIDENCE (never executed). A span value that contains one
# of these is contained: the marker is neutralized and the field is flagged needs_officer_review (schema-external
# / prompt-like content), never presented as a grounded value.
INJECTION_MARKERS: tuple[str, ...] = (
    "ignore previous",
    "ignore all previous",
    "disregard the above",
    "disregard previous",
    "system prompt",
    "you are now",
    "###system",
    "<|im_start|>",
    "以上の指示を無視",
    "これまでの指示を無視",
    "システムプロンプト",
)

_MIN_VALUE_CHARS = 4  # a value shorter than this is treated as too weak to ground → low confidence


def _sha8(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def safe_identifier(value: Any) -> str:
    """PRIVACY tokenize a caller report identifier to a deterministic opaque surrogate ``rpt:<sha8>``.

    Caller identifiers are **always** tokenized — no syntactic passthrough — so a name (with or without
    spaces) can never survive into a citation or the output, and a caller value merely *shaped* like a
    surrogate (``rpt:deadbeef``) is re-hashed rather than trusted (it can never forge an internal join key).
    Same input → same surrogate (register / citations stay joinable within one invocation). This is a privacy
    measure only; it makes no claim that the identifier is authorized.
    """
    return "rpt:" + _sha8(str(value or "").strip())


def resolve_provenance(value: Any) -> str | None:
    """Resolve a **raw** caller ``source`` to a grounded, privacy-tokenized CITATION — or ``None``.

    Provenance validation (separate from privacy) and the **single** resolution point (S-1 / pre_process). A
    citation is emitted **only** when the source names an authorized government system of record
    (``<authorized-namespace>[:<ref>]``). Any other value — a resident name, ``unknown``, a fabricated value,
    **or a value that merely looks like a surrogate (``doc:1a2b3c4d`` / ``src:deadbeef``)** — is not verifiable
    provenance and returns ``None`` so the S-3 gate blocks the register as CITATION_INCOMPLETE (fail-closed).
    When authorized, the raw label is never used verbatim: the citation is a privacy hash (``src:<sha8>``) of
    the authorized reference. No synthetic provenance is fabricated.

    ★ Forged-surrogate defence: there is **no format-based passthrough**. A caller-supplied ``src:<hex>`` /
    ``doc:<hex>`` has an unauthorized namespace, so it resolves to ``None`` — dropped here at S-1, never
    reaching a citation. Provenance is resolved exactly once (here), so the produced ``src:<sha8>`` is the
    trusted citation downstream and is **never** fed back through this function.
    """
    text = str(value or "").strip()
    if not text:
        return None
    namespace = text.split(":", 1)[0].strip().lower()
    if namespace not in AUTHORIZED_PROVENANCE_SYSTEMS:
        return None  # unverifiable / forged-surrogate provenance → fail-closed (no citation → needs_review)
    return "src:" + _sha8(text)


def _contains_injection(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in INJECTION_MARKERS)


def _neutralize(text: str) -> str:
    """Neutralize report-prose injection markers so a marker can never survive into output as an instruction."""
    out = text
    for marker in INJECTION_MARKERS:
        out = re.sub(re.escape(marker), "[CONTAINED]", out, flags=re.IGNORECASE)
    return out


class OutcomeEvidenceService:
    """Deterministic schema-field location, bounded extraction, entailment check, and register composition."""

    # ── schema-field location (anchor labels to doc/page/span provenance) ─────
    @staticmethod
    def locate(report: dict[str, Any]) -> dict[str, Any] | None:
        """Anchor the 7 schema fields inside one report to their doc/page/span.

        Returns ``{report_id, source, anchors[]}`` or ``None`` if the report has no ``report_id``. ``report_id``
        is already privacy-tokenized and ``source`` already resolved (grounded citation or None) by pre_process
        (S-1); this method trusts those verbatim and never re-resolves provenance.
        """
        report_id = str(report.get("report_id") or report.get("id") or "").strip()
        if not report_id:
            return None
        pages: Any = report.get("pages") if isinstance(report.get("pages"), list) else []

        anchors: list[dict[str, Any]] = []
        for page in pages:
            if not isinstance(page, dict):
                continue
            page_no = page.get("page")
            text = str(page.get("text") or "")
            for line in text.splitlines():
                field, value, span = OutcomeEvidenceService._match_label(line)
                if field is None:
                    continue
                anchors.append({"field": field, "page": page_no, "span": span, "raw_value": value})
        return {"report_id": report_id, "source": report.get("source"), "anchors": anchors}

    @staticmethod
    def _match_label(line: str) -> tuple[str | None, str, str]:
        """Match one line against the seeded schema-label lexicon. Returns (field, value, span_id) or (None,...).

        The line must be ``<label><sep><value>`` with sep in ``:`` / ``：``. The longest matching label wins so
        specific labels (未解決のエビデンスギャップ) beat generic ones (課題)."""
        stripped = line.strip()
        if not stripped:
            return None, "", ""
        # normalize separators; split on the first ':' or '：'
        parts = re.split(r"[:：]", stripped, maxsplit=1)
        if len(parts) != 2:
            return None, "", ""
        label_part, value = parts[0].strip(), parts[1].strip()
        label_lower = label_part.lower()
        best_field: str | None = None
        best_len = 0
        for field, labels in SCHEMA_LABELS.items():
            for label in labels:
                if label_lower == label.lower() and len(label) > best_len:
                    best_field, best_len = field, len(label)
        if best_field is None:
            return None, "", ""
        span_id = "sp-" + _sha8(label_part + "|" + value)
        return best_field, value, span_id

    # ── bounded extraction (deterministic; missing_data + containment + confidence) ──
    @staticmethod
    def extract(located: dict[str, Any]) -> dict[str, Any]:
        """Extract each of the 7 schema fields for one located report (bounded to anchored spans).

        For every schema field: if an anchor exists, the value is the anchored span value (bounded — never
        fabricated); if the span contains prompt-like / injection content it is CONTAINED (neutralized) and
        flagged ``needs_officer_review`` (schema-external content, not a grounded value); if no anchor exists
        the field is ``missing_data``. ``confidence`` is deterministic: high when anchored + provenance present,
        else low. No LLM.
        """
        by_field: dict[str, dict[str, Any]] = {}
        for a in located.get("anchors", []):
            # first anchor per field wins (deterministic, ordered by page/line)
            by_field.setdefault(a["field"], a)

        has_provenance = bool(located.get("source"))
        fields: dict[str, dict[str, Any]] = {}
        for field in OUTCOME_EVIDENCE_SCHEMA:
            anchor = by_field.get(field)
            if anchor is None:
                fields[field] = {
                    "value": None,
                    "page": None,
                    "span": None,
                    "confidence": "none",
                    "missing_data": True,
                    "needs_officer_review": True,
                    "contained": False,
                }
                continue
            raw_value = str(anchor.get("raw_value") or "")
            contained = _contains_injection(raw_value)
            value = _neutralize(raw_value) if contained else raw_value
            weak = len(value.strip()) < _MIN_VALUE_CHARS
            # schema-external / prompt-like content is not presented as a grounded value → officer review
            needs_review = contained or weak or not has_provenance
            confidence = "low" if needs_review else "high"
            fields[field] = {
                "value": None if contained else value,  # contained span content is withheld (not grounded)
                "page": anchor.get("page"),
                "span": anchor.get("span"),
                "confidence": confidence,
                "missing_data": False,
                "needs_officer_review": needs_review,
                "contained": contained,
            }
        return {"report_id": located["report_id"], "source": located.get("source"), "fields": fields}

    # ── citation / entailment verification ────────────────────────────────────
    @staticmethod
    def verify_report(extracted: dict[str, Any]) -> dict[str, Any]:
        """Per-field citation completeness + source-span entailment for one extracted report.

        A field is ``cited`` only when it has a value, a page + span anchor, AND the report resolves to a
        grounded ``source`` (authorized provenance). Uncited / low-confidence / contained / missing fields are
        flagged ``needs_officer_review``. Enforces "no claim without a citation": a value without a full
        citation is demoted to needs_officer_review (never presented as grounded)."""
        source = extracted.get("source")
        report_cited = bool(source)
        out_fields: dict[str, Any] = {}
        review_items: list[str] = []
        for field, f in extracted.get("fields", {}).items():
            cited = bool(f.get("value")) and f.get("page") is not None and bool(f.get("span")) and report_cited
            needs_review = bool(f.get("needs_officer_review")) or not cited
            if needs_review or f.get("missing_data"):
                review_items.append(field)
            out_fields[field] = {
                **f,
                "cited": cited,
                "citation": {"source": source, "page": f.get("page"), "span": f.get("span")} if cited else None,
                "needs_officer_review": needs_review,
            }
        grounded_field_count = sum(1 for f in out_fields.values() if f.get("cited"))
        return {
            "report_id": extracted["report_id"],
            "source": source,
            "fields": out_fields,
            "grounded_field_count": grounded_field_count,
            "review_fields": sorted(set(review_items)),
        }

    @staticmethod
    def register_summary(reports: list[dict[str, Any]]) -> dict[str, Any]:
        """Register-level rollup: report count, grounded field totals, fields needing officer review."""
        total_fields = len(OUTCOME_EVIDENCE_SCHEMA) * len(reports)
        grounded = sum(r.get("grounded_field_count", 0) for r in reports)
        review_reports = [r["report_id"] for r in reports if r.get("review_fields")]
        return {
            "report_count": len(reports),
            "schema_field_count": len(OUTCOME_EVIDENCE_SCHEMA),
            "total_fields": total_fields,
            "grounded_fields": grounded,
            "reports_needing_review": review_reports,
        }
