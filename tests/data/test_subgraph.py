"""Tests for scripts/data/subgraph.py on the synthetic graph in tests/fixtures/data.

The fixture (seed = {D1, D2}) was built so every count below can be checked by
hand:

- 20 raw rows -> 19 canonical edges: ``G1,D1,assoc`` is the reverse copy of
  ``D1,G1,assoc``. ``G1,G1`` is a self-loop: kept as a fact, never traversed.
- N1 = {D1, D2, G1, G2, H, P1, R1, R2, G3}.
- Degrees (distinct neighbors): D1 7, H 5, D3 4, G1 3, G2 3, ... ; with a cap
  of 3 the hub H is the only 1-hop node that is pruned.
- (a) induced on N1: 10 edges. (b) endpoint in N1: 17 edges (only G4–D3 and
  G6–D3 lie fully outside N1). (c) cap 3: 13 edges (H's four outward edges go).
- Two-hop paths: (a) 2, (b) 9, (c) 5. In (b), 4 of the 9 run through H.
"""

import pytest

import subgraph as sg
from subgraph import SubgraphSpec


def edge_set(con, name):
    rows = con.execute(
        f"SELECT u, v, rel FROM edges JOIN sub_{name} USING (eid) ORDER BY u, v, rel"
    ).fetchall()
    return set(rows)


def test_canonicalize_collapses_reverse_copies(tiny):
    assert tiny.stats == {"raw_rows": 20, "canonical_edges": 19, "rows_collapsed": 1}
    # The undirected pair is stored once, as (least, greatest).
    rows = tiny.con.execute("SELECT u, v FROM edges WHERE rel = 'assoc' AND 'G1' IN (u, v) AND 'D1' IN (u, v)").fetchall()
    assert rows == [("D1", "G1")]


def test_directed_edge_keeps_its_order(tiny):
    rows = tiny.con.execute("SELECT u, v, directed FROM edges WHERE rel = 'parent'").fetchall()
    assert rows == [("D1", "D2", True)]


def test_self_loop_is_not_traversed(tiny):
    assert tiny.con.execute("SELECT count(*) FROM adj WHERE a = b").fetchone()[0] == 0
    assert tiny.con.execute("SELECT degree FROM deg WHERE id = 'G1'").fetchone()[0] == 3


def test_degree_counts_distinct_neighbors(tiny):
    deg = dict(tiny.con.execute("SELECT id, degree FROM deg").fetchall())
    assert deg["D1"] == 7 and deg["H"] == 5 and deg["D3"] == 4 and deg["G6"] == 1


def test_degree_threshold_bounds(tiny):
    assert sg.degree_threshold(tiny.con, 100) == 7
    assert sg.degree_threshold(tiny.con, 0) == 1
    assert sg.degree_threshold(tiny.con, 50) <= sg.degree_threshold(tiny.con, 95)


def test_n1(tiny):
    n1 = {r[0] for r in tiny.con.execute("SELECT id FROM n1").fetchall()}
    assert n1 == {"D1", "D2", "G1", "G2", "H", "P1", "R1", "R2", "G3"}


def test_unknown_seed_raises(tiny):
    with pytest.raises(ValueError, match="not in the graph"):
        sg.set_seed(tiny.con, ["D1", "NOPE"])


def test_one_hop_is_induced_on_n1(tiny):
    sg.build_subgraph(tiny.con, SubgraphSpec("a", "a"))
    edges = edge_set(tiny.con, "a")
    assert len(edges) == 10
    assert ("G1", "G2", "ppi") in edges          # between two neighbors: kept
    assert ("G1", "G4", "ppi") not in edges      # G4 is two hops out


def test_two_hop_takes_every_edge_touching_n1(tiny):
    sg.build_subgraph(tiny.con, SubgraphSpec("b", "b"))
    edges = edge_set(tiny.con, "b")
    assert len(edges) == 17
    assert ("D3", "G4", "assoc") not in edges    # both ends two hops out
    assert ("D3", "G6", "assoc") not in edges
    assert ("D3", "H", "assoc") in edges


def test_hub_pruning_drops_only_the_hubs_outward_edges(tiny):
    sg.build_subgraph(tiny.con, SubgraphSpec("c", "c", max_degree=3))
    edges = edge_set(tiny.con, "c")
    assert len(edges) == 13
    assert ("D1", "H", "assoc") in edges         # seed edges are never pruned
    for gone in [("G5", "H", "ppi"), ("D3", "H", "assoc"), ("D4", "H", "assoc"), ("A1", "H", "expr_present")]:
        assert gone not in edges
    assert ("D3", "R2", "indication") in edges   # R2 (degree 2) still expands


def test_hub_pruning_requires_a_cap(tiny):
    with pytest.raises(ValueError):
        sg.build_subgraph(tiny.con, SubgraphSpec("c", "c"))


def test_single_hop_facts(tiny):
    sg.build_subgraph(tiny.con, SubgraphSpec("b", "b"))
    t = sg.single_hop_facts(tiny.con, "b")
    rows = tiny.con.execute(f"SELECT s, o, rel FROM {t}").fetchall()
    assert len(rows) == 8
    assert ("D1", "R2", "contraindication") in rows  # stored as R2,D1: seed end is s


@pytest.mark.parametrize("kind,cap,n_paths", [("a", None, 2), ("b", None, 9), ("c", 3, 5)])
def test_two_hop_path_counts(tiny, kind, cap, n_paths):
    sg.build_subgraph(tiny.con, SubgraphSpec(kind, kind, max_degree=cap))
    t = sg.two_hop_paths(tiny.con, kind)
    assert tiny.con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] == n_paths
    # Paths never pass through or end at a seed, and never double back.
    bad = tiny.con.execute(
        f"SELECT count(*) FROM {t} WHERE m IN ('D1','D2') OR t IN ('D1','D2') OR t = m"
    ).fetchone()[0]
    assert bad == 0


def test_two_hop_hub_share_and_endpoints(tiny):
    sg.build_subgraph(tiny.con, SubgraphSpec("b", "b"))
    t = sg.two_hop_paths(tiny.con, "b")
    by_m = dict(tiny.con.execute(f"SELECT m, count(*) FROM {t} GROUP BY m").fetchall())
    assert by_m == {"G1": 2, "G2": 2, "H": 4, "R2": 1}
    endpoints = {r[0] for r in tiny.con.execute(f"SELECT DISTINCT t FROM {t}").fetchall()}
    assert endpoints == {"G1", "G2", "G4", "G5", "A1", "D3", "D4"}


def test_is_absent_checks_full_graph_both_directions(tiny):
    assert sg.is_absent(tiny.con, "D1", "G4")
    assert not sg.is_absent(tiny.con, "D1", "G1")
    assert not sg.is_absent(tiny.con, "G1", "D1")
    # G4–D3 is outside subgraph (b) but in the full graph: not absent.
    assert not sg.is_absent(tiny.con, "G4", "D3")


def test_unanswerable_candidates(tiny):
    sg.build_subgraph(tiny.con, SubgraphSpec("b", "b"))
    t = sg.unanswerable_candidates(tiny.con, "b")
    rows = set(tiny.con.execute(f"SELECT s, r, y FROM {t}").fetchall())
    assert rows == {
        ("D1", "assoc", "G3"), ("D1", "assoc", "G4"), ("D1", "assoc", "G5"),
        ("D1", "parent", "D3"), ("D1", "parent", "D4"),
        ("D2", "parent", "D3"), ("D2", "parent", "D4"),
        ("D2", "assoc", "G1"), ("D2", "assoc", "G2"), ("D2", "assoc", "G4"),
        ("D2", "assoc", "G5"), ("D2", "assoc", "H"),
    }
    # Every candidate is a code-verified absence in the full graph.
    for s, _, y in rows:
        assert sg.is_absent(tiny.con, s, y)
    # G6 is outside the subgraph, so it is never a candidate.
    assert not any(y == "G6" for _, _, y in rows)


def test_partial_candidates(tiny):
    sg.build_subgraph(tiny.con, SubgraphSpec("b", "b"))
    sg.single_hop_facts(tiny.con, "b")
    sg.unanswerable_candidates(tiny.con, "b")
    # D1–G1, D1–G2, D1–H and D2–G3 have an absent gene sibling; the phenotype,
    # drug and seed–seed facts don't.
    assert sg.partial_candidates(tiny.con, "b") == 4


OPP = [("indication", "contraindication"), ("expr_present", "expr_absent")]


def test_contradiction_edges(tiny):
    sg.build_subgraph(tiny.con, SubgraphSpec("b", "b"))
    t = sg.contradiction_edges(tiny.con, "b", OPP)
    rows = tiny.con.execute(f"SELECT u, v, rel, touches_seed FROM {t} ORDER BY u, v").fetchall()
    assert rows == [
        ("A1", "G2", "expr_absent", False),
        ("A1", "H", "expr_present", False),
        ("D1", "R1", "indication", True),
        ("D1", "R2", "contraindication", True),
        ("D3", "R2", "indication", False),
    ]


def test_contradiction_skips_pairs_carrying_both_relations(tiny):
    con = tiny.con
    con.execute("INSERT INTO raw_edges VALUES ('D1', 'R1', 'contraindication', false, ['DrugCentral'])")
    sg.canonicalize_edges(con)
    sg.build_adjacency(con)
    sg.compute_degree(con)
    sg.set_seed(con, ["D1", "D2"])
    sg.build_subgraph(con, SubgraphSpec("b", "b"))
    t = sg.contradiction_edges(con, "b", OPP)
    pairs = {(u, v) for u, v in con.execute(f"SELECT u, v FROM {t}").fetchall()}
    assert ("D1", "R1") not in pairs     # indication and contraindication both: ambiguous
    assert ("D1", "R2") in pairs


def test_balanced_capacity():
    assert sg.balanced_capacity({"a": 100, "b": 10}, target=100) == 50
    assert sg.balanced_capacity({"a": 40, "b": 40, "c": 40}, target=100) == 120
    assert sg.balanced_capacity({}, target=100) == 0
