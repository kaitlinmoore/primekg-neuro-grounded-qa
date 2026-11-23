"""Check a labels file against the label schema in ``docs/labeling_guide.md``.

    uv run python scripts/grading/validate.py [--dir $NGQ_DATA_DIR/grading/round-first] [--complete]

Checks shape and allowed values only, never whether a grade is right (``label_schema.py``
has the rules). With the round's export present it also checks each record against
its trace's export record: that the record's trace is in the export, that the labels
follow the export's order, the gold-point IDs, and whether the premise fields apply.
It reports how many traces are graded. ``--complete`` (what ``lock.py`` uses) also
fails unless every trace in the export has exactly one record.

Error lines name the grading_id, the field and the problem. They can show a
structured value (an off-schema enum value), never free text.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grading_common import EXPORT_FILE, LABELS_FILE, GradingError, default_round_dir, read_jsonl
from label_schema import ExportContext, validate_record


@dataclass
class Report:
    """The outcome of validating one labels file."""

    graded: int = 0
    total: int | None = None
    """Traces in the export, if one was read."""
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_labels(
    labels: Sequence[Any],
    export: Sequence[Mapping[str, Any]] | None = None,
    *,
    complete: bool = False,
) -> Report:
    """Validate parsed label records, optionally against the export.

    Args:
        labels: the records, in file order.
        export: the export records in grading order, or None to check shape alone.
        complete: also require one record for every trace in the export.
    """
    rep = Report(graded=len(labels), total=len(export) if export is not None else None)
    contexts = {r["grading_id"]: ExportContext.from_export(r) for r in export or []}
    seen: set[str] = set()
    for n, rec in enumerate(labels):
        gid = rec.get("grading_id") if isinstance(rec, dict) else None
        label = gid if isinstance(gid, str) else f"line {n + 1}"
        if isinstance(gid, str) and gid in seen:
            rep.errors.append(f"{label}: graded twice")
        if isinstance(gid, str):
            seen.add(gid)
        ctx = None
        if export is not None:
            ctx = contexts.get(gid) if isinstance(gid, str) else None
            if ctx is None:
                rep.errors.append(f"{label}: not a grading_id in the export")
        rep.errors += [f"{label}: {e}" for e in validate_record(rec, ctx)]
    if export is not None:
        order = [r["grading_id"] for r in export]
        got = [r.get("grading_id") if isinstance(r, dict) else None for r in labels]
        if got != order[: len(got)]:
            rep.errors.append("labels: not in the export's grading order (or a trace was skipped)")
        if complete and len(got) != len(order):
            rep.errors.append(f"labels: {len(got)} of {len(order)} traces graded; "
                              "the round isn't complete")
    elif complete:
        rep.errors.append("labels: --complete needs the export to count traces")
    return rep


def validate_round(round_dir: Path, *, complete: bool = False) -> Report:
    """Validate ``labels_first.jsonl`` in ``round_dir`` against its export."""
    path = round_dir / LABELS_FILE
    if not path.exists():
        raise GradingError(f"{path} not found")
    export_path = round_dir / EXPORT_FILE
    export = (sorted(read_jsonl(export_path), key=lambda r: r["position"])
              if export_path.exists() else None)
    return validate_labels(read_jsonl(path), export, complete=complete)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", type=Path, default=None,
                    help="round directory (default: $NGQ_DATA_DIR/grading/round-first)")
    ap.add_argument("--complete", action="store_true",
                    help="fail unless every trace in the export is graded")
    args = ap.parse_args(argv)
    try:
        rep = validate_round(args.dir or default_round_dir(), complete=args.complete)
    except GradingError as e:
        print(f"validate: FAIL: {e}")
        return 1
    for e in rep.errors:
        print(f"  {e}")
    total = f" of {rep.total}" if rep.total is not None else ""
    print(f"validate: {'PASS' if rep.ok else 'FAIL'} ({rep.graded}{total} traces graded)")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
