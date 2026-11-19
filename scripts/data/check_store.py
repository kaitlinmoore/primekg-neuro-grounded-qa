"""Cross-check a built evidence store against the raw release and the measurement code.

The builder (``neuro_grounded_qa.data.build``) reads ``kg.csv`` with duckdb. This
script re-reads it with Python's ``csv`` module, independently, and checks the store
three ways:

1. **Raw trace.** Every store edge is a raw ``kg.csv`` fact in one direction or the
   other, with the same relation and display relation; the two raw row numbers the
   store records hold exactly that fact and its reverse; no fact appears twice.
   Every entity's name, type and source match its raw node; its ID is re-derived
   here from the raw ``x_source``/``x_id`` with this script's own table of standard
   prefixes (``curie``), name slug (``group``) or ``primekg:<x_index>``
   (``fallback``); a ``curie`` ID really is unique over the whole graph and a
   ``fallback`` really is shared; grouped nodes list their members.
2. **Direction.** Every hierarchy edge is ``directed`` and its subject is the child
   by the file-order rule, re-derived here from the raw rows; every other edge is
   undirected. As a second, independent reading, directed ``disease_disease`` and
   ``phenotype_phenotype`` edges are compared with OptimusKG's ``PARENT`` edges
   between the same MONDO / HPO terms (OptimusKG stores parent -> child).
3. **Measurement agreement.** The store's (relation, node pair) set equals the
   subgraph that ``scripts/data/subgraph.py`` builds for the same seed and
   definition on ``derived/primekg.duckdb``, the code that produced the expansion
   test's numbers.

Writes ``checks.json`` into the store directory and exits non-zero if any check
fails. Run from the repo root::

    uv run python scripts/data/check_store.py "$NGQ_DATA_DIR/store/primekg-neuro-c95"
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
import subgraph as sg  # noqa: E402
from paths import derived_dir, raw_dir  # noqa: E402

from neuro_grounded_qa.data import build  # noqa: E402
from neuro_grounded_qa.data import store_files as sf  # noqa: E402


# Independent of build.SOURCE_PREFIXES on purpose: PrimeKG x_source -> (standard
# prefix, zero-padded width or None).
EXPECTED_PREFIX = {
    "CTD": ("MESH", None), "DrugBank": ("DrugBank", None), "GO": ("GO", 7), "HPO": ("HP", 7),
    "MONDO": ("MONDO", 7), "NCBI": ("NCBIGene", None), "REACTOME": ("REACT", None),
    "UBERON": ("UBERON", 7),
}


def curie_ok(eid: str, source: str, x_id: str) -> bool:
    """Whether ``eid`` is the standard CURIE for a raw (x_source, x_id)."""
    if source not in EXPECTED_PREFIX or ":" not in eid:
        return False
    prefix, width = EXPECTED_PREFIX[source]
    got_prefix, local = eid.split(":", 1)
    if got_prefix != prefix:
        return False
    if width is None:
        return local == x_id
    return len(local) == width and local.isdigit() and x_id.isdigit() and int(local) == int(x_id)


def group_ok(eid: str, name: str) -> bool:
    """Whether ``eid`` is ``group:<slug of name>``, optionally with a ``_<n>`` suffix."""
    slug = "_".join(w for w in re.split(r"[^a-z0-9]", name.lower()) if w)
    return eid == f"group:{slug}" or re.fullmatch(rf"group:{re.escape(slug)}_[0-9]+", eid) is not None


def load_store_files(store: Path) -> dict:
    """Every file of a store directory, as lists of dicts plus ``build.json``."""
    return {
        "meta": sf.read_build_meta(store),
        "entities": sf.read_jsonl(store / sf.ENTITIES),
        "edges": sf.read_jsonl(store / sf.EDGES),
        "entity_sources": sf.read_jsonl(store / sf.ENTITY_SOURCES),
        "edge_sources": sf.read_jsonl(store / sf.EDGE_SOURCES),
    }


def scan_kg(kg_csv: Path, rows_wanted: set[int], nodes_wanted: set[str]) -> dict:
    """One pass over ``kg.csv`` with ``csv``: the wanted rows, every node's attributes
    and ID-key counts, and the raw facts between wanted nodes."""
    rows: dict[int, tuple[str, str, str, str]] = {}
    attrs: dict[str, tuple[str, str, str, str]] = {}
    attr_conflicts = 0
    facts: set[tuple[str, str, str, str]] = set()
    with kg_csv.open(encoding="utf-8", newline="") as f:
        r = csv.reader(f)
        header = next(r)
        col = {name: i for i, name in enumerate(header)}
        for rn, row in enumerate(r, start=1):
            rel, disp = row[col["relation"]], row[col["display_relation"]]
            x, y = row[col["x_index"]], row[col["y_index"]]
            if rn in rows_wanted:
                rows[rn] = (rel, disp, x, y)
            for end in ("x", "y"):
                idx = row[col[f"{end}_index"]]
                a = (row[col[f"{end}_id"]], row[col[f"{end}_type"]], row[col[f"{end}_name"]],
                     row[col[f"{end}_source"]])
                old = attrs.setdefault(idx, a)
                if old != a:
                    attr_conflicts += 1
            if x in nodes_wanted and y in nodes_wanted:
                facts.add((rel, disp, x, y))
    keys: dict[str, set[str]] = defaultdict(set)
    for idx, (x_id, _t, _n, src) in attrs.items():
        keys[f"{src}:{x_id}"].add(idx)
    return {"rows": rows, "attrs": attrs, "attr_conflicts": attr_conflicts, "facts": facts, "keys": keys}


def check_raw(s: dict, kg: dict) -> dict:
    """Checks 1 and 2 (file-order part)."""
    ent_src = {e["id"]: e for e in s["entity_sources"]}
    fails: dict[str, list] = defaultdict(list)
    seen: set = set()
    hier = build.HIERARCHY_CHILD_END
    for e, p in zip(s["edges"], s["edge_sources"], strict=True):
        if e["id"] != p["id"]:
            fails["edge_and_source_out_of_step"].append(e["id"])
            continue
        si, oi = ent_src[e["subject"]]["primekg_index"], ent_src[e["object"]]["primekg_index"]
        if (si, oi) != (p["subject_index"], p["object_index"]):
            fails["index_mismatch"].append(e["id"])
        rel, disp = p["primekg_relation"], p["display_relation"]
        if build.relation_label(rel, disp) != e["relation"] or disp != e["display_relation"]:
            fails["label_mismatch"].append(e["id"])
        if (rel, disp, si, oi) not in kg["facts"] and (rel, disp, oi, si) not in kg["facts"]:
            fails["no_raw_fact"].append(e["id"])
        key = (e["relation"], frozenset((si, oi)))
        if key in seen:
            fails["duplicate_fact"].append(e["id"])
        seen.add(key)
        recorded = [kg["rows"].get(rn) for rn in p["kg_csv_rows"]]
        if len(recorded) != 2 or any(r is None for r in recorded) or {(r[2], r[3]) for r in recorded} != {
            (si, oi), (oi, si)
        } or any((r[0], r[1]) != (rel, disp) for r in recorded):
            fails["recorded_rows_wrong"].append(e["id"])
            continue
        first = recorded[0] if p["kg_csv_rows"][0] < p["kg_csv_rows"][1] else recorded[1]
        if rel in hier:
            child = first[3] if hier[rel] == "y" else first[2]
            if not e["directed"] or si != child:
                fails["hierarchy_not_child_to_parent"].append(e["id"])
        elif e["directed"]:
            fails["undirected_relation_flagged_directed"].append(e["id"])
    seeds = {x["id"] for x in s["meta"]["config"]["seeds"]}
    in_edges = {e[k] for e in s["edges"] for k in ("subject", "object")}
    for ent, src in zip(s["entities"], s["entity_sources"], strict=True):
        a = kg["attrs"].get(src["primekg_index"])
        if a is None or (ent["name"], ent["type"], ent["source"]) != (a[2], a[1], a[3]) or src["x_id"] != a[0]:
            fails["entity_attrs_mismatch"].append(ent["id"])
            continue
        x_id, src_name = a[0], a[3]
        scheme = src["id_scheme"]
        key_owners = kg["keys"][f"{src_name}:{x_id}"]
        if scheme == "curie":
            ok = curie_ok(ent["id"], src_name, x_id) and len(key_owners) == 1
        elif scheme == "group":
            members = [m["id"] for m in src.get("members", [])]
            ok = (src_name == "MONDO_grouped" and group_ok(ent["id"], a[2])
                  and len(members) == len(x_id.split("_"))
                  and all(curie_ok(m, "MONDO", n) for m, n in zip(members, x_id.split("_"), strict=True)))
        elif scheme == "fallback":
            ok = ent["id"] == f"primekg:{src['primekg_index']}" and len(key_owners) > 1
        else:
            ok = False
        if not ok:
            fails[f"entity_id_scheme_{scheme}_wrong"].append(ent["id"])
        if ent["id"] not in in_edges and ent["id"] not in seeds:
            fails["entity_in_no_edge"].append(ent["id"])
    ids = [e["id"] for e in s["entities"]]
    if len(set(ids)) != len(ids):
        fails["entity_ids_not_unique"].append(len(ids) - len(set(ids)))
    shared = {k: v for k, v in kg["keys"].items() if len(v) > 1}
    return {
        "edges_checked": len(s["edges"]),
        "entities_checked": len(s["entities"]),
        "raw_node_attribute_conflicts": kg["attr_conflicts"],
        "graph_source_ids_shared_by_several_nodes": len(shared),
        "graph_nodes_with_shared_source_id": sum(len(v) for v in shared.values()),
        "shared_source_ids_by_source": _count_by_source(shared),
        "failures": {k: {"count": len(v), "examples": v[:5]} for k, v in fails.items()},
    }


def _count_by_source(shared: dict[str, set[str]]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for k in shared:
        out[k.split(":", 1)[0]] += 1
    return dict(sorted(out.items()))


def check_optimuskg(s: dict) -> dict:
    """Directed disease_disease / phenotype_phenotype edges vs OptimusKG PARENT edges."""
    db = derived_dir() / "optimuskg.duckdb"
    if not db.exists():
        return {"skipped": f"{db} not found"}
    curie = {"disease_disease": ("MONDO", "MONDO_{:07d}", "DIS-DIS/PARENT"),
             "phenotype_phenotype": ("HP", "HP_{:07d}", "PHE-PHE/PARENT")}
    con = duckdb.connect(str(db), read_only=True)
    out = {}
    for rel, (src, fmt, orel) in curie.items():
        pairs = []
        for e in s["edges"]:
            if e["relation"] != rel:
                continue
            ps, po = e["subject"].split(":", 1), e["object"].split(":", 1)
            if ps[0] == src and po[0] == src and ps[1].isdigit() and po[1].isdigit():
                pairs.append((fmt.format(int(po[1])), fmt.format(int(ps[1]))))  # (parent, child)
        con.execute("CREATE OR REPLACE TEMP TABLE p(parent VARCHAR, child VARCHAR)")
        if pairs:
            con.executemany("INSERT INTO p VALUES (?, ?)", pairs)
        agree, disagree = con.execute(
            """SELECT count(*) FILTER (WHERE EXISTS (SELECT 1 FROM edges e WHERE e.rel = ? AND e.u = p.parent AND e.v = p.child)),
                      count(*) FILTER (WHERE EXISTS (SELECT 1 FROM edges e WHERE e.rel = ? AND e.u = p.child AND e.v = p.parent))
               FROM p""",
            [orel, orel],
        ).fetchone()
        out[rel] = {"store_edges": sum(1 for e in s["edges"] if e["relation"] == rel),
                    "with_plain_ontology_ids": len(pairs), "optimuskg_agrees": agree,
                    "optimuskg_reversed": disagree}
    con.close()
    return out


def check_measurement(s: dict) -> dict:
    """The store's (relation, pair) set vs ``subgraph.py`` on the measurement database."""
    db = derived_dir() / "primekg.duckdb"
    if not db.exists():
        return {"skipped": f"{db} not found; run scripts/data/measure.py primekg first"}
    cfg = s["meta"]["config"]
    con = duckdb.connect()
    con.execute(f"ATTACH '{db}' AS pk (READ_ONLY)")
    for t in ("nodes", "edges", "adj", "deg"):
        con.execute(f"CREATE TABLE {t} AS SELECT * FROM pk.{t}")
    con.execute("DETACH pk")
    sg.set_seed(con, [x["primekg_index"] for x in cfg["seeds"]])
    sub = cfg["subgraph"]
    if sub["kind"] == "c":
        cap = sg.degree_threshold(con, sub["degree_percentile"])
        spec = sg.SubgraphSpec("chk", "c", max_degree=cap)
    else:
        cap, spec = None, sg.SubgraphSpec("chk", sub["kind"])
    sg.build_subgraph(con, spec)
    theirs = {
        (rel, frozenset((u, v)))
        for rel, u, v in con.execute("SELECT e.rel, e.u, e.v FROM edges e JOIN sub_chk USING (eid)").fetchall()
    }
    n_nodes = con.execute("SELECT count(*) FROM subnodes_chk").fetchone()[0]
    con.close()
    ours = {(e["relation"], frozenset((p["subject_index"], p["object_index"])))
            for e, p in zip(s["edges"], s["edge_sources"], strict=True)}
    return {
        "degree_cap_measurement": cap,
        "degree_cap_store": sub.get("degree_cap"),
        "measurement_edges": len(theirs),
        "measurement_nodes": n_nodes,
        "store_edges": len(ours),
        "store_nodes": len(s["entities"]),
        "only_in_store": len(ours - theirs),
        "only_in_measurement": len(theirs - ours),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("store", type=Path)
    p.add_argument("--raw", type=Path, default=raw_dir("primekg"))
    args = p.parse_args()
    s = load_store_files(args.store)
    rows = {rn for x in s["edge_sources"] for rn in x["kg_csv_rows"]}
    nodes = {x["primekg_index"] for x in s["entity_sources"]}
    kg = scan_kg(args.raw / build.KG_CSV, rows, nodes)
    result = {
        "build_id": s["meta"]["build_id"],
        "raw": check_raw(s, kg),
        "optimuskg_direction": check_optimuskg(s),
        "measurement": check_measurement(s),
    }
    m = result["measurement"]
    ok = (
        not result["raw"]["failures"]
        and result["raw"]["raw_node_attribute_conflicts"] == 0
        and ("skipped" in m or (m["only_in_store"] == 0 and m["only_in_measurement"] == 0
                                and m["degree_cap_measurement"] == m["degree_cap_store"]
                                and m["measurement_nodes"] == m["store_nodes"]))
    )
    result["ok"] = ok
    (args.store / "checks.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=1))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
