"""Direction labels on directed edges in tool results (``tools.kg.served_edge``).

The store keeps directed hierarchy edges subject = child, object = parent, but PrimeKG
labels them ``parent-child``. Tools show ``subtype of`` instead, or, where a grouped
disease sits at either end, a label saying the direction isn't recorded. Only
``display_relation`` changes; everything scoring reads stays the store's.
"""

from __future__ import annotations

import json

import pytest
from mcp.server.mcpserver import MCPServer
from verifiers.v1.harnesses.utils.mcp import mcp_content_to_chat_content

from contracts.builders import TraceBuilder, call
from neuro_grounded_qa.contracts import InMemoryEvidenceStore, retrieved_ids
from neuro_grounded_qa.tools import (
    GROUPED_HIERARCHY_LABEL,
    SUBTYPE_LABEL,
    direction_label,
    served_edge,
)

from harness.helpers import make_toolset, parse, run


def hierarchy_store(n_children: int = 0, name_len: int = 10) -> InMemoryEvidenceStore:
    """A small store with PrimeKG-style labels on directed and undirected edges.

    - ``E:000001`` plain disease child -> parent, ``parent-child``, directed.
    - ``E:000002`` group subject -> plain object, directed.
    - ``E:000003`` plain subject -> group object, directed.
    - ``E:000004`` plain phenotype child -> parent, directed.
    - ``E:000005`` disease - gene, ``associated with``, undirected.
    - ``E:000006`` exposure - exposure, ``parent-child``, undirected (PrimeKG labels
      this relation ``parent-child`` too, but the store doesn't direct it).
    - ``E:000007`` a directed edge with no display label at all.
    - ``n_children`` more plain directed edges ``KID_i -> MONDO:2``, for cap tests,
      with names padded to ``name_len``.
    """
    entities = [
        {"id": "MONDO:1", "name": "narrow disease", "type": "disease"},
        {"id": "MONDO:2", "name": "broad disease", "type": "disease"},
        {"id": "group:alpha", "name": "alpha disease", "type": "disease"},
        {"id": "HP:1", "name": "narrow phenotype", "type": "effect/phenotype"},
        {"id": "HP:2", "name": "broad phenotype", "type": "effect/phenotype"},
        {"id": "NCBIGene:1", "name": "GENE1", "type": "gene/protein"},
        {"id": "MESH:1", "name": "exposure one", "type": "exposure"},
        {"id": "MESH:2", "name": "exposure two", "type": "exposure"},
    ]
    dd = {"relation": "disease_disease", "display_relation": "parent-child", "directed": True}
    edges = [
        {"id": "E:000001", "subject": "MONDO:1", "object": "MONDO:2", **dd},
        {"id": "E:000002", "subject": "group:alpha", "object": "MONDO:2", **dd},
        {"id": "E:000003", "subject": "MONDO:1", "object": "group:alpha", **dd},
        {"id": "E:000004", "subject": "HP:1", "object": "HP:2",
         "relation": "phenotype_phenotype", "display_relation": "parent-child",
         "directed": True},
        {"id": "E:000005", "subject": "MONDO:1", "object": "NCBIGene:1",
         "relation": "disease_protein", "display_relation": "associated with"},
        {"id": "E:000006", "subject": "MESH:1", "object": "MESH:2",
         "relation": "exposure_exposure", "display_relation": "parent-child"},
        {"id": "E:000007", "subject": "HP:2", "object": "MONDO:2",
         "relation": "phenotype_phenotype", "directed": True},
    ]
    for i in range(1, n_children + 1):
        kid = f"MONDO:K{i:03d}"
        entities.append({"id": kid, "name": f"kid {i:03d}".ljust(name_len, "x"),
                         "type": "disease"})
        edges.append({"id": f"E:{100 + i:06d}", "subject": kid, "object": "MONDO:2", **dd})
    return InMemoryEvidenceStore.from_records({"entities": entities, "edges": edges})


def stored(store: InMemoryEvidenceStore, edge_id: str):
    return store.get(edge_id)


# -- the label function ------------------------------------------------------------


@pytest.mark.parametrize(
    ("edge_id", "label"),
    [
        ("E:000001", SUBTYPE_LABEL),
        ("E:000002", GROUPED_HIERARCHY_LABEL),
        ("E:000003", GROUPED_HIERARCHY_LABEL),
        ("E:000004", SUBTYPE_LABEL),
        ("E:000005", "associated with"),
        ("E:000006", "parent-child"),
        ("E:000007", SUBTYPE_LABEL),
    ],
)
def test_direction_label(edge_id, label):
    assert direction_label(stored(hierarchy_store(), edge_id)) == label


def test_group_prefix_matches_the_builder():
    from neuro_grounded_qa.data import build
    from neuro_grounded_qa.tools.kg import GROUP_ID_PREFIX

    assert GROUP_ID_PREFIX == f"{build.GROUP_PREFIX}:"


def test_labels_are_the_agreed_wording():
    assert SUBTYPE_LABEL == "subtype of"
    assert GROUPED_HIERARCHY_LABEL == (
        "hierarchy link (direction not recorded for grouped diseases)"
    )


@pytest.mark.parametrize("edge_id", [f"E:{i:06d}" for i in range(1, 8)])
def test_served_edge_changes_only_display_relation(edge_id):
    edge = stored(hierarchy_store(), edge_id)
    served = served_edge(edge)
    before = edge.model_dump(exclude={"display_relation"})
    after = served.model_dump(exclude={"display_relation"})
    assert after == before
    assert served.display_relation == direction_label(edge)


@pytest.mark.parametrize("edge_id", ["E:000005", "E:000006"])
def test_undirected_edges_are_served_unchanged(edge_id):
    edge = stored(hierarchy_store(), edge_id)
    assert served_edge(edge) is edge


def test_served_edge_leaves_the_store_untouched():
    store = hierarchy_store()
    served_edge(store.get("E:000001"))
    assert store.get("E:000001").display_relation == "parent-child"


# -- every edge-returning tool -----------------------------------------------------


def _evidence(out: str) -> dict[str, dict]:
    return {e.id: e.model_dump() for e in parse(out).evidence}


def _expected(store: InMemoryEvidenceStore, ids) -> dict[str, dict]:
    out = {}
    for i in ids:
        d = store.get(i).model_dump()
        d["display_relation"] = direction_label(store.get(i))
        out[i] = d
    return out


def test_get_edge_shows_the_label():
    store = hierarchy_store()
    ts = make_toolset(store)
    for i in ("E:000001", "E:000002", "E:000005"):
        assert _evidence(run(ts.get_edge(i))) == _expected(store, [i])


@pytest.mark.parametrize("paginate", [False, True])
@pytest.mark.parametrize("relation", [None, "disease_disease"])
def test_get_neighbors_shows_the_label(paginate, relation):
    store = hierarchy_store()
    ts = make_toolset(store, paginate=paginate)
    tool = ts.get_neighbors_paged if paginate else ts.get_neighbors
    out = run(tool("MONDO:1", relation))
    want = [e.id for e in store.neighbors("MONDO:1", relation, 100)]
    assert [e.id for e in parse(out).evidence] == want  # same edges, same order
    assert _evidence(out) == _expected(store, want)


def test_paged_offset_pages_carry_the_label():
    store = hierarchy_store(n_children=5)
    ts = make_toolset(store, paginate=True, max_results=2)
    seen = []
    for offset in (0, 2, 4, 6):
        seen += parse(run(ts.get_neighbors_paged("MONDO:2", "disease_disease", offset))).evidence
    assert [e.id for e in seen] == [
        e.id for e in store.neighbors("MONDO:2", "disease_disease", 100)
    ]
    assert {e.display_relation for e in seen} == {SUBTYPE_LABEL, GROUPED_HIERARCHY_LABEL}
    assert "parent-child" not in {e.display_relation for e in seen}


def test_labelled_results_count_as_retrieved_evidence_through_mcp():
    # Through the MCP layer into a trace: the strict retrieval rule reads the same IDs
    # as before the label, and the served label is what the tool message carries.
    store = hierarchy_store()
    ts = make_toolset(store)
    mcp = MCPServer("kg")
    ts.register(mcp)
    result = run(mcp.call_tool("get_neighbors", {"entity_id": "MONDO:2"}))
    content = mcp_content_to_chat_content(result.content)
    assert isinstance(content, str) and not result.is_error
    b = TraceBuilder()
    b.user("q")
    b.assistant(calls=[call("c1", "kg_get_neighbors", entity_id="MONDO:2")])
    b.tool("c1", content, name="kg_get_neighbors")
    assert retrieved_ids(b.build()) == {e.id for e in store.neighbors("MONDO:2", None, 100)}
    labels = {e["id"]: e["display_relation"] for e in json.loads(content)["evidence"]}
    assert labels["E:000001"] == SUBTYPE_LABEL
    assert labels["E:000002"] == GROUPED_HIERARCHY_LABEL


def test_payload_cap_holds_with_the_longer_grouped_label():
    # 30 directed edges into a group, names padded so the page is near the cap.
    entities = [{"id": "group:hub", "name": "hub", "type": "disease"}]
    edges = []
    for i in range(1, 31):
        kid = f"MONDO:{i:03d}"
        entities.append({"id": kid, "name": "k".ljust(500, "x"), "type": "disease"})
        edges.append({"id": f"E:{i:06d}", "subject": kid, "object": "group:hub",
                      "relation": "disease_disease", "display_relation": "parent-child",
                      "directed": True})
    store = InMemoryEvidenceStore.from_records({"entities": entities, "edges": edges})
    ts = make_toolset(store, max_payload_bytes=8_000)
    out = run(ts.get_neighbors("group:hub"))
    payload = parse(out)
    assert len(out.encode("utf-8")) <= 8_000
    assert payload.truncated
    assert payload.evidence
    assert {e.display_relation for e in payload.evidence} == {GROUPED_HIERARCHY_LABEL}
