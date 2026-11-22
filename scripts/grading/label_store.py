"""Saving and resuming labels: the viewer's file logic, kept free of Streamlit so it
can be tested without a browser.

Files in the round directory:

- ``labels_first.jsonl``: one complete, valid record per graded trace, in grading
  order. :func:`save_label` adds the next trace's record by rewriting the file
  atomically with every earlier line unchanged; it refuses a record for a trace that
  already has one (saved labels are never overwritten) and a record for any trace
  but the next ungraded one (grading follows the export's order, no skipping).
- ``progress.json``: the trace in progress. When the grader reveals step 2, the
  step-1 answers (output form, earlier answer and the per-claim fields) are written
  here and can't be changed afterwards, so claims stay graded with the gold hidden
  even if the viewer is closed and reopened. It is deleted when the trace is saved.

Nothing here fills in a grade: :func:`assemble_record` only copies what the grader
entered and writes ``null`` where a field doesn't apply.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from grading_common import (
    EXPORT_FILE,
    LABELS_FILE,
    PROGRESS_FILE,
    ROUND,
    GradingError,
    atomic_write_text,
    dumps_line,
    read_jsonl,
    write_json,
)
from label_schema import RECORD_KEYS, ExportContext, applies, validate_record

STEP1_KEYS = ("output_form", "earlier_answer", "claims")
"""Answers locked at reveal. ``claims`` here holds each claim's step-1 fields."""
CLAIM_STEP1_KEYS = ("supported", "cites_unretrieved", "relevant", "duplicate_of", "note")
"""Per-claim fields graded in step 1. ``premise_restatement`` waits for step 2,
because the premise is revealed there."""


def load_export(round_dir: Path) -> list[dict[str, Any]]:
    """Export records in grading order."""
    path = round_dir / EXPORT_FILE
    if not path.exists():
        raise GradingError(f"{path} not found; run export.py first")
    return sorted(read_jsonl(path), key=lambda r: r["position"])


def load_labels(round_dir: Path) -> list[dict[str, Any]]:
    """Saved label records, in file order ([] if none yet)."""
    path = round_dir / LABELS_FILE
    return read_jsonl(path) if path.exists() else []


def next_index(export: Sequence[Mapping[str, Any]], labels: Sequence[Mapping[str, Any]]) -> int | None:
    """Index (0-based, export order) of the first ungraded trace, or None when every
    trace is graded. Raises if the labels aren't a prefix of the export's order."""
    order = [r["grading_id"] for r in export]
    done = [r.get("grading_id") for r in labels]
    if done != order[: len(done)]:
        raise GradingError(
            f"{LABELS_FILE} isn't the first {len(done)} trace(s) of the export in order"
        )
    return None if len(done) == len(order) else len(done)


def save_label(round_dir: Path, record: Mapping[str, Any]) -> None:
    """Validate ``record`` and append it to the labels file atomically.

    Raises:
        GradingError: if the record is invalid, its trace already has a saved record,
            or it isn't the next ungraded trace. The file is unchanged then.
    """
    export = load_export(round_dir)
    labels = load_labels(round_dir)
    gid = record.get("grading_id")
    if any(r.get("grading_id") == gid for r in labels):
        raise GradingError(f"{gid}: already saved; saved labels are never overwritten")
    nxt = next_index(export, labels)
    if nxt is None or export[nxt]["grading_id"] != gid:
        raise GradingError(f"{gid}: not the next trace to grade")
    errors = validate_record(dict(record), ExportContext.from_export(export[nxt]))
    if errors:
        raise GradingError("; ".join(errors))
    path = round_dir / LABELS_FILE
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if old and not old.endswith("\n"):
        old += "\n"
    atomic_write_text(path, old + dumps_line(record) + "\n")
    clear_progress(round_dir)


def load_progress(round_dir: Path, grading_id: str) -> dict[str, Any] | None:
    """The locked step-1 answers for ``grading_id``, or None if step 2 hasn't been
    revealed for it. A draft for another trace is an error (it can't be stale: it is
    deleted on save)."""
    path = round_dir / PROGRESS_FILE
    if not path.exists():
        return None
    draft = json.loads(path.read_text(encoding="utf-8"))
    if draft.get("grading_id") != grading_id:
        raise GradingError(f"{PROGRESS_FILE} is for another trace than the next one")
    return draft


def reveal(round_dir: Path, grading_id: str, step1: Mapping[str, Any]) -> dict[str, Any]:
    """Lock the step-1 answers and mark step 2 revealed. Refuses incomplete answers
    (``step1_errors``) and a second reveal."""
    if load_progress(round_dir, grading_id) is not None:
        raise GradingError(f"{grading_id}: step 2 already revealed")
    if errors := step1_errors(step1):
        raise GradingError("; ".join(errors))
    draft = {"grading_id": grading_id, "revealed": True,
             "step1": {k: step1[k] for k in STEP1_KEYS}}
    write_json(round_dir / PROGRESS_FILE, draft)
    return draft


def clear_progress(round_dir: Path) -> None:
    (round_dir / PROGRESS_FILE).unlink(missing_ok=True)


def step1_errors(step1: Mapping[str, Any]) -> list[str]:
    """What's missing from the step-1 answers before reveal (completeness only)."""
    errors: list[str] = []
    a = applies(step1.get("output_form"), step1.get("earlier_answer"))
    if step1.get("output_form") is None:
        errors.append("output_form: not set")
    if a.earlier_answer and step1.get("earlier_answer") is None:
        errors.append("earlier_answer: not set")
    claims = step1.get("claims")
    if not isinstance(claims, list):
        errors.append("claims: not set")
        return errors
    if not a.claims and claims:
        errors.append("claims: none are graded for this output form")
    for n, c in enumerate(claims):
        for k in ("supported", "cites_unretrieved", "relevant"):
            if c.get(k) is None:
                errors.append(f"claims[{n}].{k}: not set")
    return errors


def assemble_record(
    grading_id: str,
    step1: Mapping[str, Any],
    step2: Mapping[str, Any],
    *,
    guide_version: str,
    has_premise: bool,
    today: str | None = None,
) -> dict[str, Any]:
    """The label record from the grader's entries, in the guide's key order.

    ``step1`` holds the locked step-1 answers; ``step2`` holds ``gold_points``
    (``[{gold_point_id, covered}]``), per-claim ``premise_restatement`` (a list
    aligned with the claims) and the item fields. Fields that don't apply are written
    as ``null``; blank free text is written as ``null``. Nothing else is filled in.
    """
    a = applies(
        step1.get("output_form"), step1.get("earlier_answer"), step2.get("response"),
        has_premise, step2.get("text_ok"), step2.get("abstain_reason_ok"),
    )
    restate = list(step2.get("premise_restatement") or [])
    claims = []
    for n, c in enumerate((step1.get("claims") or []) if a.claims else []):
        claims.append({
            "claim_index": n,
            "supported": c.get("supported"),
            "cites_unretrieved": c.get("cites_unretrieved"),
            "relevant": c.get("relevant"),
            "premise_restatement": (restate[n] if n < len(restate) else None)
            if a.premise_restatement else None,
            "duplicate_of": c.get("duplicate_of"),
            "note": _text_or_none(c.get("note")),
        })

    def pick(name: str, applies_: bool) -> Any:
        return step2.get(name) if applies_ else None

    rec = {
        "grading_id": grading_id,
        "round": ROUND,
        "guide_version": guide_version,
        "graded_on": today or dt.date.today().isoformat(),
        "output_form": step1.get("output_form"),
        "earlier_answer": step1.get("earlier_answer") if a.earlier_answer else None,
        "response": pick("response", a.response),
        "choice_correct": step2.get("choice_correct"),
        "abstain_reason_ok": pick("abstain_reason_ok", a.abstain_reason_ok),
        "flag": pick("flag", a.flag),
        "premise_handling": pick("premise_handling", a.premise_handling),
        "text_ok": pick("text_ok", a.text_ok),
        "text_issue": _text_or_none(step2.get("text_issue")) if a.text_issue else None,
        "overall": step2.get("overall"),
        "claims": claims,
        "gold_points": [
            {"gold_point_id": g["gold_point_id"], "covered": g.get("covered")}
            for g in step2.get("gold_points") or []
        ],
        "failures": None,
        "item_issue": _text_or_none(step2.get("item_issue")),
        "note": _text_or_none(step2.get("note")),
    }
    if tuple(rec) != RECORD_KEYS:  # the schema and this function must agree
        raise GradingError("label record keys differ from the schema")
    return rec


def _text_or_none(v: Any) -> str | None:
    """One line of free text, or None when blank."""
    if v is None:
        return None
    s = " ".join(str(v).split())
    return s or None


def graded_message(record: Mapping[str, Any], earlier_answer: str | None) -> dict[str, Any] | None:
    """The message whose claims are graded: the earlier answer when the grader marked
    it present, else the final message."""
    step1 = record["step1"]
    if earlier_answer == "present" and step1.get("earlier_answer"):
        return step1["earlier_answer"]
    return step1["final_message"]
