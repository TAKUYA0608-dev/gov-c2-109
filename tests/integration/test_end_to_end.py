# GOV-C2-109 — Integration: pre → inner workflow (linear) → post, and the real outer invoke path

import json

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import Graph
from src.nodes.citation_provenance_check_node import CitationProvenanceCheckNode
from src.nodes.evidence_extract_node import EvidenceExtractNode
from src.nodes.officer_review_gate_node import OfficerReviewGateNode
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.nodes.schema_field_locate_node import SchemaFieldLocateNode

_SUCCESS = AgentStatus.SUCCESS.value

_DATASET = {
    "period": "FY2024",
    "schema": {"fields": list(("target_process", "purpose", "evidence_source", "measured_outcome",
                               "constraints", "human_oversight", "evidence_gap"))},
    "reports": [
        {"report_id": "prog-017", "source": "gov_program_registry:prog-017", "resident_name": "山田太郎",
         "pages": [{"page": 1, "text": (
             "対象業務: 住民税還付申請の一次審査\n目的: 審査待ち時間の短縮\n"
             "エビデンス出典: 業務システムログ FY2024\n測定成果: 平均処理時間を12日から5日に短縮\n"
             "制約: 高額還付案件は対象外\n人的監督: 全件を職員が最終確認\n"
             "未解決のエビデンスギャップ: 年度末繁忙期の効果は未測定 個人番号 123456789012")}]},
        {"report_id": "prog-042", "source": "outcome_report:2024/042",
         "pages": [{"page": 2, "text": "目的: 問い合わせ対応の効率化\n測定成果: 一次回答率を60%から85%に改善"}]},
    ],
}


def _run(user_input: str) -> dict:
    state: dict = {"user_input": user_input, "input_context": {"channel": "outcome_console"},
                   "node_history": [], "error_log": []}
    state.update(PreProcessNode().execute(state) or {})
    for node in (SchemaFieldLocateNode(), EvidenceExtractNode(),
                 CitationProvenanceCheckNode(), OfficerReviewGateNode()):
        state.update(node.execute(state) or {})
    state.update(PostProcessNode().execute(state) or {})
    return state


class TestEndToEnd:
    def test_register_with_fields_and_citations(self):
        state = _run(json.dumps(_DATASET, ensure_ascii=False))
        assert state["status"] == _SUCCESS
        assert state["audit_logged"] is True
        env = json.loads(state["formatted_output"])
        assert env["status_kind"] == "outcome_evidence_register"
        assert env["summary"]["report_count"] == 2
        assert env["citations"]
        # report 042 has only 2 of 7 fields → needs officer review
        assert env["officer_review"]["required"] is True
        assert "DRAFT" in env["disclaimer"]

    def test_resident_pii_never_in_output(self):
        state = _run(json.dumps(_DATASET, ensure_ascii=False))
        assert "山田太郎" not in state["formatted_output"]
        assert "山田太郎" not in state["validated_input"]
        assert "123456789012" not in state["formatted_output"]  # My-Number minimised pre-LLM

    def test_grounded_field_values_cited(self):
        env = json.loads(_run(json.dumps(_DATASET, ensure_ascii=False))["formatted_output"])
        r017 = next(e for e in env["register"] if e["fields"]["measured_outcome"]["value"] is not None
                    and "12日" in e["fields"]["measured_outcome"]["value"])
        assert r017["fields"]["measured_outcome"]["cited"] is True
        assert r017["source_citation"].startswith("src:")

    def test_out_of_scope_safe(self):
        env = json.loads(_run("来年度のAI成果を教えて")["formatted_output"])
        assert env["status_kind"] == "out_of_scope"
        assert env["citations"] == []
        assert "DRAFT" in env["disclaimer"]

    def test_empty_degrades_but_audits(self):
        state = _run("   ")
        assert state["status"] == _SUCCESS
        assert state["audit_logged"] is True
        assert json.loads(state["formatted_output"])["status_kind"] == "out_of_scope"

    def test_real_invoke_end_to_end(self):
        ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        out = Graph().invoke(json.dumps(_DATASET, ensure_ascii=False), ctx=ctx)
        assert out["status"] == _SUCCESS
        assert "PostProcessNode" in out["node_history"]
        env = json.loads(out["output"])
        assert env["status_kind"] == "outcome_evidence_register"
        assert env["summary"]["report_count"] == 2

    def test_forged_report_id_surrogate_rehashed(self):
        # ★ F-02: a caller value SHAPED like an internal surrogate (rpt:deadbeef) is re-hashed at S-1 (no
        # syntactic passthrough), so it can never forge an internal join key / reference another report.
        payload = {"reports": [
            {"report_id": "rpt:deadbeef", "source": "gov_program_registry:prog-1",
             "pages": [{"page": 1, "text": "対象業務: 一次審査\n目的: 待ち時間短縮"}]},
        ]}
        ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        out = Graph().invoke(json.dumps(payload, ensure_ascii=False), ctx=ctx)
        env = json.loads(out["output"])
        assert env["status_kind"] == "outcome_evidence_register"
        tok = env["register"][0]["report_id"]
        assert tok.startswith("rpt:") and tok != "rpt:deadbeef"   # re-hashed, not passthrough
        assert "rpt:deadbeef" not in out["output"]
