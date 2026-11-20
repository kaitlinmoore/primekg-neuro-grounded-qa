"""Feasibility of the question-generation design on the built store: counts only.

For every template in ``qgen_templates.catalog()``, this builds the candidates the
design allows from the evidence store, in memory, and runs every validity check the
generator will run (``qgen_checks``):

1. no ``primekg:`` fallback entity;
2. every named entity passes the name rule;
3. no answer entity's name appears in the question;
4. full-graph checks: absent pairs and triples really are absent from the **whole**
   PrimeKG graph, empty lists and empty chains are empty there too, and a
   correctable premise is absent while its contradicting edge is in the store;
5. ``directed`` flags agree with the store (gold points) and the relation's
   convention (premise and gap triples);
6. a route of real tool calls, within the default budget (8 calls, 20 results, no
   pagination), returns every gold edge and makes every absence visible;
7. the candidate validates as a ``QuestionItem``;
8. its question text is not identical to another candidate's.

Then it assigns each surviving candidate to a split by hash (``qgen_checks.split_of``)
and simulates the generator's seeded selection against the targets (type mix,
disease-group quotas, the 40% per-relation cap, fact- and anchor-disjointness), to
show whether the targets can be met and with how much headroom.

**Output is aggregate counts only.** No question text, entity name or ID is printed
or written: the candidates include what will become held-out items, and nobody,
human or AI, may read those (see "Held-out protection" in the design doc). Errors
inside a check are counted by check name, never echoed.

Run from the repo root (needs the ``data`` group for duckdb)::

    uv run --group data python scripts/data/qgen_feasibility.py \\
        "$NGQ_DATA_DIR/store/primekg-neuro-c95" "$NGQ_DATA_DIR/derived/primekg.duckdb"

It writes ``qgen_feasibility.json`` next to the duckdb file (aggregates only) and
prints the markdown tables used in the design doc.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import qgen_checks as qc
import qgen_templates as qt
from neuro_grounded_qa.contracts import (
    EdgeEvidence,
    GoldPoint,
    GraphGap,
    PremiseTriple,
    QuestionItem,
)
from neuro_grounded_qa.data import store_files as sf
from neuro_grounded_qa.data.build import HIERARCHY_CHILD_END
from neuro_grounded_qa.tools import KGToolset, KGToolsetConfig, load_store
from pydantic import ValidationError

GENERATOR_VERSION = "design-0"
"""Recorded in the simulated items' notes. The implementation sets its own."""

SPLIT_SEED = "ngq-split-v1"
"""Proposed split seed. Changing it reassigns every candidate, so it is set once."""
HELDOUT_SHARE = 1 / 3
"""100 of 300 items; each candidate is held out with this probability."""

TOTAL = {"train": 200, "heldout": 100}
TYPE_SHARE = {
    "single_hop": 0.30,
    "multi_hop": 0.25,
    "partial": 0.15,
    "unanswerable": 0.20,
    "false_premise": 0.10,
}
FP_CORRECTABLE_SHARE = 0.5
"""Proposed share of false-premise items that are ``correctable`` (decision open)."""
GROUP_SHARE = {"AD": 0.30, "PD": 0.20, "ALS": 0.15, "FTD": 0.15, "HD": 0.12, "DLB": 0.08}
"""Proposed disease-group quotas (decision open). FTD/ALS seeds count under FTD."""
RELATION_CAP = 0.4
"""No relation (or bridge pattern) supplies more than this share of a type."""
HEADROOM = 3
"""Valid candidates wanted per item, per split and type."""

K_LIST = 5
"""Most answers a list question may have (gold points)."""
K_LIST2 = 6
"""Most gold points over both clauses of a two-list question."""
K_BRIDGE = 3
"""Most intermediates a bridge question may have (gold points = 2 per intermediate)."""
TEXT_DUP_JACCARD = 0.9
"""Question-token Jaccard at or above which two selected items count as duplicates."""


# --------------------------------------------------------------------------------------
# Candidates
# --------------------------------------------------------------------------------------


@dataclass
class Cand:
    """One candidate item, in memory only. Never printed."""

    tpl: qt.Template
    params: tuple[str, ...]
    group: str
    named: tuple[str, ...]
    text: str
    gold_edges: list[EdgeEvidence] = field(default_factory=list)
    answer_ids: tuple[str, ...] = ()
    premise: PremiseTriple | None = None
    gaps: list[GraphGap] = field(default_factory=list)
    route_options: list[tuple[list, set[str] | None]] = field(default_factory=list)
    # full-graph conditions
    need_absent_pairs: list[tuple[str, str]] = field(default_factory=list)
    need_absent_triples: list[tuple[str, str, str, bool]] = field(default_factory=list)
    need_zero_degree: list[tuple[str, str]] = field(default_factory=list)
    need_no_chain: list[tuple[str, str, str, str]] = field(default_factory=list)
    iid: str = ""

    @property
    def entity_ids(self) -> set[str]:
        ids = set(self.named) | set(self.answer_ids)
        for e in self.gold_edges:
            ids |= {e.subject.id, e.object.id}
        if self.premise:
            ids |= {self.premise.subject_id, self.premise.object_id}
        for g in self.gaps:
            if g.triple:
                ids |= {g.triple.subject_id, g.triple.object_id}
        return ids

    def facts(self) -> frozenset:
        keys = {qc.fact_key(e.subject.id, e.relation, e.object.id, e.directed) for e in self.gold_edges}
        if self.premise:
            p = self.premise
            keys.add(qc.fact_key(p.subject_id, p.relation, p.object_id, p.directed))
        for g in self.gaps:
            if g.triple:
                t = g.triple
                keys.add(qc.fact_key(t.subject_id, t.relation, t.object_id, t.directed))
        return frozenset(keys)


class Graph:
    """Store adjacency by entity and relation, plus seed groups."""

    def __init__(self, store, edge_ids: Iterable[str], seeds: list[dict]) -> None:
        self.store = store
        self.nbr: dict[str, dict[str, list[EdgeEvidence]]] = defaultdict(lambda: defaultdict(list))
        self.edges: list[EdgeEvidence] = []
        for eid in edge_ids:  # the store protocol has no edge iterator
            e = store.get(eid)
            self.edges.append(e)
            self.nbr[e.subject.id][e.relation].append(e)
            if e.object.id != e.subject.id:
                self.nbr[e.object.id][e.relation].append(e)
        self.seed_group = {s["id"]: ("FTD" if s["group"] == "FTD/ALS" else s["group"]) for s in seeds}
        self.groups: dict[str, list[str]] = defaultdict(list)
        for s, g in self.seed_group.items():
            self.groups[g].append(s)

    def entity(self, eid: str):
        return self.store.entity(eid)

    def name(self, eid: str) -> str:
        return self.store.entity(eid).name

    def siblings(self, s: str) -> list[str]:
        """Other seeds of ``s``'s group, or every other seed for a one-seed group."""
        sib = [x for x in self.groups[self.seed_group[s]] if x != s]
        return sib or [x for x in self.seed_group if x != s]

    def edges_of(self, a: str, rel: str) -> list[EdgeEvidence]:
        return self.nbr.get(a, {}).get(rel, [])

    def between(self, a: str, b: str) -> list[EdgeEvidence]:
        return [e for es in self.nbr.get(a, {}).values() for e in es if b in (e.subject.id, e.object.id)]


def other(e: EdgeEvidence, a: str) -> str:
    return e.object.id if e.subject.id == a else e.subject.id


def role(e: EdgeEvidence, m: str) -> str | None:
    """``child``/``parent`` for ``m`` on a directed edge, None on an undirected one."""
    if not e.directed:
        return None
    return "child" if e.subject.id == m else "parent"


def list_edges(g: Graph, s: str, key: str) -> list[EdgeEvidence]:
    """Store edges answering list clause ``key`` for seed ``s``."""
    if key.startswith("disease_disease:"):
        want = key.split(":")[1]
        edges = g.edges_of(s, "disease_disease")
        if want == "children":  # s is the parent: s is the object
            return [e for e in edges if e.object.id == s and e.subject.id != s]
        return [e for e in edges if e.subject.id == s and e.object.id != s]
    return g.edges_of(s, key)


def list_relation(key: str) -> str:
    return key.split(":")[0]


def gp_from(edges: Iterable[EdgeEvidence]) -> list[GoldPoint]:
    return [
        GoldPoint(
            id=f"gp{i}", subject_id=e.subject.id, relation=e.relation, object_id=e.object.id,
            evidence=[e.id], directed=e.directed,
        )
        for i, e in enumerate(edges, start=1)
    ]


def premise_of(subj: str, rel: str, obj: str, directed: bool) -> PremiseTriple:
    return PremiseTriple(subject_id=subj, relation=rel, object_id=obj, directed=directed)


def order_for(rel: str, seed: str, partner: str, partner_role: str | None) -> tuple[str, str]:
    """(subject, object) for a fact between ``seed`` and ``partner`` under ``rel``,
    following the store's convention: the disease is subject for disease_* relations,
    the drug/exposure for theirs, the child first for the hierarchy."""
    if rel == "disease_disease":
        return (partner, seed) if partner_role == "child" else (seed, partner)
    if rel in ("indication", "contraindication", "off-label use", "exposure_disease"):
        return (partner, seed)
    return (seed, partner)


# ---- shape generators ---------------------------------------------------------------


def gen_list(g: Graph, tpl: qt.Template) -> Iterable[Cand]:
    key = tpl.relation_key
    rel = list_relation(key)
    for s in sorted(g.seed_group):
        edges = list_edges(g, s, key)
        text = qt.list_text(key, g.name(s))
        reqs = [qc.Complete(s, rel)]
        if tpl.answerable == "yes":
            if not 1 <= len(edges) <= K_LIST:
                continue
            c = Cand(tpl, (s,), g.seed_group[s], (s,), text, gold_edges=list(edges),
                     answer_ids=tuple(other(e, s) for e in edges))
            c.route_options = [(reqs + [qc.Show(e.id, e.subject.id, e.object.id, rel) for e in edges], None)]
        else:
            if edges:
                continue
            # plausible: the relation (same role) is used by a sibling seed
            if not any(list_edges(g, x, key) for x in g.siblings(s)):
                continue
            c = Cand(tpl, (s,), g.seed_group[s], (s,), text)
            c.need_zero_degree.append((s, rel))
            c.route_options = [(reqs, None)]
        yield c


def gen_list2(g: Graph, tpl: qt.Template) -> Iterable[Cand]:
    keys = [k for k in qt.LIST_CLAUSE]
    for s in sorted(g.seed_group):
        for k1 in keys:
            for k2 in keys:
                if k1 == k2 or (k1.startswith("disease_disease") and k2.startswith("disease_disease")):
                    continue
                e1, e2 = list_edges(g, s, k1), list_edges(g, s, k2)
                r1, r2 = list_relation(k1), list_relation(k2)
                text = qt.list2_text(k1, k2, g.name(s))
                reqs = [qc.Complete(s, r1), qc.Complete(s, r2)]
                if tpl.answerable == "yes":
                    if not (e1 and e2) or len(e1) > K_LIST or len(e2) > K_LIST or len(e1) + len(e2) > K_LIST2:
                        continue
                    edges = e1 + e2
                    c = Cand(tpl, (s, k1, k2), g.seed_group[s], (s,), text, gold_edges=edges,
                             answer_ids=tuple(other(e, s) for e in edges))
                    c.route_options = [(reqs, None)]
                    yield c
                else:
                    # exactly one clause empty, at either position
                    full, empty, ke = (e1, e2, k2) if e1 else (e2, e1, k1)
                    if empty or not full or len(full) > K_LIST:
                        continue
                    if not any(list_edges(g, x, ke) for x in g.siblings(s)):
                        continue
                    clause = qt.LIST_CLAUSE[ke][0].format(s=g.name(s))
                    gap = GraphGap(description=f"{clause}: the graph records none", triple=None)
                    c = Cand(tpl, (s, k1, k2), g.seed_group[s], (s,), text, gold_edges=list(full),
                             answer_ids=tuple(other(e, s) for e in full), gaps=[gap])
                    c.need_zero_degree.append((s, list_relation(ke)))
                    c.route_options = [(reqs, None)]
                    yield c


def near_miss_partners(g: Graph, s: str, rel: str, ptype: str) -> list[tuple[str, str | None]]:
    """Partners of ``ptype`` linked by ``rel`` to a sibling of ``s`` but sharing no
    store edge with ``s``: plausible pairs the graph doesn't link. With the partner's
    role on a directed relation."""
    seen: dict[str, str | None] = {}
    for x in g.siblings(s):
        for e in g.edges_of(x, rel):
            p = other(e, x)
            if p == s or p in g.seed_group or g.entity(p).type != ptype:
                continue
            if p not in seen and not g.between(s, p):
                seen[p] = role(e, p)
    return sorted(seen.items())


PARTNER_TYPE = {
    "disease_protein": "gene/protein",
    "disease_phenotype_positive": "effect/phenotype",
    "disease_phenotype_negative": "effect/phenotype",
    "indication": "drug",
    "contraindication": "drug",
    "off-label use": "drug",
    "exposure_disease": "exposure",
    "disease_disease": "disease",
}


def gen_evid(g: Graph, tpl: qt.Template, cap: int) -> Iterable[Cand]:
    rel = tpl.relation_key
    fam = tpl.id.split(".")[0]
    for s in sorted(g.seed_group):
        if fam == "sh_evid":
            for e in g.edges_of(s, rel):
                if e.subject.id == e.object.id:
                    continue
                text = qt.evid_text(rel, g.name(e.subject.id), g.name(e.object.id))
                c = Cand(tpl, (e.subject.id, rel, e.object.id), g.seed_group[s],
                         (e.subject.id, e.object.id), text, gold_edges=[e])
                c.route_options = [([qc.Show(e.id, e.subject.id, e.object.id, rel)], None)]
                yield c
        elif fam == "fp_evid":  # absent premise, answerable "no"
            directed = rel == "disease_disease"
            for p, prole in near_miss_partners(g, s, rel, PARTNER_TYPE[rel])[:cap]:
                subj, obj = order_for(rel, s, p, prole)
                text = qt.evid_text(rel, g.name(subj), g.name(obj))
                c = Cand(tpl, (subj, rel, obj), g.seed_group[s], (subj, obj), text,
                         premise=premise_of(subj, rel, obj, directed))
                c.need_absent_pairs.append((subj, obj))
                c.route_options = [([qc.RuleOut(subj, rel, obj)], None)]
                yield c
        elif fam == "fp_contra":  # premise rel; store holds the opposite
            opp = qt.OPPOSITE[rel]
            for e in g.edges_of(s, opp):
                text = qt.evid_text(rel, g.name(e.subject.id), g.name(e.object.id))
                prem = premise_of(e.subject.id, rel, e.object.id, False)
                c = Cand(tpl, (e.subject.id, rel, e.object.id), g.seed_group[s],
                         (e.subject.id, e.object.id), text, gold_edges=[e], premise=prem)
                c.need_absent_triples.append((e.subject.id, rel, e.object.id, False))
                c.route_options = [(
                    [qc.Show(e.id, e.subject.id, e.object.id, opp), qc.RuleOut(e.subject.id, rel, e.object.id)],
                    None,
                )]
                yield c
        elif fam == "fp_reversed":  # "parent is a subtype of child"
            for e in g.edges_of(s, "disease_disease"):
                if e.subject.id == e.object.id:
                    continue
                child, parent = e.subject.id, e.object.id
                text = qt.evid_text(rel, g.name(parent), g.name(child))
                prem = premise_of(parent, rel, child, True)
                c = Cand(tpl, (parent, rel, child), g.seed_group[s], (parent, child), text,
                         gold_edges=[e], premise=prem)
                c.need_absent_triples.append((parent, rel, child, True))
                c.route_options = [([qc.Show(e.id, child, parent, rel)], None)]
                yield c


def gen_rel(g: Graph, tpl: qt.Template, cap: int) -> Iterable[Cand]:
    ptype = tpl.relation_key
    rels = qt.ADMISSIBLE[ptype]
    for s in sorted(g.seed_group):
        if tpl.answerable == "yes":
            partners = sorted({other(e, s) for r in rels for e in g.edges_of(s, r)
                               if g.entity(other(e, s)).type == ptype and other(e, s) != s})
            for p in partners:
                edges = [e for e in g.between(s, p) if e.relation in rels]
                text = qt.rel_text(g.name(s), g.name(p))
                reqs: list = [qc.Show(e.id, e.subject.id, e.object.id, e.relation) for e in edges]
                have = {e.relation for e in edges}
                reqs += [qc.RuleOut(s, r, p) for r in rels if r not in have]
                c = Cand(tpl, (s, p), g.seed_group[s], (s, p), text, gold_edges=edges)
                c.route_options = [(reqs, None)]
                yield c
        else:
            pool: dict[str, None] = {}
            for r in rels:
                for p, _ in near_miss_partners(g, s, r, ptype):
                    pool[p] = None
            for p in sorted(pool)[:cap]:
                text = qt.rel_text(g.name(s), g.name(p))
                c = Cand(tpl, (s, p), g.seed_group[s], (s, p), text)
                c.need_absent_pairs.append((s, p))
                c.route_options = [([qc.RuleOut(s, r, p) for r in rels], None)]
                yield c


def gen_evid2(g: Graph, tpl: qt.Template, cap: int) -> Iterable[Cand]:
    rel = tpl.relation_key
    for s in sorted(g.seed_group):
        edges = sorted((e for e in g.edges_of(s, rel) if e.subject.id != e.object.id),
                       key=lambda e: qc.split_hash(e.id, "pairing"))
        if tpl.answerable == "yes":
            for e1, e2 in zip(edges[0::2], edges[1::2]):
                pairs = [(g.name(e.subject.id), g.name(e.object.id)) for e in (e1, e2)]
                text = qt.evid2_text(rel, pairs)
                c = Cand(tpl, (e1.id, e2.id), g.seed_group[s],
                         tuple(dict.fromkeys([e1.subject.id, e1.object.id, e2.subject.id, e2.object.id])),
                         text, gold_edges=[e1, e2])
                c.route_options = [([qc.Show(e.id, e.subject.id, e.object.id, rel) for e in (e1, e2)], None)]
                yield c
        else:
            absent = sorted(near_miss_partners(g, s, rel, PARTNER_TYPE[rel]),
                            key=lambda pr: qc.split_hash(pr[0], "pairing"))[:cap]
            for e, (p, prole) in zip(edges, absent):
                subj, obj = order_for(rel, s, p, prole)
                present = (g.name(e.subject.id), g.name(e.object.id))
                missing = (g.name(subj), g.name(obj))
                first_missing = qc.split_hash(e.id + p, "position") < 0.5
                pairs = [missing, present] if first_missing else [present, missing]
                text = qt.evid2_text(rel, pairs)
                gap = GraphGap(
                    description="no edge states that " + qt.evid_statement(rel, *missing),
                    triple=premise_of(subj, rel, obj, rel == "disease_disease"),
                )
                c = Cand(tpl, (e.id, subj, obj), g.seed_group[s],
                         tuple(dict.fromkeys([e.subject.id, e.object.id, subj, obj])), text,
                         gold_edges=[e], gaps=[gap])
                c.need_absent_pairs.append((subj, obj))
                c.route_options = [(
                    [qc.Show(e.id, e.subject.id, e.object.id, rel), qc.RuleOut(subj, rel, obj)], None
                )]
                yield c


def bridge_M(g: Graph, b: qt.Bridge, x: str, y: str) -> dict[str, tuple[EdgeEvidence, EdgeEvidence]]:
    """Intermediates m with x -r_x- m -r_y- y in the store, respecting types and roles."""
    out = {}
    for e1 in g.edges_of(x, b.r_x):
        m = other(e1, x)
        if m in (x, y) or g.entity(m).type != b.m_type:
            continue
        if b.m_role_x and role(e1, m) != b.m_role_x:
            continue
        for e2 in g.edges_of(m, b.r_y):
            if other(e2, m) != y:
                continue
            if b.m_role_y and role(e2, m) != b.m_role_y:
                continue
            out[m] = (e1, e2)
            break
    return out


def bridge_pairs(g: Graph, b: qt.Bridge, seeds: Iterable[str]) -> set[tuple[str, str]]:
    """(x, y) pairs with at least one store chain, with the seed constraint."""
    pairs = set()
    for s in seeds:
        if b.seed_end in ("x", "both"):
            for e1 in g.edges_of(s, b.r_x):
                m = other(e1, s)
                for e2 in g.edges_of(m, b.r_y):
                    y = other(e2, m)
                    if y != s and g.entity(y).type == b.y_type:
                        pairs.add((s, y))
        if b.seed_end in ("y", "both"):
            for e2 in g.edges_of(s, b.r_y):
                m = other(e2, s)
                for e1 in g.edges_of(m, b.r_x):
                    x = other(e1, m)
                    if x != s and g.entity(x).type == b.x_type:
                        pairs.add((x, s))
    return pairs


def bridge_routes(g: Graph, prober: qc.Prober, b: qt.Bridge, x: str, y: str,
                  M: dict[str, tuple[EdgeEvidence, EdgeEvidence]]) -> list[tuple[list, set[str] | None]]:
    """Route options for a bridge question (see the design doc, "Reachability")."""
    gold_show = [qc.Show(e.id, e.subject.id, e.object.id, e.relation) for es in M.values() for e in es]
    opts: list[tuple[list, set[str] | None]] = [
        ([qc.Complete(x, b.r_x), qc.Complete(y, b.r_y), *gold_show], {x, y})
    ]
    for end, rel_end, rel_far, far, mrole_end in (
        (x, b.r_x, b.r_y, y, b.m_role_x), (y, b.r_y, b.r_x, x, b.m_role_y)
    ):
        page = prober.page(end, rel_end)
        if page.truncated:
            continue
        ms = []
        for e in page.edges:
            m = other(e, end)
            if m in (x, y) or g.entity(m).type != b.m_type:
                continue
            if mrole_end and role(e, m) != mrole_end:
                continue
            ms.append(m)
        reqs: list = [qc.Complete(end, rel_end), *gold_show]
        for m in dict.fromkeys(ms):
            if m not in M:
                reqs.append(qc.RuleOut(m, rel_far, far))
        opts.append((reqs, {x, y, *ms}))
    return opts


def gen_bridge(g: Graph, prober: qc.Prober, tpl: qt.Template, cap: int) -> Iterable[Cand]:
    b = next(br for br in qt.BRIDGES if br.key == tpl.relation_key)
    seeds = sorted(g.seed_group)
    if tpl.answerable == "yes":
        for x, y in sorted(bridge_pairs(g, b, seeds)):
            M = bridge_M(g, b, x, y)
            if not 1 <= len(M) <= K_BRIDGE:
                continue
            anchor = x if b.seed_end == "x" else y
            text = qt.bridge_text(b, g.name(x), g.name(y))
            edges = [e for m in sorted(M) for e in M[m]]
            c = Cand(tpl, (x, y), g.seed_group.get(anchor, ""), (x, y), text, gold_edges=edges,
                     answer_ids=tuple(sorted(M)))
            c.route_options = bridge_routes(g, prober, b, x, y, M)
            yield c
    else:
        # near misses: pairs with a chain to a sibling of the seed end, none to the seed
        for s in seeds:
            pool: set[tuple[str, str]] = set()
            for sib in g.siblings(s):
                for x, y in bridge_pairs(g, b, [sib]):
                    if b.seed_end == "y" and y == sib and x != s:
                        pool.add((x, s))
                    elif b.seed_end == "x" and x == sib and y != s:
                        pool.add((s, y))
            kept = 0
            for x, y in sorted(pool, key=lambda p: qc.split_hash("|".join(p), "pool")):
                if kept >= cap:
                    break
                if bridge_M(g, b, x, y):
                    continue
                if not g.edges_of(x, b.r_x) or not g.edges_of(y, b.r_y):
                    continue
                kept += 1
                text = qt.bridge_text(b, g.name(x), g.name(y))
                c = Cand(tpl, (x, y), g.seed_group[s], (x, y), text)
                c.need_no_chain.append((x, y, b.r_x, b.r_y))
                c.route_options = bridge_routes(g, prober, b, x, y, {})
                yield c


# --------------------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------------------

STAGES = ("generated", "no_fallback", "names", "no_leak", "full_graph", "flags", "route", "item_valid", "unique_text")


def run_checks(g: Graph, prober: qc.Prober, fg: qc.FullGraph, cands: list[Cand],
               directed_by_rel: dict[str, bool], build_id: str) -> tuple[list[Cand], dict, dict, Counter]:
    """Filter candidates through every check; return survivors, per-template funnel,
    per-template name-failure reasons, and route call counts."""
    funnel: dict[str, Counter] = defaultdict(Counter)
    name_fail: dict[str, Counter] = defaultdict(Counter)
    route_calls: Counter = Counter()
    for c in cands:
        funnel[c.tpl.id]["generated"] += 1

    # full-graph conditions, batched
    pairs = {p for c in cands for p in c.need_absent_pairs}
    absent_pairs = fg.absent_pairs(pairs)
    trip = {t for c in cands for t in c.need_absent_triples}
    present_trip = fg.present_triples(trip)
    zero: dict[str, set[str]] = defaultdict(set)
    for c in cands:
        for ent, rel in c.need_zero_degree:
            zero[rel].add(ent)
    degree = {rel: fg.relation_degree(ents, rel) for rel, ents in zero.items()}
    chains: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for c in cands:
        for x, y, rx, ry in c.need_no_chain:
            chains[(rx, ry)].add((x, y))
    chain_n = {k: fg.shared_neighbors(v, *k) for k, v in chains.items()}

    name_cache: dict[str, qc.NameCheck] = {}
    out: list[Cand] = []
    seen_text: set[str] = set()
    for c in cands:
        f = funnel[c.tpl.id]
        if qc.has_fallback(c.entity_ids):
            continue
        f["no_fallback"] += 1
        bad = None
        for n in c.named:
            if n not in name_cache:
                name_cache[n] = qc.name_check(prober.search, g.entity(n))
            if not name_cache[n].ok:
                bad = name_cache[n].reason
                break
        if bad:
            name_fail[c.tpl.id][bad] += 1
            continue
        f["names"] += 1
        if qc.answer_leaks(c.text, [g.name(a) for a in c.answer_ids]):
            continue
        f["no_leak"] += 1
        ok = all(p in absent_pairs for p in c.need_absent_pairs)
        ok = ok and not any(t in present_trip for t in c.need_absent_triples)
        ok = ok and all(degree[rel][e] == 0 for e, rel in c.need_zero_degree)
        ok = ok and all(chain_n[(rx, ry)][(x, y)] == 0 for x, y, rx, ry in c.need_no_chain)
        if not ok:
            continue
        f["full_graph"] += 1
        gps = gp_from(c.gold_edges)
        if any(qc.gold_point_problems(gp, g.store) for gp in gps):
            continue
        trips = ([c.premise] if c.premise else []) + [x.triple for x in c.gaps if x.triple]
        if any(qc.triple_flag_problems(t, directed_by_rel) for t in trips):
            continue
        f["flags"] += 1
        best = None
        for reqs, allowed_set in c.route_options:
            try:
                r = qc.plan_route(prober, c.named, reqs,
                                  allowed=None if allowed_set is None else allowed_set.__contains__)
            except ValueError:
                r = None
            if r is not None and (best is None or r.calls < best.calls):
                best = r
        if best is None:
            continue
        f["route"] += 1
        route_calls[best.calls] += 1
        iid = c.iid = qc.item_id(build_id, c.tpl.id, c.params)
        notes = json.dumps({
            "build_id": build_id, "generator": GENERATOR_VERSION, "template_params": list(c.params),
            "route": best.as_notes(),
        })
        try:
            QuestionItem(
                id=iid, prompt=c.text, qtype=c.tpl.qtype, answerable=c.tpl.answerable,
                evidence_scope="graph", gold_points=gps,
                gold_evidence=sorted({e for gp in gps for e in gp.evidence}),
                premise=c.premise, graph_gaps=c.gaps, source="template", build_id=build_id,
                split=qc.split_of(iid, SPLIT_SEED, HELDOUT_SHARE), template_id=c.tpl.id, notes=notes,
            )
        except ValidationError:
            # Never print the error: pydantic echoes input values (item text).
            continue
        f["item_valid"] += 1
        if c.text in seen_text:
            continue
        seen_text.add(c.text)
        f["unique_text"] += 1
        out.append(c)
    return out, funnel, name_fail, route_calls


# --------------------------------------------------------------------------------------
# Targets and selection
# --------------------------------------------------------------------------------------


def apportion(total: int, shares: dict[str, float]) -> dict[str, int]:
    """Largest-remainder rounding of ``total`` by ``shares`` (deterministic)."""
    raw = {k: total * v / sum(shares.values()) for k, v in shares.items()}
    out = {k: int(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: (-(raw[k] - out[k]), k))[: total - sum(out.values())]:
        out[k] += 1
    return out


def targets() -> dict[str, dict[str, int]]:
    return {split: apportion(n, TYPE_SHARE) for split, n in TOTAL.items()}


def subtype(c: Cand) -> str:
    """The type key used for quotas: false premises split into no/correctable."""
    if c.tpl.qtype == "false_premise":
        return f"false_premise:{c.tpl.answerable}"
    return c.tpl.qtype


def select(cands: list[Cand], split: str, used_facts: set, used_anchors: set) -> dict:
    """Simulate the generator's seeded selection for one split.

    Candidates in hash order; accept one if its type (and, in pass 1, its
    type-by-group cell) still has room, its relation key is under the 40% cap, it
    shares no fact and no named-entity set with any selected item (either split), and
    its text isn't a near-duplicate of a selected item's. Pass 2 runs the same order
    again without the group cells, so a cell its group can't fill spills to others.

    Returns counts only: items selected per type and per (type, group), the targets,
    and why candidates of each type were passed over.
    """
    tgt = targets()[split]
    fp = tgt["false_premise"]
    type_tgt = {k: v for k, v in tgt.items() if k != "false_premise"}
    type_tgt["false_premise:correctable"] = round(fp * FP_CORRECTABLE_SHARE)
    type_tgt["false_premise:no"] = fp - type_tgt["false_premise:correctable"]
    cell_tgt = {t: apportion(n, GROUP_SHARE) for t, n in type_tgt.items()}
    rel_cap = {t: int(RELATION_CAP * (tgt["false_premise"] if t.startswith("false_premise") else n))
               for t, n in type_tgt.items()}
    have_type: Counter = Counter()
    have_cell: Counter = Counter()
    have_rel: Counter = Counter()
    passed_over: dict[str, Counter] = defaultdict(Counter)
    toks: list[frozenset] = []
    order = sorted(cands, key=lambda c: qc.split_hash(c.iid, SPLIT_SEED + "/order"))
    # Scarcest cells pick first: a (type, group) cell with few candidates per slot
    # goes ahead of plentiful ones, so a shared cap (relation, facts) doesn't use
    # up what only it could fill. Ties and order within a cell follow the hash.
    pool: Counter = Counter((subtype(c), c.group) for c in cands)
    def scarcity(c: Cand) -> float:
        n = cell_tgt[subtype(c)].get(c.group, 0)
        return pool[(subtype(c), c.group)] / n if n else float("inf")
    order = sorted(order, key=lambda c: (scarcity(c), qc.split_hash(c.iid, SPLIT_SEED + "/order")))
    taken: set[str] = set()
    pass1: dict = {}
    for group_quota, c in [(True, c) for c in order] + [(False, c) for c in order]:
        if not group_quota and not pass1:
            pass1 = {"selected": dict(have_type), "passed_over": {t: dict(v) for t, v in passed_over.items()}}
            passed_over = defaultdict(Counter)
        if c.iid in taken:
            continue
        t = subtype(c)
        why = None
        rk = (c.tpl.qtype, c.tpl.relation_key)
        facts, anchors = c.facts(), frozenset(c.named)
        if have_type[t] >= type_tgt[t]:
            continue  # type full: not a shortfall reason
        if group_quota and have_cell[(t, c.group)] >= cell_tgt[t].get(c.group, 0):
            why = "group_cell_full"
        elif have_rel[rk] >= rel_cap[t]:
            why = "relation_cap"
        elif facts & used_facts:
            why = "fact_used"
        elif anchors in used_anchors:
            why = "anchors_used"
        else:
            tk = qc.tokens(c.text)
            if any(qc.jaccard(tk, o) >= TEXT_DUP_JACCARD for o in toks):
                why = "text_near_duplicate"
        if why:
            passed_over[f"{t}|{c.group}"][why] += 1
            continue
        toks.append(tk)
        taken.add(c.iid)
        used_facts |= facts
        used_anchors.add(anchors)
        have_type[t] += 1
        have_cell[(t, c.group)] += 1
        have_rel[rk] += 1
    return {
        "target": type_tgt,
        "selected": {t: have_type[t] for t in type_tgt},
        "cell_target": {f"{t}|{g}": n for t in type_tgt for g, n in cell_tgt[t].items()},
        "cell_selected": {f"{t}|{g}": n for (t, g), n in sorted(have_cell.items())},
        "pass1": pass1,
        "pass2_passed_over": {t: dict(v) for t, v in sorted(passed_over.items())},
    }


def markdown(r: dict) -> str:
    """The design doc's feasibility tables, from the aggregate result. Counts only."""
    out: list[str] = []

    def table(head: list[str], rows: list[list]) -> None:
        out.append("| " + " | ".join(head) + " |")
        out.append("|" + "---|" * len(head))
        out.extend("| " + " | ".join(str(x) for x in row) + " |" for row in rows)
        out.append("")

    out.append("**Funnel per template** (candidates left after each check, in order)\n")
    table(["Template", *STAGES[:-1], "valid"],
          [[f"`{t}`", *(f[s] for s in STAGES)] for t, f in r["funnel"].items()])

    out.append("**Valid candidates per split and type, against targets**\n")
    types = ["single_hop", "multi_hop", "partial", "unanswerable", "false_premise:no", "false_premise:correctable"]
    rows = []
    for t in types:
        row = [t]
        for split in ("train", "heldout"):
            n = r["valid_by_split_type"].get(split, {}).get(t, 0)
            tgt = r["selection"][split]["target"][t]
            row += [n, tgt, f"{n / tgt:.0f}x" if tgt else "n/a"]
        rows.append(row)
    table(["Type", "train valid", "train target", "multiple", "held-out valid", "held-out target", "multiple"], rows)

    out.append("**Valid candidates per type and relation key, both splits**\n")
    rel: Counter = Counter()
    for split in ("train", "heldout"):
        for k, v in r["valid_by_split_type_relation"].get(split, {}).items():
            rel[k] += v
    table(["Type", "Relation key", "Valid"], [[*k.split("|", 1), v] for k, v in sorted(rel.items())])

    out.append("**Valid candidates per type and disease group, both splits**\n")
    grp: Counter = Counter()
    for split in ("train", "heldout"):
        for k, v in r["valid_by_split_type_group"].get(split, {}).items():
            grp[k] += v
    groups = list(GROUP_SHARE)
    table(["Type", *groups], [[t, *(grp.get(f"{t}|{g}", 0) for g in groups)] for t in types])

    out.append("**Simulated selection** (pass 1 with disease-group cells; pass 2 spills)\n")
    rows = []
    for split in ("heldout", "train"):
        sel = r["selection"][split]
        for t in types:
            short = [
                f"{k.split('|')[1]} {sel['cell_selected'].get(k, 0)}/{n}"
                for k, n in sel["cell_target"].items()
                if k.startswith(t + "|") and sel["cell_selected"].get(k, 0) < n
            ]
            rows.append([split, t, sel["target"][t], sel["pass1"]["selected"].get(t, 0),
                         sel["selected"][t], ", ".join(short) or "none"])
    table(["Split", "Type", "Target", "After pass 1", "Selected", "Cells short (selected/target)"], rows)

    rows = []
    for split in ("train", "heldout"):
        sel = r["selection"][split]
        pool = r["valid_by_split_type_group"].get(split, {})
        for k, n in sel["cell_target"].items():
            if n and pool.get(k, 0) < HEADROOM * n:
                rows.append([split, *k.split("|"), n, pool.get(k, 0), f"{pool.get(k, 0) / n:.1f}x"])
    out.append(f"**Cells under {HEADROOM}x headroom** (valid candidates per item, by split, type and group)\n")
    if rows:
        table(["Split", "Type", "Group", "Target", "Valid", "Multiple"], rows)
    else:
        out.append("None.\n")

    out.append("**Route length** (tool calls in the cheapest route, valid candidates)\n")
    table(["Calls", "Candidates"], [[k, v] for k, v in r["route_calls"].items()])

    nf: Counter = Counter()
    for v in r["name_failures"].values():
        nf.update(v)
    out.append("**Name-rule failures** (candidates dropped, by reason): "
               + ", ".join(f"{k} {v}" for k, v in sorted(nf.items())) + "\n")
    return "\n".join(out)


def feasibility(store, edge_ids: list[str], seeds: list[dict], fg: qc.FullGraph, build_id: str,
                cap: int = 300, tool_config: KGToolsetConfig | None = None) -> dict:
    """Generate, check, split and select; return aggregate counts only.

    Args:
        store: the evidence store.
        edge_ids: every edge ID in the store.
        seeds: the store's seed records (``id`` and ``group``), from ``build.json``.
        fg: full-graph absence checks.
        build_id: the store's build ID.
        cap: most near-miss candidates per (template, seed) for absence templates.
        tool_config: the tool config routes are planned under (default: the run's).
    """
    t0 = time.perf_counter()
    g = Graph(store, edge_ids, seeds)
    ts = KGToolset(tool_config or KGToolsetConfig(), store=store)
    prober = qc.Prober(ts)
    directed_by_rel = qc.relation_directedness(g.edges)

    cands: list[Cand] = []
    for tpl in qt.catalog():
        if tpl.shape == "list":
            cands += gen_list(g, tpl)
        elif tpl.shape == "list2":
            cands += gen_list2(g, tpl)
        elif tpl.shape == "evid":
            cands += gen_evid(g, tpl, cap)
        elif tpl.shape == "evid2":
            cands += gen_evid2(g, tpl, cap)
        elif tpl.shape == "rel":
            cands += gen_rel(g, tpl, cap)
        elif tpl.shape == "bridge":
            cands += gen_bridge(g, prober, tpl, cap)
    print(f"[{time.perf_counter() - t0:.0f}s] {len(cands)} candidates generated", file=sys.stderr)

    valid, funnel, name_fail, route_calls = run_checks(g, prober, fg, cands, directed_by_rel, build_id)
    print(f"[{time.perf_counter() - t0:.0f}s] {len(valid)} pass every check", file=sys.stderr)

    by_split: dict[str, list[Cand]] = defaultdict(list)
    for c in valid:
        by_split[qc.split_of(c.iid, SPLIT_SEED, HELDOUT_SHARE)].append(c)

    # Held-out first: its selection never depends on train.
    used_f: set = set()
    used_a: set = set()
    sim = {split: select(by_split[split], split, used_f, used_a) for split in ("heldout", "train")}

    def agg(cs: Iterable[Cand], key) -> dict:
        out: Counter = Counter()
        for c in cs:
            out[key(c)] += 1
        return {"|".join(k) if isinstance(k, tuple) else k: v for k, v in sorted(out.items())}

    return {
        "build_id": build_id,
        "generator": GENERATOR_VERSION,
        "split_seed": SPLIT_SEED,
        "heldout_share": HELDOUT_SHARE,
        "params": {"K_LIST": K_LIST, "K_LIST2": K_LIST2, "K_BRIDGE": K_BRIDGE, "cap": cap,
                   "relation_cap": RELATION_CAP, "headroom": HEADROOM, "group_share": GROUP_SHARE,
                   "fp_correctable_share": FP_CORRECTABLE_SHARE, "text_dup_jaccard": TEXT_DUP_JACCARD},
        "tool_config": {"max_calls": ts.config.max_calls, "max_results": ts.config.max_results,
                        "paginate": ts.config.paginate},
        "targets": targets(),
        "funnel": {t: {s: funnel[t][s] for s in STAGES} for t in sorted(funnel)},
        "name_failures": {t: dict(v) for t, v in sorted(name_fail.items())},
        "route_calls": dict(sorted(route_calls.items())),
        "valid_by_split_type": {s: agg(cs, subtype) for s, cs in sorted(by_split.items())},
        "valid_by_split_type_group": {
            s: agg(cs, lambda c: (subtype(c), c.group)) for s, cs in sorted(by_split.items())
        },
        "valid_by_split_type_group_relation": {
            s: agg(cs, lambda c: (subtype(c), c.group, c.tpl.relation_key)) for s, cs in sorted(by_split.items())
        },
        "valid_by_split_type_relation": {
            s: agg(cs, lambda c: (subtype(c), c.tpl.relation_key)) for s, cs in sorted(by_split.items())
        },
        "selection": sim,
        "seconds": round(time.perf_counter() - t0, 1),
    }


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("store", type=Path)
    ap.add_argument("duckdb", type=Path)
    ap.add_argument("--cap", type=int, default=300,
                    help="most near-miss candidates per (template, seed) for absence templates")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--from-json", type=Path, default=None,
                    help="print the markdown tables from an earlier result and exit")
    args = ap.parse_args()
    if args.from_json:
        print(markdown(json.loads(args.from_json.read_text(encoding="utf-8"))))
        return

    meta = sf.read_build_meta(args.store)
    store = load_store(args.store)
    edge_ids = [r["id"] for r in sf.read_jsonl(args.store / sf.EDGES)]
    index_of = {r["id"]: r["primekg_index"] for r in sf.read_jsonl(args.store / sf.ENTITY_SOURCES)}
    fg = qc.FullGraph(duckdb.connect(str(args.duckdb), read_only=True), index_of, HIERARCHY_CHILD_END)
    result = feasibility(store, edge_ids, meta["config"]["seeds"], fg, meta["build_id"], args.cap)
    out = args.out or args.duckdb.parent / "qgen_feasibility.json"
    out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out}", file=sys.stderr)
    print(markdown(result))


if __name__ == "__main__":
    main()
