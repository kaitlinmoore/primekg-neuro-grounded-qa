"""Tests for the pure scoring code: each component, and the aggregate per answerable.

Default params throughout unless a test says otherwise: λ = 0.5, κ = 0.2, abstaining
on a correctable item scores 1, flag-only answers count on partial items.

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
from neuro_grounded_qa.reward import RewardParams, clip, score, unsupported_penalty

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


def test_stripping_is_per_cite_not_per_claim():
    c = claim("GENE_A", "associated_with", "DIS_1", "E:999999", "E:000001")
    b = run(SINGLE, answer(c), retrieved=evidence("E:000001"))
    (check,) = b.claims
    assert check.valid_cites == ("E:000001",)
    assert check.stripped_cites == ("E:999999",)
    assert check.supported_by == "E:000001"
    assert b.citation_validity == 0.5


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
        fakes = [claim("GENE_D", "associated_with", "DIS_1", f"E:9{i:05d}") for i in range(n_bad)]
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
    assert b.reward == 1.0 and b.abstention_correct
    assert b.premise_asserted is False


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
