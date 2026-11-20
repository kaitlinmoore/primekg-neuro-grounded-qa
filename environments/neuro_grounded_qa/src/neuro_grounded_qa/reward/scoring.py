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
   valid cites is evidence that states it, by ``contracts.supports``, **and none of
   its cites was stripped** (strict citations: one bad cite voids the claim).
4. **Coverage** (``coverage``): the share of the item's gold points stated by a
   supported claim, by ``contracts.same_fact``. Only gold points count, so true but
   irrelevant claims earn nothing.
5. **Unsupported-claim penalty** (``unsupported_penalty``):
   ``lam * |U| / (|S| + |U|)``, where S and U count distinct *facts*: claims that
   state the same fact (``contracts.same_fact``) are collapsed first
   (``group_facts``), and a fact is supported if any of its copies is.
6. **Precision penalty** (``precision_penalty``, reward v1.1):
   ``mu * max(0, N - a) / S``, where N counts supported facts that state no gold
   point (``gold_fact``), S the supported distinct facts and ``a`` is a small
   allowance of free non-gold facts. It is what makes restating every retrieved edge
   (the evidence dump) cost something: those claims are all supported, so (5) never
   sees them. Reward v1 divided by F, all distinct facts, instead of S; fabricated
   (unsupported) claims then raised F and diluted the term (audit candidate 12).
7. **Abstention correctness** (``abstention_correct``): whether answering vs
   abstaining was the right choice for the item's ``answerable`` label.

``aggregate`` combines them per ``answerable`` value and clips to [0, 1]. The two
penalties are added before it subtracts them.

**Versions.** Reward v0 (2026-11-19) is the same formula with ``mu = 0``
(``V0_PARAMS``). Reward v1 (2026-11-20, ``V1_PARAMS``) adds the precision penalty at
``mu = 1, a = 3``, over all distinct facts F. Reward v1.1 (2026-11-20,
``DEFAULT_PARAMS``) is v1 with the precision term over supported facts S; nothing else
changed. The version string selects the denominator (``RewardParams.precision_over``),
so a record's constants plus its version reproduce its score. Every trace records the
constants and the version it was scored with; :meth:`RewardParams.from_record` reads
them back, and treats a record with no version as v0, since v0 traces predate the
marker. With no unsupported facts S = F, so v1 and v1.1 agree on every such answer.

Two rules keep the audit honest. Triples are never compared directly: every
comparison goes through ``supports``/``same_fact`` from ``contracts.facts``, so the
direction rule lives in one place. Facts match in either order, except facts flagged
``directed`` (hierarchy relations), which match in order only. And the retrieved set
is only ever the ``retrieved`` argument: nothing here reads IDs from the model's text.

**Direction when comparing two claims.** Claims carry no ``directed`` flag, so
``same_fact(claim, claim)`` would match a directed fact and its reversal. Deduplication
(``group_facts``) therefore takes direction from the relation label: a relation is
directed if any retrieved edge, gold point, premise or graph gap triple with that
label is flagged (``directed_relations``), and claims with that label are compared in
order. Claim vs edge, gold point or premise needs nothing extra: those carry their own
flag.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
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


REWARD_VERSIONS = ("v0", "v1", "v1.1")
"""Reward versions this code can score. v0 is v1 with the precision weight at 0; v1.1 is
v1 with the precision term divided by supported facts instead of all facts."""


@dataclass(frozen=True)
class RewardParams:
    """The reward's tunable constants. The defaults are **reward v1.1**, set by Kaitlin
    on 2026-11-20 (v0's four constants, set 2026-11-19, plus the precision penalty's
    two, unchanged from v1); ``docs/reward_spec.md`` gives the reasoning for each.
    Exploratory, not frozen: a change goes in ``docs/changelog.md``.

    - ``lam`` (λ): weight of the unsupported-claim penalty. v0, v1: 0.5.
    - ``partial_flag_credit`` (κ): on ``partial`` items, the share of the score
      given for naming the unsupported part (a non-blank ``unsupported_parts``).
      Coverage gets the other ``1 - κ``. v0, v1: 0.2.
    - ``correctable_abstain_credit``: the score for abstaining on a
      ``correctable`` false-premise item. A cited correction can score up to 1, so
      below 1 correcting beats abstaining. v0, v1: 0.7.
    - ``flag_only_counts``: whether an answer with no claims and only
      ``unsupported_parts`` earns the flag credit on a ``partial`` item. v0, v1: True.
    - ``mu`` (μ): weight of the precision penalty, ``μ · max(0, N − a) / S`` (v1.1;
      ``/ F`` under v1). At 1, an answer made almost entirely of supported non-gold
      facts loses nearly all its coverage. v1, v1.1: 1.0. v0: 0 (no precision penalty).
    - ``nongold_allowance`` (a): supported non-gold facts an answer may state before
      the precision penalty starts, so an honest answer can add a little context for
      free. v1, v1.1: 3. Ignored when ``mu`` is 0.
    - ``version``: which reward these constants define, recorded with every trace so
      each trace says which reward scored it. ``"v0"`` requires ``mu == 0``. It also
      picks the precision term's denominator (:attr:`precision_over`): ``"v1"`` divides
      by all facts, ``"v1.1"`` by supported facts.
    """

    lam: float = 0.5
    partial_flag_credit: float = 0.2
    correctable_abstain_credit: float = 0.7
    flag_only_counts: bool = True
    mu: float = 1.0
    nongold_allowance: int = 3
    version: str = "v1.1"

    def __post_init__(self) -> None:
        for name in ("lam", "partial_flag_credit", "correctable_abstain_credit", "mu"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and >= 0, got {value}")
        for name in ("partial_flag_credit", "correctable_abstain_credit"):
            if getattr(self, name) > 1:
                raise ValueError(f"{name} must be <= 1, got {getattr(self, name)}")
        a = self.nongold_allowance
        if isinstance(a, bool) or not isinstance(a, int) or a < 0:
            raise ValueError(f"nongold_allowance must be an int >= 0, got {a!r}")
        if self.version not in REWARD_VERSIONS:
            raise ValueError(f"version must be one of {REWARD_VERSIONS}, got {self.version!r}")
        if self.version == "v0" and self.mu != 0:
            raise ValueError("reward v0 has no precision penalty: mu must be 0")

    @property
    def precision_over(self) -> Literal["facts", "supported"]:
        """The precision term's denominator: ``"facts"`` (F, every distinct fact) under
        v0 and v1, ``"supported"`` (S, supported distinct facts) under v1.1. Under v0 the
        term is 0 either way."""
        return "supported" if self.version == "v1.1" else "facts"

    @classmethod
    def from_record(cls, record: Mapping[str, object] | None) -> RewardParams:
        """The constants a trace was scored with, from ``trace.info["reward_params"]``.

        A record without ``version`` was written by reward v0, before the marker and
        the precision constants existed, so it is read as v0 (``mu = 0``) with its
        recorded λ, κ, A and ``flag_only_counts``. Building ``RewardParams(**record)``
        directly would silently fill in the current precision constants and version
        instead. A ``"v1"`` record stays v1 (precision over all facts) and a ``"v1.1"``
        record v1.1, since the version picks the denominator. No record at all gives
        ``DEFAULT_PARAMS``.
        """
        if record is None:
            return DEFAULT_PARAMS
        data = dict(record)
        if "version" not in data:
            data = {**data, "mu": 0.0, "nongold_allowance": 0, "version": "v0"}
        return cls(**data)  # type: ignore[arg-type]


DEFAULT_PARAMS = RewardParams()
"""Reward v1.1 (Kaitlin, 2026-11-20). Not frozen: see ``docs/reward_spec.md``."""

V1_PARAMS = RewardParams(version="v1")
"""Reward v1 (Kaitlin, 2026-11-20): the precision term over all distinct facts. Kept so
results recorded under v1 can be recomputed and compared."""

V0_PARAMS = RewardParams(mu=0.0, nongold_allowance=0, version="v0")
"""Reward v0 (Kaitlin, 2026-11-19): v1 without the precision penalty. Kept so earlier
results can be recomputed and compared."""


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
      None if no valid cite does **or any cite was stripped** (strict citations).
    - ``voided``: a valid cite states the claim, but another cite was stripped, so
      the strict-citation rule made the claim unsupported. Recorded as a metric so
      the audit can see how often the rule bites.
    - ``asserts_premise``: the claim states the item's false premise. Recorded for
      the failure taxonomy ("premise accepted"); not scored separately, since a
      premise is absent from the graph and so can't be supported.
    """

    claim: Claim
    valid_cites: tuple[str, ...]
    stripped_cites: tuple[str, ...]
    supported_by: str | None
    voided: bool
    asserts_premise: bool

    @property
    def supported(self) -> bool:
        return self.supported_by is not None


@dataclass(frozen=True)
class FactGroup:
    """Claims in one answer that state the same fact (``contracts.same_fact``).

    The reward counts facts, not claims: repeating a claim, or restating it with
    subject and object swapped, adds nothing to |S| or |U|. The fact is supported if
    any copy is (each copy judged under the strict-citation rule on its own cites).
    """

    claims: tuple[ClaimCheck, ...]

    @property
    def supported(self) -> bool:
        return any(c.supported for c in self.claims)

    @property
    def duplicates(self) -> int:
        """Copies beyond the first: what deduplication removed."""
        return len(self.claims) - 1


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
    That covers fabricated IDs, IDs never retrieved, malformed IDs and empty strings.
    A claim with *any* stripped cite is unsupported (strict citations, applied in
    ``check_claims``), even if a valid cite states it.
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
    ``contracts.supports``: an identical relation label, and the entity IDs in either
    order, or in stored order only if the edge is flagged ``directed``.
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
    """Run citation validity and claim support on every claim, in order.

    Strict citations: a claim with any stripped cite is unsupported, even when one of
    its valid cites states it (that case is recorded as ``voided``).
    """
    checks = []
    for claim in answer.claims:
        valid, stripped = split_citations(claim, retrieved)
        stating = supporting_citation(claim, valid, retrieved)
        checks.append(
            ClaimCheck(
                claim=claim,
                valid_cites=valid,
                stripped_cites=stripped,
                supported_by=None if stripped else stating,
                voided=bool(stripped) and stating is not None,
                asserts_premise=item.premise is not None and same_fact(claim, item.premise),
            )
        )
    return tuple(checks)


@dataclass(frozen=True)
class _OrientedClaim:
    """A claim's triple plus a ``directed`` flag, so ``same_fact`` can compare two
    claims in order. ``contracts.is_directed`` reads the flag."""

    subject_id: str
    relation: str
    object_id: str
    directed: bool


def directed_relations(
    item: QuestionItem, retrieved: Mapping[str, Evidence]
) -> frozenset[str]:
    """Relation labels known to be directed for this score: any retrieved edge, gold
    point, premise or graph gap triple with that label is flagged ``directed``.

    Gap triples count too: a relation can appear in an item only as a gap (the absent
    fact) and in no retrieved edge, and two reversed claims on it are still two facts.

    Used only to compare claims with each other, since claims carry no flag. Item
    validation already rejects mixed flags on one relation within an item, and the
    data builder sets the flag per relation.
    """
    flagged: Iterable[tuple[str, bool]] = (
        *((e.relation, e.directed) for e in retrieved.values() if isinstance(e, EdgeEvidence)),
        *((gp.relation, gp.directed) for gp in item.gold_points),
        *(((item.premise.relation, item.premise.directed),) if item.premise is not None else ()),
        *((g.triple.relation, g.triple.directed) for g in item.graph_gaps if g.triple is not None),
    )
    return frozenset(relation for relation, directed in flagged if directed)


def group_facts(
    checks: tuple[ClaimCheck, ...], directed: frozenset[str] = frozenset()
) -> tuple[FactGroup, ...]:
    """Collapse claims that state the same fact into one ``FactGroup`` each.

    Groups are in order of each fact's first claim. Every comparison goes through
    ``contracts.same_fact``. Reversed restatements of an undirected fact are
    duplicates. Claims whose relation is in ``directed`` (see
    :func:`directed_relations`) are compared in order, so a directed fact and its
    reversal stay two facts: the reversal can't hide inside the correct claim's group.
    """

    def oriented(check: ClaimCheck) -> _OrientedClaim:
        c = check.claim
        return _OrientedClaim(c.subject_id, c.relation, c.object_id, c.relation in directed)

    groups: list[list[ClaimCheck]] = []
    for check in checks:
        for group in groups:
            if same_fact(oriented(check), oriented(group[0])):
                group.append(check)
                break
        else:
            groups.append([check])
    return tuple(FactGroup(tuple(g)) for g in groups)


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

    ``score`` passes counts of distinct facts (``group_facts``): a fact is unsupported
    if no copy of it is supported, that is, no copy has a valid cite stating it and no
    stripped cite. The penalty is a share, so it scales with the number of
    unsupported facts for a fixed number of supported ones.
    """
    total = n_supported + n_unsupported
    return 0.0 if total == 0 else lam * n_unsupported / total


def gold_fact(fact: FactGroup, item: QuestionItem) -> bool:
    """The fact is supported and states one of the item's gold points
    (``contracts.same_fact``, on any of its copies).

    A supported fact that is not a gold fact is a *non-gold* fact: true and retrieved,
    but not what the question asked. An unsupported fact is neither: the unsupported
    penalty already counts it. On items with no gold points (``no``), every supported
    fact is non-gold.
    """
    return fact.supported and any(
        same_fact(c.claim, gp) for c in fact.claims for gp in item.gold_points
    )


def precision_penalty(n_nongold: int, n_denominator: int, mu: float, allowance: int) -> float:
    """Precision penalty: ``mu * max(0, N - a) / D``, or 0 when D is 0.

    N is the answer's supported non-gold facts and ``a`` the allowance: the first ``a``
    non-gold facts are free, and each one beyond costs ``mu / D``. ``score`` passes D by
    version (:attr:`RewardParams.precision_over`):

    - **v1.1**: D = S, the supported distinct facts. N ≤ S, so the term is a share of
      what the answer supports, at most ``mu``, and 0 when nothing is supported.
      Fabricated claims don't change S, so they can't shrink the term; they only add
      to the unsupported penalty.
    - **v1**: D = F, every distinct fact, supported or not. Each unsupported fact grows
      F and so shrinks the term (candidate 12 in ``docs/reward_spec.md``).

    With no unsupported facts S = F and the two agree. Duplicates count once
    (``group_facts``), so restating a fact can't raise N or D.
    """
    if n_denominator == 0:
        return 0.0
    return mu * max(0, n_nongold - allowance) / n_denominator


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

    ``penalty`` is the total penalty: ``score`` passes the unsupported penalty plus
    the precision penalty. It is subtracted on answers to ``yes``, ``partial`` and
    ``correctable`` items and nowhere else.

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
    """Every claim as written, in order, before deduplication."""
    facts: tuple[FactGroup, ...]
    """The claims collapsed by fact: what S and U count."""
    citation_validity: float | None
    """Valid cites / all cites (each deduplicated per claim). None with no cites."""
    claim_support: float | None
    """Supported facts / all facts. None with no claims."""
    coverage: float | None
    covered_gold: tuple[str, ...]
    penalty: float
    """The unsupported-claim penalty (v0's only penalty)."""
    n_nongold_supported: int
    """N: supported distinct facts that state no gold point (``gold_fact``)."""
    nongold_over_allowance: int
    """``max(0, N − a)``: the non-gold facts the precision penalty charges for."""
    precision_penalty: float
    """``μ · max(0, N − a) / S`` under v1.1, ``/ F`` under v1, 0 under v0. The aggregate subtracts
    ``penalty + precision_penalty``; like ``penalty`` it is recorded on every answer
    but used only where the aggregate uses a penalty (not on ``no`` items)."""
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
        """Claims as written, duplicates included."""
        return len(self.claims)

    @property
    def n_facts(self) -> int:
        """Distinct facts: |S| + |U|."""
        return len(self.facts)

    @property
    def n_supported(self) -> int:
        """|S|: supported distinct facts."""
        return sum(f.supported for f in self.facts)

    @property
    def n_unsupported(self) -> int:
        """|U|: unsupported distinct facts."""
        return self.n_facts - self.n_supported

    @property
    def n_duplicates(self) -> int:
        """Claims removed by deduplication."""
        return self.n_claims - self.n_facts

    @property
    def n_voided(self) -> int:
        """Claims a stripped cite made unsupported despite a valid stating cite."""
        return sum(c.voided for c in self.claims)


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
    facts = group_facts(checks, directed_relations(item, retrieved))
    n_claims = len(checks)
    n_supported = sum(f.supported for f in facts)
    n_unsupported = len(facts) - n_supported
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
    penalty = unsupported_penalty(n_supported, n_unsupported, params.lam)
    n_nongold = sum(f.supported and not gold_fact(f, item) for f in facts)
    over = max(0, n_nongold - params.nongold_allowance)
    denominator = n_supported if params.precision_over == "supported" else len(facts)
    prec_penalty = precision_penalty(n_nongold, denominator, params.mu, params.nongold_allowance)

    raw = aggregate(
        item.answerable, outcome, cov, penalty + prec_penalty, is_flagged, n_claims, params
    )
    return ScoreBreakdown(
        answerable=item.answerable,
        outcome=outcome,
        parse_failure_reason=parsed.reason if isinstance(parsed, ParseFailure) else None,
        format_ok=format_ok(parsed),
        claims=checks,
        facts=facts,
        citation_validity=n_valid / n_cites if n_cites else None,
        claim_support=n_supported / len(facts) if facts else None,
        coverage=cov,
        covered_gold=covered,
        penalty=penalty,
        n_nongold_supported=n_nongold,
        nongold_over_allowance=over,
        precision_penalty=prec_penalty,
        flagged=is_flagged,
        abstention_correct=abstention_correct(item, parsed),
        premise_asserted=(
            None if item.premise is None else any(c.asserts_premise for c in checks)
        ),
        raw=raw,
        reward=clip(raw),
    )
