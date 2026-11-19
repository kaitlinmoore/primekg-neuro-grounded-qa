"""Contract tests for the shared fact comparison: facts match in either direction,
except in order when a side is flagged ``directed``."""

import pytest

from contracts.builders import fixture_store
from neuro_grounded_qa.contracts import (
    Claim,
    GoldPoint,
    PremiseTriple,
    is_directed,
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


# --- directed facts ------------------------------------------------------------
# E:000013 is the fixture's one directed edge: DIS_3 subtype_of DIS_G (child, parent).

FWD = ("DIS_3", "subtype_of", "DIS_G")
REV = ("DIS_G", "subtype_of", "DIS_3")


def gold(s, r, o, directed=False):
    return GoldPoint(
        id="gp", subject_id=s, relation=r, object_id=o, evidence=["E:000013"],
        directed=directed,
    )


def premise(s, r, o, directed=False):
    return PremiseTriple(subject_id=s, relation=r, object_id=o, directed=directed)


@pytest.fixture
def directed_edge():
    edge = fixture_store().get("E:000013")
    assert edge.directed and triple(edge) == FWD
    return edge


def test_flag_defaults_to_false_and_claims_have_none():
    assert not is_directed(gold(*FWD))
    assert not is_directed(premise(*FWD))
    assert not is_directed(fixture_store().get("E:000001"))
    assert not is_directed(claim(*FWD))
    assert is_directed(gold(*FWD, directed=True))


def test_directed_forward_matches():
    assert same_fact(gold(*FWD, directed=True), premise(*FWD))
    assert same_fact(gold(*FWD, directed=True), premise(*FWD, directed=True))


def test_directed_reversed_does_not_match():
    assert not same_fact(gold(*FWD, directed=True), premise(*REV))
    assert not same_fact(premise(*REV), gold(*FWD, directed=True))


@pytest.mark.parametrize("b", [FWD, REV])
def test_undirected_matches_either_way(b):
    assert same_fact(gold(*FWD), premise(*b))
    assert same_fact(premise(*b), gold(*FWD))


def test_claim_vs_directed_gold_point_matches_in_order_only():
    gp = gold(*FWD, directed=True)
    assert same_fact(claim(*FWD), gp)
    assert not same_fact(claim(*REV), gp)
    assert not same_fact(gp, claim(*REV))  # symmetric


def test_claim_vs_undirected_gold_point_matches_either_way():
    gp = gold(*FWD)
    assert same_fact(claim(*FWD), gp) and same_fact(claim(*REV), gp)


def test_directed_edge_supports_claim_in_order_only(directed_edge):
    assert supports(claim(*FWD), directed_edge)
    assert not supports(claim(*REV), directed_edge)


def test_directed_edge_still_needs_exact_relation_and_entities(directed_edge):
    assert not supports(claim("DIS_3", "subtype_of", "DIS_1"), directed_edge)
    assert not supports(claim("DIS_3", "parent_of", "DIS_G"), directed_edge)


def test_undirected_edge_support_is_unchanged(edge):
    assert not edge.directed
    assert supports(claim("GENE_A", "associated_with", "DIS_1"), edge)
    assert supports(claim("DIS_1", "associated_with", "GENE_A"), edge)


@pytest.mark.parametrize(
    "a_directed, b_directed, reversed_matches",
    [(False, False, True), (True, False, False), (False, True, False), (True, True, False)],
)
def test_either_side_directed_forces_order(a_directed, b_directed, reversed_matches):
    a, b_fwd, b_rev = gold(*FWD, a_directed), premise(*FWD, b_directed), premise(*REV, b_directed)
    assert same_fact(a, b_fwd) and same_fact(b_fwd, a)
    assert same_fact(a, b_rev) is same_fact(b_rev, a) is reversed_matches


def test_both_sides_directed(directed_edge):
    gp = gold(*FWD, directed=True)
    assert same_fact(gp, directed_edge)
    assert not same_fact(gold(*REV, directed=True), directed_edge)


def test_directed_edge_vs_undirected_gold_point(directed_edge):
    # The edge's flag alone forces order: a reversed, unflagged gold point doesn't match.
    assert same_fact(gold(*FWD), directed_edge)
    assert not same_fact(gold(*REV), directed_edge)
