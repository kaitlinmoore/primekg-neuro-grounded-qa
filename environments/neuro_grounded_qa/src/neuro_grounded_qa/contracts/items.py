"""The question item schema: one question, its gold answer and its provenance.

The data lane writes items, the harness turns them into tasks, and the reward scores
traces against their gold. ``QuestionItem`` subclasses ``vf.TaskData`` so an item *is*
the task data Verifiers carries onto every trace (``trace.task.data``): the reward reads
gold straight off the trace, and nothing has to be joined back in by ID.

Gold comes from the graph, computed in code. Each gold point is a triple in the same
shape as a model claim (entity IDs plus the graph's relation label), so coverage is
checked with ``contracts.facts.same_fact``.
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
- ``partial``: part of the question; the rest should be named as unsupported.
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
    ID), so coverage is ``same_fact`` (direction-insensitive). ``evidence`` lists the
    evidence IDs that state this fact: at least one, since gold comes from the graph.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    relation: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)

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
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_id: str = Field(min_length=1)
    relation: str = Field(min_length=1)
    object_id: str = Field(min_length=1)


class QuestionItem(vf.TaskData):
    """A question item: Verifiers task data plus gold and provenance.

    Inherits the other ``vf.TaskData`` fields, which are frozen, and makes ``id`` and
    ``prompt`` required: items need durable IDs for provenance and splits, and a
    question. ``extra="forbid"`` is added so a misspelled field in an items file fails
    loudly instead of being dropped.

    Validators enforce the label rules the reward relies on:

    - ``qtype`` and ``answerable`` agree (``QTYPE_ANSWERABLE``).
    - ``answerable="no"``: no gold points and no gold evidence.
      ``yes``, ``partial`` and ``correctable``: at least one gold point.
    - Every evidence ID is well-formed, and each gold point's evidence is a subset
      of ``gold_evidence``.
    - ``premise`` is set exactly when ``qtype="false_premise"``, and never states the
      same fact as a gold point.

    For ``correctable`` items the gold points hold the correction, as ordinary
    triples; the output schema needs nothing extra, since a correction is just cited
    claims. How the reward scores an abstention vs a correction on these items is
    the reward's decision, not fixed here.
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
    source: ItemSource
    split: Split
    template_id: str | None = None
    """The generating template, for templated items; None for hand-written ones."""
    notes: str = ""

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
        if self.premise is not None:
            for gp in self.gold_points:
                if same_fact(self.premise, gp):
                    raise ValueError(f"premise states the same fact as gold point {gp.id!r}")
        return self
