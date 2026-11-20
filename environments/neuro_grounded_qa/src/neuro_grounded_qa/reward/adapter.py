"""The thin layer between a Verifiers trace and the pure scoring code.

``evaluate(trace, item)`` reads the three scoring inputs off a trace through the
contracts and calls ``scoring.score``:

- the final answer: the content of the last model-sampled assistant message, parsed
  with ``contracts.parse_output``. ``trace.root_reply`` is ignored for scoring (see
  ``docs/reward_spec.md``, "Final-answer source");
- the retrieved evidence: ``contracts.retrieved_evidence(trace)``, the strict rule;
- the item: the task's ``QuestionItem``.

``GroundingRewardMixin`` exposes that as one ``@vf.reward`` and one ``@vf.metric``
method for a ``vf.Task`` subclass to inherit::

    class NeuroGroundedQATask(GroundingRewardMixin, vf.Task[QuestionItem, KGState]):
        ...

Verifiers finds decorated methods by scanning the task class's MRO
(``verifiers.v1.utils.decorators.discover_decorated``), so methods defined on a mixin
are found like methods on the class itself. ``tests/reward/test_adapter.py`` checks
this against ``Task.score``.

**One reward, everything else a metric.** Verifiers sums every ``@vf.reward``
(weighted) into ``trace.reward``. The aggregate here isn't a sum of components (the
format gate zeroes it, and the formula changes with ``answerable``), so the
components are recorded as metrics and only the aggregate is a reward.

``taskset.NeuroGroundedQATask`` inherits the mixin; that is the wiring.
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
    """The model's final answer text: the content of the last model-sampled assistant
    message in node order, stripped. ``""`` if there is none, or if it has only tool
    calls (the episode was cut off), which parses as ``ParseFailure("empty")``.

    This is *not* ``trace.last_reply``. In Verifiers 0.4.1 ``last_reply`` returns
    ``trace.root_reply`` whenever the harness reports one, and ``root_reply`` is set
    by the harness program rather than tied to a sampled node, so a tampering harness
    could report an answer the model never produced. Reward v0 scores only what the
    model sampled; ``root_reply_metrics`` records whether ``root_reply`` was present
    and whether it differed.

    ``assistant_messages`` keeps only ``sampled=True`` nodes, so a prompt-supplied or
    injected assistant message is never the final answer.
    """
    messages = trace.assistant_messages
    return (messages[-1].content or "").strip() if messages else ""


def root_reply_metrics(trace: vf.Trace) -> dict[str, float]:
    """Whether the harness reported a ``root_reply``, and whether it differs from the
    scored final reply (both stripped). Recorded, never scored.

    - ``root_reply_present``: 1 if ``trace.root_reply`` is set.
    - ``root_reply_differs``: 1 if it is set and its stripped text isn't the scored
      reply. A nonzero mean on a single-agent harness is a tampering signal.
    """
    root = trace.root_reply
    present = root is not None
    differs = present and root.strip() != final_reply(trace)
    return {"root_reply_present": float(present), "root_reply_differs": float(differs)}


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
    """Every recorded metric for one trace: answer metrics, trace metrics and the
    ``root_reply`` check."""
    ev = evaluate(trace, item, params)
    audit: RetrievalAudit = audit_retrieval(trace)
    return (
        answer_metrics(ev.breakdown, set(ev.retrieved_ids))
        | trace_metrics(trace, audit)
        | root_reply_metrics(trace)
    )


class GroundingRewardMixin:
    """Grounding reward and metrics for a ``vf.Task`` whose data is a ``QuestionItem``.

    Set ``reward_params`` on the subclass to change the constants (λ, the
    partial-credit values, μ, the allowance); the default is reward v1.
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
