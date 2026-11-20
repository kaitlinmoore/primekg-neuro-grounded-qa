"""Pick a small, seeded, stratified sample of **train** items for a model run.

    uv run python scripts/analysis/select_items.py \\
        --items-dir "$NGQ_DATA_DIR/items/<build_id>" --per-type 2 --seed ngq-smoke-v1 \\
        --out outputs/smoke-items-<date>

Reads ``train.jsonl`` only, through the train-only loader (``qgen.view.load_train``),
which refuses a file holding any non-train record. Held-out items are never read.

For each question type (``qtype``), in sorted order, it sorts that type's item IDs
and draws ``--per-type`` of them with ``random.Random(f"{seed}:{qtype}")``. The draw
depends only on the seed, the type and the set of IDs, so it is stable across runs
and doesn't shift when another type's items change.

With ``--templates T1 T2 ...`` it keeps only items of those template IDs and
stratifies by template instead of by type: ``--per-type`` items from each named
template, seeded with ``f"{seed}:{template_id}"``. A template with fewer items gives
all it has, and ``selection.json`` records how many each had. A named template with
no train items is an error, so a typo can't silently shrink the sample.

    uv run python scripts/analysis/select_items.py \\
        --items-dir "$NGQ_DATA_DIR/items/<build_id>" --per-type 2 --seed ngq-bridge-v1 \\
        --templates mh_bridge.shared_phenotype un_bridge.shared_protein \\
        --out outputs/bridge-items-<date>

Writes, under ``--out``:

- ``items.jsonl``: the chosen items, one JSON object per line, exactly as they
  appear in ``train.jsonl`` (pass this file to ``scripts/run.sh`` with
  ``--env.taskset.items_path``);
- ``selection.json``: the seed, the source file's SHA-256, the chosen IDs per type
  (or per template), and the output file's SHA-256.

Prints IDs, types and template IDs only, never question text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "data"))

from qgen.view import load_train  # noqa: E402


def sha256_file(path: Path) -> str:
    """Hex SHA-256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stratified_ids(by_type: dict[str, list[str]], per_type: int, seed: str) -> dict[str, list[str]]:
    """Draw ``per_type`` IDs from each type, seeded per type.

    Args:
        by_type: item IDs per question type.
        per_type: how many to draw from each type; a type with fewer items gives all.
        seed: the run's seed string.

    Returns:
        The chosen IDs per type, each list sorted, types in sorted order.
    """
    out: dict[str, list[str]] = {}
    for qtype in sorted(by_type):
        ids = sorted(by_type[qtype])
        rng = random.Random(f"{seed}:{qtype}")
        out[qtype] = sorted(rng.sample(ids, min(per_type, len(ids))))
    return out


def select(
    items_dir: Path,
    per_type: int,
    seed: str,
    out_dir: Path,
    templates: list[str] | None = None,
) -> dict:
    """Select, write ``items.jsonl`` and ``selection.json``, and return the selection.

    Args:
        items_dir: the item-set directory; only its ``train.jsonl`` is read.
        per_type: items to draw per stratum (type, or template with ``templates``).
        seed: the seed string.
        out_dir: where to write.
        templates: if given, keep only these template IDs and stratify by template.

    Raises:
        ValueError: a named template has no train items.
    """
    train_file = items_dir / "train.jsonl"
    items = load_train(items_dir)  # refuses anything that isn't train
    by_type: dict[str, list[str]] = defaultdict(list)
    if templates:
        wanted_templates = set(templates)
        for item in items:
            if item.template_id in wanted_templates:
                by_type[item.template_id].append(item.id)
        missing = sorted(wanted_templates - by_type.keys())
        if missing:
            raise ValueError(f"no train items for template(s): {', '.join(missing)}")
    else:
        for item in items:
            by_type[item.qtype].append(item.id)
    chosen = stratified_ids(by_type, per_type, seed)
    wanted = {i for ids in chosen.values() for i in ids}

    # Copy the chosen lines byte for byte, in file order.
    lines = [
        line for line in train_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line)["id"] in wanted
    ]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "items.jsonl"
    out_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    template = {item.id: item.template_id for item in items}
    selection = {
        "seed": seed,
        "per_type": per_type,
        "stratify_by": "template_id" if templates else "qtype",
        "available": {k: len(v) for k, v in sorted(by_type.items())},
        "source": str(train_file),
        "source_sha256": sha256_file(train_file),
        "build_id": items[0].build_id,
        "chosen": {q: [{"id": i, "template_id": template[i]} for i in ids] for q, ids in chosen.items()},
        "n_items": len(lines),
        "items_sha256": sha256_file(out_file),
    }
    (out_dir / "selection.json").write_text(json.dumps(selection, indent=2) + "\n", encoding="utf-8")
    return selection


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--items-dir", type=Path, required=True,
                    help="item-set directory ($NGQ_DATA_DIR/items/<build_id>); only its train.jsonl is read")
    ap.add_argument("--per-type", type=int, default=2)
    ap.add_argument("--seed", default="ngq-smoke-v1")
    ap.add_argument("--out", type=Path, required=True, help="output directory")
    ap.add_argument("--templates", nargs="+", default=None, metavar="TEMPLATE_ID",
                    help="keep only these template IDs and draw --per-type from each")
    args = ap.parse_args(argv)
    sel = select(args.items_dir, args.per_type, args.seed, args.out, args.templates)
    for group, rows in sel["chosen"].items():
        if len(rows) < args.per_type:
            print(f"{group}: only {len(rows)} train item(s), taking all")
        for row in rows:
            print(f"{group:14} {row['id']}  {row['template_id']}")
    print(f"{sel['n_items']} items -> {args.out / 'items.jsonl'} (sha256 {sel['items_sha256'][:12]}…)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
