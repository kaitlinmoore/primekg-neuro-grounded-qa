"""Load PrimeKG, OptimusKG or a small CSV graph into the canonical duckdb tables.

Each loader creates ``nodes(id, type, name, source)`` and
``raw_edges(u, v, rel, directed, sources)`` (see ``subgraph.py``), then the
caller runs ``subgraph.canonicalize_edges`` and the rest.

Graph-specific conventions, and why:

PrimeKG (``kg.csv``)
    - Node ID: ``x_index`` / ``y_index``, PrimeKG's own node index. It is the
      only column that is unique per node (``x_id`` collides across sources:
      an NCBI gene ID and an HPO term can share a number). It is internal to
      this release.
    - Relation: the ``relation`` column, except ``drug_protein``, which covers
      four different facts (target, enzyme, transporter, carrier) told apart
      only by ``display_relation``. That one becomes ``drug_protein/target``
      and so on, so a drug that is both target and enzyme of a protein gives
      two facts, not one.
    - Direction: every row is stored twice (x→y and y→x), so all edges are
      undirected.
    - Sources: ``kg.csv`` carries no edge-level provenance. Each relation is
      mapped to the primary resource(s) the PrimeKG paper names for it
      (``PRIMEKG_RELATION_SOURCES``). That mapping is relation-level, not
      per-edge.

OptimusKG (``nodes.parquet``, ``edges.parquet``)
    - Node ID: ``id`` (ontology CURIEs such as ``MONDO_0004975`` or Ensembl
      gene IDs).
    - Relation: ``label`` + ``/`` + ``relation`` (``DIS-GEN/ASSOCIATED_WITH``):
      OptimusKG reuses relation strings across edge types (``ASSOCIATED_WITH``
      appears on DIS-GEN, PHE-GEN and DRG-PHE).
    - Direction: from the ``undirected`` column.
    - Sources: ``properties.sources.direct`` ∪ ``properties.sources.indirect``,
      per edge.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

Con = duckdb.DuckDBPyConnection

# Relation -> primary resource(s), relation-level only. kg.csv has no per-edge
# source column, so this is the only provenance available for PrimeKG.
# Attributions follow the PrimeKG paper (Chandak, Huang & Zitnik, Scientific
# Data 2023) as recalled; they were NOT re-checked against the paper in this
# run. Entries marked "(unverified)" are the least certain. For the licensing
# count, a relation mapped to a restricted source counts all its edges, which
# is an upper bound if the paper lists further sources for that relation.
PRIMEKG_RELATION_SOURCES: dict[str, list[str]] = {
    "protein_protein": ["PPI resources (unverified)"],
    "drug_protein": ["DrugBank"],
    "drug_drug": ["DrugBank"],
    "drug_effect": ["SIDER"],
    "indication": ["DrugCentral"],
    "contraindication": ["DrugCentral"],
    "off-label use": ["DrugCentral"],
    "disease_protein": ["DisGeNET"],
    "disease_phenotype_positive": ["HPO annotations"],
    "disease_phenotype_negative": ["HPO annotations"],
    "phenotype_protein": ["HPO annotations"],
    "disease_disease": ["MONDO"],
    "phenotype_phenotype": ["HPO"],
    "anatomy_anatomy": ["UBERON"],
    "anatomy_protein_present": ["Bgee"],
    "anatomy_protein_absent": ["Bgee"],
    "bioprocess_protein": ["GO annotations"],
    "cellcomp_protein": ["GO annotations"],
    "molfunc_protein": ["GO annotations"],
    "bioprocess_bioprocess": ["GO"],
    "cellcomp_cellcomp": ["GO"],
    "molfunc_molfunc": ["GO"],
    "pathway_protein": ["Reactome"],
    "pathway_pathway": ["Reactome"],
    "exposure_protein": ["CTD"],
    "exposure_disease": ["CTD"],
    "exposure_exposure": ["CTD"],
    "exposure_bioprocess": ["CTD"],
    "exposure_molfunc": ["CTD"],
    "exposure_cellcomp": ["CTD"],
}

# Sources whose terms are non-commercial or license-gated. Matched
# case-insensitively as substrings of an edge's source names.
RESTRICTED_SOURCES = {
    "DrugBank": ["drugbank", "drug_bank"],
    "SIDER": ["sider"],
    "DisGeNET": ["disgenet"],
    "UMLS": ["umls"],
}

# Relation pairs that contradict each other, for the false-premise count (C8).
OPPOSING = {
    "primekg": [
        ("indication", "contraindication"),
        ("disease_phenotype_positive", "disease_phenotype_negative"),
        ("anatomy_protein_present", "anatomy_protein_absent"),
    ],
    "optimuskg": [
        ("DRG-DIS/INDICATION", "DRG-DIS/CONTRAINDICATION"),
        ("DRG-PHE/INDICATION", "DRG-PHE/CONTRAINDICATION"),
        ("ANA-GEN/EXPRESSION_PRESENT", "ANA-GEN/EXPRESSION_ABSENT"),
    ],
}


def load_primekg(con: Con, raw: Path) -> dict:
    """Load ``kg.csv`` into ``nodes`` and ``raw_edges``. Returns load checks for the report."""
    kg = raw / "kg.csv"
    con.execute(
        f"""
        CREATE OR REPLACE TABLE pk_raw AS
        SELECT * FROM read_csv('{kg}', all_varchar=true, header=true,
                               quote='"', escape='"', delim=',')
        """
    )
    # drug_protein is the only relation with several display_relation values.
    multi = con.execute(
        "SELECT relation FROM pk_raw GROUP BY relation HAVING count(DISTINCT display_relation) > 1"
    ).fetchall()
    multi_rel = sorted(r[0] for r in multi)
    con.execute(
        """
        CREATE OR REPLACE TABLE nodes AS
        SELECT x_index AS id, any_value(x_type) AS type, any_value(x_name) AS name,
               any_value(x_source) AS source, any_value(x_id) AS source_id
        FROM (SELECT x_index, x_type, x_name, x_source, x_id FROM pk_raw
              UNION ALL SELECT y_index, y_type, y_name, y_source, y_id FROM pk_raw)
        GROUP BY x_index
        """
    )
    con.execute("CREATE OR REPLACE TEMP TABLE relsrc(relation VARCHAR, sources VARCHAR[])")
    con.executemany(
        "INSERT INTO relsrc VALUES (?, ?)", list(PRIMEKG_RELATION_SOURCES.items())
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE raw_edges AS
        SELECT p.x_index AS u, p.y_index AS v,
               CASE WHEN p.relation IN (SELECT unnest(?::VARCHAR[]))
                    THEN p.relation || '/' || p.display_relation ELSE p.relation END AS rel,
               false AS directed,
               coalesce(s.sources, ['unmapped']) AS sources
        FROM pk_raw p LEFT JOIN relsrc s ON s.relation = p.relation
        """,
        [multi_rel],
    )
    # Every row should have its reverse; count the ones that don't.
    unpaired = con.execute(
        """
        SELECT count(*) FROM pk_raw a
        ANTI JOIN pk_raw b ON b.x_index = a.y_index AND b.y_index = a.x_index
                          AND b.relation = a.relation AND b.display_relation = a.display_relation
        """
    ).fetchone()[0]
    unmapped = con.execute(
        "SELECT DISTINCT relation FROM pk_raw WHERE relation NOT IN (SELECT relation FROM relsrc)"
    ).fetchall()
    con.execute("DROP TABLE pk_raw")
    return {
        "relations_split_by_display_relation": multi_rel,
        "rows_without_reverse_copy": unpaired,
        "relations_without_source_mapping": sorted(r[0] for r in unmapped),
    }


def load_optimuskg(con: Con, raw: Path) -> dict:
    """Load OptimusKG's unified parquet tables into ``nodes`` and ``raw_edges``."""
    con.execute(
        f"""
        CREATE OR REPLACE TABLE nodes AS
        SELECT id, label AS type, properties->>'$.name' AS name,
               array_to_string(from_json(properties->'$.sources.direct', '["VARCHAR"]'), ';') AS source,
               id AS source_id
        FROM read_parquet('{raw / "nodes.parquet"}')
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE raw_edges AS
        SELECT "from" AS u, "to" AS v, label || '/' || relation AS rel,
               NOT undirected AS directed,
               list_concat(
                   coalesce(from_json(properties->'$.sources.direct', '["VARCHAR"]'), []),
                   coalesce(from_json(properties->'$.sources.indirect', '["VARCHAR"]'), [])
               ) AS sources
        FROM read_parquet('{raw / "edges.parquet"}')
        """
    )
    dangling = con.execute(
        """
        SELECT count(*) FROM raw_edges
        WHERE u NOT IN (SELECT id FROM nodes) OR v NOT IN (SELECT id FROM nodes)
        """
    ).fetchone()[0]
    dup_ids = con.execute(
        "SELECT count(*) FROM (SELECT id FROM nodes GROUP BY id HAVING count(*) > 1)"
    ).fetchone()[0]
    return {"edges_with_unknown_endpoint": dangling, "duplicate_node_ids": dup_ids}


def load_csv_graph(con: Con, nodes_csv: Path, edges_csv: Path) -> dict:
    """Load a small CSV graph (the test fixture format).

    ``nodes_csv``: id,type,name,source. ``edges_csv``: u,v,rel,directed,sources,
    with ``sources`` separated by ``;``.
    """
    con.execute(
        f"""
        CREATE OR REPLACE TABLE nodes AS
        SELECT id, type, name, source, id AS source_id
        FROM read_csv('{nodes_csv}', all_varchar=true, header=true)
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE raw_edges AS
        SELECT u, v, rel, lower(directed) = 'true' AS directed,
               string_split(sources, ';') AS sources
        FROM read_csv('{edges_csv}', all_varchar=true, header=true)
        """
    )
    return {}
