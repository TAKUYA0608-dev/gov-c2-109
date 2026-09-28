# Test Specification — GOV-C2-109

## Test Strategy
- Coverage target: **80%+** (achieved **92%**, `--cov=src`)
- Test types: Unit (pre/post + inner nodes + service) / Unit (Cat 2 graph wiring + real invoke) / Integration / Proof-of-Boundary
- **Deterministic** template (schema-label span anchoring, no LLM) → outputs are exactly reproducible; no model mocking needed.

## Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | Result |
|-------|------|----------------|--------|
| TC-01 | State contract: flat TypedDict | `State(AgentState)`, NotRequired primitives + JSON strings; **no PII / My-Number** | ✅ PASS |
| TC-02 | S-2 rejection without raising | `_extra_security_gate_input` → `SUCCESS + error_code` (never raises, never `status=ERROR`) | ✅ PASS |
| TC-03 | No JWT/Credential in `src/` | `gate-credential-scan`: 0 violations | ✅ PASS |
| TC-05 | S-4: no duplicate lifecycle events | only domain events emitted | ✅ PASS |
| TC-06 | S-2 `_security_gate_input()` not overridden | `@final`; only `_extra_*` extended | ✅ (real SDK on CI; local-stub env-diff) |
| TC-07 | S-3 `_security_gate_output()` not overridden | `@final`; may raise via `_extra_*` | ✅ (real SDK on CI; local-stub env-diff) |
| TC-08 | `required_trust_level` enforced | VERIFIED_EXTERNAL on all 6 FunctionNode subclasses | ✅ PASS (`check_trust_level.py`) |
| TC-11 | S-4: ≥1 domain `emit_trace_event()` per `execute()` | emitted on every path incl. skip / 0-report / degraded | ✅ PASS |

> **injection contract (SDK 1.0.0):** a caller-instruction injection attempt / oversize degrades to
> `SUCCESS + error_code` (`INJECTION_REJECTED` / `INPUT_TOO_LONG`) with the body discarded, so `post_process`
> (S-3 + S-4 + disclaimer) always runs — it is **never** `status=ERROR`. Report-prose injection is a **separate,
> pre-LLM containment layer**: it is treated as quoted evidence (never rejected, never executed) and the field
> is neutralized + flagged `needs_officer_review`. Proof-tested independently of the S-3 output gate.

## Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Expected Result | Result |
|-------|----------|----------------|--------|
| PB-1 | `emit_trace_event()` fires from `shared.utils.audit_logger` (every path) | No silent failures | ✅ (real SDK on CI) |
| PB-2 | Post-invoke State is primitives only | No Pydantic/dataclass | ✅ PASS |
| PB-4 | Import isolation — no Level 0 imports | AST scan: 0 violations | ✅ PASS |
| PB-6 | Invoke order S-1 → S-4 → S-2 → execute → S-3 → S-4 | Order verified | ✅ (real SDK on CI; local-stub env-diff) |
| PB-7 | HITL interrupt propagation *(conditional)* | **Auto-waived — non-HITL** (`hitl.enabled` unset) | ⏭ 2 SKIPPED |
| Composition | Cat 2 `GraphNode`-in-main wraps inner `BaseGraph` (subgraph cached) | gate-composition passes | ✅ (S-0 gate) |

## Business Logic Tests

| BL-ID | Test | Input | Expected Result | Result |
|-------|------|-------|----------------|--------|
| BL-01 | Grounded register | reports w/ 7 labelled fields + authorized source | 7 fields cited (doc/page), citation_complete, DRAFT disclaimer | ✅ PASS |
| BL-02 | Missing fields → officer review | report with only 2 of 7 fields | missing fields flagged, `officer_review.required=True` | ✅ PASS |
| BL-03 | Out-of-scope | non-report NL question | `out_of_scope`, no citations | ✅ PASS |
| BL-04 | Empty input | "   " | degraded SUCCESS (`INPUT_REJECTED`), still audits | ✅ PASS |
| BL-05 | Caller-instruction injection | "ignore all previous instructions…" | degraded SUCCESS (`INJECTION_REJECTED`), out_of_scope, body discarded | ✅ PASS |
| BL-06 | Oversize input | >300 KB | degraded SUCCESS (`INPUT_TOO_LONG`), audits error_code | ✅ PASS |
| BL-07 | **Report-prose injection contained (F-01)** | valid report whose field value quotes an injection marker | NOT rejected; field contained (value withheld) + `needs_officer_review`; marker never in output | ✅ PASS |
| BL-08 | Missing provenance → fail-closed | grounded register, report has no `source` | `needs_review`, register body withheld, `CITATION_INCOMPLETE` | ✅ PASS |
| BL-09 | Forged surrogate source | `source="doc:1a2b3c4d"` / `"src:deadbeef"` | not grounded → `needs_review`, no citation, forged value absent | ✅ PASS |
| BL-10 | Unverifiable source | `source="unknown"` / a resident name | not a citation → `needs_review`, source absent | ✅ PASS |
| BL-11 | report_id PII tokenized | `report_id="Taro Yamada 090-…"` | tokenized `rpt:<sha8>`, name/phone never in output; referential integrity | ✅ PASS |
| BL-12 | No-space name report_id | `report_id="Alice"` / `"TaroYamada"` | still tokenized (no syntactic bypass) | ✅ PASS |
| BL-13 | **My-Number minimised pre-LLM** | 12-digit number in report text | never in `validated_input` / output | ✅ PASS |
| BL-14 | period / caller-field PII redacted (S-3) | period + arbitrary field carrying name/phone/email | redacted from grounded output | ✅ PASS |
| BL-15 | S-3 disclaimer preservation | any output | S-3 gate raises if the DRAFT / candidate disclaimer is missing | ✅ PASS |

## Test Execution Summary
- Total (`tests/`): **89** (unit 82 [nodes 41 + graph 35 + integration 6] + PB/framework-compliance 7)
- Pass: **83** · Skip: **3** (server import + PB-7 ×2 non-HITL) · env-diff: **3** (TC-06/TC-07 + PB-6 — pass on CI with the real SDK's `@final` gates; the local SDK stub does not enforce them)
- Coverage: **92%** (`--cov=src`; `src/api/server.py` is the standalone HTTP adapter, exercised on the platform)
