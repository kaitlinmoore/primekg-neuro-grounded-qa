"""Grounding reward: reward components, the aggregate, and recorded metrics.

Scores a trace in code. The model's final reply is parsed with
``contracts.parse_output``; each claim's cites are checked against the evidence the
model actually retrieved (``contracts.retrieved_evidence``, strict retrieval); each
claim is checked against its cited evidence with ``contracts.supports``; supported
claims are matched to the item's gold points with ``contracts.same_fact``. This is
the instrument the reward-hacking audit targets. ``docs/reward_spec.md`` defines it.

- ``scoring``: pure functions, one per component, plus ``score`` and ``aggregate``,
  and ``RewardParams`` (the constants; ``DEFAULT_PARAMS`` is reward v1,
  ``V0_PARAMS`` reward v0). No trace or runtime involved.
- ``metrics``: recorded-but-unscored metrics.
- ``adapter``: reads a ``vf.Trace`` through the contracts, and
  ``GroundingRewardMixin`` exposes ``@vf.reward``/``@vf.metric`` methods.
"""

from neuro_grounded_qa.reward.adapter import (
    Evaluation,
    GroundingRewardMixin,
    all_metrics,
    evaluate,
    final_reply,
    root_reply_metrics,
)
from neuro_grounded_qa.reward.metrics import answer_metrics, trace_metrics
from neuro_grounded_qa.reward.scoring import (
    DEFAULT_PARAMS,
    REWARD_VERSIONS,
    SUPPORT_CHECKERS,
    V0_PARAMS,
    ClaimCheck,
    FactGroup,
    Outcome,
    RewardParams,
    ScoreBreakdown,
    abstention_correct,
    aggregate,
    check_claims,
    clip,
    coverage,
    directed_relations,
    flagged,
    format_ok,
    gold_fact,
    group_facts,
    precision_penalty,
    score,
    split_citations,
    supporting_citation,
    unsupported_penalty,
)

__all__ = [
    "DEFAULT_PARAMS",
    "REWARD_VERSIONS",
    "SUPPORT_CHECKERS",
    "V0_PARAMS",
    "ClaimCheck",
    "Evaluation",
    "FactGroup",
    "GroundingRewardMixin",
    "Outcome",
    "RewardParams",
    "ScoreBreakdown",
    "abstention_correct",
    "aggregate",
    "all_metrics",
    "answer_metrics",
    "check_claims",
    "clip",
    "coverage",
    "directed_relations",
    "evaluate",
    "final_reply",
    "flagged",
    "format_ok",
    "gold_fact",
    "group_facts",
    "precision_penalty",
    "root_reply_metrics",
    "score",
    "split_citations",
    "supporting_citation",
    "trace_metrics",
    "unsupported_penalty",
]
