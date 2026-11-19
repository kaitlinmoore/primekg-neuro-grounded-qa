"""The one rule for "do these two triples state the same fact?".

Claims, gold points, false premises and graph edges are all triples of
``(subject_id, relation, object_id)``. The support check (does a cited edge state the
claim?), gold-point coverage (does a claim state a gold point?) and item validation
(is the false premise really different from the correction?) all compare triples.
They use the helpers here, so the data and reward code apply the same rule.

**Edges are undirected facts.** An edge's subject/object order is arbitrary but
stable (the store keeps one row per edge), so a fact matches in either order as long
as the relation label is identical. ``(GENE_A, associated_with, DIS_1)`` and
``(DIS_1, associated_with, GENE_A)`` are the same fact. The relation label still has
to match exactly: there's no synonym or inverse-label matching.

This means relations whose direction carries meaning (``targets``,
``indicated_for``) are also compared without direction. That's deliberate: the graph
source doesn't reliably encode direction, and PrimeKG ships many edges twice, once
per orientation. The data lane's store builder deduplicates those reversed duplicate
rows into one edge with one ID.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from neuro_grounded_qa.contracts.evidence import EdgeEvidence


@runtime_checkable
class TripleLike(Protocol):
    """Anything with ``subject_id``, ``relation`` and ``object_id``: a ``Claim``, a
    ``GoldPoint`` or a ``PremiseTriple``."""

    subject_id: str
    relation: str
    object_id: str


Fact = TripleLike | EdgeEvidence
"""What the helpers compare: a triple-shaped model, or an edge (whose ends are full
entities)."""


def triple(x: Fact) -> tuple[str, str, str]:
    """``(subject_id, relation, object_id)`` for a triple-like model or an edge."""
    if isinstance(x, EdgeEvidence):
        return (x.subject.id, x.relation, x.object.id)
    return (x.subject_id, x.relation, x.object_id)


def same_fact(a: Fact, b: Fact) -> bool:
    """True if ``a`` and ``b`` state the same undirected fact.

    The relation labels must be identical, and the two entity IDs must match in
    either order. Exact string equality throughout; no normalization.
    """
    sa, ra, oa = triple(a)
    sb, rb, ob = triple(b)
    return ra == rb and ((sa, oa) == (sb, ob) or (sa, oa) == (ob, sb))


def supports(claim: TripleLike, edge: EdgeEvidence) -> bool:
    """True if ``edge`` states ``claim``: the support check for edge evidence.

    This compares content only. Whether the claim actually cites this edge, and
    whether the edge was retrieved, is up to the caller (the reward), which should
    pass only the edges the claim cites that ``retrieved_evidence`` returned.
    """
    return same_fact(claim, edge)
