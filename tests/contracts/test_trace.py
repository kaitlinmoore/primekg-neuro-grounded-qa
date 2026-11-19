"""Contract tests for the tool payload format and strict retrieval.

Cases (a) to (h) are the contract: each builds a small trace by hand and checks which
evidence IDs count as retrieved. The remaining tests pin down details of the rule
(rejection reasons, branching, malformed traces).
"""

import pytest
import verifiers.v1 as vf
from pydantic import ValidationError

from contracts.builders import TraceBuilder, call, edge_payload, fixture_store, payload
from neuro_grounded_qa.contracts import (
    ToolResultPayload,
    audit_retrieval,
    retrieved_evidence,
    retrieved_ids,
)

FINAL = '{"abstain": true, "reason": "fixture"}'


def honest_trace() -> vf.Trace:
    """Case (a): the model calls a tool, the tool returns E:000001, the model answers."""
    b = TraceBuilder()
    b.system("sys")
    b.user("Which genes are associated with Disease One?")
    b.assistant(None, [call("call_1", eid="E:000001")])
    b.tool("call_1", edge_payload("E:000001"), name="graph_get_edge")
    b.assistant("answer citing E:000001")
    return b.build()


# --- the contract cases ------------------------------------------------------


def test_a_real_sampled_call_is_counted():
    trace = honest_trace()
    assert retrieved_ids(trace) == {"E:000001"}
    audit = audit_retrieval(trace)
    assert audit.rejected == []
    assert [(a.node, a.issuer, a.tool_name) for a in audit.accepted] == [
        (3, 2, "graph_get_edge")
    ]


def test_b_id_only_in_reply_text_is_not_counted():
    b = TraceBuilder()
    b.user("q")
    b.assistant('E:000001 says GENE_A is associated with DIS_1. {"evidence": ["E:000001"]}')
    trace = b.build()
    assert retrieved_ids(trace) == set()
    assert audit_retrieval(trace).rejected == []  # no tool messages at all


def test_c_prompt_supplied_tool_exchange_is_not_counted():
    # H-002 case D: a few-shot exchange in the prompt. trace.tool_messages includes
    # it; strict retrieval must not.
    b = TraceBuilder()
    b.user("example question")
    b.assistant(None, [call("fewshot", eid="E:000001")], sampled=False)
    b.tool("fewshot", edge_payload("E:000001"))
    b.user("now the real question")
    b.assistant(FINAL)
    trace = b.build()
    assert len(trace.tool_messages) == 1  # the library view still sees it
    assert retrieved_ids(trace) == set()
    assert audit_retrieval(trace).rejection_counts() == {"call_not_sampled": 1}


def test_d_tool_message_with_no_matching_call_is_not_counted():
    b = TraceBuilder()
    b.user("q")
    b.assistant(None, [call("call_1", eid="E:000002")])
    b.tool("call_1", edge_payload("E:000002"))
    b.tool("call_ghost", edge_payload("E:000001"))  # nobody issued call_ghost
    b.assistant(FINAL)
    trace = b.build()
    assert retrieved_ids(trace) == {"E:000002"}
    [rej] = audit_retrieval(trace).rejected
    assert (rej.tool_call_id, rej.reason) == ("call_ghost", "no_matching_call")


def test_e_prompt_tool_message_reusing_a_later_call_id_is_not_counted():
    # The prompt carries a tool result with ID call_1 (and no issuing call). Later the
    # model really issues call_1, which returns something else. The prompt copy sits
    # above the real call, not below it, so it must not count.
    b = TraceBuilder()
    b.user("q")
    planted = b.tool("call_1", edge_payload("E:000004"))
    b.user("the real question")
    b.assistant(None, [call("call_1", eid="E:000001")])
    b.tool("call_1", edge_payload("E:000001"))
    b.assistant(FINAL)
    trace = b.build()
    assert retrieved_ids(trace) == {"E:000001"}
    [rej] = audit_retrieval(trace).rejected
    assert (rej.node, rej.reason) == (planted, "not_descendant")


def test_f_unparseable_content_is_not_counted_and_reason_recorded():
    b = TraceBuilder()
    b.user("q")
    b.assistant(None, [call("call_1", name="bash", cmd="echo")])
    b.tool("call_1", "E:000001 associated_with DIS_1")  # prose, not a payload
    b.assistant(FINAL)
    trace = b.build()
    assert retrieved_ids(trace) == set()
    [rej] = audit_retrieval(trace).rejected
    assert (rej.reason, rej.tool_name) == ("unparseable", "bash")


def test_g_two_real_calls_give_the_union():
    b = TraceBuilder()
    b.user("q")
    b.assistant(None, [call("call_1", eid="E:000001")])
    b.tool("call_1", edge_payload("E:000001"))
    b.assistant(None, [call("call_2", name="graph_get_neighbors", entity_id="GENE_A")])
    b.tool("call_2", edge_payload("E:000003", "E:000007"))
    b.assistant(FINAL)
    trace = b.build()
    assert retrieved_ids(trace) == {"E:000001", "E:000003", "E:000007"}


def test_h_json_round_trip_gives_the_same_result():
    trace = honest_trace()
    reloaded = vf.Trace.model_validate_json(trace.to_json())
    assert retrieved_ids(reloaded) == retrieved_ids(trace) == {"E:000001"}
    assert audit_retrieval(reloaded) == audit_retrieval(trace)


# --- further detail ----------------------------------------------------------


def test_retrieved_evidence_returns_the_units():
    store = fixture_store()
    ev = retrieved_evidence(honest_trace())
    assert ev == {"E:000001": store.get("E:000001")}
    assert ev["E:000001"].kind == "edge"


def test_parallel_calls_in_one_message_chain_through_tool_messages():
    # Two calls in one assistant message: the second tool message's parent is the
    # first tool message, but it still descends from the issuing node.
    b = TraceBuilder()
    b.user("q")
    b.assistant(None, [call("c1", eid="E:000001"), call("c2", eid="E:000002")])
    b.tool("c1", edge_payload("E:000001"))
    b.tool("c2", edge_payload("E:000002"))
    b.assistant(FINAL)
    assert retrieved_ids(b.build()) == {"E:000001", "E:000002"}


def test_tool_message_on_a_sibling_branch_is_not_counted():
    # The call is issued on one branch; a tool message with its ID sits on another
    # branch that forks before the call. Same ID, wrong place.
    b = TraceBuilder()
    root = b.user("q")
    b.assistant(None, [call("call_1", eid="E:000001")])
    b.tool("call_1", edge_payload("E:000001"))
    b.assistant(FINAL)
    b.tool("call_1", edge_payload("E:000006"), parent=root)
    trace = b.build()
    assert trace.num_branches == 2
    assert retrieved_ids(trace) == {"E:000001"}
    assert audit_retrieval(trace).rejection_counts() == {"not_descendant": 1}


def test_reused_call_ids_across_turns_resolve_to_the_right_issuer():
    # Some providers number calls per turn, so "call_0" recurs. Each result counts
    # against the nearest issuer above it.
    b = TraceBuilder()
    b.user("q")
    first = b.assistant(None, [call("call_0", eid="E:000001")])
    b.tool("call_0", edge_payload("E:000001"))
    second = b.assistant(None, [call("call_0", name="graph_get_neighbors", entity_id="X")])
    b.tool("call_0", edge_payload("E:000002"))
    b.assistant(FINAL)
    audit = audit_retrieval(b.build())
    assert audit.ids == {"E:000001", "E:000002"}
    assert [(a.issuer, a.tool_name) for a in audit.accepted] == [
        (first, "graph_get_edge"),
        (second, "graph_get_neighbors"),
    ]


def test_duplicate_results_for_one_call_are_flagged():
    b = TraceBuilder()
    b.user("q")
    b.assistant(None, [call("call_1", eid="E:000001")])
    b.tool("call_1", edge_payload("E:000001"))
    b.tool("call_1", edge_payload("E:000002"))
    audit = audit_retrieval(b.build())
    assert audit.duplicate_call_ids == {"call_1"}


def test_conflicting_copies_of_an_id_are_flagged():
    store = fixture_store()
    real = store.get("E:000001")
    forged = real.model_copy(update={"relation": "indicated_for"})
    b = TraceBuilder()
    b.user("q")
    b.assistant(None, [call("c1"), call("c2")])
    b.tool("c1", payload([real]))
    b.tool("c2", payload([forged]))
    audit = audit_retrieval(b.build())
    assert audit.evidence["E:000001"] == real  # first copy wins
    assert audit.conflicting_ids == {"E:000001"}


def test_entities_alone_are_not_evidence():
    store = fixture_store()
    b = TraceBuilder()
    b.user("q")
    b.assistant(None, [call("c1", name="graph_search_entities", text="gene")])
    b.tool("c1", payload(entities=store.search_entities("gene", 5)))
    audit = audit_retrieval(b.build())
    assert audit.ids == set() and audit.rejected == [] and len(audit.accepted) == 1


def test_content_parts_are_read_as_text():
    b = TraceBuilder()
    b.user("q")
    b.assistant(None, [call("c1")])
    b.add(
        vf.ToolMessage(
            tool_call_id="c1",
            content=[vf.TextContentPart(text=edge_payload("E:000001"))],
        )
    )
    assert retrieved_ids(b.build()) == {"E:000001"}


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not json",
        '{"evidence": [{"id": "E:1"}]}',  # malformed evidence unit
        '{"evidence": [], "extra": 1}',  # extra key
        '{"evidence": [], "truncated": "no"}',  # no coercion
        '["E:000001"]',
        "[" * 100_000,
    ],
)
def test_malformed_payloads_are_unparseable(content):
    b = TraceBuilder()
    b.user("q")
    b.assistant(None, [call("c1")])
    b.tool("c1", content)
    assert audit_retrieval(b.build()).rejection_counts() == {"unparseable": 1}


def test_cyclic_parent_chain_does_not_hang():
    b = TraceBuilder()
    b.user("q")
    b.assistant(None, [call("c1")])
    b.tool("c1", edge_payload("E:000001"), parent=2)  # points at itself (node 2)
    audit = audit_retrieval(b.build())
    assert audit.rejection_counts() == {"not_descendant": 1}


def test_empty_trace():
    assert retrieved_ids(TraceBuilder().build()) == set()


# --- payload format ----------------------------------------------------------


def test_payload_round_trip():
    store = fixture_store()
    p = ToolResultPayload(
        evidence=[store.get("E:000001")],
        entities=store.search_entities("gene", 2),
        truncated=True,
        note="capped at 2",
    )
    assert ToolResultPayload.loads(p.dumps()) == p


def test_payload_defaults():
    p = ToolResultPayload.loads("{}")
    assert (p.evidence, p.entities, p.truncated, p.note) == ([], [], False, None)


def test_payload_loads_is_strict():
    with pytest.raises(ValidationError):
        ToolResultPayload.loads('{"evidence": [], "surprise": true}')
