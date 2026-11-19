"""The knowledge-graph toolset: the only way the model can retrieve evidence.

Three tools, exposed over MCP with the prefix ``kg`` (so the model sees
``kg_search_entities``, ``kg_get_neighbors`` and ``kg_get_edge``):

- ``search_entities(text)``: find entities by name. Returns entities, not evidence.
- ``get_neighbors(entity_id, relation=None)``: edges touching an entity, optionally of
  one relation. Returns edges as evidence.
- ``get_edge(edge_id)``: one edge by its evidence ID. Returns it as evidence.

Every tool returns ``ToolResultPayload.dumps()``, the JSON format
``contracts.trace.retrieved_ids`` reads evidence IDs from. Nothing a tool says outside
the payload's ``evidence`` list counts as retrieved, so error messages and notes can
mention IDs freely.

**Limits** (all from :class:`KGToolsetConfig`):

- *Result cap* (``max_results``, default 20). A tool asks the store for one more
  result than the cap; if it gets it, the list is cut, ``truncated`` is true and the
  ``note`` says so.
- *Payload size* (``max_payload_bytes``, default 16 000). Verifiers' chat-loop
  harnesses cut any tool result over 20 000 bytes into head and tail, which breaks
  the JSON, and a payload that doesn't parse counts for nothing. So results are also
  dropped from the end until the payload fits, again with ``truncated`` set.
- *Call budget* (``max_calls``, default 8 per rollout). See "Call budget" below.
- *Pagination* (``paginate``, default **off**). When on, ``get_neighbors`` takes an
  ``offset`` argument. With paging, the call budget is the only limit on reading every
  edge of an entity.

**Errors never raise.** An unknown ID, an unknown relation or blank search text comes
back as a payload with no evidence and a ``note``. An unexpected exception inside a
tool is logged and also returned as a note, so one bad call can't end the rollout.
(Arguments of the wrong JSON type are rejected by the MCP layer before the tool runs;
the model then gets the library's error text, which isn't a payload.)

**Call budget.** The tool server is *shared*: one process serves every rollout of a
run (``Taskset.toolsets``), so a counter on the server object would mix rollouts.
Verifiers 0.4.1 has a per-rollout channel for exactly this: a ``vf.State`` subclass
declared on both the toolset and the task. Before each tool call the server pulls the
calling rollout's ``trace.state`` from the host, the tool reads and changes it, and the
server pushes it back afterwards (``ServerBase._with_state``). :class:`KGState` holds
one counter, ``kg_calls``. Each call, including refused and failed ones, increments
it; once it has reached ``max_calls``, the tool returns a "budget used up" payload
without touching the store. The counter ends up on the trace as
``trace.state.kg_calls``.

The library's pull/push is last-write-wins: two calls of one rollout running at once
would both read ``kg_calls = k``, both pass the check and both write ``k + 1``. The
built-in ``bash`` and ``null`` harnesses run a turn's tool calls one at a time, but
other harnesses may not, and a policy that fires many calls in one turn would slip
past the budget. So :meth:`KGToolset.register` wraps every tool in one server-wide
``asyncio.Lock`` held across pull, call and push. That serializes tool calls across
all concurrent rollouts of a run, which costs little because each call is an
in-memory lookup plus two local HTTP requests.

Run as ``python -m neuro_grounded_qa.tools.kg``: that is how verifiers starts the
server process, with the config in ``$VF_CONFIG``.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import verifiers.v1 as vf
from pydantic import Field

from neuro_grounded_qa.contracts import (
    EdgeEvidence,
    Entity,
    EvidenceStore,
    ToolResultPayload,
)
from neuro_grounded_qa.tools.store import load_store

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

logger = logging.getLogger(__name__)

TOOL_PREFIX = "kg"
"""Prefix verifiers puts on each tool name: ``kg_search_entities`` and so on."""

DEFAULT_STORE_PATH = "tests/fixtures/contracts/mini_store.json"
"""The contracts' synthetic fixture store, relative to the repo root. The default lets
the environment run end to end with no graph data. The taskset resolves it to an
absolute path before the tool server starts (the server runs in its own working
directory)."""

DEFAULT_MAX_PAYLOAD_BYTES = 16_000
"""Below verifiers 0.4.1's ``TOOL_OUTPUT_MAX_BYTES`` (20 000), with headroom."""


class KGState(vf.State):
    """Per-rollout state shared by the task and the KG tool server.

    The task class must declare the same state type (``vf.Task[..., KGState]``):
    the host validates every state push against the trace's state type, and a type
    without ``kg_calls`` would silently drop the counter.
    """

    kg_calls: int = 0
    """KG tool calls received in this rollout so far, including refused and failed
    ones."""


class KGToolsetConfig(vf.SharedToolsetConfig):
    """Settings for the KG tool server. Arrives in the server process as JSON."""

    store_path: Path = Path(DEFAULT_STORE_PATH)
    """The evidence store to serve (see ``tools.store.load_store``)."""
    max_results: int = Field(20, ge=1)
    """Most entities or edges one call returns."""
    max_calls: int = Field(8, ge=0)
    """Most KG tool calls per rollout. Later calls get a payload with no evidence."""
    paginate: bool = False
    """Give ``get_neighbors`` an ``offset`` argument. Off by default: with paging, the
    call budget becomes the only limit on retrieving every edge of an entity."""
    max_payload_bytes: int = Field(DEFAULT_MAX_PAYLOAD_BYTES, ge=1_000)
    """Largest payload, in UTF-8 bytes, a tool returns. Keep it under the harness's
    tool-output truncation limit, or truncated payloads will fail to parse."""


def _payload(**fields: Any) -> str:
    return ToolResultPayload(**fields).dumps()


class KGToolset(vf.Toolset[KGToolsetConfig, KGState]):
    """MCP tool server over an :class:`EvidenceStore`. See the module docstring."""

    TOOL_PREFIX = TOOL_PREFIX

    def __init__(self, config: KGToolsetConfig, store: EvidenceStore | None = None) -> None:
        """Create the toolset.

        Args:
            config: the toolset config.
            store: an already-built store, for tests and notebooks. The served
                process leaves it None and loads ``config.store_path`` in ``setup``.
        """
        super().__init__(config)
        self._store = store
        self._relations: frozenset[str] | None = None
        self._lock = asyncio.Lock()

    # -- lifecycle -----------------------------------------------------------------

    async def setup(self) -> None:
        """Load the store once, when the server process starts."""
        self.store  # noqa: B018 - loads and caches

    @property
    def store(self) -> EvidenceStore:
        """The evidence store, loaded from ``config.store_path`` on first use."""
        if self._store is None:
            self._store = load_store(self.config.store_path)
            logger.info(
                "KG store loaded: %s (build %s)", self.config.store_path, self._store.build_id
            )
        return self._store

    @property
    def relations(self) -> frozenset[str]:
        """The store's relation vocabulary, cached."""
        if self._relations is None:
            self._relations = frozenset(self.store.relations())
        return self._relations

    def register(self, mcp: MCPServer) -> None:
        """Register the three tools, each serialized under the server-wide lock.

        Replaces the base class's registration for two reasons: the lock (see the
        module docstring) and choosing which ``get_neighbors`` to advertise, so the
        tool schema the model sees has ``offset`` only when paging is on.
        """
        neighbors = self.get_neighbors_paged if self.config.paginate else self.get_neighbors
        for fn in (self.search_entities, neighbors, self.get_edge):
            mcp.add_tool(
                self._serialized(self._with_state(fn)),
                name=getattr(fn, "tool_name", None) or fn.__name__,
                description=(fn.__doc__ or "").strip() or None,
            )

    def _serialized(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Run ``fn`` (already wrapped with state sync) under the server-wide lock."""

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            async with self._lock:
                return await fn(*args, **kwargs)

        wrapper.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
        return wrapper

    # -- budget and output ---------------------------------------------------------

    def _take_call(self) -> str | None:
        """Count this call against the rollout's budget.

        Returns None if the call may proceed, or the refusal payload if the budget
        is already used up.
        """
        state = self.state
        state.kg_calls += 1
        if state.kg_calls > self.config.max_calls:
            return _payload(
                note=(
                    f"Tool call budget used up: {self.config.max_calls} calls per "
                    "episode. This call returned nothing."
                )
            )
        return None

    def _fit(
        self,
        items: list[EdgeEvidence] | list[Entity],
        field: str,
        more: bool,
        hint: Callable[[int], str] = lambda shown: "",
    ) -> str:
        """Serialize a result list, capped by count and by payload size.

        Args:
            items: results, already cut to ``max_results``.
            field: ``"evidence"`` or ``"entities"``.
            more: True if the store had more results than ``items``.
            hint: given how many results are shown, says how to get the rest;
                appended to the note when truncated.
        """
        items = list(items)
        truncated = more
        while True:
            note = None
            if truncated:
                note = f"Showing {len(items)} results; more exist. {hint(len(items))}".strip()
            elif not items:
                note = "No results."
            out = _payload(**{field: items}, truncated=truncated, note=note)
            if len(out.encode("utf-8")) <= self.config.max_payload_bytes or not items:
                return out
            items.pop()
            truncated = True

    def _guarded(self, fn: Callable[[], str]) -> str:
        """Apply the budget, then run ``fn``; turn any exception into a note."""
        if (refusal := self._take_call()) is not None:
            return refusal
        try:
            return fn()
        except Exception:  # noqa: BLE001 - a tool must never kill the rollout
            logger.exception("KG tool failed")
            return _payload(note="Internal tool error; this call returned nothing.")

    # -- tools ---------------------------------------------------------------------

    @vf.tool
    async def search_entities(self, text: str) -> str:
        """Search the knowledge graph for entities whose names contain `text` (case-insensitive). Returns matching entities with their graph IDs, names and types, best matches first. Entities are not evidence; use their IDs with get_neighbors."""
        return self._guarded(lambda: self._search(text))

    @vf.tool
    async def get_neighbors(self, entity_id: str, relation: str | None = None) -> str:
        """List the edges that touch an entity, in either direction. `entity_id` is a graph ID from an earlier result. Optionally pass `relation`, a relation label copied exactly from an earlier result, to keep only edges of that relation. Each edge is evidence with an ID, a subject, a relation and an object."""
        return self._guarded(lambda: self._neighbors(entity_id, relation, 0, paged=False))

    @vf.tool(name="get_neighbors")
    async def get_neighbors_paged(
        self, entity_id: str, relation: str | None = None, offset: int = 0
    ) -> str:
        """List the edges that touch an entity, in either direction. `entity_id` is a graph ID from an earlier result. Optionally pass `relation`, a relation label copied exactly from an earlier result, to keep only edges of that relation. Results come in pages; pass `offset` to skip that many edges. Each edge is evidence with an ID, a subject, a relation and an object."""
        return self._guarded(lambda: self._neighbors(entity_id, relation, offset, paged=True))

    @vf.tool
    async def get_edge(self, edge_id: str) -> str:
        """Fetch one edge by its evidence ID (for example E:000001). Returns the edge as evidence."""
        return self._guarded(lambda: self._edge(edge_id))

    # -- tool bodies (sync, no budget) ---------------------------------------------

    def _search(self, text: str) -> str:
        if not text.strip():
            return _payload(note="Empty search text; pass part of an entity name.")
        cap = self.config.max_results
        hits = self.store.search_entities(text, cap + 1)
        return self._fit(
            hits[:cap], "entities", len(hits) > cap, lambda _: "Use a more specific name."
        )

    def _neighbors(self, entity_id: str, relation: str | None, offset: int, paged: bool) -> str:
        if self.store.entity(entity_id) is None:
            return _payload(
                note=f"Unknown entity ID {entity_id!r}. Use an ID from search_entities."
            )
        if relation is not None and relation not in self.relations:
            return _payload(
                note=(
                    f"Unknown relation {relation!r}. Relation labels: "
                    + ", ".join(sorted(self.relations))
                )
            )
        if offset < 0:
            return _payload(note="offset must be 0 or more.")
        cap = self.config.max_results
        edges = self.store.neighbors(entity_id, relation, offset + cap + 1)
        page = edges[offset : offset + cap]
        more = len(edges) > offset + cap
        def hint(shown: int) -> str:
            if paged:
                return f"Call again with offset={offset + shown} for more."
            return "" if relation is not None else "Pass a relation to narrow the list."

        return self._fit(page, "evidence", more, hint)

    def _edge(self, edge_id: str) -> str:
        edge = self.store.get(edge_id)
        if edge is None:
            return _payload(note=f"No edge with ID {edge_id!r}.")
        return _payload(evidence=[edge])


if __name__ == "__main__":
    KGToolset.run()
