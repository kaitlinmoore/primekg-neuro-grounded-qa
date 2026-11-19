"""The one rule for "do these two triples state the same fact?".

Claims, gold points, false premises and graph edges are all triples of
``(subject_id, relation, object_id)``. The support check (does a cited edge state the
claim?), gold-point coverage (does a claim state a gold point?) and item validation
(is the false premise really different from the correction?) all compare triples.
They use the helpers here, so the data and reward code apply the same rule.

**Facts match in either direction by default.** An edge's subject/object order is
arbitrary but stable (the store keeps one row per edge), so a fact matches in either
order as long as the relation label is identical. ``(GENE_A, associated_with, DIS_1)``
and ``(DIS_1, associated_with, GENE_A)`` are the same fact. The relation label still
has to match exactly: there's no synonym or inverse-label matching.

This means relations whose direction carries meaning but is fixed by the entity types
(``targets``, ``indicated_for``: a drug targets a gene, never the reverse) are also
compared without direction. That's deliberate: PrimeKG's stored edge order is
arbitrary, it ships many edges twice, once per orientation, and the entity types
already fix the roles. The data lane's store builder deduplicates those reversed
duplicate rows into one edge with one ID.

**Except facts flagged ``directed``.** ``EdgeEvidence``, ``GoldPoint`` and
``PremiseTriple`` carry ``directed: bool = False``. Same-type hierarchy relations are
the case it exists for: "A is a subtype of B" reversed is a different, usually false,
fact, and the entity types can't tell the two readings apart. The rule:

- ``same_fact(a, b)`` compares in order when **either** side has ``directed=True``,
  and in either order otherwise.
- ``supports(claim, edge)`` follows ``edge.directed``.
- A ``Claim`` has no flag, so a claim is compared in order against a directed gold
  point, premise or edge, and in either order against an undirected one.

Which relations are directed, and their convention, is the data's choice: the data
lane sets the flag and normalizes each directed relation so that order follows its
documented convention (for hierarchies: subject = child, object = parent). Nothing
here names a relation.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from neuro_grounded_qa.contracts.evidence import EdgeEvidence


@runtime_checkable
class TripleLike(Protocol):
    """Anything with ``subject_id``, ``relation`` and ``object_id``: a ``Claim``, a
    ``GoldPoint`` or a ``PremiseTriple``. A ``directed`` flag is optional, so it isn't
    part of the protocol; :func:`is_directed` reads it."""

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


def is_directed(x: Fact) -> bool:
    """True if ``x`` is flagged ``directed``. Anything without the flag (a ``Claim``)
    counts as undirected."""
    return getattr(x, "directed", False) is True


def same_fact(a: Fact, b: Fact) -> bool:
    """True if ``a`` and ``b`` state the same fact.

    The relation labels must be identical. The two entity IDs must match in order if
    either side is flagged ``directed``, and in either order otherwise. Exact string
    equality throughout; no normalization. Symmetric: ``same_fact(a, b) ==
    same_fact(b, a)``.
    """
    sa, ra, oa = triple(a)
    sb, rb, ob = triple(b)
    if ra != rb:
        return False
    if (sa, oa) == (sb, ob):
        return True
    return not (is_directed(a) or is_directed(b)) and (sa, oa) == (ob, sb)


def supports(claim: TripleLike, edge: EdgeEvidence) -> bool:
    """True if ``edge`` states ``claim``: the support check for edge evidence.

    Direction follows ``edge.directed``: a directed edge supports the claim only in
    its stored order, an undirected one in either order.

    This compares content only. Whether the claim actually cites this edge, and
    whether the edge was retrieved, is up to the caller (the reward), which should
    pass only the edges the claim cites that ``retrieved_evidence`` returned.
    """
    return same_fact(claim, edge)
