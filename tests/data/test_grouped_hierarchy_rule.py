"""Tests for the grouped-end rule (generator 1.2.0): no item rests on a hierarchy edge
with a PrimeKG grouped disease (``group:``) at either end.

The rule is one predicate, ``qgen.candidates.grouped_hierarchy``, applied by
``grouped_hierarchy_problem`` at each place an item can rest on such an edge:

- **gold**: a gold point on one (``sh_evid.disease_disease``, list answers, bridges);
- **premise**: a premise triple on one (``fp_evid.disease_disease``);
- **gap**: a gap triple on one;
- **role reads**: a template that picks answers by child/parent role (child/parent
  list clauses in ``sh_list``/``un_list``/``sh_list2``/``pa_list2``, the role bridges
  ``subtype_assoc`` and ``phenotype_kind``) at an entity that has one, even when the
  edge itself isn't in the item.

The generator applies it at the ``domain`` stage when ``Filters.grouped_hierarchy_out``
is on, and the item check (``qgen.verify``) applies the same function to written items
when the manifest records the filter. Hand-built edges here; the generator world
(``tests/fixtures/data/qgen/gen_world.json``, one grouped seed with a child) covers
the pipeline end to end.
"""

from __future__ import annotations

import json

import pytest
import qgen_templates as qt
from neuro_grounded_qa.contracts import EdgeEvidence, Entity
from neuro_grounded_qa.data import store_files as sf
from qgen import files as F
from qgen.answers import Adjacency
from qgen.candidates import (
    Cand,
    Filters,
    Graph,
    domain_problem,
    grouped_hierarchy,
    grouped_hierarchy_problem,
    role_reads,
)

GROUP = "group:alpha_disease"
PLAIN, PLAIN2, CHILD = "MONDO:0000001", "MONDO:0000002", "MONDO:0000003"
GENE = "NCBIGene:1"
TPL = {t.id: t for t in qt.catalog()}

_n = iter(range(1, 1000))


def edge(s: str, rel: str, o: str, directed: bool | None = None) -> EdgeEvidence:
    def ent(i: str) -> Entity:
        return Entity(id=i, name=i, type="gene/protein" if i.startswith("NCBI") else "disease")
    d = rel in ("disease_disease", "phenotype_phenotype") if directed is None else directed
    return EdgeEvidence(id=f"E:{next(_n):06d}", subject=ent(s), relation=rel, object=ent(o), directed=d)


def edges_at(edges: list[EdgeEvidence]):
    adj = Adjacency(edges)
    return adj.edges


def problem(tpl_id: str, params, gold=(), extra=(), edges=()):
    return grouped_hierarchy_problem(TPL[tpl_id], params, list(gold), list(extra), edges_at(list(edges)))


# ---------------------------------------------------------------------------------------
# The predicate
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("s,rel,o,want", [
    (PLAIN, "disease_disease", GROUP, True),       # grouped parent
    (GROUP, "disease_disease", PLAIN, True),       # grouped child
    (GROUP, "disease_disease", "group:beta", True),
    (PLAIN, "disease_disease", PLAIN2, False),     # two plain terms: checked, kept
    (GROUP, "disease_protein", GENE, False),       # not a hierarchy relation
    (GROUP, "phenotype_phenotype", PLAIN, True),   # any hierarchy relation (none occurs in the store)
    ("primekg:12", "disease_disease", PLAIN, False),
])
def test_predicate(s, rel, o, want):
    assert grouped_hierarchy(s, rel, o) is want


# ---------------------------------------------------------------------------------------
# Each place the rule applies
# ---------------------------------------------------------------------------------------


def test_gold_on_a_grouped_hierarchy_edge():
    e = edge(CHILD, "disease_disease", GROUP)
    gold = [(e.subject.id, e.relation, e.object.id)]
    assert problem("sh_evid.disease_disease", (CHILD, "disease_disease", GROUP), gold, edges=[e]) \
        == "grouped_hierarchy_gold"
    plain = edge(CHILD, "disease_disease", PLAIN)
    assert problem("sh_evid.disease_disease", (CHILD, "disease_disease", PLAIN),
                   [(CHILD, "disease_disease", PLAIN)], edges=[plain, e]) is None


def test_gold_on_a_grouped_end_of_another_relation_is_fine():
    gold = [(GROUP, "disease_protein", GENE)]
    assert problem("sh_evid.disease_protein", (GROUP, "disease_protein", GENE), gold,
                   edges=[edge(GROUP, "disease_protein", GENE), edge(CHILD, "disease_disease", GROUP)]) is None


def test_premise_on_a_grouped_hierarchy_pair():
    prem = [(PLAIN, "disease_disease", GROUP)]
    assert problem("fp_evid.disease_disease", (PLAIN, "disease_disease", GROUP), extra=prem) \
        == "grouped_hierarchy_premise_or_gap"
    assert problem("fp_evid.disease_disease", (PLAIN, "disease_disease", PLAIN2),
                   extra=[(PLAIN, "disease_disease", PLAIN2)]) is None


def test_gap_on_a_grouped_hierarchy_pair():
    # No current template writes a hierarchy gap triple; the rule covers one anyway.
    gaps = [(GENE, "disease_protein", PLAIN), (CHILD, "disease_disease", GROUP)]
    assert problem("pa_evid2.disease_protein", ("E:000001", CHILD, GROUP),
                   gold=[(PLAIN, "disease_protein", GENE)], extra=gaps) == "grouped_hierarchy_premise_or_gap"


@pytest.mark.parametrize("tpl_id", ["sh_list.disease_disease:children", "un_list.disease_disease:parents"])
def test_role_list_at_a_seed_with_a_grouped_edge(tpl_id):
    # PLAIN's children list (or its empty parents list) reads the stored direction
    # of PLAIN's grouped edge, though that edge is not in the item.
    edges = [edge(CHILD, "disease_disease", PLAIN), edge(PLAIN, "disease_disease", GROUP)]
    gold = [(CHILD, "disease_disease", PLAIN)] if tpl_id.startswith("sh_") else []
    assert problem(tpl_id, (PLAIN,), gold, edges=edges) == "grouped_hierarchy_role"
    # without the grouped edge, the same list is fine
    assert problem(tpl_id, (PLAIN,), gold, edges=edges[:1]) is None


def test_role_list_ignores_grouped_edges_of_other_relations():
    edges = [edge(CHILD, "disease_disease", PLAIN), edge(GROUP, "disease_protein", GENE),
             edge(PLAIN, "disease_protein", GENE)]
    assert problem("sh_list.disease_disease:children", (PLAIN,), [(CHILD, "disease_disease", PLAIN)],
                   edges=edges) is None
    # a non-role list at a group reads no direction
    assert problem("sh_list.disease_protein", (GROUP,), [(GROUP, "disease_protein", GENE)], edges=edges) is None


@pytest.mark.parametrize("tpl_id", ["sh_list2", "pa_list2"])
def test_role_reads_in_either_clause_of_a_two_list(tpl_id):
    edges = [edge(PLAIN, "disease_disease", GROUP), edge(PLAIN, "disease_protein", GENE)]
    gold = [(PLAIN, "disease_protein", GENE)]
    for params in ((PLAIN, "disease_protein", "disease_disease:children"),
                   (PLAIN, "disease_disease:children", "disease_protein")):
        assert problem(tpl_id, params, gold, edges=edges) == "grouped_hierarchy_role"
    assert problem(tpl_id, (PLAIN, "disease_protein", "indication"), gold, edges=edges) is None


@pytest.mark.parametrize("tpl_id", ["mh_bridge.subtype_assoc", "un_bridge.subtype_assoc"])
def test_subtype_bridge_reads_roles_at_x(tpl_id):
    # "Which subtypes of x are associated with y?": every disease_disease edge at x
    # has its direction read to find the subtypes.
    edges = [edge(CHILD, "disease_disease", PLAIN), edge(CHILD, "disease_protein", GENE),
             edge(PLAIN, "disease_disease", GROUP)]
    assert role_reads(TPL[tpl_id], (PLAIN, GENE)) == [(PLAIN, "disease_disease")]
    assert problem(tpl_id, (PLAIN, GENE), edges=edges) == "grouped_hierarchy_role"
    assert problem(tpl_id, (PLAIN, GENE), edges=edges[:2]) is None


def test_phenotype_kind_reads_roles_at_y_and_other_bridges_read_none():
    assert role_reads(TPL["mh_bridge.phenotype_kind"], (PLAIN, "HP:0000001")) == \
        [("HP:0000001", "phenotype_phenotype")]
    for b in qt.BRIDGES:
        if not (b.m_role_x or b.m_role_y):
            assert role_reads(TPL[f"mh_bridge.{b.key}"], (PLAIN, GENE)) == []


def test_reversed_premise_template_is_covered_by_its_gold_not_by_roles():
    assert role_reads(TPL["fp_reversed.disease_disease"], (GROUP, "disease_disease", CHILD)) == []
    e = edge(CHILD, "disease_disease", GROUP)
    assert problem("fp_reversed.disease_disease", (GROUP, "disease_disease", CHILD),
                   gold=[(CHILD, "disease_disease", GROUP)], extra=[(GROUP, "disease_disease", CHILD)],
                   edges=[e]) == "grouped_hierarchy_gold"


# ---------------------------------------------------------------------------------------
# The domain stage and its switch
# ---------------------------------------------------------------------------------------


class _Store:
    def __init__(self, edges):
        self.by_id = {e.id: e for e in edges}

    def get(self, eid):
        return self.by_id[eid]


def test_domain_stage_applies_the_rule_only_when_the_filter_is_on():
    edges = [edge(CHILD, "disease_disease", PLAIN), edge(PLAIN, "disease_disease", GROUP)]
    g = Graph(_Store(edges), [e.id for e in edges], [{"id": PLAIN, "group": "AD"}])
    tpl = TPL["sh_list.disease_disease:children"]
    c = Cand(tpl, (PLAIN,), "AD", (PLAIN,), lambda m: "", gold_edges=[edges[0]], answer_ids=(CHILD,))
    assert domain_problem(c, set(), Filters(), g) == "grouped_hierarchy_role"
    assert domain_problem(c, set(), Filters.revision2(), g) is None
    assert Filters().as_json()["grouped_hierarchy_out"] is True
    assert not Filters.none().grouped_hierarchy_out and not Filters.revision1().grouped_hierarchy_out


# ---------------------------------------------------------------------------------------
# End to end on the generator world
# ---------------------------------------------------------------------------------------


def test_world_items_keep_out_grouped_hierarchy_edges(tmp_path):
    import qgen_world as W
    from qgen.cli import main
    from neuro_grounded_qa.tools import load_store

    store_dir, db, spec = W.build_world(tmp_path)
    out = tmp_path / "items" / spec["build_id"]
    args = ["--store", str(store_dir), "--full-graph", str(db)]
    assert main(["generate", *args, "--out", str(out), "--train", "60", "--heldout", "60"]) == 0
    manifest = json.loads((out / F.MANIFEST).read_text())
    assert manifest["filters"]["grouped_hierarchy_out"] is True
    reasons = {k for d in manifest["pool"]["domain_failures"].values() for k in d}
    # the world's grouped seed has a child: the rule drops gold, premise and role candidates
    assert {"grouped_hierarchy_gold", "grouped_hierarchy_premise_or_gap", "grouped_hierarchy_role"} <= reasons
    store = load_store(store_dir)
    adj = Adjacency([store.get(r["id"]) for r in sf.read_jsonl(store_dir / sf.EDGES)])
    for split in ("train", "heldout"):
        for r in sf.read_jsonl(out / F.SPLIT_FILES[split]):
            notes = json.loads(r["notes"])
            trips = ([r["premise"]] if r["premise"] else []) + [g["triple"] for g in r["graph_gaps"] if g["triple"]]
            assert grouped_hierarchy_problem(
                TPL[r["template_id"]], notes["template_params"],
                [(g["subject_id"], g["relation"], g["object_id"]) for g in r["gold_points"]],
                [(t["subject_id"], t["relation"], t["object_id"]) for t in trips], adj.edges) is None
    assert main(["check", *args, "--items", str(out)]) == 0


def test_check_enforces_the_rule_when_the_manifest_records_it(tmp_path):
    """A set written without the rule passes ``check``; the same files fail it once the
    manifest says the rule was applied (the world's grouped-seed items break it)."""
    import qgen_world as W
    from qgen.cli import main

    store_dir, db, spec = W.build_world(tmp_path)
    out = tmp_path / "items" / spec["build_id"]
    args = ["--store", str(store_dir), "--full-graph", str(db)]
    assert main(["generate", *args, "--out", str(out), "--train", "60", "--heldout", "60",
                 "--no-filters", "--allow-shortfall"]) == 0
    assert main(["check", *args, "--items", str(out), "--split", "train"]) == 0
    m = json.loads((out / F.MANIFEST).read_text())
    m["filters"]["grouped_hierarchy_out"] = True
    (out / F.MANIFEST).write_text(json.dumps(m))
    report = tmp_path / "r.json"
    assert main(["check", *args, "--items", str(out), "--split", "train", "--json", str(report)]) == 1
    checks = json.loads(report.read_text())["splits"]["train"]["item_checks"]
    assert checks["domain"]["fail"] > 0
