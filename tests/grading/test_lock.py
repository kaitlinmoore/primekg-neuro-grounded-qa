"""The lock step: refuses partial labels, strips free text, logs the hash.
All labels here are synthetic test data on fixture traces."""

from __future__ import annotations

import pytest

import label_store as L  # noqa: E402  (path set up by helpers)
import lock  # noqa: E402
from grading_common import LABELS_FILE, NOTES_FILE, GradingError, read_jsonl, sha256_file  # noqa: E402


def fill(rec_export: dict, text: str | None = "free text here") -> dict:
    """A shape-valid synthetic record for one export record: output unusable, so only
    the item-level fields apply. Free text in every free-text field."""
    s2 = rec_export["step2"]
    step1 = {"output_form": "unusable", "earlier_answer": None, "claims": []}
    step2 = {
        "gold_points": [{"gold_point_id": g["id"], "covered": "no"} for g in s2["gold_points"]],
        "choice_correct": "no",
        "premise_handling": "ignored" if s2["premise"] is not None else None,
        "overall": 1,
        "item_issue": text,
        "note": text,
    }
    return L.assemble_record(rec_export["grading_id"], step1, step2, guide_version="sha256:test",
                             has_premise=s2["premise"] is not None, today="2026-01-01")


def grade_all(round_dir, upto=None):
    export = L.load_export(round_dir)
    for rec in export[:upto]:
        L.save_label(round_dir, fill(rec))
    return export


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "validation" / "labels_first.jsonl", tmp_path / "validation" / "grading_log.md"


def test_lock_refuses_partial_labels(rd, paths):
    grade_all(rd, upto=3)
    committed, log = paths
    with pytest.raises(GradingError, match="complete"):
        lock.lock_round(rd, committed=committed, log=log)
    assert not committed.exists() and not log.exists() and not (rd / NOTES_FILE).exists()


def test_lock_strips_free_text_and_logs_the_hash(rd, paths):
    export = grade_all(rd)
    committed, log = paths
    entry = lock.lock_round(rd, committed=committed, log=log, today="2026-01-02")
    locked = read_jsonl(committed)
    assert len(locked) == len(export) == entry["traces"]
    text = committed.read_text()
    assert "free text here" not in text
    for rec in locked:
        assert not set(rec) & {"text_issue", "item_issue", "note"}
        assert all("note" not in c for c in rec["claims"])
    notes = read_jsonl(rd / NOTES_FILE)
    assert [n["grading_id"] for n in notes] == [r["grading_id"] for r in locked]
    assert notes[0]["note"] == "free text here" and notes[0]["item_issue"] == "free text here"
    log_text = log.read_text()
    assert sha256_file(rd / LABELS_FILE) in log_text and "2026-01-02" in log_text
    assert sha256_file(committed) == entry["committed_sha256"]
    with pytest.raises(GradingError, match="exists"):
        lock.lock_round(rd, committed=committed, log=log)


def test_split_free_text_handles_claims_and_failures():
    rec = {"grading_id": "g", "text_issue": "t", "item_issue": None, "note": "n",
           "claims": [{"claim_index": 0, "supported": "no", "note": "c"}],
           "failures": [{"code": "other", "claim_index": 0, "note": "f"}]}
    (s,), (n,) = lock.split_free_text([rec])
    assert s == {"grading_id": "g", "claims": [{"claim_index": 0, "supported": "no"}],
                 "failures": [{"code": "other", "claim_index": 0}]}
    assert n["claims"] == [{"claim_index": 0, "note": "c"}]
    assert n["failures"] == [{"code": "other", "claim_index": 0, "note": "f"}]
    assert (n["text_issue"], n["note"]) == ("t", "n")
