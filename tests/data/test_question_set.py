"""Tests for the question-set generator and its commands (``scripts/data/qgen/``,
``scripts/data/question_set.py``), on the synthetic world in
``tests/fixtures/data/qgen/gen_world.json`` (see ``qgen_world.py``).

What is pinned here:

- **Writing**: ``generate`` writes ``train.jsonl``, ``heldout.jsonl`` and
  ``manifest.json``; every item validates, carries the store's ``build_id`` (as do
  the manifest and the directory name), ``source="template"``, its template ID and
  JSON notes with the route and parameters, and meets the targets exactly.
- **Determinism**: two runs give byte-identical files.
- **Checks**: ``check`` and ``check-heldout`` pass on a fresh set and fail on a
  tampered one.
- **No text out**: every command's stdout, stderr and non-item files hold no entity
  name, entity ID, edge ID or item ID from the held-out set (the viewer's output, which
  is train text, holds none that only the held-out set has).
- **Split stability**: changing the candidate pool never moves a candidate across
  the split, and no held-out candidate of one pool is a train item of another.
- **Domain filters and the name rule**: the HPO subtree filter, the grouped-node
  exclusion for reversed premises, and type-qualified mentions.
- **Viewer**: train only, refuses held-out paths and held-out records.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
from pathlib import Path

import pytest
import qgen_templates as qt
import qgen_world as W
from neuro_grounded_qa.contracts import QuestionItem
from neuro_grounded_qa.data import store_files as sf
from neuro_grounded_qa.data.build import HIERARCHY_CHILD_END
from neuro_grounded_qa.tools import KGToolset, KGToolsetConfig, load_store
from pydantic import BaseModel, ValidationError
from qgen import checks as qc
from qgen import files as F
from qgen.candidates import Filters, build_pool
from qgen.cli import main
from qgen.safety import no_text_out, scrubbed_error
from qgen.selection import Plan

duckdb = pytest.importorskip("duckdb")

TRAIN, HELDOUT = 40, 30
"""Fixture targets the world fills exactly (the 40% relation cap rounds to zero for
very small type targets, so the fixture can't use tiny splits)."""


def run(*argv: str) -> tuple[int, str, str]:
    """Run a command in-process; return (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
    return code, out.getvalue(), err.getvalue()


class World:
    def __init__(self, root: Path, drop_edges: set[int] = frozenset()) -> None:
        self.root = root
        self.store, self.db, self.spec = W.build_world(root, drop_edges=drop_edges)
        self.build_id = self.spec["build_id"]
        self.items = root / "items" / self.build_id
        self.outputs: dict[str, tuple[int, str, str]] = {}

    def args(self) -> list[str]:
        return ["--store", str(self.store), "--full-graph", str(self.db)]

    def generate(self, *extra: str, out: Path | None = None) -> tuple[int, str, str]:
        return run("generate", *self.args(), "--out", str(out or self.items),
                   "--train", str(TRAIN), "--heldout", str(HELDOUT), *extra)

    def records(self, split: str) -> list[dict]:
        return sf.read_jsonl(self.items / F.SPLIT_FILES[split])


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    w = World(tmp_path_factory.mktemp("world"))
    mp = pytest.MonkeyPatch()
    mp.setenv("NGQ_DATA_DIR", str(w.root))  # --build-id resolves under $NGQ_DATA_DIR/items
    w.outputs["generate"] = w.generate()
    assert w.outputs["generate"][0] == 0, w.outputs["generate"][2]
    w.outputs["check"] = run("check", *w.args(), "--items", str(w.items), "--json", str(w.root / "check.json"))
    w.outputs["check-heldout"] = run("check-heldout", *w.args(), "--build-id", w.build_id)
    w.outputs["view"] = run("view", "--items", str(w.items), "--store", str(w.store))
    w.outputs["spot-check"] = run("spot-check", "--items", str(w.items), "--store", str(w.store), "--n", "10")
    yield w
    mp.undo()


# ---------------------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------------------


def test_generate_writes_the_three_files_with_one_build_id(world):
    manifest = json.loads((world.items / F.MANIFEST).read_text())
    assert world.items.name == manifest["build_id"] == world.build_id
    for split in ("train", "heldout"):
        recs = world.records(split)
        assert recs and [r["id"] for r in recs] == sorted(r["id"] for r in recs)
        for r in recs:
            assert list(r) == list(F.ITEM_FIELDS)
            item = QuestionItem.model_validate(r)
            assert item.build_id == world.build_id and item.split == split
            assert item.source == "template" and item.template_id
            notes = json.loads(item.notes)
            assert "build_id" not in notes
            assert notes["route"] and notes["template_params"] and notes["group"]
            assert qc.split_of(item.id, Plan().split_seed, Plan().heldout_share) == split
        entry = manifest["files"][F.SPLIT_FILES[split]]
        assert entry["items"] == len(recs)
        assert entry["sha256"] == sf.file_sha256(world.items / F.SPLIT_FILES[split])
    assert manifest["template_catalog_sha256"] == qt.catalog_sha256()
    assert manifest["split"]["seed"] == "ngq-split-v1"


def test_generate_meets_every_target_exactly(world):
    manifest = json.loads((world.items / F.MANIFEST).read_text())
    assert manifest["shortfalls"] == {}
    for split, n in (("train", TRAIN), ("heldout", HELDOUT)):
        sel = manifest["selection"][split]
        assert sel["selected"] == sel["target"]
        assert manifest["counts"][split]["total"] == n
        assert manifest["counts"][split]["type"] == {k: v for k, v in sel["target"].items() if v}


def test_generate_refuses_a_shortfall_and_writes_nothing(world, tmp_path):
    out = tmp_path / world.build_id
    code, _, err = run("generate", *world.args(), "--out", str(out), "--train", "400", "--heldout", "30")
    assert code == 2 and "targets not met" in err
    assert not (out / F.MANIFEST).exists() and not (out / F.TRAIN_FILE).exists()


def test_generate_refuses_a_directory_not_named_after_the_build(world, tmp_path):
    code, _, err = run("generate", *world.args(), "--out", str(tmp_path / "items"))
    assert code == 2 and "build_id" in err


def test_generate_refuses_to_overwrite_without_force(world):
    code, _, err = world.generate()
    assert code == 2 and "--force" in err


def test_generate_is_deterministic(world, tmp_path):
    out = tmp_path / world.build_id
    assert world.generate(out=out)[0] == 0
    for name in (F.TRAIN_FILE, F.HELDOUT_FILE, F.MANIFEST):
        assert (out / name).read_bytes() == (world.items / name).read_bytes(), name


# ---------------------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------------------


def test_check_passes_on_a_fresh_set(world):
    code, out, _ = world.outputs["check"]
    assert code == 0 and out.strip().endswith("OK")
    report = json.loads((world.root / "check.json").read_text())
    assert report["ok"] and all(report["cross_split"].values())
    for r in report["splits"].values():
        assert all(c["fail"] == 0 and c["pass"] > 0 for c in r["item_checks"].values())
    code, out, _ = world.outputs["check-heldout"]
    assert code == 0 and "heldout: 30 items" in out and "train:" not in out


def _tampered(world, tmp_path, edit) -> Path:
    d = tmp_path / world.build_id
    shutil.copytree(world.items, d)
    recs = sf.read_jsonl(d / F.HELDOUT_FILE)
    edit(recs)
    sf.write_jsonl(d / F.HELDOUT_FILE, recs)
    return d


def test_check_heldout_fails_on_a_changed_file(world, tmp_path):
    def flip(recs):  # point one gold point at an edge that doesn't state it
        r = next(r for r in recs if r["gold_points"])
        other = next(x for x in recs if x["gold_points"] and x["gold_evidence"] != r["gold_evidence"])
        r["gold_points"][0]["evidence"] = other["gold_points"][0]["evidence"]
        r["gold_evidence"] = sorted({e for gp in r["gold_points"] for e in gp["evidence"]})
    d = _tampered(world, tmp_path, flip)
    code, out, _ = run("check-heldout", *world.args(), "--items", str(d))
    assert code == 1 and "FAILED" in out and "gold_points" in out and "sha256_matches_manifest" in out


def test_check_heldout_catches_a_premise_present_in_the_full_graph(world, tmp_path):
    def present(recs):  # make a "no" premise state a fact the full graph has
        r = next(r for r in recs if r["premise"] and r["answerable"] == "no")
        r["premise"] = {"subject_id": "S_DLB", "relation": "disease_protein", "object_id": "G_01", "directed": False}
    d = _tampered(world, tmp_path, present)
    code, out, _ = run("check-heldout", *world.args(), "--items", str(d))
    assert code == 1 and "premise_gap_absent" in out


def test_check_refuses_a_file_path(world):
    code, _, err = run("check-heldout", *world.args(), "--items", str(world.items / F.HELDOUT_FILE))
    assert code == 2 and "directory" in err


# ---------------------------------------------------------------------------------------
# No item text out
# ---------------------------------------------------------------------------------------


def _assert_no_tokens(text: str, tokens: set[str], where: str) -> None:
    """No token appears in ``text``, case-insensitively, on identifier boundaries (so the
    ID ``P_SH`` doesn't match inside ``group_share``)."""
    hits = sorted(t for t in tokens
                  if re.search(rf"(?<![A-Za-z0-9_]){re.escape(t)}(?![A-Za-z0-9_])", text, re.IGNORECASE))
    assert not hits, f"{where} contains held-out tokens: {hits[:5]}"


def test_commands_print_and_write_no_heldout_text(world):
    held = W.heldout_tokens(world.records("heldout"), world.store)
    train = W.heldout_tokens(world.records("train"), world.store)
    assert held and any(t.startswith("E:") for t in held)
    for cmd in ("generate", "check", "check-heldout"):
        _, out, err = world.outputs[cmd]
        _assert_no_tokens(out + err, held, cmd)
    # files other than the item files
    for path in (world.items / F.MANIFEST, world.root / "check.json"):
        _assert_no_tokens(path.read_text(), held, path.name)
    # the viewer shows train text, so only tokens that are held-out-only must be absent
    held_only = held - train
    assert held_only
    for cmd in ("view", "spot-check"):
        _, out, err = world.outputs[cmd]
        _assert_no_tokens(out + err, held_only, cmd)
    _assert_no_tokens((world.items / F.SPOT_CHECK).read_text(), held_only, F.SPOT_CHECK)


def test_a_failing_heldout_check_prints_no_text(world, tmp_path):
    secret = "Zyxsecret Canary"

    def poison(recs):  # invalid item whose text carries a canary name
        recs[0]["prompt"] = f"Which genes are associated with {secret}?"
        recs[0]["qtype"] = "no_such_type"
    d = _tampered(world, tmp_path, poison)
    held = W.heldout_tokens(world.records("heldout"), world.store) | {secret}
    code, out, err = run("check-heldout", *world.args(), "--items", str(d), "--json", str(tmp_path / "r.json"))
    assert code == 1
    _assert_no_tokens(out + err + (tmp_path / "r.json").read_text(), held, "check-heldout")


def test_errors_are_scrubbed():
    class M(BaseModel):
        x: int

    try:
        M(x="Synthgene Secret E:000123")
    except ValidationError as e:
        msg = scrubbed_error("stage", e)
    assert "Secret" not in msg and "E:000123" not in msg and "x:int_parsing" in msg
    msg = scrubbed_error("stage", KeyError("no graph node index for entity 'G_SECRET'"))
    assert "G_SECRET" not in msg and "KeyError" in msg

    err = io.StringIO()
    with contextlib.redirect_stderr(err), pytest.raises(SystemExit) as exc, no_text_out("stage"):
        raise ValueError("Synthgene Secret")
    assert exc.value.code == 2 and "Secret" not in err.getvalue() and "ValueError" in err.getvalue()


def test_generate_errors_are_scrubbed(world, tmp_path, monkeypatch):
    # A failure inside generation, carrying an entity name, comes out as type only.
    import qgen.candidates as cand

    def boom(*a, **k):
        raise RuntimeError("Synthdisease Huntington E:000001")
    monkeypatch.setattr(cand.qc, "answer_leaks", boom)
    code, out, err = run("generate", *world.args(), "--out", str(tmp_path / world.build_id))
    assert code == 2 and "RuntimeError" in err
    assert "Synthdisease" not in out + err and "E:000001" not in out + err


# ---------------------------------------------------------------------------------------
# Split stability
# ---------------------------------------------------------------------------------------


def _pool(w: World, filters: Filters | None = None):
    store = load_store(w.store)
    meta = sf.read_build_meta(w.store)
    edge_ids = [r["id"] for r in sf.read_jsonl(w.store / sf.EDGES)]
    index_of = {r["id"]: r["primekg_index"] for r in sf.read_jsonl(w.store / sf.ENTITY_SOURCES)}
    fg = qc.FullGraph(duckdb.connect(str(w.db), read_only=True), index_of, HIERARCHY_CHILD_END)
    pool, _, _ = build_pool(store, edge_ids, meta["config"]["seeds"], fg, w.build_id,
                            split_seed=Plan().split_seed, heldout_share=Plan().heldout_share,
                            generator_version="test", filters=filters)
    return {c.iid: c.item.split for c in pool.valid}


def test_changing_the_pool_never_moves_a_candidate_across_the_split(world, tmp_path):
    # Drop every fifth store edge: a different pool (and different evidence IDs).
    smaller = World(tmp_path / "smaller", drop_edges=set(range(0, len(world.spec["store_edges"]), 5)))
    a = _pool(world)
    b = _pool(smaller)
    c = _pool(world, Filters.none())
    assert a.keys() != b.keys() and a.keys() != c.keys()
    for other in (b, c):
        shared = a.keys() & other.keys()
        assert shared and all(a[k] == other[k] for k in shared)
    # No held-out candidate of one pool becomes a train item of the other.
    assert smaller.generate("--allow-shortfall")[0] == 0
    held_a = {r["id"] for r in world.records("heldout")}
    held_b = {r["id"] for r in smaller.records("heldout")}
    assert not ({r["id"] for r in smaller.records("train")} & {k for k, s in a.items() if s == "heldout"})
    assert not ({r["id"] for r in world.records("train")} & held_b)
    assert held_a or held_b


# ---------------------------------------------------------------------------------------
# Domain filters and the name rule
# ---------------------------------------------------------------------------------------


def test_hpo_subtree_is_kept_out_of_every_item(world):
    manifest = json.loads((world.items / F.MANIFEST).read_text())
    assert manifest["pool"]["hpo_excluded_store_entities"] == 1
    drops = sum(v.get("hpo_excluded_subtree", 0) for v in manifest["pool"]["domain_failures"].values())
    assert drops > 0
    for split in ("train", "heldout"):
        assert all("P_INH" not in json.dumps(r) for r in world.records(split))
    # and the filter off lets such candidates through
    assert any(k for k in _pool(world, Filters(hpo_excluded_roots=())) if k not in _pool(world))


def test_subtree_query_walks_the_full_graph(world):
    index_of = {r["id"]: r["primekg_index"] for r in sf.read_jsonl(world.store / sf.ENTITY_SOURCES)}
    fg = qc.FullGraph(duckdb.connect(str(world.db), read_only=True), index_of, HIERARCHY_CHILD_END)
    # The root is only in the full graph; its child is in the store.
    assert fg.subtree_entities("phenotype_phenotype", "HPO", ["5"]) == {"P_INH"}
    assert fg.subtree_entities("phenotype_phenotype", "HPO", ["999"]) == set()
    # P_02 is a kind of P_01 in the store's hierarchy (P_01 has HPO source ID 101).
    assert fg.subtree_entities("phenotype_phenotype", "HPO", ["101"]) == {"P_01", "P_02"}


def test_reversed_premises_skip_grouped_nodes(world):
    manifest = json.loads((world.items / F.MANIFEST).read_text())
    assert manifest["pool"]["domain_failures"]["fp_reversed.disease_disease"] == {
        "reversed_premise_grouped_node": 1
    }
    for split in ("train", "heldout"):
        for r in world.records(split):
            if r["template_id"] == "fp_reversed.disease_disease":
                p = r["premise"]
                assert not p["subject_id"].startswith("group:") and not p["object_id"].startswith("group:")


@pytest.fixture
def prober(world):
    return qc.Prober(KGToolset(KGToolsetConfig(), store=load_store(world.store)))


def test_shared_name_resolves_only_with_its_type(prober):
    store = prober.ts.store
    d, p = store.entity("D_SH"), store.entity("P_SH")
    assert d.name == p.name
    for e in (d, p):
        assert not qc.name_check(prober.search, e).ok
        assert qc.typed_name_check(prober.search, e).ok
        assert qc.resolve_mention(prober.search, e) == qc.Mention(True, True, "ok")
        assert not qc.resolve_mention(prober.search, e, allow_typed=False).ok
    assert qc.resolve_mention(prober.search, store.entity("G_01")) == qc.Mention(True, False, "ok")


def test_typed_name_rule_rejects_a_same_type_duplicate(prober):
    from neuro_grounded_qa.contracts import Entity

    hits = [Entity(id="A", name="Same", type="drug"), Entity(id="B", name="same", type="drug"),
            Entity(id="C", name="Same", type="exposure")]
    search = lambda _text: hits
    assert qc.typed_name_check(search, hits[0]).reason == "shared_name_in_type"
    assert qc.typed_name_check(search, hits[1]).reason == "not_first_of_type"
    assert qc.typed_name_check(search, hits[2]).ok


def test_typed_mentions_name_the_type_in_the_question(world):
    seen = 0
    for split in ("train", "heldout"):
        for r in world.records(split):
            typed = json.loads(r["notes"])["typed_mentions"]
            for eid in typed:
                seen += 1
                word = "disease" if eid == "D_SH" else "phenotype"
                assert f"the {word} Synthshared condition" in r["prompt"] or (
                    word == "phenotype" and "phenotype Synthshared condition" in r["prompt"])
            if not typed:
                assert "Synthshared condition" not in r["prompt"]
    assert seen
    # Where the wording already names the type, the bare name is used, once.
    assert qt.wording_names_type("disease_phenotype_positive", "obj", "effect/phenotype")
    assert not qt.wording_names_type("disease_protein", "obj", "gene/protein")
    assert qt.mention("X", "effect/phenotype", True) == "the phenotype X"
    assert set(qt.TYPE_WORD) >= {"disease", "effect/phenotype", "gene/protein", "drug", "exposure", "pathway"}


def test_text_patterns_stay_shared_by_two_answerability_classes():
    by_pattern: dict = {}
    for t in qt.catalog():
        by_pattern.setdefault((t.shape, t.relation_key), set()).add(t.answerable)
    assert all(len(v) >= 2 for v in by_pattern.values())


def test_catalog_hash_tracks_wording(monkeypatch):
    h = qt.catalog_sha256()
    monkeypatch.setitem(qt.EVID_STATEMENT, "indication", "{subj} treats {obj}")
    assert qt.catalog_sha256() != h


# ---------------------------------------------------------------------------------------
# Viewer and spot check
# ---------------------------------------------------------------------------------------


def test_viewer_renders_train_items(world):
    code, out, _ = world.outputs["view"]
    recs = world.records("train")
    assert code == 0 and f"{len(recs)} items. Train split only." in out
    assert all(r["id"] in out for r in recs)
    code, out, _ = run("view", "--items", str(world.items), "--qtype", "partial", "--limit", "2")
    assert code == 0 and out.count("### ") == 2 and "· partial ·" in out


def test_viewer_refuses_heldout(world, tmp_path):
    code, out, err = run("view", "--items", str(world.items / F.HELDOUT_FILE))
    assert code == 2 and "refusing" in err and not out
    # a train file that holds a held-out record is refused whole
    bad = tmp_path / "x"
    bad.mkdir()
    (bad / F.TRAIN_FILE).write_text(
        (world.items / F.TRAIN_FILE).read_text() + (world.items / F.HELDOUT_FILE).read_text().splitlines()[0] + "\n")
    code, out, err = run("view", "--items", str(bad))
    held = W.heldout_tokens(world.records("heldout")[:1], world.store) - W.heldout_tokens(
        world.records("train"), world.store)
    assert code == 2 and "not a train item" in err and not out
    _assert_no_tokens(err, held, "view refusal")


def test_spot_check_is_seeded_and_stratified(world):
    assert world.outputs["spot-check"][0] == 0
    text = (world.items / F.SPOT_CHECK).read_text()
    assert "10 of 40 train items" in text and text.count("### ") == 10
    for t in ("single_hop", "multi_hop", "unanswerable"):
        assert f"· {t} ·" in text
    assert run("spot-check", "--items", str(world.items), "--store", str(world.store), "--n", "10")[0] == 0
    assert (world.items / F.SPOT_CHECK).read_text() == text
