"""Shared contracts: the interfaces the data, reward and harness code build against.

Each module fixes one interface as code, with contract tests in ``tests/contracts/``:

- ``evidence``: kind-tagged evidence units (graph edges now, ``E:`` IDs; passages
  reserved, ``P:``), graph-neutral entities, and the ``EvidenceStore`` protocol with
  a small in-memory implementation for fixtures. Edges are undirected facts unless
  flagged ``directed``.
- ``facts``: ``same_fact`` and ``supports``, the one rule for comparing triples
  (claims, gold points, premises, edges): either direction, except in order when a
  side is flagged ``directed``.
- ``items``: the question item schema, a ``vf.TaskData`` subclass carrying gold
  triples, gold evidence, answerability (including correctable false premises),
  the unsupported part of partially answerable questions, evidence scope and
  provenance.
- ``output``: the model's final answer format (claims as graph-ID triples citing
  evidence IDs, or an abstention) and its strict parser ``parse_output``.
- ``trace``: the JSON payload evidence-returning tools emit, and ``retrieved_ids``,
  which decides under the strict-retrieval rule (sampled call, descendant, one
  result per call) which evidence the model actually retrieved.

Claims cite entities by graph ID and relations by the graph's own labels; names are
display-only. Change these only on purpose, together with their tests.
"""

from neuro_grounded_qa.contracts.evidence import (
    EDGE_ID_RE,
    EDGE_PREFIX,
    PASSAGE_PREFIX,
    EdgeEvidence,
    Entity,
    Evidence,
    EvidenceKind,
    EvidenceStore,
    InMemoryEvidenceStore,
    evidence_kind,
    is_evidence_id,
)
from neuro_grounded_qa.contracts.facts import (
    Fact,
    TripleLike,
    is_directed,
    same_fact,
    supports,
    triple,
)
from neuro_grounded_qa.contracts.items import (
    QTYPE_ANSWERABLE,
    Answerable,
    EvidenceScope,
    GoldPoint,
    ItemSource,
    PremiseTriple,
    QType,
    QuestionItem,
    Split,
    UnsupportedPart,
)
from neuro_grounded_qa.contracts.output import (
    ANSWER_KEYS,
    Abstain,
    Answer,
    Claim,
    ParseFailure,
    ParseFailureReason,
    ParseResult,
    parse_output,
)
from neuro_grounded_qa.contracts.trace import (
    AcceptedToolMessage,
    RejectedToolMessage,
    RejectionReason,
    RetrievalAudit,
    ToolResultPayload,
    audit_retrieval,
    retrieved_evidence,
    retrieved_ids,
)

__all__ = [
    # evidence
    "EDGE_ID_RE",
    "EDGE_PREFIX",
    "PASSAGE_PREFIX",
    "EdgeEvidence",
    "Entity",
    "Evidence",
    "EvidenceKind",
    "EvidenceStore",
    "InMemoryEvidenceStore",
    "evidence_kind",
    "is_evidence_id",
    # facts
    "Fact",
    "TripleLike",
    "is_directed",
    "same_fact",
    "supports",
    "triple",
    # items
    "QTYPE_ANSWERABLE",
    "Answerable",
    "EvidenceScope",
    "GoldPoint",
    "ItemSource",
    "PremiseTriple",
    "QType",
    "QuestionItem",
    "Split",
    "UnsupportedPart",
    # output
    "ANSWER_KEYS",
    "Abstain",
    "Answer",
    "Claim",
    "ParseFailure",
    "ParseFailureReason",
    "ParseResult",
    "parse_output",
    # trace
    "AcceptedToolMessage",
    "RejectedToolMessage",
    "RejectionReason",
    "RetrievalAudit",
    "ToolResultPayload",
    "audit_retrieval",
    "retrieved_evidence",
    "retrieved_ids",
]
