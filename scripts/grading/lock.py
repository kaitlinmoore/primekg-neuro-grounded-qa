"""Lock a finished grading round: hash the labels, split off free text, log it.

    uv run python scripts/grading/lock.py [--dir $NGQ_DATA_DIR/grading/round-first]

Run once, when every trace is graded. It refuses unless ``validate.py --complete``
passes, and refuses to replace an existing locked file (a correction goes into a new
labels version with its reason; agreement is reported on the locked version).

It writes:

- ``validation/labels_first.jsonl`` (committed): every record with the free-text
  fields removed: ``text_issue``, ``item_issue`` and ``note``, each claim's ``note``
  and each failure's ``note``. IDs and structured fields only.
- ``<round dir>/notes_first.jsonl`` (not committed yet): the free text, keyed by
  ``grading_id`` (and ``claim_index``). Committed after unblinding only if no note
  quotes item or trace text.
- ``validation/grading_log.md`` (committed): a dated entry with the SHA-256 of the
  full labels file, the committed file and the notes file, the count, the guide
  version(s), and the hashes of the export, key file, sealed list and draw record
  (hashes only, so the hidden files are fixed before unblinding without being shown).

It never commits. It prints the ``git add`` command for the two committed files, and
how to commit the key file, sealed list, draw record and notes after unblinding.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from grading_common import (
    COMMITTED_LABELS,
    DRAW_FILE,
    EXPORT_FILE,
    GRADING_LOG,
    KEY_FILE,
    LABELS_FILE,
    NOTES_FILE,
    REPO,
    ROUND,
    SEALED_FILE,
    GradingError,
    atomic_write_text,
    default_round_dir,
    jsonl_text,
    read_jsonl,
    sha256_bytes,
    sha256_file,
)
from label_schema import FREE_TEXT_KEYS
from validate import validate_round

LOG_HEADER = (
    "# Grading log\n\n"
    "One entry per locked labels file, written by `scripts/grading/lock.py`. Each "
    "entry fixes the labels by SHA-256 before unblinding. A later correction is a new "
    "labels version with its own entry and reason.\n"
)


def split_free_text(records: Sequence[Mapping[str, Any]]) -> tuple[list[dict], list[dict]]:
    """(records without free text, the free text keyed by grading_id).

    A notes line is written for every record, with ``null`` where a field was empty,
    so the two files line up one to one.
    """
    stripped: list[dict] = []
    notes: list[dict] = []
    for rec in records:
        s = {k: v for k, v in rec.items() if k not in FREE_TEXT_KEYS}
        s["claims"] = [{k: v for k, v in c.items() if k != "note"} for c in rec["claims"]]
        if rec.get("failures") is not None:
            s["failures"] = [{k: v for k, v in f.items() if k != "note"} for f in rec["failures"]]
        stripped.append(s)
        n = {"grading_id": rec["grading_id"], **{k: rec.get(k) for k in FREE_TEXT_KEYS}}
        n["claims"] = [{"claim_index": c["claim_index"], "note": c.get("note")} for c in rec["claims"]]
        n["failures"] = ([{"code": f.get("code"), "claim_index": f.get("claim_index"),
                           "note": f.get("note")} for f in rec["failures"]]
                         if rec.get("failures") is not None else None)
        notes.append(n)
    return stripped, notes


def _rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return str(p)


def lock_round(
    round_dir: Path,
    *,
    committed: Path = COMMITTED_LABELS,
    log: Path = GRADING_LOG,
    today: str | None = None,
) -> dict[str, Any]:
    """Lock ``round_dir``'s labels. Returns the log entry's fields.

    Raises:
        GradingError: if the labels don't validate as complete, or ``committed``
            already exists. Nothing is written then.
    """
    rep = validate_round(round_dir, complete=True)
    if not rep.ok:
        raise GradingError(f"labels don't validate as complete ({len(rep.errors)} "
                           "error(s)); run validate.py --complete")
    if committed.exists():
        raise GradingError(f"{_rel(committed)} exists; a locked round isn't replaced "
                           "(a correction is a new labels version)")
    labels_path = round_dir / LABELS_FILE
    records = read_jsonl(labels_path)
    stripped, notes = split_free_text(records)
    stripped_text = jsonl_text(stripped)
    notes_text = jsonl_text(notes)
    date = today or dt.date.today().isoformat()
    hashes = {name: sha256_file(round_dir / name) if (round_dir / name).exists() else None
              for name in (EXPORT_FILE, KEY_FILE, SEALED_FILE, DRAW_FILE)}
    entry = {
        "date": date,
        "round": ROUND,
        "traces": len(records),
        "labels_sha256": sha256_file(labels_path),
        "committed_sha256": sha256_bytes(stripped_text.encode()),
        "notes_sha256": sha256_bytes(notes_text.encode()),
        "guide_versions": sorted({r["guide_version"] for r in records}),
        "hashes": hashes,
    }
    lines = [
        f"\n## Round `{ROUND}`, locked {date}\n",
        f"- Traces graded: {entry['traces']}",
        f"- Guide version(s): {', '.join(f'`{v}`' for v in entry['guide_versions'])}",
        f"- Full labels file (`{LABELS_FILE}`, round directory, not committed): "
        f"`{entry['labels_sha256']}`",
        f"- Committed labels (`{_rel(committed)}`, free text removed): "
        f"`{entry['committed_sha256']}`",
        f"- Notes (`{NOTES_FILE}`, round directory, committed after unblinding only if "
        f"no note quotes item or trace text): `{entry['notes_sha256']}`",
    ]
    for name, h in hashes.items():
        lines.append(f"- `{name}` (round directory): `{h}`" if h else f"- `{name}`: not found")
    old_log = log.read_text(encoding="utf-8") if log.exists() else LOG_HEADER
    atomic_write_text(round_dir / NOTES_FILE, notes_text)
    atomic_write_text(committed, stripped_text)
    atomic_write_text(log, old_log.rstrip("\n") + "\n" + "\n".join(lines) + "\n")
    return entry


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", type=Path, default=None,
                    help="round directory (default: $NGQ_DATA_DIR/grading/round-first)")
    args = ap.parse_args(argv)
    try:
        round_dir = args.dir or default_round_dir()
        entry = lock_round(round_dir)
    except GradingError as e:
        print(f"lock: FAIL: {e}")
        return 1
    print(f"lock: PASS ({entry['traces']} traces)")
    print(f"labels sha256 {entry['labels_sha256']} recorded in {_rel(GRADING_LOG)}")
    print("\nCommit the locked labels and the log (this script never commits):")
    print(f"  git add {_rel(COMMITTED_LABELS)} {_rel(GRADING_LOG)}")
    print("\nAfter unblinding, commit the key file, sealed list and draw record:")
    for name, dest in ((KEY_FILE, f"grading_key_{ROUND}.jsonl"),
                       (SEALED_FILE, f"sealed_{ROUND}.json"), (DRAW_FILE, f"draw_{ROUND}.json")):
        print(f"  cp {round_dir / name} validation/{dest}")
    print(f"  git add validation/grading_key_{ROUND}.jsonl validation/sealed_{ROUND}.json "
          f"validation/draw_{ROUND}.json")
    print(f"\nRead {round_dir / NOTES_FILE} first; commit it only if no note quotes item "
          "or trace text:")
    print(f"  cp {round_dir / NOTES_FILE} validation/{NOTES_FILE} && git add validation/{NOTES_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
