"""Build the synthetic generator world (``tests/fixtures/data/qgen/gen_world.json``) on disk.

The generator reads a real store directory (``build.json`` plus JSON Lines, the
``store_files`` layout) and a full-graph duckdb file with the measurement code's
tables (``adj``, ``pk_hier_first``, ``nodes``). This helper writes both from the
fixture, so the tests run the generator's CLI exactly as on the real data.

The world (59 entities, 151 store edges; see the fixture's ``_comment``) has seeds
in all six disease groups (one with a grouped ``group:`` ID), an HPO
"Mode of inheritance" root that exists only in the full graph with one store
phenotype below it, a disease and a phenotype that share a name, and one
``disease_protein`` fact the subgraph cut.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
from neuro_grounded_qa.data import store_files as sf

WORLD = Path(__file__).resolve().parents[1] / "fixtures" / "data" / "qgen" / "gen_world.json"

HIERARCHY = {"disease_disease", "phenotype_phenotype"}
"""The world's hierarchy relations; both parent-first in file order (child is ``y``)."""


def load_world() -> dict:
    return json.loads(WORLD.read_text(encoding="utf-8"))


def build_world(root: Path, world: dict | None = None, drop_edges: set[int] = frozenset(),
                build_id: str | None = None) -> tuple[Path, Path, dict]:
    """Write ``<root>/store`` and ``<root>/full.duckdb``; return (store dir, duckdb, world).

    ``drop_edges`` removes store edges by their position in ``store_edges`` (from the
    store and the full graph), to make a world with a different candidate pool.
    """
    world = world or load_world()
    build_id = build_id or world["build_id"]
    index = {e["id"]: e["primekg_index"] for e in world["entities"]}
    store_dir = root / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    kept = [e for i, e in enumerate(world["store_edges"]) if i not in drop_edges]
    edges = sorted(kept, key=lambda e: (e[1], e[0], e[3]))
    edge_recs = [
        {"id": f"E:{i:06d}", "subject": s, "relation": r, "display_relation": d, "object": o, "directed": dr}
        for i, (s, r, d, o, dr) in enumerate(edges, start=1)
    ]
    ents = sorted(world["entities"], key=lambda e: e["id"])
    files = {
        sf.ENTITIES: sf.write_jsonl(store_dir / sf.ENTITIES, [
            {"id": e["id"], "name": e["name"], "type": e["type"], "source": e["source"]} for e in ents]),
        sf.EDGES: sf.write_jsonl(store_dir / sf.EDGES, edge_recs),
        sf.ENTITY_SOURCES: sf.write_jsonl(store_dir / sf.ENTITY_SOURCES, [
            {"id": e["id"], "primekg_index": e["primekg_index"]} for e in ents]),
        sf.EDGE_SOURCES: sf.write_jsonl(store_dir / sf.EDGE_SOURCES, []),
    }
    sf.write_build_meta(store_dir, {
        "format": sf.FORMAT,
        "build_id": build_id,
        "config": {"seeds": world["seeds"]},
        "files": {k: {"sha256": v} for k, v in files.items()},
    })

    db = root / "full.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE nodes(id VARCHAR, type VARCHAR, name VARCHAR, source VARCHAR, source_id VARCHAR)")
    con.executemany("INSERT INTO nodes VALUES (?, ?, ?, ?, ?)",
                    [(e["primekg_index"], e["type"], e["name"], e["source"], e["source_id"])
                     for e in world["entities"]]
                    + [(n["primekg_index"], n["type"], n["name"], n["source"], n["source_id"])
                       for n in world["full_only_nodes"]])
    con.execute("CREATE TABLE adj(a VARCHAR, b VARCHAR, eid BIGINT, rel VARCHAR)")
    con.execute("CREATE TABLE pk_hier_first(rel VARCHAR, parent VARCHAR, child VARCHAR)")
    full = [(s, r, o, dr) for s, r, _, o, dr in kept] + [tuple(x) for x in world["full_only_edges"]]
    adj, hier = [], []
    for i, (s, r, o, dr) in enumerate(full):
        a, b = index.get(s, s), index.get(o, o)
        adj += [(a, b, i, r), (b, a, i, r)]
        if r in HIERARCHY:
            hier.append((r, b, a))  # first row parent-first: x = parent (the object), y = child
    con.executemany("INSERT INTO adj VALUES (?, ?, ?, ?)", adj)
    con.executemany("INSERT INTO pk_hier_first VALUES (?, ?, ?)", hier)
    con.close()
    return store_dir, db, world


def heldout_tokens(items: list[dict], store_dir: Path) -> set[str]:
    """Every entity name, entity ID, edge ID and item ID in the given item records."""
    names = {r["id"]: r["name"] for r in sf.read_jsonl(store_dir / sf.ENTITIES)}
    out: set[str] = set()
    for it in items:
        out.add(it["id"])
        ids = set(json.loads(it["notes"]).get("named", []))
        for gp in it["gold_points"]:
            ids |= {gp["subject_id"], gp["object_id"]}
            out |= set(gp["evidence"])
        for t in [it["premise"], *(g["triple"] for g in it["graph_gaps"])]:
            if t:
                ids |= {t["subject_id"], t["object_id"]}
        out |= ids | {names[i] for i in ids}
    return out
