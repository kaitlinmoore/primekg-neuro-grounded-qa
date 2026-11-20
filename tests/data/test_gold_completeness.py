"""Tests for the gold-completeness check (``scripts/data/qgen/answers.py``) and the
answer rules in the template catalog (``qgen_templates.ANSWER_RULE``), on the
synthetic generator world (``tests/fixtures/data/qgen/gen_world.json``, built by
``qgen_world.py``).

For every template family the world yields candidates for:

- **pass**: every valid candidate's gold equals its answer set;
- **missing answer**: gold without one of its points fails (for families with gold);
  for families without gold (``un_*``, ``fp_evid``), a store that gains a fact
  answering the question fails;
- **extra non-answer**: gold with an added point that doesn't answer the question
  fails.

Plus: every catalog family has a rule, unknown templates and bad parameters are
refused, and the generator drops a candidate whose gold a shape generator left
incomplete.
"""

from __future__ import annotations

import pytest
import qgen_templates as qt
import qgen_world as W
from neuro_grounded_qa.contracts import EdgeEvidence, Entity, GoldPoint
from neuro_grounded_qa.data import store_files as sf
from neuro_grounded_qa.data.build import HIERARCHY_CHILD_END
from neuro_grounded_qa.tools import load_store
from qgen import answers as qa
from qgen import checks as qc
from qgen.candidates import Filters, build_pool
from qgen.selection import Plan

duckdb = pytest.importorskip("duckdb")

FAMILIES = sorted({qt.family(t.id) for t in qt.catalog()})


class PoolWorld:
    """The fixture world's store, edges and checked candidate pool."""

    def __init__(self, root) -> None:
        self.store_dir, self.db, self.spec = W.build_world(root)
        self.store = load_store(self.store_dir)
        self.meta = sf.read_build_meta(self.store_dir)
        self.edge_ids = [r["id"] for r in sf.read_jsonl(self.store_dir / sf.EDGES)]
        self.edges = [self.store.get(e) for e in self.edge_ids]
        self.adj = qa.Adjacency(self.edges)
        index_of = {r["id"]: r["primekg_index"] for r in sf.read_jsonl(self.store_dir / sf.ENTITY_SOURCES)}
        self.fg = qc.FullGraph(duckdb.connect(str(self.db), read_only=True), index_of, HIERARCHY_CHILD_END)

    def pool(self, filters: Filters | None = None):
        pool, _, _ = build_pool(self.store, self.edge_ids, self.meta["config"]["seeds"], self.fg,
                                self.spec["build_id"], split_seed=Plan().split_seed,
                                heldout_share=Plan().heldout_share, generator_version="test", filters=filters)
        return pool


@pytest.fixture(scope="module")
def pw(tmp_path_factory):
    w = PoolWorld(tmp_path_factory.mktemp("gold"))
    yield w
    w.fg.con.close()


@pytest.fixture(scope="module")
def pool(pw):
    return pw.pool()


@pytest.fixture(scope="module")
def by_family(pool):
    out: dict = {}
    for c in pool.valid:
        out.setdefault(qt.family(c.tpl.id), []).append(c)
    return out


# ---------------------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------------------


def test_every_catalog_family_has_a_known_answer_rule():
    assert set(qt.ANSWER_RULE) == set(FAMILIES)
    assert set(qt.ANSWER_RULE.values()) <= set(qt.ANSWER_RULES)
    for t in qt.catalog():
        assert qt.answer_rule(t.id) in qt.ANSWER_RULES


def test_a_template_without_a_rule_is_refused(pw, pool):
    with pytest.raises(KeyError):
        qt.answer_rule("new_family.disease_protein")
    item = pool.valid[0].item.model_copy(update={"template_id": "new_family.disease_protein"})
    assert qa.gold_completeness(item, ["X"], pw.adj) == qa.GoldCompleteness(False, "no_rule")


def test_parameters_that_dont_fit_the_template_are_refused(pw, by_family):
    c = by_family["sh_list"][0]
    assert qa.gold_completeness(c.item, [*c.params, "extra"], pw.adj).reason == "bad_params"
    c = by_family["sh_evid2"][0]
    assert qa.gold_completeness(c.item, ["E:999999", c.params[1]], pw.adj).reason == "bad_params"


def test_the_rules_are_not_in_the_catalog_hash(monkeypatch):
    # The hash ties an item set to its wording and templates; the answer rules are a
    # check on candidates, recorded through the generator version and commit.
    h = qt.catalog_sha256()
    monkeypatch.setitem(qt.ANSWER_RULE, "sh_list", "list2")
    assert qt.catalog_sha256() == h


# ---------------------------------------------------------------------------------------
# Pass, missing answer, extra non-answer: every family
# ---------------------------------------------------------------------------------------


def test_the_world_covers_every_family(by_family):
    assert sorted(by_family) == FAMILIES


def test_no_candidate_of_the_world_fails_and_every_valid_one_passes(pw, pool):
    assert dict(pool.gold_fail) == {}
    assert all(c.item is not None and pool.funnel[c.tpl.id]["gold_complete"] for c in pool.valid)
    for c in pool.valid:
        assert qa.gold_completeness(c.item, list(c.params), pw.adj).ok, c.tpl.id


@pytest.mark.parametrize("fam", FAMILIES)
def test_complete_gold_passes(pw, by_family, fam):
    for c in by_family[fam]:
        r = qa.gold_completeness(c.item, list(c.params), pw.adj)
        assert r == qa.GoldCompleteness(True, "ok"), c.tpl.id
        # the answer set is the gold, as facts
        gold = {qc.fact_key(g.subject_id, g.relation, g.object_id, g.directed) for g in c.item.gold_points}
        assert qa.answer_facts(c.tpl.id, list(c.params), pw.adj) == gold


def _new(eid: str, etype: str) -> Entity:
    return Entity(id=eid, name=f"Injected {eid}", type=etype)


def _edge(eid: str, s: Entity, rel: str, o: Entity, directed: bool = False) -> EdgeEvidence:
    return EdgeEvidence(id=eid, subject=s, relation=rel, object=o, directed=directed)


def _hop(eid: str, end: Entity, rel: str, m: Entity, m_role: str | None) -> EdgeEvidence:
    """An edge between ``end`` and ``m`` under ``rel``, with ``m`` in ``m_role``."""
    if m_role == "child":
        return _edge(eid, m, rel, end, True)
    if m_role == "parent":
        return _edge(eid, end, rel, m, True)
    return _edge(eid, end, rel, m)


def answering_edges(c, store) -> list[EdgeEvidence]:
    """Store edges that would answer candidate ``c``'s question (for gold-less items)."""
    rule, key, p = qt.answer_rule(c.tpl.id), c.tpl.relation_key, c.params
    ent = store.entity
    if rule == "list":
        rel, _, role = key.partition(":")
        new = _new("NEW", "disease")
        if role == "children":
            return [_edge("E:900001", new, rel, ent(p[0]), True)]
        if role == "parents":
            return [_edge("E:900001", ent(p[0]), rel, new, True)]
        return [_edge("E:900001", ent(p[0]), rel, new)]
    if rule == "pair_any_relation":
        return [_edge("E:900001", ent(p[0]), "some_relation", ent(p[1]))]
    if rule == "stated_facts":  # fp_evid: the absent statement, stated
        subj, rel, obj = p
        return [_edge("E:900001", ent(subj), rel, ent(obj), rel == "disease_disease")]
    if rule == "bridge_paths":
        b = next(x for x in qt.BRIDGES if x.key == key)
        m = _new("NEW", b.m_type)
        return [_hop("E:900001", ent(p[0]), b.r_x, m, b.m_role_x), _hop("E:900002", ent(p[1]), b.r_y, m, b.m_role_y)]
    raise AssertionError(f"no injection for {rule}")


@pytest.mark.parametrize("fam", FAMILIES)
def test_a_missing_true_answer_fails(pw, by_family, fam):
    for c in by_family[fam]:
        gps = c.item.gold_points
        if gps:
            item = c.item.model_copy(update={"gold_points": gps[1:]})
            r = qa.gold_completeness(item, list(c.params), pw.adj)
        else:
            adj = qa.Adjacency(pw.edges + answering_edges(c, pw.store))
            r = qa.gold_completeness(c.item, list(c.params), adj)
        assert r.reason == "missing_answer" and r.missing >= 1 and not r.extra, c.tpl.id


@pytest.mark.parametrize("fam", FAMILIES)
def test_an_extra_non_answer_fails(pw, by_family, fam):
    for c in by_family[fam]:
        answers = qa.answer_facts(c.tpl.id, list(c.params), pw.adj)
        e = next(e for e in pw.edges if qa.fact_of(e) not in answers)
        extra = GoldPoint(id="gpx", subject_id=e.subject.id, relation=e.relation, object_id=e.object.id,
                          evidence=[e.id], directed=e.directed)
        item = c.item.model_copy(update={"gold_points": [*c.item.gold_points, extra]})
        r = qa.gold_completeness(item, list(c.params), pw.adj)
        assert r == qa.GoldCompleteness(False, "non_answer_in_gold", 0, 1), c.tpl.id


def test_facts_not_edges_are_compared(pw, by_family):
    # A second store edge stating a gold fact is the same answer, not a missing one.
    c = next(c for c in by_family["sh_list"] if c.item.gold_points)
    e = pw.store.get(c.item.gold_points[0].evidence[0])
    dup = e.model_copy(update={"id": "E:900003"})
    assert qa.gold_completeness(c.item, list(c.params), qa.Adjacency(pw.edges + [dup])).ok


def test_a_hierarchy_fact_matches_in_order_only(pw, by_family):
    # fp_reversed: the answer is the child -> parent edge; the premise's order is not.
    c = by_family["fp_reversed"][0]
    parent, rel, child = c.params
    assert qa.stated(pw.adj, child, rel, parent) and not qa.stated(pw.adj, parent, rel, child)


# ---------------------------------------------------------------------------------------
# The generator refuses incomplete gold
# ---------------------------------------------------------------------------------------


def test_the_generator_drops_a_candidate_whose_gold_is_incomplete(pw, monkeypatch):
    import qgen.candidates as cand

    real = cand.list_edges

    def short(g, s, key):  # a shape-generator bug: lists lose their last edge
        edges = real(g, s, key)
        return edges[:-1] if len(edges) > 1 else edges

    monkeypatch.setattr(cand, "list_edges", short)
    pool = pw.pool()
    failed = {t: dict(v) for t, v in pool.gold_fail.items()}
    assert failed and all(qt.answer_rule(t) in ("list", "list2") for t in failed)
    assert all(set(v) == {"missing_answer"} for v in failed.values())
    for c in pool.valid:  # nothing incomplete got through
        assert qa.gold_completeness(c.item, list(c.params), pw.adj).ok
