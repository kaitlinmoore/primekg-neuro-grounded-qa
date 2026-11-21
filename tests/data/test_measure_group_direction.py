"""Tests for ``scripts/data/measure_group_direction.py`` on a hand-built fixture.

Fixture (``tests/fixtures/data/group_direction/``). Reference hierarchy (OptimusKG
form, parent -> child): R(1) -> A(2), B(3); A -> A1(4), A2(5); B -> B1(6); X(10) ->
X1(11); and HP 1 -> HP 2. Groups: ``g_a`` = {A, A1} (named like A), ``g_m`` = {A1, B},
``g_n`` = {A, B1}, ``g_p`` = {A1, B1}. Directed store edges, stored child -> parent,
with the class each should get:

| Edge | Stored | Class | Why |
|---|---|---|---|
| E:000001 | A2 -> g_a | agrees | A is A2's parent; on a cycle with E:000005 |
| E:000002 | X1 -> A | no_ancestry | both known, unrelated |
| E:000003 | MONDO:99 -> A | unknown | MONDO:99 isn't in the reference |
| E:000004 | g_a -> R | agrees | R is above A and A1 |
| E:000005 | g_a -> A2 | reversed | A (in g_a) is A2's parent |
| E:000006 | g_m -> g_n | mixed | A1 under A; B over B1; no containment |
| E:000007 | g_p -> A | partial_agree | A1 under A; B1 not |
| E:000010 | HP 2 -> HP 1 | agrees | |

E:000008 and E:000009 are undirected ``disease_protein`` edges.
"""

import csv
import json
from pathlib import Path

import duckdb
import pytest

import measure_group_direction as m

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "data" / "group_direction"


@pytest.fixture
def reference_db(tmp_path):
    db = tmp_path / "reference.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE edges(u VARCHAR, v VARCHAR, rel VARCHAR)")
    with (FIX / "reference_edges.csv").open(encoding="utf-8") as f:
        con.executemany("INSERT INTO edges VALUES (?, ?, ?)", [(r["u"], r["v"], r["rel"]) for r in csv.DictReader(f)])
    con.close()
    return db


@pytest.fixture
def report(reference_db):
    return m.measure(FIX, None, reference_db)


def by_id(report):
    return {e["id"]: e for e in report["edges"]}


# ---- classification -------------------------------------------------------------------


def test_classes_match_the_hand_table(report):
    got = {k: e["class"] for k, e in by_id(report).items()}
    assert got == {
        "E:000001": "agrees", "E:000002": "no_ancestry", "E:000003": "unknown",
        "E:000004": "agrees", "E:000005": "reversed", "E:000006": "mixed",
        "E:000007": "partial_agree", "E:000010": "agrees",
    }


def test_grouped_flag_and_head_member_reading(report):
    e = by_id(report)
    assert {k for k, v in e.items() if v["grouped"]} == {"E:000001", "E:000004", "E:000005", "E:000006", "E:000007"}
    # g_a is named like its member A, so its head reading is A alone.
    assert e["E:000005"]["head_class"] == "reversed"
    assert e["E:000004"]["head_class"] == "agrees"


def test_classify_equivalent_and_partial_reversed():
    parents = {"c": {"p"}}
    anc = m.ancestor_fn(parents)
    known = {"c", "p", "q"}
    assert m.classify(["c"], ["c"], anc, known) == "equivalent"
    # object {c, q} under subject {p}? p is c's parent: a reversed pair only, q unrelated.
    assert m.classify(["p"], ["c", "q"], anc, known) == "partial_reversed"


# ---- cycles ---------------------------------------------------------------------------


def test_the_fixture_cycle(report):
    assert report["cycle_edges"] == ["E:000001", "E:000005"]
    assert len(report["cycles"]) == 1


def test_simple_cycles_lists_each_cycle_once():
    arcs = [("a", "b"), ("b", "c"), ("c", "a"), ("b", "a"), ("x", "y")]
    assert sorted(m.simple_cycles(arcs)) == [["a", "b"], ["a", "b", "c"]]
    assert m.simple_cycles([("a", "b"), ("b", "c")]) == []


# ---- options --------------------------------------------------------------------------


def changed(report, opt):
    return report["options"][opt]


def test_option_actions(report):
    assert changed(report, "D")["edges_changed"] == 0
    assert changed(report, "A")["by_action"] == {"undirect": 4}       # all grouped but E:000004
    assert changed(report, "A2")["by_action"] == {"undirect": 3}      # E:000001 (cycle), 05, 06
    assert changed(report, "B")["by_action"] == {"undirect": 3, "reverse": 1}
    assert changed(report, "C")["by_action"] == {"drop": 4}
    assert changed(report, "E")["by_action"] == {"undirect": 7}       # every disease_disease edge
    assert changed(report, "F")["by_action"] == {"undirect": 5}       # every grouped one
    assert changed(report, "F-label")["by_action"] == {"relabel": 5}


def test_mixed_flags_and_cycles_after(report):
    assert changed(report, "A")["mixed_flag_relations"] == ["disease_disease"]
    assert changed(report, "C")["mixed_flag_relations"] == []
    assert changed(report, "E")["mixed_flag_relations"] == []
    assert changed(report, "F-label")["mixed_flag_relations"] == []
    assert changed(report, "D")["cycles_after"] == 1
    assert all(changed(report, o)["cycles_after"] == 0 for o in ("A", "A2", "B", "C", "E", "F"))


def test_renumbering(report):
    # Flags alone keep the numbering; drops shift every later ID, across relations.
    assert changed(report, "A")["edge_ids_renumbered"] == 0
    assert changed(report, "C")["edge_ids_renumbered"] == 6
    assert changed(report, "C")["edge_ids_renumbered_outside_disease_disease"] == 3
    edges = [m.Edge("E:000002", "b", "r", "c", True), m.Edge("E:000001", "a", "r", "b", True)]
    assert m.renumber(edges) == {"E:000001": "E:000001", "E:000002": "E:000002"}


def test_relabel_keeps_one_flag_per_label():
    e = [m.Edge("E:1", "a", "disease_disease", "b", True), m.Edge("E:2", "c", "disease_disease", "d", True)]
    out = m.apply_option(e, {"E:2": "relabel"})
    assert m.mixed_flag_relations(out) == []
    assert out[1].relation == m.RELABEL and out[1].directed is False


# ---- items ----------------------------------------------------------------------------


def item(**kw):
    base = dict(id="q-x", template_id="mh_bridge.subtype_assoc", gold=[], premise=None, gaps=[],
                params=[], relation_key="subtype_assoc", route=[])
    base.update(kw)
    return m.ItemView(**base)


def test_item_effects():
    edges = {
        "E:1": m.Edge("E:1", "s1", "disease_disease", "g", True),
        "E:2": m.Edge("E:2", "s2", "disease_disease", "p", True),
    }
    gold = [("s1", "disease_disease", "g", True, ("E:1",)), ("s2", "disease_disease", "p", True, ("E:2",))]
    it = item(gold=gold, params=["p", "NCBIGene:1"],
              route=[{"tool": "get_neighbors", "entity_id": "g", "relation": "disease_disease"}])
    ch = {"disease_disease": {"s1", "g"}}
    assert m.item_effects(it, {"E:1": "undirect"}, edges, ch) == ["gold_undirected", "mixed_flags"]
    assert m.item_effects(it, {"E:1": "relabel"}, edges, ch) == ["gold_relabelled"]
    assert m.item_effects(it, {"E:1": "drop"}, edges, ch) == ["gold_dropped", "route_counts_change"]
    # A role bridge naming an entity whose hierarchy edge changes.
    assert "role_template_at_changed_entity" in m.item_effects(it, {"E:2": "undirect"}, edges,
                                                               {"disease_disease": {"s2", "p"}})
    # A phenotype_kind bridge isn't affected by disease_disease changes at its disease.
    pk = item(template_id="mh_bridge.phenotype_kind", relation_key="phenotype_kind", params=["p", "HP:1"])
    assert m.item_effects(pk, {"E:2": "undirect"}, edges, {"disease_disease": {"s2", "p"}}) == []


def test_hierarchy_roles_read():
    assert m.hierarchy_roles_read("pa_list2", "list2", ["X", "disease_disease:parents", "contraindication"]) \
        == {"disease_disease"}
    assert m.hierarchy_roles_read("un_bridge.phenotype_kind", "phenotype_kind", ["X", "Y"]) == {"phenotype_phenotype"}
    assert m.hierarchy_roles_read("fp_reversed.disease_disease", "disease_disease", ["X"]) == {"disease_disease"}
    assert m.hierarchy_roles_read("sh_evid.disease_protein", "disease_protein", ["X"]) == set()


def test_report_renders_without_items(report):
    # With no items the report still renders and serializes.
    md = m.markdown(report)
    assert "fixture-group-direction" in md
    json.dumps(report)


def test_option_g_changes_no_edge(report):
    g = changed(report, "G")
    assert g["by_action"] == {"exclude": 5}
    assert g["edge_ids_renumbered"] == 0 and g["mixed_flag_relations"] == [] and g["cycles_after"] == 1
