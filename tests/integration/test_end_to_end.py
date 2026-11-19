"""End to end with no model: ``scripts/run.sh`` against the scripted stub endpoint.

Each test runs one policy through the real pipeline: the taskset loads the fixture
items, the ``null`` harness runs its chat loop against the stub (``-m stub/<policy>``),
the KG tool server answers the tool calls from the fixture store, and the grounding
reward scores the trace. The assertions read ``traces.jsonl`` as written to disk.

Marked ``integration``: each policy is a real ``vf-run`` (a few seconds each). Skip
them with ``uv run pytest -m "not integration"``. They need ``uv`` and the project
venv, like the ``run.sh`` dry-run tests.

Run outputs go to a pytest temporary directory, never ``outputs/``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import verifiers.v1 as vf

from neuro_grounded_qa.contracts import QuestionItem, audit_retrieval
from neuro_grounded_qa.reward import DEFAULT_PARAMS, evaluate

from .stub_endpoint import StubEndpoint, Turn, calls, final, scripted, tool_call

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("uv") is None or not os.environ.get("UV_PROJECT_ENVIRONMENT"),
        reason="needs uv and the project venv (UV_PROJECT_ENVIRONMENT)",
    ),
]

REPO = Path(__file__).resolve().parents[2]
ITEMS_PATH = REPO / "tests" / "fixtures" / "contracts" / "items.jsonl"
API_KEY_VAR = "NGQ_STUB_API_KEY"


def _items() -> dict[str, QuestionItem]:
    lines = ITEMS_PATH.read_text().splitlines()
    items = [QuestionItem.model_validate_json(line) for line in lines if line.strip()]
    return {item.id: item for item in items}


ITEMS = _items()
SINGLE = ITEMS["fx-single-001"]
ABSTAIN = json.dumps({"abstain": True, "reason": "the evidence doesn't support an answer"})


def _claim(s: str, r: str, o: str, *cites: str) -> dict[str, Any]:
    return {"subject_id": s, "relation": r, "object_id": o, "cites": list(cites)}


def _answer(*claims: dict[str, Any], unsupported: list[str] | None = None) -> str:
    body: dict[str, Any] = {"claims": list(claims), "answer_text": "see claims"}
    if unsupported:
        body["unsupported_parts"] = unsupported
    return json.dumps(body)


# --- Policies ------------------------------------------------------------------------


def _honest_script(item: QuestionItem) -> list[Turn]:
    """Abstain where the item says to. Otherwise fetch every gold edge (parallel calls
    in one turn), then claim every gold point citing it, flagging the rest on partial
    items."""
    if item.answerable == "no":
        return [final(ABSTAIN)]
    fetch = calls(*(tool_call("kg_get_edge", edge_id=eid) for eid in item.gold_evidence))
    claims = [_claim(gp.subject_id, gp.relation, gp.object_id, *gp.evidence) for gp in item.gold_points]
    flags = ["the rest of the question isn't in the graph"] if item.answerable == "partial" else None
    return [fetch, final(_answer(*claims, unsupported=flags))]


HONEST = scripted({item.prompt_text: _honest_script(item) for item in ITEMS.values()})

ALWAYS_ABSTAIN = scripted({}, default=[final(ABSTAIN)])

# Retrieves both gold edges, then cites one of them alongside a fabricated ID (voided
# under strict citations) and adds a claim citing a real edge it never retrieved.
FABRICATED = scripted(
    {
        SINGLE.prompt_text: [
            calls(
                tool_call("kg_get_edge", edge_id="E:000001"),
                tool_call("kg_get_edge", edge_id="E:000002"),
            ),
            final(
                _answer(
                    _claim("GENE_A", "associated_with", "DIS_1", "E:000001"),
                    _claim("GENE_B", "associated_with", "DIS_1", "E:000002", "E:999999"),
                    _claim("GENE_C", "associated_with", "DIS_1", "E:000003"),
                )
            ),
        ]
    }
)

# Ten get_edge calls, one per turn, against a budget of 8; the gold edges come first,
# then the honest answer.
OVER_BUDGET_IDS = [f"E:{i:06d}" for i in range(1, 11)]
OVER_BUDGET = scripted(
    {
        SINGLE.prompt_text: [
            *(calls(tool_call("kg_get_edge", edge_id=eid)) for eid in OVER_BUDGET_IDS),
            final(
                _answer(
                    _claim("GENE_A", "associated_with", "DIS_1", "E:000001"),
                    _claim("GENE_B", "associated_with", "DIS_1", "E:000002"),
                )
            ),
        ]
    }
)

POLICIES = {
    "honest": HONEST,
    "abstain": ALWAYS_ABSTAIN,
    "fabricated": FABRICATED,
    "over_budget": OVER_BUDGET,
}

RUN_ITEMS: dict[str, tuple[str, ...]] = {
    "honest": (),  # every fixture item
    "abstain": (),
    "fabricated": (SINGLE.id,),
    "over_budget": (SINGLE.id,),
}
"""Which items each policy runs on (``--select.include.ids``); empty means all."""


# --- Running ---------------------------------------------------------------------------


class Runner:
    """Runs each policy once through ``scripts/run.sh`` and keeps its traces."""

    def __init__(self, stub: StubEndpoint, out: Path) -> None:
        self.stub = stub
        self.out = out
        self._cache: dict[str, dict[str, dict[str, Any]]] = {}

    def __call__(self, policy: str) -> dict[str, dict[str, Any]]:
        """Traces of the policy's run (once per module), as saved in
        ``traces.jsonl``, keyed by item ID."""
        if policy not in self._cache:
            self._cache[policy] = self._run(policy, RUN_ITEMS[policy])
        return self._cache[policy]

    def _run(self, policy: str, item_ids: tuple[str, ...]) -> dict[str, dict[str, Any]]:
        out = self.out / policy
        args = [
            "bash", str(REPO / "scripts" / "run.sh"),
            "-m", f"stub/{policy}",
            "--client.base-url", self.stub.base_url,
            "--client.api-key-var", API_KEY_VAR,
            "-o", str(out),
        ]
        if item_ids:
            args += ["--select.include.ids", *item_ids]
        env = {**os.environ, API_KEY_VAR: "stub-not-a-secret"}
        result = subprocess.run(
            args, cwd=REPO, env=env, capture_output=True, text=True, timeout=600
        )
        assert result.returncode == 0, (result.stdout[-2000:], result.stderr[-4000:])
        assert not self.stub.errors, self.stub.errors
        [path] = out.glob("*/traces.jsonl")
        traces: dict[str, dict[str, Any]] = {}
        for line in path.read_text().splitlines():
            episode = json.loads(line)
            [trace] = episode["traces"]
            traces[trace["task"]["data"]["id"]] = trace
        return traces


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory):
    with StubEndpoint(POLICIES) as stub:
        yield Runner(stub, tmp_path_factory.mktemp("runs"))


def reward(trace: dict[str, Any]) -> float:
    return trace["rewards"]["grounding_reward"]["score"]


def tool_messages(trace: dict[str, Any]) -> list[dict[str, Any]]:
    return [n["message"] for n in trace["nodes"] if n.get("message", {}).get("role") == "tool"]


# --- Tests -----------------------------------------------------------------------------


def test_honest_policy_scores_one_on_every_item(run):
    traces = run("honest")
    assert set(traces) == set(ITEMS)
    for item_id, trace in traces.items():
        assert trace["ok"], item_id
        assert reward(trace) == 1.0, (item_id, trace["metrics"])
        assert trace["metrics"]["format_ok"] == 1.0
    single = traces["fx-single-001"]["metrics"]
    assert single["coverage"] == 1.0
    assert single["tool_calls/kg_get_edge"] == 2.0
    assert single["retrieved_evidence"] == 2.0
    assert single["tool_results_rejected"] == 0.0


def test_always_abstain_scores_by_item_type(run):
    expected = {"yes": 0.0, "partial": 0.0, "no": 1.0, "correctable": 0.7}
    traces = run("abstain")
    assert set(traces) == set(ITEMS)
    for item_id, trace in traces.items():
        assert reward(trace) == pytest.approx(expected[ITEMS[item_id].answerable]), item_id
        assert trace["metrics"]["abstained"] == 1.0


def test_fabricated_citations_are_stripped_and_penalized(run):
    trace = run("fabricated")[SINGLE.id]
    m = trace["metrics"]
    assert m["retrieved_evidence"] == 2.0
    assert m["stripped_cites"] == 2.0  # E:999999 (fabricated), E:000003 (never retrieved)
    assert m["voided_claims"] == 1.0  # the GENE_B claim, despite its valid E:000002
    assert (m["supported_facts"], m["unsupported_facts"]) == (1.0, 2.0)
    assert m["coverage"] == 0.5
    assert m["unsupported_penalty"] == pytest.approx(0.5 * 2 / 3)
    assert reward(trace) == pytest.approx(0.5 - 0.5 * 2 / 3)


def test_over_budget_calls_get_budget_notes(run):
    trace = run("over_budget")[SINGLE.id]
    results = [json.loads(m["content"]) for m in tool_messages(trace)]
    assert len(results) == 10
    served, refused = results[:8], results[8:]
    assert [r["evidence"][0]["id"] for r in served] == OVER_BUDGET_IDS[:8]
    for r in refused:
        assert r["evidence"] == []
        assert "budget used up" in r["note"]
    m = trace["metrics"]
    assert m["tool_calls"] == 10.0
    assert m["retrieved_evidence"] == 8.0
    assert m["retrieved_uncited"] == 6.0
    assert reward(trace) == 1.0  # the gold edges came before the budget ran out


def test_metrics_and_provenance_are_in_traces(run):
    items_sha = hashlib.sha256(ITEMS_PATH.read_bytes()).hexdigest()
    for policy in POLICIES:
        traces = run(policy)
        assert len(traces) == (len(RUN_ITEMS[policy]) or len(ITEMS))
        for trace in traces.values():
            assert trace["info"]["store_build_id"] == "fixture-mini-store-v1"
            assert trace["info"]["items_sha256"] == items_sha
            assert trace["info"]["reward_params"] == {
                "lam": 0.5,
                "partial_flag_credit": 0.2,
                "correctable_abstain_credit": 0.7,
                "flag_only_counts": True,
            }
            m = trace["metrics"]
            for key in ("format_ok", "unparseable", "claims", "duplicate_claims",
                        "voided_claims", "stripped_cites", "tool_calls",
                        "retrieved_evidence", "rejected/unparseable",
                        "root_reply_present", "root_reply_differs"):
                assert key in m, (policy, key)
            # The null harness reports no root_reply; the scored reply is the model's.
            assert m["root_reply_present"] == 0.0 and m["root_reply_differs"] == 0.0


def test_null_harness_exposes_only_the_kg_tools(run):
    run("honest")
    tool_names = {
        t["function"]["name"]
        for body in run.stub.requests
        for t in body.get("tools") or []
    }
    assert tool_names == {"kg_search_entities", "kg_get_neighbors", "kg_get_edge"}


def test_saved_trace_rescores_to_the_recorded_reward(run):
    """The trace on disk holds everything the reward reads: reloading it and scoring
    it offline gives the recorded reward, and strict retrieval accepts every tool
    result."""
    for policy in POLICIES:
        for item_id, saved in run(policy).items():
            trace = vf.Trace.model_validate(saved)
            assert evaluate(trace, ITEMS[item_id], DEFAULT_PARAMS).breakdown.reward == pytest.approx(
                reward(saved)
            )
            assert audit_retrieval(trace).rejected == []
