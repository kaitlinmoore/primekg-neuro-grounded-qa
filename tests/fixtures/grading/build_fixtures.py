"""Build the grading tools' test fixtures (synthetic; no real data).

    uv run python tests/fixtures/grading/build_fixtures.py

Writes, next to this file:

- ``store.json``: a small evidence store with undirected edges, one plain directed
  edge and one directed edge with a grouped (``group:``) end, both labelled
  ``parent-child`` as PrimeKG labels them.
- ``items/train.jsonl``: 20 train items, five per ``answerable`` value, with sentinel
  ``template_id`` and ``notes``.
- ``runs/parent-child/<run>/`` and ``runs/direction-labels/<run>/``: one trace per
  item in the on-disk ``vf-run`` format, from two sentinel "models". The first run
  serves directed edges with PrimeKG's label (like train run 3), the second with
  ``tools.kg.served_edge`` (like train run 4). Every field the export must hide
  carries a sentinel string or number.
- ``docs/mentions.md``: names one item ID, for the exclusion-list test.

Each trace follows one of five scripts, by item number: a plain answer; a call on an
edge ID no earlier result showed, then an abstention; a duplicate result, an
unparseable result and a result answering no call, then prose around the JSON; an
answer in an earlier message and a final message with only tool calls; and
intermediate text with reasoning content, then an answer.

The trace skeleton (top-level fields, node fields, the agent config) is copied from
``tests/fixtures/analysis/stub_run``, a real ``vf-run`` of a scripted stub.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from neuro_grounded_qa.contracts import EdgeEvidence, Entity, ToolResultPayload
from neuro_grounded_qa.tools.kg import served_edge

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
TEMPLATE = REPO / "tests" / "fixtures" / "analysis" / "stub_run" / "traces.jsonl"

BUILD_ID = "fixture-grading-store-v1"
STORE_REL = "tests/fixtures/grading/store.json"

SENTINELS = {
    "template_id": "tpl_SENTINEL_TEMPLATE",
    "notes": "SENTINEL_NOTES_TEXT",
    "reward": 0.987654,
    "metric": 0.123457,
    "reward_param": "SENTINEL_REWARD_PARAM",
    "endpoint": "SENTINEL_ENDPOINT",
    "base_url": "http://sentinel-endpoint.invalid/v1",
    "system_prompt": "SENTINEL_SYSTEM_PROMPT",
    "intermediate": "SENTINEL_INTERMEDIATE_TEXT",
    "reasoning": "SENTINEL_REASONING_TEXT",
    "prompt_tokens": 7777777,
}
MODELS = {"parent-child": "sentinel-model-older", "direction-labels": "sentinel-model-newer"}
MENTIONED_ITEM = "q-0000000000a3"
HEX = {"yes": "a", "partial": "b", "correctable": "c", "no": "d"}
"""One hex digit per answerable value in the fixture item IDs (q- plus 12 hex)."""

ENTITIES = {
    "GENE_A": ("Gene Alpha", "gene/protein"),
    "GENE_B": ("Gene Beta", "gene/protein"),
    "DIS_1": ("Disease One", "disease"),
    "DIS_2": ("Disease Two", "disease"),
    "DIS_3": ("Disease Three", "disease"),
    "DIS_P": ("Disease Parent", "disease"),
    "group:DIS_GRP": ("Disease Group", "disease"),
    "DRUG_X": ("Drug X", "drug"),
}
EDGES = [
    ("E:000001", "GENE_A", "associated_with", "DIS_1", False, "associated with"),
    ("E:000002", "GENE_B", "associated_with", "DIS_1", False, "associated with"),
    ("E:000003", "DRUG_X", "indicated_for", "DIS_2", False, "indication"),
    ("E:000004", "DRUG_X", "contraindicated_for", "DIS_1", False, "contraindication"),
    ("E:000010", "DIS_3", "disease_disease", "DIS_P", True, "parent-child"),
    ("E:000011", "DIS_P", "disease_disease", "group:DIS_GRP", True, "parent-child"),
]


def entity(eid: str) -> Entity:
    name, typ = ENTITIES[eid]
    return Entity(id=eid, name=name, type=typ)


def edge(eid: str) -> EdgeEvidence:
    _, s, r, o, d, label = next(e for e in EDGES if e[0] == eid)
    return EdgeEvidence(id=eid, subject=entity(s), relation=r, display_relation=label,
                        object=entity(o), directed=d)


def store_json() -> dict[str, Any]:
    return {
        "_comment": "Synthetic store for tests/grading. Built by build_fixtures.py.",
        "build_id": BUILD_ID,
        "entities": [{"id": k, "name": n, "type": t} for k, (n, t) in ENTITIES.items()],
        "edges": [{"id": i, "subject": s, "relation": r, "display_relation": lab,
                   "object": o, "directed": d} for i, s, r, o, d, lab in EDGES],
    }


def gp(i: int, s: str, r: str, o: str, ev: str, directed: bool = False) -> dict[str, Any]:
    return {"id": f"gp{i}", "subject_id": s, "relation": r, "object_id": o,
            "evidence": [ev], "directed": directed}


def items() -> list[dict[str, Any]]:
    out = []
    n = 0
    for answerable in ("yes", "partial", "correctable", "no"):
        for k in range(5):
            n += 1
            base = {
                "id": f"q-0000000000{HEX[answerable]}{k}",
                "evidence_scope": "graph", "build_id": BUILD_ID, "source": "template",
                "split": "train", "template_id": f"{SENTINELS['template_id']}.{answerable}",
                "notes": SENTINELS["notes"],
            }
            if answerable == "yes":
                directed = k % 2 == 1
                g = ([gp(0, "DIS_3", "disease_disease", "DIS_P", "E:000010", True)] if directed
                     else [gp(0, "GENE_A", "associated_with", "DIS_1", "E:000001"),
                           gp(1, "GENE_B", "associated_with", "DIS_1", "E:000002")])
                base |= {"prompt": f"Fixture question {n}: which genes or parents?",
                         "qtype": "single_hop", "answerable": "yes", "gold_points": g}
            elif answerable == "partial":
                base |= {"prompt": f"Fixture question {n}: genes and drugs for Disease One?",
                         "qtype": "partial", "answerable": "partial",
                         "gold_points": [gp(0, "GENE_A", "associated_with", "DIS_1", "E:000001")],
                         "graph_gaps": [{"description": "No drug indicated for Disease One"}]}
            elif answerable == "correctable":
                base |= {"prompt": f"Fixture question {n}: why is Drug X indicated for Disease One?",
                         "qtype": "false_premise", "answerable": "correctable",
                         "premise": {"subject_id": "DRUG_X", "relation": "indicated_for",
                                     "object_id": "DIS_1"},
                         "gold_points": [gp(0, "DRUG_X", "contraindicated_for", "DIS_1", "E:000004")]}
            else:
                if k % 2:
                    base |= {"qtype": "false_premise",
                             "premise": {"subject_id": "GENE_B", "relation": "associated_with",
                                         "object_id": "DIS_2"}}
                else:
                    base |= {"qtype": "unanswerable"}
                base |= {"prompt": f"Fixture question {n}: which disease is Gene Beta linked to?",
                         "answerable": "no"}
            base["gold_evidence"] = sorted({e for g in base.get("gold_points", []) for e in g["evidence"]})
            out.append(base)
    return out


# --- Traces ------------------------------------------------------------------------------


class TraceBuilder:
    """Builds one saved trace's node list."""

    def __init__(self, template_nodes: list[dict[str, Any]], served: Any):
        self.tpl = template_nodes
        self.served = served
        self.nodes: list[dict[str, Any]] = []
        self.calls = 0

    def _node(self, message: dict[str, Any], sampled: bool) -> int:
        base = copy.deepcopy(self.tpl[0] if not self.nodes else self.tpl[1])
        base.pop("tools", None)
        base["message"] = message
        base["sampled"] = sampled
        base["timestamp"] = 1795000000.0 + len(self.nodes)
        if self.nodes:
            base["parent"] = len(self.nodes) - 1
        else:
            base.pop("parent", None)
            base["tools"] = copy.deepcopy(self.tpl[0]["tools"])
        self.nodes.append(base)
        return len(self.nodes) - 1

    def system(self) -> None:
        self._node({"role": "system", "content": SENTINELS["system_prompt"]}, False)

    def user(self, text: str) -> None:
        self._node({"role": "user", "content": text}, False)

    def assistant(self, content: str | None = None, calls: list[tuple[str, dict]] = (),
                  reasoning: str | None = None) -> list[str]:
        msg: dict[str, Any] = {"role": "assistant"}
        if content is not None:
            msg["content"] = content
        if reasoning is not None:
            msg["reasoning_content"] = reasoning
        ids = []
        if calls:
            msg["tool_calls"] = []
            for name, args in calls:
                cid = f"call_{self.calls}"
                self.calls += 1
                ids.append(cid)
                msg["tool_calls"].append({"id": cid, "type": "function", "name": name,
                                          "arguments": json.dumps(args)})
        self._node(msg, True)
        return ids

    def tool(self, call_id: str, name: str, content: str) -> None:
        self._node({"role": "tool", "content": content, "name": name, "tool_call_id": call_id},
                   False)

    def result(self, call_id: str, name: str, edge_ids: list[str], note: str | None = None) -> None:
        payload = ToolResultPayload(evidence=[self.served(edge(e)) for e in edge_ids], note=note)
        self.tool(call_id, name, payload.dumps())


def answer(claims: list[tuple[str, str, str, list[str]]], text: str = "Fixture answer.",
           unsupported: list[str] | None = None) -> str:
    return json.dumps({
        "claims": [{"subject_id": s, "relation": r, "object_id": o, "cites": c}
                   for s, r, o, c in claims],
        "answer_text": text,
        "unsupported_parts": unsupported or [],
    })


def script(b: TraceBuilder, n: int, item: dict[str, Any]) -> None:
    """One of five trace scripts, by ``n % 5``."""
    b.system()
    b.user(item["prompt"])
    nb = "kg_get_neighbors"
    kind = n % 5
    if kind == 0:  # plain answer with directed and grouped edges in the result
        (c,) = b.assistant(calls=[(nb, {"entity_id": "DIS_P"})])
        b.result(c, nb, ["E:000010", "E:000011"])
        (c,) = b.assistant(calls=[(nb, {"entity_id": "DIS_1"})])
        b.result(c, nb, ["E:000001", "E:000002"])
        b.assistant(answer([("GENE_A", "associated_with", "DIS_1", ["E:000001"]),
                            ("DIS_3", "disease_disease", "DIS_P", ["E:000010"])]))
    elif kind == 1:  # probe an unseen edge ID, then abstain
        (c,) = b.assistant(calls=[(nb, {"entity_id": "DIS_1", "relation": "associated_with"})])
        b.result(c, nb, ["E:000001"])
        (c,) = b.assistant(calls=[("kg_get_edge", {"edge_id": "E:000004"})])
        b.result(c, "kg_get_edge", ["E:000004"])
        b.assistant(json.dumps({"abstain": True, "reason": "Fixture abstention."}))
    elif kind == 2:  # duplicate, unparseable and unmatched results; prose around JSON
        (c1, c2) = b.assistant(calls=[(nb, {"entity_id": "DIS_1"}),
                                      ("kg_get_edge", {"edge_id": "E:000001"})])
        b.result(c1, nb, ["E:000001", "E:000004"])
        b.result(c1, nb, ["E:000003"])  # duplicate_result
        b.tool(c2, "kg_get_edge", "Error: fixture tool failure")  # unparseable
        b.result("call_ghost", nb, ["E:000010"])  # no_matching_call
        b.assistant("Here is my answer: " + answer(
            [("GENE_A", "associated_with", "DIS_1", ["E:000001"])]))
    elif kind == 3:  # earlier answer, final message with only tool calls
        (c,) = b.assistant(calls=[(nb, {"entity_id": "DIS_1"})])
        b.result(c, nb, ["E:000001"])
        (c,) = b.assistant(
            content=answer([("GENE_A", "associated_with", "DIS_1", ["E:000001"])]),
            calls=[(nb, {"entity_id": "GENE_A"})])
        b.result(c, nb, ["E:000001"])
        b.assistant(calls=[(nb, {"entity_id": "GENE_B"})])
    else:  # intermediate text and reasoning content, then an answer
        (c,) = b.assistant(content=SENTINELS["intermediate"], reasoning=SENTINELS["reasoning"],
                           calls=[(nb, {"entity_id": "DIS_1"})])
        b.result(c, nb, ["E:000001", "E:000004"])
        b.assistant(answer([("DRUG_X", "contraindicated_for", "DIS_1", ["E:000004"])],
                           unsupported=["Fixture gap."]))


def build_trace(template_line: dict[str, Any], item: dict[str, Any], n: int, run: str,
                train_sha: str) -> dict[str, Any]:
    line = copy.deepcopy(template_line)
    tpl = line["traces"][0]
    served = served_edge if run == "direction-labels" else (lambda e: e)
    b = TraceBuilder(tpl["nodes"], served)
    script(b, n, item)
    model = MODELS[run]
    task_data = {**copy.deepcopy(tpl["task"]["data"]), **copy.deepcopy(item)}
    for k in ("premise", "graph_gaps", "gold_points", "gold_evidence"):
        task_data.setdefault(k, [] if k != "premise" else None)
    task_data["idx"] = n
    task_data["system_prompt"] = SENTINELS["system_prompt"]
    trace_id = hashlib.sha256(f"{run}:{item['id']}".encode()).hexdigest()[:32]
    sampled = [i for i, nd in enumerate(b.nodes) if nd["sampled"]]
    call_tpl = tpl["calls"][0]
    calls = [{**copy.deepcopy(call_tpl), "node": i, "model": model,
              "endpoint": SENTINELS["endpoint"],
              "usage": {"prompt_tokens": SENTINELS["prompt_tokens"], "completion_tokens": 11}}
             for i in sampled]
    tpl.update({
        "id": trace_id,
        "nodes": b.nodes,
        "calls": calls,
        "task": {**tpl["task"], "data": task_data},
        "rewards": {"grounding_reward": {"score": SENTINELS["reward"], "weight": 1.0}},
        "metrics": {"coverage": SENTINELS["metric"], "format_ok": SENTINELS["metric"]},
        "info": {"store_build_id": BUILD_ID, "items_sha256": train_sha,
                 "reward_params": {"version": SENTINELS["reward_param"]}},
    })
    tpl["agent"]["config"]["model"] = model
    tpl["agent"]["config"]["client"]["base_url"] = SENTINELS["base_url"]
    tpl["run"]["name"] = f"neuro-grounded-qa--{model}--null--fixture"
    line.update({"id": trace_id, "task": tpl["task"], "run": tpl["run"]})
    return line


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8",
                    newline="\n")


def main() -> None:
    (HERE / "store.json").write_text(json.dumps(store_json(), indent=1) + "\n",
                                     encoding="utf-8", newline="\n")
    its = items()
    write_jsonl(HERE / "items" / "train.jsonl", its)
    train_sha = hashlib.sha256((HERE / "items" / "train.jsonl").read_bytes()).hexdigest()
    template_line = json.loads(TEMPLATE.read_text(encoding="utf-8").splitlines()[0])
    cfg_tpl = json.loads((TEMPLATE.parent / "configs" / "resolved" / "run.json").read_text("utf-8"))
    for run in MODELS:
        run_dir = HERE / "runs" / run / f"neuro-grounded-qa--{MODELS[run]}--null--fixture"
        lines = [build_trace(template_line, it, n, run, train_sha) for n, it in enumerate(its)]
        write_jsonl(run_dir / "traces.jsonl", lines)
        cfg = copy.deepcopy(cfg_tpl)
        cfg["env"]["taskset"]["tools"]["store_path"] = STORE_REL
        cfg["env"]["taskset"]["items_path"] = "tests/fixtures/grading/items/train.jsonl"
        (run_dir / "configs" / "resolved").mkdir(parents=True, exist_ok=True)
        (run_dir / "configs" / "resolved" / "run.json").write_text(
            json.dumps(cfg, indent=2) + "\n", encoding="utf-8", newline="\n")
    (HERE / "docs").mkdir(exist_ok=True)
    (HERE / "docs" / "mentions.md").write_text(
        f"# Fixture doc\n\nA write-up that names item {MENTIONED_ITEM} and its score.\n",
        encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
