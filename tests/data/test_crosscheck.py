"""Triangulation: the SQL in subgraph.py against an independent pure-Python version.

Random synthetic graphs (fixed seeds, so the test is deterministic) are built
in memory, measured both ways, and compared: N1, the three subgraph edge sets,
two-hop path counts, unanswerable candidates and the partial count. The Python
side is written from the definitions in docs/question_set_design.md, not from
the SQL.
"""

import random
from collections import defaultdict

import duckdb
import pytest

import subgraph as sg
from subgraph import SubgraphSpec

TYPES = ["disease", "gene", "drug", "phenotype"]
RELS = {  # relation -> (type of one end, type of the other)
    "assoc": ("disease", "gene"),
    "ppi": ("gene", "gene"),
    "target": ("drug", "gene"),
    "indication": ("drug", "disease"),
    "pheno": ("disease", "phenotype"),
}


def random_graph(seed: int, n_nodes: int = 40, n_edges: int = 140):
    rnd = random.Random(seed)
    nodes = {f"n{i}": TYPES[i % len(TYPES)] for i in range(n_nodes)}
    by_type = defaultdict(list)
    for n, t in nodes.items():
        by_type[t].append(n)
    rows = []
    for _ in range(n_edges):
        rel = rnd.choice(list(RELS))
        a, b = RELS[rel]
        u, v = rnd.choice(by_type[a]), rnd.choice(by_type[b])
        if rnd.random() < 0.5:
            u, v = v, u  # stored in either order
        rows.append((u, v, rel))
        if rnd.random() < 0.2:
            rows.append((v, u, rel))  # a reverse copy, as PrimeKG stores
    return nodes, rows


def python_reference(nodes, rows, seed_ids, max_degree):
    """Direct implementation of the definitions, with plain sets and dicts."""
    edges = {(min(u, v), max(u, v), r) for u, v, r in rows}
    nbrs = defaultdict(set)
    inc = defaultdict(set)  # node -> canonical edges touching it (non-loop)
    for u, v, r in edges:
        if u != v:
            nbrs[u].add(v)
            nbrs[v].add(u)
            inc[u].add((u, v, r))
            inc[v].add((u, v, r))
    seed = set(seed_ids)
    n1 = seed | {b for s in seed for b in nbrs[s]}
    deg = {n: len(nbrs[n]) for n in nodes}
    sub = {
        "a": {e for e in edges if e[0] in n1 and e[1] in n1},
        "b": {e for e in edges if e[0] in n1 or e[1] in n1},
    }
    expand = seed | {m for m in n1 if deg[m] <= max_degree}
    sub["c"] = sub["a"] | {e for e in edges if e[0] in expand or e[1] in expand}

    out = {"n1": n1, "sub": sub, "paths": {}, "unans": {}, "partial": {}}
    for k, es in sub.items():
        sadj = defaultdict(list)
        for u, v, r in es:
            if u != v:
                sadj[u].append((v, (u, v, r)))
                sadj[v].append((u, (u, v, r)))
        n_paths = 0
        for s in seed:
            for m, e1 in sadj[s]:
                if m in seed:
                    continue
                for t, e2 in sadj[m]:
                    if t not in seed and t != m and e2 != e1:
                        n_paths += 1
        out["paths"][k] = n_paths

        subnodes = {x for e in es for x in e[:2]} | seed
        unans = set()
        for s in seed:
            shapes = {(r, nodes[o]) for (u, v, r) in inc[s] for o in [v if u == s else u]}
            for r, t in shapes:
                for y in subnodes:
                    if nodes[y] == t and y not in seed and y not in nbrs[s]:
                        unans.add((s, r, y))
        out["unans"][k] = unans

        partial = 0
        for u, v, r in es:
            if u in seed or v in seed:
                s, o = (u, v) if u in seed else (v, u)
                if o in seed:
                    continue
                if any(rr == r and nodes[y] == nodes[o] for (ss, rr, y) in unans if ss == s):
                    partial += 1
        out["partial"][k] = partial
    return out


@pytest.mark.parametrize("graph_seed", [1, 2, 3, 4, 5])
def test_sql_matches_python_reference(graph_seed):
    nodes, rows = random_graph(graph_seed)
    seed_ids = sorted(n for n, t in nodes.items() if t == "disease")[:2]
    con = duckdb.connect()
    con.execute("CREATE TABLE nodes(id VARCHAR, type VARCHAR, name VARCHAR, source VARCHAR, source_id VARCHAR)")
    con.executemany("INSERT INTO nodes VALUES (?, ?, ?, 'x', ?)", [(n, t, n, n) for n, t in nodes.items()])
    con.execute("CREATE TABLE raw_edges(u VARCHAR, v VARCHAR, rel VARCHAR, directed BOOLEAN, sources VARCHAR[])")
    con.executemany("INSERT INTO raw_edges VALUES (?, ?, ?, false, ['x'])", rows)
    sg.canonicalize_edges(con)
    sg.build_adjacency(con)
    sg.compute_degree(con)
    sg.set_seed(con, seed_ids)
    cap = sg.degree_threshold(con, 60)
    ref = python_reference(nodes, rows, seed_ids, cap)

    assert {r[0] for r in con.execute("SELECT id FROM n1").fetchall()} == ref["n1"]
    for k in ("a", "b", "c"):
        sg.build_subgraph(con, SubgraphSpec(k, k, max_degree=cap if k == "c" else None))
        got = set(con.execute(f"SELECT u, v, rel FROM edges JOIN sub_{k} USING (eid)").fetchall())
        assert got == ref["sub"][k], k
        sg.single_hop_facts(con, k)
        paths = sg.two_hop_paths(con, k)
        assert con.execute(f"SELECT count(*) FROM {paths}").fetchone()[0] == ref["paths"][k], k
        un = sg.unanswerable_candidates(con, k)
        assert set(con.execute(f"SELECT s, r, y FROM {un}").fetchall()) == ref["unans"][k], k
        assert sg.partial_candidates(con, k) == ref["partial"][k], k
    con.close()
