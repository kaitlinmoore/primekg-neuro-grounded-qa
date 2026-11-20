"""Tests for the question-generation checks (``scripts/data/qgen_checks.py``), the
template catalog (``qgen_templates.py``) and the feasibility pipeline
(``qgen_feasibility.py``), on synthetic fixtures in ``tests/fixtures/data/qgen/``.

The fixture store (``store.json``), with the tool cap lowered to 2 results so pages
truncate at small sizes:

- ``Disease Alpha`` (D_A) is associated with G_1, G_2, G_3 (E1-E3): its
  ``disease_protein`` page is truncated at 2 (shows E1, E2).
- ``Disease Beta`` (D_B) is associated with G_1, G_S and the fallback node
  ``primekg:77`` (E4, E9, E10): also truncated.
- ``Disease Alpha type 1`` (D_A1) is a subtype of D_A (E5, directed, child first).
- ``Drug One`` (X_1) targets G_1 (E6) and is contraindicated in D_A (E7).
- ``Shared Name`` (G_S, a gene) and ``shared name`` (P_S, a phenotype) share a name.

The full graph (``full_graph.json``) has the store's facts plus two the subgraph left
out: D_B-G_2 (disease_protein) and a chain X_1 -target- G_9 -associated- D_A1, through
a node the store doesn't have.
"""

from __future__ import annotations

import json

import pytest
import qgen_checks as qc
import qgen_templates as qt
from neuro_grounded_qa.contracts import (
    Entity,
    GoldPoint,
    InMemoryEvidenceStore,
    PremiseTriple,
    same_fact,
)
from neuro_grounded_qa.tools import KGToolset, KGToolsetConfig

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def qdir(fixtures_dir):
    return fixtures_dir / "data" / "qgen"


@pytest.fixture
def store(qdir):
    return InMemoryEvidenceStore.from_records(json.loads((qdir / "store.json").read_text()))


@pytest.fixture
def prober(store):
    return qc.Prober(KGToolset(KGToolsetConfig(max_results=2, max_calls=8), store=store))


@pytest.fixture
def full(qdir):
    spec = json.loads((qdir / "full_graph.json").read_text())
    con = duckdb.connect()
    con.execute("CREATE TABLE adj(a VARCHAR, b VARCHAR, eid BIGINT, rel VARCHAR)")
    rows = []
    for i, (u, v, rel) in enumerate(spec["edges"]):
        rows += [(u, v, i, rel), (v, u, i, rel)]
    con.executemany("INSERT INTO adj VALUES (?, ?, ?, ?)", rows)
    con.execute("CREATE TABLE pk_hier_first(rel VARCHAR, parent VARCHAR, child VARCHAR)")
    con.executemany("INSERT INTO pk_hier_first VALUES (?, ?, ?)", spec["hierarchy_first_rows"])
    yield qc.FullGraph(con, spec["index_of"], spec["child_end"])
    con.close()


# ---------------------------------------------------------------------------------------
# Name ambiguity
# ---------------------------------------------------------------------------------------


def test_name_unique_exact_match_passes(store, prober):
    assert qc.name_check(prober.search, store.entity("G_1")) == qc.NameCheck(True, "ok", 0)


def test_name_that_is_a_prefix_of_another_still_passes(store, prober):
    # "Disease Alpha" also matches "Disease Alpha type 1" by prefix; exact ranks first.
    assert qc.name_check(prober.search, store.entity("D_A")).ok


def test_shared_name_fails_for_both_nodes(store, prober):
    # Ties on the same name sort by ID: G_S first, P_S second.
    assert qc.name_check(prober.search, store.entity("P_S")) == qc.NameCheck(False, "not_first", 1)
    assert qc.name_check(prober.search, store.entity("G_S")) == qc.NameCheck(False, "shared_name", 0)


def test_blank_and_unknown_names_fail(prober):
    assert qc.name_check(prober.search, Entity(id="Z", name="  ", type="x")).reason == "blank"
    assert qc.name_check(prober.search, Entity(id="Z", name="Nothing", type="x")).reason == "not_found"


def test_fallback_ids_are_detected():
    assert qc.has_fallback(["G_1", "primekg:77"])
    assert not qc.has_fallback(["G_1", "MONDO:0000001"])


# ---------------------------------------------------------------------------------------
# Pages and routes
# ---------------------------------------------------------------------------------------


def test_page_reflects_the_real_cap(prober):
    p = prober.page("D_A", "disease_protein")
    assert p.ids == {"E:000001", "E:000002"} and p.truncated
    q = prober.page("G_1", "disease_protein")
    assert q.ids == {"E:000001", "E:000004"} and not q.truncated


def test_page_refuses_unknown_entity_or_relation(prober):
    # An unknown ID comes back from the tool as an empty, untruncated page, which
    # would wrongly read as "complete, nothing there".
    with pytest.raises(ValueError):
        prober.page("NOPE", "disease_protein")
    with pytest.raises(ValueError):
        prober.page("D_A", "no_such_relation")


def test_show_uses_the_end_whose_page_has_the_edge(prober):
    # E3 is cut off D_A's truncated page, so only G_3's side shows it.
    sats = qc.satisfiers(qc.Show("E:000003", "D_A", "G_3", "disease_protein"), prober)
    assert sats == [("G_3", "disease_protein"), ("G_3", None)]


def test_rule_out_needs_a_complete_page(prober):
    # D_B-G_2: D_B's page is truncated, G_2's (E2 only) is complete.
    sats = qc.satisfiers(qc.RuleOut("D_B", "disease_protein", "G_2"), prober)
    assert ("G_2", "disease_protein") in sats
    assert ("D_B", "disease_protein") not in sats
    # D_B-G_3: G_3's page is complete too.
    assert qc.satisfiers(qc.RuleOut("D_B", "disease_protein", "G_3"), prober)


def test_rule_out_impossible_when_both_ends_truncated(store):
    p = qc.Prober(KGToolset(KGToolsetConfig(max_results=1), store=store))
    # D_A and D_B both have more than one disease_protein edge.
    assert qc.satisfiers(qc.RuleOut("D_A", "disease_protein", "D_B"), p) == []


def test_complete_list(prober):
    assert qc.satisfiers(qc.Complete("D_A", "disease_protein"), prober) == []
    assert qc.satisfiers(qc.Complete("D_A1", "disease_disease"), prober)


def test_plan_route_is_minimal_and_counts_searches(prober):
    reqs = [
        qc.Show("E:000003", "D_A", "G_3", "disease_protein"),
        qc.RuleOut("D_B", "disease_protein", "G_3"),
    ]
    r = qc.plan_route(prober, ["D_B", "G_3"], reqs)
    # One probe on G_3 meets both: it shows E3 and its complete page rules out D_B.
    assert r.probes == (("G_3", "disease_protein"),)
    assert r.calls == 3
    assert r.as_notes()[0] == {"tool": "search_entities", "entity": "D_B"}


def test_plan_route_fails_when_unmet_or_over_budget(prober):
    assert qc.plan_route(prober, ["D_A"], [qc.Complete("D_A", "disease_protein")]) is None
    reqs = [qc.Show("E:000001", "D_A", "G_1", "disease_protein")]
    assert qc.plan_route(prober, ["D_A", "G_1"], reqs, max_calls=2) is None
    assert qc.plan_route(prober, ["D_A", "G_1"], reqs, max_calls=3).calls == 3


def test_plan_route_respects_allowed_entities(prober):
    reqs = [qc.Show("E:000003", "D_A", "G_3", "disease_protein")]
    # Only G_3 can show E3; forbid probing it and no route exists.
    assert qc.plan_route(prober, ["D_A"], reqs, allowed=lambda e: e != "G_3") is None


def test_plan_route_is_deterministic(prober):
    reqs = [qc.Show("E:000001", "D_A", "G_1", "disease_protein")]
    assert qc.plan_route(prober, ["D_A"], reqs) == qc.plan_route(prober, ["D_A"], reqs)


# ---------------------------------------------------------------------------------------
# Full-graph absence
# ---------------------------------------------------------------------------------------


def test_pair_absence_uses_the_full_graph(full):
    absent = full.absent_pairs([("D_B", "G_2"), ("D_B", "G_3"), ("G_3", "D_B"), ("D_A", "G_1")])
    # D_B-G_2 has no store edge but does have a full-graph edge: not absent.
    assert absent == {("D_B", "G_3"), ("G_3", "D_B")}


def test_pair_absence_counts_any_relation(full):
    # X_1-D_A share only a contraindication edge: still not an absent pair.
    assert full.absent_pairs([("X_1", "D_A")]) == set()


def test_undirected_triples_match_either_order(full):
    t1 = ("X_1", "contraindication", "D_A", False)
    t2 = ("D_A", "contraindication", "X_1", False)
    t3 = ("X_1", "indication", "D_A", False)
    assert full.present_triples([t1, t2, t3]) == {t1, t2}


def test_directed_triples_match_in_order_only(full):
    child_parent = ("D_A1", "disease_disease", "D_A", True)
    reversed_ = ("D_A", "disease_disease", "D_A1", True)
    assert full.present_triples([child_parent, reversed_]) == {child_parent}


def test_directed_triples_follow_each_relations_file_convention(full):
    # anatomy_anatomy's first row is child-first: x = child.
    assert full.present_triples([("AN_C", "anatomy_anatomy", "AN_P", True)]) == {
        ("AN_C", "anatomy_anatomy", "AN_P", True)
    }
    assert full.present_triples([("AN_P", "anatomy_anatomy", "AN_C", True)]) == set()


def test_directed_triple_on_unknown_hierarchy_raises(full):
    with pytest.raises(ValueError):
        full.present_triples([("D_A", "disease_protein", "G_1", True)])


def test_unknown_entity_raises(full):
    with pytest.raises(KeyError):
        full.absent_pairs([("NOPE", "G_1")])


def test_relation_degree(full):
    assert full.relation_degree(["D_A", "D_A1", "G_3"], "disease_protein") == {"D_A": 3, "D_A1": 1, "G_3": 1}
    assert full.relation_degree(["D_A"], "indication") == {"D_A": 0}


def test_shared_neighbors_counts_full_graph_chains(full):
    n = full.shared_neighbors([("X_1", "D_A"), ("X_1", "D_A1"), ("X_1", "D_B"), ("G_3", "D_B")],
                              "drug_protein/target", "disease_protein")
    # X_1-G_1-D_A, X_1-G_1-D_B in the store; X_1-G_9-D_A1 only in the full graph.
    assert n == {("X_1", "D_A"): 1, ("X_1", "D_A1"): 1, ("X_1", "D_B"): 1, ("G_3", "D_B"): 0}


# ---------------------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------------------


def gp(s, r, o, ev, directed=False):
    return GoldPoint(id="gp1", subject_id=s, relation=r, object_id=o, evidence=ev, directed=directed)


def test_gold_point_consistent(store):
    assert qc.gold_point_problems(gp("G_1", "disease_protein", "D_A", ["E:000001"]), store) == []
    assert qc.gold_point_problems(gp("D_A1", "disease_disease", "D_A", ["E:000005"], True), store) == []


def test_gold_point_problems(store):
    assert qc.gold_point_problems(gp("G_1", "disease_protein", "D_A", ["E:999999"]), store) == ["evidence_missing"]
    assert qc.gold_point_problems(gp("G_2", "disease_protein", "D_A", ["E:000001"]), store) == [
        "not_stated_by_evidence"
    ]
    # Directed edge cited by an undirected gold point.
    assert qc.gold_point_problems(gp("D_A1", "disease_disease", "D_A", ["E:000005"]), store) == [
        "directed_mismatch"
    ]
    # Reversed order on a directed edge isn't stated by it.
    assert "not_stated_by_evidence" in qc.gold_point_problems(
        gp("D_A", "disease_disease", "D_A1", ["E:000005"], True), store
    )


def test_relation_directedness_and_triple_flags(store):
    edges = [store.get(f"E:{i:06d}") for i in range(1, 12)]
    flags = qc.relation_directedness(edges)
    assert flags["disease_disease"] is True and flags["disease_protein"] is False
    ok = PremiseTriple(subject_id="D_A", relation="disease_disease", object_id="D_A1", directed=True)
    bad = PremiseTriple(subject_id="D_A", relation="disease_disease", object_id="D_A1", directed=False)
    gone = PremiseTriple(subject_id="X_1", relation="indication", object_id="D_A")
    assert qc.triple_flag_problems(ok, flags) == []
    assert qc.triple_flag_problems(bad, flags) == ["directed_mismatch"]
    assert qc.triple_flag_problems(gone, flags) == ["relation_not_in_store"]


def test_mixed_flags_on_one_relation_raise(store):
    e = store.get("E:000005")
    flipped = e.model_copy(update={"id": "E:000099", "directed": False})
    with pytest.raises(ValueError):
        qc.relation_directedness([e, flipped])


# ---------------------------------------------------------------------------------------
# Split, IDs, duplicates, leakage
# ---------------------------------------------------------------------------------------


def test_split_is_a_pure_function_of_key_and_seed():
    keys = [f"k{i}" for i in range(3000)]
    a = [qc.split_of(k, "s", 1 / 3) for k in keys]
    # Same answer in reverse order and with other keys around: no pool dependence.
    assert a == [qc.split_of(k, "s", 1 / 3) for k in reversed(keys)][::-1]
    share = a.count("heldout") / len(a)
    assert 0.30 < share < 0.37
    assert [qc.split_of(k, "other", 1 / 3) for k in keys] != a


def test_split_share_bounds():
    for bad in (0.0, 1.0, -0.1):
        with pytest.raises(ValueError):
            qc.split_of("k", "s", bad)


def test_item_id_is_stable_and_opaque():
    i = qc.item_id("b1", "sh_list.indication", ["D_A"])
    assert i == qc.item_id("b1", "sh_list.indication", ["D_A"])
    assert i.startswith("q-") and len(i) == 14
    assert "sh_list" not in i and i != qc.item_id("b2", "sh_list.indication", ["D_A"])


@pytest.mark.parametrize("directed", [False, True])
def test_fact_key_agrees_with_same_fact(directed):
    t = [
        PremiseTriple(subject_id="A", relation="r", object_id="B", directed=directed),
        PremiseTriple(subject_id="B", relation="r", object_id="A", directed=directed),
        PremiseTriple(subject_id="A", relation="s", object_id="B", directed=directed),
    ]
    for x in t:
        for y in t:
            kx = qc.fact_key(x.subject_id, x.relation, x.object_id, x.directed)
            ky = qc.fact_key(y.subject_id, y.relation, y.object_id, y.directed)
            assert (kx == ky) == same_fact(x, y)


def test_near_duplicate():
    f1 = frozenset({("r", "A", "B")})
    assert qc.near_duplicate("Which X?", "Totally other words", f1, f1)
    assert qc.near_duplicate("Which drugs treat Disease Alpha?", "which drugs treat disease alpha", frozenset(), frozenset())
    assert not qc.near_duplicate("Which drugs treat Disease Alpha?", "Which genes relate to Disease Beta?",
                                 f1, frozenset({("r", "A", "C")}))


def test_answer_leaks_on_word_boundaries():
    assert qc.answer_leaks("Is Gene One linked to Disease Alpha?", ["gene one"])
    assert not qc.answer_leaks("Which genes apply to Disease Alpha?", ["APP"])  # trailing boundary
    assert not qc.answer_leaks("Which genes apply to Disease Alpha?", ["ply"])  # leading boundary
    assert not qc.answer_leaks("Which genes?", ["", "  "])


# ---------------------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------------------


def test_template_ids_unique_and_well_formed():
    ids = [t.id for t in qt.catalog()]
    assert len(ids) == len(set(ids))
    assert all(t.qtype in qt_types() for t in qt.catalog())


def qt_types():
    return {"single_hop", "multi_hop", "partial", "unanswerable", "false_premise"}


def test_every_text_pattern_is_shared_by_two_answerability_classes():
    """Wording neutrality: no text pattern belongs to one answerability class only."""
    by_pattern: dict = {}
    for t in qt.catalog():
        by_pattern.setdefault((t.shape, t.relation_key), set()).add(t.answerable)
    lonely = {k: v for k, v in by_pattern.items() if len(v) < 2}
    assert lonely == {}


def test_texts_name_entities_only_through_name_slots():
    assert qt.list_text("indication", "Disease Alpha") == "Which drugs are indicated for Disease Alpha?"
    assert qt.evid_text("disease_disease", "Disease Alpha type 1", "Disease Alpha") == (
        "Which edge in the knowledge graph shows that Disease Alpha type 1 is a subtype of Disease Alpha?"
    )
    assert qt.list2_text("indication", "exposure_disease", "Disease Alpha") == (
        "Which drugs are indicated for Disease Alpha, and which exposures are linked to it?"
    )
    for clause, back in qt.LIST_CLAUSE.values():
        assert "{s}" in clause and "{" not in back


# ---------------------------------------------------------------------------------------
# The feasibility pipeline: runs end to end and reports counts only
# ---------------------------------------------------------------------------------------


def test_feasibility_reports_no_names_or_ids(store, full, capsys):
    import qgen_feasibility as qf

    seeds = [{"id": "D_A", "group": "AD"}, {"id": "D_A1", "group": "AD"}, {"id": "D_B", "group": "PD"}]
    edge_ids = [f"E:{i:06d}" for i in range(1, 12)]
    res = qf.feasibility(store, edge_ids, seeds, full, "fixture-qgen",
                         tool_config=KGToolsetConfig(max_results=2))
    text = json.dumps(res) + qf.markdown(res) + capsys.readouterr().err
    for i in range(1, 12):
        e = store.get(f"E:{i:06d}")
        for ent in (e.subject, e.object):
            assert ent.name.casefold() not in text.casefold()
            assert ent.id not in text
        assert e.id not in text
    # Something survived, and the fallback, shared-name and truncated cases were cut.
    funnel = res["funnel"]
    assert funnel["sh_evid.disease_protein"]["unique_text"] >= 1
    assert sum(f["unique_text"] for f in funnel.values()) < sum(f["generated"] for f in funnel.values())
