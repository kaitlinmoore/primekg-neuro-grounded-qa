"""Render the measurement JSON files into the markdown tables of the expansion-test results.

Writes, under ``NGQ_DATA_DIR/derived``:

- ``expansion_results.md`` from ``results_<graph>.json`` (Alzheimer's only);
- ``expansion_results_neuro.md`` from ``results_<graph>_neuro.json`` (broader
  neurodegeneration), plus the per-disease contribution table, the OptimusKG
  score sensitivity (``score_sensitivity_optimuskg.json``) and the
  directed-relation inventory (``direction_inventory.json``) when those files
  exist.

The tables are pasted into ``docs/question_set_design.md``; the interpretive
paragraphs there are written by hand. The output holds aggregate counts, node
names and IDs only, no edge lists, so it can be committed.

Run::

    uv run python scripts/data/report.py
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


def pass_table(results: dict, seed_names: dict[str, str] | None = None) -> str:
    seed_names = seed_names or {"proposed": "proposed", "main_only": "main only"}
    rows = []
    for g in GRAPHS:
        for label in ("proposed", "main_only"):
            defs = results[g]["seed_sets"][label]["definitions"]
            for d in DEFS:
                p = defs[d]["pass"]
                rows.append([TITLE[g], seed_names[label], DEF_LABEL[d]] + [p[c] for c in CRITERIA] + [p["all"]])
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


# Reported beside R3 in the broader-seed tables (PrimeKG only; n/a elsewhere).
EXTRA_METRICS = [
    ("R3 note: single-hop facts whose disease end was mapped through the UMLS crosswalk (not counted in R3)", "umls_single"),
    ("R3 note: subgraph edges mapped through the UMLS crosswalk (not counted in R3)", "umls_all"),
]


def extra_value(m: dict, key: str):
    u = m.get("umls_crosswalk")
    if not u:
        return "n/a"
    x = u["single_hop_facts" if key == "umls_single" else "all_subgraph_edges"]
    return f"{x['share']:.1%} ({x['count']:,})"


def metrics_table(r: dict, label: str, extras: bool = False) -> str:
    defs = r["seed_sets"][label]["definitions"]
    rows = [[name] + [metric_value(defs[d], key, name) for d in DEFS] for name, key in METRICS]
    if extras:
        rows += [[name] + [extra_value(defs[d], key) for d in DEFS] for name, key in EXTRA_METRICS]
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


# --------------------------------------------------------------------------
# Broader neurodegeneration seed
# --------------------------------------------------------------------------

DISEASE_ORDER = ["AD", "PD", "FTD", "ALS", "HD", "DLB"]


def _rels(c: dict, k: int = 3) -> str:
    items = list(c["relations"].items())
    more = f" (+{len(items) - k} more)" if len(items) > k else ""
    return ", ".join(f"{r} {n:,}" for r, n in items[:k]) + more or "none"


def neuro_seed_section(r: dict) -> str:
    """Per disease: candidate table, proposed seed, grouped members, exclusions."""
    g = r["graph"]
    dis_type = "disease" if g == "primekg" else "DIS"
    out = []
    for dz in DISEASE_ORDER:
        d = r["diseases"][dz]
        cands = d["candidates"]
        dis = [c for c in cands if c["type"] == dis_type]
        other = [c for c in cands if c["type"] != dis_type]
        if g == "primekg":
            header = ["ID", "Name", "Degree", "Role", "File order", "MONDO (via OptimusKG)", "Edges by relation", "In seed", "Excluded because"]
            rows = [[f"`{c['id']}`", c["name"], c["degree"], c["role"], c.get("file_order") or "",
                     c.get("mondo_role") or "", _rels(c), "yes" if c["in_seed"] else "no",
                     c.get("excluded_because", "")] for c in dis]
        else:
            header = ["ID", "Name", "Degree", "Role", "Edges by relation", "In seed", "Excluded because"]
            rows = [[f"`{c['id']}`", c["name"], c["degree"], c["role"], _rels(c),
                     "yes" if c["in_seed"] else "no", c.get("excluded_because", "")] for c in dis]
        part = [f"#### {d['label']} ({dz}), {TITLE[g]}", table(header, rows)]
        if other:
            part.append(
                "Non-disease nodes matching the search terms (not in the seed: not a disease node): "
                + "; ".join(f"`{c['id']}` {c['name']} ({c['type']})" for c in other) + "."
            )
        part.append(f"Proposed seed ({len(d['ids'])} node{'s' if len(d['ids']) != 1 else ''}): {', '.join('`' + i + '`' for i in d['ids'])}. Rule: {d['rule']}.")
        grouped = [c for c in cands if c["in_seed"] and c.get("grouped_members")]
        for c in grouped:
            part.append(f"Grouped node `{c['id']}` ({c['name']}) merges {len(c['grouped_members'])} MONDO diseases: "
                        + "; ".join(c["grouped_members"]) + ".")
        for i, why in d.get("excluded_by_hand", {}).items():
            part.append(f"Excluded by hand: `{i}`. {why}")
        out.append("\n\n".join(part))
    return "\n\n".join(out)


def seed_summary_table(results: dict) -> str:
    """Disease x graph: main node, seed size and single-hop facts."""
    rows = []
    for dz in DISEASE_ORDER:
        row = [f"{results['primekg']['diseases'][dz]['label']} ({dz})"]
        for g in GRAPHS:
            d = results[g]["diseases"][dz]
            pd_ = results[g]["seed_sets"]["proposed"]["per_disease"]["groups"][dz]
            row += [f"`{d['main']}`", len(d["ids"]), pd_["facts"]]
        rows.append(row)
    tot = ["**Combined (proposed)**"]
    for g in GRAPHS:
        ss = results[g]["seed_sets"]["proposed"]
        tot += ["", len(ss["ids"]), ss["per_disease"]["combined"]["facts"]]
    rows.append(tot)
    return table(["Disease", "PrimeKG main", "PrimeKG seed nodes", "PrimeKG single-hop facts",
                  "OptimusKG main", "OptimusKG seed nodes", "OptimusKG single-hop facts"], rows)


def overlap_table(results: dict) -> str:
    """Pairwise overlap between the diseases' proposed seeds, per graph."""
    rows = []
    for g in GRAPHS:
        for o in results[g]["seed_sets"]["proposed"]["per_disease"]["overlap"]:
            rows.append([TITLE[g], f"{o['a']}–{o['b']}", ", ".join(f"`{x}`" for x in o["shared_seed_nodes"]) or "none",
                         o["shared_facts"], o["shared_partner_relation"], f"{o['jaccard_partner_relation']:.1%}"])
    return table(["Graph", "Pair", "Shared seed nodes", "Shared single-hop facts",
                  "Shared (partner, relation)", "Jaccard of (partner, relation)"], rows)


def per_disease_table(results: dict) -> str:
    """Reported only: what each disease contributes to the single-hop pool and to C2."""
    rows = []
    for g in GRAPHS:
        pdis = results[g]["seed_sets"]["proposed"]["per_disease"]
        for dz in DISEASE_ORDER:
            x = pdis["groups"][dz]
            top = ", ".join(f"{r} {n:,}" for r, n in list(x["facts_by_relation"].items())[:4])
            ad = next((p for p in pdis["pairs_c2"] if {p["a"], p["b"]} == {"AD", dz}), None)
            rows.append([TITLE[g], dz, x["seed_nodes"], x["facts"], x["distinct_partner_relation"],
                         x["facts_only_this_group"], len(x["facts_by_relation"]), top or "none",
                         f"{x['relations_ge_min']} / {x['balanced_capacity']:,}",
                         f"{x['without_relations_ge_min']} / {x['without_balanced_capacity']:,}",
                         f"+{x['adds_relations_ge_min']} / +{x['adds_balanced_capacity']:,}",
                         "n/a" if ad is None else f"{ad['relations_ge_min']} / {ad['balanced_capacity']:,}"])
        c = pdis["combined"]
        rows.append([TITLE[g], "**all six**", len(results[g]["seed_sets"]["proposed"]["ids"]), c["facts"],
                     "", "", "", "", f"{c['relations_ge_min']} / {c['balanced_capacity']:,}", "", "", ""])
    return table(["Graph", "Disease", "Seed nodes", "Single-hop facts", "Distinct (partner, relation)",
                  "Facts no other disease reaches", "Relations", "Largest relations",
                  "C2 inputs alone: relations ≥ 30 / balanced capacity",
                  "C2 inputs, other five without it", "It adds (all six − other five)",
                  "C2 inputs, AD plus this disease"], rows)


def _sens_row(m: dict) -> list:
    p = m["pass"]
    met = sum(1 for k in CRITERIA if p[k])
    return [m["n_nodes"], m["n_edges"], m["single_hop_facts"],
            f"{m['single_hop_relations_ge_30']} / {m['single_hop_balanced_capacity']:,}",
            f"{m['two_hop_paths']:,} / {m['two_hop_endpoints']:,}",
            f"{m['two_hop_patterns_ge_20']} / {m['two_hop_balanced_capacity']:,}",
            f"{m['top1_path_share']:.1%} / {m['top5_path_share']:.1%}",
            m["partial_candidates"],
            f"{m['unanswerable_r_y']:,} / {m['unanswerable_relations_ge_20']}",
            f"{m['false_premise_total']:,} / {m['contradicted']:,}",
            f"{met} of 8" + ("" if p["all"] else " (fails " + ", ".join(k for k in CRITERIA if not p[k]) + ")")]


def score_section(sens: dict, base: dict[str, dict]) -> str:
    """Reported only: C1–C8 counts in OptimusKG with no cutoff, >= 0.1 and >= 0.5."""
    out = []
    kept = []
    for cut in sens["cutoffs"]:
        k = sens["domains"]["ad"]["cutoffs"][str(cut)]["scored_edges_kept"]
        kept.append(f"≥ {cut}: " + ", ".join(f"`{r}` {n:,} of {sens['scored_edges_total'][r]:,}" for r, n in k.items()))
    out.append("Scored edges kept in the whole graph: " + "; ".join(kept) + ".")
    header = ["Definition", "Cutoff", "Nodes", "Edges", "C1 facts", "C2 rels ≥ 30 / balanced",
              "C3 paths / endpoints", "C4 patterns / balanced", "C5 top-1 / top-5", "C6",
              "C7 pairs / rels ≥ 20", "C8 total / contradicted", "Gating criteria met (reported only)"]
    for domain, title in (("ad", "Alzheimer's-only, proposed seed"), ("neuro", "Broader neurodegeneration, proposed seed")):
        rows = []
        for d in DEFS:
            rows.append([DEF_LABEL[d], "none"] + _sens_row(base[domain]["seed_sets"]["proposed"]["definitions"][d]))
            for cut in sens["cutoffs"]:
                m = sens["domains"][domain]["cutoffs"][str(cut)]["definitions"][d]
                rows.append([DEF_LABEL[d], f"≥ {cut}"] + _sens_row(m))
        out.append(f"**{title}** (reported only, not gating)\n\n" + table(header, rows))
    return "\n\n".join(out)


def _group_relations(rows: list[dict]) -> list[dict]:
    """Collapse OptimusKG relations that share label prefix, types, flag, meaning and risk into one row."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        key = (r["relation"].split("/")[0], tuple(r["endpoint_types"]), r["source_flag"], r["meaning"],
               r["wrong_support_possible"])
        groups.setdefault(key, []).append(r)
    out = []
    for key, rs in groups.items():
        if len(rs) == 1:
            out.append(rs[0])
        else:
            names = ", ".join(x["relation"].split("/", 1)[1] for x in rs)
            out.append({"relation": f"{key[0]}/* ({len(rs)}: {names})", "edges": sum(x["edges"] for x in rs),
                        "endpoint_types": list(key[1]), "source_flag": key[2], "meaning": key[3],
                        "wrong_support_possible": key[4]})
    return out


def direction_section(inv: dict, neuro: dict[str, dict]) -> str:
    out = []
    for g in GRAPHS:
        rows = inv[g]["relations"] if g == "primekg" else _group_relations(inv[g]["relations"])
        out.append(f"**Relation inventory, {TITLE[g]}**\n\n" + table(
            ["Relation", "Edges (canonical)", "Endpoint types", "Source marks direction", "Meaning",
             "Ignoring direction could wrongly support a claim"],
            [[f"`{r['relation']}`", r["edges"], "; ".join(r["endpoint_types"]), r["source_flag"], r["meaning"],
              r["wrong_support_possible"]] for r in rows],
        ))
    conv = inv["optimuskg"]["hierarchy_convention"]
    out.append("**OptimusKG hierarchy convention** (which end of an edge is the parent, read from an ontology "
               "root, which sits only on the parent side, or from a known parent -> child pair)\n\n" + table(
                   ["Relation", "Probe", "Evidence", "Convention"],
                   [[f"`{k}`", v["probe"], v["evidence"], v["convention"]] for k, v in conv.items()]))
    fo = inv["primekg"]["file_order_check"]
    out.append("**PrimeKG file order vs OptimusKG direction**: for each PrimeKG hierarchy pair, the orientation of "
               "its first copy in `kg.csv`, compared with OptimusKG's edge between the same ontology IDs\n\n" + table(
                   ["PrimeKG relation", "Pairs", "Pairs with ontology IDs", "Found in OptimusKG, same order",
                    "Found in OptimusKG, opposite order", "Agreement", "First copy reads"],
                   [[f"`{k}`", v["pairs"], v["pairs_with_mappable_ids"], v["optimuskg_edge_same_order"],
                     v["optimuskg_edge_opposite_order"], "n/a" if v["agreement"] is None else f"{v['agreement']:.1%}",
                     v["first_copy_reads"]] for k, v in fo.items()]))
    rows = []
    for g in GRAPHS:
        defs = neuro[g]["seed_sets"]["proposed"]["definitions"]
        for rel in neuro[g]["directional_relations"]:
            row = [TITLE[g], f"`{rel}`"]
            for d in ("a", "b", "c95"):
                x = defs[d]["direction_inversions"][rel]
                row.append(f"{x['reversed_reading_differs']:,} of {x['edges']:,}")
            x = defs["b"]["direction_inversions"][rel]
            row.append(f"{x['single_hop_reversed_reading_differs']:,} of {x['single_hop_facts']:,}")
            row.append(f"{x['reverse_also_present']:,}")
            rows.append(row)
    out.append("**Broader neurodegeneration subgraph, proposed seed**: edges of each directional same-type relation "
               "whose reversed reading is a different fact (\"x of y\": x such edges among the relation's y edges)\n\n"
               + table(["Graph", "Relation", "(a) 1-hop", "(b) 2-hop", "(c) p95", "Single-hop facts (any definition)",
                        "Both directions stored, (b)"], rows))
    return "\n\n".join(out)


def neuro_report() -> str | None:
    d = derived_dir()
    paths = {g: d / f"results_{g}_neuro.json" for g in GRAPHS}
    if not all(p.exists() for p in paths.values()):
        return None
    results = {g: json.loads(p.read_text()) for g, p in paths.items()}
    names = {"proposed": "proposed (all six)", "main_only": "main nodes only"}
    # Headings differ from the Alzheimer's-only ones so the anchors in
    # docs/question_set_design.md stay unique.
    parts = ["### Broader seed: seeds by disease", seed_summary_table(results)]
    for g in GRAPHS:
        parts += [f"### Broader seed: candidates, {TITLE[g]}", neuro_seed_section(results[g])]
    parts += ["### Overlap between the diseases' seeds", overlap_table(results)]
    parts += ["### Broader seed: pass/fail", pass_table(results, names)]
    for g in GRAPHS:
        for label in ("proposed", "main_only"):
            parts += [f"### Broader seed metrics: {TITLE[g]}, {names[label]}", metrics_table(results[g], label, extras=True)]
    parts += ["### Per-disease contribution (reported)", per_disease_table(results)]
    for g in GRAPHS:
        parts += [f"### Broader seed detail: {TITLE[g]}, proposed seed", detail_section(results[g])]
    sens_path = d / "score_sensitivity_optimuskg.json"
    if sens_path.exists():
        base = {"ad": json.loads((d / "results_optimuskg.json").read_text()), "neuro": results["optimuskg"]}
        parts += ["### OptimusKG score sensitivity (reported only, not gating)",
                  score_section(json.loads(sens_path.read_text()), base)]
    inv_path = d / "direction_inventory.json"
    if inv_path.exists():
        parts += ["### Directed-relation inventory", direction_section(json.loads(inv_path.read_text()), results)]
    return "\n\n".join(parts) + "\n"


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
    neuro = neuro_report()
    if neuro:
        out = derived_dir() / "expansion_results_neuro.md"
        out.write_text(neuro)
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
