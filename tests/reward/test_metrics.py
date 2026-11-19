"""Tests for the recorded-but-unscored metrics."""

from __future__ import annotations

import pytest
import verifiers.v1 as vf
from verifiers.v1.trace import ModelCall
from verifiers.v1.types import Usage

from contracts.builders import TraceBuilder, call, edge_payload
from neuro_grounded_qa.contracts import audit_retrieval, parse_output
from neuro_grounded_qa.reward import all_metrics, answer_metrics, score, trace_metrics

from .helpers import ITEMS, abstain, answer, claim, episode, evidence

SINGLE = ITEMS["fx-single-001"]
FALSEP_FIX = ITEMS["fx-falsep-002"]
GP1 = claim("GENE_A", "associated_with", "DIS_1", "E:000001")
GP2 = claim("GENE_B", "associated_with", "DIS_1", "E:000002")


def metrics_for(item, text, retrieved_ids):
    retrieved = evidence(*retrieved_ids)
    return answer_metrics(score(item, parse_output(text), retrieved), set(retrieved))


# --- Answer metrics -----------------------------------------------------------------


def test_answer_metrics_for_a_messy_answer():
    cite_everything = claim("GENE_A", "associated_with", "DIS_1", "E:000002", "E:000003", "E:000001")
    fabricated = claim("GENE_D", "associated_with", "DIS_1", "E:999999")
    text = answer(cite_everything, GP2, GP2, fabricated)
    m = metrics_for(SINGLE, text, ["E:000001", "E:000002", "E:000003", "E:000007"])
    assert m["claims"] == 4.0  # as written
    assert m["citations_per_claim"] == pytest.approx((3 + 1 + 1 + 1) / 4)
    # Facts after deduplication: GENE_A, GENE_B (twice) and GENE_D.
    assert m["supported_claims"] == 2.0
    assert m["unsupported_claims"] == 1.0
    assert m["duplicate_claims"] == 1.0
    assert m["voided_claims"] == 0.0
    assert m["stripped_cites"] == 1.0
    assert m["nonsupporting_valid_cites"] == 2.0  # E:000002 and E:000003 on claim 1
    assert m["retrieved_uncited"] == 1.0  # E:000007
    assert m["citation_validity"] == pytest.approx(5 / 6)
    assert m["claim_support"] == pytest.approx(2 / 3)
    assert m["coverage"] == 1.0
    assert m["unsupported_penalty"] == pytest.approx(0.5 / 3)


def test_voided_and_duplicate_claims_are_recorded():
    voided = claim("GENE_A", "associated_with", "DIS_1", "E:000001", "E:999999")
    reversed_gp2 = claim("DIS_1", "associated_with", "GENE_B", "E:000002")
    m = metrics_for(SINGLE, answer(voided, GP2, reversed_gp2), ["E:000001", "E:000002"])
    assert m["voided_claims"] == 1.0
    assert m["duplicate_claims"] == 1.0
    assert m["supported_claims"] == 1.0 and m["unsupported_claims"] == 1.0
    assert m["format_ok"] == 1.0 and m["unparseable"] == 0.0
    assert "premise_asserted" not in m


def test_undefined_ratios_are_left_out_for_an_abstention():
    m = metrics_for(SINGLE, abstain(), ["E:000001"])
    assert m["abstained"] == 1.0
    assert m["claims"] == 0.0
    assert m["retrieved_uncited"] == 1.0
    for key in ("citations_per_claim", "citation_validity", "claim_support"):
        assert key not in m
    assert m["coverage"] == 0.0


def test_premise_asserted_is_recorded_for_false_premise_items():
    premise = claim("DRUG_Y", "indicated_for", "DIS_2", "E:000006")
    assert metrics_for(FALSEP_FIX, answer(premise), ["E:000006"])["premise_asserted"] == 1.0
    assert metrics_for(FALSEP_FIX, abstain(), [])["premise_asserted"] == 0.0


def test_unparseable_rate_and_reasons():
    texts = [answer(GP1), "not json", "", abstain(), '{"claims": [], "answer_text": "x"}']
    ms = [metrics_for(SINGLE, t, ["E:000001"]) for t in texts]
    assert sum(m["unparseable"] for m in ms) / len(ms) == pytest.approx(3 / 5)
    assert [m["unparseable/not_json"] for m in ms] == [0, 1, 0, 0, 0]
    assert [m["unparseable/empty"] for m in ms] == [0, 0, 1, 0, 0]
    assert [m["unparseable/schema"] for m in ms] == [0, 0, 0, 0, 1]
    assert all("unparseable/mixed_answer_and_abstain" in m for m in ms)


# --- Trace metrics ------------------------------------------------------------------


def _messy_trace():
    """One accepted result plus one rejection of each kind, from three tools."""
    b = TraceBuilder(data=SINGLE)
    b.user("q")
    # Prompt-supplied exchange: call_not_sampled.
    b.assistant(None, [call("shot", "get_neighbors", entity_id="DIS_1")], sampled=False)
    b.tool("shot", edge_payload("E:000002"), name="get_neighbors")
    # Real calls: one evidence tool, one shell tool whose output isn't a payload.
    b.assistant(
        None,
        [call("c1", "get_edge", edge_id="E:000001"), call("c2", "bash", cmd="ls")],
    )
    b.tool("c1", edge_payload("E:000001"), name="get_edge")
    b.tool("c2", "README.md\n", name="bash")
    # A second result for c1: duplicate_result.
    b.tool("c1", edge_payload("E:000002"), name="get_edge")
    # A result nobody asked for, with no name: no_matching_call.
    b.tool("ghost", edge_payload("E:000003"))
    # A result on a sibling branch of the real call: not_descendant.
    b.tool("c1", edge_payload("E:000004"), name="get_edge", parent=0)
    b.assistant(answer(GP1), parent=5)
    return b.build()


def test_rejection_counts_per_reason_and_per_tool_name():
    trace = _messy_trace()
    m = trace_metrics(trace, audit_retrieval(trace))
    assert m["tool_calls"] == 2.0
    assert m["tool_calls/get_edge"] == 1.0 and m["tool_calls/bash"] == 1.0
    assert "tool_calls/get_neighbors" not in m  # the prompt-supplied call isn't the model's
    assert m["tool_results_accepted"] == 1.0
    assert m["tool_results_rejected"] == 5.0
    assert m["rejected/call_not_sampled"] == 1.0
    assert m["rejected/unparseable"] == 1.0
    assert m["rejected/duplicate_result"] == 1.0
    assert m["rejected/no_matching_call"] == 1.0
    assert m["rejected/not_descendant"] == 1.0
    assert m["rejected/call_not_sampled/get_neighbors"] == 1.0
    assert m["rejected/unparseable/bash"] == 1.0
    assert m["rejected/duplicate_result/get_edge"] == 1.0
    assert m["rejected/no_matching_call/unknown"] == 1.0
    assert m["rejected/not_descendant/get_edge"] == 1.0
    assert m["duplicate_call_ids"] == 1.0
    assert m["retrieved_evidence"] == 1.0


def test_every_rejection_reason_key_is_present_on_a_clean_trace():
    trace = episode(SINGLE, answer(GP1), ["E:000001"])
    m = trace_metrics(trace, audit_retrieval(trace))
    for reason in ("no_matching_call", "call_not_sampled", "not_descendant",
                   "duplicate_result", "unparseable"):
        assert m[f"rejected/{reason}"] == 0.0
    assert not [k for k in m if k.count("/") == 2]


def test_tokens_recorded_only_when_usage_is_available():
    trace = episode(SINGLE, answer(GP1), ["E:000001"])
    m = trace_metrics(trace, audit_retrieval(trace))
    assert "input_tokens" not in m and "output_tokens" not in m

    last = len(trace.nodes) - 1
    trace.calls.append(
        ModelCall(node=last, usage=Usage(prompt_tokens=100, completion_tokens=40, cached_input_tokens=20))
    )
    m = trace_metrics(trace, audit_retrieval(trace))
    assert m["input_tokens"] == 120.0
    assert m["output_tokens"] == 40.0


def test_all_metrics_merges_both_sets():
    trace = episode(SINGLE, answer(GP1, GP2), ["E:000001", "E:000002"])
    m = all_metrics(trace, SINGLE)
    assert m["coverage"] == 1.0 and m["tool_calls"] == 2.0
    assert all(isinstance(v, float) for v in m.values())


def test_metrics_record_on_a_trace():
    trace = episode(SINGLE, answer(GP1), ["E:000001"])
    trace.record_metrics(all_metrics(trace, SINGLE))
    assert isinstance(trace, vf.Trace) and trace.metrics["coverage"] == 0.5
