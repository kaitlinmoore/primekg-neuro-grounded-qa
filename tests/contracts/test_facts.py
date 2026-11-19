"""Contract tests for the shared fact comparison: edges are undirected facts."""

import pytest

from contracts.builders import fixture_store
from neuro_grounded_qa.contracts import (
    Claim,
    GoldPoint,
    PremiseTriple,
    same_fact,
    supports,
    triple,
)


def claim(s, r, o):
    return Claim(subject_id=s, relation=r, object_id=o, cites=["E:000001"])


@pytest.fixture
def edge():
    return fixture_store().get("E:000001")  # GENE_A associated_with DIS_1


def test_triple_reads_edges_and_triple_models(edge):
    assert triple(edge) == ("GENE_A", "associated_with", "DIS_1")
    assert triple(claim("A", "r", "B")) == ("A", "r", "B")


def test_forward_claim_is_supported(edge):
    assert supports(claim("GENE_A", "associated_with", "DIS_1"), edge)


def test_reversed_claim_is_supported(edge):
    assert supports(claim("DIS_1", "associated_with", "GENE_A"), edge)


@pytest.mark.parametrize("relation", ["targets", "Associated_with", "associated with", ""])
def test_wrong_relation_is_not_supported(edge, relation):
    c = Claim.model_construct(
        subject_id="GENE_A", relation=relation, object_id="DIS_1", cites=["E:000001"]
    )
    assert not supports(c, edge)


@pytest.mark.parametrize(
    "s, o",
    [("GENE_B", "DIS_1"), ("GENE_A", "DIS_2"), ("GENE_A", "GENE_A"), ("DIS_1", "DIS_1")],
)
def test_wrong_entity_is_not_supported(edge, s, o):
    assert not supports(claim(s, "associated_with", o), edge)


def test_same_fact_across_model_types():
    gp = GoldPoint(
        id="gp1", subject_id="DRUG_X", relation="targets", object_id="GENE_A",
        evidence=["E:000003"],
    )
    premise = PremiseTriple(subject_id="GENE_A", relation="targets", object_id="DRUG_X")
    assert same_fact(gp, premise)
    assert same_fact(gp, fixture_store().get("E:000003"))
    assert same_fact(claim("GENE_A", "targets", "DRUG_X"), gp)
    assert not same_fact(gp, claim("DRUG_X", "indicated_for", "GENE_A"))


def test_same_fact_is_symmetric(edge):
    c = claim("DIS_1", "associated_with", "GENE_A")
    assert same_fact(c, edge) == same_fact(edge, c) is True
