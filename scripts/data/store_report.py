"""Reachability of single-hop facts through the real tools, for the pagination decision.

Loads a built store the way the tool server does (``tools.store.load_store``) and
calls the real ``get_neighbors`` tool body (``KGToolset._neighbors``, so the result
cap **and** the payload-size cap apply exactly as in a run, with the default
``KGToolsetConfig``: 20 results, 16,000 bytes, no pagination).

A **single-hop fact** is a store edge with a seed node at one end; its **partner**
is the other end. For each seed node and relation, with fan-out ``n`` (the seed's
facts of that relation), it reports the share of those facts that one call returns:

- ``seed``: ``get_neighbors(seed)``, no relation filter;
- ``seed_filtered``: ``get_neighbors(seed, relation)``;
- ``partner``: ``get_neighbors(partner)``, no filter;
- ``partner_filtered``: ``get_neighbors(partner, relation)``;
- ``any``: by at least one of the four.

A fact between two seed nodes counts under each seed, and from either end. The
store has no pagination, so "reachable" means "in the first page". Edges come back
in evidence-ID order, which is (relation, subject, object) order, so an unfiltered
first page holds the alphabetically first relations.

Writes ``reachability.json`` to the store directory and prints the markdown tables
used in ``docs/question_set_design.md`` ("Evidence store"). Run from the repo root::

    uv run python scripts/data/store_report.py "$NGQ_DATA_DIR/store/primekg-neuro-c95"
"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

from neuro_grounded_qa.contracts import ToolResultPayload
from neuro_grounded_qa.data import store_files as sf
from neuro_grounded_qa.tools import KGToolset, KGToolsetConfig, load_store

ROUTES = ("seed", "seed_filtered", "partner", "partner_filtered")


class Pager:
    """First pages of ``get_neighbors``, cached, with payload stats."""

    def __init__(self, toolset: KGToolset) -> None:
        self.ts = toolset
        self.cache: dict[tuple[str, str | None], set[str]] = {}
        self.calls = 0
        self.max_bytes = 0
        self.cut_by_payload = 0
        self.seconds = 0.0

    def page(self, entity: str, relation: str | None) -> set[str]:
        key = (entity, relation)
        if key not in self.cache:
            t = time.perf_counter()
            out = self.ts._neighbors(entity, relation, 0, paged=False)
            self.seconds += time.perf_counter() - t
            self.calls += 1
            payload = ToolResultPayload.loads(out)
            ids = {e.id for e in payload.evidence}
            self.max_bytes = max(self.max_bytes, len(out.encode("utf-8")))
            if payload.truncated and len(ids) < self.ts.config.max_results:
                self.cut_by_payload += 1
            self.cache[key] = ids
        return self.cache[key]


def reachability(store_dir: Path) -> dict:
    meta = sf.read_build_meta(store_dir)
    t = time.perf_counter()
    store = load_store(store_dir)
    load_s = time.perf_counter() - t
    ts = KGToolset(KGToolsetConfig(), store=store)
    pager = Pager(ts)
    seeds = {s["id"]: s for s in meta["config"]["seeds"]}

    facts = []  # (seed, relation, partner, edge id)
    for line in (store_dir / sf.EDGES).open(encoding="utf-8"):
        e = json.loads(line)
        for s_end, p_end in (("subject", "object"), ("object", "subject")):
            if e[s_end] in seeds:
                facts.append((e[s_end], e["relation"], e[p_end], e["id"]))

    groups: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for s, r, p, eid in facts:
        groups[(s, r)].append((p, eid))

    rows, reached_any_ids, by_rel = [], set(), defaultdict(lambda: {"facts": set(), **{k: set() for k in (*ROUTES, "any")}})
    for (s, r), items in sorted(groups.items(), key=lambda kv: (seeds[kv[0][0]]["group"], kv[0][0], kv[0][1])):
        hit = {k: set() for k in ROUTES}
        for p, eid in items:
            if eid in pager.page(s, None):
                hit["seed"].add(eid)
            if eid in pager.page(s, r):
                hit["seed_filtered"].add(eid)
            if eid in pager.page(p, None):
                hit["partner"].add(eid)
            if eid in pager.page(p, r):
                hit["partner_filtered"].add(eid)
        ids = {eid for _p, eid in items}
        anyset = set().union(*hit.values())
        n = len(ids)
        rows.append({"seed_id": s, "name": seeds[s]["name"], "group": seeds[s]["group"], "relation": r,
                     "fan_out": n, **{k: len(v) for k, v in hit.items()}, "any": len(anyset)})
        reached_any_ids |= anyset
        agg = by_rel[r]
        agg["facts"] |= ids
        for k in ROUTES:
            agg[k] |= hit[k]
        agg["any"] |= anyset

    all_ids = {eid for *_x, eid in facts}
    by_route_total = {k: len(set().union(*(by_rel[r][k] for r in by_rel))) for k in (*ROUTES, "any")}
    return {
        "build_id": meta["build_id"],
        "config": {"max_results": ts.config.max_results, "max_payload_bytes": ts.config.max_payload_bytes,
                   "paginate": ts.config.paginate},
        "load_seconds": round(load_s, 2),
        "tool_calls_made": pager.calls,
        "mean_call_ms": round(1000 * pager.seconds / max(pager.calls, 1), 2),
        "largest_payload_bytes": pager.max_bytes,
        "pages_cut_by_payload_size": pager.cut_by_payload,
        "single_hop_facts": len(all_ids),
        "reached": by_route_total,
        "by_relation": {
            r: {"facts": len(v["facts"]), **{k: len(v[k]) for k in (*ROUTES, "any")}}
            for r, v in sorted(by_rel.items(), key=lambda kv: -len(kv[1]["facts"]))
        },
        "by_seed_relation": rows,
    }


def pct(a: int, n: int) -> str:
    return f"{100 * a / n:.0f}%" if n else "n/a"


def markdown(res: dict) -> str:
    n = res["single_hop_facts"]
    out = [
        "| Route | Facts reachable in one call | Share |",
        "|---|---|---|",
    ]
    names = {"seed": "seed end, no filter", "seed_filtered": "seed end, relation filter",
             "partner": "partner end, no filter", "partner_filtered": "partner end, relation filter",
             "any": "any of the four"}
    for k, label in names.items():
        out.append(f"| {label} | {res['reached'][k]:,} of {n:,} | {pct(res['reached'][k], n)} |")
    out += ["", "**By relation** (all seeds; a fact counts once)", "",
            "| Relation | Facts | Seed end | Seed end, filter | Partner end | Partner end, filter | Any |",
            "|---|---|---|---|---|---|---|"]
    for r, v in res["by_relation"].items():
        f = v["facts"]
        out.append(f"| `{r}` | {f:,} | " + " | ".join(pct(v[k], f) for k in (*ROUTES, "any")) + " |")
    out += ["", "**By seed node and relation** (fan-out = the seed's facts of that relation)", "",
            "| Group | Seed | Relation | Fan-out | Seed end | Seed end, filter | Partner end | Partner end, filter | Any |",
            "|---|---|---|---|---|---|---|---|---|"]
    for row in res["by_seed_relation"]:
        f = row["fan_out"]
        out.append(f"| {row['group']} | `{row['seed_id']}` {row['name']} | `{row['relation']}` | {f} | "
                   + " | ".join(pct(row[k], f) for k in (*ROUTES, "any")) + " |")
    return "\n".join(out)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("store", type=Path)
    args = p.parse_args()
    res = reachability(args.store)
    (args.store / "reachability.json").write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    summary = {k: v for k, v in res.items() if k not in ("by_relation", "by_seed_relation")}
    print(json.dumps(summary, indent=1))
    print()
    print(markdown(res))


if __name__ == "__main__":
    main()
