"""Shared helpers for the harness tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from neuro_grounded_qa.contracts import InMemoryEvidenceStore, ToolResultPayload
from neuro_grounded_qa.tools import KGState, KGToolset, KGToolsetConfig

TESTS = Path(__file__).resolve().parent.parent
REPO = TESTS.parent
CONTRACT_FIXTURES = TESTS / "fixtures" / "contracts"
HARNESS_FIXTURES = TESTS / "fixtures" / "harness"
STORE_PATH = CONTRACT_FIXTURES / "mini_store.json"
ITEMS_PATH = CONTRACT_FIXTURES / "items.jsonl"
SPLITS_PATH = HARNESS_FIXTURES / "items_splits.jsonl"


def fixture_store() -> InMemoryEvidenceStore:
    """The contracts' synthetic store."""
    return InMemoryEvidenceStore.from_records(json.loads(STORE_PATH.read_text()))


def wide_store(n_edges: int, name_len: int = 10) -> InMemoryEvidenceStore:
    """A synthetic hub entity ``HUB`` with ``n_edges`` edges, for cap tests.

    Leaf names are padded to ``name_len`` characters so payload size can be pushed
    up without adding edges.
    """
    entities = [{"id": "HUB", "name": "Hub", "type": "gene"}]
    edges = []
    for i in range(1, n_edges + 1):
        leaf = f"LEAF_{i:03d}"
        entities.append({"id": leaf, "name": f"Leaf {i:03d}".ljust(name_len, "x"), "type": "gene"})
        edges.append(
            {"id": f"E:{i:06d}", "subject": "HUB", "relation": "links", "object": leaf}
        )
    return InMemoryEvidenceStore.from_records({"entities": entities, "edges": edges})


def make_toolset(store: Any = None, **config: Any) -> KGToolset:
    """A toolset over ``store`` (default: the fixture store) with config overrides."""
    return KGToolset(KGToolsetConfig(**config), store=store or fixture_store())


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def parse(s: str) -> ToolResultPayload:
    """Parse a tool's output exactly as ``retrieved_ids`` does (strict)."""
    return ToolResultPayload.loads(s)


class FakeStateChannel:
    """Stands in for the host's per-rollout state channel (``GET``/``PUT /state``).

    Patched over a toolset's ``_pull_state``/``_push_state``, it holds one rollout's
    ``KGState`` the way the host holds ``trace.state``: each pull returns a copy,
    each push replaces it (last write wins). Both yield to the event loop, as the
    real HTTP round trips do, so concurrent calls interleave.
    """

    def __init__(self, toolset: KGToolset) -> None:
        self.toolset = toolset
        self.state = KGState()
        toolset._pull_state = self.pull  # type: ignore[method-assign]
        toolset._push_state = self.push  # type: ignore[method-assign]

    async def pull(self) -> KGState:
        await asyncio.sleep(0)
        return self.state.model_copy()

    async def push(self, before: bytes) -> None:
        await asyncio.sleep(0)
        self.state = self.toolset.state.model_copy()
