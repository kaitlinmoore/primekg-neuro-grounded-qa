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


def by_qtype(raw_items, qtype, answerable=None) -> dict:
    return next(
        dict(r)
        for r in raw_items
        if r["qtype"] == qtype and answerable in (None, r["answerable"])
    )


def test_fixture_items_cover_every_allowed_label_pair(raw_items):
    items = [QuestionItem.model_validate(r) for r in raw_items]
    pairs = {(i.qtype, i.answerable) for i in items}
    assert pairs == {(q, a) for q, allowed in QTYPE_ANSWERABLE.items() for a in allowed}


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
    bad["gold_evidence"] = by_qtype(raw_items, "single_hop")["gold_evidence"]
    with pytest.raises(ValidationError, match="no gold points"):
        QuestionItem.model_validate(bad)


def test_answerable_no_rejects_gold_evidence(raw_items):
    bad = by_qtype(raw_items, "unanswerable")
    bad["gold_evidence"] = ["E:000001"]
    with pytest.raises(ValidationError, match="no gold evidence"):
        QuestionItem.model_validate(bad)


@pytest.mark.parametrize(
    "qtype, answerable",
    [("single_hop", "yes"), ("partial", "partial"), ("false_premise", "correctable")],
)
def test_answerable_items_require_gold_points(raw_items, qtype, answerable):
    bad = by_qtype(raw_items, qtype, answerable)
    bad["gold_points"] = []
    with pytest.raises(ValidationError, match="at least one gold point"):
        QuestionItem.model_validate(bad)


@pytest.mark.parametrize(
    "qtype, answerable",
    [
        ("single_hop", "partial"),
        ("multi_hop", "correctable"),
        ("partial", "yes"),
        ("unanswerable", "correctable"),
        ("false_premise", "yes"),
        ("false_premise", "partial"),
    ],
)
def test_qtype_answerable_mismatch_rejected(raw_items, qtype, answerable):
    bad = by_qtype(raw_items, qtype)
    bad["answerable"] = answerable
    with pytest.raises(ValidationError, match="allows answerable"):
        QuestionItem.model_validate(bad)


def test_gold_point_evidence_must_be_in_gold_evidence(raw_items):
    bad = by_qtype(raw_items, "single_hop")
    bad["gold_evidence"] = ["E:000001"]  # drops E:000002, which gp2 cites
    with pytest.raises(ValidationError, match="not in gold_evidence"):
        QuestionItem.model_validate(bad)


def test_gold_point_needs_evidence(raw_items):
    bad = by_qtype(raw_items, "single_hop")
    bad["gold_points"][0] = {**bad["gold_points"][0], "evidence": []}
    with pytest.raises(ValidationError):
        QuestionItem.model_validate(bad)


@pytest.mark.parametrize("field", ["id", "prompt"])
def test_id_and_prompt_required(raw_items, field):
    bad = by_qtype(raw_items, "single_hop")
    del bad[field]
    with pytest.raises(ValidationError):
        QuestionItem.model_validate(bad)


@pytest.mark.parametrize("field, value", [("id", ""), ("prompt", "   "), ("prompt", None)])
def test_id_and_prompt_not_empty(raw_items, field, value):
    bad = by_qtype(raw_items, "single_hop")
    bad[field] = value
    with pytest.raises(ValidationError):
        QuestionItem.model_validate(bad)


def test_template_id_optional(raw_items):
    raw = by_qtype(raw_items, "single_hop")
    assert QuestionItem.model_validate(raw).template_id == "fx_genes_assoc_disease"
    del raw["template_id"]
    assert QuestionItem.model_validate(raw).template_id is None


# --- false premises ----------------------------------------------------------


def test_false_premise_requires_premise(raw_items):
    for answerable in ("no", "correctable"):
        bad = by_qtype(raw_items, "false_premise", answerable)
        del bad["premise"]
        with pytest.raises(ValidationError, match="premise is required"):
            QuestionItem.model_validate(bad)


def test_premise_forbidden_on_other_qtypes(raw_items):
    bad = by_qtype(raw_items, "single_hop")
    bad["premise"] = {"subject_id": "DRUG_Y", "relation": "indicated_for", "object_id": "DIS_1"}
    with pytest.raises(ValidationError, match="premise is required"):
        QuestionItem.model_validate(bad)


@pytest.mark.parametrize("reverse", [False, True])
def test_premise_cannot_equal_a_gold_point(raw_items, reverse):
    bad = by_qtype(raw_items, "false_premise", "correctable")
    gp = bad["gold_points"][0]
    s, o = (gp["object_id"], gp["subject_id"]) if reverse else (gp["subject_id"], gp["object_id"])
    bad["premise"] = {"subject_id": s, "relation": gp["relation"], "object_id": o}
    with pytest.raises(ValidationError, match="same fact as gold point"):
        QuestionItem.model_validate(bad)


def test_correctable_item_holds_the_correction(raw_items):
    item = QuestionItem.model_validate(by_qtype(raw_items, "false_premise", "correctable"))
    assert item.premise.relation == "indicated_for"
    assert [gp.relation for gp in item.gold_points] == ["contraindicated_for"]


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


# --- directed gold points and premises -------------------------------------------
# Built from the fixture store's directed edge E:000013: DIS_3 subtype_of DIS_G
# (child, parent). Kept out of items.jsonl so the fixture item set stays as it is.


def hierarchy_item(premise_reversed: bool = True, directed: bool = True) -> dict:
    """A correctable false premise: "Why is Disease Group G a subtype of Disease
    Three?" The graph says the reverse; the gold point holds the correction."""
    s, o = ("DIS_G", "DIS_3") if premise_reversed else ("DIS_3", "DIS_G")
    return {
        "id": "fx-falsep-dir-001",
        "prompt": "Why is Disease Group G a subtype of Disease Three?",
        "qtype": "false_premise",
        "answerable": "correctable",
        "evidence_scope": "graph",
        "gold_points": [
            {"id": "gp1", "subject_id": "DIS_3", "relation": "subtype_of",
             "object_id": "DIS_G", "evidence": ["E:000013"], "directed": directed},
        ],
        "gold_evidence": ["E:000013"],
        "premise": {"subject_id": s, "relation": "subtype_of", "object_id": o,
                    "directed": directed},
        "source": "template",
        "split": "train",
        "template_id": "fx_why_subtype",
        "notes": "synthetic; the premise reverses a directed hierarchy edge",
    }


def test_directed_gold_point_item_validates():
    raw = by_qtype(load_items(), "single_hop")
    raw["gold_points"] = [
        {"id": "gp1", "subject_id": "DIS_3", "relation": "subtype_of",
         "object_id": "DIS_G", "evidence": ["E:000013"], "directed": True},
    ]
    raw["gold_evidence"] = ["E:000013"]
    item = QuestionItem.model_validate(raw)
    assert item.gold_points[0].directed is True


def test_directed_flag_defaults_to_false_on_fixture_items(raw_items):
    for item in (QuestionItem.model_validate(r) for r in raw_items):
        assert not any(gp.directed for gp in item.gold_points)
        assert item.premise is None or item.premise.directed is False


def test_premise_reversing_a_directed_gold_point_is_valid():
    item = QuestionItem.model_validate(hierarchy_item(premise_reversed=True, directed=True))
    assert (item.premise.subject_id, item.premise.object_id) == ("DIS_G", "DIS_3")
    assert item.premise.directed and item.gold_points[0].directed


def test_premise_reversing_an_undirected_gold_point_is_still_rejected():
    with pytest.raises(ValidationError, match="same fact as gold point"):
        QuestionItem.model_validate(hierarchy_item(premise_reversed=True, directed=False))


def test_premise_equal_to_a_directed_gold_point_is_rejected():
    with pytest.raises(ValidationError, match="same fact as gold point"):
        QuestionItem.model_validate(hierarchy_item(premise_reversed=False, directed=True))


def test_directed_item_survives_trace_round_trip():
    item = QuestionItem.model_validate(hierarchy_item())
    trace = TraceBuilder(item).build()
    typed = vf.Trace[QuestionItem, vf.State, vf.AgentConfig]
    assert typed.model_validate_json(trace.to_json()).task.data == item


# --- one directed value per relation within an item --------------------------------


def test_directed_gold_point_with_unflagged_premise_is_rejected():
    """The mixed-flag case this check exists for.

    Gold point ``DIS_3 subtype_of DIS_G`` is directed; the premise ``DIS_G subtype_of
    DIS_3`` isn't. ``same_fact`` compares them in order (either side directed), so the
    premise check alone would pass. But the reward compares claims with the unflagged
    premise in either order, so the correct correction claim would count as asserting
    the premise. The item is rejected instead.
    """
    raw = hierarchy_item()
    raw["premise"]["directed"] = False
    with pytest.raises(ValidationError, match="mixed directed flags on relation 'subtype_of'"):
        QuestionItem.model_validate(raw)


def test_unflagged_gold_point_with_directed_premise_is_rejected():
    raw = hierarchy_item()
    raw["gold_points"][0]["directed"] = False
    with pytest.raises(ValidationError, match="mixed directed flags"):
        QuestionItem.model_validate(raw)


def test_gold_point_and_premise_both_directed_is_valid():
    item = QuestionItem.model_validate(hierarchy_item())
    assert item.premise.directed and all(gp.directed for gp in item.gold_points)


def test_mixed_flags_between_gold_points_on_one_relation_are_rejected():
    raw = by_qtype(load_items(), "single_hop")
    raw["gold_points"][1] = {**raw["gold_points"][1], "directed": True}
    with pytest.raises(ValidationError, match="mixed directed flags on relation 'associated_with'"):
        QuestionItem.model_validate(raw)


def test_different_relations_may_have_different_flags():
    raw = by_qtype(load_items(), "single_hop")  # associated_with gold points, unflagged
    raw["gold_points"].append(
        {"id": "gp3", "subject_id": "DIS_3", "relation": "subtype_of",
         "object_id": "DIS_G", "evidence": ["E:000013"], "directed": True}
    )
    raw["gold_evidence"].append("E:000013")
    item = QuestionItem.model_validate(raw)
    assert [gp.directed for gp in item.gold_points] == [False, False, True]
