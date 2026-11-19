"""Directed-relation inventory for PrimeKG and OptimusKG.

The shared contracts (``neuro_grounded_qa.contracts.facts``) match a claim
triple to an edge in either order. That is safe for a relation only if the
reversed triple can never be a different fact. This script records, per
relation, what decides that:

- **Source flag.** OptimusKG marks each edge ``undirected`` or not. PrimeKG's
  ``kg.csv`` stores every edge twice, once per orientation, so it marks
  nothing.
- **Endpoint types.** If the two ends have different types (drug–gene,
  disease–phenotype), the types fix the reading: "gene X targets drug Y"
  can only mean the drug-targets-gene fact, so matching in either order
  can't support a wrong claim.
- **Meaning.** Between two nodes of one type, a relation is either symmetric
  (protein–protein interaction, drug–drug synergy;
  ``load.SYMMETRIC_SAME_TYPE``) or directional (parent–child, is_a). Only the
  directional ones can let a direction-insensitive check accept a wrong claim:
  "A is a subtype of B" and "B is a subtype of A" are both well-formed, and at
  most one is true.

It also checks whether PrimeKG's file order recovers hierarchy direction. The
build appends the reversed copy of each edge after the original, so the first
copy of a pair in file order should carry the source ontology's orientation.
For each PrimeKG hierarchy relation with a counterpart in OptimusKG (which
keeps direction), the first-copy orientation is compared with OptimusKG's
edge for the same ontology IDs.

The per-subgraph counts (pairs where the reversed reading is a different
fact) come from ``measure.py`` (``direction_inversions``); this script writes
the relation-level inventory to ``derived/direction_inventory.json``.

Run after ``measure.py`` has built both duckdb files::

    uv run python scripts/data/direction.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
import load  # noqa: E402
import subgraph as sg  # noqa: E402
from paths import derived_dir  # noqa: E402

# PrimeKG hierarchy relation -> (OptimusKG relation, PrimeKG node source, CURIE prefix).
# Both graphs take these hierarchies from the same ontologies, so the IDs line
# up once PrimeKG's bare numbers get OptimusKG's CURIE prefix.
COUNTERPARTS = {
    "disease_disease": ("DIS-DIS/PARENT", "MONDO", "MONDO_{:07d}"),
    "phenotype_phenotype": ("PHE-PHE/PARENT", "HPO", "HP_{:07d}"),
    "anatomy_anatomy": ("ANA-ANA/PARENT", "UBERON", "UBERON_{:07d}"),
    "bioprocess_bioprocess": ("BPO-BPO/IS_A", "GO", "GO_{:07d}"),
    "molfunc_molfunc": ("MFN-MFN/IS_A", "GO", "GO_{:07d}"),
    "cellcomp_cellcomp": ("CCO-CCO/IS_A", "GO", "GO_{:07d}"),
    "pathway_pathway": ("PWY-PWY/PARENT", "REACTOME", "REACT:{}"),
}


def relation_table(con, graph: str, raw_flags: dict[str, dict] | None) -> list[dict]:
    """One row per relation: edge count, endpoint types, source flag, meaning and risk."""
    same = sg.same_type_relations(con)
    sym = load.SYMMETRIC_SAME_TYPE[graph]
    rows = con.execute(
        """
        SELECT e.rel, count(*), list_sort(list_distinct(list(nu.type || ' / ' || nv.type)))
        FROM edges e JOIN nodes nu ON nu.id = e.u JOIN nodes nv ON nv.id = e.v
        GROUP BY e.rel ORDER BY e.rel
        """
    ).fetchall()
    out = []
    for rel, n, types in rows:
        if graph == "primekg":
            flag = "both orientations stored (no flag)"
        else:
            f = raw_flags[rel]
            flag = ("undirected" if f["undirected"] == f["rows"] else
                    "directed" if f["undirected"] == 0 else f"mixed ({f['undirected']:,} of {f['rows']:,} undirected)")
        mixed = ""
        if rel in same and same[rel]["same_type_edges"] < same[rel]["edges"]:
            mixed = f" ({same[rel]['same_type_edges']:,} of {same[rel]['edges']:,} edges join one type; the rest are fixed by their types)"
        if rel not in same:
            meaning, risk = "between different types: the types fix the reading", "no"
        elif rel in sym:
            meaning, risk = "same type, symmetric", "no"
        else:
            # Every directional same-type relation in both graphs is a hierarchy:
            # OptimusKG names them PARENT or IS_A, and every PrimeKG one has
            # display_relation "parent-child".
            hier = graph == "primekg" or any(k in rel for k in ("PARENT", "IS_A"))
            meaning = "same type, hierarchy (parent-child / is_a)" if hier else "same type, directional"
            if rel in NOT_A_CLEAN_HIERARCHY:
                meaning = "same type, directional (CTD exposure links labelled parent-child)"
            meaning += mixed
            risk = "yes"
        out.append({"relation": rel, "edges": n, "endpoint_types": types, "source_flag": flag,
                    "meaning": meaning, "wrong_support_possible": risk})
    return out


def optimus_flags(con) -> dict[str, dict]:
    """OptimusKG: raw edge rows and undirected rows per relation, from the parquet file."""
    import paths

    f = paths.raw_dir("optimuskg") / "edges.parquet"
    rows = con.execute(
        f"""SELECT label || '/' || relation, count(*), sum(undirected::INT)
            FROM read_parquet('{f}') GROUP BY 1"""
    ).fetchall()
    return {r: {"rows": n, "undirected": int(u)} for r, n, u in rows}


# Probes for OptimusKG's convention: either an ontology root (which has no
# parent, so it sits only on the parent side) or a known parent -> child pair.
CONVENTION_PROBES = {
    "DIS-DIS/PARENT": ("pair", "MONDO_0004975", "MONDO_0100087", "Alzheimer disease -> familial Alzheimer disease"),
    "PHE-PHE/PARENT": ("root", "HP_0000001", None, "HPO root 'All'"),
    "ANA-ANA/PARENT": ("root", "UBERON_0001062", None, "UBERON root 'anatomical entity'"),
    "BPO-BPO/IS_A": ("root", "GO_0008150", None, "GO root 'biological_process'"),
    "MFN-MFN/IS_A": ("root", "GO_0003674", None, "GO root 'molecular_function'"),
    "CCO-CCO/IS_A": ("root", "GO_0005575", None, "GO root 'cellular_component'"),
    "PWY-PWY/PARENT": ("root", "REACT:R-HSA-1643685", None, "Reactome top-level pathway 'Disease'"),
    "DRG-DRG/PARENT": ("pair", "CHEMBL982", "CHEMBL1201152", "nalmefene -> nalmefene hydrochloride (parent molecule -> salt)"),
}

# Same-type relations whose PARENT / parent-child label is not a clean
# hierarchy: CTD's exposure links mix class membership (alachlor ->
# Herbicides) with stressor -> marker links (alachlor -> Thyroxine). Still
# directional, so still a risk for direction-insensitive matching.
NOT_A_CLEAN_HIERARCHY = {"EXP-EXP/PARENT", "exposure_exposure"}


def optimus_hierarchy_convention(con) -> dict[str, dict]:
    """OptimusKG: which end of a hierarchy edge is the parent, from the probes above."""
    out = {}
    for rel, (kind, a, b, what) in CONVENTION_PROBES.items():
        if kind == "root":
            as_u, as_v = con.execute(
                "SELECT count(*) FILTER (WHERE u = ?), count(*) FILTER (WHERE v = ?) FROM edges WHERE rel = ?",
                [a, a, rel],
            ).fetchone()
            conv = ("from = parent" if as_u and not as_v else "from = child" if as_v and not as_u
                    else "undetermined")
            out[rel] = {"probe": what, "evidence": f"root as from: {as_u}; as to: {as_v}", "convention": conv}
        else:
            fwd, rev = con.execute(
                """SELECT count(*) FILTER (WHERE u = ? AND v = ?), count(*) FILTER (WHERE u = ? AND v = ?)
                   FROM edges WHERE rel = ?""",
                [a, b, b, a, rel],
            ).fetchone()
            conv = "from = parent" if fwd and not rev else "from = child" if rev and not fwd else "undetermined"
            out[rel] = {"probe": what, "evidence": f"edge parent->child: {fwd}; child->parent: {rev}",
                        "convention": conv}
    out["EXP-EXP/PARENT"] = {"probe": "alachlor -> Herbicides; alachlor -> Thyroxine",
                             "evidence": "both stored from the chemical", "convention": "mixed (not a clean hierarchy)"}
    return out


def primekg_file_order_check(pk, opt_db: Path, conventions: dict[str, dict]) -> dict[str, dict]:
    """PrimeKG: compare each hierarchy pair's first-copy orientation with OptimusKG's edge.

    For each relation in ``COUNTERPARTS``: the pairs whose both ends map to
    OptimusKG node IDs, and of those, how many OptimusKG has as u -> v in the
    same order as PrimeKG's first copy (``same_order``), in the opposite order
    (``opposite_order``), or not at all. Combined with OptimusKG's convention
    (which end is the parent), this says whether PrimeKG's first copy is
    parent -> child, child -> parent, or inconsistent.
    """
    pk.execute(f"ATTACH '{opt_db}' AS o (READ_ONLY)")
    out = {}
    try:
        for rel, (orel, src, fmt) in COUNTERPARTS.items():
            pairs = pk.execute(
                """
                SELECT np.source_id, nc.source_id FROM pk_hier_first h
                JOIN nodes np ON np.id = h.parent JOIN nodes nc ON nc.id = h.child
                WHERE h.rel = ? AND np.source = ? AND nc.source = ?
                """,
                [rel, src, src],
            ).fetchall()
            total_pairs = pk.execute("SELECT count(*) FROM pk_hier_first WHERE rel = ?", [rel]).fetchone()[0]

            def curie(x: str) -> str | None:
                if "{:07d}" in fmt:
                    return fmt.format(int(x)) if x.isdigit() else None
                return fmt.format(x)

            mapped = [(curie(a), curie(b)) for a, b in pairs]
            mapped = [(a, b) for a, b in mapped if a and b]
            pk.execute("CREATE OR REPLACE TEMP TABLE fo(a VARCHAR, b VARCHAR)")
            if mapped:
                pk.executemany("INSERT INTO fo VALUES (?, ?)", mapped)
            same, opp, both_known = pk.execute(
                """
                SELECT count(*) FILTER (WHERE EXISTS (SELECT 1 FROM o.edges e WHERE e.rel = ? AND e.u = fo.a AND e.v = fo.b)),
                       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM o.edges e WHERE e.rel = ? AND e.u = fo.b AND e.v = fo.a)),
                       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM o.nodes n WHERE n.id = fo.a)
                                          AND EXISTS (SELECT 1 FROM o.nodes n WHERE n.id = fo.b))
                FROM fo
                """,
                [orel, orel],
            ).fetchone()
            conv = conventions.get(orel, {}).get("convention", "undetermined")
            if same + opp == 0:
                reading = "not checkable"
            else:
                agree_first_is_u = same / (same + opp)
                first_is = "u" if agree_first_is_u >= 0.5 else "v"
                if conv == "from = parent":
                    reading = "parent -> child" if first_is == "u" else "child -> parent"
                elif conv == "from = child":
                    reading = "child -> parent" if first_is == "u" else "parent -> child"
                else:
                    reading = f"same order as OptimusKG's {orel}" if first_is == "u" else f"opposite order to OptimusKG's {orel}"
            out[rel] = {
                "optimuskg_relation": orel, "pairs": total_pairs, "pairs_with_mappable_ids": len(mapped),
                "pairs_with_both_nodes_in_optimuskg": both_known,
                "optimuskg_edge_same_order": same, "optimuskg_edge_opposite_order": opp,
                "agreement": max(same, opp) / (same + opp) if same + opp else None,
                "first_copy_reads": reading,
            }
    finally:
        pk.execute("DETACH o")
    return out


def main() -> None:
    d = derived_dir()
    opt = duckdb.connect(str(d / "optimuskg.duckdb"), read_only=True)
    flags = optimus_flags(opt)
    conv = optimus_hierarchy_convention(opt)
    inv = {"optimuskg": {"relations": relation_table(opt, "optimuskg", flags), "hierarchy_convention": conv}}
    opt.close()
    pk = duckdb.connect(str(d / "primekg.duckdb"), read_only=True)
    inv["primekg"] = {
        "relations": relation_table(pk, "primekg", None),
        "file_order_check": primekg_file_order_check(pk, d / "optimuskg.duckdb", conv),
    }
    pk.close()
    out = d / "direction_inventory.json"
    out.write_text(json.dumps(inv, indent=1))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
