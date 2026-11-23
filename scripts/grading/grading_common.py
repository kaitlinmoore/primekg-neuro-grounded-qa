"""Shared paths, file names and file helpers for the grading tools.

The grading tools prepare a blind sample of real traces for hand grading against
``docs/labeling_guide.md``, then lock the labels. They are run in this order:

    draw.py -> export.py -> app.py (grading) -> validate.py -> lock.py

Everything the grader must not see before unblinding (the key file, the sealed list,
the draw record) and everything still in progress (the export, the labels, drafts)
lives in one round directory outside the repo, by default
``$NGQ_DATA_DIR/grading/round-first/``. Only ``lock.py`` writes into the repo, to
``validation/``.

The scripts under ``scripts/grading/`` aren't a package. Each one puts this directory
(and ``scripts/data``, for the train-only item loader) on ``sys.path`` and imports its
neighbours by module name, as ``scripts/analysis/`` does.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
GRADING_DIR = Path(__file__).resolve().parent
for _p in (GRADING_DIR, REPO / "scripts" / "data"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

ROUND = "first"
"""The grading round these tools serve. The guide's ``round`` field takes this value."""

KEY_FILE = "grading_key.jsonl"
"""grading_id -> run, trace, model, item. Hidden from the grader until unblinding."""
SEALED_FILE = "sealed.json"
"""The sealed validation subset: a JSON list of grading_ids, nothing else."""
DRAW_FILE = "draw.json"
"""The draw record: seeds, source hashes, exclusion-list hash, cell counts."""
EXPORT_FILE = "export.jsonl"
"""What the grader sees: one record per grading_id, in grading order."""
LABELS_FILE = f"labels_{ROUND}.jsonl"
"""The grader's labels, one JSON object per graded trace, in grading order."""
PROGRESS_FILE = "progress.json"
"""The viewer's draft for the trace in progress (step-1 answers locked at reveal)."""
NOTES_FILE = f"notes_{ROUND}.jsonl"
"""Free-text fields split off the labels at lock time. Not committed until checked."""

VALIDATION_DIR = REPO / "validation"
"""Committed products of a locked round."""
COMMITTED_LABELS = VALIDATION_DIR / f"labels_{ROUND}.jsonl"
GRADING_LOG = VALIDATION_DIR / "grading_log.md"

GUIDE_PATH = REPO / "docs" / "labeling_guide.md"

HELDOUT_NAME = "heldout.jsonl"


class GradingError(Exception):
    """A refusal or failed check. Messages name files, fields and counts only, never
    item or trace content, so they are safe to print."""


def default_round_dir() -> Path:
    """``$NGQ_DATA_DIR/grading/round-first``; refuses if ``NGQ_DATA_DIR`` is unset.

    The round directory holds the key file and the grader's work, so it must not
    default to anywhere inside the repo.
    """
    root = os.environ.get("NGQ_DATA_DIR")
    if not root:
        raise GradingError("NGQ_DATA_DIR is not set; load the env (direnv) or pass --dir")
    return Path(root).expanduser() / "grading" / f"round-{ROUND}"


def refuse_heldout(path: Path) -> None:
    """Raise if ``path`` names a held-out items file. The grading tools never read one."""
    if path.name == HELDOUT_NAME:
        raise GradingError(f"refusing {HELDOUT_NAME}: grading uses train items only")


def sha256_bytes(data: bytes) -> str:
    """Hex SHA-256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Hex SHA-256 of a file's bytes."""
    return sha256_bytes(path.read_bytes())


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` so that a reader sees the old file or the new one,
    never a half-written file: write a temporary file in the same directory, flush it
    to disk, then rename it over ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def dumps_line(record: Mapping[str, Any]) -> str:
    """One JSONL line (no trailing newline): compact, UTF-8, key order kept."""
    return json.dumps(record, ensure_ascii=False, separators=(", ", ": "))


def jsonl_text(records: Iterable[Mapping[str, Any]]) -> str:
    """Records as JSONL text, one per line, with a trailing newline."""
    return "".join(dumps_line(r) + "\n" for r in records)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Every JSON object in a JSONL file, in order. Raises GradingError (naming the
    line number only) on a line that isn't a JSON object."""
    out: list[dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            raise GradingError(f"{path.name}:{lineno}: not JSON") from None
        if not isinstance(rec, dict):
            raise GradingError(f"{path.name}:{lineno}: not a JSON object")
        out.append(rec)
    return out


def write_json(path: Path, obj: Any) -> None:
    """Atomic write of ``obj`` as indented JSON."""
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def guide_version(path: Path = GUIDE_PATH) -> str:
    """The guide version a label records: ``sha256:`` plus 16 hex digits of the
    committed guide's bytes. It changes whenever the guide's text changes."""
    return "sha256:" + sha256_file(path)[:16]
