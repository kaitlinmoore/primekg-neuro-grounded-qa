"""The tool result format, and which evidence in a trace counts as retrieved.

The grounding reward pays only for claims backed by evidence the model actually
retrieved during the episode. "Actually retrieved" is decided here, by
:func:`audit_retrieval` and its wrappers :func:`retrieved_ids` and
:func:`retrieved_evidence`. It is the design choice the reward and the reward-hacking
audit rest on.

**Strict retrieval.** A tool message counts only if all of these hold:

1. Its ``tool_call_id`` matches a ``ToolCall.id`` in an assistant message that the
   model sampled (``MessageNode.sampled=True``).
2. The tool message's node descends, through the ``parent`` chain, from the sampled
   node that issued that call.
3. Its content parses as a :class:`ToolResultPayload`.

Evidence IDs are then read from the payload's structure, never pattern-matched in
free text. Why this strict: Verifiers' ``trace.tool_messages`` returns *every* tool
message, including tool exchanges supplied in the prompt that the model never made,
so a reward built on it pays out for evidence the model never fetched. Rule 1 drops
prompt-supplied calls; rule 2 drops tool messages that reuse a real call's ID
somewhere it doesn't belong (another branch, or before the call was made).

The rule is source-agnostic: any tool whose result uses the payload format
contributes, not only graph tools. Phase 2's passage tool plugs in unchanged.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

import verifiers.v1 as vf
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from verifiers.v1.types import content_text

from neuro_grounded_qa.contracts.evidence import Entity, Evidence


class ToolResultPayload(BaseModel):
    """The JSON every evidence-returning tool puts in ``ToolMessage.content``.

    A structured payload, rather than prose with IDs in it, is what lets
    ``retrieved_ids`` read IDs exactly: an ID the model merely typed, or that a tool
    echoed in an error message, is never mistaken for retrieved evidence.

    - ``evidence``: evidence units returned. Only these count as retrieved.
    - ``entities``: entities returned (for example, search hits). Not evidence: an
      entity alone supports no claim.
    - ``truncated``: True if the tool capped its results, so the model knows to
      narrow its query.
    - ``note``: optional message to the model (no results, bad argument, budget).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence: list[Evidence] = Field(default_factory=list)
    entities: list[Entity] = Field(default_factory=list)
    truncated: bool = False
    note: str | None = None

    def dumps(self) -> str:
        """Serialize to the compact JSON string a tool returns."""
        return self.model_dump_json()

    @classmethod
    def loads(cls, s: str | bytes) -> ToolResultPayload:
        """Parse a tool message's content. Strict: no coercion, no extra keys.

        Raises:
            pydantic.ValidationError: if ``s`` isn't a valid payload.
        """
        return cls.model_validate_json(s, strict=True)


RejectionReason = Literal[
    "no_matching_call", "call_not_sampled", "not_descendant", "unparseable"
]
"""Why a tool message contributed no evidence.

- ``no_matching_call``: no assistant message anywhere in the trace issued its
  ``tool_call_id``.
- ``call_not_sampled``: only prompt-supplied (unsampled) assistant messages issued it.
  This is the prompt-injected tool result case.
- ``not_descendant``: a sampled message issued the ID, but this tool message isn't
  below it in the message graph.
- ``unparseable``: provenance is fine, but the content isn't a ``ToolResultPayload``.
  Expected for tools that don't return evidence (for example a shell tool), so read
  this count per ``tool_name``.
"""


@dataclass(frozen=True)
class AcceptedToolMessage:
    """A tool message that counted: where it is and what it contributed."""

    node: int
    """Index into ``trace.nodes``."""
    tool_call_id: str
    issuer: int
    """Index of the sampled assistant node that issued the call."""
    tool_name: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class RejectedToolMessage:
    """A tool message that didn't count, and why."""

    node: int
    """Index into ``trace.nodes``."""
    tool_call_id: str
    reason: RejectionReason
    tool_name: str | None
    """Name from the issuing ``ToolCall`` if one was found, else from the message."""
    detail: str = ""


@dataclass
class RetrievalAudit:
    """Everything :func:`audit_retrieval` decided about one trace.

    The reward uses ``evidence``. The audit and metrics use the rest: how many tool
    messages were rejected and why, and signs of tampering.
    """

    evidence: dict[str, Evidence] = field(default_factory=dict)
    """Retrieved evidence by ID. If an ID arrives more than once, the first copy is
    kept and a differing later copy is listed in ``conflicting_ids``."""
    accepted: list[AcceptedToolMessage] = field(default_factory=list)
    rejected: list[RejectedToolMessage] = field(default_factory=list)
    conflicting_ids: set[str] = field(default_factory=set)
    """IDs returned with different content by different accepted tool messages. A
    real store never does this, so it signals tampering or a store bug."""

    @property
    def ids(self) -> set[str]:
        """The retrieved evidence IDs."""
        return set(self.evidence)

    def rejection_counts(self) -> dict[RejectionReason, int]:
        """Rejected tool messages counted by reason (reasons with none omitted)."""
        return dict(Counter(r.reason for r in self.rejected))

    @property
    def duplicate_call_ids(self) -> set[str]:
        """Call IDs answered by more than one accepted tool message.

        The strict rule allows this (each one descends from the issuing call), but a
        real harness sends one result per call, so it is worth watching for.
        """
        counts = Counter(a.tool_call_id for a in self.accepted)
        return {cid for cid, n in counts.items() if n > 1}


def _ancestors(trace: vf.Trace, node: int) -> set[int]:
    """Indices of every node above ``node`` on its ``parent`` chain.

    Guards against cycles and out-of-range parents, which a hand-built or tampered
    trace could contain: the walk stops rather than looping or crashing.
    """
    seen: set[int] = set()
    current = trace.nodes[node].parent
    while current is not None and current not in seen and 0 <= current < len(trace.nodes):
        seen.add(current)
        current = trace.nodes[current].parent
    return seen


def audit_retrieval(trace: vf.Trace) -> RetrievalAudit:
    """Apply the strict retrieval rule to every tool message in ``trace``.

    Walks ``trace.nodes`` directly. ``trace.tool_messages`` can't be used: it
    doesn't record provenance and includes prompt-supplied tool results.

    Returns:
        A :class:`RetrievalAudit` with the retrieved evidence and a record of
        every tool message accepted or rejected.
    """
    # Call ID -> issuing node indices, split by provenance. A list, because some
    # providers reuse IDs like "call_0" every turn; the descendant check then picks
    # the right issuer.
    sampled_issuers: dict[str, list[int]] = {}
    unsampled_ids: set[str] = set()
    call_names: dict[tuple[int, str], str] = {}
    for i, node in enumerate(trace.nodes):
        msg = node.message
        if not isinstance(msg, vf.AssistantMessage):
            continue
        for call in msg.tool_calls or []:
            if node.sampled:
                sampled_issuers.setdefault(call.id, []).append(i)
                call_names[(i, call.id)] = call.name
            else:
                unsampled_ids.add(call.id)

    audit = RetrievalAudit()
    for i, node in enumerate(trace.nodes):
        msg = node.message
        if not isinstance(msg, vf.ToolMessage):
            continue
        cid = msg.tool_call_id

        def reject(reason: RejectionReason, name: str | None, detail: str = "") -> None:
            audit.rejected.append(RejectedToolMessage(i, cid, reason, name, detail))

        issuers = sampled_issuers.get(cid)
        if not issuers:
            reason = "call_not_sampled" if cid in unsampled_ids else "no_matching_call"
            reject(reason, msg.name)
            continue
        above = _ancestors(trace, i)
        # Nearest issuer first, in case one ID was issued twice on the same path.
        issuer = next((n for n in sorted(issuers, reverse=True) if n in above), None)
        if issuer is None:
            reject("not_descendant", msg.name, f"issued at node(s) {issuers}")
            continue
        name = call_names[(issuer, cid)]
        try:
            payload = ToolResultPayload.loads(content_text(msg.content))
        except ValidationError as e:
            reject("unparseable", name, f"{e.error_count()} validation error(s)")
            continue

        for ev in payload.evidence:
            kept = audit.evidence.setdefault(ev.id, ev)
            if kept != ev:
                audit.conflicting_ids.add(ev.id)
        audit.accepted.append(
            AcceptedToolMessage(i, cid, issuer, name, tuple(e.id for e in payload.evidence))
        )
    return audit


def retrieved_evidence(trace: vf.Trace) -> dict[str, Evidence]:
    """Retrieved evidence units by ID, under the strict rule. The support check reads
    these to compare a claim's triple with what its cited evidence actually says."""
    return audit_retrieval(trace).evidence


def retrieved_ids(trace: vf.Trace) -> set[str]:
    """IDs of the evidence the model retrieved, under the strict rule.

    The set of IDs a claim may legitimately cite. See the module docstring for the
    rule and why it is strict.
    """
    return audit_retrieval(trace).ids
