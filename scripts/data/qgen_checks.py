"""Validity checks for question generation: the reusable, deterministic parts.

The question generator (not written yet; see "Question generation design" in
``docs/question_set_design.md``) builds candidate items from templates and keeps only
those that pass every check below. ``scripts/data/qgen_feasibility.py`` runs the same
checks now, to count how many candidates survive before any item is written. Both
use this module, so the counts and the eventual items rest on one implementation.

What lives here, and why each check exists:

- **Name ambiguity** (:func:`name_check`). Questions name entities by name, never by
  ID, so the model has to find the right node with ``search_entities``. A name that
  returns another node first, or that another node shares, makes the question
  ambiguous. The check runs the real tool body, so ranking and caps are exactly what
  a model would see.
- **Fallback entities** (:func:`has_fallback`). The store gives ``primekg:<index>``
  IDs to the 126 nodes whose CURIE collides (mostly one gene under an old and a new
  symbol). A claim about one copy doesn't match the other, so items avoid them.
- **Full-graph absence** (:class:`FullGraph`). "The graph has no such fact" must hold
  for the **whole** PrimeKG graph, not just the store's subgraph; otherwise an item
  could be unanswerable only because the subgraph cut its edge. Pair absence (no
  edge under any relation) is what unanswerable items, absent premises and graph
  gaps use; triple absence (no edge under this relation, in order for hierarchy
  relations) is what a correctable premise uses, since its pair does carry an edge.
- **Reachability and observability** (:class:`Prober`, :func:`plan_route`). Pagination
  is off, so a fact the model can't reach in one ``get_neighbors`` page can't be
  retrieved at all. Every gold edge must be returned by some call, and every absent
  fact must be *visibly* absent: some call whose page is complete (not truncated)
  would have shown it. A route is the set of calls that does both, within the call
  budget; the item records it.
- **Flag consistency** (:func:`gold_point_problems`, :func:`triple_flag_problems`).
  ``directed`` on a gold point must match the store edge it cites, in the edge's
  order; on a premise or gap triple it must match the relation's convention.
- **Split assignment** (:func:`split_of`). A candidate's split is a pure function of
  its key and the split seed, so no regeneration can move a held-out candidate into
  train.
- **Near-duplicates and leakage** (:func:`near_duplicate`, :func:`answer_leaks`).

Nothing here prints or returns question text. Checks return booleans, reasons and
counts, so the scripts that call them can report aggregates only (see "Held-out
protection" in the design doc).
"""

from __future__ import annotations

import hashlib
import itertools
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from neuro_grounded_qa.contracts import EdgeEvidence, Entity, ToolResultPayload

FALLBACK_PREFIX = "primekg:"
"""ID prefix of entities whose CURIE collides (``build.FALLBACK_PREFIX`` + ``:``)."""

# --------------------------------------------------------------------------------------
# Name ambiguity
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class NameCheck:
    """Result of :func:`name_check`.

    ``ok`` is True when the entity's own name, searched with the real tool, returns
    that entity first **and** no other returned entity has the same name
    (case-insensitively). ``reason`` is one of ``ok``, ``blank``, ``not_found``,
    ``not_first``, ``shared_name``.
    """

    ok: bool
    reason: str
    rank: int | None = None


def name_check(search: Callable[[str], list[Entity]], entity: Entity) -> NameCheck:
    """Is ``entity.name`` an unambiguous way to name ``entity`` in a question?

    The rule (from the design doc): search the exact name the question uses with the
    real ``search_entities`` tool (default cap, 20 results). The name passes if

    1. the intended entity is the **first** result, and
    2. no other result has a name equal to it, ignoring case.

    Rule 2 matters even when rule 1 holds: the store ranks exact matches first and
    breaks ties by name and then ID, so a second node with the same name can land
    second only because of its ID. A model can't tell the two apart from the name.

    Args:
        search: ``text -> entities`` in the tool's order (``Prober.search``).
        entity: the entity the question means.
    """
    name = entity.name.strip()
    if not name:
        return NameCheck(False, "blank")
    hits = search(name)
    ids = [h.id for h in hits]
    if entity.id not in ids:
        return NameCheck(False, "not_found")
    rank = ids.index(entity.id)
    if rank != 0:
        return NameCheck(False, "not_first", rank)
    key = name.casefold()
    if any(h.id != entity.id and h.name.strip().casefold() == key for h in hits):
        return NameCheck(False, "shared_name", rank)
    return NameCheck(True, "ok", rank)


def has_fallback(entity_ids: Iterable[str]) -> bool:
    """True if any ID is a ``primekg:`` collision fallback."""
    return any(i.startswith(FALLBACK_PREFIX) for i in entity_ids)


# --------------------------------------------------------------------------------------
# Tool pages: what one call returns
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Page:
    """First page of ``get_neighbors(entity, relation)`` as the tool returns it."""

    edges: tuple[EdgeEvidence, ...]
    truncated: bool

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(e.id for e in self.edges)


class Prober:
    """Calls the real tool bodies (no budget, cached) to see what a model would see.

    Wraps a ``KGToolset`` built over the store with the run's tool config, and calls
    its private ``_neighbors`` and ``_search`` bodies, so the result cap, the
    payload-size cap and the result order are exactly those of a run. Pagination is
    whatever the config says (off by default): the page is always the first one.
    """

    def __init__(self, toolset) -> None:  # KGToolset; untyped to keep imports light
        self.ts = toolset
        self._pages: dict[tuple[str, str | None], Page] = {}
        self._search: dict[str, list[Entity]] = {}

    @property
    def max_calls(self) -> int:
        return self.ts.config.max_calls

    def page(self, entity_id: str, relation: str | None) -> Page:
        key = (entity_id, relation)
        if key not in self._pages:
            # The tool answers an unknown entity or relation with an empty,
            # untruncated payload and a note. That would read as "complete page,
            # nothing there" and wrongly rule an absence out, so refuse instead.
            if self.ts.store.entity(entity_id) is None:
                raise ValueError(f"unknown entity {entity_id!r}")
            if relation is not None and relation not in self.ts.relations:
                raise ValueError(f"unknown relation {relation!r}")
            out = self.ts._neighbors(entity_id, relation, 0, paged=False)
            p = ToolResultPayload.loads(out)
            self._pages[key] = Page(tuple(p.evidence), p.truncated)
        return self._pages[key]

    def search(self, text: str) -> list[Entity]:
        if text not in self._search:
            out = self.ts._search(text)
            self._search[text] = list(ToolResultPayload.loads(out).entities)
        return self._search[text]


# --------------------------------------------------------------------------------------
# Routes: which calls retrieve the gold and expose the absences
# --------------------------------------------------------------------------------------

Probe = tuple[str, str | None]
"""One ``get_neighbors`` call: ``(entity_id, relation or None)``."""


@dataclass(frozen=True)
class Show:
    """Requirement: some call in the route returns edge ``edge_id`` (between ``u`` and
    ``v`` under ``relation``)."""

    edge_id: str
    u: str
    v: str
    relation: str


@dataclass(frozen=True)
class RuleOut:
    """Requirement: an absent fact ``(u, relation, v)`` is visibly absent: some call
    on ``u`` or ``v`` filtered to ``relation`` returns a complete (untruncated) page,
    so the edge would have been on it if it existed."""

    u: str
    relation: str
    v: str


@dataclass(frozen=True)
class Complete:
    """Requirement: the full list of ``entity``'s ``relation`` edges is visible in one
    complete page. List questions need it, so the model can know it has every answer."""

    entity: str
    relation: str


Requirement = Show | RuleOut | Complete


def satisfiers(req: Requirement, prober: Prober) -> list[Probe]:
    """The single calls that would meet ``req``, in a fixed order (filtered first)."""
    out: list[Probe] = []
    if isinstance(req, Show):
        for ent in (req.u, req.v):
            for rel in (req.relation, None):
                if req.edge_id in prober.page(ent, rel).ids:
                    out.append((ent, rel))
    elif isinstance(req, RuleOut):
        for ent in (req.u, req.v):
            for rel in (req.relation, None):
                p = prober.page(ent, rel)
                # An unfiltered page rules out only if it is complete.
                if not p.truncated:
                    out.append((ent, rel))
    else:
        for rel in (req.relation, None):
            if not prober.page(req.entity, rel).truncated:
                out.append((req.entity, rel))
    # Filtered calls before unfiltered ones, then by entity; dedupe keeping order.
    out.sort(key=lambda pr: (pr[1] is None, pr[0], pr[1] or ""))
    return list(dict.fromkeys(out))


@dataclass(frozen=True)
class Route:
    """A plan of tool calls that meets every requirement within the budget.

    ``searches`` are the names searched (one ``search_entities`` call each);
    ``probes`` are the ``get_neighbors`` calls. ``calls`` is their total, which must
    not exceed the run's ``max_calls``.
    """

    searches: tuple[str, ...]
    probes: tuple[Probe, ...]

    @property
    def calls(self) -> int:
        return len(self.searches) + len(self.probes)

    def as_notes(self) -> list[dict]:
        """The route in the JSON form an item's ``notes`` carries (IDs only)."""
        return [{"tool": "search_entities", "entity": s} for s in self.searches] + [
            {"tool": "get_neighbors", "entity_id": e, "relation": r} for e, r in self.probes
        ]


def plan_route(
    prober: Prober,
    named: Sequence[str],
    reqs: Sequence[Requirement],
    allowed: Callable[[str], bool] | None = None,
    max_calls: int | None = None,
) -> Route | None:
    """The cheapest route that meets every requirement, or None if none fits.

    Each named entity costs one search (the question names it; the model has to find
    its ID). Probes are chosen as a minimum set cover over :func:`satisfiers`, tried
    smallest first and in a fixed order, so the result is deterministic.

    Args:
        prober: tool pages.
        named: entity IDs the question names (each needs one search).
        reqs: what the route must achieve.
        allowed: which entities a probe may start from. The default allows any;
            a caller can restrict probes to entities a model could know at that point
            (named ones, or ones shown on an earlier page).
        max_calls: the budget; defaults to the toolset's ``max_calls``.

    Returns:
        A :class:`Route`, or None if some requirement has no satisfier or no cover
        fits in the budget.
    """
    budget = prober.max_calls if max_calls is None else max_calls
    searches = tuple(dict.fromkeys(named))
    room = budget - len(searches)
    if room < 0:
        return None
    options: list[list[Probe]] = []
    for r in reqs:
        sats = [p for p in satisfiers(r, prober) if allowed is None or allowed(p[0])]
        if not sats:
            return None
        options.append(sats)
    if not options:
        return Route(searches, ())
    candidates = list(dict.fromkeys(p for sats in options for p in sats))
    sets = [frozenset(s) for s in options]
    for k in range(1, min(room, len(candidates)) + 1):
        for combo in itertools.combinations(candidates, k):
            chosen = set(combo)
            if all(s & chosen for s in sets):
                return Route(searches, combo)
    return None


# --------------------------------------------------------------------------------------
# Full-graph absence
# --------------------------------------------------------------------------------------


class FullGraph:
    """Absence checks against the **full** graph, not the store's subgraph.

    Backed by a duckdb connection with the measurement code's tables (``load.py``):

    - ``adj(a, b, eid, rel)``: every non-loop edge once from each end, with node
      IDs as PrimeKG ``x_index`` strings and relation labels as in the store
      (``drug_protein/<display>`` qualified);
    - ``pk_hier_first(rel, parent, child)``: the first row in file order of each
      parent-child pair. Its ``parent``/``child`` columns are really that row's
      ``x``/``y``; ``child_end`` says which end is the child per relation (the
      store builder's ``HIERARCHY_CHILD_END``).

    Store entity IDs are mapped to graph node IDs with ``index_of`` (from the store's
    ``entity_sources.jsonl``). Queries are batched: callers pass every pair they need
    and get back the absent ones.
    """

    def __init__(self, con, index_of: dict[str, str], child_end: dict[str, str]) -> None:
        self.con = con
        self.index_of = index_of
        self.child_end = child_end

    def _ix(self, entity_id: str) -> str:
        try:
            return self.index_of[entity_id]
        except KeyError:
            raise KeyError(f"no graph node index for entity {entity_id!r}") from None

    def absent_pairs(self, pairs: Iterable[tuple[str, str]]) -> set[tuple[str, str]]:
        """Pairs (store IDs) with **no edge under any relation**, in either order."""
        pairs = list(dict.fromkeys(pairs))
        if not pairs:
            return set()
        rows = [(u, v, self._ix(u), self._ix(v)) for u, v in pairs]
        self.con.execute("CREATE OR REPLACE TEMP TABLE _q(u VARCHAR, v VARCHAR, a VARCHAR, b VARCHAR)")
        self.con.executemany("INSERT INTO _q VALUES (?, ?, ?, ?)", rows)
        present = self.con.execute(
            "SELECT DISTINCT q.u, q.v FROM _q q JOIN adj x ON x.a = q.a AND x.b = q.b"
        ).fetchall()
        return set(pairs) - {(u, v) for u, v in present}

    def present_triples(
        self, triples: Iterable[tuple[str, str, str, bool]]
    ) -> set[tuple[str, str, str, bool]]:
        """Triples ``(subject, relation, object, directed)`` the full graph states.

        Undirected: an edge of that relation between the two nodes, either order.
        Directed (hierarchy relations): an edge of that relation with ``subject`` as
        the child and ``object`` as the parent, by the relation's file-order
        convention. The reverse orientation does not count: it is another fact.
        """
        triples = list(dict.fromkeys(triples))
        if not triples:
            return set()
        und = [t for t in triples if not t[3]]
        dirs = [t for t in triples if t[3]]
        out: set[tuple[str, str, str, bool]] = set()
        if und:
            self.con.execute(
                "CREATE OR REPLACE TEMP TABLE _t(s VARCHAR, r VARCHAR, o VARCHAR, a VARCHAR, b VARCHAR)"
            )
            self.con.executemany(
                "INSERT INTO _t VALUES (?, ?, ?, ?, ?)",
                [(s, r, o, self._ix(s), self._ix(o)) for s, r, o, _ in und],
            )
            hits = self.con.execute(
                "SELECT DISTINCT t.s, t.r, t.o FROM _t t JOIN adj x "
                "ON x.a = t.a AND x.b = t.b AND x.rel = t.r"
            ).fetchall()
            out |= {(s, r, o, False) for s, r, o in hits}
        if dirs:
            rows = []
            for s, r, o, _ in dirs:
                if r not in self.child_end:
                    raise ValueError(f"relation {r!r} is flagged directed but has no hierarchy convention")
                # child = s, parent = o. In pk_hier_first, the child is column
                # `child` (the first row's y) when child_end is "y", else `parent`.
                x, y = (self._ix(o), self._ix(s)) if self.child_end[r] == "y" else (self._ix(s), self._ix(o))
                rows.append((s, r, o, x, y))
            self.con.execute(
                "CREATE OR REPLACE TEMP TABLE _d(s VARCHAR, r VARCHAR, o VARCHAR, x VARCHAR, y VARCHAR)"
            )
            self.con.executemany("INSERT INTO _d VALUES (?, ?, ?, ?, ?)", rows)
            hits = self.con.execute(
                "SELECT DISTINCT d.s, d.r, d.o FROM _d d JOIN pk_hier_first h "
                "ON h.rel = d.r AND h.parent = d.x AND h.child = d.y"
            ).fetchall()
            out |= {(s, r, o, True) for s, r, o in hits}
        return out

    def relation_degree(self, entity_ids: Iterable[str], relation: str) -> dict[str, int]:
        """Full-graph count of ``relation`` edges at each entity (0 if none)."""
        ids = list(dict.fromkeys(entity_ids))
        if not ids:
            return {}
        self.con.execute("CREATE OR REPLACE TEMP TABLE _e(e VARCHAR, a VARCHAR)")
        self.con.executemany("INSERT INTO _e VALUES (?, ?)", [(e, self._ix(e)) for e in ids])
        rows = self.con.execute(
            "SELECT e.e, count(x.b) FROM _e e LEFT JOIN adj x ON x.a = e.a AND x.rel = ? GROUP BY e.e",
            [relation],
        ).fetchall()
        return {e: n for e, n in rows}

    def shared_neighbors(
        self, pairs: Iterable[tuple[str, str]], rel_x: str, rel_y: str
    ) -> dict[tuple[str, str], int]:
        """For each pair (x, y): how many nodes m have ``x -rel_x- m`` and ``m -rel_y- y``
        in the full graph. 0 means no two-hop chain of that pattern exists.

        Direction is ignored, also for hierarchy relations. That over-counts chains,
        which only makes an "empty chain" check stricter (fewer pairs pass)."""
        pairs = list(dict.fromkeys(pairs))
        if not pairs:
            return {}
        self.con.execute("CREATE OR REPLACE TEMP TABLE _p(x VARCHAR, y VARCHAR, a VARCHAR, b VARCHAR)")
        self.con.executemany(
            "INSERT INTO _p VALUES (?, ?, ?, ?)", [(x, y, self._ix(x), self._ix(y)) for x, y in pairs]
        )
        rows = self.con.execute(
            """
            SELECT p.x, p.y, count(DISTINCT m1.b)
            FROM _p p
            LEFT JOIN adj m1 ON m1.a = p.a AND m1.rel = ?
            LEFT JOIN adj m2 ON m2.a = p.b AND m2.rel = ? AND m2.b = m1.b
            WHERE m2.b IS NOT NULL OR m1.b IS NULL
            GROUP BY p.x, p.y
            """,
            [rel_x, rel_y],
        ).fetchall()
        out = {pair: 0 for pair in pairs}
        for x, y, n in rows:
            out[(x, y)] = n
        return out


# --------------------------------------------------------------------------------------
# Flag consistency
# --------------------------------------------------------------------------------------


def gold_point_problems(gp, store) -> list[str]:
    """Problems with one gold point against the store (empty list = consistent).

    - every cited evidence ID exists in the store;
    - at least one cited edge states the gold point (``contracts.facts.supports``);
    - ``directed`` equals every cited edge's ``directed``;
    - when directed, the gold point's subject/object follow the edge's order
      (``supports`` already enforces this; reported separately for clarity).
    """
    from neuro_grounded_qa.contracts import supports

    problems: list[str] = []
    edges = []
    for eid in gp.evidence:
        edge = store.get(eid)
        if edge is None:
            problems.append("evidence_missing")
        else:
            edges.append(edge)
    if edges and not any(supports(gp, e) for e in edges):
        problems.append("not_stated_by_evidence")
    if any(e.directed != gp.directed for e in edges):
        problems.append("directed_mismatch")
    return problems


def relation_directedness(edges: Iterable[EdgeEvidence]) -> dict[str, bool]:
    """Relation label -> directed, from the store's edges.

    Raises ValueError if one label has both directed and undirected edges (the store
    builder never writes that; a mixed label would make the flag ambiguous).
    """
    out: dict[str, bool] = {}
    for e in edges:
        if out.setdefault(e.relation, e.directed) != e.directed:
            raise ValueError(f"relation {e.relation!r} has mixed directed flags in the store")
    return out


def triple_flag_problems(t, directed_by_relation: dict[str, bool]) -> list[str]:
    """Problems with a premise or gap triple's flag (empty list = consistent).

    The flag must equal the relation's directedness in the store. A relation the
    store doesn't carry is a problem too: the model couldn't retrieve anything about
    it, and nothing fixes its convention.
    """
    if t.relation not in directed_by_relation:
        return ["relation_not_in_store"]
    if directed_by_relation[t.relation] != t.directed:
        return ["directed_mismatch"]
    return []


# --------------------------------------------------------------------------------------
# Split assignment
# --------------------------------------------------------------------------------------


def split_hash(key: str, seed: str) -> float:
    """A number in [0, 1) from SHA-256 of ``seed`` and ``key``: stable, uniform."""
    h = hashlib.sha256(f"{seed}\x1f{key}".encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64


def split_of(key: str, seed: str, heldout_share: float) -> str:
    """``"heldout"`` or ``"train"`` for a candidate, from its key alone.

    Depends only on the key, the seed and the share, never on the rest of the pool.
    So adding, removing or re-ranking other candidates (a new template, a stricter
    filter, a fix prompted by reviewing train) can't move a candidate across the
    boundary: a held-out candidate stays held out in every regeneration with the same
    seed. That is what lets train review change the generator without exposing the
    held-out split.
    """
    if not 0.0 < heldout_share < 1.0:
        raise ValueError(f"heldout_share must be in (0, 1), got {heldout_share}")
    return "heldout" if split_hash(key, seed) < heldout_share else "train"


def item_id(build_id: str, template_id: str, params: Sequence[str]) -> str:
    """Opaque, stable item ID: ``q-`` plus 12 hex digits of SHA-256.

    Built from the build, the template and its parameters, so the same candidate gets
    the same ID in every regeneration. It reveals neither the type nor the split,
    which keeps blind grading blind.
    """
    basis = "\x1f".join([build_id, template_id, *params])
    return "q-" + hashlib.sha256(basis.encode()).hexdigest()[:12]


# --------------------------------------------------------------------------------------
# Near-duplicates and leakage
# --------------------------------------------------------------------------------------

_TOKEN = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> frozenset[str]:
    """Lowercase alphanumeric tokens of ``text``."""
    return frozenset(_TOKEN.findall(text.casefold()))


def jaccard(a: frozenset, b: frozenset) -> float:
    """Jaccard similarity; 1.0 for two empty sets."""
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def near_duplicate(a_text: str, b_text: str, a_facts: frozenset, b_facts: frozenset, threshold: float = 0.8) -> bool:
    """Are two items near-duplicates?

    True if their question tokens overlap at ``threshold`` or more (Jaccard), or if
    they rest on the same set of facts (gold points, premise and gap triples, as
    fact keys from :func:`fact_key`) and that set is non-empty.
    """
    if a_facts and a_facts == b_facts:
        return True
    return jaccard(tokens(a_text), tokens(b_text)) >= threshold


def fact_key(subject_id: str, relation: str, object_id: str, directed: bool) -> tuple[str, str, str]:
    """A hashable key that equals for two triples exactly when ``same_fact`` holds
    (relation identical; ends in order if directed, else either order)."""
    if directed:
        return (relation, subject_id, object_id)
    a, b = sorted((subject_id, object_id))
    return (relation, a, b)


def answer_leaks(question: str, answer_names: Iterable[str]) -> bool:
    """True if any answer entity's name appears in the question text.

    "Answer entities" are the gold entities the question does **not** name (list
    members, bridge intermediates). Case-insensitive substring match, on word
    boundaries, so "APP" doesn't match "apply".
    """
    q = question.casefold()
    for name in answer_names:
        n = name.strip().casefold()
        if n and re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", q):
            return True
    return False


@dataclass
class Funnel:
    """Counts of candidates surviving each check, in order, for aggregate reports."""

    counts: dict[str, int] = field(default_factory=dict)

    def add(self, stage: str, n: int = 1) -> None:
        self.counts[stage] = self.counts.get(stage, 0) + n
