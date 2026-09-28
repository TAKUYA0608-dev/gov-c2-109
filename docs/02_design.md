# Template Design Specification — GOV-C2-109

Government AI Use-Case Outcome Evidence Extractor (Cat 2).

## Position in AgentCore Architecture

- **Agent Class**: `GovernmentAIUseCaseOutcomeAgent` (module-level alias of `Graph`)
- **L1 Base**: **AgentBaseGraph** (Cat 2 — outer 5-node backbone; direct L1 inheritance, no L2).
  `DocGenerationAgent` is a §5/§10 *pattern reference only*; implementation inherits AgentBaseGraph directly
  and confines the OutcomeEvidenceExtract workflow inside this template (2026-05-18 L2 retirement).
- **Category**: Cat 2 — a multi-step domain workflow (schema-field locate → deterministic bounded extract →
  citation/provenance check → officer-review gate) producing a candidate outcome-evidence register; GOV industry
- **Three-Layer Separation**: State = flat TypedDict; Node = L1 inheritance (`execute(self, state: dict) -> dict`
  override only, no `config` param per the node contract); Graph = outer `AgentBaseGraph` + **`GraphNode` in the `main`
  slot** wrapping an inner `BaseGraph`

## Architecture Overview

Cat 2 pattern — the `main` slot is a **`GraphNode`** (`OutcomeEvidenceWorkflowGraphNode`, **subgraph cached** on the **class attribute**
(`OutcomeEvidenceWorkflowGraphNode._subgraph`, not `self` — avoids mutable node-instance state per §9)) that wraps the inner `OutcomeEvidenceWorkflow` (`BaseGraph`). The inner graph is a
**static linear backbone with per-node skip guards** (conditional edges do not propagate across the subgraph
boundary). **Deterministic (token/label anchoring + KB-free span composition, no LLM)** — there is no model in
`config/agent.yaml` and no LLM dependency; the extraction is span-anchored, never generative.

### Node Configuration

| Node | Responsibility | Input State | Output State | Inherits/Overrides |
|------|---------------|-------------|--------------|-------------------|
| initialize | schema_version, session_id, trust_level | user_input | (framework) | InitializeNode (default) |
| pre_process | `PreProcessNode` (ReportIntake + SensitiveDataDetectAndMinimise) — S-1 normalize + reporting-period/metadata validate, **pre-LLM S-2 PII/My-Number minimise + injection containment**, source-provenance resolve | user_input | validated_input, input_format, enriched_context | FunctionNode.execute |
| main | `OutcomeEvidenceWorkflowGraphNode` (GraphNode) → inner workflow | validated_input | result, report_count, human_review_required | GraphNode |
| post_process | `PostProcessNode` (EvidenceRegisterCompose) — S-3 citation fail-closed + injection-marker neutralize + re-redact + mandatory candidate disclaimer + S-4 audit | result | formatted_output, disclaimer, audit_logged | FunctionNode.execute |
| finalize | response_metadata, total_time_ms | | (framework) | FinalizeNode (default) |

**Inner workflow (`OutcomeEvidenceWorkflow` : BaseGraph):**

```
START → schema_field_locate → evidence_extract → citation_provenance_check → officer_review_gate → END
```

| Inner node | Responsibility |
|---|---|
| SchemaFieldLocate | deterministic anchor of the 7 government schema fields to doc/page provenance; 0 reports → out-of-scope safe answer |
| EvidenceExtract | **deterministic** bounded extraction (schema-label anchoring, no LLM): value + doc/page/span; unlabelled field → `missing_data`; prompt-like / schema-external span content → **contained** (never followed) + `needs_officer_review` |
| CitationProvenanceCheck | deterministic per-field citation completeness + source-span entailment; uncited / low-confidence / unentailed → `needs_officer_review`; enforces "no claim without a citation" |
| OfficerReviewGate | mandatory human gate — flags fields/register requiring an authorized officer's sign-off, sets `human_review_required`; never approves an outcome or assigns a real owner |

### Data Flow

```
START → initialize → pre_process → main(GraphNode → inner linear workflow) → post_process → finalize → END
                                     ↓ (retry, max 3)
                                   pre_process
```

Rejected / oversize / injection input never sets `status=ERROR`; it degrades to `SUCCESS + error_code` and the
offending body is discarded, so `main`/`post_process` still run (out-of-scope safe answer + disclaimer + S-3 +
S-4). On 0-report or rejected input, `schema_field_locate` sets `report_count=0` (+error_code); the downstream
inner nodes no-op and `citation_provenance_check` emits the out-of-scope safe answer — no fabricated evidence.

### State Definition

| Field | Type | Purpose |
|-------|------|---------|
| validated_input | str (JSON) | `{reports[], schema, period}` — PII minimised, `source` resolved to a grounded citation or None |
| located_evidence / report_count | str/int | schema-field anchors per report / 0 → out-of-scope safe answer |
| extracted_fields | str (JSON) | per-report field extractions (value, page, span, confidence, missing_data, needs_officer_review) |
| result / formatted_output | str (JSON) | inner candidate register / final envelope |
| human_review_required / review_status | bool/str | officer-review gate flag + status |
| disclaimer / audit_logged | str/bool | mandatory candidate disclaimer + terminal audit |
| error_code / error_message | str | degraded path (SUCCESS + error_code, never status=ERROR) |

**State Constraints:** flat TypedDict; JSON strings for complex fields (ADR-005); **no PII / My-Number / credentials
in State** — minimised pre-LLM at S-2 before `validated_input` is written; `enriched_context` is a JSON string.

## Framework Utilization

- [x] **GraphNode-in-main** (Cat 2 composition, criterion #9) — `error_strategy="propagate"`, `propagate_hitl=False`, **subgraph cached** on the **class attribute** (`OutcomeEvidenceWorkflowGraphNode._subgraph`, not `self` — avoids mutable node-instance state per §9)
- [x] S-1 `required_trust_level=VERIFIED_EXTERNAL` on all FunctionNode subclasses (pre/post + 4 inner nodes)
- [x] **pre-LLM S-2** `_extra_security_gate_input()` (pre) — size cap + injection markers; **returns `dict(state)`, never raises, never `status=ERROR`** (a rejection degrades to `SUCCESS + error_code`; the body is discarded by `execute()`) (SDK 1.0.0)
- [x] **pre-LLM containment (separate layer from S-3)** — resident/staff PII + My-Number (12 digit) + credential/email/phone minimised, and report-prose injection markers treated as **quoted evidence** (never executed); prompt-like / schema-external span content → `needs_officer_review`. Proof-tested separately from the S-3 output gate.
- [x] S-3 `_extra_security_gate_output()` (post) — mandatory-disclaimer preservation; **may raise** (SDK 1.0.0); execute() additionally enforces citation fail-closed + re-redaction + injection-marker neutralization
- [x] S-4 `emit_trace_event()` in **every** `execute()` including every skip / 0-report / degraded path (aggregate counts / error_code only — no PII, no raw outcome value); terminal audit always fires

## Import Isolation Confirmation
- [x] No `agenticstar` SDK (Level 0) import — PB-4
- [x] Import targets: `framework/`, `langgraph`, and `src.` only

## Design Decision Record

| Decision | Chosen | Rationale |
|----------|--------|-----------|
| L1 base type | AgentBaseGraph | Fixed pipeline, no autonomous loop |
| Composition | **GraphNode-in-main + inner BaseGraph (cached)** | Cat 2 multi-step domain workflow |
| Inner topology | **Linear + per-node skip guards** | Conditional edges don't propagate across the subgraph boundary |
| Extraction | **Deterministic — schema-label span anchoring (no LLM)** | Auditable, reproducible; every value is anchored to a doc/page/span and cited |
| PII / injection | **Minimised pre-LLM at S-2 + report-prose containment; re-redact at S-3** | Public/PII/policy high-risk; PII never enters State; injection text is quoted evidence, not instructions |
| Provenance | **`source` resolved once at S-1** to an authorized system of record → grounded citation, else None → S-3 fail-closed | No claim without a verifiable citation; forged surrogates dropped |
| Officer review | **Mandatory OfficerReviewGate** flags material items; final verdict is a human's | Read-only candidate; agent never approves / publishes / procures |
| Rejection signalling | SUCCESS + error_code | Guarantees post_process S-3/S-4 always run (SDK 1.0.0) |

## Open Items (Stage ③ implementation MR)
- Node implementations + inner workflow graph (shipped in the implementation MR).
- Seeded government outcome-evidence schema (7 fields) + deterministic schema-label lexicon (CoE-calibratable).
- Unit + integration + PB tests; coverage ≥ 80% (target ≥ 89%).
