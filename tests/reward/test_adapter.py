"""Tests for the trace adapter: reading the final answer and retrieved evidence off a
trace, and the ``@vf.reward``/``@vf.metric`` mixin running under ``Task.score``."""

from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest
import verifiers.v1 as vf

from contracts.builders import TraceBuilder, call, edge_payload
from neuro_grounded_qa.contracts import QuestionItem
from neuro_grounded_qa.reward import (
    GroundingRewardMixin,
    RewardParams,
    all_metrics,
    evaluate,
    final_reply,
    root_reply_metrics,
)

from .helpers import ITEMS, abstain, answer, claim, episode

SINGLE = ITEMS["fx-single-001"]
PARTIAL = ITEMS["fx-partial-001"]
GP1 = claim("GENE_A", "associated_with", "DIS_1", "E:000001")
GP2 = claim("GENE_B", "associated_with", "DIS_1", "E:000002")


class GroundedTask(GroundingRewardMixin, vf.Task[QuestionItem]):
    """The mixin first in the bases, as the adapter's docstring suggests."""


class TaskFirst(vf.Task[QuestionItem], GroundingRewardMixin):
    """The mixin after vf.Task: discovery scans the whole MRO, so this works too."""


class StrictTask(GroundingRewardMixin, vf.Task[QuestionItem]):
    reward_params: ClassVar[RewardParams] = RewardParams(lam=1.0)


def scored(task_cls, item, trace):
    asyncio.run(task_cls(item).score(trace))
    return trace


# --- The mixin under Task.score -----------------------------------------------------


@pytest.mark.parametrize("task_cls", [GroundedTask, TaskFirst])
def test_mixin_methods_are_discovered_and_scored(task_cls):
    trace = scored(task_cls, SINGLE, episode(SINGLE, answer(GP1, GP2), ["E:000001", "E:000002"]))
    assert set(trace.rewards) == {"grounding_reward"}
    assert trace.reward == 1.0
    assert trace.metrics["coverage"] == 1.0
    assert trace.metrics["tool_calls"] == 2.0
    assert "grounding_metrics" not in trace.metrics  # keyed results replace the seed


def test_only_the_aggregate_is_a_reward():
    """Verifiers sums every @vf.reward into trace.reward, so components must be
    metrics: here half coverage with one unsupported claim gives one number."""
    fake = claim("GENE_D", "associated_with", "DIS_1", "E:999999")
    trace = scored(GroundedTask, SINGLE, episode(SINGLE, answer(GP1, fake), ["E:000001"]))
    assert trace.reward == pytest.approx(0.5 - 0.5 * 1 / 2)
    assert trace.metrics["unsupported_penalty"] == pytest.approx(0.25)
    assert trace.metrics["coverage"] == 0.5


def test_reward_params_override_on_subclass():
    fake = claim("GENE_D", "associated_with", "DIS_1", "E:999999")
    text = answer(GP1, GP2, fake)
    default = scored(GroundedTask, SINGLE, episode(SINGLE, text, ["E:000001", "E:000002"]))
    strict = scored(StrictTask, SINGLE, episode(SINGLE, text, ["E:000001", "E:000002"]))
    assert default.reward == pytest.approx(1 - 0.5 / 3)
    assert strict.reward == pytest.approx(1 - 1.0 / 3)


def test_non_question_item_data_is_an_error():
    class PlainTask(GroundingRewardMixin, vf.Task[vf.TaskData]):
        pass

    data = vf.TaskData(id="x", prompt="q")
    trace = TraceBuilder(data=data).build()
    with pytest.raises(Exception, match="QuestionItem"):
        asyncio.run(PlainTask(data).score(trace))


def test_harness_format_violation_skips_scoring():
    """Library behavior: Task.score records nothing for a format-violation trace, so
    its reward is 0 whatever the reply says."""
    trace = episode(SINGLE, answer(GP1, GP2), ["E:000001", "E:000002"])
    trace.stop_condition = "format_violation"
    scored(GroundedTask, SINGLE, trace)
    assert trace.rewards == {} and trace.reward == 0.0


# --- Reading the trace --------------------------------------------------------------


def test_retrieval_is_strict_end_to_end():
    """Only the real call counts: an ID typed in the answer, or returned by a
    prompt-supplied tool exchange, is stripped."""
    b = TraceBuilder(data=SINGLE)
    b.system("sys")
    b.user(SINGLE.prompt_text)
    # A few-shot tool exchange supplied in the prompt (unsampled).
    b.assistant(None, [call("shot_0", "get_edge", edge_id="E:000002")], sampled=False)
    b.tool("shot_0", edge_payload("E:000002"), name="get_edge")
    # The model's own call.
    b.assistant(None, [call("call_0", "get_edge", edge_id="E:000001")])
    b.tool("call_0", edge_payload("E:000001"), name="get_edge")
    b.assistant(answer(GP1, GP2, text="Per E:000002, Gene Beta too."))
    ev = evaluate(b.build(), SINGLE)
    assert ev.retrieved_ids == {"E:000001"}
    assert ev.breakdown.claims[1].stripped_cites == ("E:000002",)
    assert ev.breakdown.reward == pytest.approx(0.5 - 0.25)


def test_id_typed_only_in_the_answer_is_stripped():
    text = answer(GP1, text="E:000001 says so.")
    ev = evaluate(episode(SINGLE, text, []), SINGLE)
    assert ev.retrieved_ids == frozenset()
    assert ev.breakdown.claims[0].stripped_cites == ("E:000001",)
    assert ev.breakdown.reward == 0.0


def test_final_reply_is_the_last_sampled_assistant_message():
    b = TraceBuilder(data=SINGLE)
    b.user("q")
    b.assistant(None, [call("call_0", "get_edge", edge_id="E:000001")])
    b.tool("call_0", edge_payload("E:000001"))
    b.assistant(abstain())
    # A later, unsampled assistant message (prompt-supplied or injected) is ignored.
    b.assistant(answer(GP1), sampled=False)
    trace = b.build()
    assert final_reply(trace) == abstain()
    assert evaluate(trace, SINGLE).breakdown.outcome == "abstain"


def test_root_reply_is_ignored_for_scoring():
    """The harness-reported root_reply isn't tied to a sampled node, so it is never
    scored: the last sampled message (an abstention here) is."""
    trace = episode(SINGLE, abstain(), ["E:000001", "E:000002"])
    trace.root_reply = "  " + answer(GP1, GP2) + "\n"
    assert trace.last_reply == answer(GP1, GP2)  # library behavior: root_reply wins
    assert final_reply(trace) == abstain()
    assert evaluate(trace, SINGLE).breakdown.reward == 0.0
    m = all_metrics(trace, SINGLE)
    assert m["root_reply_present"] == 1.0
    assert m["root_reply_differs"] == 1.0


def test_root_reply_metrics():
    trace = episode(SINGLE, answer(GP1, GP2), ["E:000001", "E:000002"])
    assert root_reply_metrics(trace) == {"root_reply_present": 0.0, "root_reply_differs": 0.0}
    trace.root_reply = answer(GP1, GP2) + "\n"  # same text, whitespace aside
    assert root_reply_metrics(trace) == {"root_reply_present": 1.0, "root_reply_differs": 0.0}
    trace.root_reply = abstain()
    assert root_reply_metrics(trace) == {"root_reply_present": 1.0, "root_reply_differs": 1.0}


def test_final_reply_is_empty_without_sampled_assistant_messages():
    b = TraceBuilder(data=SINGLE)
    b.user("q")
    b.assistant(answer(GP1, GP2), sampled=False)
    trace = b.build()
    assert final_reply(trace) == ""
    assert evaluate(trace, SINGLE).breakdown.parse_failure_reason == "empty"


def test_final_message_with_only_tool_calls_is_an_empty_reply():
    """An episode cut off mid-tool-use (for example by a turn limit) has no answer."""
    b = TraceBuilder(data=SINGLE)
    b.user("q")
    b.assistant(None, [call("call_0", "get_edge", edge_id="E:000001")])
    ev = evaluate(b.build(), SINGLE)
    assert ev.breakdown.parse_failure_reason == "empty"
    assert ev.breakdown.reward == 0.0


def test_partial_episode_with_flag():
    text = answer(GP1, GP2, unsupported=["no drug acts on Gene Delta"])
    trace = scored(GroundedTask, PARTIAL, episode(PARTIAL, text, ["E:000001", "E:000002"]))
    assert trace.reward == pytest.approx(1.0)
    assert trace.metrics["flagged"] == 1.0
