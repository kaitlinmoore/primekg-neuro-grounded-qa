"""Tests for the evidence store builder (``neuro_grounded_qa.data.build``) and the store
loader (``tools.store``), against a tiny synthetic PrimeKG release.

Fixture: ``tests/fixtures/data/primekg_raw/`` holds a 34-row ``kg.csv`` in PrimeKG's
column layout: 17 facts, each written x→y and then, after all originals, y→x, as
PrimeKG does. Its nodes (``x_index``: name, ``<source>:<id>``):

- 0 APP ``NCBI:351``; 1 TRNP and 2 MT-TP, which share ``NCBI:4571`` (a collision)
- 3 Alzheimer disease, grouped ``4975_11913`` (member 4975 has the node's name)
- 4 familial Alzheimer disease ``MONDO:100087``; 5 dementia ``MONDO:4847``
- 6 Donepezil ``DrugBank:DB00843``; 7 Memory impairment ``HPO:2354``; 13 All ``HPO:1``
- 8 brain ``UBERON:955``; 9 cerebral cortex ``UBERON:956``
- 10 lonely group, grouped ``9_7`` (no member has the node's name)
- 11 lead ``CTD:D007854``; 12 metals ``CTD:D008670``

Hierarchy facts (first row in file order): ``disease_disease`` 5→3 and 3→4
(parent→child), ``phenotype_phenotype`` 13→7 (parent→child), ``anatomy_anatomy``
9→8 (child→parent, PrimeKG's anatomy convention), ``exposure_exposure`` 12→11 (left
undirected). Donepezil–APP carries both ``drug_protein`` target and enzyme.

Hand-computed degrees (distinct neighbours): APP 6, AD 5, MT-TP 3; 1, 4, 6, 7, 8, 9,
11 have 2; 5, 10, 12, 13 have 1. With seed {3, 4}, N1 = {0, 1, 3, 4, 5, 6, 7}; at the
50th percentile the cap is 2, so APP (6) is not expanded and (c) keeps the 9 edges
among N1 plus 13–7: 10 edges on 8 nodes.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import load  # scripts/data, on sys.path via conftest
import subgraph as sg
from neuro_grounded_qa.contracts import EdgeEvidence, EvidenceStore, InMemoryEvidenceStore
from neuro_grounded_qa.data import build
from neuro_grounded_qa.data import store_files as sf
from neuro_grounded_qa.data.build import BuildConfig, Seed
from neuro_grounded_qa.tools import load_store, read_build_id

RAW = Path(__file__).resolve().parents[1] / "fixtures" / "data" / "primekg_raw"

AD = Seed("3", "Alzheimer disease", "AD")
FAD = Seed("4", "familial Alzheimer disease", "AD")
CORTEX = Seed("9", "cerebral cortex", "test")
LEAD = Seed("11", "lead", "test")

# Every fact: seeds touching every relation, subgraph (b).
FULL = BuildConfig(seeds=(AD, FAD, CORTEX, LEAD), seed_set="fixture-full", subgraph="b")
PRUNED = BuildConfig(seeds=(AD, FAD), seed_set="fixture-c50", subgraph="c", percentile=50)


def _build(tmp: Path, config: BuildConfig, name: str = "store", raw: Path = RAW) -> tuple[Path, dict]:
    out = tmp / name
    return out, build.build_store(raw, out, config)


@pytest.fixture(scope="module")
def full(tmp_path_factory):
    out, meta = _build(tmp_path_factory.mktemp("full"), FULL)
    edges = sf.read_jsonl(out / sf.EDGES)
    return out, meta, edges


def _edge(edges, relation, a, b):
    """The one edge of ``relation`` between entities a and b (either order)."""
    hits = [e for e in edges if e["relation"] == relation and {e["subject"], e["object"]} == {a, b}]
    assert len(hits) == 1, (relation, a, b, hits)
    return hits[0]


# -- deduplication -------------------------------------------------------------


def test_reversed_rows_collapse_to_one_edge_per_fact(full):
    _out, meta, edges = full
    assert meta["counts"]["raw_rows"] == 34
    assert meta["counts"]["graph_canonical_edges"] == 17
    assert len(edges) == 17
    keys = [(e["relation"], frozenset((e["subject"], e["object"]))) for e in edges]
    assert len(set(keys)) == len(keys)
    assert meta["checks"]["pairs_without_exactly_two_rows"] == 0
    assert meta["checks"]["pairs_whose_second_row_is_not_the_reverse"] == 0


def test_each_edge_records_both_raw_rows(full):
    out, _meta, _edges = full
    for src in sf.read_jsonl(out / sf.EDGE_SOURCES):
        a, b = src["kg_csv_rows"]
        assert b == a + 17  # the reverse copy sits 17 rows after the original


# -- hierarchy normalization ---------------------------------------------------


def test_parent_child_file_order_becomes_child_to_parent(full):
    _out, _meta, edges = full
    e = _edge(edges, "disease_disease", "MONDO:4847", "MONDO_grouped:4975")
    assert (e["subject"], e["object"], e["directed"]) == ("MONDO_grouped:4975", "MONDO:4847", True)
    e = _edge(edges, "disease_disease", "MONDO_grouped:4975", "MONDO:100087")
    assert (e["subject"], e["object"], e["directed"]) == ("MONDO:100087", "MONDO_grouped:4975", True)
    e = _edge(edges, "phenotype_phenotype", "HPO:1", "HPO:2354")
    assert (e["subject"], e["object"], e["directed"]) == ("HPO:2354", "HPO:1", True)


def test_anatomy_file_order_is_child_first(full):
    _out, _meta, edges = full
    e = _edge(edges, "anatomy_anatomy", "UBERON:955", "UBERON:956")
    assert (e["subject"], e["object"], e["directed"]) == ("UBERON:956", "UBERON:955", True)


def test_orient_follows_first_row_for_each_convention():
    types = {"p": "disease", "c": "disease", "a": "anatomy", "b": "anatomy"}
    ids = {k: k for k in types}
    assert build.orient("disease_disease", ("p", "c"), ids, types) == ("c", "p", True)
    assert build.orient("disease_disease", ("c", "p"), ids, types) == ("p", "c", True)
    assert build.orient("anatomy_anatomy", ("a", "b"), ids, types) == ("a", "b", True)
    assert build.orient("anatomy_anatomy", ("b", "a"), ids, types) == ("b", "a", True)


def test_exposure_exposure_stays_undirected(full):
    _out, _meta, edges = full
    e = _edge(edges, "exposure_exposure", "CTD:D007854", "CTD:D008670")
    assert e["directed"] is False
    assert e["subject"] == "CTD:D007854"  # smaller ID first


def test_only_hierarchy_relations_are_directed(full):
    _out, _meta, edges = full
    directed = {e["relation"] for e in edges if e["directed"]}
    assert directed == {"disease_disease", "phenotype_phenotype", "anatomy_anatomy"}


def test_other_relations_put_the_named_type_first(full):
    _out, _meta, edges = full
    # kg.csv's first row is APP -> brain; the relation name puts anatomy first.
    e = _edge(edges, "anatomy_protein_present", "NCBI:351", "UBERON:955")
    assert (e["subject"], e["object"]) == ("UBERON:955", "NCBI:351")
    e = _edge(edges, "indication", "DrugBank:DB00843", "MONDO_grouped:4975")
    assert e["subject"] == "DrugBank:DB00843"


def test_orient_rejects_unknown_relation_and_bad_types():
    types = {"x": "drug", "y": "drug"}
    with pytest.raises(ValueError, match="no orientation rule"):
        build.orient("mystery", ("x", "y"), {"x": "x", "y": "y"}, types)
    with pytest.raises(ValueError, match="expected one drug"):
        build.orient("drug_protein", ("x", "y"), {"x": "x", "y": "y"}, types)


# -- entity IDs ------------------------------------------------------------------


def test_entity_id_scheme(full):
    out, meta, _edges = full
    ents = {e["id"]: e for e in sf.read_jsonl(out / sf.ENTITIES)}
    assert ents["NCBI:351"]["name"] == "APP"
    assert ents["MONDO_grouped:4975"]["name"] == "Alzheimer disease"  # member named like the node
    assert ents["MONDO_grouped:7"]["name"] == "lonely group"  # no name match: smallest member
    assert ents["primekg:1"]["name"] == "TRNP"  # NCBI:4571 is shared, so both fall back
    assert ents["primekg:2"]["name"] == "MT-TP"
    assert "NCBI:4571" not in ents
    assert meta["counts"]["entity_id_schemes_graph"] == {"fallback": 2, "grouped": 2, "source": 10}
    srcs = {e["id"]: e for e in sf.read_jsonl(out / sf.ENTITY_SOURCES)}
    assert srcs["MONDO_grouped:4975"]["members"] == ["4975", "11913"]
    assert srcs["primekg:1"] == {"id": "primekg:1", "primekg_index": "1", "x_source": "NCBI",
                                 "x_id": "4571", "id_scheme": "fallback"}


def test_grouped_member_choice():
    names = {"10": "B thing", "2": "a thing", "30": "other"}
    assert build.grouped_member("A Thing", ["10", "2", "30"], names) == "2"
    assert build.grouped_member("no match", ["10", "2", "30"], names) == "2"
    assert build.grouped_member("no match", ["10", "30"], names) == "10"


def test_entity_ids_fall_back_only_for_collisions():
    nodes = [("0", "1", "NCBI", "a"), ("1", "1", "NCBI", "b"), ("2", "1", "HPO", "c"),
             ("3", "5_6", "MONDO_grouped", "g")]
    ids = build.entity_ids(nodes, {"6": "g"})
    assert ids == {"0": ("primekg:0", "fallback"), "1": ("primekg:1", "fallback"),
                   "2": ("HPO:1", "source"), "3": ("MONDO_grouped:6", "grouped")}


# -- relation labels -------------------------------------------------------------


def test_drug_protein_is_qualified_and_others_are_not(full):
    _out, meta, edges = full
    t = _edge(edges, "drug_protein/target", "DrugBank:DB00843", "NCBI:351")
    z = _edge(edges, "drug_protein/enzyme", "DrugBank:DB00843", "NCBI:351")
    assert (t["display_relation"], z["display_relation"]) == ("target", "enzyme")
    assert {e["relation"] for e in edges if "/" in e["relation"]} == {
        "drug_protein/target", "drug_protein/enzyme"}
    assert meta["checks"]["relations_qualified"] == ["drug_protein"]
    assert build.relation_label("disease_protein", "associated with") == "disease_protein"


def test_ambiguous_label_fails_the_build(tmp_path):
    raw = tmp_path / "raw"
    shutil.copytree(RAW, raw)
    kg = raw / "kg.csv"
    lines = kg.read_text().splitlines(keepends=True)
    lines[1] = lines[1].replace("disease_protein,associated with", "disease_protein,causes", 1)
    kg.write_text("".join(lines))
    with pytest.raises(ValueError, match="several display_relation values but no qualifier"):
        build.build_store(raw, tmp_path / "out", FULL)


# -- subgraph selection ----------------------------------------------------------


def test_pruned_subgraph_matches_hand_count(tmp_path):
    out, meta = _build(tmp_path, PRUNED)
    assert meta["config"]["subgraph"]["degree_cap"] == 2
    assert meta["counts"]["edges"] == 10
    assert meta["counts"]["nodes"] == 8
    ents = {e["id"] for e in sf.read_jsonl(out / sf.ENTITIES)}
    assert "UBERON:955" not in ents  # only reachable through APP, which is over the cap


@pytest.mark.parametrize("config", [PRUNED, FULL, BuildConfig(seeds=(AD,), subgraph="a")])
def test_subgraph_matches_the_measurement_code(tmp_path, config):
    """The builder's selection equals scripts/data/subgraph.py's on the same raw file."""
    import duckdb

    out, meta = _build(tmp_path, config)
    con = duckdb.connect()
    load.load_primekg(con, RAW)
    sg.canonicalize_edges(con)
    sg.build_adjacency(con)
    sg.compute_degree(con)
    sg.set_seed(con, [s.index for s in config.seeds])
    if config.subgraph == "c":
        spec = sg.SubgraphSpec("t", "c", max_degree=sg.degree_threshold(con, config.percentile))
    else:
        spec = sg.SubgraphSpec("t", config.subgraph)
    sg.build_subgraph(con, spec)
    theirs = {(r, frozenset((u, v))) for r, u, v in
              con.execute("SELECT e.rel, e.u, e.v FROM edges e JOIN sub_t USING (eid)").fetchall()}
    ours = {(e["relation"], frozenset((s["subject_index"], s["object_index"])))
            for e, s in zip(sf.read_jsonl(out / sf.EDGES), sf.read_jsonl(out / sf.EDGE_SOURCES), strict=True)}
    assert ours == theirs


def test_renamed_seed_fails(tmp_path):
    bad = BuildConfig(seeds=(Seed("3", "Alzheimers", "AD"),))
    with pytest.raises(ValueError, match="seed nodes missing or renamed"):
        build.build_store(RAW, tmp_path / "out", bad)


def test_manifest_mismatch_fails(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"datasets": {"primekg": {"files": [
        {"name": "kg.csv", "sha256": "0" * 64},
        {"name": "kg_grouped_diseases.csv", "sha256": "0" * 64}]}}}))
    with pytest.raises(ValueError, match="raw files differ"):
        build.build_store(RAW, tmp_path / "out", FULL, manifest)


# -- determinism and build_id -----------------------------------------------------


def test_same_config_gives_identical_files_and_build_id(tmp_path):
    a, meta_a = _build(tmp_path, FULL, "a")
    b, meta_b = _build(tmp_path, FULL, "b")
    for name in sf.DATA_FILES:
        assert (a / name).read_bytes() == (b / name).read_bytes(), name
    assert meta_a["build_id"] == meta_b["build_id"]


def test_evidence_ids_follow_relation_subject_object_order(full):
    _out, _meta, edges = full
    assert [e["id"] for e in edges] == [f"E:{i:06d}" for i in range(1, 18)]
    keys = [(e["relation"], e["subject"], e["object"]) for e in edges]
    assert keys == sorted(keys)


def test_evidence_id_width_grows_with_the_count(monkeypatch):
    # With the minimum lowered to 2 digits, 100 edges need 3: the width is never
    # less than the count needs (the real minimum is 6).
    monkeypatch.setattr(build, "MIN_ID_DIGITS", 2)
    edges = [{"relation": "r", "subject": f"s{i:03d}", "object": "o"} for i in range(100)]
    out = build.assign_evidence_ids(edges)
    assert out[0]["id"] == "E:001" and out[-1]["id"] == "E:100"
    assert out[0]["subject"] == "s000"


def test_duplicate_edges_are_rejected():
    e = {"relation": "r", "subject": "a", "object": "b"}
    with pytest.raises(ValueError, match="duplicate"):
        build.assign_evidence_ids([e, dict(e)])


def test_build_id_depends_on_inputs():
    raw = {"kg.csv": "a" * 64, "kg_grouped_diseases.csv": "b" * 64}
    base, basis = build.compute_build_id(raw, PRUNED)
    assert base.startswith("primekg-2.1-") and len(base) == len("primekg-2.1-") + 12
    assert build.compute_build_id(dict(raw), PRUNED)[0] == base
    # Seed order doesn't matter; seed membership, percentile, data and rules do.
    assert build.compute_build_id(raw, BuildConfig(seeds=(FAD, AD), subgraph="c", percentile=50))[0] == base
    assert build.compute_build_id(raw, BuildConfig(seeds=(AD,), subgraph="c", percentile=50))[0] != base
    assert build.compute_build_id(raw, BuildConfig(seeds=(AD, FAD), subgraph="c", percentile=95))[0] != base
    assert build.compute_build_id({**raw, "kg.csv": "c" * 64}, PRUNED)[0] != base
    assert basis["rules"] == build.RULES and basis["builder_version"] == build.BUILDER_VERSION


def test_named_seed_sets():
    neuro = {s.index for s in build.SEED_SETS["neuro"]}
    assert len(neuro) == len(build.SEED_SETS["neuro"]) == 28
    assert {"28473", "28467", "30093", "29670", "28708", "32696"} <= neuro
    assert "38307" not in neuro
    assert len(build.SEED_SETS["neuro-name-rule"]) == 22


# -- loading -------------------------------------------------------------------------


def test_store_round_trips_through_load_store(full):
    out, meta, edges = full
    store = load_store(out)
    assert isinstance(store, InMemoryEvidenceStore)
    assert isinstance(store, EvidenceStore)
    assert len(store) == 17
    e = store.get(_edge(edges, "anatomy_anatomy", "UBERON:955", "UBERON:956")["id"])
    assert isinstance(e, EdgeEvidence)
    assert (e.subject.name, e.object.name, e.directed) == ("cerebral cortex", "brain", True)
    assert store.entity("primekg:2").name == "MT-TP"
    assert [x.id for x in store.search_entities("alzheimer", 5)] == ["MONDO_grouped:4975", "MONDO:100087"]
    assert "drug_protein/target" in store.relations()


def test_read_build_id_agrees_with_load_store(full):
    out, meta, _edges = full
    assert read_build_id(out) == load_store(out).build_id == meta["build_id"]
    assert read_build_id(out / sf.BUILD_JSON) == meta["build_id"]
    assert load_store(out / sf.BUILD_JSON).build_id == meta["build_id"]


def test_read_build_id_never_reads_the_edges(tmp_path):
    out, meta = _build(tmp_path, PRUNED)
    (out / sf.EDGES).write_text("not json\n")
    assert read_build_id(out) == meta["build_id"]
    with pytest.raises(ValueError, match="does not match build.json"):
        load_store(out)


def test_directory_without_build_json_is_not_a_store(tmp_path):
    with pytest.raises(ValueError, match="unknown evidence store format"):
        load_store(tmp_path)
    with pytest.raises(ValueError, match="unknown evidence store format"):
        read_build_id(tmp_path)


def test_loading_a_store_needs_no_duckdb(full):
    """duckdb is only the optional build extra: loading must work without it."""
    import subprocess
    import sys

    out, meta, _edges = full
    code = (
        "import sys; sys.modules['duckdb'] = None\n"  # any `import duckdb` now raises
        "from neuro_grounded_qa.tools import load_store, read_build_id\n"
        f"s = load_store({str(out)!r})\n"
        f"assert s.build_id == read_build_id({str(out)!r}) == {meta['build_id']!r}\n"
        "assert len(s) == 17\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
