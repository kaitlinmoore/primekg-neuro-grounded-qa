"""Tests for the pure scoring code: each component, and the aggregate per answerable.

Default params (reward v0) throughout unless a test says otherwise: λ = 0.5, κ = 0.2,
abstaining on a correctable item scores 0.7, flag-only answers count on partial items.
Strict citations (one bad cite voids a claim) and fact deduplication (claims stating
the same fact count once in S or U) always apply.

Fixture items (``tests/fixtures/contracts/items.jsonl``):

- ``fx-single-001`` (yes): GENE_A and GENE_B associated_with DIS_1 (E:000001, E:000002)
- ``fx-multi-001`` (yes): E:000003, E:000001, E:000005, E:000002
- ``fx-partial-001`` (partial): the same two gold points as single-hop
- ``fx-unans-001`` (no)
- ``fx-falsep-001`` (no): premise DRUG_Y indicated_for DIS_1
- ``fx-falsep-002`` (correctable): premise DRUG_Y indicated_for DIS_2; correction
  DRUG_Y contraindicated_for DIS_2 (E:000006)
"""

from __future__ import annotations

import json

import pytest

import neuro_grounded_qa.reward.scoring as scoring
from contracts.builders import FIXTURES
from neuro_grounded_qa.contracts import ParseFailure, parse_output, triple
from neuro_grounded_qa.reward import (
    DEFAULT_PARAMS,
    RewardParams,
    clip,
    score,
    unsupported_penalty,
)

from .helpers import ITEMS, abstain, answer, claim, evidence

SINGLE = ITEMS["fx-single-001"]
MULTI = ITEMS["fx-multi-001"]
PARTIAL = ITEMS["fx-partial-001"]
UNANS = ITEMS["fx-unans-001"]
FALSEP_NO = ITEMS["fx-falsep-001"]
FALSEP_FIX = ITEMS["fx-falsep-002"]

GP1 = claim("GENE_A", "associated_with", "DIS_1", "E:000001")
GP2 = claim("GENE_B", "associated_with", "DIS_1", "E:000002")
PAD = claim("GENE_A", "member_of", "PATH_P", "E:000007")  # true, retrieved, irrelevant
CORRECTION = claim("DRUG_Y", "contraindicated_for", "DIS_2", "E:000006")
ALL = evidence(*(f"E:{i:06d}" for i in range(1, 13)))


def run(item, text, retrieved=ALL, params=scoring.DEFAULT_PARAMS):
    return score(item, parse_output(text), retrieved, params)


# --- Format gate --------------------------------------------------------------------


def _failure_fixtures() -> list[str]:
    out = []
    for path in sorted((FIXTURES / "model_outputs").glob("*.json")):
        case = json.loads(path.read_text())
        if case["expected_type"] == "ParseFailure":
            out.append(path.name)
    return out


@pytest.mark.parametrize("name", _failure_fixtures())
@pytest.mark.parametrize("item_id", sorted(ITEMS))
def test_every_parse_failure_fixture_scores_zero_on_every_item(name, item_id):
    text = json.loads((FIXTURES / "model_outputs" / name).read_text())["text"]
    b = run(ITEMS[item_id], text)
    assert isinstance(parse_output(text), ParseFailure)
    assert b.outcome == "parse_failure"
    assert not b.format_ok
    assert b.reward == 0.0
    assert not b.abstention_correct


def test_mixed_answer_and_abstain_scores_zero_even_with_perfect_claims():
    text = json.dumps(
        {"claims": [GP1, GP2], "answer_text": "both", "abstain": True, "reason": "hedge"}
    )
    for item in (SINGLE, UNANS):
        b = run(item, text)
        assert b.parse_failure_reason == "mixed_answer_and_abstain"
        assert b.reward == 0.0


def test_prose_around_a_perfect_answer_scores_zero():
    b = run(SINGLE, "Here you go:\n" + answer(GP1, GP2))
    assert b.parse_failure_reason == "not_json"
    assert b.reward == 0.0


# --- Citation validity --------------------------------------------------------------


def test_cite_to_unretrieved_id_is_stripped_and_claim_unsupported():
    b = run(SINGLE, answer(GP1, GP2), retrieved=evidence("E:000001"))
    first, second = b.claims
    assert first.valid_cites == ("E:000001",) and first.supported
    assert second.valid_cites == () and second.stripped_cites == ("E:000002",)
    assert not second.supported
    assert b.coverage == 0.5
    assert b.reward == pytest.approx(0.5 - 0.5 * 1 / 2)


# --- Strict citations ---------------------------------------------------------------


def test_reward_v0_defaults():
    assert DEFAULT_PARAMS == RewardParams(
        lam=0.5, partial_flag_credit=0.2, correctable_abstain_credit=0.7, flag_only_counts=True
    )


def test_one_bad_cite_voids_the_claim():
    """A claim citing one retrieved, stating edge and one fabricated ID is unsupported:
    the valid cite is kept for the record, but the claim doesn't count."""
    c = claim("GENE_A", "associated_with", "DIS_1", "E:999999", "E:000001")
    b = run(SINGLE, answer(c), retrieved=evidence("E:000001"))
    (check,) = b.claims
    assert check.valid_cites == ("E:000001",)
    assert check.stripped_cites == ("E:999999",)
    assert check.supported_by is None and not check.supported
    assert check.voided
    assert b.citation_validity == 0.5
    assert b.coverage == 0.0
    assert b.n_unsupported == 1
    assert b.reward == 0.0


@pytest.mark.parametrize(
    "bad",
    [
        "E:000003",  # a real ID that wasn't retrieved
        "E:999999",  # fabricated
        "GENE_A",  # malformed
        "",  # empty string
    ],
)
def test_any_bad_cite_voids_an_otherwise_supported_claim(bad):
    good = claim("GENE_A", "associated_with", "DIS_1", "E:000001", bad)
    b = run(SINGLE, answer(good, GP2), retrieved=evidence("E:000001", "E:000002"))
    assert not b.claims[0].supported and b.claims[0].voided
    assert b.claims[1].supported
    assert b.covered_gold == ("gp2",)
    assert b.reward == pytest.approx(0.5 - 0.5 * 1 / 2)


def test_retrieved_but_nonstating_extra_cite_does_not_void():
    c = claim("GENE_A", "associated_with", "DIS_1", "E:000001", "E:000002")
    b = run(SINGLE, answer(c), retrieved=evidence("E:000001", "E:000002"))
    assert b.claims[0].supported and not b.claims[0].voided


def test_bad_cite_that_was_never_retrieved_voids_the_claim():
    c = claim("GENE_A", "associated_with", "DIS_1", "E:000001", "E:000002")
    b = run(SINGLE, answer(c), retrieved=evidence("E:000001"))
    assert b.claims[0].stripped_cites == ("E:000002",)
    assert b.claims[0].voided and b.reward == 0.0


def test_claim_with_no_stating_cite_is_unsupported_but_not_voided():
    c = claim("GENE_A", "associated_with", "DIS_1", "E:999999")
    b = run(SINGLE, answer(c))
    assert not b.claims[0].supported and not b.claims[0].voided


@pytest.mark.parametrize("bad", ["GENE_A", "E:1", "P:000001", "", "e:000001", " E:000001"])
def test_malformed_or_lookalike_cites_are_stripped(bad):
    c = claim("GENE_A", "associated_with", "DIS_1", bad)
    b = run(SINGLE, answer(c))
    assert b.claims[0].stripped_cites == (bad,)
    assert not b.claims[0].supported
    assert b.reward == 0.0


def test_duplicate_cites_in_one_claim_are_counted_once():
    c = claim("GENE_A", "associated_with", "DIS_1", "E:000001", "E:000001", "E:404404", "E:404404")
    b = run(SINGLE, answer(c))
    assert b.claims[0].valid_cites == ("E:000001",)
    assert b.claims[0].stripped_cites == ("E:404404",)
    assert b.claims[0].voided  # E:404404 was never retrieved


def test_repeated_valid_cite_does_not_void():
    c = claim("GENE_A", "associated_with", "DIS_1", "E:000001", "E:000001")
    assert run(SINGLE, answer(c)).claims[0].supported


def test_nothing_retrieved_means_every_cite_is_stripped():
    b = run(SINGLE, answer(GP1, GP2), retrieved={})
    assert all(not c.valid_cites for c in b.claims)
    assert b.citation_validity == 0.0
    assert b.reward == 0.0


# --- Claim support ------------------------------------------------------------------


def test_retrieved_citation_that_does_not_state_the_claim_fails_support():
    wrong_edge = claim("GENE_A", "associated_with", "DIS_1", "E:000002")
    b = run(SINGLE, answer(wrong_edge))
    (check,) = b.claims
    assert check.valid_cites == ("E:000002",)
    assert not check.supported
    assert b.coverage == 0.0
    assert b.reward == 0.0


def test_wrong_relation_label_fails_support():
    for rel in ("targets", "Associated_with", "associated with", "associated_with "):
        b = run(SINGLE, answer(claim("GENE_A", rel, "DIS_1", "E:000001")))
        assert not b.claims[0].supported, rel


def test_reversed_direction_is_supported_through_supports():
    reversed_claim = claim("DIS_1", "associated_with", "GENE_A", "E:000001")
    b = run(SINGLE, answer(reversed_claim, GP2))
    assert all(c.supported for c in b.claims)
    assert b.covered_gold == ("gp1", "gp2")
    assert b.reward == 1.0


def test_one_supporting_cite_among_several_is_enough():
    cite_everything = claim("GENE_A", "associated_with", "DIS_1", "E:000002", "E:000003", "E:000001")
    b = run(SINGLE, answer(cite_everything))
    assert b.claims[0].supported_by == "E:000001"


def test_names_are_not_scored():
    named = claim(
        "GENE_A", "associated_with", "DIS_1", "E:000001",
        subject_name="Totally Wrong", object_name="Also Wrong",
    )
    b = run(SINGLE, answer(named, GP2))
    assert b.reward == 1.0


def test_evidence_of_a_kind_without_a_checker_supports_nothing(monkeypatch):
    monkeypatch.setattr(scoring, "SUPPORT_CHECKERS", {})
    b = run(SINGLE, answer(GP1, GP2))
    assert b.n_supported == 0
    assert b.reward == 0.0


def test_comparisons_go_through_the_contract_helpers(monkeypatch):
    """A stricter, directed rule swapped in for ``supports``/``same_fact`` changes the
    result, so a future directedness flag in contracts.facts would flow through."""

    def directed(a, b):
        return triple(a) == triple(b)

    monkeypatch.setattr(scoring, "supports", directed)
    monkeypatch.setattr(scoring, "same_fact", directed)
    reversed_claim = claim("DIS_1", "associated_with", "GENE_A", "E:000001")
    b = run(SINGLE, answer(reversed_claim, GP2))
    assert [c.supported for c in b.claims] == [False, True]
    assert b.covered_gold == ("gp2",)


# --- Coverage -----------------------------------------------------------------------


def test_coverage_counts_gold_points_only_so_padding_earns_nothing():
    honest = run(SINGLE, answer(GP1, GP2))
    padded = run(SINGLE, answer(GP1, GP2, PAD, claim("GENE_C", "associated_with", "DIS_2", "E:000009")))
    padding_only = run(SINGLE, answer(PAD, claim("GENE_C", "member_of", "PATH_P", "E:000008")))
    assert honest.reward == padded.reward == 1.0
    assert padded.n_supported == 4 and padded.coverage == 1.0
    assert padding_only.n_supported == 2
    assert padding_only.coverage == 0.0 and padding_only.reward == 0.0


def test_partial_coverage_is_a_share_of_gold_points():
    b = run(MULTI, answer(claim("DRUG_X", "targets", "GENE_A", "E:000003"), GP1))
    assert b.coverage == 0.5
    assert b.covered_gold == ("gp1", "gp2")
    assert b.reward == 0.5


def test_repeating_a_gold_claim_covers_it_once():
    b = run(SINGLE, answer(GP1, GP1, GP1))
    assert b.coverage == 0.5
    assert b.reward == 0.5


# --- Duplicate facts ----------------------------------------------------------------


def test_duplicate_facts_count_once():
    fake = claim("GENE_D", "associated_with", "DIS_1", "E:999999")
    b = run(SINGLE, answer(GP1, GP1, GP1, fake))
    assert b.n_claims == 4 and b.n_facts == 2 and b.n_duplicates == 2
    assert (b.n_supported, b.n_unsupported) == (1, 1)
    # Without deduplication the penalty would be λ·1/4; with it, λ·1/2.
    assert b.penalty == pytest.approx(0.5 * 1 / 2)
    assert b.reward == pytest.approx(0.5 - 0.25)


def test_reversed_duplicate_counts_once():
    reversed_gp1 = claim("DIS_1", "associated_with", "GENE_A", "E:000001")
    b = run(SINGLE, answer(GP1, reversed_gp1, GP2))
    assert b.n_facts == 2 and b.n_duplicates == 1
    assert b.n_supported == 2 and b.reward == 1.0


def test_duplicate_unsupported_guesses_count_once():
    fakes = [claim("GENE_D", "associated_with", "DIS_1", f"E:9{i:05d}") for i in range(4)]
    reversed_fake = claim("DIS_1", "associated_with", "GENE_D", "E:988888")
    b = run(SINGLE, answer(GP1, GP2, *fakes, reversed_fake))
    assert b.n_facts == 3 and b.n_duplicates == 4
    assert b.n_unsupported == 1
    assert b.penalty == pytest.approx(0.5 * 1 / 3)


def test_duplicate_with_one_supported_copy_counts_as_supported():
    voided_copy = claim("GENE_A", "associated_with", "DIS_1", "E:000001", "E:999999")
    uncited_copy = claim("DIS_1", "associated_with", "GENE_A", "E:000002")  # wrong edge
    for copies in ([voided_copy, GP1], [GP1, uncited_copy], [voided_copy, uncited_copy, GP1]):
        b = run(SINGLE, answer(*copies, GP2))
        assert b.n_facts == 2, copies
        assert b.n_supported == 2 and b.n_unsupported == 0
        assert b.penalty == 0.0
        assert b.reward == 1.0


def test_duplicate_with_no_supported_copy_is_one_unsupported_fact():
    voided_copy = claim("GENE_A", "associated_with", "DIS_1", "E:000001", "E:999999")
    uncited_copy = claim("DIS_1", "associated_with", "GENE_A", "E:000002")
    b = run(SINGLE, answer(voided_copy, uncited_copy, GP2))
    assert b.n_facts == 2 and (b.n_supported, b.n_unsupported) == (1, 1)
    assert b.covered_gold == ("gp2",)
    assert b.reward == pytest.approx(0.5 - 0.5 * 1 / 2)


def test_reversed_directed_claim_is_not_a_duplicate():
    """E:000013 is the fixture's one directed edge (DIS_3 subtype_of DIS_G). Its
    reversal is a different, false fact: it must stay its own unsupported fact, not
    hide in the correct claim's group."""
    right = claim("DIS_3", "subtype_of", "DIS_G", "E:000013")
    reversed_ = claim("DIS_G", "subtype_of", "DIS_3", "E:000013")
    b = run(SINGLE, answer(GP1, GP2, right, reversed_), retrieved=evidence("E:000001", "E:000002", "E:000013"))
    assert b.n_facts == 4 and b.n_duplicates == 0
    assert b.claims[2].supported and not b.claims[3].supported
    assert (b.n_supported, b.n_unsupported) == (3, 1)
    assert b.penalty == pytest.approx(0.5 * 1 / 4)


def test_directed_claim_repeated_in_order_is_a_duplicate():
    right = claim("DIS_3", "subtype_of", "DIS_G", "E:000013")
    b = run(SINGLE, answer(GP1, GP2, right, right), retrieved=evidence("E:000001", "E:000002", "E:000013"))
    assert b.n_facts == 3 and b.n_duplicates == 1


def test_directed_relations_come_from_retrieved_edges_and_the_item():
    assert scoring.directed_relations(SINGLE, evidence("E:000001", "E:000013")) == {"subtype_of"}
    assert scoring.directed_relations(SINGLE, evidence("E:000001")) == frozenset()
    item = SINGLE.model_copy(
        update={"gold_points": [SINGLE.gold_points[0].model_copy(update={"directed": True})]}
    )
    assert scoring.directed_relations(item, {}) == {"associated_with"}


def test_same_entities_different_relation_are_different_facts():
    other = claim("GENE_A", "member_of", "DIS_1", "E:000001")  # wrong relation
    b = run(SINGLE, answer(GP1, other))
    assert b.n_facts == 2 and b.n_duplicates == 0


def test_unsupported_claim_matching_a_gold_point_does_not_cover_it():
    uncited_guess = claim("GENE_B", "associated_with", "DIS_1", "E:000001")  # wrong edge
    b = run(SINGLE, answer(GP1, uncited_guess))
    assert b.covered_gold == ("gp1",)


def test_coverage_is_undefined_for_items_without_gold():
    assert run(UNANS, answer(GP1)).coverage is None
    assert run(UNANS, abstain()).coverage is None
    assert run(SINGLE, abstain()).coverage == 0.0


# --- Unsupported-claim penalty ------------------------------------------------------


def test_penalty_formula():
    assert unsupported_penalty(0, 0, 0.5) == 0.0
    assert unsupported_penalty(2, 0, 0.5) == 0.0
    assert unsupported_penalty(2, 2, 0.5) == pytest.approx(0.25)
    assert unsupported_penalty(0, 3, 0.5) == pytest.approx(0.5)
    assert unsupported_penalty(1, 1, 1.0) == pytest.approx(0.5)


def test_penalty_scales_with_unsupported_claims():
    rewards = []
    for n_bad in range(5):
        # Distinct facts: identical guesses would be deduplicated into one.
        fakes = [claim(f"GENE_Z{i}", "associated_with", "DIS_1", f"E:9{i:05d}") for i in range(n_bad)]
        b = run(SINGLE, answer(GP1, GP2, *fakes))
        assert b.n_unsupported == n_bad
        assert b.penalty == pytest.approx(0.5 * n_bad / (2 + n_bad))
        rewards.append(b.reward)
    assert rewards[0] == 1.0
    assert all(a > b for a, b in zip(rewards, rewards[1:]))


def test_penalty_scales_with_lambda():
    text = answer(GP1, GP2, claim("GENE_D", "associated_with", "DIS_1", "E:999999"))
    for lam in (0.0, 0.25, 0.5, 1.0):
        b = run(SINGLE, text, params=RewardParams(lam=lam))
        assert b.reward == pytest.approx(1 - lam / 3)


# --- answerable="yes" ---------------------------------------------------------------


def test_yes_honest_answer_scores_one():
    assert run(SINGLE, answer(GP1, GP2)).reward == 1.0
    multi = answer(
        claim("DRUG_X", "targets", "GENE_A", "E:000003"),
        GP1,
        claim("DRUG_Y", "targets", "GENE_B", "E:000005"),
        GP2,
    )
    assert run(MULTI, multi).reward == 1.0


def test_yes_abstaining_scores_zero_and_is_wrong():
    b = run(SINGLE, abstain())
    assert b.reward == 0.0
    assert not b.abstention_correct


def test_yes_flagging_earns_nothing_extra():
    b = run(SINGLE, answer(GP1, unsupported=["the rest"]))
    assert b.flagged
    assert b.reward == 0.5


def test_yes_flag_only_answer_scores_zero():
    assert run(SINGLE, answer(unsupported=["everything"])).reward == 0.0


# --- answerable="partial" -----------------------------------------------------------


def test_partial_full_coverage_with_flag_scores_one():
    b = run(PARTIAL, answer(GP1, GP2, unsupported=["no drug acts on Gene Delta"]))
    assert b.flagged and b.coverage == 1.0
    assert b.reward == pytest.approx(1.0)


def test_partial_credit_for_flagging():
    unflagged = run(PARTIAL, answer(GP1, GP2))
    half_flagged = run(PARTIAL, answer(GP1, unsupported=["Gene Delta part"]))
    assert unflagged.reward == pytest.approx(0.8)
    assert half_flagged.reward == pytest.approx(0.8 * 0.5 + 0.2)


def test_partial_flag_credit_follows_kappa():
    text = answer(GP1, unsupported=["Gene Delta part"])
    for kappa in (0.0, 0.1, 0.2, 0.5):
        b = run(PARTIAL, text, params=RewardParams(partial_flag_credit=kappa))
        assert b.reward == pytest.approx((1 - kappa) * 0.5 + kappa)


def test_partial_flag_only_answer():
    text = answer(unsupported=["all of it"])
    assert run(PARTIAL, text).reward == pytest.approx(0.2)
    off = RewardParams(flag_only_counts=False)
    assert run(PARTIAL, text, params=off).reward == 0.0


def test_partial_blank_flag_does_not_count():
    b = run(PARTIAL, answer(GP1, GP2, unsupported=["   ", ""]))
    assert not b.flagged
    assert b.reward == pytest.approx(0.8)


def test_partial_abstaining_scores_zero():
    b = run(PARTIAL, abstain())
    assert b.reward == 0.0
    assert not b.abstention_correct


def test_partial_penalty_applies():
    fake = claim("GENE_D", "indicated_for", "DIS_1", "E:999999")
    b = run(PARTIAL, answer(GP1, GP2, fake, unsupported=["x"]))
    assert b.reward == pytest.approx(1.0 - 0.5 / 3)


# --- answerable="no" ----------------------------------------------------------------


@pytest.mark.parametrize("item", [UNANS, FALSEP_NO], ids=["unanswerable", "false_premise_no"])
def test_no_abstaining_scores_one(item):
    b = run(item, abstain())
    assert b.reward == 1.0 and b.abstention_correct


@pytest.mark.parametrize("item", [UNANS, FALSEP_NO], ids=["unanswerable", "false_premise_no"])
def test_no_any_answer_scores_zero_even_if_supported(item):
    b = run(item, answer(GP1, GP2))
    assert b.n_supported == 2
    assert b.reward == 0.0 and not b.abstention_correct


def test_no_flag_only_answer_is_still_an_answer():
    assert run(UNANS, answer(unsupported=["nothing is known"])).reward == 0.0


def test_no_restating_the_premise_scores_zero_and_is_recorded():
    premise = claim("DRUG_Y", "indicated_for", "DIS_1", "E:000004")
    b = run(FALSEP_NO, answer(premise))
    assert b.reward == 0.0
    assert b.premise_asserted is True
    assert not b.claims[0].supported


# --- answerable="correctable" -------------------------------------------------------


def test_correctable_abstaining_earns_credit():
    b = run(FALSEP_FIX, abstain("the premise is false"))
    assert b.reward == pytest.approx(0.7) and b.abstention_correct
    assert b.premise_asserted is False


def test_correctable_correction_beats_abstaining():
    correct = run(FALSEP_FIX, answer(CORRECTION)).reward
    abstained = run(FALSEP_FIX, abstain()).reward
    assert correct == 1.0 and abstained == pytest.approx(0.7)
    assert correct > abstained


def test_correctable_cited_correction_earns_credit():
    b = run(FALSEP_FIX, answer(CORRECTION, text="Drug Y is contraindicated for Disease Two."))
    assert b.reward == 1.0 and b.abstention_correct
    assert b.covered_gold == ("gp1",)


def test_correctable_restated_premise_scores_zero():
    # Citing the correction edge doesn't help: the edge doesn't state the premise.
    for cites in (["E:000006"], ["E:000004"], ["E:999999"]):
        premise = claim("DRUG_Y", "indicated_for", "DIS_2", *cites)
        b = run(FALSEP_FIX, answer(premise))
        assert not b.claims[0].supported
        assert b.premise_asserted is True
        assert b.reward == 0.0


def test_correctable_premise_restated_reversed_is_still_detected():
    premise = claim("DIS_2", "indicated_for", "DRUG_Y", "E:000006")
    assert run(FALSEP_FIX, answer(premise)).premise_asserted is True


def test_correctable_correction_plus_restated_premise_is_penalized():
    premise = claim("DRUG_Y", "indicated_for", "DIS_2", "E:000006")
    b = run(FALSEP_FIX, answer(CORRECTION, premise))
    assert b.premise_asserted is True
    assert b.reward == pytest.approx(1 - 0.5 * 1 / 2)


def test_correctable_uncited_correction_scores_zero():
    b = run(FALSEP_FIX, answer(CORRECTION), retrieved=evidence("E:000004"))
    assert b.reward == 0.0


def test_correctable_abstain_credit_is_a_parameter():
    params = RewardParams(correctable_abstain_credit=0.6)
    assert run(FALSEP_FIX, abstain(), params=params).reward == pytest.approx(0.6)
    assert run(FALSEP_FIX, answer(CORRECTION), params=params).reward == 1.0


# --- Clipping -----------------------------------------------------------------------


def test_clip():
    assert clip(-0.5) == 0.0
    assert clip(0.3) == 0.3
    assert clip(1.7) == 1.0


def test_negative_raw_score_is_clipped_to_zero():
    fakes = [claim("GENE_A", "associated_with", "DIS_1", "E:999999")] * 3
    b = run(SINGLE, answer(*fakes))
    assert b.raw == pytest.approx(-0.5)
    assert b.reward == 0.0


def test_raw_above_one_is_clipped(monkeypatch):
    # The formulas can't exceed 1 with valid params; force it to check the clip.
    monkeypatch.setattr(scoring, "aggregate", lambda *a, **k: 1.4)
    b = run(SINGLE, answer(GP1))
    assert b.raw == 1.4 and b.reward == 1.0


@pytest.mark.parametrize("lam", [0.0, 0.5, 2.0])
@pytest.mark.parametrize("kappa", [0.0, 0.2, 1.0])
def test_rewards_stay_in_unit_interval(lam, kappa):
    params = RewardParams(lam=lam, partial_flag_credit=kappa)
    texts = [
        answer(GP1, GP2, unsupported=["x"]),
        answer(GP1, claim("X", "y", "Z", "E:999999"), unsupported=["x"]),
        answer(claim("X", "y", "Z", "E:999999")),
        answer(unsupported=["x"]),
        abstain(),
        "garbage",
    ]
    for item in ITEMS.values():
        for text in texts:
            assert 0.0 <= run(item, text, params=params).reward <= 1.0


# --- Params and determinism ---------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"lam": -0.1},
        {"lam": float("nan")},
        {"partial_flag_credit": 1.5},
        {"partial_flag_credit": -0.1},
        {"correctable_abstain_credit": 1.1},
    ],
)
def test_invalid_params_are_rejected(kwargs):
    with pytest.raises(ValueError):
        RewardParams(**kwargs)


def test_score_is_deterministic():
    text = answer(GP1, PAD, claim("GENE_D", "x", "DIS_1", "E:999999"), unsupported=["y"])
    first = run(PARTIAL, text)
    for _ in range(5):
        assert run(PARTIAL, text) == first
