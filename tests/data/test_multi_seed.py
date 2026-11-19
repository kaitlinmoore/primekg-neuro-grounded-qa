"""Tests for the reported-only extras in scripts/data/subgraph.py and load.py.

Fixture: tests/fixtures/data/multi_{nodes,edges}.csv, 17 edges. The seed is
{D1, D2, D5, D6}, split into three overlapping disease groups:
X = {D1, D2}, Y = {D2, D5} (D2 is in both), Z = {D6}.

Hand-computed facts:

- Single-hop facts (edges with an endpoint in the seed): edges 1–12 and 16,
  so 13. By relation: assoc 6, parent 4, pheno 1, indication 1, parent_u 1.
- X reaches 7 facts (D1–G1, D1–G2, D2–G1, D2–G3, D1→D2, D5→D2, D2→D5);
  Y reaches 7 (D2–G1, D2–G3, D5–G3, D5–P1, D1→D2, D5→D2, D2→D5);
  Z reaches 4 (D6–G4, D6–R1, D7→D6, D6–D3).
- assoc scores: 0.6, 0.05, 0.2, 0.7, 0.02, 0.9. A cutoff of 0.1 drops D1–G2
  and D5–G3.
- parent: D5→D2 and D2→D5 are both stored, so the reversed reading of either
  is true; D1→D2 and D7→D6 have no reverse. parent_u (D6–D3) is a
  directional relation stored undirected, like PrimeKG's hierarchies. The
  last edge, P1→D3 under ``parent``, joins a phenotype to a disease, as some
  of OptimusKG's DIS-DIS/PARENT edges do: its types fix its reading.
"""

import duckdb
import pytest

import load
import subgraph as sg
from subgraph import SubgraphSpec

SEED = ["D1", "D2", "D5", "D6"]
GROUPS = {"X": ["D1", "D2"], "Y": ["D2", "D5"], "Z": ["D6"]}


@pytest.fixture
def multi(fixtures_dir):
    con = duckdb.connect()
    d = fixtures_dir / "data"
    load.load_csv_graph(con, d / "multi_nodes.csv", d / "multi_edges.csv")
    sg.canonicalize_edges(con)
    sg.build_adjacency(con)
    sg.compute_degree(con)
    sg.set_seed(con, SEED)
    yield con
    con.close()


def test_score_column_is_loaded_and_kept(multi):
    rows = dict(multi.execute("SELECT u || '-' || v, score FROM edges WHERE rel = 'assoc'").fetchall())
    assert rows["D1-G2"] == pytest.approx(0.05)
    assert multi.execute("SELECT count(*) FROM edges WHERE score IS NULL").fetchone()[0] == 11


def test_score_column_is_optional(fixtures_dir):
    con = duckdb.connect()
    d = fixtures_dir / "data"
    load.load_csv_graph(con, d / "tiny_nodes.csv", d / "tiny_edges.csv")
    sg.canonicalize_edges(con)
    assert con.execute("SELECT count(*) FROM edges WHERE score IS NOT NULL").fetchone()[0] == 0


# ---- multi-disease seeds with overlap ----

@pytest.fixture
def contributions(multi):
    sg.build_subgraph(multi, SubgraphSpec("b", "b"))
    sg.single_hop_facts(multi, "b")
    return sg.seed_group_contributions(multi, "facts_b", GROUPS, target=10, cap_share=0.4, min_facts=2)


def test_combined_pool_counts_each_fact_once(contributions):
    # Shared facts (D2's, and the X–Y parent edges) are not double counted.
    assert contributions["combined"] == {"facts": 13, "relations_ge_min": 2, "balanced_capacity": 11}


def test_group_facts_and_partners(contributions):
    g = contributions["groups"]
    assert (g["X"]["facts"], g["Y"]["facts"], g["Z"]["facts"]) == (7, 7, 4)
    assert g["X"]["facts_by_relation"] == {"assoc": 4, "parent": 3}
    # Partners inside the group's own seed don't count: X's D1→D2 drops out,
    # Y's D5→D2 and D2→D5 drop out.
    assert g["X"]["distinct_partner_relation"] == 4   # G1, G2, G3 assoc; D5 parent
    assert g["Y"]["distinct_partner_relation"] == 4   # G1, G3 assoc; P1 pheno; D1 parent
    assert g["Z"]["distinct_partner_relation"] == 4   # G4 assoc; R1 indication; D7 parent; D3 parent_u


def test_facts_only_one_group_reaches(contributions):
    g = contributions["groups"]
    assert (g["X"]["facts_only_this_group"], g["Y"]["facts_only_this_group"], g["Z"]["facts_only_this_group"]) == (2, 2, 4)


def test_what_each_group_adds_to_c2(contributions):
    g = contributions["groups"]
    # Alone: X has assoc 4, parent 3 -> 2 relations >= 2, balanced 4 + 3.
    assert (g["X"]["relations_ge_min"], g["X"]["balanced_capacity"]) == (2, 7)
    # Without X, Y and Z still reach every relation with the same capped counts.
    assert (g["X"]["adds_relations_ge_min"], g["X"]["adds_balanced_capacity"]) == (0, 0)
    # Only Y reaches the pheno fact.
    assert (g["Y"]["without_balanced_capacity"], g["Y"]["adds_balanced_capacity"]) == (10, 1)
    # Z alone has no relation with 2 facts; it adds indication, parent_u and a
    # fourth parent edge.
    assert (g["Z"]["relations_ge_min"], g["Z"]["balanced_capacity"]) == (0, 4)
    assert (g["Z"]["without_balanced_capacity"], g["Z"]["adds_balanced_capacity"]) == (8, 3)


def test_overlap_between_groups(contributions):
    ov = {(o["a"], o["b"]): o for o in contributions["overlap"]}
    xy = ov[("X", "Y")]
    assert xy["shared_seed_nodes"] == ["D2"]
    assert xy["shared_facts"] == 5
    assert xy["shared_partner_relation"] == 2          # (G1, assoc), (G3, assoc)
    assert xy["jaccard_partner_relation"] == pytest.approx(2 / 6)
    assert ov[("X", "Z")]["shared_seed_nodes"] == [] and ov[("X", "Z")]["shared_facts"] == 0


def test_c2_inputs_for_pairs_of_groups(contributions):
    pairs = {(x["a"], x["b"]): x for x in contributions["pairs_c2"]}
    # X ∪ Z: assoc 5 (D1–G1, D1–G2, D2–G1, D2–G3, D6–G4), parent 4, indication 1,
    # parent_u 1 -> 11 facts; capped at 4 each: 4 + 4 + 1 + 1.
    assert pairs[("X", "Z")] == {"a": "X", "b": "Z", "facts": 11, "relations_ge_min": 2, "balanced_capacity": 10}
    # X ∪ Y is the 9 facts on D1, D2 and D5: assoc 5, parent 3, pheno 1.
    assert (pairs[("X", "Y")]["facts"], pairs[("X", "Y")]["balanced_capacity"]) == (9, 8)


# ---- score-cutoff filter ----

def test_score_filter_drops_only_low_scored_edges_of_scored_relations(multi):
    sg.apply_score_filter(multi, 0.1, ["assoc"])
    try:
        assert multi.execute("SELECT count(*) FROM edges").fetchone()[0] == 15
        kept = {r[0] for r in multi.execute("SELECT u || '-' || v FROM edges WHERE rel = 'assoc'").fetchall()}
        assert kept == {"D1-G1", "D2-G1", "D2-G3", "D6-G4"}
        # Unscored relations are untouched, and degrees still come from the full graph.
        assert multi.execute("SELECT count(*) FROM edges WHERE rel = 'parent'").fetchone()[0] == 5
        assert multi.execute("SELECT degree FROM deg WHERE id = 'D1'").fetchone()[0] == 3
    finally:
        sg.clear_score_filter(multi)
    assert multi.execute("SELECT count(*) FROM edges").fetchone()[0] == 17
    assert multi.execute("SELECT count(*) FROM information_schema.schemata WHERE schema_name = 'filt'").fetchone()[0] == 0


def test_score_filter_shrinks_the_seed_neighborhood(multi):
    sg.apply_score_filter(multi, 0.1, ["assoc"])
    try:
        sg.set_seed(multi, SEED)
        n1 = {r[0] for r in multi.execute("SELECT id FROM n1").fetchall()}
        assert "G2" not in n1 and "G3" in n1          # G3 is still reached through D2
        sg.build_subgraph(multi, SubgraphSpec("b", "b"))
        facts = sg.single_hop_facts(multi, "b")
        assert multi.execute(f"SELECT count(*) FROM {facts}").fetchone()[0] == 11
    finally:
        sg.clear_score_filter(multi)


def test_absence_under_the_filter_uses_the_full_graph(multi):
    sg.apply_score_filter(multi, 0.1, ["assoc"])
    try:
        sg.set_seed(multi, SEED)
        sg.build_subgraph(multi, SubgraphSpec("b", "b"))
        # G2 is still in subgraph (b) through G1–G2, but D1–G2 was only filtered out.
        strict = sg.unanswerable_candidates(multi, "b", absence_adj="main.adj")
        rows = set(multi.execute(f"SELECT s, r, y FROM {strict}").fetchall())
        assert ("D1", "assoc", "G2") not in rows
        assert not sg.is_absent(multi, "D1", "G2", absence_adj="main.adj")
        loose = sg.unanswerable_candidates(multi, "b", absence_adj="adj")
        assert ("D1", "assoc", "G2") in set(multi.execute(f"SELECT s, r, y FROM {loose}").fetchall())
    finally:
        sg.clear_score_filter(multi)


# ---- directed-inversion count ----

def test_same_type_relations(multi):
    st = sg.same_type_relations(multi)
    assert {r: v["type"] for r, v in st.items()} == {
        "parent": "disease", "parent_u": "disease", "pheno_parent": "phenotype", "ppi": "gene",
    }
    # parent is mixed: four disease→disease edges and one phenotype→disease edge.
    assert (st["parent"]["same_type_edges"], st["parent"]["edges"]) == (4, 5)


def test_direction_inversions(multi):
    sg.build_subgraph(multi, SubgraphSpec("b", "b"))
    sg.single_hop_facts(multi, "b")
    inv = sg.direction_inversions(multi, "b", ["parent", "parent_u", "pheno_parent"])
    # D5→D2 and D2→D5 are both stored: reversing either gives a true fact.
    # P1→D3 is in (b) but joins two types, so it is not counted.
    assert inv["parent"] == {
        "edges": 4, "single_hop_facts": 4, "stored_directed": 4, "reverse_also_present": 2,
        "reversed_reading_differs": 2, "single_hop_reversed_reading_differs": 2,
    }
    # Stored undirected: the source can't say which reading holds, so it counts.
    assert inv["parent_u"]["reversed_reading_differs"] == 1
    assert inv["parent_u"]["single_hop_reversed_reading_differs"] == 1
    # P1→P2 is in (b) through P1 but touches no seed node.
    assert inv["pheno_parent"]["edges"] == 1 and inv["pheno_parent"]["single_hop_facts"] == 0


# ---- PrimeKG relation-source mapping ----

def test_apply_primekg_sources_maps_by_base_relation():
    con = duckdb.connect()
    con.execute("CREATE TABLE edges(eid INT, rel VARCHAR, sources VARCHAR[])")
    con.execute(
        "INSERT INTO edges VALUES (1, 'drug_protein/target', NULL), (2, 'phenotype_protein', NULL),"
        " (3, 'off-label use', NULL), (4, 'not_a_relation', NULL)"
    )
    load.apply_primekg_sources(con)
    got = dict(con.execute("SELECT rel, sources FROM edges").fetchall())
    assert got["drug_protein/target"] == ["DrugBank"]
    assert got["phenotype_protein"] == ["DisGeNET"]
    assert got["off-label use"] == ["DrugCentral"]
    assert got["not_a_relation"] == ["unmapped"]
