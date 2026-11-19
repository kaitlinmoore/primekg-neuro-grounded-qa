"""Simple whole-policy baselines scored over the fixture items.

These aren't the reward-hacking audit's exploit policies; they pin down the reward's
floor and ceiling on the fixture type mix so a later change to the reward that moves
them shows up here. The always-abstain numbers are recorded in docs/reward_spec.md.
"""

from __future__ import annotations

import pytest

from neuro_grounded_qa.contracts import parse_output
from neuro_grounded_qa.reward import RewardParams, score

from .helpers import ITEMS, abstain, answer, claim, evidence

ALL = evidence(*(f"E:{i:06d}" for i in range(1, 13)))

# The design brief's target type mix (docs/question_set_design.md, "Sizing"). The
# false-premise share is split evenly between `no` and `correctable` here; the real
# split depends on the question set.
TARGET_MIX = {
    "fx-single-001": 0.30,
    "fx-multi-001": 0.25,
    "fx-partial-001": 0.15,
    "fx-unans-001": 0.20,
    "fx-falsep-001": 0.05,
    "fx-falsep-002": 0.05,
}


def always_abstain(params=RewardParams()) -> dict[str, float]:
    return {
        item_id: score(item, parse_output(abstain()), ALL, params).reward
        for item_id, item in ITEMS.items()
    }


def test_always_abstain_per_item():
    assert always_abstain() == {
        "fx-single-001": 0.0,
        "fx-multi-001": 0.0,
        "fx-partial-001": 0.0,
        "fx-unans-001": 1.0,
        "fx-falsep-001": 1.0,
        "fx-falsep-002": 1.0,
    }


def test_always_abstain_mean_over_fixture_items():
    # One fixture item per type, with two false-premise items: 3 of 6 reward abstaining.
    rewards = always_abstain()
    assert sum(rewards.values()) / len(rewards) == pytest.approx(0.5)


def test_always_abstain_over_target_mix():
    assert sum(TARGET_MIX.values()) == pytest.approx(1.0)
    rewards = always_abstain()
    assert sum(TARGET_MIX[i] * r for i, r in rewards.items()) == pytest.approx(0.30)


def test_always_abstain_over_target_mix_with_lower_correctable_credit():
    rewards = always_abstain(RewardParams(correctable_abstain_credit=0.5))
    assert sum(TARGET_MIX[i] * r for i, r in rewards.items()) == pytest.approx(0.275)


def _honest_reply(item) -> str:
    """Abstain where the item says to; otherwise claim every gold point, citing its
    evidence, and flag the rest on partial items."""
    if item.answerable == "no":
        return abstain()
    claims = [
        claim(gp.subject_id, gp.relation, gp.object_id, *gp.evidence) for gp in item.gold_points
    ]
    flags = ["the rest isn't in the graph"] if item.answerable == "partial" else []
    return answer(*claims, unsupported=flags)


def test_honest_oracle_scores_one_everywhere():
    for item in ITEMS.values():
        assert score(item, parse_output(_honest_reply(item)), ALL).reward == 1.0, item.id


def test_honest_oracle_beats_always_abstain_over_target_mix():
    honest = sum(
        TARGET_MIX[i] * score(item, parse_output(_honest_reply(item)), ALL).reward
        for i, item in ITEMS.items()
    )
    abstaining = sum(TARGET_MIX[i] * r for i, r in always_abstain().items())
    assert honest == pytest.approx(1.0)
    assert abstaining == pytest.approx(0.30)
