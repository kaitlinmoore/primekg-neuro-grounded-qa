"""Feasibility of the question-generation design on the built store: counts only.

For every template in ``qgen_templates.catalog()``, this builds the candidates the
design allows and runs every validity check the generator runs, through the shared
pipeline (``qgen.candidates.build_pool``; the stages are listed there). Then it
assigns each surviving candidate to a split by hash (``qgen.checks.split_of``) and
runs the generator's seeded selection (``qgen.selection``) against the targets (type
mix, disease-group quotas, the 40% per-relation cap, fact- and anchor-disjointness),
to show whether the targets can be met and with how much headroom. It writes no
items; ``question_set.py generate`` does, with the same code.

``--rules`` picks the rule set: ``current`` (default: domain filters, every mention
typed, phenotypes in lowercase, false-premise cap per kind), ``revision1`` (the
first generated set: filters, typed only when a name is shared, names as stored,
one cap over all false premises) or ``design`` (no filters, strict names; the
design-stage counts). ``--no-filters`` is ``--rules design``.

**Output is aggregate counts only.** No question text, entity name or ID is printed
or written: the candidates include what will become held-out items, and nobody,
human or AI, may read those (see "Split plan and held-out protection" in the design
doc). Errors inside a check are counted by check name, never echoed.

Run from the repo root (needs the ``data`` group for duckdb)::

    uv run python scripts/data/qgen_feasibility.py \\
        "$NGQ_DATA_DIR/store/primekg-neuro-c95" "$NGQ_DATA_DIR/derived/primekg.duckdb"

It writes ``qgen_feasibility.json`` next to the duckdb file (aggregates only) and
prints the markdown tables used in the design doc.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import duckdb
from neuro_grounded_qa.data import store_files as sf
from neuro_grounded_qa.data.build import HIERARCHY_CHILD_END
from neuro_grounded_qa.tools import KGToolsetConfig, load_store
from qgen import GENERATOR_VERSION
from qgen import checks as qc
from qgen.candidates import K_BRIDGE, K_LIST, K_LIST2, NEAR_MISS_CAP, STAGES, Cand, Filters, build_pool
from qgen.selection import Plan, select_splits, subtype, targets

PLAN = Plan()
GROUP_SHARE = PLAN.group_share
HEADROOM = PLAN.headroom


def markdown(r: dict) -> str:
    """The design doc's feasibility tables, from the aggregate result. Counts only."""
    out: list[str] = []

    def table(head: list[str], rows: list[list]) -> None:
        out.append("| " + " | ".join(head) + " |")
        out.append("|" + "---|" * len(head))
        out.extend("| " + " | ".join(str(x) for x in row) + " |" for row in rows)
        out.append("")

    out.append("**Funnel per template** (candidates left after each check, in order)\n")
    table(["Template", *STAGES[:-1], "valid"],
          [[f"`{t}`", *(f.get(s, "") for s in STAGES)] for t, f in r["funnel"].items()])

    out.append("**Valid candidates per split and type, against targets**\n")
    types = ["single_hop", "multi_hop", "partial", "unanswerable", "false_premise:no", "false_premise:correctable"]
    rows = []
    for t in types:
        row = [t]
        for split in ("train", "heldout"):
            n = r["valid_by_split_type"].get(split, {}).get(t, 0)
            tgt = r["selection"][split]["target"][t]
            row += [n, tgt, f"{n / tgt:.0f}x" if tgt else "n/a"]
        rows.append(row)
    table(["Type", "train valid", "train target", "multiple", "held-out valid", "held-out target", "multiple"], rows)

    out.append("**Valid candidates per type and relation key, both splits**\n")
    rel: Counter = Counter()
    for split in ("train", "heldout"):
        for k, v in r["valid_by_split_type_relation"].get(split, {}).items():
            rel[k] += v
    table(["Type", "Relation key", "Valid"], [[*k.split("|", 1), v] for k, v in sorted(rel.items())])

    out.append("**Valid candidates per type and disease group, both splits**\n")
    grp: Counter = Counter()
    for split in ("train", "heldout"):
        for k, v in r["valid_by_split_type_group"].get(split, {}).items():
            grp[k] += v
    groups = list(GROUP_SHARE)
    table(["Type", *groups], [[t, *(grp.get(f"{t}|{g}", 0) for g in groups)] for t in types])

    out.append("**Simulated selection** (pass 1 with disease-group cells; pass 2 spills)\n")
    rows = []
    for split in ("heldout", "train"):
        sel = r["selection"][split]
        for t in types:
            short = [
                f"{k.split('|')[1]} {sel['cell_selected'].get(k, 0)}/{n}"
                for k, n in sel["cell_target"].items()
                if k.startswith(t + "|") and sel["cell_selected"].get(k, 0) < n
            ]
            rows.append([split, t, sel["target"][t], sel["pass1"]["selected"].get(t, 0),
                         sel["selected"][t], ", ".join(short) or "none"])
    table(["Split", "Type", "Target", "After pass 1", "Selected", "Cells short (selected/target)"], rows)

    rows = []
    for split in ("train", "heldout"):
        sel = r["selection"][split]
        pool = r["valid_by_split_type_group"].get(split, {})
        for k, n in sel["cell_target"].items():
            if n and pool.get(k, 0) < HEADROOM * n:
                rows.append([split, *k.split("|"), n, pool.get(k, 0), f"{pool.get(k, 0) / n:.1f}x"])
    out.append(f"**Cells under {HEADROOM}x headroom** (valid candidates per item, by split, type and group)\n")
    if rows:
        table(["Split", "Type", "Group", "Target", "Valid", "Multiple"], rows)
    else:
        out.append("None.\n")

    out.append("**Route length** (tool calls in the cheapest route, valid candidates)\n")
    table(["Calls", "Candidates"], [[k, v] for k, v in r["route_calls"].items()])

    nf: Counter = Counter()
    for v in r["name_failures"].values():
        nf.update(v)
    out.append("**Name-rule failures** (candidates dropped, by reason): "
               + ", ".join(f"{k} {v}" for k, v in sorted(nf.items())) + "\n")
    df: Counter = Counter()
    for v in r.get("domain_failures", {}).values():
        df.update(v)
    out.append("**Domain-filter drops** (candidates, by reason): "
               + (", ".join(f"{k} {v}" for k, v in sorted(df.items())) or "none")
               + f"; HPO-excluded store entities: {r.get('hpo_excluded_store_entities', 0)}\n")
    tm, va = r.get("typed_mentions_by_answerable", {}), r.get("valid_by_answerable", {})
    out.append("**Typed mentions** (valid candidates naming at least one entity with its type, by "
               "answerability): " + (", ".join(f"{k} {tm.get(k, 0)} of {n}" for k, n in sorted(va.items())) or "none")
               + "\n")
    return "\n".join(out)


def feasibility(store, edge_ids: list[str], seeds: list[dict], fg: qc.FullGraph, build_id: str,
                cap: int = NEAR_MISS_CAP, tool_config: KGToolsetConfig | None = None,
                filters: Filters | None = None, plan: Plan = PLAN) -> dict:
    """Generate, check, split and select; return aggregate counts only.

    Args:
        store: the evidence store.
        edge_ids: every edge ID in the store.
        seeds: the store's seed records (``id`` and ``group``), from ``build.json``.
        fg: full-graph absence checks.
        build_id: the store's build ID.
        cap: most near-miss candidates per (template, seed) for absence templates.
        tool_config: the tool config routes are planned under (default: the run's).
        filters: the domain filters and the name rule (``Filters.none()`` for the
            design-stage behavior).
        plan: targets and selection rules.
    """
    t0 = time.perf_counter()
    tool_config = tool_config or KGToolsetConfig()
    pool, _, _ = build_pool(store, edge_ids, seeds, fg, build_id, split_seed=plan.split_seed,
                            heldout_share=plan.heldout_share, generator_version=GENERATOR_VERSION,
                            filters=filters, cap=cap, tool_config=tool_config)
    print(f"[{time.perf_counter() - t0:.0f}s] {len(pool.valid)} pass every check", file=sys.stderr)
    _, sim = select_splits(pool.valid, plan)
    by_split: dict[str, list[Cand]] = {"train": [], "heldout": []}
    for c in pool.valid:
        by_split[c.item.split].append(c)

    def agg(cs: Iterable[Cand], key) -> dict:
        out: Counter = Counter()
        for c in cs:
            out[key(c)] += 1
        return {"|".join(k) if isinstance(k, tuple) else k: v for k, v in sorted(out.items())}

    return {
        "build_id": build_id,
        "generator": GENERATOR_VERSION,
        "split_seed": plan.split_seed,
        "heldout_share": plan.heldout_share,
        "filters": (filters or Filters()).as_json(),
        "params": {"K_LIST": K_LIST, "K_LIST2": K_LIST2, "K_BRIDGE": K_BRIDGE, "cap": cap,
                   "relation_cap": plan.relation_cap, "fp_cap_per_kind": plan.fp_cap_per_kind, "headroom": plan.headroom, "group_share": plan.group_share,
                   "fp_correctable_share": plan.fp_correctable_share, "text_dup_jaccard": plan.text_dup_jaccard},
        "tool_config": {"max_calls": tool_config.max_calls, "max_results": tool_config.max_results,
                        "paginate": tool_config.paginate},
        "targets": targets(plan),
        "funnel": {t: {s: pool.funnel[t][s] for s in STAGES} for t in sorted(pool.funnel)},
        "name_failures": {t: dict(v) for t, v in sorted(pool.name_fail.items())},
        "domain_failures": {t: dict(v) for t, v in sorted(pool.domain_fail.items())},
        "gold_completeness_failures": {t: dict(v) for t, v in sorted(pool.gold_fail.items())},
        "hpo_excluded_store_entities": pool.excluded_entities,
        "typed_mentions_by_answerable": dict(sorted(pool.typed_by_answerable.items())),
        "valid_by_answerable": dict(sorted(pool.valid_by_answerable.items())),
        "route_calls": dict(sorted(pool.route_calls.items())),
        "valid_by_split_type": {s: agg(cs, subtype) for s, cs in sorted(by_split.items())},
        "valid_by_split_type_group": {
            s: agg(cs, lambda c: (subtype(c), c.group)) for s, cs in sorted(by_split.items())
        },
        "valid_by_split_type_group_relation": {
            s: agg(cs, lambda c: (subtype(c), c.group, c.tpl.relation_key)) for s, cs in sorted(by_split.items())
        },
        "valid_by_split_type_relation": {
            s: agg(cs, lambda c: (subtype(c), c.tpl.relation_key)) for s, cs in sorted(by_split.items())
        },
        "selection": sim,
        "seconds": round(time.perf_counter() - t0, 1),
    }


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("store", type=Path)
    ap.add_argument("duckdb", type=Path)
    ap.add_argument("--cap", type=int, default=NEAR_MISS_CAP,
                    help="most near-miss candidates per (template, seed) for absence templates")
    ap.add_argument("--rules", choices=("current", "revision1", "design"), default="current")
    ap.add_argument("--no-filters", action="store_true", help="same as --rules design")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--from-json", type=Path, default=None,
                    help="print the markdown tables from an earlier result and exit")
    args = ap.parse_args()
    if args.from_json:
        print(markdown(json.loads(args.from_json.read_text(encoding="utf-8"))))
        return

    meta = sf.read_build_meta(args.store)
    store = load_store(args.store)
    edge_ids = [r["id"] for r in sf.read_jsonl(args.store / sf.EDGES)]
    index_of = {r["id"]: r["primekg_index"] for r in sf.read_jsonl(args.store / sf.ENTITY_SOURCES)}
    fg = qc.FullGraph(duckdb.connect(str(args.duckdb), read_only=True), index_of, HIERARCHY_CHILD_END)
    rules = "design" if args.no_filters else args.rules
    filters = {"current": Filters(), "revision1": Filters.revision1(), "design": Filters.none()}[rules]
    plan = Plan(fp_cap_per_kind=rules == "current")
    result = feasibility(store, edge_ids, meta["config"]["seeds"], fg, meta["build_id"], args.cap,
                         filters=filters, plan=plan)
    result["rules"] = rules
    out = args.out or args.duckdb.parent / "qgen_feasibility.json"
    out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out}", file=sys.stderr)
    print(markdown(result))


if __name__ == "__main__":
    main()
