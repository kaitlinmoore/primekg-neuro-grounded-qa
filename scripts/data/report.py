"""Render derived/results_<graph>.json into the markdown tables of the expansion-test results.

Writes ``NGQ_DATA_DIR/derived/expansion_results.md``. The tables are pasted
under "Expansion test: results" in ``docs/question_set_design.md``; the
interpretive paragraph there is written by hand. The output holds aggregate
counts, node names and IDs only, no edge lists, so it can be committed.

Run::

    NGQ_DATA_DIR=$HOME/data/ngq uv run --group data python scripts/data/report.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import derived_dir  # noqa: E402

GRAPHS = ["primekg", "optimuskg"]
TITLE = {"primekg": "PrimeKG", "optimuskg": "OptimusKG"}
DEFS = ["a", "b", "c90", "c95", "c99"]
DEF_LABEL = {"a": "(a) 1-hop", "b": "(b) 2-hop", "c90": "(c) p90", "c95": "(c) p95", "c99": "(c) p99"}
DETAIL_DEFS = ["a", "b", "c95"]
CRITERIA = ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"]


def fmt(x) -> str:
    """Integers with thousands separators; shares as percentages."""
    if isinstance(x, bool):
        return "pass" if x else "**fail**"
    if isinstance(x, int):
        return f"{x:,}"
    if isinstance(x, float):
        return f"{x:.1%}" if x <= 1 else f"{x:,.1f}"
    return str(x)


def table(header: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(fmt(c) if not isinstance(c, str) else c for c in r) + " |" for r in rows]
    return "\n".join(lines)


def data_section(results: dict) -> str:
    rows = []
    for g in GRAPHS:
        r = results[g]
        ds = r["dataset"]
        lic = (ds.get("license") or {}).get("name", "not stated")
        for f in ds["files"]:
            rows.append(
                [TITLE[g], f"`{f['name']}`", f["url"], ds["dataset_version"], f"{f['size_bytes']:,}",
                 f"`{f['sha256']}`", "yes" if f["md5_matches"] else "**no**", r["accessed"], lic]
            )
    return table(
        ["Graph", "File", "URL", "Dataverse version", "Bytes", "sha256", "md5 matches Dataverse", "Accessed", "Dataset license (Dataverse)"],
        rows,
    )


def load_section(results: dict) -> str:
    out = []
    for g in GRAPHS:
        r = results[g]
        s, c = r["summary"], r["load_checks"]
        out.append(
            f"- **{TITLE[g]}:** {s['nodes']:,} nodes; {c['raw_rows']:,} edge rows → "
            f"{c['canonical_edges']:,} canonical edges ({c['rows_collapsed']:,} rows collapsed); "
            f"{len(s['relations'])} relation labels; {s['pairs_with_more_than_one_relation']:,} node pairs "
            f"carry more than one relation. Full-graph degree percentiles (distinct neighbors): "
            + ", ".join(f"p{p} = {v:,}" for p, v in s["degree_percentiles"].items())
            + ". Other load checks: "
            + "; ".join(f"{k} = {v}" for k, v in c.items() if k not in ("raw_rows", "canonical_edges", "rows_collapsed"))
            + "."
        )
    return "\n".join(out)


def seed_section(r: dict) -> str:
    proposed = set(r["seed_sets"]["proposed"]["ids"])
    rows = []
    for c in r["seed_candidates"]:
        rels = ", ".join(f"{k} {v}" for k, v in c["relations"].items())
        rows.append(
            [f"`{c['id']}`", c["name"], c["type"], c["source"] or "", f"{c['degree']:,}",
             rels or "none", "yes" if c["id"] in proposed else "no"]
        )
    t = table(["ID", "Name", "Type", "Source", "Degree", "Edges by relation", "In proposed seed"], rows)
    ss = r["seed_sets"]
    return (
        t
        + f"\n\nProposed seed ({len(ss['proposed']['ids'])} nodes): {', '.join('`'+i+'`' for i in ss['proposed']['ids'])}. "
        f"Rule: {ss['proposed']['rule']}. N1 = {ss['proposed']['n1_size']:,} nodes.  \n"
        f"Main node alone: `{ss['main_only']['ids'][0]}`; N1 = {ss['main_only']['n1_size']:,} nodes."
    )


def pass_table(results: dict) -> str:
    rows = []
    for g in GRAPHS:
        for label in ("proposed", "main_only"):
            defs = results[g]["seed_sets"][label]["definitions"]
            for d in DEFS:
                p = defs[d]["pass"]
                rows.append([TITLE[g], label.replace("_", " "), DEF_LABEL[d]] + [p[c] for c in CRITERIA] + [p["all"]])
    return table(["Graph", "Seed", "Definition"] + CRITERIA + ["All gating"], rows)


METRICS = [
    ("Nodes", "n_nodes"),
    ("Edges", "n_edges"),
    ("Degree cap for pruning", "max_degree"),
    ("C1 single-hop facts (≥ 270)", "single_hop_facts"),
    ("single-hop facts, distinct (partner, relation) over seed nodes (reported)", "single_hop_distinct_object_relation"),
    ("C2 relations with ≥ 30 facts (≥ 4)", "single_hop_relations_ge_30"),
    ("C2 balanced capacity (≥ 270)", "single_hop_balanced_capacity"),
    ("C3 two-hop paths (≥ 225)", "two_hop_paths"),
    ("C3 distinct endpoints (≥ 225)", "two_hop_endpoints"),
    ("C4 patterns with ≥ 20 paths (≥ 3)", "two_hop_patterns_ge_20"),
    ("C4 balanced capacity (≥ 225)", "two_hop_balanced_capacity"),
    ("C5 top-1 intermediate path share (≤ 20%)", "top1_path_share"),
    ("C5 top-5 path share (≤ 50%)", "top5_path_share"),
    ("top-20 path share", "top20_path_share"),
    ("top-1 / top-5 / top-20 endpoint share", None),
    ("endpoints reachable only via top 5", "top5_endpoints_only_via_share"),
    ("C6 facts with an absent sibling (≥ 135)", "partial_candidates"),
    ("C7 unanswerable (r, y) pairs (≥ 180)", "unanswerable_r_y"),
    ("C7 relations with ≥ 20 (≥ 3)", "unanswerable_relations_ge_20"),
    ("C8 (i) contradicted pairs (≥ 30)", "contradicted"),
    ("C8 (i) of which touch the seed", "contradicted_touching_seed"),
    ("C8 (ii) spare absence premises", "absence_premises_spare"),
    ("C8 total (≥ 90)", "false_premise_total"),
    ("R1 shared-neighbor disease pairs", None),
    ("R2 facts in seed groups of ≤ 20 edges", "facts_in_seed_groups_le_cap"),
    ("R2 facts reachable in one page from either end", "facts_reachable_in_one_page_either_side"),
    ("R3 single-hop facts from a restricted source", None),
    ("R3 two-hop path edges from a restricted source", None),
    ("R3 subgraph edges from a restricted source", None),
]


def metric_value(m: dict, key: str | None, label: str):
    if key is not None:
        v = m.get(key)
        return "n/a" if v is None else v
    if label.startswith("top-1 / top-5"):
        return " / ".join(fmt(m[f"top{k}_endpoint_share"]) for k in (1, 5, 20))
    if label.startswith("R1"):
        s = m["shared_neighbor_pairs"]
        return f"{s['pairs']:,} ({s['pairs_with_1_to_20_shared']:,} with 1–20 shared)"
    lic_key = {"R3 single": "single_hop_facts", "R3 two-ho": "edges_on_two_hop_paths", "R3 subgra": "all_subgraph_edges"}[label[:9]]
    x = m["licensing"][lic_key]["any_restricted"]
    return f"{x['share']:.1%} ({x['count']:,})"


def metrics_table(r: dict, label: str) -> str:
    defs = r["seed_sets"][label]["definitions"]
    rows = [[name] + [metric_value(defs[d], key, name) for d in DEFS] for name, key in METRICS]
    return table(["Metric (proposed threshold)"] + [DEF_LABEL[d] for d in DEFS], rows)


def detail_section(r: dict) -> str:
    defs = r["seed_sets"]["proposed"]["definitions"]
    out = []
    types = sorted({t for d in DETAIL_DEFS for t in defs[d]["nodes_by_type"]}, key=lambda t: -defs["b"]["nodes_by_type"].get(t, 0))
    out.append("**Nodes by type**\n\n" + table(
        ["Type"] + [DEF_LABEL[d] for d in DETAIL_DEFS],
        [[t] + [defs[d]["nodes_by_type"].get(t, 0) for d in DETAIL_DEFS] for t in types],
    ))
    rels = sorted({x for d in DETAIL_DEFS for x in defs[d]["edges_by_relation"]}, key=lambda x: -defs["b"]["edges_by_relation"].get(x, {}).get("edges", 0))
    rows = []
    for rel in rels:
        srcs = defs["b"]["edges_by_relation"].get(rel, {}).get("top_sources", {})
        rows.append(
            [f"`{rel}`"] + [defs[d]["edges_by_relation"].get(rel, {}).get("edges", 0) for d in DETAIL_DEFS]
            + [defs["c95"]["single_hop_by_relation"].get(rel, 0), ", ".join(list(srcs)[:6])]
        )
    out.append("**Edges by relation**, with the relation's sources (top six by edge count in (b))\n\n" + table(
        ["Relation"] + [DEF_LABEL[d] for d in DETAIL_DEFS] + ["single-hop facts", "Sources"], rows))
    out.append("**Degree within the subgraph** (distinct neighbors)\n\n" + table(
        ["Definition", "min", "median", "p90", "p99", "max", "mean"],
        [[DEF_LABEL[d]] + [fmt(defs[d]["degree_in_subgraph"][k]) for k in ("min", "p50", "p90", "p99", "max", "mean")] for d in DETAIL_DEFS],
    ))
    for d in DETAIL_DEFS:
        out.append(f"**Top 20 hubs, {DEF_LABEL[d]}** (by degree within the subgraph)\n\n" + table(
            ["ID", "Name", "Type", "Degree in subgraph", "Degree in full graph", "Seed"],
            [[f"`{h['id']}`", h["name"] or "", h["type"], h["degree_in_subgraph"], h["degree_full"], "yes" if h["is_seed"] else ""] for h in defs[d]["top20_hubs"]],
        ))
    for d in DETAIL_DEFS:
        out.append(f"**Top 10 two-hop intermediates, {DEF_LABEL[d]}**\n\n" + table(
            ["ID", "Name", "Type", "Paths", "Path share", "Endpoint share"],
            [[f"`{x['id']}`", x["name"] or "", x["type"], x["paths"], x["path_share"], x["endpoint_share"]] for x in defs[d]["top_intermediates"][:10]],
        ))
    m = defs["c95"]
    out.append("**Candidate breakdowns, (c) p95**\n\n" + table(
        ["Breakdown", "Counts"],
        [
            ["Single-hop facts by relation", "; ".join(f"`{k}` {v:,}" for k, v in m["single_hop_by_relation"].items())],
            ["Top two-hop patterns", "; ".join(f"`{k}` {v:,}" for k, v in list(m["two_hop_patterns_top15"].items())[:8])],
            ["Unanswerable candidates by relation (distinct y)", "; ".join(f"`{k}` {v:,}" for k, v in m["unanswerable_by_relation"].items())],
            ["Contradicted pairs by relation", "; ".join(f"`{k}` {v:,}" for k, v in m["contradicted_by_relation"].items()) or "none"],
            ["Seed fan-out (seed, relation): edges", "; ".join(f"{k}: {v:,}" for k, v in list(m["seed_fanout"].items())[:12])],
            ["Shared-neighbor disease pairs by shared count", "; ".join(f"{k} {v:,}" for k, v in m["shared_neighbor_pairs"].items())],
        ],
    ))
    lic = m["licensing"]
    out.append("**Licensing, (c) p95**: share of edges with each restricted source\n\n" + table(
        ["Edge set", "Edges", "DrugBank", "SIDER", "DisGeNET", "UMLS", "Any"],
        [[k.replace("_", " "), v["edges"]] + [f"{v[s]['share']:.1%}" for s in ("DrugBank", "SIDER", "DisGeNET", "UMLS", "any_restricted")] for k, v in lic.items()],
    ))
    return "\n\n".join(out)


def main() -> None:
    results = {g: json.loads((derived_dir() / f"results_{g}.json").read_text()) for g in GRAPHS}
    parts = ["### Data obtained", data_section(results), "### Load checks", load_section(results)]
    for g in GRAPHS:
        parts += [f"### Seed candidates: {TITLE[g]}", seed_section(results[g])]
    parts += ["### Pass/fail", pass_table(results)]
    for g in GRAPHS:
        for label in ("proposed", "main_only"):
            parts += [f"### Metrics: {TITLE[g]}, {label.replace('_', ' ')} seed", metrics_table(results[g], label)]
    for g in GRAPHS:
        parts += [f"### Detail: {TITLE[g]}, proposed seed", detail_section(results[g])]
    out = derived_dir() / "expansion_results.md"
    out.write_text("\n\n".join(parts) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
