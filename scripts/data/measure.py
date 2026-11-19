"""Measure disease-centered subgraphs of PrimeKG and OptimusKG against the expansion test.

Two domains, both checked against the expansion test exactly as committed in
``docs/question_set_design.md`` (thresholds below, all still proposed):

- ``ad``: Alzheimer's disease alone (the first run of the test).
- ``neuro``: broader neurodegeneration, the set the test's decision rule names
  when the Alzheimer's-only subgraph fails: Alzheimer's disease, Parkinson
  disease, frontotemporal dementia, amyotrophic lateral sclerosis, Huntington
  disease and dementia with Lewy bodies, each with its subtypes under the same
  seed rule as Alzheimer's.

For each graph and domain this script:

1. loads the graph into a duckdb file under ``NGQ_DATA_DIR/derived`` and
   builds the canonical tables (``load.py``, ``subgraph.py``);
2. lists every seed candidate per disease (name or synonym match, plus the
   main node's neighbors in the disease hierarchy), with degree, relation
   counts, its role relative to the main node and, if excluded, why;
3. builds two seed sets: the **proposed** set (each disease's main node plus
   its subtypes, rule in :func:`proposed_seed`; for ``neuro`` the union over
   the six diseases) and the **main nodes alone**;
4. for each seed set and subgraph definition (a, b, c at the 90th, 95th and
   99th degree percentiles) computes every expansion-test metric (C1–C8,
   R1–R4) and its pass/fail, plus reported-only extras: the direction
   inversion counts and, for PrimeKG, the share of facts placed through the
   UMLS crosswalk;
5. for ``neuro``'s proposed seed, the per-disease contribution to the
   single-hop pool (reported only);
6. writes ``derived/results_<graph>.json`` (``ad``) or
   ``derived/results_<graph>_neuro.json``.

``--scores`` adds the OptimusKG score sensitivity (reported only, never in the
pass/fail table): the C1–C8 counts with Open Targets ``evidence_score`` cutoffs
of 0.1 and 0.5 on the scored relations, for both domains' proposed seeds,
written to ``derived/score_sensitivity_optimuskg.json``.

``report.py`` turns the JSON files into the markdown tables in
``docs/question_set_design.md``. Nothing here drafts a question: candidate
counts only.

Run (WSL, repo root; ``NGQ_DATA_DIR`` comes from ``.envrc``)::

    uv run python scripts/data/measure.py --domain all --scores primekg optimuskg
"""

from __future__ import annotations

import argparse
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

# Expansion-test thresholds (docs/question_set_design.md). All proposed. These
# are the committed values and must not change without a changelog entry.
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

# Reported-only score sensitivity (OptimusKG evidence_score cutoffs).
SCORE_CUTOFFS = (0.1, 0.5)

DISEASE_TYPE = {"primekg": "disease", "optimuskg": "DIS"}

# The six diseases of the decision rule. ``main`` is the node named for the
# disease in each graph. ``terms`` are the name/synonym search terms for the
# candidate list (case-insensitive substrings); they list candidates but do
# not choose the seed.
DISEASES: dict[str, dict] = {
    "AD": {"label": "Alzheimer's disease",
           "main": {"primekg": "28780", "optimuskg": "MONDO_0004975"},
           "terms": ["alzheimer"]},
    "PD": {"label": "Parkinson disease",
           "main": {"primekg": "28547", "optimuskg": "MONDO_0005180"},
           "terms": ["parkinson"]},
    "FTD": {"label": "frontotemporal dementia",
            "main": {"primekg": "35691", "optimuskg": "MONDO_0017276"},
            "terms": ["frontotemporal"]},
    "ALS": {"label": "amyotrophic lateral sclerosis",
            "main": {"primekg": "27248", "optimuskg": "MONDO_0004976"},
            "terms": ["amyotrophic lateral sclerosis"]},
    "HD": {"label": "Huntington disease",
           "main": {"primekg": "31245", "optimuskg": "MONDO_0007739"},
           "terms": ["huntington"]},
    # Neither graph has a node named "dementia with Lewy bodies"; both name it
    # "Lewy body dementia" (PrimeKG 29296 is MONDO 7488; OptimusKG uses
    # EFO_0006792).
    "DLB": {"label": "dementia with Lewy bodies",
            "main": {"primekg": "29296", "optimuskg": "EFO_0006792"},
            "terms": ["lewy"]},
}
DOMAINS = {"ad": ["AD"], "neuro": ["AD", "PD", "FTD", "ALS", "HD", "DLB"]}

# Nodes the PrimeKG name rule picks up that the rule's purpose (subtypes only)
# excludes. Each needs a reason; Kaitlin approves or reverses it.
PRIMEKG_SEED_EXCLUDE = {
    "38307": (
        "'Huntington disease and related disorders' contains the main node's name, "
        "but MONDO places it above Huntington disease (OptimusKG DIS-DIS/PARENT "
        "MONDO_0000167 -> MONDO_0007739), and kg.csv's file order also gives it as "
        "the parent. Degree 3."
    ),
}


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def open_graph(graph: str) -> tuple[duckdb.DuckDBPyConnection, dict]:
    """Open (building if needed) the duckdb file for a graph with canonical tables in place.

    Rebuilds if the file predates the ``score`` column (or, for PrimeKG, the
    ``pk_hier_first`` table). PrimeKG's relation sources are re-applied from
    ``load.PRIMEKG_RELATION_SOURCES`` on every open, so the mapping in code is
    always the one measured.
    """
    derived_dir().mkdir(parents=True, exist_ok=True)
    db = derived_dir() / f"{graph}.duckdb"
    checks_path = derived_dir() / f"{graph}_load_checks.json"
    con = duckdb.connect(str(db))
    con.execute("SET memory_limit='20GB'")
    con.execute(f"SET temp_directory='{derived_dir() / 'tmp'}'")
    tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()}
    stale = "edges" not in tables or not checks_path.exists()
    if not stale:
        cols = {r[0] for r in con.execute("DESCRIBE edges").fetchall()}
        stale = "score" not in cols or (graph == "primekg" and "pk_hier_first" not in tables)
    if stale:
        loader = load.load_primekg if graph == "primekg" else load.load_optimuskg
        checks = loader(con, raw_dir(graph))
        checks.update(sg.canonicalize_edges(con))
        sg.build_adjacency(con)
        sg.compute_degree(con)
        checks_path.write_text(json.dumps(checks, indent=1))
    if graph == "primekg":
        load.apply_primekg_sources(con)
    return con, json.loads(checks_path.read_text())


# --------------------------------------------------------------------------
# Seeds
# --------------------------------------------------------------------------


def _descendants(con, root: str) -> dict[str, int]:
    """OptimusKG: every descendant of ``root`` over DIS-DIS/PARENT (parent -> child), with depth."""
    rows = con.execute(
        """
        WITH RECURSIVE d(id, depth) AS (
            SELECT ?::VARCHAR, 0
            UNION
            SELECT e.v, d.depth + 1 FROM edges e JOIN d ON e.u = d.id WHERE e.rel = 'DIS-DIS/PARENT'
        )
        SELECT id, min(depth) FROM d GROUP BY id
        """,
        [root],
    ).fetchall()
    return dict(rows)


def _ancestors(con, root: str) -> set[str]:
    """OptimusKG: every ancestor of ``root`` over DIS-DIS/PARENT."""
    rows = con.execute(
        """
        WITH RECURSIVE d(id) AS (
            SELECT ?::VARCHAR
            UNION
            SELECT e.u FROM edges e JOIN d ON e.v = d.id WHERE e.rel = 'DIS-DIS/PARENT'
        )
        SELECT id FROM d
        """,
        [root],
    ).fetchall()
    return {r[0] for r in rows} - {root}


def proposed_seed(con, graph: str, disease: str) -> tuple[list[str], str, dict[str, str]]:
    """One disease's proposed seed set, the rule that produced it, and rule-matched nodes excluded.

    Rule, the same for every disease (written for Alzheimer's in the first run
    of the test): the main disease node plus its subtypes in the graph's own
    disease hierarchy.

    - OptimusKG: ``DIS-DIS/PARENT`` edges are directed parent -> child, so the
      subtypes are all descendants of the main node.
    - PrimeKG: ``disease_disease`` is stored undirected, so the edge alone
      can't tell parent from child. Subtypes are the main node's
      ``disease_disease`` neighbors whose name contains the main node's name
      (for Alzheimer's, "Alzheimer disease"; this selects the same four
      neighbors as the first run's '*Alzheimer*' filter). Nodes in
      ``PRIMEKG_SEED_EXCLUDE`` are then removed, with their reason returned.
    """
    main = DISEASES[disease]["main"][graph]
    if graph == "optimuskg":
        ids = sorted(_descendants(con, main))
        rule = f"{main} plus all its descendants over DIS-DIS/PARENT (parent -> child)"
        return ids, rule, {}
    main_name = con.execute("SELECT name FROM nodes WHERE id = ?", [main]).fetchone()[0]
    rows = con.execute(
        """
        SELECT DISTINCT adj.b FROM adj JOIN nodes n ON n.id = adj.b
        WHERE adj.a = ? AND adj.rel = 'disease_disease' AND n.name ILIKE '%' || ? || '%'
        """,
        [main, main_name],
    ).fetchall()
    matched = {r[0] for r in rows}
    excluded = {i: PRIMEKG_SEED_EXCLUDE[i] for i in matched if i in PRIMEKG_SEED_EXCLUDE}
    ids = sorted({main} | (matched - set(excluded)))
    rule = (
        f"{main} plus its disease_disease neighbors whose name contains '{main_name}'"
        + (f", minus {', '.join(sorted(excluded))} (see exclusions)" if excluded else "")
    )
    return ids, rule, excluded


def _grouped_members(graph: str) -> dict[str, str]:
    """PrimeKG: MONDO number -> member name, from kg_grouped_diseases.csv (empty for OptimusKG)."""
    if graph != "primekg":
        return {}
    f = raw_dir(graph) / "kg_grouped_diseases.csv"
    if not f.exists():
        return {}
    rows = duckdb.sql(f"SELECT node_id, node_name FROM read_csv('{f}', all_varchar=true, header=true)").fetchall()
    return dict(rows)


def _mondo_role(graph: str, source_id: str, ref: str) -> str:
    """PrimeKG cross-check: a node's place relative to the disease in MONDO, read from OptimusKG.

    OptimusKG carries MONDO's hierarchy as directed DIS-DIS/PARENT edges.
    PrimeKG's MONDO nodes store the MONDO number in ``source_id`` (grouped
    nodes join several numbers with '_'). ``ref`` is the disease's main node
    in OptimusKG. Returns 'descendant', 'ancestor', 'both' (a grouped node
    with members on each side), 'same', 'unrelated', 'not in OptimusKG' or
    'not checkable'. Informational only; it does not choose the seed.
    """
    db = derived_dir() / "optimuskg.duckdb"
    if graph != "primekg" or not db.exists() or not source_id:
        return "not checkable"
    mine = {f"MONDO_{int(x):07d}" for x in source_id.split("_") if x.isdigit()}
    if not mine:
        return "not checkable"
    if "con" not in _OPT_CACHE:
        _OPT_CACHE["con"] = duckdb.connect(str(db), read_only=True)
    o = _OPT_CACHE["con"]
    present = {r[0] for r in o.execute(
        "SELECT id FROM nodes WHERE list_contains(?::VARCHAR[], id)", [sorted(mine)]).fetchall()}
    if not present:
        return "not in OptimusKG"
    if ref in present:
        return "same"
    if ref not in _OPT_CACHE:
        _OPT_CACHE[ref] = (set(_descendants(o, ref)) - {ref}, _ancestors(o, ref))
    desc, anc = _OPT_CACHE[ref]
    d, a = bool(present & desc), bool(present & anc)
    return "both" if d and a else "descendant" if d else "ancestor" if a else "unrelated"


_OPT_CACHE: dict = {}


def seed_candidates(con, graph: str, disease: str, seed_ids: set[str], excluded: dict[str, str]) -> list[dict]:
    """Every candidate node for one disease, with its role and, if not in the seed, why.

    Candidates are nodes of any type whose name (or, in OptimusKG, a synonym)
    contains one of the disease's search terms, plus the main node's
    neighbors in the disease hierarchy (PrimeKG: ``disease_disease``
    neighbors; OptimusKG: ancestors one step up and every descendant).
    """
    d = DISEASES[disease]
    main = d["main"][graph]
    terms = d["terms"]
    if graph == "optimuskg":
        raw = raw_dir(graph) / "nodes.parquet"
        conds = " OR ".join(
            f"""properties->>'$.name' ILIKE '%{t}%'
                OR CAST(properties->'$.exact_synonyms' AS VARCHAR) ILIKE '%{t}%'
                OR CAST(properties->'$.related_synonyms' AS VARCHAR) ILIKE '%{t}%'
                OR CAST(properties->'$.narrow_synonyms' AS VARCHAR) ILIKE '%{t}%'
                OR CAST(properties->'$.broad_synonyms' AS VARCHAR) ILIKE '%{t}%'"""
            for t in terms
        )
        name_ids = {r[0] for r in con.execute(f"SELECT id FROM read_parquet('{raw}') WHERE {conds}").fetchall()}
        desc = _descendants(con, main)
        parents = {r[0] for r in con.execute(
            "SELECT u FROM edges WHERE rel = 'DIS-DIS/PARENT' AND v = ?", [main]).fetchall()}
        hier = set(desc) | parents
    else:
        conds = " OR ".join(f"name ILIKE '%{t}%'" for t in terms)
        name_ids = {r[0] for r in con.execute(f"SELECT id FROM nodes WHERE {conds}").fetchall()}
        nbrs = {r[0] for r in con.execute(
            "SELECT b FROM adj WHERE a = ? AND rel = 'disease_disease'", [main]).fetchall()}
        hier = nbrs
        file_child = {r[0] for r in con.execute(
            "SELECT child FROM pk_hier_first WHERE rel = 'disease_disease' AND parent = ?", [main]).fetchall()}
        file_parent = {r[0] for r in con.execute(
            "SELECT parent FROM pk_hier_first WHERE rel = 'disease_disease' AND child = ?", [main]).fetchall()}
        members = _grouped_members(graph)
    ids = sorted(name_ids | hier | {main})
    out = []
    for id_ in ids:
        name, typ, source, source_id, degree = con.execute(
            "SELECT n.name, n.type, n.source, n.source_id, d.degree FROM nodes n JOIN deg d USING (id) WHERE n.id = ?",
            [id_],
        ).fetchone()
        rels = con.execute(
            "SELECT rel, count(*) FROM adj WHERE a = ? GROUP BY rel ORDER BY 2 DESC, rel", [id_]
        ).fetchall()
        c = {"id": id_, "name": name, "type": typ, "source": source, "source_id": source_id,
             "degree": degree, "relations": {r: n for r, n in rels}, "in_seed": id_ in seed_ids}
        if graph == "optimuskg":
            c["role"] = ("main" if id_ == main else f"descendant (depth {desc[id_]})" if id_ in desc
                         else "parent" if id_ in parents else "name or synonym match")
        else:
            c["role"] = ("main" if id_ == main else "disease_disease neighbor" if id_ in nbrs
                         else "name match")
            c["file_order"] = ("child" if id_ in file_child else "parent" if id_ in file_parent else "")
            c["mondo_role"] = (_mondo_role(graph, source_id or "", d["main"]["optimuskg"])
                               if typ == "disease" else "")
            if source == "MONDO_grouped":
                nums = (source_id or "").split("_")
                c["grouped_members"] = [f"MONDO:{n} {members.get(n, '?')}" for n in nums]
        if not c["in_seed"]:
            c["excluded_because"] = _exclusion_reason(graph, c, excluded, DISEASE_TYPE[graph])
        out.append(c)
    order = {"main": 0}
    out.sort(key=lambda c: (order.get(c["role"], 1), not c["in_seed"], c["type"] != DISEASE_TYPE[graph], -c["degree"], c["id"]))
    return out


def _exclusion_reason(graph: str, c: dict, excluded: dict[str, str], dis_type: str) -> str:
    """Why a candidate is outside the proposed seed, under the seed rule."""
    if c["id"] in excluded:
        return "excluded by hand: " + excluded[c["id"]]
    if c["type"] != dis_type:
        return f"not a disease node (type {c['type']})"
    if graph == "optimuskg":
        if c["role"] == "parent":
            return "parent of the main node"
        return "not a descendant of the main node"
    if c["role"] == "disease_disease neighbor":
        return "disease_disease neighbor whose name lacks the main node's name"
    return "not a disease_disease neighbor of the main node"


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


def umls_crosswalk_share(con, eid_table: str) -> dict:
    """PrimeKG: share of edges in ``eid_table`` whose relation used the UMLS -> MONDO crosswalk."""
    rels = sorted(load.PRIMEKG_UMLS_CROSSWALK)
    total, n = con.execute(
        f"""SELECT count(*), count(*) FILTER (WHERE list_contains(?::VARCHAR[], split_part(e.rel, '/', 1)))
            FROM edges e JOIN {eid_table} t USING (eid)""",
        [rels],
    ).fetchone()
    return {"edges": total, "count": n, "share": n / total if total else 0.0}


def measure_one(con, graph: str, spec: SubgraphSpec, directional: list[str], absence_adj: str = "adj") -> dict:
    """Compute every expansion-test metric for the current seed and one subgraph definition.

    ``directional`` lists the graph's same-type relations whose reversed
    reading is a different fact (for the reported direction counts).
    ``absence_adj`` is passed to the absence checks; it is ``main.adj`` only
    under the score filter.
    """
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
        ) GROUP BY rel, s ORDER BY rel, 3 DESC, s
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
    # Reported, not gating: facts collapsed over which seed node they hang on.
    # Seed subtypes often repeat the main node's edges (same partner, same
    # relation); those would make near-duplicate questions.
    res["single_hop_distinct_object_relation"] = one(
        f"SELECT count(*) FROM (SELECT DISTINCT o, rel FROM {facts} WHERE o NOT IN (SELECT id FROM seed))"
    )
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
    pat = dict((f"{a} | {b}", n) for a, b, n in q(f"SELECT r1, r2, count(*) FROM {paths} GROUP BY 1, 2 ORDER BY 3 DESC, 1, 2"))
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
    un = sg.unanswerable_candidates(con, name, absence_adj=absence_adj)
    res["unanswerable_s_r_y"] = one(f"SELECT count(*) FROM {un}")
    res["unanswerable_r_y"] = one(f"SELECT count(*) FROM (SELECT DISTINCT r, y FROM {un})")
    un_by_rel = dict(q(f"SELECT r, count(DISTINCT y) FROM {un} GROUP BY r ORDER BY 2 DESC"))
    res["unanswerable_by_relation"] = un_by_rel
    res["unanswerable_relations_ge_20"] = sum(1 for n in un_by_rel.values() if n >= C7_MIN_PER_REL)
    res["partial_candidates"] = sg.partial_candidates(con, name)

    # ---- C8 false premise ----
    ct = sg.contradiction_edges(con, name, load.OPPOSING[graph], absence_adj=absence_adj)
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
    if graph == "primekg":
        # Reported beside R3, not counted in it (see load.PRIMEKG_UMLS_CROSSWALK).
        res["umls_crosswalk"] = {
            "single_hop_facts": umls_crosswalk_share(con, "lic_facts"),
            "all_subgraph_edges": umls_crosswalk_share(con, f"sub_{name}"),
        }

    # ---- Reported: facts whose reversed reading is a different fact ----
    res["direction_inversions"] = sg.direction_inversions(con, name, directional)

    res["pass"] = evaluate(res)
    res["seconds"] = round(time.time() - t0, 1)
    # Drop the big per-definition tables; the JSON keeps what the report needs.
    # facts_<name> stays: the per-disease contributions read facts_b.
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


def directional_relations(con, graph: str) -> list[str]:
    """Same-type relations whose meaning has a direction (everything same-type but the symmetric ones)."""
    same = sg.same_type_relations(con)
    return sorted(r for r in same if r not in load.SYMMETRIC_SAME_TYPE[graph])


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


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------


def domain_seeds(con, graph: str, domain: str) -> dict:
    """Per-disease proposed seeds, candidates and exclusions, plus the two combined seed sets."""
    per: dict[str, dict] = {}
    for dz in DOMAINS[domain]:
        ids, rule, excluded = proposed_seed(con, graph, dz)
        per[dz] = {"label": DISEASES[dz]["label"], "main": DISEASES[dz]["main"][graph], "ids": ids,
                   "rule": rule, "excluded_by_hand": excluded}
    for dz in per:
        per[dz]["candidates"] = seed_candidates(con, graph, dz, set(per[dz]["ids"]), per[dz]["excluded_by_hand"])
    proposed = sorted({i for v in per.values() for i in v["ids"]})
    mains = sorted({v["main"] for v in per.values()})
    return {"per_disease": per, "sets": {
        "proposed": (proposed, "union of each disease's proposed seed" if domain == "neuro" else per["AD"]["rule"]),
        "main_only": (mains, "the main node of each disease alone" if domain == "neuro" else "the main Alzheimer disease node alone"),
    }}


def run_domain(con, graph: str, domain: str, base: dict, directional: list[str]) -> dict:
    """Measure one domain in one graph; returns the JSON-ready result."""
    seeds = domain_seeds(con, graph, domain)
    out = dict(base, domain=domain, diseases=seeds["per_disease"], seed_sets={})
    # Kept for report.py's seed table in the Alzheimer's-only layout.
    out["seed_candidates"] = seeds["per_disease"]["AD"]["candidates"] if domain == "ad" else []
    for label, (ids, why) in seeds["sets"].items():
        sg.set_seed(con, ids)
        entry = {"ids": ids, "rule": why, "n1_size": con.execute("SELECT count(*) FROM n1").fetchone()[0], "definitions": {}}
        for spec in specs(con):
            print(f"[{graph}/{domain}] seed={label} def={spec.name} max_degree={spec.max_degree}", flush=True)
            entry["definitions"][spec.name] = measure_one(con, graph, spec, directional)
            print(f"    pass={entry['definitions'][spec.name]['pass']} ({entry['definitions'][spec.name]['seconds']}s)", flush=True)
        if domain == "neuro" and label == "proposed":
            groups = {dz: v["ids"] for dz, v in seeds["per_disease"].items()}
            entry["per_disease"] = sg.seed_group_contributions(
                con, "facts_b", groups, TARGETS["single"], CAP_SHARE, C2_MIN_FACTS)
        out["seed_sets"][label] = entry
    return out


SENSITIVITY_KEYS = [
    "n_nodes", "n_edges", "single_hop_facts", "single_hop_by_relation", "single_hop_relations_ge_30",
    "single_hop_balanced_capacity", "two_hop_paths", "two_hop_endpoints", "two_hop_patterns_ge_20",
    "two_hop_balanced_capacity", "top1_path_share", "top5_path_share", "partial_candidates",
    "unanswerable_r_y", "unanswerable_relations_ge_20", "contradicted", "false_premise_total", "pass",
]


def run_score_sensitivity(con, graph: str, directional: list[str]) -> dict:
    """OptimusKG only: C1–C8 counts under evidence_score cutoffs, for both domains' proposed seeds.

    The filter drops scored edges below the cutoff from the graph the
    subgraphs are cut from (so N1, paths and facts shrink); hub-pruning caps
    and absence checks stay on the unfiltered graph (see
    ``subgraph.apply_score_filter``). Reported only.
    """
    scored = load.SCORED_RELATIONS[graph]
    out: dict = {"graph": graph, "scored_relations": scored, "cutoffs": list(SCORE_CUTOFFS), "domains": {}}
    for domain in DOMAINS:
        ids = domain_seeds(con, graph, domain)["sets"]["proposed"][0]
        out["domains"][domain] = {"seed_ids": ids, "cutoffs": {}}
        for cut in SCORE_CUTOFFS:
            sg.apply_score_filter(con, cut, scored)
            try:
                kept = {r: con.execute("SELECT count(*) FROM edges WHERE rel = ?", [r]).fetchone()[0] for r in scored}
                sg.set_seed(con, ids)
                defs = {}
                for spec in specs(con):
                    print(f"[{graph}/scores>={cut}/{domain}] def={spec.name}", flush=True)
                    m = measure_one(con, graph, spec, directional, absence_adj="main.adj")
                    defs[spec.name] = {k: m[k] for k in SENSITIVITY_KEYS}
                out["domains"][domain]["cutoffs"][str(cut)] = {"scored_edges_kept": kept, "definitions": defs}
            finally:
                sg.clear_score_filter(con)
    out["scored_edges_total"] = {r: con.execute("SELECT count(*) FROM edges WHERE rel = ?", [r]).fetchone()[0] for r in scored}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("graphs", nargs="*", default=["primekg", "optimuskg"])
    ap.add_argument("--domain", choices=["ad", "neuro", "all"], default="all")
    ap.add_argument("--scores", action="store_true", help="also run the OptimusKG score sensitivity")
    args = ap.parse_args()
    manifest = json.loads((meta_dir() / "manifest.json").read_text())
    domains = list(DOMAINS) if args.domain == "all" else [args.domain]
    # Load every graph first so the PrimeKG MONDO cross-check can read OptimusKG.
    for graph in sorted(args.graphs, key=lambda g: g != "optimuskg"):
        t0 = time.time()
        con, checks = open_graph(graph)
        directional = directional_relations(con, graph)
        base = {
            "graph": graph,
            "dataset": dict(manifest["datasets"][graph].items()),
            "accessed": manifest["accessed"],
            "load_checks": checks,
            "summary": graph_summary(con),
            "directional_relations": directional,
        }
        for domain in domains:
            out = run_domain(con, graph, domain, base, directional)
            suffix = "" if domain == "ad" else f"_{domain}"
            path = derived_dir() / f"results_{graph}{suffix}.json"
            path.write_text(json.dumps(out, indent=1, default=str))
            print(f"[{graph}/{domain}] -> {path}")
        if args.scores and load.SCORED_RELATIONS[graph]:
            out = run_score_sensitivity(con, graph, directional)
            path = derived_dir() / f"score_sensitivity_{graph}.json"
            path.write_text(json.dumps(out, indent=1, default=str))
            print(f"[{graph}/scores] -> {path}")
        print(f"[{graph}] done in {time.time() - t0:.0f}s")
        con.close()
        if "con" in _OPT_CACHE:
            _OPT_CACHE.pop("con").close()
        _OPT_CACHE.clear()


if __name__ == "__main__":
    main()
