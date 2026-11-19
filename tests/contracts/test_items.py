"""Contract tests for the question item schema."""

import json

import pytest
import verifiers.v1 as vf
from pydantic import ValidationError

from contracts.builders import FIXTURES, TraceBuilder
from neuro_grounded_qa.contracts import QTYPE_ANSWERABLE, QuestionItem


def load_items() -> list[dict]:
    lines = (FIXTURES / "items.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


@pytest.fixture
def raw_items() -> list[dict]:
    return load_items()


def by_qtype(raw_items, qtype) -> dict:
    return next(dict(r) for r in raw_items if r["qtype"] == qtype)


def test_fixture_items_validate_one_per_qtype(raw_items):
    items = [QuestionItem.model_validate(r) for r in raw_items]
    assert sorted(i.qtype for i in items) == sorted(QTYPE_ANSWERABLE)


def test_item_is_task_data(raw_items):
    item = QuestionItem.model_validate(raw_items[0])
    assert isinstance(item, vf.TaskData)
    assert item.prompt_text == raw_items[0]["prompt"]


def test_items_work_as_task_and_taskset_data(raw_items):
    class ItemTask(vf.Task[QuestionItem]):
        pass

    class ItemTaskset(vf.Taskset[ItemTask, vf.TasksetConfig]):
        def load(self):
            return [ItemTask(QuestionItem.model_validate(r), self.config.task) for r in raw_items]

    tasks = list(ItemTaskset(vf.TasksetConfig()))
    assert [t.data.idx for t in tasks] == list(range(len(raw_items)))
    assert all(isinstance(t.data, QuestionItem) for t in tasks)
    assert len({t.key for t in tasks}) == len(tasks)


def test_item_survives_trace_round_trip(raw_items):
    # The reward reads gold off trace.task.data, so it must survive serialization.
    item = QuestionItem.model_validate(by_qtype(raw_items, "multi_hop"))
    trace = TraceBuilder(item).build()
    typed = vf.Trace[QuestionItem, vf.State, vf.AgentConfig]
    reloaded = typed.model_validate_json(trace.to_json())
    assert reloaded.task.data == item


def test_items_are_frozen(raw_items):
    item = QuestionItem.model_validate(raw_items[0])
    with pytest.raises(ValidationError):
        item.answerable = "no"


def test_misspelled_field_rejected(raw_items):
    bad = by_qtype(raw_items, "single_hop")
    bad["gold_point"] = bad.pop("gold_points")
    with pytest.raises(ValidationError):
        QuestionItem.model_validate(bad)


def test_answerable_no_rejects_gold_points(raw_items):
    bad = by_qtype(raw_items, "unanswerable")
    bad["gold_points"] = by_qtype(raw_items, "single_hop")["gold_points"]
    with pytest.raises(ValidationError, match="no gold points"):
        QuestionItem.model_validate(bad)


def test_answerable_yes_requires_gold_points(raw_items):
    bad = by_qtype(raw_items, "single_hop")
    bad["gold_points"] = []
    with pytest.raises(ValidationError, match="at least one gold point"):
        QuestionItem.model_validate(bad)


@pytest.mark.parametrize(
    "qtype, answerable",
    [
        ("single_hop", "partial"),
        ("multi_hop", "partial"),
        ("partial", "yes"),
        ("unanswerable", "partial"),
        ("false_premise", "partial"),
    ],
)
def test_qtype_answerable_mismatch_rejected(raw_items, qtype, answerable):
    bad = by_qtype(raw_items, qtype)
    bad["answerable"] = answerable
    with pytest.raises(ValidationError, match="implies answerable"):
        QuestionItem.model_validate(bad)


@pytest.mark.parametrize("bad_id", ["E:1", "GENE_A", "P:000001", "edge-1"])
def test_malformed_gold_evidence_rejected(raw_items, bad_id):
    bad = by_qtype(raw_items, "single_hop")
    bad["gold_evidence"] = [*bad["gold_evidence"], bad_id]
    with pytest.raises(ValidationError, match="malformed gold_evidence"):
        QuestionItem.model_validate(bad)


def test_malformed_gold_point_evidence_rejected(raw_items):
    bad = by_qtype(raw_items, "single_hop")
    bad["gold_points"][0] = {**bad["gold_points"][0], "evidence": ["E:1"]}
    with pytest.raises(ValidationError, match="malformed evidence IDs"):
        QuestionItem.model_validate(bad)


@pytest.mark.parametrize(
    "field, value",
    [
        ("evidence_scope", "graph+pubmed"),  # reserved for phase 2, not valid yet
        ("split", "test"),
        ("source", "llm"),
        ("qtype", "comparison"),
    ],
)
def test_closed_vocabularies(raw_items, field, value):
    bad = by_qtype(raw_items, "single_hop")
    bad[field] = value
    with pytest.raises(ValidationError):
        QuestionItem.model_validate(bad)
