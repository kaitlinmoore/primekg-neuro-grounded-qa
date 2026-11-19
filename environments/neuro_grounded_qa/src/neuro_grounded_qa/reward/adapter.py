"""The thin layer between a Verifiers trace and the pure scoring code.

``evaluate(trace, item)`` reads the three scoring inputs off a trace through the
contracts and calls ``scoring.score``:

- the final answer: ``trace.last_reply``, parsed with ``contracts.parse_output``
  (see ``docs/reward_spec.md``, "Final-answer source", for why this field);
- the retrieved evidence: ``contracts.retrieved_evidence(trace)``, the strict rule;
- the item: the task's ``QuestionItem``.

``GroundingRewardMixin`` exposes that as one ``@vf.reward`` and one ``@vf.metric``
method for a ``vf.Task`` subclass to inherit::

    class GroundedQATask(GroundingRewardMixin, vf.Task[QuestionItem]):
        ...

Verifiers finds decorated methods by scanning the task class's MRO
(``verifiers.v1.utils.decorators.discover_decorated``), so methods defined on a mixin
are found like methods on the class itself. ``tests/reward/test_adapter.py`` checks
this against ``Task.score``.

**One reward, everything else a metric.** Verifiers sums every ``@vf.reward``
(weighted) into ``trace.reward``. The aggregate here isn't a sum of components (the
format gate zeroes it, and the formula changes with ``answerable``), so the
components are recorded as metrics and only the aggregate is a reward.

Wiring this into ``taskset.py`` is a separate step.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

import verifiers.v1 as vf

from neuro_grounded_qa.contracts import (
    ParseResult,
    QuestionItem,
    RetrievalAudit,
    audit_retrieval,
    parse_output,
    retrieved_evidence,
)
from neuro_grounded_qa.reward.metrics import answer_metrics, trace_metrics
from neuro_grounded_qa.reward.scoring import (
    DEFAULT_PARAMS,
    RewardParams,
    ScoreBreakdown,
    score,
)


def final_reply(trace: vf.Trace) -> str:
    """The model's final answer text: ``trace.last_reply``.

    In Verifiers 0.4.1 that is ``trace.root_reply`` when the harness reports one
    (ACP harnesses do, and it's the root agent's answer when subagents ran), and
    otherwise the content of the last model-sampled assistant message in node order.
    Both are stripped of surrounding whitespace.
    """
    return trace.last_reply


def _item(task: vf.TaskData) -> QuestionItem:
    if not isinstance(task, QuestionItem):
        raise TypeError(
            f"the grounding reward needs QuestionItem task data, got {type(task).__name__}"
        )
    return task


@dataclass(frozen=True)
class Evaluation:
    """The scoring inputs read off one trace, and the resulting breakdown."""

    parsed: ParseResult
    retrieved_ids: frozenset[str]
    breakdown: ScoreBreakdown


def evaluate(
    trace: vf.Trace, item: QuestionItem, params: RewardParams = DEFAULT_PARAMS
) -> Evaluation:
    """Parse the final reply, collect strictly retrieved evidence, and score."""
    parsed = parse_output(final_reply(trace))
    retrieved = retrieved_evidence(trace)
    return Evaluation(
        parsed=parsed,
        retrieved_ids=frozenset(retrieved),
        breakdown=score(item, parsed, retrieved, params),
    )


def all_metrics(
    trace: vf.Trace, item: QuestionItem, params: RewardParams = DEFAULT_PARAMS
) -> dict[str, float]:
    """Every recorded metric for one trace: answer metrics plus trace metrics."""
    ev = evaluate(trace, item, params)
    audit: RetrievalAudit = audit_retrieval(trace)
    return answer_metrics(ev.breakdown, set(ev.retrieved_ids)) | trace_metrics(trace, audit)


class GroundingRewardMixin:
    """Grounding reward and metrics for a ``vf.Task`` whose data is a ``QuestionItem``.

    Set ``reward_params`` on the subclass to change λ or the partial-credit values.
    Verifiers passes the task's data as ``task`` and the trace as ``trace``.
    """

    reward_params: ClassVar[RewardParams] = DEFAULT_PARAMS

    @vf.reward(weight=1.0)
    async def grounding_reward(self, task: vf.TaskData, trace: vf.Trace) -> float:
        """The aggregate grounding reward, in [0, 1]."""
        return evaluate(trace, _item(task), self.reward_params).breakdown.reward

    @vf.metric
    async def grounding_metrics(self, task: vf.TaskData, trace: vf.Trace) -> dict[str, float]:
        """Components and early-warning metrics, recorded but not scored."""
        return all_metrics(trace, _item(task), self.reward_params)
