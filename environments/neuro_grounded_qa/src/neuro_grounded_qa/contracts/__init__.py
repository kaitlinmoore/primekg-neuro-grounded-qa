"""Shared contracts: the interfaces the data, reward and harness code build against.

Each module fixes one interface as code, with contract tests in ``tests/contracts/``:

- ``evidence``: kind-tagged evidence units (graph edges now, ``E:`` IDs; passages
  reserved, ``P:``), graph-neutral entities, and the ``EvidenceStore`` protocol with
  a small in-memory implementation for fixtures.
- ``items``: the question item schema, a ``vf.TaskData`` subclass carrying gold
  triples, gold evidence, answerability, evidence scope and provenance.
- ``output``: the model's final answer format (claims as graph-ID triples citing
  evidence IDs, or an abstention) and its strict parser ``parse_output``.
- ``trace``: the JSON payload evidence-returning tools emit, and ``retrieved_ids``,
  which decides under the strict-retrieval rule which evidence the model actually
  retrieved.

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
from neuro_grounded_qa.contracts.items import (
    QTYPE_ANSWERABLE,
    Answerable,
    EvidenceScope,
    GoldPoint,
    ItemSource,
    QType,
    QuestionItem,
    Split,
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
    # items
    "QTYPE_ANSWERABLE",
    "Answerable",
    "EvidenceScope",
    "GoldPoint",
    "ItemSource",
    "QType",
    "QuestionItem",
    "Split",
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
