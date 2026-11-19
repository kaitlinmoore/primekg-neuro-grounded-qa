"""Evidence units, evidence IDs and the evidence store interface.

Everything the model is allowed to ground an answer in is an *evidence unit* with a
stable ID. The reward checks claims against evidence units, the toolset returns them,
and the data lane builds a store of them from the graph. This module fixes their shape
so those three lanes agree without reading each other's code.

Two rules shape it:

- **Kind-tagged.** Each unit carries a ``kind`` and its ID carries a matching prefix
  (``E:`` for graph edges; ``P:`` reserved for phase-2 text passages). The support
  check dispatches on kind, so phase 2 can add passages, and a judge-backed checker
  for them, without touching edge handling.
- **Graph-neutral.** Nothing here names PrimeKG columns. Entity IDs are whatever the
  source graph uses, kept opaque; relation labels are the graph's own strings. The
  data lane maps PrimeKG (or OptimusKG) onto these models.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

EvidenceKind = Literal["edge", "passage"]
"""The kinds of evidence unit. Only ``edge`` has a model in phase 1; ``passage`` is
reserved for PubMed abstracts in phase 2."""

EDGE_PREFIX = "E:"
"""ID prefix for graph edges."""
PASSAGE_PREFIX = "P:"
"""ID prefix reserved for phase-2 text passages. No passage ID format is defined yet."""

EDGE_ID_RE = re.compile(r"^E:\d{6,}$")
"""Full edge ID format: ``E:`` then at least six digits, e.g. ``E:000001``. A fixed,
boring format makes IDs easy to validate exactly and hard to confuse with names."""

_KIND_BY_PREFIX: dict[str, EvidenceKind] = {
    EDGE_PREFIX: "edge",
    PASSAGE_PREFIX: "passage",
}

# Full-format patterns, by kind. A kind whose format isn't defined yet (passage) has no
# entry, so no ID of that kind is valid until phase 2 adds one.
_ID_FORMAT: dict[EvidenceKind, re.Pattern[str]] = {"edge": EDGE_ID_RE}


def evidence_kind(evidence_id: str) -> EvidenceKind:
    """Return the evidence kind an ID's prefix names.

    This is how the support check will dispatch: by the ID's namespace, without
    having to look the unit up first. It checks only the prefix, not the full
    format; use :func:`is_evidence_id` for that.

    Raises:
        ValueError: if the ID has no known prefix. An unknown prefix is a bug or a
            fabricated ID, and silently guessing a kind would hide it.
    """
    for prefix, kind in _KIND_BY_PREFIX.items():
        if isinstance(evidence_id, str) and evidence_id.startswith(prefix):
            return kind
    raise ValueError(f"unknown evidence ID prefix: {evidence_id!r}")


def is_evidence_id(s: object) -> bool:
    """True if ``s`` is a well-formed evidence ID of a kind whose format is defined.

    Used to validate gold evidence on question items and evidence IDs in tool
    payloads. It is a format check only: a well-formed ID may still not exist in the
    store. Passage IDs (``P:``) return False until phase 2 defines their format, so
    nothing can cite a passage before passages exist.
    """
    if not isinstance(s, str):
        return False
    try:
        kind = evidence_kind(s)
    except ValueError:
        return False
    pattern = _ID_FORMAT.get(kind)
    return pattern is not None and pattern.fullmatch(s) is not None


class Entity(BaseModel):
    """A graph node, as tools show it to the model and as claims refer to it.

    ``id`` is the source graph's own node ID, kept opaque: the contracts never parse
    it. Claims cite entities by this ID, so the support check is exact equality and
    never depends on name matching. ``name`` and ``type`` are for display and search.
    ``source`` is the node's source database, if the graph records one.

    Frozen, so an entity can be used as a dict key and can't be edited after a tool
    returns it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    name: str
    type: str
    source: str | None = None


class EdgeEvidence(BaseModel):
    """One graph edge as an evidence unit: ``subject --relation--> object``.

    ``relation`` is the graph's relation label, copied verbatim; claims must copy it
    exactly, which is what makes the support check exact equality.
    ``display_relation`` is an optional human-readable label and is never scored.

    Subject and object are full :class:`Entity` objects rather than bare IDs, so a
    tool result is self-contained and the model can read names without another call.

    Frozen, with ``kind`` fixed to ``"edge"``, so the support check can dispatch on
    ``kind`` and the unit can't change between retrieval and scoring.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    kind: Literal["edge"] = "edge"
    subject: Entity
    relation: str = Field(min_length=1)
    display_relation: str | None = None
    object: Entity

    @field_validator("id")
    @classmethod
    def _edge_id_format(cls, v: str) -> str:
        if not EDGE_ID_RE.fullmatch(v):
            raise ValueError(f"edge ID must match {EDGE_ID_RE.pattern}, got {v!r}")
        return v


Evidence = EdgeEvidence
"""Any evidence unit. In phase 1 this is just :class:`EdgeEvidence`.

Phase 2 adds a ``PassageEvidence`` model (``kind: Literal["passage"]``, a ``P:`` ID
format registered in ``_ID_FORMAT``) and widens this alias to a discriminated union::

    Evidence = Annotated[EdgeEvidence | PassageEvidence, Field(discriminator="kind")]

Code that reads ``evidence.kind`` keeps working unchanged.
"""


@runtime_checkable
class EvidenceStore(Protocol):
    """What the toolset and the reward need from an evidence store.

    The data lane's real store (built from the downloaded graph) and the in-memory
    fixture store both satisfy this, so the harness and reward lanes can build and
    test against fixtures before any graph data exists. ``isinstance`` checks only
    that the methods exist, not their behavior; the contract tests cover behavior.
    """

    def get(self, evidence_id: str) -> Evidence | None:
        """The evidence unit with this ID, or None if the store has none."""
        ...

    def search_entities(self, text: str, limit: int) -> list[Entity]:
        """Entities whose names match ``text``, at most ``limit``, in a stable order."""
        ...

    def neighbors(
        self, entity_id: str, relation: str | None, limit: int
    ) -> list[EdgeEvidence]:
        """Edges touching ``entity_id`` (either end), optionally of one relation, at
        most ``limit``, in a stable order."""
        ...

    def relations(self) -> list[str]:
        """The closed relation vocabulary: every relation label in the store."""
        ...


class InMemoryEvidenceStore:
    """A small deterministic :class:`EvidenceStore` built from a list of edges.

    For fixtures and tests, not for the real graph. Its behavior is the reference the
    real store should match:

    - ``search_entities``: case-insensitive substring match on names. Exact matches
      first, then prefix matches, then other substrings; ties by name, then ID.
      Blank text returns nothing rather than every entity.
    - ``neighbors``: edges where the entity is subject *or* object, ordered by edge ID.
      Both directions are returned because a question may need inbound edges.
    - ``relations``: sorted unique labels.

    Raises ``ValueError`` at construction on duplicate edge IDs, or on two different
    entities sharing an ID, since either would make lookups ambiguous.
    """

    def __init__(
        self, edges: Iterable[EdgeEvidence], entities: Iterable[Entity] = ()
    ) -> None:
        """Build the store.

        Args:
            edges: the edges. Their subject and object entities are added
                automatically.
            entities: extra entities, for nodes with no edges in the store (useful
                for "absent" cases, where an entity exists but nothing links it).
        """
        self._edges: dict[str, EdgeEvidence] = {}
        self._entities: dict[str, Entity] = {}
        for entity in entities:
            self._add_entity(entity)
        for edge in edges:
            if edge.id in self._edges:
                raise ValueError(f"duplicate edge ID {edge.id!r}")
            self._edges[edge.id] = edge
            self._add_entity(edge.subject)
            self._add_entity(edge.object)
        self._edges = dict(sorted(self._edges.items()))

    def _add_entity(self, entity: Entity) -> None:
        existing = self._entities.get(entity.id)
        if existing is not None and existing != entity:
            raise ValueError(f"conflicting entities for ID {entity.id!r}")
        self._entities[entity.id] = entity

    @classmethod
    def from_records(cls, data: Mapping[str, Any]) -> InMemoryEvidenceStore:
        """Build a store from a normalized dict, the format of the fixture file.

        ``data`` has ``entities`` (a list of :class:`Entity` dicts) and ``edges``
        (dicts with ``id``, ``subject`` and ``object`` as entity IDs, ``relation``
        and optional ``display_relation``). Entities are stored once and edges refer
        to them, which keeps fixture files short and consistent.

        Raises:
            ValueError: if an edge refers to an entity ID not in ``entities``.
        """
        entities = {e["id"]: Entity.model_validate(e) for e in data.get("entities", [])}
        edges = []
        for record in data.get("edges", []):
            missing = {record["subject"], record["object"]} - entities.keys()
            if missing:
                raise ValueError(f"edge {record['id']!r} refers to unknown {missing}")
            edges.append(
                EdgeEvidence(
                    id=record["id"],
                    subject=entities[record["subject"]],
                    relation=record["relation"],
                    display_relation=record.get("display_relation"),
                    object=entities[record["object"]],
                )
            )
        return cls(edges, entities.values())

    def get(self, evidence_id: str) -> Evidence | None:
        """The edge with this ID, or None."""
        return self._edges.get(evidence_id)

    def entity(self, entity_id: str) -> Entity | None:
        """The entity with this ID, or None. A convenience, not part of the Protocol."""
        return self._entities.get(entity_id)

    def search_entities(self, text: str, limit: int) -> list[Entity]:
        """Entities whose names contain ``text``, case-insensitively, best first."""
        _check_limit(limit)
        needle = text.strip().casefold()
        if not needle:
            return []
        hits = []
        for entity in self._entities.values():
            name = entity.name.casefold()
            if needle not in name:
                continue
            rank = 0 if name == needle else 1 if name.startswith(needle) else 2
            hits.append((rank, name, entity.id, entity))
        hits.sort(key=lambda h: h[:3])
        return [h[3] for h in hits[:limit]]

    def neighbors(
        self, entity_id: str, relation: str | None, limit: int
    ) -> list[EdgeEvidence]:
        """Edges with ``entity_id`` at either end, optionally one relation, by ID."""
        _check_limit(limit)
        out = [
            edge
            for edge in self._edges.values()
            if entity_id in (edge.subject.id, edge.object.id)
            and (relation is None or edge.relation == relation)
        ]
        return out[:limit]

    def relations(self) -> list[str]:
        """Sorted unique relation labels."""
        return sorted({edge.relation for edge in self._edges.values()})

    def __len__(self) -> int:
        return len(self._edges)


def _check_limit(limit: int) -> None:
    if limit < 0:
        raise ValueError(f"limit must be >= 0, got {limit}")
