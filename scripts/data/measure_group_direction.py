"""Measure the direction of hierarchy edges that touch PrimeKG grouped diseases.

**Why this exists.** The store flags hierarchy edges ``directed`` and stores them
subject = child, object = parent (``neuro_grounded_qa.data.build.orient``). For plain
MONDO and HPO terms that is checked against OptimusKG and holds. A PrimeKG grouped
disease (``group:<slug>``) merges several MONDO terms, and an edge between a group and
another disease comes from one member pair, oriented by whichever member's row comes
first in ``kg.csv``. Read at the level of the entities the model sees, such an edge
can run broader -> narrower, or form a cycle with other edges. This script sizes
that, and works out what each proposed fix would change. It changes nothing.

**What it reads.** The store directory (``entities.jsonl``, ``entity_sources.jsonl``
for group members, ``edges.jsonl``); OptimusKG's ``DIS-DIS/PARENT`` and
``PHE-PHE/PARENT`` edges (parent -> child) from ``derived/optimuskg.duckdb``; and,
for gold evidence IDs, the train items through the train-only loader
(``qgen.view.load_train``, which refuses any file holding a non-train record). It
never opens the held-out file and prints IDs, template IDs and store entity names,
never item text.

**The classification rule** (:func:`classify`), for one directed edge with subject
member set S and object member set O (a plain term is its own one-member set),
against OptimusKG's ancestor closure:

- ``agrees``: the object contains the subject (every member of S has a member of O
  as ancestor-or-self) and not the reverse. The stored order is right at group level.
- ``reversed``: the subject contains the object, and not the reverse.
- ``equivalent``: each contains the other (no such edge is expected).
- ``mixed``: no containment either way, but some member pair runs each way.
- ``partial_agree``: no containment, some member pair runs the stored way, none the
  other way. Includes groups with a member OptimusKG doesn't know, since containment
  then can't be confirmed (counted separately as ``agrees_if_unknown_members_ignored``).
- ``partial_reversed``: the mirror of ``partial_agree``.
- ``no_ancestry``: both ends have a member OptimusKG knows, and no member pair is
  related either way.
- ``unknown``: an end has no member OptimusKG knows.

Cycles (:func:`simple_cycles`) are found over every directed edge in the store.

**Options** (:data:`OPTIONS`, :func:`action`) are applied per edge, and each one's
effect on edges, ``E:`` numbering, cycles and train items is computed.

Run from the repo root with the env loaded::

    uv run python scripts/data/measure_group_direction.py [--store DIR] [--items-dir DIR] [--out DIR]

Writes ``group_direction.json`` and ``group_direction.md`` into ``--out`` (default
``outputs/group-direction-<today>/``, gitignored) and prints the markdown.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from paths import REPO_ROOT, data_dir  # noqa: E402

GROUP_PREFIX = "group:"
HIERARCHY_RELATIONS = {"disease_disease": "DIS-DIS/PARENT", "phenotype_phenotype": "PHE-PHE/PARENT"}
"""Store hierarchy relations present in the store, and their OptimusKG counterparts."""
CURIE_FORMAT = {"MONDO": "MONDO_{:07d}", "HP": "HP_{:07d}"}
"""Store CURIE prefix -> OptimusKG node ID format."""

RELABEL = "disease_disease/grouped"
"""A derived label for undirected grouped-end hierarchy edges (the ``-label`` options),
in the style of the store's ``drug_protein/<display_relation>`` labels. Illustrative."""

CLASSES = ("agrees", "reversed", "equivalent", "mixed", "partial_agree", "partial_reversed",
           "no_ancestry", "unknown")
EVIDENCE_AGAINST = frozenset({"reversed", "equivalent", "mixed", "partial_reversed"})
"""Classes where OptimusKG has at least one member pair running against the stored order."""

# --------------------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------------------


def ancestor_fn(parents: Mapping[str, Iterable[str]]) -> Callable[[str], frozenset[str]]:
    """Strict ancestors of a node, from a child -> parents map (memoized)."""

    @lru_cache(maxsize=None)
    def anc(node: str) -> frozenset[str]:
        seen: set[str] = set()
        stack = [node]
        while stack:
            for p in parents.get(stack.pop(), ()):
                if p not in seen:
                    seen.add(p)
                    stack.append(p)
        return frozenset(seen)

    return anc


def classify(s_members: list[str], o_members: list[str],
             anc: Callable[[str], frozenset[str]], known: set[str] | frozenset[str]) -> str:
    """One of :data:`CLASSES` for an edge stored subject -> object (child -> parent).

    Args:
        s_members, o_members: reference-ontology IDs of the two ends (a group's
            members, or the single term).
        anc: strict ancestors of a reference ID.
        known: reference IDs the reference hierarchy has at all.
    """
    if not any(x in known for x in s_members) or not any(x in known for x in o_members):
        return "unknown"

    def contains(inner: list[str], outer: list[str]) -> bool:
        return all(any(o == i or o in anc(i) for o in outer) for i in inner)

    fwd_all, rev_all = contains(s_members, o_members), contains(o_members, s_members)
    if fwd_all and rev_all:
        return "equivalent"
    if fwd_all:
        return "agrees"
    if rev_all:
        return "reversed"
    fwd = any(o in anc(s) for s in s_members for o in o_members)
    rev = any(s in anc(o) for s in s_members for o in o_members)
    if fwd and rev:
        return "mixed"
    if fwd:
        return "partial_agree"
    if rev:
        return "partial_reversed"
    return "no_ancestry"


def simple_cycles(pairs: Iterable[tuple[str, str]]) -> list[list[str]]:
    """Every elementary directed cycle in a graph given as (u, v) arcs.

    Each cycle is a node list starting at its smallest node, listed once. Restricted to
    strongly connected components with more than one node, then a depth-first search
    from each node over nodes not smaller than it (enough for the small components a
    hierarchy with a few faults has).
    """
    succ: dict[str, set[str]] = defaultdict(set)
    nodes: set[str] = set()
    for u, v in pairs:
        succ[u].add(v)
        nodes |= {u, v}
    # Tarjan's strongly connected components, iterative.
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    comps: list[set[str]] = []
    counter = 0
    for root in sorted(nodes):
        if root in index:
            continue
        work = [(root, iter(sorted(succ[root])))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, it = work[-1]
            nxt = next(it, None)
            if nxt is not None:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(sorted(succ[nxt]))))
                elif nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                comp = set()
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.add(w)
                    if w == node:
                        break
                if len(comp) > 1:
                    comps.append(comp)
    cycles: list[list[str]] = []
    for comp in comps:
        for start in sorted(comp):
            allowed = {n for n in comp if n >= start}
            path = [start]

            def dfs(n: str) -> None:
                for m in sorted(succ[n] & allowed):
                    if m == start:
                        cycles.append(list(path))
                    elif m not in path:
                        path.append(m)
                        dfs(m)
                        path.pop()

            dfs(start)
    return cycles


# --------------------------------------------------------------------------------------
# Options
# --------------------------------------------------------------------------------------

OPTIONS: dict[str, str] = {
    "A": "Grouped-end edge stays directed only if the object group contains the subject group "
         "(class agrees); every other grouped-end edge becomes undirected, stored order kept.",
    "A2": "Narrow A: undirected only where OptimusKG has a member pair against the stored order "
          "(reversed, mixed, partial_reversed) or the edge is on a directed cycle.",
    "B": "Grouped-end edge: agrees -> kept; reversed (subject contains object) -> re-oriented and "
         "kept directed; every other class -> undirected.",
    "B2": "Narrow B: reversed -> re-oriented; mixed, partial_reversed or on a cycle -> undirected; "
          "every other class kept as stored.",
    "C": "Drop every grouped-end hierarchy edge that is not agrees.",
    "C2": "Narrow C: drop only edges with a member pair against the stored order, or on a cycle.",
    "D": "No store change; disclose as a limitation.",
    "E": "Every disease_disease edge undirected (grouped or not); one flag per relation kept.",
    "F": "Every disease_disease edge with a grouped end undirected, whatever OptimusKG says "
         "(no build-time dependency on OptimusKG).",
    "A-label": "A, but the edges A makes undirected move to a derived undirected label "
               f"({RELABEL!r}), so every label keeps one directed value.",
    "F-label": f"F, but the grouped-end edges move to a derived undirected label ({RELABEL!r}).",
    "G": "No store change. The generator keeps every hierarchy edge with a grouped end out of "
         "items: no gold point, premise or gap on one, and role-reading templates don't traverse one.",
}

ACTIONS = ("keep", "undirect", "reverse", "drop", "relabel", "exclude")
"""``exclude`` (option G) leaves the edge in the store and only keeps it out of items."""


def action(option: str, relation: str, grouped: bool, cls: str | None, on_cycle: bool) -> str:
    """What ``option`` does to one directed edge: one of :data:`ACTIONS`.

    ``cls`` is the edge's class (None for an edge not classified); ``grouped`` is
    whether either end is a ``group:`` entity.
    """
    if option == "D":
        return "keep"
    if option == "E":
        return "undirect" if relation == "disease_disease" else "keep"
    if not grouped:
        return "keep"
    against = cls in EVIDENCE_AGAINST or on_cycle
    if option == "A":
        return "keep" if cls == "agrees" and not on_cycle else "undirect"
    if option == "A2":
        return "undirect" if against else "keep"
    if option == "B":
        if cls == "agrees" and not on_cycle:
            return "keep"
        return "reverse" if cls == "reversed" else "undirect"
    if option == "B2":
        if cls == "reversed":
            return "reverse"
        return "undirect" if against else "keep"
    if option == "C":
        return "keep" if cls == "agrees" and not on_cycle else "drop"
    if option == "C2":
        return "drop" if against else "keep"
    if option == "F":
        return "undirect" if relation == "disease_disease" else "keep"
    if option == "G":
        return "exclude" if relation == "disease_disease" and grouped else "keep"
    if option in ("A-label", "F-label"):
        a = action(option.split("-")[0], relation, grouped, cls, on_cycle)
        return "relabel" if a == "undirect" else a
    raise ValueError(f"unknown option {option!r}")


@dataclass
class Edge:
    """A store edge, reduced to what this measurement needs."""

    id: str
    subject: str
    relation: str
    object: str
    directed: bool


def apply_option(edges: list[Edge], actions: Mapping[str, str]) -> list[Edge]:
    """The edge list after the per-edge actions (edge ID -> action; missing = keep)."""
    out = []
    for e in edges:
        a = actions.get(e.id, "keep")  # "exclude" keeps the edge as stored
        if a == "drop":
            continue
        if a == "reverse":
            out.append(Edge(e.id, e.object, e.relation, e.subject, True))
        elif a == "undirect":
            out.append(Edge(e.id, e.subject, e.relation, e.object, False))
        elif a == "relabel":
            out.append(Edge(e.id, e.subject, RELABEL, e.object, False))
        else:
            out.append(e)
    return out


def renumber(edges: list[Edge], width: int = 6) -> dict[str, str]:
    """Old ID -> new ID under the builder's rule: ``E:`` numbers in (relation, subject,
    object) order (``build.assign_evidence_ids``). Dropped edges are absent."""
    ordered = sorted(edges, key=lambda e: (e.relation, e.subject, e.object))
    return {e.id: f"E:{i:0{width}d}" for i, e in enumerate(ordered, start=1)}


def mixed_flag_relations(edges: Iterable[Edge]) -> list[str]:
    """Relations with both directed and undirected edges (the store, the generator and
    the item contract all assume one flag per relation)."""
    flags: dict[str, set[bool]] = defaultdict(set)
    for e in edges:
        flags[e.relation].add(e.directed)
    return sorted(r for r, f in flags.items() if len(f) > 1)


# --------------------------------------------------------------------------------------
# Train items
# --------------------------------------------------------------------------------------

ROLE_BRIDGE_RELATION = {"subtype_assoc": "disease_disease", "phenotype_kind": "phenotype_phenotype"}
"""Bridge relation keys whose intermediate has a fixed role on a hierarchy edge
(``qgen_templates.BRIDGES``: ``m_role_x`` / ``m_role_y``), and that edge's relation."""


def hierarchy_roles_read(template_id: str, relation_key: str, params: Iterable[str]) -> set[str]:
    """Hierarchy relations on which the template picks answers by role (child or
    parent): a role bridge, a list clause on a hierarchy relation
    (``disease_disease:children`` in the params), or a reversed-subtype premise. Such a
    template skips undirected edges (``qgen.candidates.role`` is None for them) or reads
    subject/object positions, so changing a hierarchy edge at its named entity can
    change or remove the item on regeneration."""
    out = set()
    if relation_key in ROLE_BRIDGE_RELATION:
        out.add(ROLE_BRIDGE_RELATION[relation_key])
    if template_id.startswith("fp_reversed"):
        out.add("disease_disease")
    out |= {p.split(":")[0] for p in params if p.split(":")[0] in HIERARCHY_RELATIONS and ":" in p}
    return out


@dataclass
class ItemView:
    """The parts of a train item this measurement reads: IDs and triples, no text."""

    id: str
    template_id: str
    gold: list[tuple[str, str, str, bool, tuple[str, ...]]]  # subject, relation, object, directed, evidence
    premise: tuple[str, str, str, bool] | None
    gaps: list[tuple[str, str, str, bool]]
    params: list[str]
    relation_key: str
    route: list[dict] = field(default_factory=list)


def item_view(item) -> ItemView:
    """An :class:`ItemView` of a ``QuestionItem`` (reads ``notes`` for params and route)."""
    notes = json.loads(item.notes) if isinstance(item.notes, str) else (item.notes or {})
    p = item.premise
    return ItemView(
        id=item.id, template_id=item.template_id or "",
        gold=[(g.subject_id, g.relation, g.object_id, g.directed, tuple(g.evidence)) for g in item.gold_points],
        premise=(p.subject_id, p.relation, p.object_id, p.directed) if p else None,
        gaps=[(g.triple.subject_id, g.triple.relation, g.triple.object_id, g.triple.directed)
              for g in item.graph_gaps if g.triple is not None],
        params=list(notes.get("template_params", [])),
        relation_key=notes.get("relation_key", ""),
        route=list(notes.get("route", [])),
    )


def item_effects(it: ItemView, actions: Mapping[str, str], edges_by_id: Mapping[str, Edge],
                 changed_entities: Mapping[str, set[str]]) -> list[str]:
    """Reasons an option affects one train item (empty: the item is untouched apart from
    the new ``build_id``). ``changed_entities``: relation -> entities with a changed edge
    of that relation.

    - ``gold_dropped`` / ``gold_reoriented`` / ``gold_undirected`` / ``gold_relabelled`` /
      ``gold_excluded``: a gold edge changes, or (option G) may no longer be gold;
      the item as written then fails the generator's flag check (or cites a missing edge).
    - ``mixed_flags``: after the change the item holds directed and undirected triples
      on one relation, which ``QuestionItem`` validation rejects.
    - ``premise_or_gap_pair_changed``: the premise or a gap triple is on a changed pair.
    - ``role_template_at_changed_entity``: a template that reads a hierarchy role
      (:func:`hierarchy_roles_read`) names an entity with a changed edge of that
      relation, so
      its answer set, or whether it is generated at all, can change.
    - ``route_counts_change``: the recorded route lists the neighbors of an entity that
      loses an edge (truncation and pagination can change).
    """
    reasons: list[str] = []
    new_flags: dict[str, set[bool]] = defaultdict(set)
    for s, rel, o, d, ev in it.gold:
        acts = {actions.get(e, "keep") for e in ev}
        if "drop" in acts:
            reasons.append("gold_dropped")
        if "reverse" in acts:
            reasons.append("gold_reoriented")
        if "undirect" in acts:
            reasons.append("gold_undirected")
        if "exclude" in acts:
            reasons.append("gold_excluded")
        if "relabel" in acts:
            reasons.append("gold_relabelled")
            new_flags[RELABEL].add(False)
            continue
        new_flags[rel].add(False if acts & {"undirect"} else d)
    changed_pairs = {
        (e.relation, frozenset((e.subject, e.object)))
        for eid, e in edges_by_id.items() if actions.get(eid, "keep") not in ("keep", "exclude")
    }
    excluded_pairs = {
        (e.relation, frozenset((e.subject, e.object)))
        for eid, e in edges_by_id.items() if actions.get(eid) == "exclude"
    }
    extra = ([it.premise] if it.premise else []) + it.gaps
    for s, rel, o, d in extra:
        if (rel, frozenset((s, o))) in excluded_pairs:
            reasons.append("premise_or_gap_pair_excluded")
        if (rel, frozenset((s, o))) in changed_pairs:
            reasons.append("premise_or_gap_pair_changed")
            new_flags[rel].add(False)
        else:
            new_flags[rel].add(d)
    if any(len(f) > 1 for f in new_flags.values()):
        reasons.append("mixed_flags")
    if any(changed_entities.get(r, set()) & set(it.params)
           for r in hierarchy_roles_read(it.template_id, it.relation_key, it.params)):
        reasons.append("role_template_at_changed_entity")
    dropped_ends = {x for eid, e in edges_by_id.items() if actions.get(eid) == "drop" for x in (e.subject, e.object)}
    for call in it.route:
        if call.get("tool") == "get_neighbors" and call.get("relation") in (None, *HIERARCHY_RELATIONS) \
                and call.get("entity_id") in dropped_ends:
            reasons.append("route_counts_change")
            break
    return sorted(set(reasons))


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------


def _read_jsonl(p: Path) -> list[dict]:
    with p.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def reference_id(curie: str) -> str | None:
    """A plain store CURIE in OptimusKG form (``MONDO:0004975`` -> ``MONDO_0004975``)."""
    pre, _, num = curie.partition(":")
    return CURIE_FORMAT[pre].format(int(num)) if pre in CURIE_FORMAT and num.isdigit() else None


def load_reference(db: Path) -> dict[str, tuple[dict[str, set[str]], set[str]]]:
    """OptimusKG relation -> (child -> parents, every node in that hierarchy)."""
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    out = {}
    for orel in HIERARCHY_RELATIONS.values():
        parents: dict[str, set[str]] = defaultdict(set)
        known: set[str] = set()
        for u, v in con.execute("SELECT u, v FROM edges WHERE rel = ?", [orel]).fetchall():
            parents[v].add(u)
            known |= {u, v}
        out[orel] = (dict(parents), known)
    con.close()
    return out


def measure(store: Path, items_dir: Path | None, reference_db: Path) -> dict:
    """Everything the report holds, as one JSON-ready dict."""
    ents = {r["id"]: r for r in _read_jsonl(store / "entities.jsonl")}
    srcs = {r["id"]: r for r in _read_jsonl(store / "entity_sources.jsonl")}
    raw_edges = _read_jsonl(store / "edges.jsonl")
    edges = [Edge(r["id"], r["subject"], r["relation"], r["object"], r["directed"]) for r in raw_edges]
    by_id = {e.id: e for e in edges}
    directed = [e for e in edges if e.directed]
    ref = load_reference(reference_db)

    def members(eid: str) -> list[str]:
        if eid.startswith(GROUP_PREFIX):
            return [m for m in (reference_id(x["id"]) for x in srcs[eid].get("members", [])) if m]
        r = reference_id(eid)
        return [r] if r else []

    def head(eid: str) -> list[str]:
        """The member(s) named like the group itself: what a reader takes the group to
        be. All members if none shares the name; the term itself for a plain ID."""
        if not eid.startswith(GROUP_PREFIX):
            return members(eid)
        name = ents[eid]["name"].casefold()
        hits = [reference_id(x["id"]) for x in srcs[eid].get("members", []) if x.get("name", "").casefold() == name]
        return [h for h in hits if h] or members(eid)

    grouped = lambda e: e.subject.startswith(GROUP_PREFIX) or e.object.startswith(GROUP_PREFIX)  # noqa: E731
    cycles = simple_cycles((e.subject, e.object) for e in directed)
    arc_ids = defaultdict(list)
    for e in directed:
        arc_ids[(e.subject, e.object)].append(e.id)
    cycle_edges = {i for c in cycles for k in range(len(c)) for i in arc_ids[(c[k], c[(k + 1) % len(c)])]}

    anc_fns = {orel: ancestor_fn(p) for orel, (p, _k) in ref.items()}
    rows, cls_of = [], {}
    for e in directed:
        orel = HIERARCHY_RELATIONS[e.relation]
        s_ids, o_ids = members(e.subject), members(e.object)
        c = classify(s_ids, o_ids, anc_fns[orel], ref[orel][1])
        cls_of[e.id] = c
        known = ref[orel][1]
        s_unknown = sum(1 for x in s_ids if x not in known)
        o_unknown = sum(1 for x in o_ids if x not in known)
        agrees_known = False
        if c == "partial_agree" and (s_unknown or o_unknown):
            s_known, o_known = [x for x in s_ids if x in known], [x for x in o_ids if x in known]
            agrees_known = classify(s_known, o_known, anc_fns[orel], known) == "agrees"
        rows.append({
            "id": e.id, "relation": e.relation, "grouped": grouped(e),
            "subject": e.subject, "subject_name": ents[e.subject]["name"], "subject_members": len(s_ids),
            "subject_members_unknown": s_unknown,
            "object": e.object, "object_name": ents[e.object]["name"], "object_members": len(o_ids),
            "object_members_unknown": o_unknown,
            "class": c, "on_cycle": e.id in cycle_edges,
            "agrees_if_unknown_members_ignored": agrees_known,
            "head_class": classify(head(e.subject), head(e.object), anc_fns[orel], known),
        })

    counts = Counter((r["relation"], r["grouped"], r["class"]) for r in rows)
    head_counts = Counter((r["class"], r["head_class"]) for r in rows if r["grouped"])
    grouped_entities_by_relation = Counter(
        e.relation for e in edges if grouped(e))

    # Train items (IDs and triples only).
    items: list[ItemView] = []
    if items_dir is not None:
        from qgen.view import load_train

        items = [item_view(i) for i in load_train(items_dir)]

    options = {}
    base_ids = {e.id: e.id for e in edges}
    for opt in OPTIONS:
        acts = {e.id: action(opt, e.relation, grouped(e), cls_of.get(e.id), e.id in cycle_edges)
                for e in directed}
        acts = {k: v for k, v in acts.items() if v != "keep"}
        new_edges = apply_option(edges, acts)
        new_ids = renumber(new_edges)
        id_changed = [i for i, n in new_ids.items() if n != base_ids[i]]
        outside_dd = [i for i in id_changed if by_id[i].relation != "disease_disease"]
        after_cycles = simple_cycles((e.subject, e.object) for e in new_edges if e.directed)
        by_class = Counter((cls_of.get(k, "unclassified"), v) for k, v in acts.items())
        changed_entities: dict[str, set[str]] = defaultdict(set)
        for k in acts:
            changed_entities[by_id[k].relation] |= {by_id[k].subject, by_id[k].object}
        item_rows = []
        for it in items:
            reasons = item_effects(it, acts, by_id, changed_entities)
            if reasons:
                item_rows.append({"id": it.id, "template_id": it.template_id, "reasons": reasons})
        gold_ids = {ev for it in items for g in it.gold for ev in g[4]}
        options[opt] = {
            "rule": OPTIONS[opt],
            "edges_changed": len(acts),
            "by_action": dict(Counter(acts.values())),
            "by_class_and_action": {f"{c}|{a}": n for (c, a), n in sorted(by_class.items())},
            "edge_ids_renumbered": len(id_changed),
            "edge_ids_renumbered_outside_disease_disease": len(outside_dd),
            "train_gold_edge_ids_renumbered": len(gold_ids & set(id_changed)),
            "train_gold_edge_ids_dropped": len(gold_ids & set(k for k, v in acts.items() if v == "drop")),
            "mixed_flag_relations": mixed_flag_relations(new_edges),
            "cycles_after": len(after_cycles),
            "train_items_affected": item_rows,
            "train_items_affected_by_template": dict(Counter(r["template_id"] for r in item_rows)),
            "train_items_affected_by_reason": dict(Counter(x for r in item_rows for x in r["reasons"])),
        }

    hier_items = [
        {"id": it.id, "template_id": it.template_id,
         "hierarchy_gold_edges": sorted({ev for g in it.gold if g[1] in HIERARCHY_RELATIONS for ev in g[4]})}
        for it in items
        if any(g[1] in HIERARCHY_RELATIONS for g in it.gold)
        or (it.premise and it.premise[1] in HIERARCHY_RELATIONS)
        or any(g[1] in HIERARCHY_RELATIONS for g in it.gaps)
    ]
    return {
        "store": str(store),
        "build_id": json.loads((store / "build.json").read_text())["build_id"],
        "edge_count": len(edges),
        "directed_edges": len(directed),
        "directed_by_relation": dict(Counter(e.relation for e in directed)),
        "edges_with_grouped_end_by_relation": dict(grouped_entities_by_relation),
        "class_counts": {f"{r}|{'grouped' if g else 'plain'}|{c}": n for (r, g, c), n in sorted(counts.items())},
        "class_by_head_class_grouped": {f"{a}|{b}": n for (a, b), n in sorted(head_counts.items())},
        "cycles": [[f"{n} [{ents[n]['name']}]" for n in c] for c in cycles],
        "cycle_edges": sorted(cycle_edges),
        "edges": rows,
        "options": options,
        "train_items": len(items),
        "train_items_with_hierarchy_triples": hier_items,
    }


def markdown(r: dict) -> str:
    """The report as markdown: counts, the flagged-edge table, cycles and options."""
    L = [f"# Grouped-entity edge direction: store `{r['build_id']}`", ""]
    L.append(f"{r['directed_edges']} directed edges: {r['directed_by_relation']}. "
             f"Edges with a grouped end, by relation: {r['edges_with_grouped_end_by_relation']}.")
    L += ["", "## Classes", "", "| Relation | Ends | Class | Edges |", "|---|---|---|---|"]
    for k, n in r["class_counts"].items():
        rel, g, c = k.split("|")
        L.append(f"| `{rel}` | {g} | {c} | {n} |")
    ign = sum(1 for e in r["edges"] if e["agrees_if_unknown_members_ignored"])
    L += ["", f"`partial_agree` edges that would be `agrees` if members unknown to OptimusKG were "
          f"ignored: {ign}.", ""]
    L += ["Grouped-end edges, member-set class by head-member class (the member named like the group):", "",
          "| Class | Head class | Edges |", "|---|---|---|"]
    for k, n in r["class_by_head_class_grouped"].items():
        a, b = k.split("|")
        L.append(f"| {a} | {b} | {n} |")
    L += ["", "## Cycles", ""] + [f"- {' -> '.join(c)} -> (back to start)" for c in r["cycles"]]
    L += ["", "## Grouped-end edges with evidence against the stored order (by members or by head member) or on a cycle", "",
          "| Edge | Subject | Object | Class | Head class | On cycle | Members (S/O, unknown) |",
          "|---|---|---|---|---|---|---|"]
    for e in r["edges"]:
        if e["grouped"] and (e["class"] in EVIDENCE_AGAINST or e["on_cycle"] or e["head_class"] in EVIDENCE_AGAINST):
            L.append(f"| `{e['id']}` | `{e['subject']}` {e['subject_name']} | `{e['object']}` {e['object_name']} | "
                     f"{e['class']} | {e['head_class']} | {'yes' if e['on_cycle'] else ''} | {e['subject_members']}/{e['object_members']}"
                     f" ({e['subject_members_unknown']}/{e['object_members_unknown']}) |")
    L += ["", "## Options", ""]
    L += ["| Option | Edges changed | Actions | Renumbered IDs (outside dd) | Train gold IDs renumbered / dropped "
          "| Mixed-flag relations | Cycles after | Train items affected |", "|---|---|---|---|---|---|---|---|"]
    for k, o in r["options"].items():
        L.append(f"| {k} | {o['edges_changed']} | {o['by_action']} | {o['edge_ids_renumbered']} "
                 f"({o['edge_ids_renumbered_outside_disease_disease']}) | {o['train_gold_edge_ids_renumbered']} / "
                 f"{o['train_gold_edge_ids_dropped']} | {', '.join(o['mixed_flag_relations']) or 'none'} | "
                 f"{o['cycles_after']} | {len(o['train_items_affected'])} |")
    for k, o in r["options"].items():
        L += ["", f"### {k}", "", o["rule"], "", f"- By class and action: {o['by_class_and_action']}",
              f"- Train items by reason: {o['train_items_affected_by_reason']}",
              f"- Train items by template: {o['train_items_affected_by_template']}"]
        for it in o["train_items_affected"]:
            L.append(f"  - `{it['id']}` ({it['template_id']}): {', '.join(it['reasons'])}")
    L += ["", f"## Train items with a hierarchy triple ({len(r['train_items_with_hierarchy_triples'])} of "
          f"{r['train_items']})", ""]
    for it in r["train_items_with_hierarchy_triples"]:
        L.append(f"- `{it['id']}` ({it['template_id']}): {', '.join(it['hierarchy_gold_edges']) or 'no gold edge'}")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--store", default=str(data_dir() / "store" / "primekg-neuro-c95"))
    ap.add_argument("--items-dir", default=None,
                    help="train items directory (default: items/<the store's build_id>); 'none' to skip")
    ap.add_argument("--reference-db", default=str(data_dir() / "derived" / "optimuskg.duckdb"))
    ap.add_argument("--out", default=str(REPO_ROOT / "outputs" / f"group-direction-{date.today().isoformat()}"))
    a = ap.parse_args(argv)
    store = Path(a.store).expanduser()
    build_id = json.loads((store / "build.json").read_text())["build_id"]
    items_dir = None if a.items_dir == "none" else Path(a.items_dir or data_dir() / "items" / build_id)
    r = measure(store, items_dir, Path(a.reference_db))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "group_direction.json").write_text(json.dumps(r, indent=1) + "\n", encoding="utf-8")
    md = markdown(r)
    (out / "group_direction.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
