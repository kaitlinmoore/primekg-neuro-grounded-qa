"""Builders for hand-made Verifiers traces and tool payloads, shared across test suites.

The reward, harness and exploit tests all need traces with a known shape: which
messages the model sampled, which tool calls it made, what came back. Building
``vf.Trace`` objects by hand is verbose, so this module wraps it. Import it as
``from contracts.builders import TraceBuilder`` (``tests/`` is on ``sys.path`` under
pytest's default import mode, because ``tests/`` itself has no ``__init__.py``).

A trace is a graph of ``MessageNode``s, each pointing at its ``parent``. The builder
appends nodes and, by default, makes each new node a child of the previous one, which
is what a single-branch rollout looks like. Pass ``parent=`` to branch, or
``parent=None`` to start a new root.

Everything here is synthetic and uses the fixture store's invented IDs.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import verifiers.v1 as vf

from neuro_grounded_qa.contracts import (
    EdgeEvidence,
    Entity,
    InMemoryEvidenceStore,
    ToolResultPayload,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "contracts"

_LAST = object()
"""Sentinel: "parent is the most recently added node"."""


def fixture_store() -> InMemoryEvidenceStore:
    """The synthetic store in ``tests/fixtures/contracts/mini_store.json``."""
    data = json.loads((FIXTURES / "mini_store.json").read_text())
    return InMemoryEvidenceStore.from_records(data)


def call(call_id: str, name: str = "graph_get_edge", **arguments: Any) -> vf.ToolCall:
    """A ``ToolCall``. ``arguments`` become the JSON string Verifiers stores."""
    return vf.ToolCall(id=call_id, name=name, arguments=json.dumps(arguments))


def payload(
    evidence: Iterable[EdgeEvidence] = (),
    entities: Iterable[Entity] = (),
    truncated: bool = False,
    note: str | None = None,
) -> str:
    """Tool message content in the contract format."""
    return ToolResultPayload(
        evidence=list(evidence), entities=list(entities), truncated=truncated, note=note
    ).dumps()


def edge_payload(*edge_ids: str, store: InMemoryEvidenceStore | None = None) -> str:
    """Payload content returning the named edges from the fixture store."""
    store = store or fixture_store()
    edges = []
    for eid in edge_ids:
        edge = store.get(eid)
        if edge is None:
            raise KeyError(f"{eid} is not in the fixture store")
        edges.append(edge)
    return payload(edges)


class TraceBuilder:
    """Append messages to a trace, then ``build()`` it.

    Each ``add``-style method returns the new node's index, for use as a later
    ``parent=``. Assistant messages default to ``sampled=True`` (model output);
    everything else defaults to unsampled (prompt-supplied or tool output), which is
    how Verifiers records them.
    """

    def __init__(self, data: vf.TaskData | None = None) -> None:
        self.data = data or vf.TaskData(id="fixture-0", prompt="fixture question")
        self.nodes: list[vf.MessageNode] = []

    def _parent(self, parent: object) -> int | None:
        if parent is _LAST:
            return len(self.nodes) - 1 if self.nodes else None
        return parent  # type: ignore[return-value]

    def add(self, message: Any, *, sampled: bool = False, parent: object = _LAST) -> int:
        """Append any message as a node and return its index."""
        self.nodes.append(
            vf.MessageNode(parent=self._parent(parent), message=message, sampled=sampled)
        )
        return len(self.nodes) - 1

    def system(self, text: str, *, parent: object = _LAST) -> int:
        return self.add(vf.SystemMessage(content=text), parent=parent)

    def user(self, text: str, *, parent: object = _LAST) -> int:
        return self.add(vf.UserMessage(content=text), parent=parent)

    def assistant(
        self,
        content: str | None = None,
        calls: Iterable[vf.ToolCall] = (),
        *,
        sampled: bool = True,
        parent: object = _LAST,
    ) -> int:
        """An assistant message; ``sampled=False`` fakes a prompt-supplied one."""
        calls = list(calls)
        msg = vf.AssistantMessage(content=content, tool_calls=calls or None)
        return self.add(msg, sampled=sampled, parent=parent)

    def tool(
        self,
        call_id: str,
        content: str,
        *,
        name: str | None = None,
        parent: object = _LAST,
    ) -> int:
        """A tool result answering ``call_id``. Tool messages are never sampled."""
        return self.add(
            vf.ToolMessage(tool_call_id=call_id, content=content, name=name), parent=parent
        )

    def build(self) -> vf.Trace:
        """The trace, shaped like the ones ``vf-validate`` builds."""
        return vf.Trace(
            task=vf.TraceTask(type="fixture", data=self.data),
            agent=vf.AgentInfo(config=vf.AgentConfig(), name="fixture", trainable=False),
            nodes=list(self.nodes),
        )
