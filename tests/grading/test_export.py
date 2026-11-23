"""The blind export: nothing hidden gets out; direction details, marks and the
earlier-answer rule come out right, for traces shaped like both source runs."""

from __future__ import annotations

import json
import re

import pytest

from .helpers import ITEMS, RUN_DIRECTION, RUN_PARENT_CHILD, RUNS, SENTINEL_STRINGS, STORE, saved_traces

import export  # noqa: E402  (path set up by helpers)
from grading_common import EXPORT_FILE, read_jsonl  # noqa: E402
from neuro_grounded_qa.tools.kg import GROUPED_HIERARCHY_LABEL, SUBTYPE_LABEL  # noqa: E402
from neuro_grounded_qa.tools.store import load_store  # noqa: E402


@pytest.fixture(scope="module")
def built():
    """Export records for every fixture trace of both runs: {(run, n): record}."""
    items = export.load_items(ITEMS)
    store = load_store(STORE)
    out = {}
    for run in RUNS:
        for saved in saved_traces(run):
            n = saved["task"]["data"]["idx"]
            item = items[saved["task"]["data"]["id"]]
            out[(run.name, n)] = export.build_record(saved, item, store, f"g-{n:08x}", n + 1)
    return out


def records_of(built, kind):
    """Records whose trace follows script ``kind`` (fixture item number mod 5)."""
    return [r for (run, n), r in built.items() if n % 5 == kind]


def test_no_forbidden_key_in_any_record(built):
    for rec in built.values():
        assert export.forbidden_keys(rec) == []


def test_no_hidden_value_reaches_the_export(built):
    text = json.dumps(list(built.values()))
    for s in SENTINEL_STRINGS:
        assert s not in text, s


def test_forbidden_keys_are_caught():
    rec = {"grading_id": "g", "position": 1,
           "step1": {k: None for k in export.STEP1_KEYS},
           "step2": {k: None for k in export.STEP2_KEYS}}
    assert export.forbidden_keys(rec) == []
    for path, key in (((), "model"), (("step1",), "metrics"), (("step2",), "qtype"),
                      (("step2",), "source"), (("step1",), "reasoning_content")):
        bad = json.loads(json.dumps(rec))
        target = bad
        for p in path:
            target = target[p]
        target[key] = "x"
        assert export.forbidden_keys(bad), key
    nested = json.loads(json.dumps(rec))
    nested["step1"]["tool_calls"] = [{"results": [{"payload": {"reward": 1}}]}]
    assert export.forbidden_keys(nested)


def test_written_export_is_clean_and_checks_pass(pristine_round):
    out = pristine_round
    recs = read_jsonl(out / EXPORT_FILE)
    assert len(recs) == 16
    text = (out / EXPORT_FILE).read_text()
    for s in SENTINEL_STRINGS:
        assert s not in text, s
    assert "q-0000000000" not in text, "item IDs must stay in the key file"
    rows = export.check_export(out, expect=16)
    assert all(ok for _, ok, _ in rows), rows


def test_check_prints_pass_fail_only(pristine_round, capsys):
    """The grader runs --check, so it prints check names and pass/fail: no digit but
    the record total it was told to expect, no run name and no hash."""
    rc = export.main(["--check", "--dir", str(pristine_round), "--expect", "16"])
    out = capsys.readouterr().out
    assert rc == 0 and out.splitlines()[-1] == "export check: PASS (16 records expected)"
    assert not any(ch.isdigit() for ch in re.sub(r"\b16\b", "", out)), out
    assert all(re.fullmatch(r"(PASS|FAIL)  [A-Za-z -]+", ln) for ln in out.splitlines()[:-1])
    for word in ("sha256", "runs/", "sentinel", "parent-child", "direction-labels"):
        assert word not in out


def test_check_fails_without_details(pristine_round, tmp_path, capsys):
    import shutil

    rd = tmp_path / "copy"
    shutil.copytree(pristine_round, rd)
    rc = export.main(["--check", "--dir", str(rd), "--expect", "17"])
    out = capsys.readouterr().out
    assert rc == 1 and "FAIL  record count" in out
    assert not any(ch.isdigit() for ch in re.sub(r"\b17\b", "", out)), out


def _directed(rec):
    return {ev["id"]: ev for ev in export.directed_edges(rec)}


@pytest.mark.parametrize("run,plain,grouped", [
    (RUN_PARENT_CHILD.name, "parent-child", "parent-child"),
    (RUN_DIRECTION.name, SUBTYPE_LABEL, GROUPED_HIERARCHY_LABEL),
])
def test_direction_details_on_directed_edges(built, run, plain, grouped):
    rec = built[(run, 0)]
    edges = _directed(rec)
    assert set(edges) == {"E:000010", "E:000011"}
    d10, d11 = edges["E:000010"]["direction"], edges["E:000011"]["direction"]
    assert d10 == {"stored_order": {"subject_id": "DIS_3", "object_id": "DIS_P"},
                   "served_label": plain, "primekg_label": "parent-child", "grouped_end": False}
    assert d11 == {"stored_order": {"subject_id": "DIS_P", "object_id": "group:DIS_GRP"},
                   "served_label": grouped, "primekg_label": "parent-child", "grouped_end": True}
    assert edges["E:000010"]["display_relation"] == plain
    undirected = [ev for c in rec["step1"]["tool_calls"] for r in c["results"]
                  for ev in r["payload"]["evidence"] if not ev["directed"]]
    assert undirected and all("direction" not in ev for ev in undirected)


def test_unseen_edge_id_calls_are_marked(built):
    for rec in records_of(built, 1):
        calls = rec["step1"]["tool_calls"]
        assert [c["unseen_edge_ids"] for c in calls] == [[], ["E:000004"]]
    for rec in records_of(built, 2):
        # get_edge on E:000001 in the same turn as the call whose result first shows it.
        assert [c["unseen_edge_ids"] for c in rec["step1"]["tool_calls"]] == [[], ["E:000001"]]
    for rec in records_of(built, 0):
        assert all(c["unseen_edge_ids"] == [] for c in rec["step1"]["tool_calls"])


def test_rejected_and_unmatched_results_are_marked(built):
    for rec in records_of(built, 2):
        c1, c2 = rec["step1"]["tool_calls"]
        assert [r["status"] for r in c1["results"]] == ["accepted", "rejected"]
        dup = c1["results"][1]
        assert dup["reason"] == "duplicate_result" and dup["payload"]["evidence"][0]["id"] == "E:000003"
        (bad,) = c2["results"]
        assert bad["status"] == "rejected" and bad["reason"] == "unparseable"
        assert bad["payload"] is None and bad["raw"] == "Error: fixture tool failure"
        (ghost,) = rec["step1"]["unmatched_tool_results"]
        assert ghost["status"] == "rejected" and ghost["reason"] == "no_matching_call"
    for rec in records_of(built, 0):
        assert rec["step1"]["unmatched_tool_results"] == []
        assert all(r["status"] == "accepted" for c in rec["step1"]["tool_calls"] for r in c["results"])


def test_earlier_answer_only_when_the_final_message_has_no_text(built):
    for rec in records_of(built, 3):
        s1 = rec["step1"]
        assert s1["final_message"] == {"raw": "", "layout": None}
        lay = s1["earlier_answer"]["layout"]
        assert [c["claim_index"] for c in lay["claims"]] == [0]
        assert lay["claims"][0]["cites"] == ["E:000001"]
    for kind in (0, 1, 2, 4):
        for rec in records_of(built, kind):
            assert rec["step1"]["earlier_answer"] is None
            assert rec["step1"]["final_message"]["raw"]


def test_final_message_raw_and_layout(built):
    for rec in records_of(built, 0):
        lay = rec["step1"]["final_message"]["layout"]
        assert [c["claim_index"] for c in lay["claims"]] == [0, 1]
        assert set(lay) == {"claims", "answer_text", "unsupported_parts"}
    for rec in records_of(built, 1):
        assert rec["step1"]["final_message"]["layout"] == {"abstain": True,
                                                           "reason": "Fixture abstention."}
    for rec in records_of(built, 2):  # prose around the JSON: raw only
        fm = rec["step1"]["final_message"]
        assert fm["raw"].startswith("Here is my answer:") and fm["layout"] is None


def test_step2_holds_gold_premise_and_gaps(built):
    items = export.load_items(ITEMS)
    for (_, n), rec in built.items():
        s2 = rec["step2"]
        assert set(s2) == set(export.STEP2_KEYS)
        item = next(i for i in items.values() if i.prompt_text == rec["step1"]["question"])
        assert s2["answerable"] == item.answerable
        assert [g["id"] for g in s2["gold_points"]] == [g.id for g in item.gold_points]
        assert (s2["premise"] is None) == (item.premise is None)
        assert len(s2["graph_gaps"]) == len(item.graph_gaps)
        for g in s2["gold_points"]:
            assert {"directed", "evidence", "subject_name", "object_name"} <= set(g)
    yes_directed = [r for r in built.values() if r["step2"]["gold_points"]
                    and r["step2"]["gold_points"][0]["directed"]]
    assert yes_directed and yes_directed[0]["step2"]["gold_points"][0]["subject_name"] == "Disease Three"
