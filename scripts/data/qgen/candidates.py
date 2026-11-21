"""Candidate items: the shape generators and the check pipeline.

For every template in ``qgen_templates.catalog()``, the shape generators build the
candidates the design allows from the evidence store, in memory. :func:`build_pool`
then runs every validity check on every candidate, in this order, and drops a
candidate at the first failure (stage names as in the funnel):

1. ``domain``: the domain filters (:class:`Filters`): no phenotype from HPO's
   mode-of-inheritance or clinical-modifier subtrees anywhere in the item, and no
   reversed-subtype premise on an edge that touches a grouped (``group:``) node;
2. ``no_fallback``: no ``primekg:`` fallback entity;
3. ``names``: every named entity passes the name rule (``checks.resolve_mention``,
   by default the type-qualified rule with every mention typed); the question text
   is rendered here;
4. ``no_leak``: no answer entity's name appears in the question;
5. ``full_graph``: absent pairs and triples really are absent from the **whole**
   PrimeKG graph, empty lists and empty chains are empty there too;
6. ``flags``: ``directed`` flags agree with the store (gold points) and with the
   relation's convention (premise and gap triples);
7. ``route``: a route of real tool calls, within the budget, returns every gold
   edge and makes every absence visible;
8. ``item_valid``: the candidate validates as a ``QuestionItem``;
9. ``gold_complete``: its gold points are exactly the store facts that answer its
   question under the template's answer rule (``qgen.answers``): no true answer
   missing, no non-answer included;
10. ``unique_text``: its question text is not identical to an earlier candidate's.

The generator (``question_set.py generate``) and the feasibility script both call
:func:`build_pool`, so the feasibility counts and the written items rest on one
implementation.

**Nothing here prints item content.** Candidates hold question text, names and IDs
in memory only; everything this module reports is a count. Candidates include what
will become held-out items.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field

import qgen_templates as qt
from neuro_grounded_qa.contracts import (
    EdgeEvidence,
    GoldPoint,
    GraphGap,
    PremiseTriple,
    QuestionItem,
)
from neuro_grounded_qa.tools import KGToolset, KGToolsetConfig
from pydantic import ValidationError

from qgen import answers as qa
from qgen import checks as qc

K_LIST = 5
"""Most answers a list question may have (gold points)."""
K_LIST2 = 6
"""Most gold points over both clauses of a two-list question."""
K_BRIDGE = 3
"""Most intermediates a bridge question may have (gold points = 2 per intermediate)."""
NEAR_MISS_CAP = 300
"""Most near-miss candidates per (template, seed) for absence templates."""

GROUP_PREFIX = "group:"
"""ID prefix of grouped MONDO nodes (``build.GROUP_PREFIX`` + ``:``)."""

HPO_EXCLUDED_ROOTS = ("5", "12823")
"""HPO's "Mode of inheritance" (``HP:0000005``) and "Clinical modifier"
(``HP:0012823``, which holds "Clinical course", onset and severity terms), by
PrimeKG's unpadded HPO source ID."""

STAGES = (
    "generated", "domain", "no_fallback", "names", "no_leak", "full_graph", "flags", "route",
    "item_valid", "gold_complete", "unique_text",
)
"""The funnel's stages, in check order (``unique_text`` survivors are valid)."""


@dataclass(frozen=True)
class Filters:
    """Domain filters and the name rule (Kaitlin's decisions of 2026-11-19).

    - ``hpo_excluded_roots``: HPO source IDs whose whole subtree (in the full graph's
      ``phenotype_phenotype`` hierarchy) is kept out of every item: no such phenotype
      is named, answered, cited or used in a premise or gap.
    - ``reversed_premise_skip_grouped``: ``fp_reversed`` premises only on subtype
      edges whose two ends are plain MONDO terms, not grouped nodes.
    - ``naming``: the name rule and mention form, one of ``checks.NAMING_MODES``
      (default ``typed_all``: every mention names the type; revision 2).
    - ``lowercase_phenotypes``: write phenotype names in lowercase in question text
      (``qgen_templates.display_name``; revision 2).

    :meth:`none` is the design stage (no filters, strict names), which reproduces
    the design-stage feasibility counts. :meth:`revision1` is the first generated
    set's rules (filters on, typed only when a name is shared, names as stored).
    """

    hpo_excluded_roots: tuple[str, ...] = HPO_EXCLUDED_ROOTS
    reversed_premise_skip_grouped: bool = True
    naming: str = "typed_all"
    lowercase_phenotypes: bool = True

    @classmethod
    def none(cls) -> Filters:
        return cls(hpo_excluded_roots=(), reversed_premise_skip_grouped=False, naming="strict",
                   lowercase_phenotypes=False)

    @classmethod
    def revision1(cls) -> Filters:
        return cls(naming="typed_when_shared", lowercase_phenotypes=False)

    def as_json(self) -> dict:
        return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(self).items()}


# --------------------------------------------------------------------------------------
# Candidates
# --------------------------------------------------------------------------------------

MentionFn = Callable[..., str]
"""``m(entity_id, ctx=None) -> str``: how the question names an entity. ``ctx`` is
``(statement relation or bridge key, slot)`` when the slot's wording may already name
the type (``qgen_templates.wording_names_type``)."""

Render = Callable[[MentionFn], str]


@dataclass
class Cand:
    """One candidate item, in memory only. Never printed.

    ``render`` builds the question text from a mention function, so the text can name
    an entity's type when the name rule needs it; ``gap_specs`` do the same for gap
    descriptions. Both run at the ``names`` stage. Fields after ``route_options`` are
    filled in by :func:`build_pool`.
    """

    tpl: qt.Template
    params: tuple[str, ...]
    group: str
    named: tuple[str, ...]
    render: Render
    gold_edges: list[EdgeEvidence] = field(default_factory=list)
    answer_ids: tuple[str, ...] = ()
    premise: PremiseTriple | None = None
    gap_specs: list[tuple[Render, PremiseTriple | None]] = field(default_factory=list)
    route_options: list[tuple[list, set[str] | None]] = field(default_factory=list)
    # full-graph conditions
    need_absent_pairs: list[tuple[str, str]] = field(default_factory=list)
    need_absent_triples: list[tuple[str, str, str, bool]] = field(default_factory=list)
    need_zero_degree: list[tuple[str, str]] = field(default_factory=list)
    need_no_chain: list[tuple[str, str, str, str]] = field(default_factory=list)
    # filled in by build_pool
    text: str = ""
    typed: tuple[str, ...] = ()
    naming: str = ""
    iid: str = ""
    route: qc.Route | None = None
    route_reqs: list = field(default_factory=list)
    route_allowed: set[str] | None = None
    item: QuestionItem | None = None

    @property
    def gap_triples(self) -> list[PremiseTriple]:
        return [t for _, t in self.gap_specs if t is not None]

    @property
    def entity_ids(self) -> set[str]:
        """Every entity the item touches: named, answers, gold edge ends, premise, gaps."""
        ids = set(self.named) | set(self.answer_ids)
        for e in self.gold_edges:
            ids |= {e.subject.id, e.object.id}
        if self.premise:
            ids |= {self.premise.subject_id, self.premise.object_id}
        for t in self.gap_triples:
            ids |= {t.subject_id, t.object_id}
        return ids

    def facts(self) -> frozenset:
        """Fact keys of the gold points, the premise and the gap triples."""
        keys = {qc.fact_key(e.subject.id, e.relation, e.object.id, e.directed) for e in self.gold_edges}
        if self.premise:
            p = self.premise
            keys.add(qc.fact_key(p.subject_id, p.relation, p.object_id, p.directed))
        for t in self.gap_triples:
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
        self.seed_group = {s["id"]: seed_group(s["group"]) for s in seeds}
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


def seed_group(group: str) -> str:
    """The quota group of a seed's group: the FTD/ALS nodes count under FTD."""
    return "FTD" if group == "FTD/ALS" else group


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


# ---- text renderers -------------------------------------------------------------------


def evid_render(rel: str, subj: str, obj: str) -> Render:
    return lambda m: qt.evid_text(rel, m(subj, (rel, "subj")), m(obj, (rel, "obj")))


def evid2_render(rel: str, pairs: list[tuple[str, str]]) -> Render:
    return lambda m: qt.evid2_text(rel, [(m(s, (rel, "subj")), m(o, (rel, "obj"))) for s, o in pairs])


def statement_render(rel: str, subj: str, obj: str, prefix: str) -> Render:
    return lambda m: prefix + qt.evid_statement(rel, m(subj, (rel, "subj")), m(obj, (rel, "obj")))


# ---- shape generators -----------------------------------------------------------------


def gen_list(g: Graph, tpl: qt.Template) -> Iterable[Cand]:
    key = tpl.relation_key
    rel = list_relation(key)
    for s in sorted(g.seed_group):
        edges = list_edges(g, s, key)
        render = lambda m, key=key, s=s: qt.list_text(key, m(s))  # noqa: E731 -- generator code, left as a lambda
        reqs = [qc.Complete(s, rel)]
        if tpl.answerable == "yes":
            if not 1 <= len(edges) <= K_LIST:
                continue
            c = Cand(tpl, (s,), g.seed_group[s], (s,), render, gold_edges=list(edges),
                     answer_ids=tuple(other(e, s) for e in edges))
            c.route_options = [(reqs + [qc.Show(e.id, e.subject.id, e.object.id, rel) for e in edges], None)]
        else:
            if edges:
                continue
            # plausible: the relation (same role) is used by a sibling seed
            if not any(list_edges(g, x, key) for x in g.siblings(s)):
                continue
            c = Cand(tpl, (s,), g.seed_group[s], (s,), render)
            c.need_zero_degree.append((s, rel))
            c.route_options = [(reqs, None)]
        yield c


def gen_list2(g: Graph, tpl: qt.Template) -> Iterable[Cand]:
    keys = list(qt.LIST_CLAUSE)
    for s in sorted(g.seed_group):
        for k1 in keys:
            for k2 in keys:
                if k1 == k2 or (k1.startswith("disease_disease") and k2.startswith("disease_disease")):
                    continue
                e1, e2 = list_edges(g, s, k1), list_edges(g, s, k2)
                r1, r2 = list_relation(k1), list_relation(k2)
                render = lambda m, k1=k1, k2=k2, s=s: qt.list2_text(k1, k2, m(s))  # noqa: E731 -- generator code, left as a lambda
                reqs = [qc.Complete(s, r1), qc.Complete(s, r2)]
                if tpl.answerable == "yes":
                    if not (e1 and e2) or len(e1) > K_LIST or len(e2) > K_LIST or len(e1) + len(e2) > K_LIST2:
                        continue
                    edges = e1 + e2
                    c = Cand(tpl, (s, k1, k2), g.seed_group[s], (s,), render, gold_edges=edges,
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
                    desc = lambda m, ke=ke, s=s: qt.LIST_CLAUSE[ke][0].format(s=m(s)) + ": the graph records none"  # noqa: E731 -- generator code, left as a lambda
                    c = Cand(tpl, (s, k1, k2), g.seed_group[s], (s,), render, gold_edges=list(full),
                             answer_ids=tuple(other(e, s) for e in full), gap_specs=[(desc, None)])
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
                c = Cand(tpl, (e.subject.id, rel, e.object.id), g.seed_group[s],
                         (e.subject.id, e.object.id), evid_render(rel, e.subject.id, e.object.id),
                         gold_edges=[e])
                c.route_options = [([qc.Show(e.id, e.subject.id, e.object.id, rel)], None)]
                yield c
        elif fam == "fp_evid":  # absent premise, answerable "no"
            directed = rel == "disease_disease"
            for p, prole in near_miss_partners(g, s, rel, PARTNER_TYPE[rel])[:cap]:
                subj, obj = order_for(rel, s, p, prole)
                c = Cand(tpl, (subj, rel, obj), g.seed_group[s], (subj, obj), evid_render(rel, subj, obj),
                         premise=premise_of(subj, rel, obj, directed))
                c.need_absent_pairs.append((subj, obj))
                c.route_options = [([qc.RuleOut(subj, rel, obj)], None)]
                yield c
        elif fam == "fp_contra":  # premise rel; store holds the opposite
            opp = qt.OPPOSITE[rel]
            for e in g.edges_of(s, opp):
                prem = premise_of(e.subject.id, rel, e.object.id, False)
                c = Cand(tpl, (e.subject.id, rel, e.object.id), g.seed_group[s],
                         (e.subject.id, e.object.id), evid_render(rel, e.subject.id, e.object.id),
                         gold_edges=[e], premise=prem)
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
                prem = premise_of(parent, rel, child, True)
                c = Cand(tpl, (parent, rel, child), g.seed_group[s], (parent, child),
                         evid_render(rel, parent, child), gold_edges=[e], premise=prem)
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
                render = lambda m, s=s, p=p: qt.rel_text(m(s), m(p))  # noqa: E731 -- generator code, left as a lambda
                reqs: list = [qc.Show(e.id, e.subject.id, e.object.id, e.relation) for e in edges]
                have = {e.relation for e in edges}
                reqs += [qc.RuleOut(s, r, p) for r in rels if r not in have]
                c = Cand(tpl, (s, p), g.seed_group[s], (s, p), render, gold_edges=edges)
                c.route_options = [(reqs, None)]
                yield c
        else:
            pool: dict[str, None] = {}
            for r in rels:
                for p, _ in near_miss_partners(g, s, r, ptype):
                    pool[p] = None
            for p in sorted(pool)[:cap]:
                render = lambda m, s=s, p=p: qt.rel_text(m(s), m(p))  # noqa: E731 -- generator code, left as a lambda
                c = Cand(tpl, (s, p), g.seed_group[s], (s, p), render)
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
                pairs = [(e.subject.id, e.object.id) for e in (e1, e2)]
                c = Cand(tpl, (e1.id, e2.id), g.seed_group[s],
                         tuple(dict.fromkeys([e1.subject.id, e1.object.id, e2.subject.id, e2.object.id])),
                         evid2_render(rel, pairs), gold_edges=[e1, e2])
                c.route_options = [([qc.Show(e.id, e.subject.id, e.object.id, rel) for e in (e1, e2)], None)]
                yield c
        else:
            absent = sorted(near_miss_partners(g, s, rel, PARTNER_TYPE[rel]),
                            key=lambda pr: qc.split_hash(pr[0], "pairing"))[:cap]
            for e, (p, prole) in zip(edges, absent):
                subj, obj = order_for(rel, s, p, prole)
                present = (e.subject.id, e.object.id)
                missing = (subj, obj)
                first_missing = qc.split_hash(e.id + p, "position") < 0.5
                pairs = [missing, present] if first_missing else [present, missing]
                gap = (statement_render(rel, subj, obj, "no edge states that "),
                       premise_of(subj, rel, obj, rel == "disease_disease"))
                c = Cand(tpl, (e.id, subj, obj), g.seed_group[s],
                         tuple(dict.fromkeys([e.subject.id, e.object.id, subj, obj])),
                         evid2_render(rel, pairs), gold_edges=[e], gap_specs=[gap])
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
    """Route options for a bridge question (see the design doc, "Validity checks").

    Either both ends' filtered pages are complete, or one end's page is complete and
    every intermediate on it is checked toward the other end. Probes may start only
    from entities a model could know: the two named ends, or intermediates shown on
    the complete page."""
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
    render_for = lambda x, y: (lambda m: qt.bridge_text(b, m(x, (b.key, "x")), m(y, (b.key, "y"))))  # noqa: E731 -- generator code, left as a lambda
    if tpl.answerable == "yes":
        for x, y in sorted(bridge_pairs(g, b, seeds)):
            M = bridge_M(g, b, x, y)
            if not 1 <= len(M) <= K_BRIDGE:
                continue
            anchor = x if b.seed_end == "x" else y
            edges = [e for m in sorted(M) for e in M[m]]
            c = Cand(tpl, (x, y), g.seed_group.get(anchor, ""), (x, y), render_for(x, y), gold_edges=edges,
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
                c = Cand(tpl, (x, y), g.seed_group[s], (x, y), render_for(x, y))
                c.need_no_chain.append((x, y, b.r_x, b.r_y))
                c.route_options = bridge_routes(g, prober, b, x, y, {})
                yield c


def generate(g: Graph, prober: qc.Prober, cap: int = NEAR_MISS_CAP) -> list[Cand]:
    """Every candidate of every template, in catalog order (deterministic)."""
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
    return cands


# --------------------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------------------


def domain_problem(c: Cand, excluded: set[str], filters: Filters) -> str | None:
    """The domain filter a candidate fails, or None."""
    if excluded and c.entity_ids & excluded:
        return "hpo_excluded_subtree"
    p = c.premise
    if filters.reversed_premise_skip_grouped and c.tpl.id.startswith("fp_reversed.") and p is not None \
            and (p.subject_id.startswith(GROUP_PREFIX) or p.object_id.startswith(GROUP_PREFIX)):
        return "reversed_premise_grouped_node"
    return None


def item_notes(c: Cand, generator_version: str) -> str:
    """The item's ``notes``: a JSON string with the template parameters, the disease
    group, the named and typed entities, the route, the requirements the route meets
    and the full-graph conditions checked. IDs and relation labels only; no text.

    The item check (``question_set.py check``) re-runs the route and the full-graph
    conditions from these notes."""
    assert c.route is not None
    return json.dumps({
        "generator": generator_version,
        "template_params": list(c.params),
        "group": c.group,
        "relation_key": c.tpl.relation_key,
        "named": list(c.named),
        "naming": c.naming,
        "typed_mentions": list(c.typed),
        "route": c.route.as_notes(),
        "route_allowed": None if c.route_allowed is None else sorted(c.route_allowed),
        "requirements": [qc.requirement_to_json(r) for r in c.route_reqs],
        "full_graph": {
            "absent_pairs": [list(p) for p in c.need_absent_pairs],
            "absent_triples": [list(t) for t in c.need_absent_triples],
            "zero_degree": [list(z) for z in c.need_zero_degree],
            "no_chain": [list(n) for n in c.need_no_chain],
        },
    }, ensure_ascii=False)


@dataclass
class Pool:
    """The checked candidate pool, with aggregate counts only.

    ``valid`` holds candidates that passed every stage, each with its built
    ``item``. Everything else is a count: per-template funnel, name-rule, domain and
    gold-completeness failures by reason, route lengths, and (``typed_by_answerable``) how many valid
    candidates of each answerability class use at least one typed mention, so the
    neutrality of typed mentions can be checked in aggregate.
    """

    valid: list[Cand]
    funnel: dict[str, Counter]
    name_fail: dict[str, Counter]
    domain_fail: dict[str, Counter]
    gold_fail: dict[str, Counter]
    route_calls: Counter
    typed_by_answerable: Counter
    valid_by_answerable: Counter
    excluded_entities: int


def build_pool(store, edge_ids: list[str], seeds: list[dict], fg: qc.FullGraph, build_id: str, *,
               split_seed: str, heldout_share: float, generator_version: str,
               filters: Filters | None = None, cap: int = NEAR_MISS_CAP,
               tool_config: KGToolsetConfig | None = None) -> tuple[Pool, Graph, qc.Prober]:
    """Generate every candidate and run every check; return the valid pool.

    Args:
        store: the evidence store (``tools.load_store``).
        edge_ids: every edge ID in the store.
        seeds: the store's seed records (``id`` and ``group``), from ``build.json``.
        fg: full-graph absence checks.
        build_id: the store's build ID, written into every item.
        split_seed, heldout_share: the split rule (``checks.split_of``).
        generator_version: recorded in each item's notes.
        filters: the domain filters and the name rule.
        cap: most near-miss candidates per (template, seed) for absence templates.
        tool_config: the tool config routes are planned under (default: the run's).
    """
    filters = filters or Filters()
    g = Graph(store, edge_ids, seeds)
    ts = KGToolset(tool_config or KGToolsetConfig(), store=store)
    prober = qc.Prober(ts)
    directed_by_rel = qc.relation_directedness(g.edges)
    adj = qa.Adjacency(g.edges)
    excluded: set[str] = set()
    if filters.hpo_excluded_roots:
        excluded = fg.subtree_entities("phenotype_phenotype", "HPO", filters.hpo_excluded_roots)

    cands = generate(g, prober, cap)
    funnel: dict[str, Counter] = defaultdict(Counter)
    name_fail: dict[str, Counter] = defaultdict(Counter)
    domain_fail: dict[str, Counter] = defaultdict(Counter)
    gold_fail: dict[str, Counter] = defaultdict(Counter)
    route_calls: Counter = Counter()
    typed_by_answerable: Counter = Counter()
    valid_by_answerable: Counter = Counter()
    for c in cands:
        funnel[c.tpl.id]["generated"] += 1

    # full-graph conditions, batched
    absent_pairs = fg.absent_pairs({p for c in cands for p in c.need_absent_pairs})
    present_trip = fg.present_triples({t for c in cands for t in c.need_absent_triples})
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

    mentions: dict[str, qc.Mention] = {}
    out: list[Cand] = []
    seen_text: set[str] = set()
    for c in cands:
        f = funnel[c.tpl.id]
        if why := domain_problem(c, excluded, filters):
            domain_fail[c.tpl.id][why] += 1
            continue
        f["domain"] += 1
        if qc.has_fallback(c.entity_ids):
            continue
        f["no_fallback"] += 1
        bad = None
        for n in c.named:
            if n not in mentions:
                mentions[n] = qc.resolve_mention(prober.search, g.entity(n), filters.naming)
            if not mentions[n].ok:
                bad = mentions[n].reason
                break
        if bad:
            name_fail[c.tpl.id][bad] += 1
            continue
        f["names"] += 1
        c.typed = tuple(n for n in c.named if mentions[n].typed)
        c.naming = filters.naming
        typed = set(c.typed)
        lower = filters.lowercase_phenotypes

        def m(eid: str, ctx: tuple[str, str] | None = None, typed=typed, lower=lower) -> str:
            # Typed entities get "the <type> " unless the slot's wording names the
            # type already. Gap descriptions name only named entities, the same way.
            ent = g.entity(eid)
            want = eid in typed and not (ctx and qt.wording_names_type(ctx[0], ctx[1], ent.type))
            return qt.mention(ent.name, ent.type, want, lower)

        c.text = c.render(m)
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
        trips = ([c.premise] if c.premise else []) + c.gap_triples
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
            if r is not None and (best is None or r.calls < best[0].calls):
                best = (r, reqs, allowed_set)
        if best is None:
            continue
        f["route"] += 1
        c.route, c.route_reqs, c.route_allowed = best
        route_calls[c.route.calls] += 1
        c.iid = qc.item_id(build_id, c.tpl.id, c.params)
        try:
            c.item = QuestionItem(
                id=c.iid, prompt=c.text, qtype=c.tpl.qtype, answerable=c.tpl.answerable,
                evidence_scope="graph", gold_points=gps,
                gold_evidence=sorted({e for gp in gps for e in gp.evidence}),
                premise=c.premise,
                graph_gaps=[GraphGap(description=desc(m), triple=t) for desc, t in c.gap_specs],
                source="template", build_id=build_id,
                split=qc.split_of(c.iid, split_seed, heldout_share), template_id=c.tpl.id,
                notes=item_notes(c, generator_version),
            )
        except ValidationError:
            # Never print the error: pydantic echoes input values (item text).
            continue
        f["item_valid"] += 1
        complete = qa.gold_completeness(c.item, list(c.params), adj)
        if not complete.ok:
            gold_fail[c.tpl.id][complete.reason] += 1
            continue
        f["gold_complete"] += 1
        if c.text in seen_text:
            continue
        seen_text.add(c.text)
        f["unique_text"] += 1
        valid_by_answerable[c.tpl.answerable] += 1
        if c.typed:
            typed_by_answerable[c.tpl.answerable] += 1
        out.append(c)
    pool = Pool(out, funnel, name_fail, domain_fail, gold_fail, route_calls, typed_by_answerable,
                valid_by_answerable, len(excluded))
    return pool, g, prober
