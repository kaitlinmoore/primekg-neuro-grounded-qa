"""Export a fixed random sample of store edges, next to their raw ``kg.csv`` rows, for eyeballing.

Not validation labels: the sample exists so a person can look at a few dozen edges
and see whether the build looks right (names, relation, direction, the raw row each
came from). It's written into the store directory (data, never committed).

For each sampled edge: evidence ID, subject and object (ID and name), relation and
display relation, ``directed``, and the raw rows. The **first** raw row in file order
is shown in full (it's the one whose orientation the builder uses for hierarchy
relations); the reverse copy's line number is listed beside it. Line numbers count
the header as line 1, so data row ``n`` is on line ``n + 1``.

Run from the repo root::

    uv run python scripts/data/spot_check.py "$NGQ_DATA_DIR/store/primekg-neuro-c95"
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import raw_dir  # noqa: E402

from neuro_grounded_qa.data import build  # noqa: E402
from neuro_grounded_qa.data import store_files as sf  # noqa: E402

SAMPLE_SEED = 20261119
SAMPLE_SIZE = 50


def cell(text: str) -> str:
    """Make text safe inside a markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("store", type=Path)
    p.add_argument("--raw", type=Path, default=raw_dir("primekg"))
    p.add_argument("--n", type=int, default=SAMPLE_SIZE)
    p.add_argument("--seed", type=int, default=SAMPLE_SEED)
    args = p.parse_args()

    meta = sf.read_build_meta(args.store)
    edges = sf.read_jsonl(args.store / sf.EDGES)
    sources = {e["id"]: e for e in sf.read_jsonl(args.store / sf.EDGE_SOURCES)}
    names = {e["id"]: e["name"] for e in sf.read_jsonl(args.store / sf.ENTITIES)}
    sample = sorted(random.Random(args.seed).sample(edges, args.n), key=lambda e: e["id"])

    lines_wanted = {min(sources[e["id"]]["kg_csv_rows"]) + 1 for e in sample}
    raw_lines: dict[int, str] = {}
    with (args.raw / build.KG_CSV).open(encoding="utf-8", newline="") as f:
        for lineno, line in enumerate(f, start=1):
            if lineno == 1:
                header = line.rstrip("\r\n")
            elif lineno in lines_wanted:
                raw_lines[lineno] = line.rstrip("\r\n")

    out = [
        f"# Spot-check sample: {meta['build_id']}",
        "",
        f"{args.n} edges drawn with `random.Random({args.seed}).sample` from {len(edges):,} store edges, "
        "sorted by evidence ID. For eyeballing the build, not validation labels.",
        "",
        "Direction: hierarchy relations are stored subject = child, object = parent (`directed` = yes). "
        "For them the first raw row's order is parent, child, except `anatomy_anatomy`, whose first row "
        "is child, parent. Every other edge is undirected; its subject/object order is only for display.",
        "",
        f"`kg.csv` header (line 1): `{header}`",
        "",
        "| # | Evidence ID | Subject | Relation (display) | Object | Directed | First raw row (`kg.csv` line) | Reverse copy line |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, e in enumerate(sample, start=1):
        rows = sorted(sources[e["id"]]["kg_csv_rows"])
        first = rows[0] + 1
        out.append(
            f"| {i} | `{e['id']}` | `{e['subject']}` {cell(names[e['subject']])} | "
            f"`{e['relation']}` ({cell(e['display_relation'] or '')}) | "
            f"`{e['object']}` {cell(names[e['object']])} | {'yes' if e['directed'] else 'no'} | "
            f"{first}: `{cell(raw_lines[first])}` | {rows[1] + 1} |"
        )
    target = args.store / "spot_check.md"
    target.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
