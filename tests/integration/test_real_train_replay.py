"""Opt-in: replay every real **train** item's recorded route through ``scripts/run.sh``.

Skipped unless ``NGQ_ITEMS_DIR`` names an item-set directory
(``$NGQ_DATA_DIR/items/<build_id>``). The store is ``NGQ_STORE_DIR`` if set, else
``$NGQ_DATA_DIR/store/primekg-neuro-c95``. Run it with::

    NGQ_ITEMS_DIR="$NGQ_DATA_DIR/items/<build_id>" \\
        uv run pytest -q tests/integration/test_real_train_replay.py

It reads ``train.jsonl`` only, through the train-only viewer's loader
(``qgen.view.load_train``): a file path, a held-out path, or a train file holding a
held-out record is refused before anything runs. Held-out items are never replayed.

For each train item, an honest scripted policy (served by the stub endpoint, no
model) plays the item's own route:

1. one turn of ``kg_search_entities`` calls, one per named entity, by name;
2. one turn of the recorded ``kg_get_neighbors`` calls;
3. the final answer: abstain on ``answerable="no"`` items; otherwise claim every
   gold point citing its evidence, and on partial items flag each gap.

Asserts that every item with gold reaches reward 1 with every gold edge retrieved
by the route's own calls, within the call budget, and that every ``no`` item
reaches reward 1 by abstaining. Full reward shows that the routes and the gold are
retrievable and correct under the run's real tool config and reward.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from neuro_grounded_qa.contracts import QuestionItem
from neuro_grounded_qa.tools import load_store

from .stub_endpoint import StubEndpoint, Turn, calls, final, scripted, tool_call

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "data"))
from qgen.view import load_train

ITEMS_DIR = os.environ.get("NGQ_ITEMS_DIR")
API_KEY_VAR = "NGQ_STUB_API_KEY"
ABSTAIN = json.dumps({"abstain": True, "reason": "the evidence doesn't support an answer"})

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not ITEMS_DIR, reason="set NGQ_ITEMS_DIR to an item-set directory to replay train routes"),
    pytest.mark.skipif(
        shutil.which("uv") is None or not os.environ.get("UV_PROJECT_ENVIRONMENT"),
        reason="needs uv and the project venv (UV_PROJECT_ENVIRONMENT)",
    ),
]


def items_dir() -> Path:
    """The item-set directory, refusing anything that isn't one."""
    d = Path(ITEMS_DIR or "").expanduser()
    if not d.is_dir() or "heldout" in d.name:
        pytest.fail("NGQ_ITEMS_DIR must be an item-set directory; this test reads its train.jsonl only")
    return d


def store_dir() -> Path:
    s = os.environ.get("NGQ_STORE_DIR")
    if s:
        return Path(s).expanduser()
    return Path(os.environ.get("NGQ_DATA_DIR", REPO / "data")).expanduser() / "store" / "primekg-neuro-c95"


def script(item: QuestionItem, store) -> list[Turn]:
    """The honest route-following turns for one item."""
    notes = json.loads(item.notes)
    searches = [s["entity"] for s in notes["route"] if s["tool"] == "search_entities"]
    probes = [s for s in notes["route"] if s["tool"] == "get_neighbors"]
    turns = [calls(*(tool_call("kg_search_entities", text=store.entity(e).name) for e in searches))]
    if probes:
        turns.append(calls(*(
            tool_call("kg_get_neighbors", entity_id=p["entity_id"],
                      **({"relation": p["relation"]} if p["relation"] else {}))
            for p in probes)))
    if item.answerable == "no":
        turns.append(final(ABSTAIN))
    else:
        body: dict[str, Any] = {
            "claims": [{"subject_id": g.subject_id, "relation": g.relation, "object_id": g.object_id,
                        "cites": list(g.evidence)} for g in item.gold_points],
            "answer_text": "see claims",
        }
        if item.answerable == "partial":
            body["unsupported_parts"] = [g.description for g in item.graph_gaps]
        turns.append(final(json.dumps(body)))
    return turns


def retrieved(trace: dict[str, Any]) -> set[str]:
    """Evidence IDs in the trace's tool results."""
    out: set[str] = set()
    for node in trace["nodes"]:
        m = node.get("message", {})
        if m.get("role") != "tool":
            continue
        c = m["content"] if isinstance(m["content"], str) else "".join(x.get("text", "") for x in m["content"])
        out |= {e["id"] for e in json.loads(c).get("evidence", [])}
    return out


def test_every_train_route_reaches_full_reward(tmp_path):
    d = items_dir()
    items = load_train(d)  # train.jsonl only; refuses a file holding any held-out record
    store = load_store(store_dir())
    policy = scripted({i.prompt_text: script(i, store) for i in items})
    with StubEndpoint({"route": policy}) as stub:
        args = [
            "bash", str(REPO / "scripts" / "run.sh"), "-m", "stub/route",
            "--client.base-url", stub.base_url, "--client.api-key-var", API_KEY_VAR,
            "-o", str(tmp_path),
            "--env.taskset.items_path", str(d / "train.jsonl"),
            "--env.taskset.tools.store_path", str(store_dir()),
        ]
        result = subprocess.run(args, cwd=REPO, env={**os.environ, API_KEY_VAR: "stub-not-a-secret"},
                                capture_output=True, text=True, timeout=3600, check=False)
        assert result.returncode == 0, result.stderr[-4000:]
        assert not stub.errors, stub.errors[:5]
    [path] = tmp_path.glob("*/traces.jsonl")
    seen: set[str] = set()
    failures: list[str] = []
    for line in path.read_text().splitlines():
        trace = json.loads(line)["traces"][0]
        item = QuestionItem.model_validate(trace["task"]["data"])
        assert item.split == "train"
        seen.add(item.id)
        score = trace["rewards"]["grounding_reward"]["score"]
        if score != 1.0:
            failures.append(f"{item.id} {item.template_id} reward {score}")
        if item.answerable != "no" and not set(item.gold_evidence) <= retrieved(trace):
            failures.append(f"{item.id} {item.template_id} gold not retrieved by its route")
    assert seen == {i.id for i in items}
    assert not failures, failures[:10]
