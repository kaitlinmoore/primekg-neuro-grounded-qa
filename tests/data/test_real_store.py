"""The KG tools against the real PrimeKG store, when it has been built locally.

Skipped unless the store exists: ``$NGQ_STORE``, else
``$NGQ_DATA_DIR/store/primekg-neuro-c95`` (build it with
``python -m neuro_grounded_qa.data.build``). Graph data never enters the repo, so
these tests can't run in CI; they check on a real build what the fixture tests can't:
that the store loads, satisfies the protocol, and that the tools stay inside their
result and payload caps on real names and fan-outs.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from neuro_grounded_qa.contracts import EvidenceStore, ToolResultPayload
from neuro_grounded_qa.data import store_files as sf
from neuro_grounded_qa.tools import KGToolset, KGToolsetConfig, load_store, read_build_id


def _store_path() -> Path | None:
    if os.environ.get("NGQ_STORE"):
        return Path(os.environ["NGQ_STORE"])
    if os.environ.get("NGQ_DATA_DIR"):
        return Path(os.environ["NGQ_DATA_DIR"]) / "store" / "primekg-neuro-c95"
    return None


STORE = _store_path()
pytestmark = pytest.mark.skipif(
    STORE is None or not sf.is_store_dir(STORE), reason="real evidence store not built"
)

AD = "group:alzheimer_disease"  # PrimeKG's grouped "Alzheimer disease" node


@pytest.fixture(scope="module")
def toolset():
    store = load_store(STORE)
    return KGToolset(KGToolsetConfig(), store=store)


def test_store_satisfies_protocol_and_agrees_on_build_id(toolset):
    store = toolset.store
    assert isinstance(store, EvidenceStore)
    assert store.build_id == read_build_id(STORE) == sf.read_build_meta(STORE)["build_id"]
    assert len(store) == sf.read_build_meta(STORE)["counts"]["edges"]


def test_search_finds_the_seed(toolset):
    p = ToolResultPayload.loads(toolset._search("Alzheimer disease"))
    assert p.entities[0].id == AD
    assert not p.evidence


def test_neighbors_are_capped_and_fit_the_payload_limit(toolset):
    out = toolset._neighbors(AD, None, 0, paged=False)
    p = ToolResultPayload.loads(out)
    assert len(p.evidence) == toolset.config.max_results
    assert p.truncated
    assert len(out.encode("utf-8")) <= toolset.config.max_payload_bytes
    assert all(AD in (e.subject.id, e.object.id) for e in p.evidence)


def test_relation_filter_and_directed_edges(toolset):
    p = ToolResultPayload.loads(toolset._neighbors(AD, "disease_disease", 0, paged=False))
    assert p.evidence and all(e.relation == "disease_disease" and e.directed for e in p.evidence)


def test_get_edge_round_trips(toolset):
    first = ToolResultPayload.loads(toolset._neighbors(AD, "disease_protein", 0, paged=False)).evidence[0]
    p = ToolResultPayload.loads(toolset._edge(first.id))
    assert p.evidence == [first]
