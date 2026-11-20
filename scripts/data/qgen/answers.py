"""Gold completeness: is an item's gold the complete set of true answers in the store?

Reward v1 penalizes supported facts that state no gold point. That is fair only if
gold holds **every** fact in the store that answers the question; if the store had a
true answer the gold left out, an honest answer naming it would be penalized as
padding. This module recomputes, for one item, every store fact that answers its
question, and compares that set with the item's gold points:

- a **missing answer** is a store fact that answers the question but is no gold
  point;
- a **non-answer** is a gold point whose fact doesn't answer the question.

Gold is complete when there are neither. Facts are compared with
``checks.fact_key`` (equal exactly when ``contracts.same_fact`` holds), so two store
edges stating the same fact count once, as the reward counts them.

What answers a question is defined per template family, next to the templates, in
``qgen_templates.ANSWER_RULE`` (rules described in ``qgen_templates.ANSWER_RULES``).
The question itself is read from the item's template and its recorded template
parameters, the same values the question text was rendered from, never from the
gold, so the check doesn't assume what it tests.

The answers are computed from the store's edges through :class:`Adjacency`, an index
built here, not from the generator's own ``candidates.Graph`` or shape generators.

Used by the generator (``candidates.build_pool``, stage ``gold_complete``) and by
the item check (``verify.Verifier``, check ``gold_complete``). Returns counts and
reasons only, never item content.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

import qgen_templates as qt
from neuro_grounded_qa.contracts import EdgeEvidence, QuestionItem

from qgen import checks as qc

Fact = tuple[str, str, str]
"""A fact key (``checks.fact_key``)."""


class Adjacency:
    """Store edges by entity and relation, plus edge and entity lookup.

    Built from every store edge once (the store protocol's ``neighbors`` scans the
    whole edge list per call, too slow for a pool). An edge is listed under both of
    its ends, once under a self-loop's single end.
    """

    def __init__(self, edges: Iterable[EdgeEvidence]) -> None:
        self._by_entity: dict[str, dict[str, list[EdgeEvidence]]] = defaultdict(lambda: defaultdict(list))
        self._by_id: dict[str, EdgeEvidence] = {}
        self._types: dict[str, str] = {}
        for e in edges:
            self._by_id[e.id] = e
            self._types[e.subject.id] = e.subject.type
            self._types[e.object.id] = e.object.type
            self._by_entity[e.subject.id][e.relation].append(e)
            if e.object.id != e.subject.id:
                self._by_entity[e.object.id][e.relation].append(e)

    def edges(self, entity_id: str, relation: str | None = None) -> list[EdgeEvidence]:
        """Edges at ``entity_id`` (either end), of one relation or of any."""
        rels = self._by_entity.get(entity_id, {})
        if relation is not None:
            return list(rels.get(relation, []))
        return [e for es in rels.values() for e in es]

    def edge(self, edge_id: str) -> EdgeEvidence | None:
        return self._by_id.get(edge_id)

    def entity_type(self, entity_id: str) -> str | None:
        """The type of an entity that has at least one store edge, else None."""
        return self._types.get(entity_id)


def fact_of(e: EdgeEvidence) -> Fact:
    return qc.fact_key(e.subject.id, e.relation, e.object.id, e.directed)


def _other(e: EdgeEvidence, a: str) -> str:
    return e.object.id if e.subject.id == a else e.subject.id


def _role(e: EdgeEvidence, m: str) -> str | None:
    """``child``/``parent`` for ``m`` on a directed edge (child = subject), else None."""
    if not e.directed:
        return None
    return "child" if e.subject.id == m else "parent"


# --------------------------------------------------------------------------------------
# The rules (qgen_templates.ANSWER_RULES)
# --------------------------------------------------------------------------------------


def list_answers(adj: Adjacency, seed: str, key: str) -> set[Fact]:
    """``list``: the facts of list clause ``key`` (a relation, or
    ``disease_disease:children`` / ``:parents``) at ``seed``."""
    rel, _, role = key.partition(":")
    edges = adj.edges(seed, rel)
    if role == "children":  # the seed is the parent: the object
        edges = [e for e in edges if e.object.id == seed and e.subject.id != seed]
    elif role == "parents":  # the seed is the child: the subject
        edges = [e for e in edges if e.subject.id == seed and e.object.id != seed]
    elif role:
        raise ValueError("unknown list role")
    return {fact_of(e) for e in edges}


def stated(adj: Adjacency, subj: str, rel: str, obj: str) -> set[Fact]:
    """``stated_facts`` for one statement: edges of ``rel`` between ``subj`` and
    ``obj``, in that order when the edge is directed."""
    if subj == obj:
        return set()
    return {fact_of(e) for e in adj.edges(subj, rel)
            if _other(e, subj) == obj and (not e.directed or (e.subject.id, e.object.id) == (subj, obj))}


def between(adj: Adjacency, a: str, b: str, relation: str | None = None) -> set[Fact]:
    """Facts of every edge joining ``a`` and ``b`` (any relation unless given)."""
    if a == b:
        return set()
    return {fact_of(e) for e in adj.edges(a, relation) if _other(e, a) == b}


def bridge_answers(adj: Adjacency, b: qt.Bridge, x: str, y: str) -> set[Fact]:
    """``bridge_paths``: both edges' facts of every path ``x -r_x- m -r_y- y``."""
    out: set[Fact] = set()
    for e1 in adj.edges(x, b.r_x):
        m = _other(e1, x)
        if m in (x, y) or adj.entity_type(m) != b.m_type:
            continue
        if b.m_role_x and _role(e1, m) != b.m_role_x:
            continue
        for e2 in adj.edges(m, b.r_y):
            if _other(e2, m) == y and (not b.m_role_y or _role(e2, m) == b.m_role_y):
                out |= {fact_of(e1), fact_of(e2)}
    return out


_TEMPLATES = {t.id: t for t in qt.catalog()}
_BRIDGES = {b.key: b for b in qt.BRIDGES}


def _edge_statement(adj: Adjacency, edge_id: str, rel: str) -> tuple[str, str, str]:
    e = adj.edge(edge_id)
    if e is None or e.relation != rel:
        raise ValueError("template parameter is not a store edge of the template's relation")
    return e.subject.id, rel, e.object.id


def answer_facts(template_id: str, params: list[str], adj: Adjacency) -> set[Fact]:
    """Every store fact that answers the question of template ``template_id`` asked
    with ``params`` (the item's recorded ``template_params``).

    Raises KeyError for a template or family with no rule, ValueError for parameters
    that don't fit the template.
    """
    tpl = _TEMPLATES.get(template_id)
    if tpl is None:
        raise KeyError("unknown template")
    rule = qt.answer_rule(template_id)
    fam = qt.family(template_id)
    key = tpl.relation_key
    try:
        if rule == "list":
            (s,) = params
            return list_answers(adj, s, key)
        if rule == "list2":
            s, k1, k2 = params
            if k1 not in qt.LIST_CLAUSE or k2 not in qt.LIST_CLAUSE:
                raise ValueError("unknown list clause")
            return list_answers(adj, s, k1) | list_answers(adj, s, k2)
        if rule == "stated_facts":
            if fam in ("sh_evid", "fp_evid"):
                subj, rel, obj = params
                if rel != key:
                    raise ValueError("statement relation differs from the template's")
                statements = [(subj, rel, obj)]
            elif fam == "sh_evid2":
                e1, e2 = params
                statements = [_edge_statement(adj, e1, key), _edge_statement(adj, e2, key)]
            elif fam == "pa_evid2":
                e1, subj, obj = params
                statements = [_edge_statement(adj, e1, key), (subj, key, obj)]
            else:
                raise KeyError("no parameter reading for this family under stated_facts")
            return set().union(*(stated(adj, s, r, o) for s, r, o in statements))
        if rule == "opposite_fact":
            subj, rel, obj = params
            if rel != key or rel not in qt.OPPOSITE:
                raise ValueError("premise relation has no opposite")
            return between(adj, subj, obj, qt.OPPOSITE[rel])
        if rule == "reversed_fact":
            subj, rel, obj = params
            if rel != key:
                raise ValueError("statement relation differs from the template's")
            return stated(adj, obj, rel, subj)
        if rule == "pair_any_relation":
            a, b = params
            return between(adj, a, b)
        if rule == "bridge_paths":
            x, y = params
            return bridge_answers(adj, _BRIDGES[key], x, y)
    except (TypeError, ValueError) as e:  # wrong number of parameters, mostly
        raise ValueError("template parameters don't fit the template") from e
    raise KeyError("unknown answer rule")


# --------------------------------------------------------------------------------------
# The check
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldCompleteness:
    """Result of :func:`gold_completeness`.

    ``reason`` is ``ok``, ``missing_answer``, ``non_answer_in_gold``,
    ``missing_answer_and_non_answer``, ``no_rule`` (the template has no answer rule)
    or ``bad_params`` (its recorded parameters don't fit it). ``missing`` and
    ``extra`` count facts.
    """

    ok: bool
    reason: str
    missing: int = 0
    extra: int = 0


def gold_completeness(item: QuestionItem, params: list[str], adj: Adjacency) -> GoldCompleteness:
    """Do ``item``'s gold points equal every store fact that answers its question?

    Args:
        item: the item (its ``template_id`` and gold points are used).
        params: the item's template parameters (``notes["template_params"]``).
        adj: the store's edges.
    """
    try:
        answers = answer_facts(item.template_id or "", list(params), adj)
    except KeyError:
        return GoldCompleteness(False, "no_rule")
    except ValueError:
        return GoldCompleteness(False, "bad_params")
    gold = {qc.fact_key(gp.subject_id, gp.relation, gp.object_id, gp.directed) for gp in item.gold_points}
    missing, extra = len(answers - gold), len(gold - answers)
    if missing and extra:
        reason = "missing_answer_and_non_answer"
    elif missing:
        reason = "missing_answer"
    elif extra:
        reason = "non_answer_in_gold"
    else:
        reason = "ok"
    return GoldCompleteness(reason == "ok", reason, missing, extra)
