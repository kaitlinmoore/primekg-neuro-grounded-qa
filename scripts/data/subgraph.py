"""Deterministic subgraph logic for the expansion test, written as SQL over duckdb.

Everything here works on a duckdb connection that holds two canonical tables,
built by ``load.py`` for the real graphs and by the tests for the synthetic
fixture:

``nodes(id VARCHAR, type VARCHAR, name VARCHAR, source VARCHAR)``
    One row per node.
``raw_edges(u VARCHAR, v VARCHAR, rel VARCHAR, directed BOOLEAN, sources VARCHAR[])``
    Edges as the source file gives them (possibly duplicated, possibly stored
    in both directions).

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

    Returns counts for the report: raw rows, canonical edges, and how many raw
    rows were duplicates or reverse copies.
    """
    con.execute(
        """
        CREATE OR REPLACE TABLE edges AS
        WITH keyed AS (
            SELECT
                CASE WHEN directed THEN u ELSE least(u, v) END AS u,
                CASE WHEN directed THEN v ELSE greatest(u, v) END AS v,
                rel, directed, sources
            FROM raw_edges
        )
        SELECT
            row_number() OVER (ORDER BY rel, u, v, directed) AS eid,
            u, v, rel, directed,
            list_sort(list_distinct(flatten(list(coalesce(sources, []::VARCHAR[]))))) AS sources
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


def unanswerable_candidates(con: Con, name: str) -> str:
    """Create ``unans_<name>(s, r, y)``: type-compatible seed–entity pairs with no edge at all.

    For each seed ``s`` and each relation ``r`` that ``s`` takes part in (in the
    full graph), the object types ``r`` links ``s`` to give the plausible types.
    Every subgraph node ``y`` of such a type, other than a seed, with **no edge
    to s under any relation in the full graph**, is a candidate: "does s have
    relation r with y?" can't be answered from the graph.
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
        ANTI JOIN adj ON adj.a = c.s AND adj.b = c.y
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


def contradiction_edges(con: Con, name: str, opposing: list[tuple[str, str]]) -> str:
    """Create ``contra_<name>``: subgraph edges whose relation is one side of an opposing pair.

    ``opposing`` lists relation pairs that contradict each other (indication vs
    contraindication; phenotype present vs absent; expression present vs
    absent). An edge with relation ``r`` between u and v contradicts a premise
    that asserts the opposite relation between them. Edges are kept only if
    they have an endpoint in N1, and only if the pair does **not** also carry
    the opposite relation in the full graph (that pair would be ambiguous).
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
            SELECT 1 FROM adj WHERE adj.a = e.u AND adj.b = e.v AND adj.rel = o.opposite
          )
        """
    )
    return t


def is_absent(con: Con, u: str, v: str) -> bool:
    """True if u and v share no edge under any relation in the full graph."""
    return (
        con.execute("SELECT count(*) FROM adj WHERE a = ? AND b = ?", [u, v]).fetchone()[0] == 0
    )


def balanced_capacity(counts: dict[str, int], target: int, cap_share: float = 0.4) -> int:
    """Candidates still available if no single group may supply more than ``cap_share`` of ``target``.

    Σ_g min(n_g, cap_share × target). Used for C2 (groups = relations) and C4
    (groups = relation patterns): a pool that is large only because one
    relation is huge scores low here.
    """
    cap = cap_share * target
    return int(sum(min(n, cap) for n in counts.values()))
