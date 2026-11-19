"""The question item schema: one question, its gold answer and its provenance.

The data lane writes items, the harness turns them into tasks, and the reward scores
traces against their gold. ``QuestionItem`` subclasses ``vf.TaskData`` so an item *is*
the task data Verifiers carries onto every trace (``trace.task.data``): the reward reads
gold straight off the trace, and nothing has to be joined back in by ID.

Gold comes from the graph, computed in code. Each gold point is a triple in the same
shape as a model claim (entity IDs plus the graph's relation label), so coverage can be
checked by exact equality.
"""

from __future__ import annotations

from typing import Literal

import verifiers.v1 as vf
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from neuro_grounded_qa.contracts.evidence import is_evidence_id

QType = Literal["single_hop", "multi_hop", "partial", "unanswerable", "false_premise"]
"""Question type. Drives item generation and per-type reporting."""

Answerable = Literal["yes", "partial", "no"]
"""Whether the evidence scope can support an answer: fully, in part, or not at all."""

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

# The answerability each question type implies. ``false_premise -> "no"`` is the
# current choice and is open: a false-premise item could instead allow an answer that
# corrects the premise, which would make it "partial" or "yes" and score differently.
QTYPE_ANSWERABLE: dict[str, str] = {
    "single_hop": "yes",
    "multi_hop": "yes",
    "partial": "partial",
    "unanswerable": "no",
    "false_premise": "no",
}


def _bad_evidence_ids(ids: list[str]) -> list[str]:
    return [i for i in ids if not is_evidence_id(i)]


class GoldPoint(BaseModel):
    """One fact a complete answer should state, as a graph triple.

    Same shape as a model ``Claim`` (subject ID, the graph's relation label, object
    ID), so coverage is exact matching. ``evidence`` lists the evidence IDs that state
    this fact; there may be more than one when the graph repeats a fact.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    relation: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    evidence: list[str]

    @field_validator("evidence")
    @classmethod
    def _evidence_ids_well_formed(cls, v: list[str]) -> list[str]:
        if bad := _bad_evidence_ids(v):
            raise ValueError(f"malformed evidence IDs in gold point: {bad}")
        return v


class QuestionItem(vf.TaskData):
    """A question item: Verifiers task data plus gold and provenance.

    Inherits ``id``, ``prompt``, ``system_prompt`` and the rest from ``vf.TaskData``,
    which is frozen. ``extra="forbid"`` is added so a misspelled field in an items
    file fails loudly instead of being dropped.

    Validators enforce the label rules that the reward relies on:

    - ``answerable="no"`` has no gold points; ``answerable="yes"`` has at least one.
    - ``qtype`` and ``answerable`` agree (see ``QTYPE_ANSWERABLE``).
    - every ``gold_evidence`` ID is a well-formed evidence ID.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    qtype: QType
    answerable: Answerable
    evidence_scope: EvidenceScope
    gold_points: list[GoldPoint] = Field(default_factory=list)
    gold_evidence: list[str] = Field(default_factory=list)
    """Every evidence ID that supports the gold answer."""
    source: ItemSource
    split: Split
    notes: str = ""

    @model_validator(mode="after")
    def _labels_consistent(self) -> QuestionItem:
        if self.answerable == "no" and self.gold_points:
            raise ValueError('answerable="no" items must have no gold points')
        if self.answerable == "yes" and not self.gold_points:
            raise ValueError('answerable="yes" items need at least one gold point')
        expected = QTYPE_ANSWERABLE[self.qtype]
        if self.answerable != expected:
            raise ValueError(
                f"qtype {self.qtype!r} implies answerable={expected!r}, "
                f"got {self.answerable!r}"
            )
        if bad := _bad_evidence_ids(self.gold_evidence):
            raise ValueError(f"malformed gold_evidence IDs: {bad}")
        return self
