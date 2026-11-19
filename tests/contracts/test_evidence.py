"""Contract tests for evidence IDs, evidence models and the evidence store protocol."""

import pytest
from pydantic import ValidationError

from contracts.builders import fixture_store
from neuro_grounded_qa.contracts import (
    EdgeEvidence,
    Entity,
    EvidenceStore,
    InMemoryEvidenceStore,
    ToolResultPayload,
    evidence_kind,
    is_evidence_id,
)

A = Entity(id="GENE_A", name="Gene Alpha", type="gene")
D = Entity(id="DIS_1", name="Disease One", type="disease")


# --- IDs and kinds -----------------------------------------------------------


@pytest.mark.parametrize("eid", ["E:000001", "E:123456", "E:0000001234"])
def test_well_formed_edge_ids(eid):
    assert is_evidence_id(eid)
    assert evidence_kind(eid) == "edge"


@pytest.mark.parametrize(
    "eid",
    ["E:00001", "E:abcdef", "e:000001", "E000001", " E:000001", "E:000001 ", "", None, 1],
)
def test_malformed_ids_rejected(eid):
    assert not is_evidence_id(eid)


def test_passage_prefix_is_reserved():
    # The kind is recognized, so dispatch code can be written now, but no passage ID
    # is valid until phase 2 defines the format.
    assert evidence_kind("P:000001") == "passage"
    assert not is_evidence_id("P:000001")


@pytest.mark.parametrize("eid", ["X:000001", "GENE_A", "", "edge:1"])
def test_unknown_prefix_raises(eid):
    with pytest.raises(ValueError, match="unknown evidence ID prefix"):
        evidence_kind(eid)


# --- models ------------------------------------------------------------------


def test_edge_is_kind_tagged():
    edge = EdgeEvidence(id="E:000001", subject=A, relation="associated_with", object=D)
    assert edge.kind == "edge"
    assert edge.display_relation is None


def test_edge_id_format_enforced():
    with pytest.raises(ValidationError):
        EdgeEvidence(id="E:1", subject=A, relation="associated_with", object=D)
    with pytest.raises(ValidationError):
        EdgeEvidence(id="P:000001", subject=A, relation="associated_with", object=D)


def test_edge_kind_cannot_be_overridden():
    with pytest.raises(ValidationError):
        EdgeEvidence(
            id="E:000001", kind="passage", subject=A, relation="r", object=D
        )


def test_models_are_frozen():
    edge = EdgeEvidence(id="E:000001", subject=A, relation="associated_with", object=D)
    with pytest.raises(ValidationError):
        edge.relation = "targets"
    with pytest.raises(ValidationError):
        A.name = "Something else"


def test_models_forbid_extra_fields():
    with pytest.raises(ValidationError):
        Entity(id="X", name="x", type="t", score=1.0)


def test_edge_json_round_trip():
    edge = EdgeEvidence(id="E:000001", subject=A, relation="associated_with", object=D)
    assert EdgeEvidence.model_validate_json(edge.model_dump_json()) == edge


def test_edge_directed_defaults_to_false():
    edge = EdgeEvidence(id="E:000001", subject=A, relation="associated_with", object=D)
    assert edge.directed is False
    directed = EdgeEvidence(
        id="E:000002", subject=D, relation="subtype_of", object=D, directed=True
    )
    assert EdgeEvidence.model_validate_json(directed.model_dump_json()) == directed


# --- store -------------------------------------------------------------------


@pytest.fixture
def store():
    return fixture_store()


def test_fixture_store_satisfies_protocol(store):
    assert isinstance(store, EvidenceStore)
    assert len(store) == 13


def test_protocol_requires_entity_and_build_id():
    class Base:
        def get(self, evidence_id): ...
        def search_entities(self, text, limit): ...
        def neighbors(self, entity_id, relation, limit): ...
        def relations(self): ...

    class MissingEntity(Base):
        build_id = "x"

    class MissingBuildId(Base):
        def entity(self, entity_id): ...

    class Complete(MissingEntity):
        def entity(self, entity_id): ...

    assert not isinstance(MissingEntity(), EvidenceStore)
    assert not isinstance(MissingBuildId(), EvidenceStore)
    assert isinstance(Complete(), EvidenceStore)


def test_build_id(store):
    assert store.build_id == "fixture-mini-store-v1"  # from the fixture file
    assert InMemoryEvidenceStore([]).build_id == "fixture"
    assert InMemoryEvidenceStore([], build_id="primekg-x").build_id == "primekg-x"


def test_entity_lookup(store):
    assert store.entity("GENE_D") == Entity(
        id="GENE_D", name="Gene Delta", type="gene", source="SYNTH"
    )
    assert store.entity("DRUG_X").name == "Drug X"
    assert store.entity("NO_SUCH_ENTITY") is None


def test_get_round_trip(store):
    for eid in [f"E:{n:06d}" for n in range(1, 14)]:
        edge = store.get(eid)
        assert edge is not None and edge.id == eid
    assert store.get("E:999999") is None
    edge = store.get("E:000003")
    assert (edge.subject.id, edge.relation, edge.object.id) == ("DRUG_X", "targets", "GENE_A")


def test_search_is_case_insensitive_substring_with_stable_order(store):
    assert [e.id for e in store.search_entities("gene", 10)] == [
        "GENE_A", "GENE_B", "GENE_D", "GENE_C",
    ]  # all prefix matches, ordered by name: Alpha, Beta, Delta, Gamma
    assert [e.id for e in store.search_entities("DISEASE", 10)] == [
        "DIS_G", "DIS_1", "DIS_3", "DIS_2",
    ]  # Group G, One, Three, Two
    assert [e.id for e in store.search_entities("one", 10)] == ["DIS_1"]
    assert store.search_entities("gene", 2) == store.search_entities("gene", 10)[:2]


def test_search_ranks_exact_then_prefix_then_substring():
    ents = [
        Entity(id="1", name="Amyloid beta", type="t"),
        Entity(id="2", name="Beta", type="t"),
        Entity(id="3", name="Beta cell", type="t"),
    ]
    s = InMemoryEvidenceStore([], ents)
    assert [e.id for e in s.search_entities("beta", 10)] == ["2", "3", "1"]


def test_search_edge_cases(store):
    assert store.search_entities("", 10) == []
    assert store.search_entities("   ", 10) == []
    assert store.search_entities("nothing matches this", 10) == []
    assert store.search_entities("gene", 0) == []
    with pytest.raises(ValueError):
        store.search_entities("gene", -1)


def test_isolated_entity_is_searchable_but_has_no_neighbors(store):
    # GENE_D is the "absent" case: it exists, but nothing links it.
    assert [e.id for e in store.search_entities("delta", 5)] == ["GENE_D"]
    assert store.neighbors("GENE_D", None, 10) == []


def test_neighbors_both_directions_ordered_by_id(store):
    assert [e.id for e in store.neighbors("GENE_A", None, 10)] == [
        "E:000001", "E:000003", "E:000007", "E:000011",
    ]
    assert [e.id for e in store.neighbors("GENE_A", "targets", 10)] == ["E:000003"]
    assert [e.id for e in store.neighbors("DIS_1", None, 2)] == ["E:000001", "E:000002"]
    assert store.neighbors("NO_SUCH_ENTITY", None, 10) == []


def test_relations_is_sorted_closed_vocabulary(store):
    assert store.relations() == [
        "associated_with",
        "contraindicated_for",
        "has_phenotype",
        "indicated_for",
        "interacts_with",
        "member_of",
        "subtype_of",
        "targets",
    ]


def test_store_rejects_duplicate_edge_ids():
    edge = EdgeEvidence(id="E:000001", subject=A, relation="r", object=D)
    with pytest.raises(ValueError, match="duplicate edge ID"):
        InMemoryEvidenceStore([edge, edge])


def test_store_rejects_conflicting_entities():
    other_a = Entity(id="GENE_A", name="Not Gene Alpha", type="gene")
    e1 = EdgeEvidence(id="E:000001", subject=A, relation="r", object=D)
    e2 = EdgeEvidence(id="E:000002", subject=other_a, relation="r", object=D)
    with pytest.raises(ValueError, match="conflicting entities"):
        InMemoryEvidenceStore([e1, e2])


def test_from_records_rejects_dangling_entity():
    data = {
        "entities": [A.model_dump()],
        "edges": [{"id": "E:000001", "subject": "GENE_A", "relation": "r", "object": "DIS_9"}],
    }
    with pytest.raises(ValueError, match="unknown"):
        InMemoryEvidenceStore.from_records(data)


# --- directed edges ------------------------------------------------------------


def test_from_records_reads_directed_defaulting_to_false(store):
    assert store.get("E:000013").directed is True  # DIS_3 subtype_of DIS_G
    # Every other fixture edge omits the key, so it defaults to undirected.
    ids = [f"E:{n:06d}" for n in range(1, 14)]
    assert [eid for eid in ids if store.get(eid).directed] == ["E:000013"]
    data = {
        "entities": [A.model_dump(), D.model_dump()],
        "edges": [
            {"id": "E:000001", "subject": "GENE_A", "relation": "r", "object": "DIS_1"},
            {"id": "E:000002", "subject": "GENE_A", "relation": "r2", "object": "DIS_1",
             "directed": False},
        ],
    }
    s = InMemoryEvidenceStore.from_records(data)
    assert s.get("E:000001").directed is False
    assert s.get("E:000002").directed is False


def test_payload_round_trip_keeps_directed(store):
    edges = [store.get("E:000013"), store.get("E:000001")]
    reloaded = ToolResultPayload.loads(ToolResultPayload(evidence=edges).dumps())
    assert [e.directed for e in reloaded.evidence] == [True, False]
    assert reloaded.evidence == edges


def test_payload_without_directed_key_parses_as_undirected():
    # Payloads written before the flag existed still parse under the strict loader.
    raw = (
        '{"evidence": [{"id": "E:000001", "kind": "edge", '
        '"subject": {"id": "GENE_A", "name": "Gene Alpha", "type": "gene", "source": null}, '
        '"relation": "associated_with", "display_relation": null, '
        '"object": {"id": "DIS_1", "name": "Disease One", "type": "disease", "source": null}}], '
        '"entities": [], "truncated": false, "note": null}'
    )
    assert ToolResultPayload.loads(raw).evidence[0].directed is False


def test_directed_must_be_a_real_bool_in_payloads():
    edge = EdgeEvidence(id="E:000001", subject=A, relation="r", object=D, directed=True)
    raw = ToolResultPayload(evidence=[edge]).dumps().replace('"directed":true', '"directed":"true"')
    with pytest.raises(ValidationError):
        ToolResultPayload.loads(raw)
