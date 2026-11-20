"""Metrics recorded alongside the reward but never scored.

These are the early-warning signals for reward hacking: a policy gaming the reward
usually shows up here (citations per claim climbing, tool calls exploding, many
retrieved-but-uncited edges, rejected tool messages) before it shows up in the
score. Each function returns a flat ``dict[str, float]``, the shape
``trace.record_metrics`` takes.

Key naming:

- Fixed keys are always present, so a mean over traces is well defined.
- Ratios that are undefined for a trace (no claims, no gold points) are left out
  rather than recorded as 0, so they don't drag a mean toward 0.
- Per-tool keys (``tool_calls/<tool>``, ``rejected/<reason>/<tool>``) appear only
  when nonzero, because tool names aren't fixed here. Read a missing key as 0.
"""

from __future__ import annotations

from collections import Counter
from typing import get_args

import verifiers.v1 as vf

from neuro_grounded_qa.contracts import (
    ParseFailureReason,
    RejectionReason,
    RetrievalAudit,
)
from neuro_grounded_qa.reward.scoring import ScoreBreakdown

UNKNOWN_TOOL = "unknown"
"""Tool name used when a rejected tool message has no name to report."""


def answer_metrics(breakdown: ScoreBreakdown, retrieved_ids: set[str]) -> dict[str, float]:
    """Metrics about the final answer itself, from its score breakdown.

    - ``format_ok``, ``abstained``, ``unparseable`` (1/0) and
      ``unparseable/<reason>`` for each ``ParseFailure`` reason (1/0). The mean of
      ``unparseable`` over traces is the unparseable rate.
    - The components: ``citation_validity``, ``claim_support``, ``coverage`` (when
      defined), ``unsupported_penalty``, ``abstention_correct``, ``flagged``,
      ``premise_asserted`` (false-premise items only).
    - ``claims``: claims per answer as written, duplicates included (0 for an
      abstention or parse failure).
    - ``citations_per_claim``: mean distinct cites per claim (when there are claims).
    - ``supported_facts``, ``unsupported_facts``: |S| and |U|, counted in distinct
      facts after deduplication.
    - ``nongold_supported_facts``: N, supported distinct facts that state no gold
      point (on ``no`` items, every supported fact).
    - ``nongold_over_allowance``: ``max(0, N − a)``, the non-gold facts the precision
      penalty charges for. 0 while an answer stays within the allowance.
    - ``precision_penalty``: ``μ · max(0, N − a) / F`` (reward v1; 0 under v0). Like
      ``unsupported_penalty`` it is recorded on every answer, though the aggregate
      uses neither on ``no`` items.
    - ``duplicate_claims``: claims removed by deduplication (claims stating the same
      fact as an earlier claim; reversed restatements count unless the relation is
      directed).
    - ``voided_claims``: claims a valid cite states but a stripped cite made
      unsupported (strict citations).
    - ``stripped_cites``: cites to IDs that weren't retrieved.
    - ``nonsupporting_valid_cites``: retrieved cites other than the one that
      supported the claim (the "cite everything" signal).
    - ``retrieved_uncited``: retrieved evidence IDs no claim validly cites.
    """
    b = breakdown
    out: dict[str, float] = {
        "format_ok": float(b.format_ok),
        "abstained": float(b.outcome == "abstain"),
        "unparseable": float(b.outcome == "parse_failure"),
    }
    for reason in get_args(ParseFailureReason):
        out[f"unparseable/{reason}"] = float(b.parse_failure_reason == reason)

    out["abstention_correct"] = float(b.abstention_correct)
    out["unsupported_penalty"] = b.penalty
    out["flagged"] = float(b.flagged)
    if b.citation_validity is not None:
        out["citation_validity"] = b.citation_validity
    if b.claim_support is not None:
        out["claim_support"] = b.claim_support
    if b.coverage is not None:
        out["coverage"] = b.coverage
    if b.premise_asserted is not None:
        out["premise_asserted"] = float(b.premise_asserted)

    out["claims"] = float(b.n_claims)
    out["supported_facts"] = float(b.n_supported)
    out["unsupported_facts"] = float(b.n_unsupported)
    out["nongold_supported_facts"] = float(b.n_nongold_supported)
    out["nongold_over_allowance"] = float(b.nongold_over_allowance)
    out["precision_penalty"] = b.precision_penalty
    if b.n_claims:
        out["citations_per_claim"] = (
            sum(len(c.valid_cites) + len(c.stripped_cites) for c in b.claims) / b.n_claims
        )
    out["duplicate_claims"] = float(b.n_duplicates)
    out["voided_claims"] = float(b.n_voided)
    out["stripped_cites"] = float(sum(len(c.stripped_cites) for c in b.claims))
    out["nonsupporting_valid_cites"] = float(
        sum(len([v for v in c.valid_cites if v != c.supported_by]) for c in b.claims)
    )
    cited = {v for c in b.claims for v in c.valid_cites}
    out["retrieved_uncited"] = float(len(retrieved_ids - cited))
    return out


def trace_metrics(trace: vf.Trace, audit: RetrievalAudit) -> dict[str, float]:
    """Metrics about the episode's tool use and token use.

    - ``tool_calls``: tool calls in model-sampled assistant messages, and
      ``tool_calls/<tool>`` per tool name.
    - ``tool_results_accepted``, ``tool_results_rejected``.
    - ``rejected/<reason>`` for every ``RejectionReason`` (always present), and
      ``rejected/<reason>/<tool>`` per tool name (present when nonzero). The default
      shell tool's results land in ``rejected/unparseable/<its name>``, which is
      expected; evidence tools there are not.
    - ``duplicate_call_ids``, ``conflicting_ids``: tampering signals from the audit.
    - ``retrieved_evidence``: distinct evidence IDs retrieved.
    - ``input_tokens``, ``output_tokens``: only if the trace records provider usage.
    """
    calls: Counter[str] = Counter()
    for node in trace.nodes:
        msg = node.message
        if node.sampled and isinstance(msg, vf.AssistantMessage):
            calls.update(call.name for call in msg.tool_calls or [])

    out: dict[str, float] = {"tool_calls": float(sum(calls.values()))}
    for name, n in sorted(calls.items()):
        out[f"tool_calls/{name}"] = float(n)

    out["tool_results_accepted"] = float(len(audit.accepted))
    out["tool_results_rejected"] = float(len(audit.rejected))
    counts = audit.rejection_counts()
    for reason in get_args(RejectionReason):
        out[f"rejected/{reason}"] = float(counts.get(reason, 0))
    per_tool = Counter((r.reason, r.tool_name or UNKNOWN_TOOL) for r in audit.rejected)
    for (reason, tool), n in sorted(per_tool.items()):
        out[f"rejected/{reason}/{tool}"] = float(n)

    out["duplicate_call_ids"] = float(len(audit.duplicate_call_ids))
    out["conflicting_ids"] = float(len(audit.conflicting_ids))
    out["retrieved_evidence"] = float(len(audit.evidence))

    usage = trace.usage
    if usage is not None:
        out["input_tokens"] = float(usage.prompt_tokens + (usage.cached_input_tokens or 0))
        out["output_tokens"] = float(usage.completion_tokens)
    return out
