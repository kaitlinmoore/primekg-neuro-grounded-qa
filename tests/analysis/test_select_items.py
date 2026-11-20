"""``scripts/analysis/select_items.py``: seeded, stratified, train only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .helpers import REPO

import select_items as si  # noqa: E402  (path set up by helpers)
from qgen.safety import ItemsError  # noqa: E402

FIXTURE_ITEMS = REPO / "tests" / "fixtures" / "contracts" / "items.jsonl"


def items_dir(tmp_path: Path, lines: list[str]) -> Path:
    d = tmp_path / "items"
    d.mkdir()
    (d / "train.jsonl").write_text("\n".join(lines) + "\n")
    return d


def fixture_lines() -> list[str]:
    return [line for line in FIXTURE_ITEMS.read_text().splitlines() if line.strip()]


def test_stratified_ids_is_seeded_and_per_type():
    by_type = {"a": [f"a{i}" for i in range(10)], "b": ["b0", "b1"], "c": ["c0"]}
    first = si.stratified_ids(by_type, 2, "s1")
    assert first == si.stratified_ids({k: list(reversed(v)) for k, v in by_type.items()}, 2, "s1")
    assert {k: len(v) for k, v in first.items()} == {"a": 2, "b": 2, "c": 1}
    # A type's draw doesn't depend on the other types.
    assert si.stratified_ids({"a": by_type["a"]}, 2, "s1")["a"] == first["a"]


def test_select_writes_items_and_selection(tmp_path):
    lines = fixture_lines()
    d = items_dir(tmp_path, lines)
    out = tmp_path / "out"
    sel = si.select(d, 1, "s1", out)
    chosen = [json.loads(line) for line in (out / "items.jsonl").read_text().splitlines()]
    qtypes = {json.loads(line)["qtype"] for line in lines}
    assert sorted(c["qtype"] for c in chosen) == sorted(qtypes)  # one per type
    assert {c["id"] for c in chosen} == {r["id"] for rows in sel["chosen"].values() for r in rows}
    assert all(line in lines for line in (out / "items.jsonl").read_text().splitlines())  # byte for byte
    meta = json.loads((out / "selection.json").read_text())
    assert meta["seed"] == "s1" and meta["n_items"] == len(chosen)
    assert meta["items_sha256"] == si.sha256_file(out / "items.jsonl")
    assert si.select(d, 1, "s1", tmp_path / "again")["chosen"] == sel["chosen"]


def test_refuses_a_file_holding_a_non_train_record(tmp_path):
    lines = fixture_lines()
    rec = json.loads(lines[0])
    rec["split"] = "heldout"
    d = items_dir(tmp_path, [*lines, json.dumps(rec)])
    with pytest.raises(ItemsError):
        si.select(d, 1, "s1", tmp_path / "out")
    assert not (tmp_path / "out").exists()
