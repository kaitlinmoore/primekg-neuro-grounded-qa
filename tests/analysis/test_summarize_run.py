"""``scripts/analysis/summarize_run.py`` on the committed stub-run traces.

The fixture run (``tests/fixtures/analysis/stub_run``) is a real ``vf-run`` through
``scripts/run.sh`` against the scripted stub endpoint, one scripted behavior per
fixture item (see ``helpers.STUB_RUN``). The expected values below follow from those
scripts and the fixture items, not from a model.
"""

from __future__ import annotations

import copy
import json
import shutil

import pytest

from .helpers import FIXTURES, STUB_RUN

import summarize_run as sr  # noqa: E402  (path set up by helpers)

MEMBERS = {"DIS_G_MEMBER": "DIS_G", "DIS_G_OTHER": "DIS_G"}


@pytest.fixture(scope="module")
def saved():
    return {t["task"]["data"]["id"]: t for t in sr.load_traces(STUB_RUN)}


@pytest.fixture(scope="module")
def rows(saved):
    return {k: sr.trace_row(t, MEMBERS, 8) for k, t in saved.items()}


def test_loads_every_rollout(saved):
    assert set(saved) == {"fx-single-001", "fx-multi-001", "fx-partial-001",
                          "fx-unans-001", "fx-falsep-001", "fx-falsep-002"}


def test_offline_rescore_and_provenance(rows):
    for row in rows.values():
        assert row.rescore_matches, row.item_id
        assert row.provenance_missing == []
        assert row.recorded_rejections_match
        assert row.rejections == {}


def test_budget_hit(rows):
    r = rows["fx-single-001"]
    assert r.tool_calls == 10 and r.over_budget
    assert r.budget_notes == 2  # calls 9 and 10
    assert r.reward == 1.0  # the gold edges came first


def test_relation_filter_and_unknown_relation(rows):
    r = rows["fx-multi-001"]
    assert r.calls_by_tool == {"kg_search_entities": 1, "kg_get_neighbors": 3, "kg_get_edge": 1}
    assert (r.neighbors_calls, r.neighbors_with_relation, r.unknown_relation_notes) == (3, 2, 1)
    assert r.metrics["duplicate_claims"] == 1.0
    assert not r.over_budget


def test_parse_failure_and_abstentions(rows):
    assert (rows["fx-partial-001"].outcome, rows["fx-partial-001"].parse_reason) == ("parse_failure", "not_json")
    assert rows["fx-unans-001"].outcome == "abstain" and rows["fx-unans-001"].reward == 1.0
    assert rows["fx-falsep-002"].outcome == "abstain" and rows["fx-falsep-002"].reward == pytest.approx(0.7)


def test_group_member_misuse(saved, rows):
    r = rows["fx-falsep-001"]
    assert r.group_misuse == [{"claim": "0", "role": "object_id", "member": "DIS_G_MEMBER", "group": "DIS_G"}]
    assert r.metrics["voided_claims"] == 1.0 and r.metrics["stripped_cites"] == 1.0
    assert sr.trace_row(saved["fx-falsep-001"], {}, 8).group_misuse == []


def test_template_family():
    assert sr.family_of("mh_bridge.shared_phenotype") == "mh_bridge"
    assert sr.family_of("pa_list2") == "pa_list2"
    assert sr.family_of(None) == "none"


def test_duplicate_result_is_counted_per_reason_and_tool(saved):
    """A second result for an answered call: rejected as duplicate_result, named by
    its tool, and flagged as disagreeing with the recorded metrics."""
    t = copy.deepcopy(saved["fx-partial-001"])
    tool_node = next(n for n in t["nodes"] if n["message"]["role"] == "tool")
    t["nodes"].append(copy.deepcopy(tool_node))
    row = sr.trace_row(t, {}, 8)
    assert row.rejections == {"duplicate_result": 1}
    assert row.rejections_by_tool == {"duplicate_result/kg_get_edge": 1}
    assert not row.recorded_rejections_match


def test_rescore_mismatch_and_missing_provenance_are_flagged(saved):
    t = copy.deepcopy(saved["fx-unans-001"])
    t["rewards"]["grounding_reward"]["score"] = 0.5
    del t["info"]["items_sha256"]
    row = sr.trace_row(t, {}, 8)
    assert not row.rescore_matches and row.rescore == 1.0
    assert row.provenance_missing == ["items_sha256"]


def test_tokens_are_summed_per_call(saved, rows):
    r = rows["fx-multi-001"]
    calls = saved["fx-multi-001"]["calls"]
    assert r.model_calls == len(calls) == 4
    assert r.input_tokens == sum(c["usage"]["prompt_tokens"] for c in calls)
    assert r.output_tokens == sum(c["usage"]["completion_tokens"] for c in calls)


def test_aggregate_overall_and_by_type(saved):
    s = sr.summarize(list(saved.values()), sr.load_config(STUB_RUN), MEMBERS)
    o = s["overall"]
    assert o["n"] == 6
    assert o["reward"] == pytest.approx((1 + 1 + 0 + 1 + 0 + 0.7) / 6)
    assert o["parse_failure_rate"] == pytest.approx(1 / 6)
    assert o["parse_failure_reasons"] == {"not_json": 1}
    assert o["abstention_by_answerable"] == {
        "correctable": {"n": 1, "abstained": 1}, "no": {"n": 2, "abstained": 1},
        "partial": {"n": 1, "abstained": 0}, "yes": {"n": 2, "abstained": 0}}
    assert o["over_budget"] == 1 and o["budget_notes"] == 2
    assert o["rejections"] == {r: 0 for r in o["rejections"]} and len(o["rejections"]) == 5
    assert o["group_misuse_claims"] == 1 and o["rescore_matches"] == 6 and o["provenance_complete"] == 6
    assert s["max_calls"] == 8 and s["model"] == "stub/mixed"
    assert s["by_qtype"]["false_premise"]["n"] == 2
    assert set(s["by_family"]) == {"fx_drugs_via_gene", "fx_entity_assoc_disease", "fx_genes_assoc_disease",
                                   "fx_partial_assoc_plus_absent", "fx_why_drug_indicated"}
    # Components average only over rollouts that define them: two abstentions and a
    # parse failure leave three claim-making rollouts for citation_validity.
    assert o["n_citation_validity"] == 3


def test_group_members_loader():
    members = sr.load_group_members(FIXTURES)
    assert members == MEMBERS
    assert sr.load_group_members(FIXTURES / "stub_run" / "traces.jsonl") == {}  # a store file: no groups
    assert sr.load_group_members(FIXTURES / "absent") is None


def test_cli_writes_summaries_and_compares(tmp_path, capsys):
    a, b = tmp_path / "a", tmp_path / "b"
    shutil.copytree(STUB_RUN, a)
    shutil.copytree(STUB_RUN, b)
    cfg = json.loads((b / "configs/resolved/run.json").read_text())
    cfg["model"] = "stub/other"
    (b / "configs/resolved/run.json").write_text(json.dumps(cfg))
    assert sr.main([str(a), str(b / "traces.jsonl"), "--store", str(FIXTURES)]) == 0
    out = capsys.readouterr().out
    assert "# Runs side by side" in out and "stub/other" in out
    md = (a / "summary.md").read_text()
    assert "Offline rescore equals the recorded reward: 6/6" in md
    assert "Group-ID misuse" in md and "1 claims in 1 rollouts" in md
    assert json.loads((a / "summary.json").read_text())["overall"]["n"] == 6
    # No question text in the summary.
    for t in sr.load_traces(STUB_RUN):
        prompt = t["task"]["data"]["prompt"]
        text = prompt if isinstance(prompt, str) else json.dumps(prompt)
        assert text[:40] not in md


def _record_v11(t, n, over, penalty):
    """A copy of a fixture trace as if reward v1.1 had scored it, with the given
    precision metrics recorded. (The rescore check isn't the point here.)"""
    t = copy.deepcopy(t)
    t["info"]["reward_params"] = {**t["info"]["reward_params"], "mu": 1.0, "nongold_allowance": 3,
                                  "version": "v1.1"}
    t["metrics"].update({"nongold_supported_facts": float(n), "nongold_over_allowance": float(over),
                         "precision_penalty": penalty})
    return t


def test_traces_before_v1_record_no_precision_metrics(rows):
    for row in rows.values():
        assert row.reward_version == "v0"
        assert (row.nongold_supported_facts, row.nongold_over_allowance, row.precision_penalty) == (None,) * 3
    agg = sr.precision_aggregate(list(rows.values()))
    assert agg["reward_versions"] == {"v0": 6} and agg["precision_recorded"] == 0
    assert agg["precision_penalty_charged"] == 0


def test_precision_metrics_are_read_and_counted(saved):
    traces = {k: copy.deepcopy(t) for k, t in saved.items()}
    traces["fx-single-001"] = _record_v11(saved["fx-single-001"], 5, 2, 0.25)  # yes: charged
    traces["fx-multi-001"] = _record_v11(saved["fx-multi-001"], 2, 0, 0.0)     # within the allowance
    traces["fx-falsep-001"] = _record_v11(saved["fx-falsep-001"], 4, 1, 0.5)   # `no`: recorded, not charged
    traces["fx-unans-001"] = _record_v11(saved["fx-unans-001"], 0, 0, 0.0)     # abstention: not an answer
    row = sr.trace_row(traces["fx-single-001"], {}, 8)
    assert row.reward_version == "v1.1"
    assert (row.nongold_supported_facts, row.nongold_over_allowance, row.precision_penalty) == (5, 2, 0.25)

    s = sr.summarize(list(traces.values()), sr.load_config(STUB_RUN), MEMBERS)
    o = s["overall"]
    assert o["reward_versions"] == {"v0": 2, "v1.1": 4}
    assert o["precision_recorded"] == 3  # answers only
    assert (o["nongold_any"], o["nongold_over_allowance_answers"]) == (3, 2)
    assert (o["nongold_supported_facts_sum"], o["nongold_supported_facts_max"]) == (11, 5)
    assert o["precision_penalty_charged"] == 1
    assert o["precision_penalty_charged_sum"] == pytest.approx(0.25)
    assert s["by_qtype"]["single_hop"]["precision_penalty_charged"] == 1
    assert s["by_qtype"]["false_premise"]["precision_penalty_charged"] == 0
    # The other aggregates don't move.
    base = sr.summarize(list(saved.values()), sr.load_config(STUB_RUN), MEMBERS)["overall"]
    for key in ("n", "parse_failure_rate", "abstention_by_answerable", "over_budget", "claims"):
        assert o[key] == base[key]

    md = sr.to_markdown(s, "v11")
    assert "## Precision term (reward v1 and later)" in md
    assert "Reward versions recorded: `v0` 2, `v1.1` 4." in md
    assert "N > 0 in 3, N past the allowance in 2" in md
    assert "items other than `no`): 1 answers, summed penalty 0.250" in md
    assert "| type | answers | N > 0 | N > a | penalty > 0 | penalty sum |" in md
