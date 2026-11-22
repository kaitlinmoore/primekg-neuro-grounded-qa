"""The sample draw: deterministic, stratified, one item once, exclusions honoured."""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from pathlib import Path

import pytest

from .helpers import (
    CELLS,
    EXCLUDE,
    FIXTURES,
    ITEMS,
    MENTIONED_ITEM,
    PER_CELL,
    RUN_DIRECTION,
    RUN_PARENT_CHILD,
    RUNS,
    SEALED,
    run_dir,
)

import draw  # noqa: E402  (path set up by helpers)
from grading_common import (  # noqa: E402
    DRAW_FILE,
    KEY_FILE,
    SEALED_FILE,
    GradingError,
    read_jsonl,
    sha256_file,
)


def _draw(out: Path, **kw) -> dict:
    args = dict(cells=CELLS, sealed_per_answerable=SEALED, exclude_from=EXCLUDE,
                exclude_root=FIXTURES, today="2026-01-01")
    args.update(kw)
    return draw.run_draw(ITEMS, list(RUNS), out, **args)


def test_draw_is_deterministic(tmp_path):
    _draw(tmp_path / "a")
    _draw(tmp_path / "b")
    for name in (KEY_FILE, SEALED_FILE, DRAW_FILE):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


def test_a_different_seed_draws_a_different_sample(tmp_path):
    _draw(tmp_path / "a")
    _draw(tmp_path / "b", seed="another-seed")
    ka = [(k["item_id"], k["model"]) for k in read_jsonl(tmp_path / "a" / KEY_FILE)]
    kb = [(k["item_id"], k["model"]) for k in read_jsonl(tmp_path / "b" / KEY_FILE)]
    assert ka != kb


def test_cells_filled_each_item_once_and_exclusions_respected(tmp_path):
    rec = _draw(tmp_path)
    key = read_jsonl(tmp_path / KEY_FILE)
    assert len(key) == 4 * 2 * PER_CELL == rec["total"]
    cells = Counter((k["answerable"], k["model"]) for k in key)
    assert set(cells.values()) == {PER_CELL} and len(cells) == 8
    items = [k["item_id"] for k in key]
    assert len(items) == len(set(items)), "an item appears twice"
    assert MENTIONED_ITEM not in items
    assert rec["exclusion"]["ids"] == 1
    assert rec["exclusion"]["files"] == 1
    assert rec["exclusion"]["files_per_pattern"] == {"docs/*.md": 1}
    assert "mentions.md" not in json.dumps(rec), "source file names stay out of the record"


def test_key_file_fields_ids_and_order(tmp_path):
    _draw(tmp_path)
    key = read_jsonl(tmp_path / KEY_FILE)
    assert [k["position"] for k in key] == list(range(1, len(key) + 1))
    assert all(re.fullmatch(r"g-[0-9a-f]{8}", k["grading_id"]) for k in key)
    assert len({k["grading_id"] for k in key}) == len(key)
    assert set(key[0]) == {"grading_id", "position", "round", "model", "run", "trace_id",
                           "trace_line", "trace_index", "item_id", "answerable"}
    assert {k["round"] for k in key} == {"first"}
    # Interleaved: grading order isn't grouped by cell.
    firsts = [(k["answerable"], k["model"]) for k in key[:4]]
    assert len(set(firsts)) > 1


def test_sealed_subset_is_stratified_and_holds_ids_only(tmp_path):
    rec = _draw(tmp_path, sealed_per_answerable=2)
    sealed = json.loads((tmp_path / SEALED_FILE).read_text())
    key = {k["grading_id"]: k for k in read_jsonl(tmp_path / KEY_FILE)}
    assert isinstance(sealed, list) and all(isinstance(g, str) for g in sealed)
    assert set(sealed) <= set(key)
    assert Counter(key[g]["answerable"] for g in sealed) == {a: 2 for a in
                                                             ("correctable", "no", "partial", "yes")}
    assert rec["sealed"] == {a: 2 for a in ("correctable", "no", "partial", "yes")}


def test_unequal_cells_are_filled_and_recorded(tmp_path):
    cells = {"correctable": 1, "no": 2, "partial": 2, "yes": 1}
    rec = _draw(tmp_path, cells=cells)
    key = read_jsonl(tmp_path / KEY_FILE)
    got = Counter((k["answerable"], k["model"]) for k in key)
    assert got == {(a, m): n for a, n in cells.items()
                   for m in ("sentinel-model-newer", "sentinel-model-older")}
    assert len({k["item_id"] for k in key}) == len(key) == 12 == rec["total"]
    assert rec["cells_per_model"] == cells
    assert rec["seeds"]["assign"] == "ngq-grade-first-v1/assign"
    assert rec["items_without_every_model"] == {a: 0 for a in cells}


def test_cells_must_name_every_answerable_value(tmp_path):
    with pytest.raises(GradingError, match="cell sizes"):
        _draw(tmp_path, cells={"no": 1, "partial": 1, "yes": 1})
    assert not (tmp_path / KEY_FILE).exists()


def test_parse_cells():
    assert draw.parse_cells("correctable=4, no=8,partial=8,yes=12") == draw.CELLS
    for bad in ("no=0", "no", "no=x", "no=1,no=2", "=3"):
        with pytest.raises(GradingError):
            draw.parse_cells(bad)


def _split(refs, seed="s", assign_seed="s/assign"):
    picked, _ = draw.draw_cells(refs, set(), {"yes": 3}, seed, assign_seed)
    return {r.item_id: r.model for r in picked}


def _refs(n_items, models=("model-a", "model-b")):
    return [draw.TraceRef("run", f"t{i}{m}", 1, 0, m, f"q-{i:012x}", "yes")
            for i in range(n_items) for m in models]


def test_models_are_assigned_by_the_assign_seed():
    """The items drawn depend on the draw seed only; which model each goes to depends
    on the assign seed, and no model takes first pick."""
    refs = _refs(12)
    base = _split(refs)
    other = _split(refs, assign_seed="other/assign")
    assert set(base) == set(other)
    assert base != other
    assert _split(refs) == base
    # Over many assign seeds, the first-sorted model's share of any given item is
    # about a half, as a seeded split implies (a fill-first order would give it the
    # draw seed's first picks every time).
    first = Counter()
    for k in range(200):
        for item, m in _split(refs, assign_seed=f"a{k}").items():
            first[item] += m == "model-a"
    assert all(60 <= c <= 140 for c in first.values()), first


def test_items_lacking_a_models_trace_are_left_out_and_counted():
    refs = _refs(8) + [draw.TraceRef("run", "solo", 1, 0, "model-a", "q-0000000000ff", "yes")]
    picked, incomplete = draw.draw_cells(refs, set(), {"yes": 4})
    assert incomplete == {"yes": 1}
    assert "q-0000000000ff" not in {r.item_id for r in picked}


def test_shortfall_stops_and_writes_nothing(tmp_path):
    # Five items per answerable value (four "yes" after the exclusion); 3 per model
    # needs 6 items.
    with pytest.raises(draw.Shortfall) as e:
        _draw(tmp_path, cells={a: 3 for a in CELLS})
    assert e.value.short == {"correctable": (5, 6), "no": (5, 6), "partial": (5, 6),
                             "yes": (4, 6)}
    assert "q-" not in str(e.value)
    assert not (tmp_path / KEY_FILE).exists()


def test_refuses_to_overwrite_a_draw(tmp_path):
    _draw(tmp_path)
    with pytest.raises(GradingError, match="exists"):
        _draw(tmp_path)
    _draw(tmp_path, force=True)


def test_refuses_heldout_items_file(tmp_path):
    held = tmp_path / "heldout.jsonl"
    held.write_text("")
    with pytest.raises(GradingError, match="heldout"):
        draw.run_draw(held, list(RUNS), tmp_path / "out")


def test_refuses_traces_from_another_items_file(tmp_path):
    src = run_dir(RUN_PARENT_CHILD)
    dst = tmp_path / "runs" / src.name
    shutil.copytree(src, dst)
    path = dst / "traces.jsonl"
    path.write_text(path.read_text().replace('"items_sha256": "', '"items_sha256": "00'))
    with pytest.raises(GradingError, match="SHA-256"):
        draw.run_draw(ITEMS, [dst, RUN_DIRECTION], tmp_path / "out", exclude_from=EXCLUDE,
                      exclude_root=FIXTURES)


def test_exclusion_ids_by_pattern(tmp_path):
    (tmp_path / "a.md").write_text("q-0123456789ab and q-0123456789AB and xq-0123456789ac1")
    ids, files = draw.exclusion_ids(["*.md"], tmp_path)
    assert ids == {"q-0123456789ab"} and files == ["a.md"]


def test_main_prints_no_ids_or_cell_counts(tmp_path, capsys):
    rc = draw.main(["--items", str(ITEMS), "--runs", *map(str, RUNS), "--dir", str(tmp_path),
                    "--cells", ",".join(f"{a}={n}" for a, n in CELLS.items()),
                    "--sealed-per-answerable", str(SEALED)])
    out = capsys.readouterr().out
    assert rc == 0 and "draw: PASS" in out
    assert not re.search(r"\b[qg]-[0-9a-f]{8,}", out)
    assert "sentinel-model" not in out and "yes" not in out


def test_accepts_runs_made_from_a_recorded_selection_of_train(tmp_path):
    """Chunked runs record their selection file's hash; selection.json links it to the
    train file. Without that link the run is refused."""
    src = run_dir(RUN_PARENT_CHILD)
    dst = tmp_path / "runs" / src.name
    shutil.copytree(src, dst)
    sel_dir = tmp_path / "items-c1"
    sel_dir.mkdir()
    sel_items = sel_dir / "items.jsonl"
    sel_items.write_text((ITEMS / "train.jsonl").read_text()[:200])  # any bytes will do
    sel_sha = sha256_file(sel_items)
    train_sha = sha256_file(ITEMS / "train.jsonl")
    path = dst / "traces.jsonl"
    path.write_text(path.read_text().replace(train_sha, sel_sha))
    cfg_path = dst / "configs" / "resolved" / "run.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["env"]["taskset"]["items_path"] = str(sel_items)
    cfg_path.write_text(json.dumps(cfg))
    kw = dict(cells=CELLS, sealed_per_answerable=SEALED, exclude_from=EXCLUDE,
              exclude_root=FIXTURES)
    with pytest.raises(GradingError, match="SHA-256"):
        draw.run_draw(ITEMS, [dst, RUN_DIRECTION], tmp_path / "out", **kw)
    (sel_dir / "selection.json").write_text(json.dumps(
        {"source_sha256": train_sha, "items_sha256": sel_sha}))
    rec = draw.run_draw(ITEMS, [dst, RUN_DIRECTION], tmp_path / "out", **kw)
    assert rec["runs"][0]["selection_sha256"] == sel_sha
    assert rec["runs"][1]["selection_sha256"] is None


def test_default_exclusion_sources_are_the_four_committed_docs():
    assert draw.EXCLUDE_FROM == ("docs/methodology.md", "docs/reward_hacking_audit.md",
                                 "docs/question_set_design.md", "docs/changelog.md")
