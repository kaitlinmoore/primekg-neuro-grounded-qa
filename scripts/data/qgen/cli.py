"""The question-set commands. Every command prints counts, hashes and paths only.

Run from the repo root, through the thin entry script::

    uv run python scripts/data/question_set.py generate
    uv run python scripts/data/question_set.py check --build-id <build_id>
    uv run python scripts/data/question_set.py check-heldout --build-id <build_id>
    uv run python scripts/data/question_set.py view --build-id <build_id> --qtype partial
    uv run python scripts/data/question_set.py spot-check --build-id <build_id>

Commands take an items **directory** (``--items``) or a ``--build-id`` (which means
``$NGQ_DATA_DIR/items/<build_id>``), never a held-out file path. That keeps every
command runnable under the project's deny rules, which block ``python``/``uv run``
commands that name the held-out file.

Defaults: the store is ``$NGQ_DATA_DIR/store/primekg-neuro-c95`` and the full graph
``$NGQ_DATA_DIR/derived/primekg.duckdb``.

``generate`` and ``check`` hold held-out items in memory. They run under
``safety.no_text_out``: logging off, warnings counted, every error reduced to its
stage and type. ``view`` and ``spot-check`` read the train file only.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import paths
import qgen_templates as qt
from neuro_grounded_qa.data import store_files as sf
from neuro_grounded_qa.data.build import HIERARCHY_CHILD_END
from neuro_grounded_qa.tools import KGToolset, KGToolsetConfig, load_store

from qgen import GENERATOR_VERSION
from qgen import checks as qc
from qgen import files as F
from qgen.candidates import NEAR_MISS_CAP, STAGES, Filters, build_pool
from qgen.safety import ItemsError, no_text_out, warning_summary
from qgen.selection import Plan, select_splits, shortfalls, subtype
from qgen.verify import Verifier, verify
from qgen.view import SPOT_SEED, load_train, render, select_items, spot_check_sample

DEFAULT_STORE = "store/primekg-neuro-c95"
DEFAULT_FULL_GRAPH = "derived/primekg.duckdb"


def _say(msg: str) -> None:
    print(msg, flush=True)


def open_store(store_dir: Path):
    """(store, build.json meta, edge IDs, entity -> graph index) for a store directory."""
    if not sf.is_store_dir(store_dir):
        raise ItemsError(f"not a store directory (no build.json): {store_dir}")
    meta = sf.read_build_meta(store_dir)
    store = load_store(store_dir)
    if store.build_id != meta["build_id"]:
        raise ItemsError("store build_id differs from build.json")
    edge_ids = [r["id"] for r in sf.read_jsonl(store_dir / sf.EDGES)]
    index_of = {r["id"]: r["primekg_index"] for r in sf.read_jsonl(store_dir / sf.ENTITY_SOURCES)}
    return store, meta, edge_ids, index_of


def open_full_graph(path: Path, index_of: dict[str, str]) -> qc.FullGraph:
    import duckdb

    if not path.is_file():
        raise ItemsError(f"full graph not found: {path}")
    return qc.FullGraph(duckdb.connect(str(path), read_only=True), index_of, HIERARCHY_CHILD_END)


def resolve_items_dir(args) -> Path:
    """The items directory from ``--items`` or ``--build-id``. A file path is refused
    unless the command accepts the train file itself."""
    if args.items is not None:
        d = Path(args.items).expanduser()
    elif args.build_id:
        d = F.items_dir(paths.data_dir() / "items", args.build_id)
    else:
        raise ItemsError("pass --items <items directory> or --build-id <build_id>")
    return d


def _dir_only(d: Path) -> Path:
    if not d.is_dir():
        raise ItemsError(f"pass an items directory, not a file: {d.name!r}")
    return d


# --------------------------------------------------------------------------------------
# generate
# --------------------------------------------------------------------------------------


def cmd_generate(args) -> int:
    with no_text_out("generate") as caught:
        store_dir = Path(args.store).expanduser()
        store, meta, edge_ids, index_of = open_store(store_dir)
        build_id = meta["build_id"]
        out = Path(args.out).expanduser() if args.out else F.items_dir(paths.data_dir() / "items", build_id)
        if out.name != build_id:
            raise ItemsError(f"--out must be a directory named after the store's build_id {build_id!r}")
        existing = [n for n in (F.TRAIN_FILE, F.HELDOUT_FILE, F.MANIFEST, F.SPOT_CHECK) if (out / n).exists()]
        if existing and not args.force:
            raise ItemsError(f"{out} already holds an item set; pass --force to replace it")
        fg = open_full_graph(Path(args.full_graph).expanduser(), index_of)
        plan = Plan(total={"train": args.train, "heldout": args.heldout})
        filters = Filters.none() if args.no_filters else Filters()
        tool_config = KGToolsetConfig(max_results=args.max_results, max_calls=args.max_calls)
        _say(f"store {build_id}: {len(edge_ids)} edges, {len(meta['config']['seeds'])} seeds")

        pool, _, _ = build_pool(
            store, edge_ids, meta["config"]["seeds"], fg, build_id,
            split_seed=plan.split_seed, heldout_share=plan.heldout_share,
            generator_version=GENERATOR_VERSION, filters=filters, cap=args.cap, tool_config=tool_config,
        )
        generated = sum(f["generated"] for f in pool.funnel.values())
        _say(f"candidates: {generated} generated, {len(pool.valid)} pass every check")
        chosen, sel_stats = select_splits(pool.valid, plan)
        short = shortfalls(sel_stats)
        for split in ("train", "heldout"):
            c = Counter(subtype(x) for x in chosen[split])
            _say(f"selected {split}: {len(chosen[split])} of {plan.total[split]} ("
                 + ", ".join(f"{t} {c[t]}/{n}" for t, n in sel_stats[split]["target"].items()) + ")")
        if short and not args.allow_shortfall:
            raise ItemsError("targets not met (see the counts above); nothing written")

        items = {s: [x.item for x in chosen[s]] for s in ("train", "heldout")}
        all_items = items["train"] + items["heldout"]
        F.assert_build_ids_agree(out, store.build_id, all_items, build_id)
        if any(i.split != s for s in items for i in items[s]):
            raise ItemsError("an item's split field differs from its file")

        out.mkdir(parents=True, exist_ok=True)
        for n in existing:
            (out / n).unlink()
        files = {}
        for split in ("train", "heldout"):
            sha = F.write_items(out / F.SPLIT_FILES[split], items[split])
            files[F.SPLIT_FILES[split]] = {"sha256": sha, "items": len(items[split])}
        by_split_pool: dict[str, Counter] = {"train": Counter(), "heldout": Counter()}
        by_split_pool_group: dict[str, Counter] = {"train": Counter(), "heldout": Counter()}
        for c in pool.valid:
            by_split_pool[c.item.split][subtype(c)] += 1
            by_split_pool_group[c.item.split][f"{subtype(c)}|{c.group}"] += 1
        manifest = {
            "format": F.MANIFEST_FORMAT,
            "build_id": build_id,
            "store_build_json_sha256": sf.file_sha256(store_dir / sf.BUILD_JSON),
            "generator": {"version": GENERATOR_VERSION, **F.git_state()},
            "template_catalog_sha256": qt.catalog_sha256(),
            "split": {"seed": plan.split_seed, "heldout_share": plan.heldout_share,
                      "rule": "heldout iff sha256(seed \\x1f item_id)[:8] / 2^64 < heldout_share"},
            "plan": plan.as_json(),
            "filters": filters.as_json(),
            "near_miss_cap": args.cap,
            "tool_config": {"max_calls": tool_config.max_calls, "max_results": tool_config.max_results,
                            "paginate": tool_config.paginate, "max_payload_bytes": tool_config.max_payload_bytes},
            "files": files,
            "counts": {s: F.item_counts(items[s]) for s in ("train", "heldout")},
            "selection": sel_stats,
            "pool": {
                "generated": generated,
                "valid": len(pool.valid),
                "valid_by_split_type": {s: dict(sorted(v.items())) for s, v in by_split_pool.items()},
                "valid_by_split_type_group": {s: dict(sorted(v.items())) for s, v in by_split_pool_group.items()},
                "funnel": {t: {s: pool.funnel[t][s] for s in STAGES} for t in sorted(pool.funnel)},
                "name_failures": {t: dict(v) for t, v in sorted(pool.name_fail.items())},
                "domain_failures": {t: dict(v) for t, v in sorted(pool.domain_fail.items())},
                "hpo_excluded_store_entities": pool.excluded_entities,
                "typed_mentions_by_answerable": dict(sorted(pool.typed_by_answerable.items())),
                "valid_by_answerable": dict(sorted(pool.valid_by_answerable.items())),
                "route_calls": dict(sorted(pool.route_calls.items())),
            },
            "shortfalls": {s: {t: list(v) for t, v in d.items()} for s, d in short.items()},
        }
        msha = F.write_manifest(out, manifest)
        for name, f in files.items():
            _say(f"wrote {out / name}: {f['items']} items, sha256 {f['sha256']}")
        _say(f"wrote {out / F.MANIFEST}: sha256 {msha}")
        _say(warning_summary(caught))
    return 0


# --------------------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------------------


def cmd_check(args, splits: list[str]) -> int:
    with no_text_out("check") as caught:
        d = _dir_only(resolve_items_dir(args))
        manifest = F.read_manifest(d)
        store_dir = Path(args.store).expanduser()
        store, _, edge_ids, index_of = open_store(store_dir)
        if manifest["build_id"] != store.build_id or d.name != store.build_id:
            raise ItemsError(f"build_id mismatch: directory {d.name!r}, manifest {manifest['build_id']!r}, "
                             f"store {store.build_id!r}")
        fg = open_full_graph(Path(args.full_graph).expanduser(), index_of)
        tc = manifest["tool_config"]
        ts = KGToolset(KGToolsetConfig(max_results=tc["max_results"], max_calls=tc["max_calls"],
                                       paginate=tc["paginate"], max_payload_bytes=tc["max_payload_bytes"]),
                       store=store)
        directed = qc.relation_directedness(store.get(e) for e in edge_ids)
        v = Verifier(store, qc.Prober(ts), fg, manifest, directed)
        report = verify(d, splits, v)
        report["store_build_json_sha256_matches"] = (
            sf.file_sha256(store_dir / sf.BUILD_JSON) == manifest["store_build_json_sha256"])
        report["template_catalog_sha256_matches"] = qt.catalog_sha256() == manifest["template_catalog_sha256"]
        report["ok"] = report["ok"] and report["store_build_json_sha256_matches"]
        for split, r in report["splits"].items():
            failed = {k: c["fail"] for k, c in r["item_checks"].items() if c["fail"]}
            badfile = [k for k, ok in r["file_checks"].items() if not ok]
            _say(f"{split}: {r['items']} items, sha256 {r['sha256']}: "
                 + ("PASS" if r["ok"] else f"FAIL (item checks failed: {failed or 'none'}; file checks failed: "
                    f"{badfile or 'none'})"))
        if "cross_split" in report:
            _say("cross-split: " + ", ".join(f"{k} {'yes' if ok else 'NO'}" for k, ok in report["cross_split"].items()))
        _say(f"store build.json matches manifest: {report['store_build_json_sha256_matches']}; "
             f"template catalog matches manifest: {report['template_catalog_sha256_matches']}")
        if args.json:
            Path(args.json).write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
            _say(f"wrote {args.json}")
        _say(warning_summary(caught))
        _say("OK" if report["ok"] else "FAILED")
    return 0 if report["ok"] else 1


# --------------------------------------------------------------------------------------
# view and spot-check (train only)
# --------------------------------------------------------------------------------------


def _maybe_store(path: str | None):
    if not path:
        return None
    return load_store(Path(path).expanduser())


def cmd_view(args) -> int:
    with no_text_out("view"):
        items = load_train(resolve_items_dir(args))
        store = _maybe_store(args.store)
    picked = select_items(items, qtype=args.qtype, answerable=args.answerable, template=args.template,
                          group=args.group, ids=args.id or (), contains=args.contains)
    if args.limit:
        picked = picked[: args.limit]
    text = render(picked, store)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}: {len(picked)} train items", file=sys.stderr)
    else:
        print(text)
    return 0


def cmd_spot_check(args) -> int:
    with no_text_out("spot-check"):
        d = _dir_only(resolve_items_dir(args))
        items = load_train(d)
        store = _maybe_store(args.store)
    sample = spot_check_sample(items, args.n, args.seed)
    title = (f"Train spot check: {len(sample)} of {len(items)} train items, seed {args.seed!r}, "
             "stratified by type")
    out = d / F.SPOT_CHECK
    out.write_text(render(sample, store, title=title), encoding="utf-8")
    print(f"wrote {out}: {len(sample)} train items", file=sys.stderr)
    return 0


# --------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="question_set.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    data = paths.data_dir()

    def store_args(p, full_graph: bool = True) -> None:
        p.add_argument("--store", default=str(data / DEFAULT_STORE), help="store directory")
        if full_graph:
            p.add_argument("--full-graph", default=str(data / DEFAULT_FULL_GRAPH), help="full-graph duckdb")

    def items_args(p) -> None:
        p.add_argument("--items", default=None, help="items directory (<items root>/<build_id>)")
        p.add_argument("--build-id", default=None, help="means $NGQ_DATA_DIR/items/<build_id>")

    g = sub.add_parser("generate", help="generate, split, select and write the item set")
    store_args(g)
    g.add_argument("--out", default=None, help="output directory, named after the build_id "
                                               "(default $NGQ_DATA_DIR/items/<build_id>)")
    g.add_argument("--train", type=int, default=200)
    g.add_argument("--heldout", type=int, default=100)
    g.add_argument("--cap", type=int, default=NEAR_MISS_CAP,
                   help="most near-miss candidates per (template, seed) for absence templates")
    g.add_argument("--max-results", type=int, default=KGToolsetConfig().max_results)
    g.add_argument("--max-calls", type=int, default=KGToolsetConfig().max_calls)
    g.add_argument("--no-filters", action="store_true",
                   help="design-stage rules: no domain filters, strict names (exploration only)")
    g.add_argument("--allow-shortfall", action="store_true", help="write even if a target is not met")
    g.add_argument("--force", action="store_true", help="replace an existing item set")

    for name, helptext in (("check", "re-check both splits (counts only)"),
                           ("check-heldout", "re-check the held-out split (counts and hashes only)")):
        c = sub.add_parser(name, help=helptext)
        store_args(c)
        items_args(c)
        if name == "check":
            c.add_argument("--split", choices=("train", "heldout", "both"), default="both")
        c.add_argument("--json", default=None, help="also write the counts-only report here")

    v = sub.add_parser("view", help="render train items as markdown (refuses held-out)")
    items_args(v)
    v.add_argument("--store", default=None, help="store directory, to show entity names")
    v.add_argument("--qtype")
    v.add_argument("--answerable")
    v.add_argument("--template", help="template ID or family (sh_evid, mh_bridge, ...)")
    v.add_argument("--group")
    v.add_argument("--id", action="append")
    v.add_argument("--contains", help="text in the question (case-insensitive)")
    v.add_argument("--limit", type=int, default=0)
    v.add_argument("--out", default=None, help="write the markdown here instead of stdout")

    s = sub.add_parser("spot-check", help="write a seeded, type-stratified train sample for review")
    items_args(s)
    s.add_argument("--store", default=str(data / DEFAULT_STORE), help="store directory, for names")
    s.add_argument("--n", type=int, default=20)
    s.add_argument("--seed", default=SPOT_SEED)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.cmd == "generate":
        return cmd_generate(args)
    if args.cmd == "check":
        return cmd_check(args, ["train", "heldout"] if args.split == "both" else [args.split])
    if args.cmd == "check-heldout":
        return cmd_check(args, ["heldout"])
    if args.cmd == "view":
        return cmd_view(args)
    return cmd_spot_check(args)
