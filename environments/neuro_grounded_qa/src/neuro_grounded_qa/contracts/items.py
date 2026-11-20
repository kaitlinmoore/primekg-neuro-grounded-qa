"""The question item schema: one question, its gold answer and its provenance.

The data lane writes items, the harness turns them into tasks, and the reward scores
traces against their gold. ``QuestionItem`` subclasses ``vf.TaskData`` so an item *is*
the task data Verifiers carries onto every trace (``trace.task.data``): the reward reads
gold straight off the trace, and nothing has to be joined back in by ID.

Gold comes from the graph, computed in code. Each gold point is a triple in the same
shape as a model claim (entity IDs plus the graph's relation label), so coverage is
checked with ``contracts.facts.same_fact``: in either direction, or in order when the
gold point is flagged ``directed``.
"""

from __future__ import annotations

from typing import Literal

import verifiers.v1 as vf
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from neuro_grounded_qa.contracts.evidence import is_evidence_id
from neuro_grounded_qa.contracts.facts import same_fact

QType = Literal["single_hop", "multi_hop", "partial", "unanswerable", "false_premise"]
"""Question type. Drives item generation and per-type reporting."""

Answerable = Literal["yes", "partial", "correctable", "no"]
"""What the evidence scope can support.

- ``yes``: a full answer.
- ``partial``: part of the question; the rest should be named as unsupported. The
  item records which parts in ``graph_gaps``.
- ``correctable``: the question rests on a false premise that an edge contradicts.
  Abstaining and a cited correction are both correct.
- ``no``: nothing; only abstaining is correct.
"""

EvidenceScope = Literal["graph"]
"""Which evidence the answerability label is relative to.

Phase 1 is ``"graph"`` only: an item marked unanswerable is unanswerable *from the
graph*. ``"graph+pubmed"`` is reserved for phase 2, so that adding PubMed never
silently changes what a phase-1 label meant.
"""

ItemSource = Literal["template", "template+llm_paraphrase", "human"]
"""How the question text was produced. Gold always comes from the graph via code,
whatever the source; this records who or what wrote the wording."""

Split = Literal["train", "heldout"]
"""Data split. Held-out items stay unexamined until the freeze."""

QTYPE_ANSWERABLE: dict[str, frozenset[str]] = {
    "single_hop": frozenset({"yes"}),
    "multi_hop": frozenset({"yes"}),
    "partial": frozenset({"partial"}),
    "unanswerable": frozenset({"no"}),
    "false_premise": frozenset({"no", "correctable"}),
}
"""The answerability values each question type allows.

A false-premise item is ``no`` when the premise is simply absent from the graph, and
``correctable`` when an edge contradicts it.
"""


def _bad_evidence_ids(ids: list[str]) -> list[str]:
    return [i for i in ids if not is_evidence_id(i)]


class GoldPoint(BaseModel):
    """One fact a complete answer should state, as a graph triple.

    Same shape as a model ``Claim`` (subject ID, the graph's relation label, object
    ID), so coverage is ``same_fact``. ``evidence`` lists the evidence IDs that state
    this fact: at least one, since gold comes from the graph.

    ``directed``: when True, subject → object order is meaningful under the
    relation's documented convention, and a claim covers this point only in that
    order; otherwise either order counts. The data lane sets it, normalizing each
    directed relation to one convention (for hierarchies: subject = child or
    narrower term, object = parent or broader term). It should match the flag on the
    evidence edges. The contracts encode no relation names.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    relation: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)
    directed: bool = False

    @field_validator("evidence")
    @classmethod
    def _evidence_ids_well_formed(cls, v: list[str]) -> list[str]:
        if bad := _bad_evidence_ids(v):
            raise ValueError(f"malformed evidence IDs in gold point: {bad}")
        return v


class PremiseTriple(BaseModel):
    """The false assumption a false-premise question makes, as a triple.

    Code has verified that the graph has no edge stating it. Recorded so the reward
    and the audit can check whether a model asserted the premise anyway.

    ``directed``: when True, subject → object order is meaningful under the
    relation's documented convention (set by the data lane, as for ``GoldPoint``), so
    the premise is compared in order. The reverse of a directed fact is a different
    fact, so a premise may be the reverse of a directed gold point: "A is a subtype
    of B" when the graph says B is a subtype of A is a legitimate false premise.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_id: str = Field(min_length=1)
    relation: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    directed: bool = False


class GraphGap(BaseModel):
    """One part of a partial question that the graph can't support.

    Recorded on ``partial`` items only, in ``QuestionItem.graph_gaps`` (at least one
    per partial item). A gap is a fact about the item: what the graph lacks. It is
    kept apart, in name too, from the output field ``Answer.unsupported_parts``,
    which is what the model flagged. Recording the gaps lets grading, and any later
    check of flag text, judge whether a model's flags named the right parts. Reward
    v0 doesn't score against them: a partial answer earns the flag credit for any
    non-blank flag. (It does read gap triples' ``directed`` flags, to compare claims
    with each other; see ``reward.scoring.directed_relations``.)

    - ``description``: what the question asks that the graph doesn't support, in
      plain words. Must not be blank.
    - ``triple``: the specific absent fact, when the gap is one checkable triple;
      None when it isn't (for example, the question asks for an unknown entity, so
      there is no single triple to name). Same shape as a premise, ``directed`` flag
      included.

    The contract can only check a triple against the item itself: it never states the
    same fact as a gold point or another gap's triple, and its ``directed`` flag
    agrees with the item's other triples on the same relation. Whether the graph
    really lacks it can't be checked here (an item has no store). The question
    generator must check each triple's absence against the **full** graph, not just
    the subgraph, as for false premises.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    description: str = Field(min_length=1)
    triple: PremiseTriple | None = None

    @field_validator("description")
    @classmethod
    def _description_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("graph gap description must not be blank")
        return v


class QuestionItem(vf.TaskData):
    """A question item: Verifiers task data plus gold and provenance.

    Inherits the other ``vf.TaskData`` fields, which are frozen, and makes ``id`` and
    ``prompt`` required: items need durable IDs for provenance and splits, and a
    question. ``extra="forbid"`` is added so a misspelled field in an items file fails
    loudly instead of being dropped.

    ``build_id`` binds the item to the store build it was generated from: its
    evidence IDs name edges of that build only. It is required and non-blank; the
    taskset checks it against the store's ``build_id`` before any rollout.

    Validators enforce the label rules the reward relies on:

    - ``qtype`` and ``answerable`` agree (``QTYPE_ANSWERABLE``).
    - ``answerable="no"``: no gold points and no gold evidence.
      ``yes``, ``partial`` and ``correctable``: at least one gold point.
    - Every evidence ID is well-formed, and each gold point's evidence is a subset
      of ``gold_evidence``.
    - ``premise`` is set exactly when ``qtype="false_premise"``, and never states the
      same fact as a gold point (by ``same_fact``, so the reverse of a directed gold
      point is allowed).
    - ``graph_gaps`` is non-empty when ``answerable="partial"`` and empty otherwise
      (on ``correctable`` items the premise already names what's wrong). No gap
      triple states the same fact as a gold point or as another gap's triple (by
      ``same_fact``).
    - All triples on one relation label (the gold points, the premise and the gap
      triples) share one ``directed`` value. Mixed flags would let the item validate
      while the reward compares the same relation in order in one place and in
      either order in another. Whether the flags also match the store's edges can't
      be checked here (an item has no store); the data lane's builder checks that.

    For ``correctable`` items the gold points hold the correction, as ordinary
    triples; the output schema needs nothing extra, since a correction is just cited
    claims. How the reward scores an abstention vs a correction on these items is
    the reward's decision, not fixed here.

    For ``partial`` items the gold points hold only the answerable part, and
    ``graph_gaps`` names the rest. The absence of gap triples from the graph is the
    generator's check, not the contract's (see ``GraphGap``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    prompt: str | vf.Messages
    qtype: QType
    answerable: Answerable
    evidence_scope: EvidenceScope
    gold_points: list[GoldPoint] = Field(default_factory=list)
    gold_evidence: list[str] = Field(default_factory=list)
    """Every evidence ID that supports the gold answer."""
    premise: PremiseTriple | None = None
    """The false premise; required for ``false_premise`` items, forbidden otherwise."""
    graph_gaps: list[GraphGap] = Field(default_factory=list)
    """The parts of the question the graph can't support; non-empty for
    ``answerable="partial"``, empty otherwise."""
    build_id: str = Field(min_length=1)
    """The ``build_id`` of the evidence store whose ``E:`` IDs this item cites
    (``EvidenceStore.build_id``). Evidence IDs are only meaningful within one build,
    so the taskset refuses to pair an item with a store of a different build."""
    source: ItemSource
    split: Split
    template_id: str | None = None
    """The generating template, for templated items; None for hand-written ones."""
    notes: str = ""

    @field_validator("build_id")
    @classmethod
    def _build_id_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("build_id must not be blank")
        return v

    @model_validator(mode="after")
    def _labels_consistent(self) -> QuestionItem:
        if not self.prompt_text.strip():
            raise ValueError("prompt must not be empty")
        allowed = QTYPE_ANSWERABLE[self.qtype]
        if self.answerable not in allowed:
            raise ValueError(
                f"qtype {self.qtype!r} allows answerable in {sorted(allowed)}, "
                f"got {self.answerable!r}"
            )
        if self.answerable == "no":
            if self.gold_points:
                raise ValueError('answerable="no" items must have no gold points')
            if self.gold_evidence:
                raise ValueError('answerable="no" items must have no gold evidence')
        elif not self.gold_points:
            raise ValueError(
                f'answerable="{self.answerable}" items need at least one gold point'
            )
        if bad := _bad_evidence_ids(self.gold_evidence):
            raise ValueError(f"malformed gold_evidence IDs: {bad}")
        gold = set(self.gold_evidence)
        for gp in self.gold_points:
            if missing := [e for e in gp.evidence if e not in gold]:
                raise ValueError(
                    f"gold point {gp.id!r} cites evidence not in gold_evidence: {missing}"
                )
        if (self.qtype == "false_premise") != (self.premise is not None):
            raise ValueError("premise is required for false_premise items only")
        if (self.answerable == "partial") != bool(self.graph_gaps):
            raise ValueError(
                'graph_gaps must be non-empty for answerable="partial" items and empty '
                f'otherwise; got {len(self.graph_gaps)} on answerable="{self.answerable}"'
            )
        gap_triples = [g.triple for g in self.graph_gaps if g.triple is not None]
        flags: dict[str, bool] = {}
        triples = [
            *self.gold_points,
            *([self.premise] if self.premise else []),
            *gap_triples,
        ]
        for t in triples:
            if flags.setdefault(t.relation, t.directed) != t.directed:
                raise ValueError(
                    f"mixed directed flags on relation {t.relation!r}: every gold point, "
                    "the premise and every graph gap triple on one relation must share "
                    "one directed value"
                )
        if self.premise is not None:
            for gp in self.gold_points:
                if same_fact(self.premise, gp):
                    raise ValueError(f"premise states the same fact as gold point {gp.id!r}")
        for i, t in enumerate(gap_triples):
            for gp in self.gold_points:
                if same_fact(t, gp):
                    raise ValueError(
                        f"a graph gap triple states the same fact as gold point {gp.id!r}"
                    )
            if any(same_fact(t, other) for other in gap_triples[:i]):
                raise ValueError("two graph gap triples state the same fact")
        return self
