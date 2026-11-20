"""The taskset wiring: items by split, the held-out guard, the toolset and state, the
reward, and run provenance in ``trace.info``."""

from __future__ import annotations

import asyncio
import hashlib
import json

import pytest
import verifiers.v1 as vf
from verifiers.v1.state import state_cls
from verifiers.v1.utils.loaders import taskset_class

from contracts.builders import TraceBuilder, call, edge_payload
from harness.helpers import ITEMS_PATH, SPLITS_PATH, STORE_PATH
from neuro_grounded_qa.contracts import QuestionItem
from neuro_grounded_qa.reward import DEFAULT_PARAMS, GroundingRewardMixin, RewardParams
from neuro_grounded_qa.taskset import (
    NeuroGroundedQAConfig,
    NeuroGroundedQATask,
    NeuroGroundedQATaskset,
    file_sha256,
    load_items,
    render_system_prompt,
    store_relations,
)
from neuro_grounded_qa.tools import KGState, KGToolset, KGToolsetConfig, read_build_id


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
    prompt = render_system_prompt(cfg.tools, store_relations(STORE_PATH))
    assert {t.data.system_prompt for t in NeuroGroundedQATaskset(cfg)} == {prompt}


def test_prompt_labels_come_from_the_configured_store(tmp_path):
    """Point the taskset at a different store and the prompt's label line follows it."""
    records = json.loads(STORE_PATH.read_text())
    records["edges"] = [dict(e, relation="only_label") for e in records["edges"]]
    store = tmp_path / "one_label_store.json"
    store.write_text(json.dumps(records))
    cfg = NeuroGroundedQAConfig(items_path=ITEMS_PATH, tools=KGToolsetConfig(store_path=store))
    prompts = {t.data.system_prompt for t in NeuroGroundedQATaskset(cfg)}
    assert len(prompts) == 1
    assert "Relation labels in this graph: `only_label`. Pass these" in prompts.pop()


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


# --- Build check: items and store from the same build --------------------------------


def _store_with_build_id(tmp_path, build_id: str):
    """A copy of the fixture store with its ``build_id`` set to ``build_id``."""
    data = json.loads(STORE_PATH.read_text())
    data["build_id"] = build_id
    path = tmp_path / "store.json"
    path.write_text(json.dumps(data))
    return path


def test_matching_build_ids_load():
    tasks = list(_taskset(items_path=ITEMS_PATH, tools=KGToolsetConfig(store_path=STORE_PATH)))
    assert len(tasks) == 6
    assert {t.data.build_id for t in tasks} == {read_build_id(STORE_PATH)}


def test_mismatched_store_is_refused_naming_both_ids(tmp_path):
    other = _store_with_build_id(tmp_path, "some-other-build")
    cfg = NeuroGroundedQAConfig(items_path=ITEMS_PATH, tools=KGToolsetConfig(store_path=other))
    with pytest.raises(ValueError) as err:
        list(NeuroGroundedQATaskset(cfg))
    msg = str(err.value)
    assert "'fixture-mini-store-v1'" in msg  # the items' build
    assert "'some-other-build'" in msg  # the store's build
    assert f"{ITEMS_PATH}:1:" in msg  # where: the first mismatched line


def test_one_mismatched_item_is_enough_to_refuse(tmp_path):
    lines = [line for line in ITEMS_PATH.read_text().splitlines() if line.strip()]
    stray = json.loads(lines[-1])
    stray["build_id"] = "some-other-build"
    items = tmp_path / "items.jsonl"
    items.write_text("\n".join([*lines[:-1], json.dumps(stray)]) + "\n")
    cfg = NeuroGroundedQAConfig(items_path=items, tools=KGToolsetConfig(store_path=STORE_PATH))
    with pytest.raises(ValueError, match=rf"items.jsonl:{len(lines)}: item build_id 'some-other-build'"):
        list(NeuroGroundedQATaskset(cfg))


def test_mismatch_error_carries_no_item_text(tmp_path):
    other = _store_with_build_id(tmp_path, "some-other-build")
    with pytest.raises(ValueError) as err:
        load_items(ITEMS_PATH, "train", store_build_id=read_build_id(other))
    first = json.loads(ITEMS_PATH.read_text().splitlines()[0])
    assert first["prompt"] not in str(err.value)
    assert first["id"] not in str(err.value)


def test_build_check_skips_other_splits(tmp_path):
    # Lines of other splits are never validated, so their build_id isn't read either.
    assert len(load_items(SPLITS_PATH, "train", store_build_id="fixture-mini-store-v1")) == 2
    lines = SPLITS_PATH.read_text().splitlines()
    heldout = json.loads(lines[1])
    heldout["build_id"] = "some-other-build"
    items = tmp_path / "items.jsonl"
    items.write_text("\n".join([lines[0], json.dumps(heldout), *lines[2:]]) + "\n")
    assert len(load_items(items, "train", store_build_id="fixture-mini-store-v1")) == 2


def test_load_items_without_a_store_skips_the_build_check(tmp_path):
    assert len(load_items(ITEMS_PATH, "train", store_build_id=None)) == 6


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


# --- Reward wiring --------------------------------------------------------------------


def test_task_carries_the_grounding_reward():
    [task] = list(_taskset(items_path=ITEMS_PATH).take(1))
    assert isinstance(task, vf.Task) and isinstance(task, GroundingRewardMixin)
    assert [fn.__name__ for fn in task.hooks("reward")] == ["grounding_reward"]
    assert [fn.__name__ for fn in task.hooks("metric")] == ["grounding_metrics"]
    assert task.reward_params == DEFAULT_PARAMS


def test_loaded_task_scores_a_trace():
    """The wiring end to end, with no rollout: a hand-built honest episode on the
    first fixture item scores 1.0 through ``Task.score``."""
    [task] = list(_taskset(items_path=ITEMS_PATH).take(1))
    b = TraceBuilder(data=task.data)
    b.user(task.data.prompt_text)
    for i, eid in enumerate(("E:000001", "E:000002")):
        b.assistant(None, [call(f"c{i}", "kg_get_edge", edge_id=eid)])
        b.tool(f"c{i}", edge_payload(eid), name="kg_get_edge")
    claims = [
        {"subject_id": gp.subject_id, "relation": gp.relation, "object_id": gp.object_id,
         "cites": list(gp.evidence)}
        for gp in task.data.gold_points
    ]
    b.assistant(json.dumps({"claims": claims, "answer_text": "both"}))
    trace = b.build()
    asyncio.run(task.score(trace))
    assert trace.reward == 1.0
    assert trace.metrics["coverage"] == 1.0
    assert trace.metrics["root_reply_present"] == 0.0


# --- Provenance ---------------------------------------------------------------------


def test_read_build_id_reads_the_store_file():
    assert read_build_id(STORE_PATH) == "fixture-mini-store-v1"


def test_read_build_id_defaults_like_the_in_memory_store(tmp_path):
    store = tmp_path / "store.json"
    store.write_text(json.dumps({"entities": [], "edges": []}))
    assert read_build_id(store) == "fixture"


def test_read_build_id_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_build_id(tmp_path / "missing.json")
    (tmp_path / "store.duckdb").write_bytes(b"")
    with pytest.raises(ValueError, match="unknown evidence store format"):
        read_build_id(tmp_path / "store.duckdb")


def test_file_sha256_matches_hashlib():
    assert file_sha256(ITEMS_PATH) == hashlib.sha256(ITEMS_PATH.read_bytes()).hexdigest()


def test_tasks_carry_run_provenance():
    tasks = list(_taskset(items_path=ITEMS_PATH, tools=KGToolsetConfig(store_path=STORE_PATH)))
    expected = {
        "store_build_id": "fixture-mini-store-v1",
        "items_sha256": hashlib.sha256(ITEMS_PATH.read_bytes()).hexdigest(),
    }
    assert all(t.provenance == expected for t in tasks)


def test_setup_writes_provenance_into_trace_info():
    [task] = list(
        _taskset(items_path=ITEMS_PATH, tools=KGToolsetConfig(store_path=STORE_PATH)).take(1)
    )
    trace = TraceBuilder(data=task.data).build()
    trace.info["other"] = "kept"
    asyncio.run(task.setup(trace, runtime=None))
    assert trace.info == {
        "other": "kept",
        "store_build_id": "fixture-mini-store-v1",
        "items_sha256": hashlib.sha256(ITEMS_PATH.read_bytes()).hexdigest(),
        "reward_params": {
            "lam": 0.5,
            "partial_flag_credit": 0.2,
            "correctable_abstain_credit": 0.7,
            "flag_only_counts": True,
            "mu": 1.0,
            "nongold_allowance": 3,
            "version": "v1",
        },
    }
    # trace.info is serialized with the trace (traces.jsonl).
    assert json.loads(trace.model_dump_json())["info"]["store_build_id"] == "fixture-mini-store-v1"


def test_reward_params_override_reaches_trace_info():
    class StrictTask(NeuroGroundedQATask):
        reward_params = RewardParams(lam=1.0)

    [task] = list(_taskset(items_path=ITEMS_PATH).take(1))
    strict = StrictTask(task.data, provenance=task.provenance)
    assert strict.provenance_info()["reward_params"]["lam"] == 1.0


def test_task_built_without_a_taskset_records_only_reward_params():
    [task] = list(_taskset(items_path=ITEMS_PATH).take(1))
    bare = NeuroGroundedQATask(task.data)
    assert set(bare.provenance_info()) == {"reward_params"}


def test_invalid_item_error_carries_no_item_text(tmp_path):
    # An invalid item's error names the file, the line, field paths and error codes,
    # never the item's values: pydantic's own message would echo them, and with
    # allow_heldout this file could be the held-out split.
    record = json.loads(ITEMS_PATH.read_text().splitlines()[0])
    record.update(prompt="Which genes are associated with Zyxsecret Canary?", qtype="no_such_type")
    record["gold_points"][0]["evidence"] = ["E:SECRET1"]
    bad = tmp_path / "items.jsonl"
    bad.write_text(json.dumps(record) + "\n")
    with pytest.raises(ValueError) as exc:
        load_items(bad, "train")
    msg = str(exc.value)
    assert f"{bad}:1" in msg and "qtype:literal_error" in msg
    assert "Zyxsecret" not in msg and "SECRET1" not in msg and "no_such_type" not in msg
    assert exc.value.__cause__ is None and exc.value.__suppress_context__
