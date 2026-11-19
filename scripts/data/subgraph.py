"""Deterministic subgraph logic for the expansion test, written as SQL over duckdb.

Everything here works on a duckdb connection that holds two canonical tables,
built by ``load.py`` for the real graphs and by the tests for the synthetic
fixture:

``nodes(id VARCHAR, type VARCHAR, name VARCHAR, source VARCHAR)``
    One row per node.
``raw_edges(u VARCHAR, v VARCHAR, rel VARCHAR, directed BOOLEAN, sources VARCHAR[], score DOUBLE)``
    Edges as the source file gives them (possibly duplicated, possibly stored
    in both directions). ``score`` is NULL where the source gives none.

The functions then build, in order:

1. ``edges``: canonical, de-duplicated facts with an integer ``eid``
   (:func:`canonicalize_edges`).
2. ``adj``: both directions of every edge, for direction-agnostic traversal
   (:func:`build_adjacency`), and ``deg``: distinct-neighbor degree
   (:func:`compute_degree`).
3. ``seed`` and ``n1``: the seed set and its 1-hop node set (:func:`set_seed`).
4. A subgraph, as a table of edge IDs (:func:`build_subgraph`), for the three
   definitions in ``docs/question_set_design.md``: (a) induced on N1,
   (b) every edge with an endpoint in N1, (c) as (b) but expanding only
   through 1-hop nodes at or below a degree cap.
5. Candidate counts per question type (the ``count_*`` / ``*_candidates``
   functions). Counts only: nothing here drafts a question.
6. Reported-only extras: per-disease contributions for a multi-disease seed
   (:func:`seed_group_contributions`), a score-filtered view of the graph
   (:func:`apply_score_filter`), and counts of facts whose reversed reading
   is a different fact (:func:`direction_inversions`).

Absence ("no edge between u and v") is always checked against the **full**
graph's ``adj``, never the subgraph, so a pair is never called unanswerable
just because the subgraph cut its edge out.

Why SQL rather than networkx: PrimeKG has ~4M edges and OptimusKG ~22M; the
two-hop path counts join millions of rows. duckdb does that out of core in
seconds, and the same SQL runs unchanged on the 15-edge test fixture.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

Con = duckdb.DuckDBPyConnection


# --------------------------------------------------------------------------
# 1–2. Canonical edges, adjacency, degree
# --------------------------------------------------------------------------


def canonicalize_edges(con: Con) -> dict:
    """Build ``edges`` from ``raw_edges``: one row per distinct fact.

    An undirected edge is keyed as the unordered pair (least, greatest) per
    relation; a directed edge as the ordered pair. Rows that collapse onto the
    same key are one fact, and their ``sources`` lists are unioned. Self-loops
    are kept as facts but never traversed (see :func:`build_adjacency`).

    ``score`` is the highest score among the collapsed rows (NULL if none has
    one).

    Returns counts for the report: raw rows, canonical edges, and how many raw
    rows were duplicates or reverse copies.
    """
    # Callers that build raw_edges by hand may leave out the score column.
    has_score = any(
        c[0] == "score" for c in con.execute("DESCRIBE raw_edges").fetchall()
    )
    score = "score" if has_score else "NULL::DOUBLE AS score"
    con.execute(
        f"""
        CREATE OR REPLACE TABLE edges AS
        WITH keyed AS (
            SELECT
                CASE WHEN directed THEN u ELSE least(u, v) END AS u,
                CASE WHEN directed THEN v ELSE greatest(u, v) END AS v,
                rel, directed, sources, {score}
            FROM raw_edges
        )
        SELECT
            row_number() OVER (ORDER BY rel, u, v, directed) AS eid,
            u, v, rel, directed,
            list_sort(list_distinct(flatten(list(coalesce(sources, []::VARCHAR[]))))) AS sources,
            max(score) AS score
        FROM keyed
        GROUP BY u, v, rel, directed
        """
    )
    raw = con.execute("SELECT count(*) FROM raw_edges").fetchone()[0]
    canon = con.execute("SELECT count(*) FROM edges").fetchone()[0]
    return {"raw_rows": raw, "canonical_edges": canon, "rows_collapsed": raw - canon}


def build_adjacency(con: Con) -> None:
    """Build ``adj(a, b, eid, rel)``: each non-loop edge once from each end.

    Traversal ignores direction: a directed edge ``u -> v`` still makes ``v`` a
    neighbor of ``u`` and ``u`` a neighbor of ``v``, because a question can be
    asked from either end ("what does drug X target" / "which drugs target
    gene Y").
    """
    con.execute(
        """
        CREATE OR REPLACE TABLE adj AS
        SELECT u AS a, v AS b, eid, rel FROM edges WHERE u <> v
        UNION ALL
        SELECT v AS a, u AS b, eid, rel FROM edges WHERE u <> v
        """
    )


def compute_degree(con: Con) -> None:
    """Build ``deg(id, degree)``: the number of distinct neighbors, over all relations.

    Distinct neighbors rather than edge count, so a pair linked by several
    relations (for example target and enzyme) counts once. Nodes with no edges
    get degree 0.
    """
    con.execute(
        """
        CREATE OR REPLACE TABLE deg AS
        SELECT n.id, coalesce(d.degree, 0) AS degree
        FROM nodes n
        LEFT JOIN (SELECT a AS id, count(DISTINCT b) AS degree FROM adj GROUP BY a) d
          ON d.id = n.id
        """
    )


def degree_threshold(con: Con, percentile: float) -> int:
    """Degree at the given percentile (0–100) of the full graph's degree distribution.

    Uses the discrete quantile, so the threshold is a degree some node really has.
    """
    return int(
        con.execute(
            "SELECT quantile_disc(degree, ?) FROM deg", [percentile / 100.0]
        ).fetchone()[0]
    )


# --------------------------------------------------------------------------
# 3–4. Seed, N1, subgraph definitions
# --------------------------------------------------------------------------


def set_seed(con: Con, seed_ids: list[str]) -> None:
    """Create ``seed(id)`` and ``n1(id)`` (the seed plus every node one edge away)."""
    missing = con.execute(
        "SELECT unnest(?::VARCHAR[]) AS id EXCEPT SELECT id FROM nodes", [seed_ids]
    ).fetchall()
    if missing:
        raise ValueError(f"seed ids not in the graph: {[m[0] for m in missing]}")
    con.execute("CREATE OR REPLACE TABLE seed AS SELECT DISTINCT unnest(?::VARCHAR[]) AS id", [seed_ids])
    con.execute(
        """
        CREATE OR REPLACE TABLE n1 AS
        SELECT id FROM seed
        UNION
        SELECT adj.b AS id FROM adj JOIN seed ON adj.a = seed.id
        """
    )


@dataclass(frozen=True)
class SubgraphSpec:
    """One subgraph definition.

    ``kind`` is ``"a"`` (1-hop, induced on N1), ``"b"`` (2-hop: every edge
    with an endpoint in N1) or ``"c"`` (2-hop, hub-pruned). For ``"c"``,
    ``max_degree`` is the cap: the second hop expands only through 1-hop
    nodes with degree <= ``max_degree``. Seed nodes are never pruned and
    edges among N1 nodes are always kept.
    """

    name: str
    kind: str
    max_degree: int | None = None


def build_subgraph(con: Con, spec: SubgraphSpec) -> str:
    """Create table ``sub_<name>(eid)`` holding the subgraph's edge IDs; return its name.

    Also creates ``subnodes_<name>(id)``: every endpoint of those edges plus the
    seed (so an isolated seed still counts as a node).
    """
    t = f"sub_{spec.name}"
    if spec.kind == "a":
        where = "e.u IN (SELECT id FROM n1) AND e.v IN (SELECT id FROM n1)"
    elif spec.kind == "b":
        where = "e.u IN (SELECT id FROM n1) OR e.v IN (SELECT id FROM n1)"
    elif spec.kind == "c":
        if spec.max_degree is None:
            raise ValueError("kind 'c' needs max_degree")
        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE expand AS
            SELECT id FROM seed
            UNION
            SELECT n1.id FROM n1 JOIN deg USING (id) WHERE deg.degree <= ?
            """,
            [spec.max_degree],
        )
        where = (
            "(e.u IN (SELECT id FROM n1) AND e.v IN (SELECT id FROM n1))"
            " OR e.u IN (SELECT id FROM expand) OR e.v IN (SELECT id FROM expand)"
        )
    else:
        raise ValueError(f"unknown subgraph kind {spec.kind!r}")
    con.execute(f"CREATE OR REPLACE TABLE {t} AS SELECT e.eid FROM edges e WHERE {where}")
    con.execute(
        f"""
        CREATE OR REPLACE TABLE subnodes_{spec.name} AS
        SELECT e.u AS id FROM edges e JOIN {t} USING (eid)
        UNION SELECT e.v FROM edges e JOIN {t} USING (eid)
        UNION SELECT id FROM seed
        """
    )
    return t


def _sub_adj(con: Con, name: str) -> str:
    """Create ``subadj_<name>``: the adjacency rows whose edge is in the subgraph."""
    t = f"subadj_{name}"
    con.execute(f"CREATE OR REPLACE TABLE {t} AS SELECT adj.* FROM adj JOIN sub_{name} USING (eid)")
    return t


# --------------------------------------------------------------------------
# 5. Candidate capacity per question type
# --------------------------------------------------------------------------


def single_hop_facts(con: Con, name: str) -> str:
    """Create ``facts_<name>``: subgraph edges with at least one endpoint in the seed set.

    Columns: eid, rel, s (the seed endpoint; if both ends are seeds, the u end),
    o (the other end), o_type.
    """
    t = f"facts_{name}"
    con.execute(
        f"""
        CREATE OR REPLACE TABLE {t} AS
        SELECT e.eid, e.rel,
               CASE WHEN e.u IN (SELECT id FROM seed) THEN e.u ELSE e.v END AS s,
               CASE WHEN e.u IN (SELECT id FROM seed) THEN e.v ELSE e.u END AS o
        FROM edges e JOIN sub_{name} USING (eid)
        WHERE e.u IN (SELECT id FROM seed) OR e.v IN (SELECT id FROM seed)
        """
    )
    con.execute(
        f"CREATE OR REPLACE TABLE {t} AS SELECT f.*, n.type AS o_type FROM {t} f JOIN nodes n ON n.id = f.o"
    )
    return t


def two_hop_paths(con: Con, name: str) -> str:
    """Create ``paths_<name>``: distinct chains s -r1- m -r2- t inside the subgraph.

    ``s`` is a seed node; ``m`` and ``t`` are not seeds; ``t != m``. Both edges
    must be in the subgraph. A path is identified by its edge pair (eid1, eid2).
    Columns: s, eid1, r1, m, eid2, r2, t.
    """
    sa = _sub_adj(con, name)
    t = f"paths_{name}"
    con.execute(
        f"""
        CREATE OR REPLACE TABLE {t} AS
        SELECT x.a AS s, x.eid AS eid1, x.rel AS r1, x.b AS m,
               y.eid AS eid2, y.rel AS r2, y.b AS t
        FROM {sa} x
        JOIN {sa} y ON y.a = x.b
        WHERE x.a IN (SELECT id FROM seed)
          AND x.b NOT IN (SELECT id FROM seed)
          AND y.b NOT IN (SELECT id FROM seed)
          AND y.b <> x.b
          AND y.eid <> x.eid
        """
    )
    return t


def unanswerable_candidates(con: Con, name: str, absence_adj: str = "adj") -> str:
    """Create ``unans_<name>(s, r, y)``: type-compatible seed–entity pairs with no edge at all.

    For each seed ``s`` and each relation ``r`` that ``s`` takes part in (in the
    full graph), the object types ``r`` links ``s`` to give the plausible types.
    Every subgraph node ``y`` of such a type, other than a seed, with **no edge
    to s under any relation in the full graph**, is a candidate: "does s have
    relation r with y?" can't be answered from the graph.

    ``absence_adj`` names the adjacency table absence is checked against. It is
    ``adj`` except under a score filter, where it is the unfiltered
    ``main.adj``: a pair with a low-scoring edge is left out of the filtered
    store but is still not called unanswerable.
    """
    t = f"unans_{name}"
    con.execute(
        f"""
        CREATE OR REPLACE TABLE {t} AS
        WITH shapes AS (
            SELECT DISTINCT adj.a AS s, adj.rel AS r, n.type AS y_type
            FROM adj JOIN seed ON adj.a = seed.id JOIN nodes n ON n.id = adj.b
        ),
        cand AS (
            SELECT sh.s, sh.r, sn.id AS y
            FROM shapes sh
            JOIN nodes n ON n.type = sh.y_type
            JOIN subnodes_{name} sn ON sn.id = n.id
            WHERE sn.id NOT IN (SELECT id FROM seed)
        )
        SELECT c.* FROM cand c
        ANTI JOIN {absence_adj} x ON x.a = c.s AND x.b = c.y
        """
    )
    return t


def partial_candidates(con: Con, name: str) -> int:
    """Count single-hop facts (s, r, o) that have an absent sibling.

    An absent sibling is a subgraph node o' of the same type as o with no edge
    to s in the full graph (taken from ``unans_<name>`` for the same s and r).
    Such a fact supports one part of a two-part question ("does s r o, and does
    s r o'?") and leaves the other unsupported. Only facts whose other end is
    not a seed count.
    """
    return con.execute(
        f"""
        SELECT count(*) FROM facts_{name} f
        WHERE f.o NOT IN (SELECT id FROM seed)
          AND EXISTS (
            SELECT 1 FROM unans_{name} u JOIN nodes n ON n.id = u.y
            WHERE u.s = f.s AND u.r = f.rel AND n.type = f.o_type
          )
        """
    ).fetchone()[0]


def contradiction_edges(
    con: Con, name: str, opposing: list[tuple[str, str]], absence_adj: str = "adj"
) -> str:
    """Create ``contra_<name>``: subgraph edges whose relation is one side of an opposing pair.

    ``opposing`` lists relation pairs that contradict each other (indication vs
    contraindication; phenotype present vs absent; expression present vs
    absent). An edge with relation ``r`` between u and v contradicts a premise
    that asserts the opposite relation between them. Edges are kept only if
    they have an endpoint in N1, and only if the pair does **not** also carry
    the opposite relation in the full graph (that pair would be ambiguous;
    ``absence_adj`` as in :func:`unanswerable_candidates`).
    Columns: eid, rel, opposite, u, v, touches_seed.
    """
    t = f"contra_{name}"
    pairs = [(a, b) for a, b in opposing] + [(b, a) for a, b in opposing]
    con.execute("CREATE OR REPLACE TEMP TABLE opp(rel VARCHAR, opposite VARCHAR)")
    if pairs:
        con.executemany("INSERT INTO opp VALUES (?, ?)", pairs)
    con.execute(
        f"""
        CREATE OR REPLACE TABLE {t} AS
        SELECT e.eid, e.rel, o.opposite, e.u, e.v,
               (e.u IN (SELECT id FROM seed) OR e.v IN (SELECT id FROM seed)) AS touches_seed
        FROM edges e
        JOIN sub_{name} USING (eid)
        JOIN opp o ON o.rel = e.rel
        WHERE (e.u IN (SELECT id FROM n1) OR e.v IN (SELECT id FROM n1))
          AND NOT EXISTS (
            SELECT 1 FROM {absence_adj} x WHERE x.a = e.u AND x.b = e.v AND x.rel = o.opposite
          )
        """
    )
    return t


def is_absent(con: Con, u: str, v: str, absence_adj: str = "adj") -> bool:
    """True if u and v share no edge under any relation in the full graph."""
    return (
        con.execute(f"SELECT count(*) FROM {absence_adj} WHERE a = ? AND b = ?", [u, v]).fetchone()[0]
        == 0
    )


def balanced_capacity(counts: dict[str, int], target: int, cap_share: float = 0.4) -> int:
    """Candidates still available if no single group may supply more than ``cap_share`` of ``target``.

    Σ_g min(n_g, cap_share × target). Used for C2 (groups = relations) and C4
    (groups = relation patterns): a pool that is large only because one
    relation is huge scores low here.
    """
    cap = cap_share * target
    return int(sum(min(n, cap) for n in counts.values()))


# --------------------------------------------------------------------------
# 6. Reported-only extras
# --------------------------------------------------------------------------


def seed_group_contributions(
    con: Con,
    facts: str,
    groups: dict[str, list[str]],
    target: int,
    cap_share: float = 0.4,
    min_facts: int = 30,
) -> dict:
    """What each disease in a multi-disease seed contributes to the single-hop pool (C1, C2).

    ``facts`` is a ``facts_<name>`` table from :func:`single_hop_facts` built
    for the **combined** seed; ``groups`` maps a disease label to its seed node
    IDs. Groups may overlap (a node may be in two diseases' seeds). A fact
    belongs to a group if either endpoint is one of the group's seed nodes, so
    a fact between two diseases' seeds belongs to both, and a fact on a shared
    seed node belongs to every group that holds the node. The combined pool
    still counts each fact once.

    Per group: its facts, facts by relation, distinct (partner, relation)
    (partners outside the group's own seed),
    facts no other group reaches, the C2 inputs on its own (relations with at
    least ``min_facts`` facts; balanced capacity), and the C2 inputs of the
    combined pool with the group left out. ``adds_*`` is combined minus
    leave-one-out: what the group adds to C2 that the others don't already
    supply. Also returns the pairwise overlap between groups, by seed node and
    by (partner, relation), and the C2 inputs of each pair of groups taken
    together (``pairs_c2``).

    Reported only: it describes the pool and defines no new criterion.
    """
    con.execute("CREATE OR REPLACE TEMP TABLE seed_group(id VARCHAR, grp VARCHAR)")
    rows = [(i, g) for g, ids in groups.items() for i in ids]
    if rows:
        con.executemany("INSERT INTO seed_group VALUES (?, ?)", rows)
    # One row per (fact, group, partner): the partner is the end opposite the
    # group's seed node. A fact between two of the group's own seed nodes has
    # its partner inside the group; such facts count as facts but not as
    # (partner, relation) pairs, mirroring the "distinct (partner, relation)"
    # metric, which skips partners in the seed.
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE fact_group AS
        SELECT DISTINCT f.eid, f.rel, g.grp,
               CASE WHEN f.s = g.id THEN f.o ELSE f.s END AS partner,
               EXISTS (SELECT 1 FROM seed_group h
                       WHERE h.grp = g.grp AND h.id = CASE WHEN f.s = g.id THEN f.o ELSE f.s END
               ) AS partner_in_group
        FROM {facts} f JOIN seed_group g ON g.id IN (f.s, f.o)
        """
    )
    all_by_rel = dict(con.execute(f"SELECT rel, count(*) FROM {facts} GROUP BY rel").fetchall())

    def c2(by_rel: dict[str, int]) -> tuple[int, int]:
        return sum(1 for n in by_rel.values() if n >= min_facts), balanced_capacity(by_rel, target, cap_share)

    comb_rels, comb_bal = c2(all_by_rel)
    out: dict = {"combined": {"facts": sum(all_by_rel.values()), "relations_ge_min": comb_rels,
                              "balanced_capacity": comb_bal}, "groups": {}}
    for g in groups:
        by_rel = dict(con.execute(
            "SELECT rel, count(DISTINCT eid) FROM fact_group WHERE grp = ? GROUP BY rel ORDER BY 2 DESC", [g]
        ).fetchall())
        n_facts = con.execute("SELECT count(DISTINCT eid) FROM fact_group WHERE grp = ?", [g]).fetchone()[0]
        distinct_pr = con.execute(
            "SELECT count(*) FROM (SELECT DISTINCT partner, rel FROM fact_group WHERE grp = ? AND NOT partner_in_group)",
            [g],
        ).fetchone()[0]
        unique = con.execute(
            """SELECT count(DISTINCT eid) FROM fact_group a WHERE grp = ?
               AND NOT EXISTS (SELECT 1 FROM fact_group b WHERE b.eid = a.eid AND b.grp <> a.grp)""",
            [g],
        ).fetchone()[0]
        # Leave-one-out: facts reached by some other group (or by no group, which
        # can't happen when the groups cover the seed).
        loo_by_rel = dict(con.execute(
            f"""SELECT rel, count(*) FROM {facts} f
                WHERE f.eid NOT IN (SELECT eid FROM fact_group WHERE grp = ?)
                   OR f.eid IN (SELECT eid FROM fact_group WHERE grp <> ?)
                GROUP BY rel""",
            [g, g],
        ).fetchall())
        own_rels, own_bal = c2(by_rel)
        loo_rels, loo_bal = c2(loo_by_rel)
        out["groups"][g] = {
            "seed_nodes": len(groups[g]),
            "facts": n_facts,
            "facts_by_relation": by_rel,
            "distinct_partner_relation": distinct_pr,
            "facts_only_this_group": unique,
            "relations_ge_min": own_rels,
            "balanced_capacity": own_bal,
            "without_relations_ge_min": loo_rels,
            "without_balanced_capacity": loo_bal,
            "adds_relations_ge_min": comb_rels - loo_rels,
            "adds_balanced_capacity": comb_bal - loo_bal,
        }
    names = list(groups)
    overlap = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            shared_nodes = sorted(set(groups[a]) & set(groups[b]))
            shared_pr, union_pr = con.execute(
                """WITH x AS (SELECT DISTINCT partner, rel FROM fact_group WHERE grp = ? AND NOT partner_in_group),
                        y AS (SELECT DISTINCT partner, rel FROM fact_group WHERE grp = ? AND NOT partner_in_group)
                   SELECT (SELECT count(*) FROM (SELECT * FROM x INTERSECT SELECT * FROM y)),
                          (SELECT count(*) FROM (SELECT * FROM x UNION SELECT * FROM y))""",
                [a, b],
            ).fetchone()
            shared_facts = con.execute(
                """SELECT count(*) FROM (SELECT eid FROM fact_group WHERE grp = ?
                   INTERSECT SELECT eid FROM fact_group WHERE grp = ?)""",
                [a, b],
            ).fetchone()[0]
            overlap.append({
                "a": a, "b": b, "shared_seed_nodes": shared_nodes, "shared_facts": shared_facts,
                "shared_partner_relation": shared_pr,
                "jaccard_partner_relation": shared_pr / union_pr if union_pr else 0.0,
            })
    out["overlap"] = overlap
    # C2 inputs for each pair of groups' combined facts (for example
    # Alzheimer's plus one other disease). C2 only: no other criterion is
    # recomputed for a subset, so these say nothing about pass/fail overall.
    pairs = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            by_rel = dict(con.execute(
                "SELECT rel, count(DISTINCT eid) FROM fact_group WHERE grp IN (?, ?) GROUP BY rel", [a, b]
            ).fetchall())
            rels_ge, bal = c2(by_rel)
            pairs.append({"a": a, "b": b, "facts": sum(by_rel.values()),
                          "relations_ge_min": rels_ge, "balanced_capacity": bal})
    out["pairs_c2"] = pairs
    return out


def apply_score_filter(con: Con, min_score: float, scored_rels: list[str]) -> None:
    """Shadow ``edges`` and ``adj`` with a copy that drops low-scoring edges.

    Edges of a relation in ``scored_rels`` are kept only if ``score >=
    min_score`` (an edge of such a relation with no score is dropped). Edges of
    other relations are untouched. The filtered tables go in schema ``filt``,
    and the search path becomes ``filt, main``: every function in this module
    then reads the filtered ``edges``/``adj`` while ``nodes`` and ``deg`` (the
    full graph's degrees, so hub-pruning caps don't move) fall back to
    ``main``. New tables (seed, subgraphs) are created in ``filt``.

    Absence checks must then be pointed at the unfiltered adjacency with
    ``absence_adj="main.adj"``. Undo with :func:`clear_score_filter`.
    Reported-only sensitivity: the pass/fail table never uses it.
    """
    con.execute("SET search_path = 'main'")
    con.execute("DROP SCHEMA IF EXISTS filt CASCADE")
    con.execute("CREATE SCHEMA filt")
    con.execute(
        """
        CREATE TABLE filt.edges AS SELECT * FROM main.edges
        WHERE NOT list_contains(?::VARCHAR[], rel) OR coalesce(score >= ?, false)
        """,
        [scored_rels, min_score],
    )
    con.execute("CREATE TABLE filt.adj AS SELECT a.* FROM main.adj a SEMI JOIN filt.edges e ON e.eid = a.eid")
    con.execute("SET search_path = 'filt,main'")


def clear_score_filter(con: Con) -> None:
    """Drop the ``filt`` schema made by :func:`apply_score_filter` and restore the search path."""
    con.execute("SET search_path = 'main'")
    con.execute("DROP SCHEMA IF EXISTS filt CASCADE")


def same_type_relations(con: Con) -> dict[str, dict]:
    """Relations with at least one edge joining two nodes of the same type.

    Maps each such relation to its most common same-type node type, the
    number of its edges that join two nodes of one type, and its total edge
    count. A relation can be mixed: OptimusKG's ``DIS-DIS/PARENT`` also holds
    some phenotype -> disease edges. Only the same-type edges can be read in
    two different directions; the types fix the reading of the rest.
    """
    rows = con.execute(
        """
        WITH t AS (
            SELECT e.rel, nu.type AS tu, nv.type AS tv
            FROM edges e JOIN nodes nu ON nu.id = e.u JOIN nodes nv ON nv.id = e.v
        )
        SELECT rel, count(*) AS n, count(*) FILTER (WHERE tu = tv) AS same,
               mode(tu) FILTER (WHERE tu = tv) AS typ
        FROM t GROUP BY rel HAVING count(*) FILTER (WHERE tu = tv) > 0
        """
    ).fetchall()
    return {rel: {"type": typ, "same_type_edges": same, "edges": n} for rel, n, same, typ in rows}


def direction_inversions(con: Con, name: str, rels: list[str]) -> dict:
    """For directional same-type relations, count subgraph facts whose reversed reading is a different fact.

    ``rels`` are relations between nodes of one type whose meaning has a
    direction (parent-child, is_a). For such an edge between a and b,
    "(a, r, b)" and "(b, r, a)" are different claims, so a direction-insensitive
    support check would accept the reversed one. Per relation:

    - ``edges``: its edges in subgraph ``name``;
    - ``single_hop_facts``: those with an endpoint in the seed;
    - ``stored_directed``: how many the source stores with a direction;
    - ``reverse_also_present``: directed edges whose reverse is also an edge of
      the same relation in the full graph (the reversed claim is then true and
      no harm is done);
    - ``reversed_reading_differs``: edges where the reversed claim is a
      different, unsupported fact. For edges stored undirected (PrimeKG) every
      edge counts, since the file can't say which reading is true.

    Only edges whose two ends have the same type are counted (see
    :func:`same_type_relations`). Needs ``facts_<name>`` from
    :func:`single_hop_facts`.
    """
    out = {}
    for rel in rels:
        row = con.execute(
            f"""
            WITH s AS (
                SELECT e.* FROM edges e JOIN sub_{name} USING (eid)
                JOIN nodes nu ON nu.id = e.u JOIN nodes nv ON nv.id = e.v
                WHERE e.rel = ? AND nu.type = nv.type
            )
            SELECT count(*),
                   count(*) FILTER (WHERE eid IN (SELECT eid FROM facts_{name})),
                   count(*) FILTER (WHERE directed),
                   count(*) FILTER (WHERE directed AND EXISTS (
                       SELECT 1 FROM edges r WHERE r.rel = s.rel AND r.directed
                         AND r.u = s.v AND r.v = s.u)),
                   count(*) FILTER (WHERE eid IN (SELECT eid FROM facts_{name}) AND (NOT directed OR NOT EXISTS (
                       SELECT 1 FROM edges r WHERE r.rel = s.rel AND r.directed
                         AND r.u = s.v AND r.v = s.u)))
            FROM s
            """,
            [rel],
        ).fetchone()
        n, n_fact, n_dir, n_both, fact_differs = row
        out[rel] = {
            "edges": n,
            "single_hop_facts": n_fact,
            "stored_directed": n_dir,
            "reverse_also_present": n_both,
            "reversed_reading_differs": n - n_both,
            "single_hop_reversed_reading_differs": fact_differs,
        }
    return out
