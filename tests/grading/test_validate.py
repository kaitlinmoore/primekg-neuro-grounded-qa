"""The label schema and validator: the guide's JSON form is accepted, off-schema
records are rejected. Every record here is synthetic test data, not a grade."""

from __future__ import annotations

import copy
import json
import re

import pytest

from .helpers import REPO

import label_schema as S  # noqa: E402  (path set up by helpers)
from validate import validate_labels  # noqa: E402

CTX_PLAIN = S.ExportContext(has_premise=False, gold_point_ids=("gp0", "gp1"))
CTX_PREMISE = S.ExportContext(has_premise=True, gold_point_ids=("gp0",))


def claim(i: int, **kw) -> dict:
    c = {"claim_index": i, "supported": "cited", "cites_unretrieved": "no", "relevant": "yes",
         "premise_restatement": None, "duplicate_of": None, "note": None}
    c.update(kw)
    return c


def record(**kw) -> dict:
    """A shape-valid answered record on a plain item (synthetic values)."""
    r = {
        "grading_id": "g-00000001", "round": "first", "guide_version": "sha256:abc",
        "graded_on": "2026-01-01", "output_form": "valid", "earlier_answer": None,
        "response": "answered", "choice_correct": "yes", "abstain_reason_ok": None,
        "flag": "none", "premise_handling": None, "text_ok": "ok", "text_issue": None,
        "overall": 3, "claims": [claim(0), claim(1, duplicate_of=0)],
        "gold_points": [{"gold_point_id": "gp0", "covered": "yes"},
                        {"gold_point_id": "gp1", "covered": "cited_not_stated"}],
        "failures": None, "item_issue": None, "note": None,
    }
    r.update(kw)
    return r


def guide_json_form() -> dict:
    """The JSON block in section 3 of the committed guide."""
    text = (REPO / "docs" / "labeling_guide.md").read_text(encoding="utf-8")
    (block,) = re.findall(r"```json\n(.*?)```", text, re.S)
    return json.loads(block)


def _values(s: str) -> list[str]:
    return [v.strip() for v in s.split("|") if v.strip() != "null"]


def test_schema_values_match_the_guide():
    form = guide_json_form()
    assert set(form) == set(S.RECORD_KEYS)
    assert form["round"] == "first"
    for key, allowed in (("output_form", S.OUTPUT_FORMS), ("earlier_answer", S.EARLIER_ANSWER),
                         ("response", S.RESPONSES), ("choice_correct", S.YES_NO),
                         ("abstain_reason_ok", S.TEXT_SCALE), ("flag", S.FLAGS),
                         ("premise_handling", S.PREMISE_HANDLING), ("text_ok", S.TEXT_SCALE)):
        assert tuple(_values(form[key])) == allowed, key
    assert [int(v) for v in _values(form["overall"])] == list(S.OVERALL)
    (c,) = form["claims"]
    assert set(c) == set(S.CLAIM_KEYS)
    assert tuple(_values(c["supported"])) == S.SUPPORTED
    assert tuple(_values(c["premise_restatement"])) == S.YES_NO
    (g,) = form["gold_points"]
    assert set(g) == set(S.GOLD_KEYS) and tuple(_values(g["covered"])) == S.COVERED


@pytest.mark.parametrize("rec,ctx", [
    (record(), CTX_PLAIN),
    (record(), None),
    (record(failures=[{"code": "miscitation", "claim_index": 0, "note": None}]), CTX_PLAIN),
    # abstention: no claims, reason rated, text_issue required when not ok
    (record(response="abstained", flag=None, text_ok=None, abstain_reason_ok="minor",
            text_issue="reason overstates the gap", claims=[]), CTX_PLAIN),
    # flag-only answer
    (record(response="flag_only", flag="names_unsupported_part", text_ok=None, claims=[]), CTX_PLAIN),
    # unusable: nothing but choice_correct and overall
    (record(output_form="unusable", response=None, flag=None, text_ok=None, claims=[],
            overall=1, choice_correct="no"), CTX_PLAIN),
    # empty with an earlier answer graded
    (record(output_form="empty", earlier_answer="present"), CTX_PLAIN),
    # empty with none
    (record(output_form="empty", earlier_answer="none", response=None, flag=None,
            text_ok=None, claims=[]), CTX_PLAIN),
    # premise item
    (record(premise_handling="corrected_supported",
            claims=[claim(0, premise_restatement="no")],
            gold_points=[{"gold_point_id": "gp0", "covered": "yes"}]), CTX_PREMISE),
])
def test_valid_records_pass(rec, ctx):
    assert S.validate_record(rec, ctx) == []


@pytest.mark.parametrize("change,ctx,match", [
    ({"output_form": "ok"}, CTX_PLAIN, "output_form"),
    ({"overall": "2"}, CTX_PLAIN, "overall"),
    ({"overall": True}, CTX_PLAIN, "overall"),
    ({"overall": 4}, CTX_PLAIN, "overall"),
    ({"choice_correct": None}, CTX_PLAIN, "choice_correct: missing"),
    ({"round": "second"}, CTX_PLAIN, "round"),
    ({"graded_on": "22/11/2026"}, CTX_PLAIN, "graded_on"),
    ({"earlier_answer": "none"}, CTX_PLAIN, "earlier_answer: must be null"),
    ({"text_ok": "minor"}, CTX_PLAIN, "text_issue: required"),
    ({"text_issue": "something"}, CTX_PLAIN, "text_issue: must be null"),
    ({"text_ok": None}, CTX_PLAIN, "text_ok: missing"),
    ({"abstain_reason_ok": "ok"}, CTX_PLAIN, "abstain_reason_ok: must be null"),
    ({"flag": "maybe"}, CTX_PLAIN, "flag"),
    ({"output_form": "unusable"}, CTX_PLAIN, "response: must be null"),
    ({"premise_handling": "accepted"}, CTX_PLAIN, "premise_handling: must be null"),
    ({}, CTX_PREMISE, "premise_handling: missing"),
    ({"gold_points": [{"gold_point_id": "gp1", "covered": "yes"},
                      {"gold_point_id": "gp0", "covered": "yes"}]}, CTX_PLAIN, "gold_points"),
    ({"gold_points": [{"gold_point_id": "gp0", "covered": "partly"}]}, None, "covered"),
    ({"extra": 1}, CTX_PLAIN, "unknown keys"),
    ({"note": "  "}, CTX_PLAIN, "note"),
    ({"failures": [{"code": "x"}]}, CTX_PLAIN, "failures"),
])
def test_off_schema_records_fail(change, ctx, match):
    rec = record(**change)
    errors = S.validate_record(rec, ctx)
    assert any(match in e for e in errors), errors


def test_claim_errors():
    bad = record(claims=[claim(1)])
    assert any("claim_index" in e for e in S.validate_record(bad, CTX_PLAIN))
    bad = record(claims=[claim(0, duplicate_of=0)])
    assert any("duplicate_of" in e for e in S.validate_record(bad, CTX_PLAIN))
    bad = record(claims=[claim(0, supported="yes")])
    assert any("supported" in e for e in S.validate_record(bad, CTX_PLAIN))
    bad = record(claims=[claim(0, premise_restatement="no")])
    assert any("premise_restatement: must be null" in e for e in S.validate_record(bad, CTX_PLAIN))
    bad = record(claims=[claim(0)], premise_handling="ignored",
                 gold_points=[{"gold_point_id": "gp0", "covered": "no"}])
    assert any("premise_restatement: missing" in e for e in S.validate_record(bad, CTX_PREMISE))
    bad = record(output_form="unusable", response=None, flag=None, text_ok=None)
    assert any("claims: must be []" in e for e in S.validate_record(bad, CTX_PLAIN))
    rec = record()
    del rec["claims"][0]["note"]
    assert any("keys must be" in e for e in S.validate_record(rec, CTX_PLAIN))


def test_missing_failures_key_is_allowed():
    rec = record()
    del rec["failures"]
    assert S.validate_record(rec, CTX_PLAIN) == []


def _export(*gids):
    return [{"grading_id": g, "position": n + 1,
             "step2": {"premise": None, "gold_points": [{"id": "gp0"}, {"id": "gp1"}]}}
            for n, g in enumerate(gids)]


def test_validate_labels_order_and_completeness():
    exp = _export("g-1", "g-2", "g-3")
    one = record(grading_id="g-1")
    two = record(grading_id="g-2")
    assert validate_labels([one, two], exp).ok
    rep = validate_labels([one, two], exp, complete=True)
    assert not rep.ok and rep.graded == 2 and rep.total == 3
    assert not validate_labels([two], exp).ok, "skipping a trace"
    assert not validate_labels([one, copy.deepcopy(one)], exp).ok, "graded twice"
    assert not validate_labels([record(grading_id="g-9")], exp).ok, "unknown grading_id"
    full = [record(grading_id=g) for g in ("g-1", "g-2", "g-3")]
    assert validate_labels(full, exp, complete=True).ok


def test_applies_rules():
    a = S.applies("empty", "present", "answered", False, "minor")
    assert a.claims and a.response and a.flag and a.text_ok and a.text_issue
    a = S.applies("empty", "none")
    assert not a.claims and not a.response and a.earlier_answer
    a = S.applies("mixed", None, "abstained", True, None, "ok")
    assert a.abstain_reason_ok and not a.text_issue and a.premise_handling
    a = S.applies("unusable", None, None, True)
    assert not a.claims and a.premise_handling and not a.earlier_answer
