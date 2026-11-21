"""Tests for the direction checks ``scripts/data/check_store.py`` runs on a built store:
the directed-cycle check (fails on any cycle not listed as a known exception) and the
grouped-edge report (counts by relation and class).

Fixture: ``tests/fixtures/data/group_direction/`` (described in
``test_measure_group_direction.py``). Its directed edges hold one cycle of two edges,
``E:000001`` (A2 -> g_a) and ``E:000005`` (g_a -> A2); five directed edges have a
grouped end.
"""

import csv
import json
from pathlib import Path

import check_store as cs
import duckdb
import pytest

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "data" / "group_direction"


def read(name: str) -> list[dict]:
    with (FIX / name).open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


@pytest.fixture
def edges():
    return read("edges.jsonl")


@pytest.fixture
def reference_db(tmp_path):
    db = tmp_path / "reference.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE edges(u VARCHAR, v VARCHAR, rel VARCHAR)")
    with (FIX / "reference_edges.csv").open(encoding="utf-8") as f:
        con.executemany("INSERT INTO edges VALUES (?, ?, ?)", [(r["u"], r["v"], r["rel"]) for r in csv.DictReader(f)])
    con.close()
    return db


def test_finds_the_fixture_cycle(edges):
    assert cs.directed_cycles(edges) == [["E:000001", "E:000005"]]


def test_an_unlisted_cycle_fails(edges):
    r = cs.check_cycles(edges, known={})
    assert r["new_cycles"] == [["E:000001", "E:000005"]] and r["known_exceptions"] == []
    assert r["cycles"] == 1 and r["directed_edges"] == 8


def test_a_listed_cycle_is_reported_as_a_known_exception(edges):
    known = {frozenset({"E:000001", "E:000005"}): "disclosed"}
    r = cs.check_cycles(edges, known=known)
    assert r["new_cycles"] == []
    assert r["known_exceptions"] == [{"edges": ["E:000001", "E:000005"], "note": "disclosed"}]
    assert r["known_exceptions_not_found"] == []


def test_a_second_cycle_fails_even_with_the_first_listed(edges):
    known = {frozenset({"E:000001", "E:000005"}): "disclosed"}
    # X1 -> A is stored (E:000002); add A -> X1: a new two-edge cycle.
    extra = dict(edges[0], id="E:000099", subject="MONDO:0000002", object="MONDO:0000011", directed=True,
                 relation="disease_disease")
    a_x1 = next(e for e in edges if e["id"] == "E:000002")
    assert (a_x1["subject"], a_x1["object"]) == ("MONDO:0000011", "MONDO:0000002")
    r = cs.check_cycles(edges + [extra], known=known)
    assert r["new_cycles"] == [["E:000002", "E:000099"]]
    assert len(r["known_exceptions"]) == 1


def test_undirected_edges_and_self_loops(edges):
    # an undirected reverse edge is no cycle; a directed self-loop is one
    undirected = dict(edges[0], id="E:000098", subject=edges[0]["object"], object=edges[0]["subject"],
                      directed=False)
    assert cs.directed_cycles([e for e in edges if e["id"] != "E:000005"] + [undirected]) == []
    loop = dict(edges[0], id="E:000097", object=edges[0]["subject"])
    assert ["E:000097"] in cs.directed_cycles([loop])


def test_a_known_cycle_that_is_gone_is_reported_not_failed(edges):
    known = {frozenset({"E:000001", "E:000005"}): "disclosed"}
    r = cs.check_cycles([e for e in edges if e["id"] != "E:000005"], known=known)
    assert r["cycles"] == 0 and r["new_cycles"] == []
    assert r["known_exceptions_not_found"] == [["E:000001", "E:000005"]]


def test_the_real_stores_cycle_is_listed():
    # The store's one cycle, as measured (docs/question_set_design.md).
    assert frozenset({"E:014202", "E:014583", "E:014667"}) in cs.KNOWN_DIRECTED_CYCLES


def test_grouped_edge_report_counts_by_relation_and_class(edges, reference_db):
    r = cs.grouped_edge_report(edges, read("entity_sources.jsonl"), reference_db)
    assert r["directed_edges"] == 8
    assert r["with_grouped_end_by_relation"] == {"disease_disease": 5}
    assert r["classes"] == {
        "disease_disease|grouped|agrees": 2,
        "disease_disease|grouped|mixed": 1,
        "disease_disease|grouped|partial_agree": 1,
        "disease_disease|grouped|reversed": 1,
        "disease_disease|plain|no_ancestry": 1,
        "disease_disease|plain|unknown": 1,
        "phenotype_phenotype|plain|agrees": 1,
    }


def test_grouped_edge_report_without_the_reference(edges, tmp_path):
    r = cs.grouped_edge_report(edges, read("entity_sources.jsonl"), tmp_path / "missing.duckdb")
    assert r["with_grouped_end_by_relation"] == {"disease_disease": 5}
    assert "skipped" in r["classes"]
