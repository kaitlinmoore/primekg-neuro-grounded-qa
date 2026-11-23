"""The viewer's save and resume logic, and the app itself driven headlessly with
Streamlit's AppTest (no browser). Fixture traces only; every value entered is
synthetic test data."""

from __future__ import annotations

import pytest
import streamlit.config as st_config
from streamlit.testing.v1 import AppTest

from .helpers import GRADING, SENTINEL_STRINGS
from .test_lock import fill

import label_store as L  # noqa: E402  (path set up by helpers)
from grading_common import LABELS_FILE, PROGRESS_FILE, GradingError, read_jsonl  # noqa: E402


def test_save_appends_in_order_and_never_overwrites(rd):
    export = L.load_export(rd)
    assert L.next_index(export, []) == 0
    L.save_label(rd, fill(export[0]))
    first = (rd / LABELS_FILE).read_bytes()
    with pytest.raises(GradingError, match="already saved"):
        L.save_label(rd, fill(export[0], text="changed"))
    with pytest.raises(GradingError, match="not the next"):
        L.save_label(rd, fill(export[2]))
    assert (rd / LABELS_FILE).read_bytes() == first
    L.save_label(rd, fill(export[1]))
    assert (rd / LABELS_FILE).read_bytes().startswith(first)
    labels = L.load_labels(rd)
    assert [r["grading_id"] for r in labels] == [e["grading_id"] for e in export[:2]]
    assert L.next_index(export, labels) == 2


def test_save_refuses_an_invalid_record(rd):
    export = L.load_export(rd)
    rec = fill(export[0])
    rec["overall"] = None  # never filled in for the grader
    with pytest.raises(GradingError, match="overall"):
        L.save_label(rd, rec)
    assert not (rd / LABELS_FILE).exists()


def test_assemble_record_fills_nothing_in(rd):
    export = L.load_export(rd)
    step1 = {"output_form": "valid", "earlier_answer": "present",
             "claims": [{"supported": "no", "cites_unretrieved": "yes", "relevant": "no",
                         "duplicate_of": None, "note": "  "}]}
    rec = L.assemble_record(export[0]["grading_id"], step1, {}, guide_version="v",
                            has_premise=False, today="2026-01-01")
    assert rec["earlier_answer"] is None, "doesn't apply to a non-empty output"
    for k in ("response", "choice_correct", "flag", "text_ok", "overall", "premise_handling"):
        assert rec[k] is None, k
    assert rec["claims"][0]["note"] is None and rec["claims"][0]["premise_restatement"] is None
    assert rec["gold_points"] == []


def test_reveal_locks_step1_and_resumes(rd):
    export = L.load_export(rd)
    gid = export[0]["grading_id"]
    with pytest.raises(GradingError, match="output_form"):
        L.reveal(rd, gid, {"output_form": None, "earlier_answer": None, "claims": []})
    assert L.load_progress(rd, gid) is None
    step1 = {"output_form": "unusable", "earlier_answer": None, "claims": []}
    L.reveal(rd, gid, step1)
    assert L.load_progress(rd, gid)["step1"] == step1
    with pytest.raises(GradingError, match="already revealed"):
        L.reveal(rd, gid, step1)
    with pytest.raises(GradingError, match="another trace"):
        L.load_progress(rd, export[1]["grading_id"])
    L.save_label(rd, fill(export[0]))
    assert not (rd / PROGRESS_FILE).exists()


def test_step1_errors_require_every_claim_field():
    errs = L.step1_errors({"output_form": "valid", "earlier_answer": None,
                           "claims": [{"supported": "cited", "cites_unretrieved": None,
                                       "relevant": "yes"}]})
    assert errs == ["claims[0].cites_unretrieved: not set"]
    assert L.step1_errors({"output_form": "empty", "earlier_answer": None, "claims": []}) == [
        "earlier_answer: not set"]


# --- The app, headless -------------------------------------------------------------------


def _app(rd, monkeypatch, local=True) -> AppTest:
    monkeypatch.setenv("NGQ_GRADING_DIR", str(rd))
    st_config.set_option("server.address", "127.0.0.1" if local else "0.0.0.0")
    st_config.set_option("browser.gatherUsageStats", not local)
    at = AppTest.from_file(str(GRADING / "app.py"), default_timeout=60)
    at.run()
    return at


def _page_text(at: AppTest) -> str:
    parts = [str(e.value) for kind in ("title", "header", "subheader", "markdown", "caption",
                                       "code", "warning", "info", "error") for e in getattr(at, kind)]
    parts += [str(df.value) for df in at.dataframe]
    return "\n".join(parts)


def test_app_refuses_without_localhost_and_stats_off(rd, monkeypatch):
    at = _app(rd, monkeypatch, local=False)
    assert at.error and "localhost" in at.error[0].value
    assert not at.title


def test_app_grades_one_trace_and_resumes(rd, monkeypatch):
    export = L.load_export(rd)
    first = export[0]
    at = _app(rd, monkeypatch)
    assert not at.exception
    assert at.title[0].value == f"Trace 1 of {len(export)}"
    text = _page_text(at)
    assert "answerable" not in text and "Premise" not in text, "gold shown before reveal"
    for s in SENTINEL_STRINGS:
        assert s not in text, s
    assert all(r.value is None for r in at.radio), "a field was pre-selected"

    at.radio(key=f"{first['grading_id']}:output_form").set_value("unusable").run()
    at.button[0].click().run()
    assert not at.exception and not at.error
    assert "answerable" in _page_text(at)
    gid = first["grading_id"]
    for g in first["step2"]["gold_points"]:
        at.radio(key=f"{gid}:gp:{g['id']}").set_value("no")
    if first["step2"]["premise"] is not None:
        at.radio(key=f"{gid}:premise_handling").set_value("ignored")
    at.radio(key=f"{gid}:choice_correct").set_value("no")
    at.radio(key=f"{gid}:overall").set_value(1)
    at.run()
    save = next(b for b in at.button if b.label.startswith("Save"))
    save.click().run()
    assert not at.exception and not at.error, [e.value for e in at.error]
    (rec,) = read_jsonl(rd / LABELS_FILE)
    assert rec["grading_id"] == gid and rec["output_form"] == "unusable"
    assert rec["response"] is None and rec["claims"] == []
    assert at.title[0].value == f"Trace 2 of {len(export)}"

    again = _app(rd, monkeypatch)
    assert again.title[0].value == f"Trace 2 of {len(export)}"
