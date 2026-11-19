"""Helpers for the reward tests: fixture items, final answers, and episode traces.

Everything is built from the contracts' synthetic fixtures (``mini_store.json`` and
``items.jsonl``) with the shared ``contracts.builders``. Nothing here is real graph
data.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

import verifiers.v1 as vf

from contracts.builders import FIXTURES, TraceBuilder, call, edge_payload, fixture_store
from neuro_grounded_qa.contracts import Evidence, QuestionItem

STORE = fixture_store()


def load_items() -> dict[str, QuestionItem]:
    """The contract fixture items, by ID."""
    lines = (FIXTURES / "items.jsonl").read_text().splitlines()
    items = [QuestionItem.model_validate_json(line) for line in lines if line.strip()]
    return {item.id: item for item in items}


ITEMS = load_items()


def evidence(*ids: str) -> dict[str, Evidence]:
    """A retrieved-evidence mapping for the given fixture edge IDs."""
    out: dict[str, Evidence] = {}
    for eid in ids:
        edge = STORE.get(eid)
        assert edge is not None, eid
        out[eid] = edge
    return out


def claim(s: str, r: str, o: str, *cites: str, **extra: Any) -> dict[str, Any]:
    """A claim as the model writes it in JSON."""
    return {"subject_id": s, "relation": r, "object_id": o, "cites": list(cites), **extra}


def answer(*claims: dict[str, Any], text: str = "answer", unsupported: Sequence[str] = ()) -> str:
    """Final reply text for an answer."""
    body: dict[str, Any] = {"claims": list(claims), "answer_text": text}
    if unsupported:
        body["unsupported_parts"] = list(unsupported)
    return json.dumps(body)


def abstain(reason: str = "the evidence doesn't support an answer") -> str:
    """Final reply text for an abstention."""
    return json.dumps({"abstain": True, "reason": reason})


def episode(
    item: QuestionItem,
    final: str,
    retrieved: Iterable[str] = (),
    *,
    tool: str = "get_edge",
) -> vf.Trace:
    """A one-branch episode: prompt, one sampled tool call per retrieved edge (each
    answered by a real tool result), then the sampled final reply."""
    b = TraceBuilder(data=item)
    b.system("Answer only from retrieved evidence.")
    b.user(item.prompt_text)
    for i, eid in enumerate(retrieved):
        cid = f"call_{i}"
        b.assistant(None, [call(cid, tool, edge_id=eid)])
        b.tool(cid, edge_payload(eid), name=tool)
    b.assistant(final)
    return b.build()
