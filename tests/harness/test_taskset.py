"""The taskset wiring: items by split, the held-out guard, the toolset and state."""

from __future__ import annotations

import pytest
import verifiers.v1 as vf
from verifiers.v1.state import state_cls
from verifiers.v1.utils.loaders import taskset_class

from harness.helpers import ITEMS_PATH, SPLITS_PATH, STORE_PATH
from neuro_grounded_qa.contracts import QuestionItem
from neuro_grounded_qa.taskset import (
    NeuroGroundedQAConfig,
    NeuroGroundedQATask,
    NeuroGroundedQATaskset,
    load_items,
    render_system_prompt,
)
from neuro_grounded_qa.tools import KGState, KGToolset, KGToolsetConfig


def _taskset(**config) -> NeuroGroundedQATaskset:
    return NeuroGroundedQATaskset(NeuroGroundedQAConfig(**config))


def test_loader_resolves_the_real_taskset():
    assert taskset_class("neuro-grounded-qa") is NeuroGroundedQATaskset


def test_defaults_point_at_the_contract_fixtures(monkeypatch):
    monkeypatch.chdir(ITEMS_PATH.parents[3])  # repo root, as scripts/run.sh runs
    cfg = NeuroGroundedQAConfig()
    assert cfg.items_path.resolve() == ITEMS_PATH
    assert cfg.tools.store_path.resolve() == STORE_PATH
    assert cfg.split == "train" and cfg.allow_heldout is False


def test_loads_fixture_items_as_tasks():
    tasks = list(_taskset(items_path=ITEMS_PATH))
    expected = [line for line in ITEMS_PATH.read_text().splitlines() if line.strip()]
    assert len(tasks) == len(expected) == 6
    assert all(isinstance(t, NeuroGroundedQATask) for t in tasks)
    assert all(isinstance(t.data, QuestionItem) for t in tasks)
    assert [t.data.idx for t in tasks] == list(range(6))
    assert tasks[0].data.id == "fx-single-001"


def test_tasks_carry_the_system_prompt():
    cfg = NeuroGroundedQAConfig(items_path=ITEMS_PATH)
    prompt = render_system_prompt(cfg.tools)
    assert {t.data.system_prompt for t in NeuroGroundedQATaskset(cfg)} == {prompt}


def test_filters_by_split():
    train = [t.data.id for t in _taskset(items_path=SPLITS_PATH)]
    assert train == ["hx-train-001", "hx-train-002"]


def test_other_splits_are_skipped_without_validation():
    # The file holds a deliberately invalid held-out line; loading train must not
    # touch it (and so can never print it in an error).
    assert len(load_items(SPLITS_PATH, "train")) == 2


def test_heldout_refused_by_default():
    with pytest.raises(ValueError, match="heldout"):
        NeuroGroundedQAConfig(items_path=SPLITS_PATH, split="heldout")
    with pytest.raises(ValueError, match="allow_heldout"):
        load_items(SPLITS_PATH, "heldout")


def test_heldout_refused_even_if_config_validation_is_bypassed():
    # model_copy(update=...) skips validators, so the config check doesn't run.
    cfg = NeuroGroundedQAConfig(items_path=SPLITS_PATH).model_copy(update={"split": "heldout"})
    with pytest.raises(ValueError, match="heldout"):
        list(NeuroGroundedQATaskset(cfg))


def test_heldout_loads_only_with_explicit_flag(tmp_path):
    valid_only = tmp_path / "items.jsonl"
    lines = SPLITS_PATH.read_text().splitlines()
    valid_only.write_text("\n".join(lines[:2]) + "\n")
    cfg = NeuroGroundedQAConfig(items_path=valid_only, split="heldout", allow_heldout=True)
    assert [t.data.id for t in NeuroGroundedQATaskset(cfg)] == ["hx-heldout-001"]


def test_empty_split_is_an_error(tmp_path):
    only_train = tmp_path / "items.jsonl"
    only_train.write_text(SPLITS_PATH.read_text().splitlines()[0] + "\n")
    with pytest.raises(ValueError, match="no items"):
        load_items(only_train, "heldout", allow_heldout=True)


def test_line_without_split_is_an_error(tmp_path):
    bad = tmp_path / "items.jsonl"
    bad.write_text('{"id": "x"}\n')
    with pytest.raises(ValueError, match="split"):
        load_items(bad, "train")


def test_toolsets_attaches_the_kg_toolset_with_an_absolute_store_path(tmp_path, monkeypatch):
    monkeypatch.chdir(STORE_PATH.parent)
    cfg = NeuroGroundedQAConfig(tools=KGToolsetConfig(store_path="mini_store.json", max_calls=3))
    [toolset] = NeuroGroundedQATaskset.toolsets(cfg)
    assert isinstance(toolset, KGToolset)
    assert toolset.server_name == "kg"
    assert toolset.config.store_path == STORE_PATH  # resolved in the host process
    assert toolset.config.max_calls == 3
    assert cfg.tools.store_path.is_absolute() is False  # the config itself is untouched


def test_task_and_toolset_share_the_state_type():
    # The host validates tool-server state pushes against the task's state type;
    # a mismatch would silently drop the call counter.
    assert state_cls(NeuroGroundedQATask) is KGState
    assert state_cls(KGToolset) is KGState
    assert "kg_calls" in KGState.model_fields


def test_task_has_no_reward_yet():
    # The reward adapter is wired in a later step; until then nothing scores.
    [task] = list(_taskset(items_path=ITEMS_PATH).take(1))
    assert task.hooks("reward") == []
    assert isinstance(task, vf.Task)
