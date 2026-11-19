"""Pure scoring: one parsed final answer, one item, the retrieved evidence -> a score.

This is the grounding reward itself, with no Verifiers runtime in it. ``score`` takes
three inputs and nothing else:

- ``item``: the ``QuestionItem`` (gold points, answerability, false premise).
- ``parsed``: what ``contracts.parse_output`` made of the model's final reply.
- ``retrieved``: evidence the model actually retrieved, by ID, exactly as
  ``contracts.retrieved_evidence(trace)`` returns it (strict retrieval).

It returns a ``ScoreBreakdown`` holding every component and the clipped aggregate.
``docs/reward_spec.md`` is the prose definition; this module is the executable one,
and the two must agree.

Components, each its own small function below:

1. **Format gate** (``format_ok``): a ``ParseFailure`` scores 0 overall.
2. **Citation validity** (``split_citations``): a cite counts only if the ID is in
   ``retrieved``. Other cites are stripped, whatever they look like.
3. **Claim support** (``supporting_citation``): a claim is supported if one of its
   valid cites is evidence that states it, by ``contracts.supports``.
4. **Coverage** (``coverage``): the share of the item's gold points stated by a
   supported claim, by ``contracts.same_fact``. Only gold points count, so true but
   irrelevant claims earn nothing.
5. **Unsupported-claim penalty** (``unsupported_penalty``):
   ``lam * |U| / (|S| + |U|)``.
6. **Abstention correctness** (``abstention_correct``): whether answering vs
   abstaining was the right choice for the item's ``answerable`` label.

``aggregate`` combines them per ``answerable`` value and clips to [0, 1].

Two rules keep the audit honest. Triples are never compared directly: every
comparison goes through ``supports``/``same_fact`` from ``contracts.facts``, so a
change there (for example a per-relation directedness flag) flows through here
unchanged. And the retrieved set is only ever the ``retrieved`` argument: nothing
here reads IDs from the model's text.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Callable, Literal

from neuro_grounded_qa.contracts import (
    Abstain,
    Answer,
    Claim,
    EdgeEvidence,
    Evidence,
    ParseFailure,
    ParseResult,
    QuestionItem,
    same_fact,
    supports,
)

Outcome = Literal["answer", "abstain", "parse_failure"]
"""What the model's final reply was, after parsing."""


@dataclass(frozen=True)
class RewardParams:
    """The reward's tunable constants. All values here are **proposals** pending
    Kaitlin's decision; ``docs/reward_spec.md`` gives the reasoning for each.

    - ``lam`` (λ): weight of the unsupported-claim penalty. Proposed 0.5.
    - ``partial_flag_credit`` (κ): on ``partial`` items, the share of the score
      given for naming the unsupported part (a non-blank ``unsupported_parts``).
      Coverage gets the other ``1 - κ``. Proposed 0.2.
    - ``correctable_abstain_credit``: the score for abstaining on a
      ``correctable`` false-premise item. A cited correction can score up to 1.
      Proposed 1.0 (equal credit for both paths).
    - ``flag_only_counts``: whether an answer with no claims and only
      ``unsupported_parts`` earns the flag credit on a ``partial`` item. Proposed
      True.
    """

    lam: float = 0.5
    partial_flag_credit: float = 0.2
    correctable_abstain_credit: float = 1.0
    flag_only_counts: bool = True

    def __post_init__(self) -> None:
        for name in ("lam", "partial_flag_credit", "correctable_abstain_credit"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and >= 0, got {value}")
        for name in ("partial_flag_credit", "correctable_abstain_credit"):
            if getattr(self, name) > 1:
                raise ValueError(f"{name} must be <= 1, got {getattr(self, name)}")


DEFAULT_PARAMS = RewardParams()
"""The proposed starting values. Not frozen: see ``docs/reward_spec.md``."""


# --- Support check, dispatched by evidence kind -------------------------------------

SupportChecker = Callable[[Claim, Evidence], bool]


def _edge_supports(claim: Claim, evidence: Evidence) -> bool:
    return isinstance(evidence, EdgeEvidence) and supports(claim, evidence)


SUPPORT_CHECKERS: dict[str, SupportChecker] = {"edge": _edge_supports}
"""Support check per evidence kind. Phase 1 has edges only, checked in code with
``contracts.supports``. Phase 2 adds a ``"passage"`` entry (a judge-backed check)
without changing anything else here. Evidence of a kind with no checker supports
nothing."""


# --- Per-claim result ---------------------------------------------------------------


@dataclass(frozen=True)
class ClaimCheck:
    """What the reward decided about one claim.

    - ``valid_cites``: the claim's cites that are in the retrieved set, deduplicated,
      in the order the claim gave them.
    - ``stripped_cites``: the cites that weren't retrieved (fabricated, typed only in
      the answer, from a prompt-supplied tool result, malformed), deduplicated.
    - ``supported_by``: the first valid cite whose evidence states the claim, or
      None if no valid cite does.
    - ``asserts_premise``: the claim states the item's false premise. Recorded for
      the failure taxonomy ("premise accepted"); not scored separately, since a
      premise is absent from the graph and so can't be supported.
    """

    claim: Claim
    valid_cites: tuple[str, ...]
    stripped_cites: tuple[str, ...]
    supported_by: str | None
    asserts_premise: bool

    @property
    def supported(self) -> bool:
        return self.supported_by is not None


# --- Components -------------------------------------------------------------------


def format_ok(parsed: ParseResult) -> bool:
    """Format gate: the reply parsed as an ``Answer`` or an ``Abstain``.

    A ``ParseFailure`` (empty, not JSON, wrong schema, answer mixed with abstain)
    scores 0 overall, whatever else it contains.
    """
    return not isinstance(parsed, ParseFailure)


def _dedupe(ids: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(ids))


def split_citations(
    claim: Claim, retrieved: Mapping[str, Evidence]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Citation validity: ``(valid, stripped)`` cites for one claim.

    A cite is valid only if its ID is a key of ``retrieved``. Everything else is
    stripped: an ID the model never fetched counts for nothing, however well-formed.
    Stripping is per cite, not per claim: a claim citing one retrieved and one
    fabricated ID keeps the retrieved one.
    """
    cites = _dedupe(claim.cites)
    valid = tuple(c for c in cites if c in retrieved)
    stripped = tuple(c for c in cites if c not in retrieved)
    return valid, stripped


def supporting_citation(
    claim: Claim, valid_cites: tuple[str, ...], retrieved: Mapping[str, Evidence]
) -> str | None:
    """Claim support: the first valid cite whose evidence states ``claim``, or None.

    A valid citation that doesn't state the claim doesn't support it. The check is
    dispatched on the evidence's ``kind`` (``SUPPORT_CHECKERS``); for edges it is
    ``contracts.supports``, which matches the triple in either direction with an
    identical relation label.
    """
    for cite in valid_cites:
        evidence = retrieved[cite]
        checker = SUPPORT_CHECKERS.get(evidence.kind)
        if checker is not None and checker(claim, evidence):
            return cite
    return None


def check_claims(
    answer: Answer, retrieved: Mapping[str, Evidence], item: QuestionItem
) -> tuple[ClaimCheck, ...]:
    """Run citation validity and claim support on every claim, in order."""
    checks = []
    for claim in answer.claims:
        valid, stripped = split_citations(claim, retrieved)
        checks.append(
            ClaimCheck(
                claim=claim,
                valid_cites=valid,
                stripped_cites=stripped,
                supported_by=supporting_citation(claim, valid, retrieved),
                asserts_premise=item.premise is not None and same_fact(claim, item.premise),
            )
        )
    return tuple(checks)


def coverage(
    item: QuestionItem, checks: tuple[ClaimCheck, ...]
) -> tuple[float | None, tuple[str, ...]]:
    """Coverage: ``(share of gold points covered, IDs of the covered gold points)``.

    A gold point is covered when a *supported* claim states the same fact
    (``contracts.same_fact``). Each gold point counts once, however many claims
    state it, and claims that match no gold point add nothing. The share is None
    for items with no gold points (``answerable="no"``), where coverage isn't
    defined.
    """
    if not item.gold_points:
        return None, ()
    supported = [c.claim for c in checks if c.supported]
    covered = tuple(
        gp.id for gp in item.gold_points if any(same_fact(c, gp) for c in supported)
    )
    return len(covered) / len(item.gold_points), covered


def unsupported_penalty(n_supported: int, n_unsupported: int, lam: float) -> float:
    """Unsupported-claim penalty: ``lam * |U| / (|S| + |U|)``, or 0 with no claims.

    A claim is unsupported if no valid cite states it (including claims whose cites
    were all stripped). The penalty is a share of the answer's claims, so it scales
    with the number of unsupported claims for a fixed number of supported ones.
    """
    total = n_supported + n_unsupported
    return 0.0 if total == 0 else lam * n_unsupported / total


def flagged(answer: Answer) -> bool:
    """The answer names at least one unsupported part (a non-blank string in
    ``unsupported_parts``). Its content isn't checked: free text isn't scored."""
    return any(part.strip() for part in answer.unsupported_parts)


def abstention_correct(item: QuestionItem, parsed: ParseResult) -> bool:
    """Whether answering vs abstaining was the right call for the item.

    - ``yes``, ``partial``: answering is right.
    - ``no``: abstaining is right.
    - ``correctable``: both are right (abstain, or give a cited correction).

    A parse failure is never right. This is the choice only; whether an answer was
    any good is coverage's job.
    """
    if isinstance(parsed, ParseFailure):
        return False
    abstained = isinstance(parsed, Abstain)
    match item.answerable:
        case "yes" | "partial":
            return not abstained
        case "no":
            return abstained
        case "correctable":
            return True
    raise ValueError(f"unknown answerable value {item.answerable!r}")


def clip(x: float) -> float:
    """Clip to [0, 1]."""
    return min(1.0, max(0.0, x))


def aggregate(
    answerable: str,
    outcome: Outcome,
    cov: float | None,
    penalty: float,
    is_flagged: bool,
    n_claims: int,
    params: RewardParams,
) -> float:
    """Combine the components into the unclipped reward, per ``answerable`` value.

    - Parse failure: 0.
    - ``yes``: answer -> ``coverage - penalty``; abstain -> 0.
    - ``partial``: answer -> ``(1 - κ)·coverage + κ·flagged - penalty``; an answer
      with no claims gets the flag credit only if ``params.flag_only_counts``;
      abstain -> 0.
    - ``no``: abstain -> 1; any answer -> 0.
    - ``correctable``: abstain -> ``params.correctable_abstain_credit``; answer ->
      ``coverage - penalty`` (the correction is the gold points).

    The caller clips the result to [0, 1].
    """
    if outcome == "parse_failure":
        return 0.0
    abstained = outcome == "abstain"
    match answerable:
        case "yes":
            return 0.0 if abstained else (cov or 0.0) - penalty
        case "partial":
            if abstained:
                return 0.0
            kappa = params.partial_flag_credit
            flag = 1.0 if is_flagged and (n_claims > 0 or params.flag_only_counts) else 0.0
            return (1 - kappa) * (cov or 0.0) + kappa * flag - penalty
        case "no":
            return 1.0 if abstained else 0.0
        case "correctable":
            if abstained:
                return params.correctable_abstain_credit
            return (cov or 0.0) - penalty
    raise ValueError(f"unknown answerable value {answerable!r}")


# --- The whole score ----------------------------------------------------------------


@dataclass(frozen=True)
class ScoreBreakdown:
    """Every component of one score, plus the aggregate.

    Ratios are None where they're undefined (no claims, no cites, no gold points),
    so a metric mean over traces isn't dragged toward 0 by traces where the ratio
    has no meaning.
    """

    answerable: str
    outcome: Outcome
    parse_failure_reason: str | None
    format_ok: bool
    claims: tuple[ClaimCheck, ...]
    citation_validity: float | None
    """Valid cites / all cites (each deduplicated per claim). None with no cites."""
    claim_support: float | None
    """Supported claims / all claims. None with no claims."""
    coverage: float | None
    covered_gold: tuple[str, ...]
    penalty: float
    flagged: bool
    abstention_correct: bool
    premise_asserted: bool | None
    """Any claim states the false premise. None for items without a premise."""
    raw: float
    """The aggregate before clipping."""
    reward: float
    """The aggregate clipped to [0, 1]: the scored value."""

    @property
    def n_claims(self) -> int:
        return len(self.claims)

    @property
    def n_supported(self) -> int:
        return sum(c.supported for c in self.claims)

    @property
    def n_unsupported(self) -> int:
        return self.n_claims - self.n_supported


def score(
    item: QuestionItem,
    parsed: ParseResult,
    retrieved: Mapping[str, Evidence],
    params: RewardParams = DEFAULT_PARAMS,
) -> ScoreBreakdown:
    """Score one parsed final answer against one item and the retrieved evidence.

    Pure and deterministic: the same inputs always give the same breakdown, and
    nothing outside the arguments is read.

    Args:
        item: the question item, with gold and answerability.
        parsed: ``contracts.parse_output(final_reply)``.
        retrieved: ``contracts.retrieved_evidence(trace)``. Only these IDs can be
            cited validly.
        params: the reward's constants.
    """
    if isinstance(parsed, ParseFailure):
        outcome: Outcome = "parse_failure"
    elif isinstance(parsed, Abstain):
        outcome = "abstain"
    else:
        outcome = "answer"

    checks = check_claims(parsed, retrieved, item) if isinstance(parsed, Answer) else ()
    n_claims = len(checks)
    n_supported = sum(c.supported for c in checks)
    n_cites = sum(len(c.valid_cites) + len(c.stripped_cites) for c in checks)
    n_valid = sum(len(c.valid_cites) for c in checks)

    if isinstance(parsed, Answer):
        cov, covered = coverage(item, checks)
        is_flagged = flagged(parsed)
    else:
        # Abstaining or failing to parse states nothing, so covers nothing.
        cov = 0.0 if item.gold_points else None
        covered = ()
        is_flagged = False
    penalty = unsupported_penalty(n_supported, n_claims - n_supported, params.lam)

    raw = aggregate(item.answerable, outcome, cov, penalty, is_flagged, n_claims, params)
    return ScoreBreakdown(
        answerable=item.answerable,
        outcome=outcome,
        parse_failure_reason=parsed.reason if isinstance(parsed, ParseFailure) else None,
        format_ok=format_ok(parsed),
        claims=checks,
        citation_validity=n_valid / n_cites if n_cites else None,
        claim_support=n_supported / n_claims if n_claims else None,
        coverage=cov,
        covered_gold=covered,
        penalty=penalty,
        flagged=is_flagged,
        abstention_correct=abstention_correct(item, parsed),
        premise_asserted=(
            None if item.premise is None else any(c.asserts_premise for c in checks)
        ),
        raw=raw,
        reward=clip(raw),
    )
