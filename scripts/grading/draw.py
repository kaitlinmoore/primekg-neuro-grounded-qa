"""Draw the blind grading sample: a seeded, stratified set of real traces.

    uv run python scripts/grading/draw.py \\
        --items $NGQ_DATA_DIR/items/<build_id> \\
        --runs outputs/<haiku-run>/pass-a outputs/<sonnet-run>/c1 ... \\
        [--dir $NGQ_DATA_DIR/grading/round-first]

What it does, in order:

1. Reads the train items through ``qgen.view.load_train`` (the train-only loader; it
   refuses any other file) and every trace in the given run directories. Each trace
   must be on a train item, carry that item unchanged, and record as
   ``items_sha256`` the train file's SHA-256, or that of a selection of it whose
   ``selection.json`` names the train file as its source (how chunked runs are made);
   otherwise the draw stops.
2. Builds the **exclusion list**: every item ID (``q-`` plus 12 hex digits) named in
   the files Kaitlin has read about items and their scores (``EXCLUDE_FROM``). Only
   the IDs are taken, by pattern; the files are never summarized. Excluded items
   can't enter the sample, so the grader can't recognize a trace from the docs.
3. Fills the **cells**: (the item's ``answerable`` value, the model recorded in the
   trace), with ``--cells`` items per model for each ``answerable`` value. For each
   value, in two seeded steps:

   a. **Items.** From the value's items that are not excluded and have a trace from
      every model, it draws (cell size x number of models) items with the draw seed.
   b. **Models.** It shuffles those items with the assign seed and splits them into
      one block per model (models in sorted order). No model fills first from a
      shared pool, so the split doesn't favour one model.

   **Each item is at most once in the whole sample**, so the grader never sees two
   models answer the same question. If an item has several traces for its model, one
   is chosen by the draw seed. If any value has too few items it stops and prints the
   shortfall (counts only), writing nothing.
4. Gives each drawn trace an opaque ``grading_id`` (random hex from its own seed),
   shuffles the grading order with the order seed, and draws the **sealed validation
   subset** (``--sealed-per-answerable`` per ``answerable`` value) with the sealed
   seed.
5. Writes, into the round directory (outside the repo):

   - ``grading_key.jsonl``: one line per grading_id in grading order: ``grading_id``,
     ``position``, ``round``, ``model``, ``run`` (the run directory, relative to the
     repo), ``trace_id``, ``trace_line`` and ``trace_index`` (where the trace sits in
     ``traces.jsonl``), ``item_id`` and ``answerable``.
   - ``sealed.json``: the sealed grading_ids, as a JSON list, nothing else.
   - ``draw.json``: the seeds, the requested cell sizes, the train file's and every
     run's ``traces.jsonl`` SHA-256, the exclusion list's size and SHA-256 (of its sorted IDs, one per
     line), how many source files each pattern matched and one hash over them (no
     file names), the cell counts and the sealed counts.

It prints only pass/fail, the exclusion list's size and the files' hashes: no IDs and
no counts by cell. It refuses to overwrite an existing key file unless ``--force``
is given, since a redraw after grading has started would change the sample.

Composition is fixed by the decisions recorded in ``docs/labeling_guide.md`` (Q1):
train items only, stratified by ``answerable`` and model, no exploit-policy traces
(the runs drawn from are model runs).
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import random
import re
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grading_common import (
    DRAW_FILE,
    KEY_FILE,
    REPO,
    ROUND,
    SEALED_FILE,
    GradingError,
    atomic_write_text,
    default_round_dir,
    jsonl_text,
    refuse_heldout,
    sha256_bytes,
    sha256_file,
    write_json,
)
from neuro_grounded_qa.contracts import QuestionItem
from qgen.safety import ItemsError
from qgen.view import load_train, train_path

DRAW_SEED = "ngq-grade-first-v1"
"""Seed of the item draw. The assign, order, ID and sealed seeds are derived from it."""
ASSIGN_SEED = f"{DRAW_SEED}/assign"
ORDER_SEED = f"{DRAW_SEED}/order"
IDS_SEED = f"{DRAW_SEED}/ids"
SEALED_SEED = f"{DRAW_SEED}/sealed"

CELLS = {"correctable": 4, "no": 8, "partial": 8, "yes": 12}
"""Traces per model for each ``answerable`` value: (4 + 8 + 8 + 12) x 2 models = 64.
Unequal because the train set has only 10 ``correctable`` items (Kaitlin's decision,
2026-11-22)."""
SEALED_PER_ANSWERABLE = 5
"""Sealed traces per answerable value: 4 x 5 = 20."""

EXCLUDE_FROM = (
    "docs/methodology.md",
    "docs/reward_hacking_audit.md",
    "docs/question_set_design.md",
    "docs/changelog.md",
)
"""Repo-relative files (globs) whose item IDs are excluded from the sample: the
committed documents that discuss items and their scores (Kaitlin's decision,
2026-11-22: these four only)."""

ITEM_ID_RE = re.compile(r"\bq-[0-9a-f]{12}\b")
"""An item ID as the generator writes it (``qgen.checks.item_id``)."""

IGNORED_ITEM_FIELDS = {"idx", "system_prompt"}
"""Fields the taskset sets on a trace's copy of an item; everything else must match
the train file."""


@dataclass(frozen=True)
class TraceRef:
    """Where one trace is, and the cell it belongs to."""

    run: str
    """The run directory, relative to the repo when inside it."""
    trace_id: str
    trace_line: int
    """1-based line in ``traces.jsonl``."""
    trace_index: int
    """Index in that line's ``traces`` list."""
    model: str
    item_id: str
    answerable: str


# --- Reading ---------------------------------------------------------------------------


def rel(path: Path) -> str:
    """``path`` relative to the repo if it is inside it, else absolute."""
    path = path.resolve()
    try:
        return path.relative_to(REPO).as_posix()
    except ValueError:
        return path.as_posix()


def run_dirs(given: Sequence[Path]) -> list[Path]:
    """Run directories (each holding ``traces.jsonl``) under each given path: the path
    itself, or its subdirectories one level down."""
    out: list[Path] = []
    for p in given:
        if (p / "traces.jsonl").is_file():
            out.append(p)
            continue
        found = sorted(q.parent for q in p.glob("*/traces.jsonl"))
        if not found:
            raise GradingError(f"{p}: no traces.jsonl in it or one level down")
        out.extend(found)
    return out


def iter_saved_traces(run_dir: Path) -> Iterable[tuple[int, int, dict[str, Any]]]:
    """(line, index, saved trace) for every trace in ``run_dir/traces.jsonl``."""
    path = run_dir / "traces.jsonl"
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        for j, saved in enumerate(json.loads(line)["traces"]):
            yield lineno, j, saved


def trace_model(saved: Mapping[str, Any]) -> str:
    """The model a trace records (``agent.config.model``)."""
    model = (((saved.get("agent") or {}).get("config") or {}).get("model"))
    if not isinstance(model, str) or not model:
        raise GradingError("a trace records no model (agent.config.model)")
    return model


def selection_sha(run_dir: Path, train_sha: str) -> str | None:
    """The SHA-256 of the items file a run read, if that file is a selection of the
    train file: ``scripts/analysis/select_items.py`` writes ``selection.json`` next to
    it, recording the file's own hash and its source's. None otherwise.

    Runs made from a selection record the selection's hash as ``items_sha256``, not
    the train file's; this links the two.
    """
    cfg_path = run_dir / "configs" / "resolved" / "run.json"
    if not cfg_path.is_file():
        return None
    raw = ((json.loads(cfg_path.read_text("utf-8")).get("env") or {}).get("taskset") or {}).get("items_path")
    if not raw:
        return None
    items_file = Path(raw).expanduser()
    items_file = items_file if items_file.is_absolute() else REPO / items_file
    refuse_heldout(items_file)
    sel = items_file.parent / "selection.json"
    if not (items_file.is_file() and sel.is_file()):
        return None
    record = json.loads(sel.read_text("utf-8"))
    own = sha256_file(items_file)
    if record.get("source_sha256") == train_sha and record.get("items_sha256") == own:
        return own
    return None


def read_runs(
    dirs: Sequence[Path], items: Mapping[str, QuestionItem], train_sha: str
) -> list[TraceRef]:
    """Every trace in ``dirs`` as a :class:`TraceRef`, after checking that it is on an
    unchanged train item from this train file, and that it records as ``items_sha256``
    the train file's hash or the hash of a selection of it (:func:`selection_sha`).
    Messages name runs and counts only."""
    refs: list[TraceRef] = []
    for d in dirs:
        run = rel(d)
        accepted = {train_sha} | ({s} if (s := selection_sha(d, train_sha)) else set())
        bad_item = bad_hash = not_train = 0
        for lineno, j, saved in iter_saved_traces(d):
            data = (saved.get("task") or {}).get("data") or {}
            item_id = data.get("id")
            if item_id not in items:
                not_train += 1
                continue
            item = items[item_id]
            mine = {k: v for k, v in data.items() if k not in IGNORED_ITEM_FIELDS}
            theirs = item.model_dump(mode="json", exclude=IGNORED_ITEM_FIELDS)
            if QuestionItem.model_validate(mine).model_dump(
                mode="json", exclude=IGNORED_ITEM_FIELDS
            ) != theirs:
                bad_item += 1
            if (saved.get("info") or {}).get("items_sha256") not in accepted:
                bad_hash += 1
            refs.append(TraceRef(run, str(saved["id"]), lineno, j, trace_model(saved),
                                 item_id, item.answerable))
        if not_train or bad_item or bad_hash:
            raise GradingError(
                f"{run}: {not_train} trace(s) not on a train item, {bad_item} with an "
                f"item that differs from the train file, {bad_hash} not recording the "
                "train file's SHA-256 (or a selection of it)"
            )
    return refs


def exclusion_ids(patterns: Sequence[str], root: Path = REPO) -> tuple[set[str], list[str]]:
    """Item IDs named in the files matching ``patterns`` (globs relative to ``root``),
    and the files read, sorted. Only the pattern match is kept."""
    files: set[Path] = set()
    for pat in patterns:
        files.update(Path(p) for p in glob.glob(str(root / pat), recursive=True))
    ids: set[str] = set()
    for f in sorted(files):
        if f.is_file():
            refuse_heldout(f)
            ids.update(ITEM_ID_RE.findall(f.read_text(encoding="utf-8", errors="replace")))
    return ids, sorted(Path(f).resolve().relative_to(root.resolve()).as_posix() for f in files)


def sources_sha256(sources: Sequence[str], root: Path = REPO) -> str:
    """One SHA-256 over the exclusion sources: each file's own SHA-256, one per line,
    in sorted path order. The draw record keeps this and counts, never the file
    names, since some sources are local planning files that are never committed."""
    return sha256_bytes("".join(f"{sha256_file(root / f)}\n" for f in sorted(sources)).encode())


def ids_sha256(ids: Iterable[str]) -> str:
    """SHA-256 of IDs sorted, one per line, each line ending in a newline."""
    return sha256_bytes("".join(f"{i}\n" for i in sorted(ids)).encode())


# --- Drawing ---------------------------------------------------------------------------


class Shortfall(GradingError):
    """At least one ``answerable`` value has too few items. Carries counts only:
    {answerable: (items available, items needed)}."""

    def __init__(self, short: Mapping[str, tuple[int, int]]):
        self.short = dict(short)
        parts = [f"{a}: {have} available, {need} needed"
                 for a, (have, need) in sorted(self.short.items())]
        super().__init__("cell(s) can't be filled: " + "; ".join(parts))


@dataclass(frozen=True)
class Drawn:
    """One sampled trace with its grading ID and position."""

    grading_id: str
    position: int
    ref: TraceRef


def parse_cells(text: str) -> dict[str, int]:
    """``correctable=4,no=8,...`` as a dict; every size a positive integer."""
    out: dict[str, int] = {}
    for part in text.split(","):
        name, sep, size = part.strip().partition("=")
        if not sep or not name or name in out or not size.strip().isdigit() or int(size) < 1:
            raise GradingError(f"--cells: can't read {part.strip()!r} "
                               "(expected answerable=positive integer, each value once)")
        out[name] = int(size)
    return out


def draw_cells(
    refs: Sequence[TraceRef],
    excluded: set[str],
    cells: Mapping[str, int],
    seed: str = DRAW_SEED,
    assign_seed: str = ASSIGN_SEED,
) -> tuple[list[TraceRef], dict[str, int]]:
    """Draw ``cells[a]`` traces per model for every ``answerable`` value ``a``, each
    item at most once across the whole sample.

    Per value (in sorted order): the pool is the value's items that are not excluded
    and have a trace from every model; ``cells[a]`` x (number of models) items are
    drawn from it with ``seed``; those are shuffled with ``assign_seed`` and split
    into one block per model, models in sorted order. Pools are sorted before
    sampling, so the draw depends only on the traces and the seeds, not on file order.

    Returns:
        The drawn traces, and for each value the number of its non-excluded items left
        out for lacking some model's trace (zero when every run covers every item).

    Raises:
        GradingError: if ``cells`` doesn't name exactly the ``answerable`` values the
            traces have.
        Shortfall: if any value's pool is smaller than it needs.
    """
    answerables = sorted({r.answerable for r in refs})
    if set(cells) != set(answerables):
        raise GradingError(f"cell sizes are for {sorted(cells)}; the traces have "
                           f"{answerables}")
    models = sorted({r.model for r in refs})
    traces: dict[tuple[str, str], list[TraceRef]] = defaultdict(list)
    models_of: dict[str, set[str]] = defaultdict(set)
    value_of: dict[str, str] = {}
    for r in refs:
        traces[(r.item_id, r.model)].append(r)
        models_of[r.item_id].add(r.model)
        value_of[r.item_id] = r.answerable
    item_rng = random.Random(seed)
    assign_rng = random.Random(assign_seed)
    out: list[TraceRef] = []
    short: dict[str, tuple[int, int]] = {}
    incomplete: dict[str, int] = {}
    for a in answerables:
        mine = sorted(i for i, v in value_of.items() if v == a and i not in excluded)
        pool = [i for i in mine if models_of[i] == set(models)]
        incomplete[a] = len(mine) - len(pool)
        need = cells[a] * len(models)
        if len(pool) < need:
            short[a] = (len(pool), need)
            continue
        chosen = sorted(item_rng.sample(pool, need))
        assign_rng.shuffle(chosen)
        for k, m in enumerate(models):
            for item_id in chosen[k * cells[a]:(k + 1) * cells[a]]:
                options = sorted(traces[(item_id, m)], key=lambda r: (r.run, r.trace_id))
                out.append(options[0] if len(options) == 1 else item_rng.choice(options))
    if short:
        raise Shortfall(short)
    return out, incomplete


def assign_ids_and_order(
    drawn: Sequence[TraceRef], ids_seed: str = IDS_SEED, order_seed: str = ORDER_SEED
) -> list[Drawn]:
    """Opaque grading IDs (``g-`` plus 8 hex digits, from ``ids_seed``) and a shuffled
    grading order (from ``order_seed``). Positions count from 1."""
    base = sorted(drawn, key=lambda r: (r.answerable, r.model, r.item_id, r.run, r.trace_id))
    id_rng = random.Random(ids_seed)
    ids: list[str] = []
    seen: set[str] = set()
    while len(ids) < len(base):
        gid = f"g-{id_rng.getrandbits(32):08x}"
        if gid not in seen:
            seen.add(gid)
            ids.append(gid)
    pairs = list(zip(ids, base, strict=True))
    random.Random(order_seed).shuffle(pairs)
    return [Drawn(gid, pos, ref) for pos, (gid, ref) in enumerate(pairs, start=1)]


def draw_sealed(drawn: Sequence[Drawn], per_answerable: int,
                seed: str = SEALED_SEED) -> list[str]:
    """The sealed validation subset: ``per_answerable`` grading IDs from each
    ``answerable`` value (all of them if fewer), seeded, returned sorted."""
    by_a: dict[str, list[str]] = defaultdict(list)
    for d in drawn:
        by_a[d.ref.answerable].append(d.grading_id)
    rng = random.Random(seed)
    out: list[str] = []
    for a in sorted(by_a):
        pool = sorted(by_a[a])
        out.extend(rng.sample(pool, min(per_answerable, len(pool))))
    return sorted(out)


def key_records(drawn: Sequence[Drawn]) -> list[dict[str, Any]]:
    """The key file's lines, in grading order."""
    return [
        {
            "grading_id": d.grading_id,
            "position": d.position,
            "round": ROUND,
            "model": d.ref.model,
            "run": d.ref.run,
            "trace_id": d.ref.trace_id,
            "trace_line": d.ref.trace_line,
            "trace_index": d.ref.trace_index,
            "item_id": d.ref.item_id,
            "answerable": d.ref.answerable,
        }
        for d in sorted(drawn, key=lambda d: d.position)
    ]


# --- Main ------------------------------------------------------------------------------


def run_draw(
    items_path: Path,
    runs: Sequence[Path],
    out_dir: Path,
    *,
    cells: Mapping[str, int] = CELLS,
    sealed_per_answerable: int = SEALED_PER_ANSWERABLE,
    exclude_from: Sequence[str] = EXCLUDE_FROM,
    exclude_root: Path = REPO,
    seed: str = DRAW_SEED,
    force: bool = False,
    today: str | None = None,
) -> dict[str, Any]:
    """Run the whole draw and write the three files. Returns the draw record.

    Raises:
        GradingError: on any failed check, a shortfall, or an existing key file
            without ``force``. Nothing is written in that case.
    """
    refuse_heldout(items_path)
    train_file = train_path(items_path)
    refuse_heldout(train_file)
    key_path = out_dir / KEY_FILE
    if key_path.exists() and not force:
        raise GradingError(f"{key_path} exists; a redraw would change the sample "
                           "(pass --force to replace it)")
    try:
        items = {it.id: it for it in load_train(train_file)}
    except ItemsError as e:
        raise GradingError(f"train items: {e}") from None
    train_sha = sha256_file(train_file)
    dirs = run_dirs(runs)
    refs = read_runs(dirs, items, train_sha)
    excluded, sources = exclusion_ids(exclude_from, exclude_root)
    assign_seed, order_seed = f"{seed}/assign", f"{seed}/order"
    ids_seed, sealed_seed = f"{seed}/ids", f"{seed}/sealed"
    picked, incomplete = draw_cells(refs, excluded, cells, seed, assign_seed)
    drawn = assign_ids_and_order(picked, ids_seed, order_seed)
    sealed = draw_sealed(drawn, sealed_per_answerable, sealed_seed)

    key_text = jsonl_text(key_records(drawn))
    sealed_text = json.dumps(sealed, indent=2) + "\n"
    cell_counts: dict[str, dict[str, int]] = defaultdict(dict)
    for d in drawn:
        c = cell_counts[d.ref.answerable]
        c[d.ref.model] = c.get(d.ref.model, 0) + 1
    sealed_set = set(sealed)
    sealed_counts: dict[str, int] = defaultdict(int)
    for d in drawn:
        if d.grading_id in sealed_set:
            sealed_counts[d.ref.answerable] += 1
    record = {
        "round": ROUND,
        "drawn_on": today or dt.date.today().isoformat(),
        "seeds": {"draw": seed, "assign": assign_seed, "order": order_seed, "ids": ids_seed,
                  "sealed": sealed_seed},
        "cells_per_model": dict(sorted(cells.items())),
        "sealed_per_answerable": sealed_per_answerable,
        "train_file": {"path": train_file.name, "sha256": train_sha, "items": len(items)},
        "runs": [
            {"run": run, "traces_sha256": sha256_file(d / "traces.jsonl"),
             "traces": sum(1 for r in refs if r.run == run),
             "selection_sha256": selection_sha(d, train_sha)}
            for d, run in ((d, rel(d)) for d in dirs)
        ],
        "exclusion": {
            "patterns": list(exclude_from),
            "files_per_pattern": {
                pat: len(exclusion_ids([pat], exclude_root)[1]) for pat in exclude_from
            },
            "files": len(sources),
            "files_sha256": sources_sha256(sources, exclude_root),
            "ids": len(excluded),
            "ids_sha256": ids_sha256(excluded),
        },
        "items_without_every_model": incomplete,
        "cells": {a: dict(sorted(m.items())) for a, m in sorted(cell_counts.items())},
        "sealed": dict(sorted(sealed_counts.items())),
        "total": len(drawn),
        "key_sha256": sha256_bytes(key_text.encode()),
        "sealed_sha256": sha256_bytes(sealed_text.encode()),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(key_path, key_text)
    atomic_write_text(out_dir / SEALED_FILE, sealed_text)
    write_json(out_dir / DRAW_FILE, record)
    return record


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--items", type=Path, required=True,
                    help="item-set directory or its train.jsonl (nothing else is read)")
    ap.add_argument("--runs", type=Path, nargs="+", required=True,
                    help="run directories, or parents holding them one level down")
    ap.add_argument("--dir", type=Path, default=None,
                    help="round directory (default: $NGQ_DATA_DIR/grading/round-first)")
    ap.add_argument("--cells", default=",".join(f"{a}={n}" for a, n in CELLS.items()),
                    help="traces per model for each answerable value "
                         "(default: %(default)s)")
    ap.add_argument("--sealed-per-answerable", type=int, default=SEALED_PER_ANSWERABLE)
    ap.add_argument("--seed", default=DRAW_SEED)
    ap.add_argument("--force", action="store_true", help="replace an existing draw")
    args = ap.parse_args(argv)
    try:
        out_dir = args.dir or default_round_dir()
        record = run_draw(args.items, args.runs, out_dir, cells=parse_cells(args.cells),
                          sealed_per_answerable=args.sealed_per_answerable, seed=args.seed,
                          force=args.force)
    except GradingError as e:
        print(f"draw: FAIL: {e}")
        return 1
    print("draw: PASS")
    print(f"exclusion list: {record['exclusion']['ids']} item IDs, "
          f"sha256 {record['exclusion']['ids_sha256']}")
    for name in (KEY_FILE, SEALED_FILE, DRAW_FILE):
        print(f"{name}: sha256 {sha256_file(out_dir / name)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
