"""Measure the Alzheimer's-centered subgraph of PrimeKG and OptimusKG against the expansion test.

For each graph this script:

1. loads the graph into a duckdb file under ``NGQ_DATA_DIR/derived`` and
   builds the canonical tables (``load.py``, ``subgraph.py``);
2. lists every Alzheimer's seed candidate (name or synonym match, any node
   type) with its degree and relation counts;
3. builds two seed sets: the **proposed** set (the main Alzheimer disease node
   plus its subtypes in the graph's own disease hierarchy; rule in
   :func:`proposed_seed`) and the **main node alone**;
4. for each seed set and each subgraph definition (a, b, c at the 90th, 95th
   and 99th degree percentiles) computes every metric in the expansion test
   (C1–C8, R1–R4) and its pass/fail;
5. writes ``derived/results_<graph>.json``.

``report.py`` turns the JSON files into the markdown tables in
``docs/question_set_design.md``. Nothing here drafts a question: candidate
counts only.

Run (WSL, repo root)::

    NGQ_DATA_DIR=$HOME/data/ngq uv run --group data python scripts/data/measure.py [primekg|optimuskg]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
import load  # noqa: E402
import subgraph as sg  # noqa: E402
from paths import derived_dir, meta_dir, raw_dir  # noqa: E402
from subgraph import SubgraphSpec  # noqa: E402

# Expansion-test thresholds (docs/question_set_design.md). All proposed.
TARGETS = {"single": 270, "multi": 225, "partial": 135, "unans": 180, "false": 90}
CAP_SHARE = 0.4
C2_MIN_RELS, C2_MIN_FACTS = 4, 30
C4_MIN_PATTERNS, C4_MIN_PATHS = 3, 20
C5_TOP1, C5_TOP5 = 0.20, 0.50
C7_MIN_RELS, C7_MIN_PER_REL = 3, 20
C8_MIN_CONTRA = 30
TOOL_CAP = 20  # edges per tool result (design brief)
PERCENTILES = (90, 95, 99)
PRIMARY_PERCENTILE = 95

MAIN_NODE = {"primekg": "28780", "optimuskg": "MONDO_0004975"}
DISEASE_TYPE = {"primekg": "disease", "optimuskg": "DIS"}


# --------------------------------------------------------------------------
# Loading and seeds
# --------------------------------------------------------------------------


def open_graph(graph: str) -> tuple[duckdb.DuckDBPyConnection, dict]:
    """Open (building if needed) the duckdb file for a graph with canonical tables in place."""
    derived_dir().mkdir(parents=True, exist_ok=True)
    db = derived_dir() / f"{graph}.duckdb"
    checks_path = derived_dir() / f"{graph}_load_checks.json"
    fresh = not db.exists()
    con = duckdb.connect(str(db))
    con.execute("SET memory_limit='20GB'")
    con.execute(f"SET temp_directory='{derived_dir() / 'tmp'}'")
    if fresh or not checks_path.exists():
        loader = load.load_primekg if graph == "primekg" else load.load_optimuskg
        checks = loader(con, raw_dir(graph))
        checks.update(sg.canonicalize_edges(con))
        sg.build_adjacency(con)
        sg.compute_degree(con)
        checks_path.write_text(json.dumps(checks, indent=1))
    return con, json.loads(checks_path.read_text())


def seed_candidates(con, graph: str) -> list[dict]:
    """Every node whose name (or, in OptimusKG, a synonym) mentions Alzheimer, with degree and relations."""
    if graph == "optimuskg":
        match = f"""
            SELECT id FROM read_parquet('{raw_dir(graph) / "nodes.parquet"}')
            WHERE properties->>'$.name' ILIKE '%alzheimer%'
               OR CAST(properties->'$.exact_synonyms' AS VARCHAR) ILIKE '%alzheimer%'
               OR CAST(properties->'$.related_synonyms' AS VARCHAR) ILIKE '%alzheimer%'
               OR CAST(properties->'$.narrow_synonyms' AS VARCHAR) ILIKE '%alzheimer%'
               OR CAST(properties->'$.broad_synonyms' AS VARCHAR) ILIKE '%alzheimer%'
        """
    else:
        match = "SELECT id FROM nodes WHERE name ILIKE '%alzheimer%'"
    rows = con.execute(
        f"""
        SELECT n.id, n.name, n.type, n.source, n.source_id, d.degree
        FROM nodes n JOIN deg d USING (id)
        WHERE n.id IN ({match})
        ORDER BY n.type, d.degree DESC, n.name
        """
    ).fetchall()
    out = []
    for id_, name, typ, source, source_id, degree in rows:
        rels = con.execute(
            "SELECT rel, count(*) FROM adj WHERE a = ? GROUP BY rel ORDER BY 2 DESC", [id_]
        ).fetchall()
        out.append(
            {
                "id": id_, "name": name, "type": typ, "source": source,
                "source_id": source_id, "degree": degree,
                "relations": {r: n for r, n in rels},
            }
        )
    return out


def proposed_seed(con, graph: str) -> tuple[list[str], str]:
    """The proposed seed set and the rule that produced it.

    Rule, both graphs: the main Alzheimer disease node plus its subtypes in the
    graph's own disease hierarchy.

    - OptimusKG: ``DIS-DIS/PARENT`` edges are directed parent -> child, so the
      subtypes are all descendants of ``MONDO_0004975``.
    - PrimeKG: ``disease_disease`` is stored undirected, so parent and child
      can't be told apart from the edge. Subtypes are taken as the main node's
      ``disease_disease`` neighbors whose name contains "Alzheimer" (its other
      neighbors are parents such as "dementia" or "tauopathy").

    Not included (listed in the candidates for Kaitlin): trait and measurement
    nodes (EFO "biomarker measurement", "age of onset", "family history"), the
    HPO phenotype node, the Reactome pathway, and diseases that are not AD
    subtypes in the hierarchy (posterior cortical atrophy, Lewy body dementia,
    "dementia/parkinsonism with non-Alzheimer amyloid plaques").
    """
    main = MAIN_NODE[graph]
    if graph == "optimuskg":
        rows = con.execute(
            """
            WITH RECURSIVE d(id) AS (
                SELECT ?::VARCHAR
                UNION
                SELECT e.v FROM edges e JOIN d ON e.u = d.id WHERE e.rel = 'DIS-DIS/PARENT'
            )
            SELECT id FROM d
            """,
            [main],
        ).fetchall()
        rule = "MONDO_0004975 plus all its descendants over DIS-DIS/PARENT (parent -> child)"
    else:
        rows = con.execute(
            """
            SELECT ?::VARCHAR AS id
            UNION
            SELECT adj.b FROM adj JOIN nodes n ON n.id = adj.b
            WHERE adj.a = ? AND adj.rel = 'disease_disease' AND n.name ILIKE '%alzheimer%'
            """,
            [main, main],
        ).fetchall()
        rule = "28780 plus its disease_disease neighbors named '*Alzheimer*' (PrimeKG stores this relation undirected)"
    return sorted(r[0] for r in rows), rule


# --------------------------------------------------------------------------
# Metrics for one (seed, definition)
# --------------------------------------------------------------------------


def restricted_flags_sql(col: str) -> str:
    """SQL expressions flagging each restricted source in a VARCHAR[] column of source names."""
    parts = []
    for name, needles in load.RESTRICTED_SOURCES.items():
        cond = " OR ".join(f"lower(x) LIKE '%{n}%'" for n in needles)
        parts.append(f"len(list_filter({col}, lambda x: {cond})) > 0 AS \"{name}\"")
    return ", ".join(parts)


def licensing(con, eid_table: str) -> dict:
    """Share of edges in ``eid_table`` whose sources include each restricted source, and any."""
    names = list(load.RESTRICTED_SOURCES)
    row = con.execute(
        f"""
        WITH f AS (SELECT {restricted_flags_sql('e.sources')} FROM edges e JOIN {eid_table} t USING (eid))
        SELECT count(*), {", ".join(f'sum("{n}"::INT)' for n in names)},
               sum(({" OR ".join(f'"{n}"' for n in names)})::INT)
        FROM f
        """
    ).fetchone()
    total = row[0] or 0
    out = {"edges": total}
    for i, n in enumerate(names, start=1):
        out[n] = {"count": int(row[i] or 0), "share": (row[i] or 0) / total if total else 0.0}
    out["any_restricted"] = {"count": int(row[-1] or 0), "share": (row[-1] or 0) / total if total else 0.0}
    return out


def measure_one(con, graph: str, spec: SubgraphSpec) -> dict:
    """Compute every expansion-test metric for the current seed and one subgraph definition."""
    t0 = time.time()
    name = spec.name
    sg.build_subgraph(con, spec)
    q = lambda sql, p=None: con.execute(sql, p or []).fetchall()  # noqa: E731
    one = lambda sql, p=None: con.execute(sql, p or []).fetchone()[0]  # noqa: E731

    res: dict = {"definition": spec.kind, "max_degree": spec.max_degree}

    # ---- R4 size ----
    res["n_nodes"] = one(f"SELECT count(*) FROM subnodes_{name}")
    res["n_edges"] = one(f"SELECT count(*) FROM sub_{name}")
    res["nodes_by_type"] = dict(
        q(f"SELECT n.type, count(*) FROM subnodes_{name} s JOIN nodes n USING (id) GROUP BY 1 ORDER BY 2 DESC")
    )
    rel_counts = q(f"SELECT e.rel, count(*) FROM edges e JOIN sub_{name} USING (eid) GROUP BY 1 ORDER BY 2 DESC")
    src_counts = q(
        f"""
        SELECT rel, s, count(*) FROM (
            SELECT e.rel, unnest(e.sources) AS s FROM edges e JOIN sub_{name} USING (eid)
        ) GROUP BY rel, s ORDER BY rel, 3 DESC
        """
    )
    srcs: dict[str, dict[str, int]] = {}
    for r, s_, n in src_counts:
        srcs.setdefault(r, {})
        if len(srcs[r]) < 10:  # top 10 sources per relation
            srcs[r][s_] = n
    res["edges_by_relation"] = {r: {"edges": n, "top_sources": srcs.get(r, {})} for r, n in rel_counts}
    sa = sg._sub_adj(con, name)
    con.execute(
        f"CREATE OR REPLACE TABLE subdeg_{name} AS SELECT a AS id, count(DISTINCT b) AS degree FROM {sa} GROUP BY a"
    )
    qs = q(
        f"""SELECT min(degree), quantile_disc(degree, 0.5), quantile_disc(degree, 0.9),
                   quantile_disc(degree, 0.99), max(degree), avg(degree) FROM subdeg_{name}"""
    )[0]
    res["degree_in_subgraph"] = dict(zip(["min", "p50", "p90", "p99", "max", "mean"], [float(x) for x in qs]))
    res["top20_hubs"] = [
        {"id": i, "name": nm, "type": ty, "degree_in_subgraph": d, "degree_full": fd, "is_seed": bool(isseed)}
        for i, nm, ty, d, fd, isseed in q(
            f"""
            SELECT s.id, n.name, n.type, s.degree, g.degree, s.id IN (SELECT id FROM seed)
            FROM subdeg_{name} s JOIN nodes n USING (id) JOIN deg g USING (id)
            ORDER BY s.degree DESC, s.id LIMIT 20
            """
        )
    ]

    # ---- C1, C2 single-hop ----
    facts = sg.single_hop_facts(con, name)
    res["single_hop_facts"] = one(f"SELECT count(*) FROM {facts}")
    by_rel = dict(q(f"SELECT rel, count(*) FROM {facts} GROUP BY rel ORDER BY 2 DESC"))
    res["single_hop_by_relation"] = by_rel
    res["single_hop_relations_ge_30"] = sum(1 for n in by_rel.values() if n >= C2_MIN_FACTS)
    res["single_hop_balanced_capacity"] = sg.balanced_capacity(by_rel, TARGETS["single"], CAP_SHARE)

    # ---- C3, C4, C5 two-hop ----
    paths = sg.two_hop_paths(con, name)
    n_paths = one(f"SELECT count(*) FROM {paths}")
    n_end = one(f"SELECT count(DISTINCT t) FROM {paths}")
    res["two_hop_paths"] = n_paths
    res["two_hop_endpoints"] = n_end
    pat = dict((f"{a} | {b}", n) for a, b, n in q(f"SELECT r1, r2, count(*) FROM {paths} GROUP BY 1, 2 ORDER BY 3 DESC"))
    res["two_hop_patterns_total"] = len(pat)
    res["two_hop_patterns_top15"] = dict(list(pat.items())[:15])
    res["two_hop_patterns_ge_20"] = sum(1 for n in pat.values() if n >= C4_MIN_PATHS)
    res["two_hop_balanced_capacity"] = sg.balanced_capacity(pat, TARGETS["multi"], CAP_SHARE)
    inter = q(
        f"""
        SELECT p.m, n.name, n.type, count(*) AS paths, count(DISTINCT p.t) AS endpoints
        FROM {paths} p JOIN nodes n ON n.id = p.m
        GROUP BY 1, 2, 3 ORDER BY paths DESC, p.m LIMIT 20
        """
    )
    res["top_intermediates"] = [
        {"id": i, "name": nm, "type": ty, "paths": p,
         "path_share": p / n_paths if n_paths else 0.0,
         "endpoint_share": e / n_end if n_end else 0.0}
        for i, nm, ty, p, e in inter
    ]
    for k in (1, 5, 20):
        top_ids = [x["id"] for x in res["top_intermediates"][:k]]
        res[f"top{k}_path_share"] = sum(x["paths"] for x in res["top_intermediates"][:k]) / n_paths if n_paths else 0.0
        if top_ids:
            ends = one(f"SELECT count(DISTINCT t) FROM {paths} WHERE m IN (SELECT unnest(?::VARCHAR[]))", [top_ids])
            only = one(
                f"""SELECT count(*) FROM (SELECT t FROM {paths} GROUP BY t
                    HAVING bool_and(list_contains(?::VARCHAR[], m)))""",
                [top_ids],
            )
        else:
            ends = only = 0
        res[f"top{k}_endpoint_share"] = ends / n_end if n_end else 0.0
        res[f"top{k}_endpoints_only_via_share"] = only / n_end if n_end else 0.0

    # ---- R1 shared-neighbor pairs (disease endpoints) ----
    dis = DISEASE_TYPE[graph]
    rows = q(
        f"""
        WITH pairs AS (
            SELECT p.s, p.t, count(DISTINCT p.m) AS shared
            FROM {paths} p JOIN nodes n ON n.id = p.t
            WHERE n.type = ? GROUP BY p.s, p.t
        )
        SELECT count(*), sum((shared = 1)::INT), sum((shared BETWEEN 2 AND 5)::INT),
               sum((shared BETWEEN 6 AND 20)::INT), sum((shared > 20)::INT),
               sum((shared BETWEEN 1 AND {TOOL_CAP})::INT), count(DISTINCT t)
        FROM pairs
        """,
        [dis],
    )[0]
    res["shared_neighbor_pairs"] = {
        "pairs": rows[0], "shared_1": rows[1] or 0, "shared_2_5": rows[2] or 0,
        "shared_6_20": rows[3] or 0, "shared_gt_20": rows[4] or 0,
        "pairs_with_1_to_20_shared": rows[5] or 0, "distinct_other_diseases": rows[6],
    }

    # ---- C7 unanswerable, C6 partial ----
    un = sg.unanswerable_candidates(con, name)
    res["unanswerable_s_r_y"] = one(f"SELECT count(*) FROM {un}")
    res["unanswerable_r_y"] = one(f"SELECT count(*) FROM (SELECT DISTINCT r, y FROM {un})")
    un_by_rel = dict(q(f"SELECT r, count(DISTINCT y) FROM {un} GROUP BY r ORDER BY 2 DESC"))
    res["unanswerable_by_relation"] = un_by_rel
    res["unanswerable_relations_ge_20"] = sum(1 for n in un_by_rel.values() if n >= C7_MIN_PER_REL)
    res["partial_candidates"] = sg.partial_candidates(con, name)

    # ---- C8 false premise ----
    ct = sg.contradiction_edges(con, name, load.OPPOSING[graph])
    res["contradicted"] = one(f"SELECT count(*) FROM {ct}")
    res["contradicted_touching_seed"] = one(f"SELECT count(*) FROM {ct} WHERE touches_seed")
    res["contradicted_by_relation"] = dict(q(f"SELECT rel, count(*) FROM {ct} GROUP BY 1 ORDER BY 2 DESC"))
    res["absence_premises_spare"] = max(0, res["unanswerable_r_y"] - TARGETS["unans"])
    res["false_premise_total"] = res["contradicted"] + res["absence_premises_spare"]

    # ---- R2 tool-cap fit ----
    fan = dict(q(f"SELECT s || ' | ' || rel, count(*) FROM {facts} GROUP BY s, rel ORDER BY 2 DESC"))
    res["seed_fanout"] = fan
    res["facts_in_seed_groups_le_cap"] = one(
        f"""SELECT count(*) FROM {facts} f JOIN (SELECT s, rel, count(*) c FROM {facts} GROUP BY 1, 2) g
            USING (s, rel) WHERE g.c <= {TOOL_CAP}"""
    )
    res["facts_reachable_in_one_page_either_side"] = one(
        f"""
        WITH so AS (SELECT a, rel, count(*) c FROM {sa} GROUP BY a, rel)
        SELECT count(*) FROM {facts} f
        LEFT JOIN so x ON x.a = f.s AND x.rel = f.rel
        LEFT JOIN so y ON y.a = f.o AND y.rel = f.rel
        WHERE coalesce(x.c, 0) <= {TOOL_CAP} OR coalesce(y.c, 0) <= {TOOL_CAP}
        """
    )

    # ---- R3 licensing ----
    con.execute(f"CREATE OR REPLACE TEMP TABLE lic_facts AS SELECT eid FROM {facts}")
    con.execute(
        f"CREATE OR REPLACE TEMP TABLE lic_paths AS SELECT eid1 AS eid FROM {paths} UNION SELECT eid2 FROM {paths}"
    )
    res["licensing"] = {
        "single_hop_facts": licensing(con, "lic_facts"),
        "edges_on_two_hop_paths": licensing(con, "lic_paths"),
        "all_subgraph_edges": licensing(con, f"sub_{name}"),
    }

    res["pass"] = evaluate(res)
    res["seconds"] = round(time.time() - t0, 1)
    # Drop the big per-definition tables; the JSON keeps what the report needs.
    for t in (paths, un, sa, ct, f"subdeg_{name}"):
        con.execute(f"DROP TABLE IF EXISTS {t}")
    return res


def evaluate(r: dict) -> dict:
    """Pass/fail per gating criterion (C1–C8), from the proposed thresholds above."""
    c = {
        "C1": r["single_hop_facts"] >= TARGETS["single"],
        "C2": r["single_hop_relations_ge_30"] >= C2_MIN_RELS
        and r["single_hop_balanced_capacity"] >= TARGETS["single"],
        "C3": r["two_hop_paths"] >= TARGETS["multi"] and r["two_hop_endpoints"] >= TARGETS["multi"],
        "C4": r["two_hop_patterns_ge_20"] >= C4_MIN_PATTERNS
        and r["two_hop_balanced_capacity"] >= TARGETS["multi"],
        "C5": r["two_hop_paths"] > 0 and r["top1_path_share"] <= C5_TOP1 and r["top5_path_share"] <= C5_TOP5,
        "C6": r["partial_candidates"] >= TARGETS["partial"],
        "C7": r["unanswerable_r_y"] >= TARGETS["unans"] and r["unanswerable_relations_ge_20"] >= C7_MIN_RELS,
        "C8": r["false_premise_total"] >= TARGETS["false"] and r["contradicted"] >= C8_MIN_CONTRA,
    }
    c["all"] = all(c.values())
    return c


def specs(con) -> list[SubgraphSpec]:
    """Definitions (a), (b) and (c) at each percentile."""
    out = [SubgraphSpec("a", "a"), SubgraphSpec("b", "b")]
    for p in PERCENTILES:
        out.append(SubgraphSpec(f"c{p}", "c", max_degree=sg.degree_threshold(con, p)))
    return out


def graph_summary(con) -> dict:
    """Whole-graph facts: sizes, relation list, duplicate pair counts, degree percentiles."""
    return {
        "nodes": con.execute("SELECT count(*) FROM nodes").fetchone()[0],
        "edges": con.execute("SELECT count(*) FROM edges").fetchone()[0],
        "relations": dict(con.execute("SELECT rel, count(*) FROM edges GROUP BY 1 ORDER BY 2 DESC").fetchall()),
        "pairs_with_more_than_one_relation": con.execute(
            "SELECT count(*) FROM (SELECT least(u,v), greatest(u,v) FROM edges GROUP BY 1, 2 HAVING count(*) > 1)"
        ).fetchone()[0],
        "degree_percentiles": {p: sg.degree_threshold(con, p) for p in (50, 90, 95, 99)},
        "licensing_full_graph": licensing(con, "edges"),
    }


def main(graphs: list[str]) -> None:
    manifest = json.loads((meta_dir() / "manifest.json").read_text())
    for graph in graphs:
        t0 = time.time()
        con, checks = open_graph(graph)
        out: dict = {
            "graph": graph,
            "dataset": {k: v for k, v in manifest["datasets"][graph].items()},
            "accessed": manifest["accessed"],
            "load_checks": checks,
            "summary": graph_summary(con),
            "seed_candidates": seed_candidates(con, graph),
            "seed_sets": {},
        }
        proposed, rule = proposed_seed(con, graph)
        seeds = {"proposed": (proposed, rule), "main_only": ([MAIN_NODE[graph]], "the main Alzheimer disease node alone")}
        for label, (ids, why) in seeds.items():
            sg.set_seed(con, ids)
            entry = {"ids": ids, "rule": why, "n1_size": con.execute("SELECT count(*) FROM n1").fetchone()[0], "definitions": {}}
            for spec in specs(con):
                print(f"[{graph}] seed={label} def={spec.name} max_degree={spec.max_degree}", flush=True)
                entry["definitions"][spec.name] = measure_one(con, graph, spec)
                print(f"    pass={entry['definitions'][spec.name]['pass']} ({entry['definitions'][spec.name]['seconds']}s)", flush=True)
            out["seed_sets"][label] = entry
        (derived_dir() / f"results_{graph}.json").write_text(json.dumps(out, indent=1, default=str))
        print(f"[{graph}] done in {time.time() - t0:.0f}s -> {derived_dir() / f'results_{graph}.json'}")
        con.close()


if __name__ == "__main__":
    main(sys.argv[1:] or ["primekg", "optimuskg"])
