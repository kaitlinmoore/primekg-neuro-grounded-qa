"""Load PrimeKG, OptimusKG or a small CSV graph into the canonical duckdb tables.

Each loader creates ``nodes(id, type, name, source)`` and
``raw_edges(u, v, rel, directed, sources, score)`` (see ``subgraph.py``), then
the caller runs ``subgraph.canonicalize_edges`` and the rest. ``score`` is the
edge's association score where the source gives one (OptimusKG's Open Targets
``evidence_score``) and NULL otherwise; it is used only by the reported-only
score sensitivity.

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
      undirected. For parent-child relations the first copy of a pair in file
      order keeps the source ontology's parent→child orientation (the build
      appends the reversed copies after the originals); ``load_primekg``
      records that orientation in ``pk_hier_first`` for the directed-relation
      inventory. It is a property of the file layout, not a documented field.
    - Sources: ``kg.csv`` carries no edge-level provenance. Each relation is
      mapped to the primary resource(s) that built it, verified against the
      PrimeKG paper and build code (``PRIMEKG_RELATION_SOURCES``). That
      mapping is relation-level, not per-edge.

OptimusKG (``nodes.parquet``, ``edges.parquet``)
    - Node ID: ``id`` (ontology CURIEs such as ``MONDO_0004975`` or Ensembl
      gene IDs).
    - Relation: ``label`` + ``/`` + ``relation`` (``DIS-GEN/ASSOCIATED_WITH``):
      OptimusKG reuses relation strings across edge types (``ASSOCIATED_WITH``
      appears on DIS-GEN, PHE-GEN and DRG-PHE).
    - Direction: from the ``undirected`` column.
    - Sources: ``properties.sources.direct`` ∪ ``properties.sources.indirect``,
      per edge.
    - Score: ``properties.evidence_score`` (present on DIS-GEN and PHE-GEN
      ASSOCIATED_WITH edges), else NULL.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

Con = duckdb.DuckDBPyConnection

# Relation -> primary resource(s), relation-level only. kg.csv has no per-edge
# source column, so this is the only provenance available for PrimeKG.
#
# Verified on 2026-11-19 against two sources (the first version of this table,
# written from memory, had phenotype_protein wrong; see docs/changelog.md):
#   [P] the PrimeKG paper: Chandak, Huang & Zitnik, "Building a knowledge graph
#       to enable precision medicine", Scientific Data 10:67 (2023),
#       doi:10.1038/s41597-023-01960-3, Methods ("Primary data resources" and
#       "Resolving overlap between phenotype and disease nodes").
#   [R] the build code in https://github.com/mims-harvard/PrimeKG at commit
#       32a3818 (knowledge_graph/build_graph.ipynb, which concatenates the 28
#       dataframes that make up kg.csv, and datasets/primary_data_resources.sh).
#       Dataverse v2.1 predates the repo's July 2023 refresh; the refresh
#       changed download links and source releases, not which source feeds
#       which relation.
# Where [P] and [R] disagree, [R] wins, because it is the code that built the
# file. The one disagreement: [P]'s HPO section lists protein-phenotype edges
# among HPO outputs, but [R] builds phenotype_protein only from DisGeNET rows
# with diseaseType == "phenotype" (cell "Effect protein interactions
# (DisGenNet)"), and [P] itself says "Protein-phenotype edges extracted from
# DisGeNet were converted to protein-disease relations" when the phenotype is
# also a MONDO disease.
#
# For the licensing count, a relation mapped to a restricted source counts all
# its edges (relation-level, so an upper bound for relations with more than one
# source). Each entry cites [P] section and/or [R] notebook cell.
PRIMEKG_RELATION_SOURCES: dict[str, list[str]] = {
    # [P] "Four sources of physical protein-protein interactions": the Menche et
    # al. human interactome, BioGRID, STRING and HuRI. [R] reads a manually
    # copied ppi/protein_protein.csv.
    "protein_protein": ["Menche et al. interactome", "BioGRID", "STRING", "HuRI"],
    # [P] "DrugBank"; [R] cell "Drug protein interactions (DrugBank)":
    # target, enzyme, carrier and transporter files, joined as drug_protein.
    "drug_protein": ["DrugBank"],
    # [P] "DrugBank" (synergistic interactions); [R] cell "Drug drug
    # interactions (DrugBank)".
    "drug_drug": ["DrugBank"],
    # [P] "Side effect knowledgebases" (SIDER); [R] cell "Drug effect
    # interactions (SIDER)": SIDER MedDRA terms mapped to HPO, drugs mapped
    # through ATC codes to DrugBank IDs.
    "drug_effect": ["SIDER"],
    # [P] "Drug Central"; [R] cell "Drug disease interactions": DrugCentral's
    # omop_relationship table. Drugs are joined to DrugBank by CAS number and
    # diseases to MONDO through the UMLS crosswalk (PRIMEKG_UMLS_CROSSWALK).
    "indication": ["DrugCentral"],
    "contraindication": ["DrugCentral"],
    "off-label use": ["DrugCentral"],
    # [P] "DisGeNET" and "Resolving overlap..."; [R] cells "Disease protein
    # interactions (DisGenNet)" (curated rows with diseaseType == "disease",
    # UMLS CUI -> MONDO) and "HANDLE PROTEIN EFFECT" (DisGeNET phenotype rows
    # whose HPO term is also a MONDO disease). Both halves are DisGeNET.
    "disease_protein": ["DisGeNET"],
    # [P] "Human phenotype ontology" (phenotype.hpoa annotations); [R] cells
    # "Disease effect interactions (HPO-A)" plus "HANDLE EFFECT EFFECT", which
    # turns HPO parent-child edges with one end in MONDO into disease-phenotype
    # edges.
    "disease_phenotype_positive": ["HPO annotations", "HPO"],
    "disease_phenotype_negative": ["HPO annotations"],
    # [R] cell "Effect protein interactions (DisGenNet)": DisGeNET curated rows
    # with diseaseType == "phenotype", mapped to HPO. Corrected from
    # "HPO annotations" on 2026-11-19.
    "phenotype_protein": ["DisGeNET"],
    # [P] "MONDO disease ontology"; [R] cells "Disease disease interactions
    # (MONDO)" plus "HANDLE EFFECT EFFECT" (HPO parent-child edges with both
    # ends in MONDO become disease_disease).
    "disease_disease": ["MONDO", "HPO"],
    # [P] "Human phenotype ontology"; [R] cell "Effect effect interactions (HPO)".
    "phenotype_phenotype": ["HPO"],
    # [P] "Uberon"; [R] cell "Anatomy anatomy interactions (UBERON)".
    "anatomy_anatomy": ["UBERON"],
    # [P] "Bgee"; [R] cell "Anatomy Protein (BGEE)".
    "anatomy_protein_present": ["Bgee"],
    "anatomy_protein_absent": ["Bgee"],
    # [P] "Entrez gene" (gene2go via GOATOOLS); [R] cell "Go protein
    # interactions (Gene2GO)".
    "bioprocess_protein": ["GO annotations (NCBI gene2go)"],
    "cellcomp_protein": ["GO annotations (NCBI gene2go)"],
    "molfunc_protein": ["GO annotations (NCBI gene2go)"],
    # [P] "Gene ontology"; [R] cell "Go terms interactions (GO)".
    "bioprocess_bioprocess": ["GO"],
    "cellcomp_cellcomp": ["GO"],
    "molfunc_molfunc": ["GO"],
    # [P] "Reactome"; [R] cells "Pathways" and "Pathway protein interactions".
    "pathway_protein": ["Reactome"],
    "pathway_pathway": ["Reactome"],
    # [P] "Comparative toxicogenomics database"; [R] cells "Exposure ...
    # interactions (CTD)" (exposure events; diseases mapped through MONDO's
    # MeSH cross-references, GO terms through CTD's phenotype IDs).
    "exposure_protein": ["CTD"],
    "exposure_disease": ["CTD"],
    "exposure_exposure": ["CTD"],
    "exposure_bioprocess": ["CTD"],
    "exposure_molfunc": ["CTD"],
    "exposure_cellcomp": ["CTD"],
}

# Relations whose disease end was placed through a UMLS CUI -> MONDO crosswalk
# built from the UMLS Metathesaurus (MRCONSO.RRF, license-gated) by [R]
# datasets/processing_scripts/umls.py and map_umls_mondo.py. The facts
# themselves come from DrugCentral and DisGeNET, so these edges are not counted
# as "UMLS-derived" in R3; the share is reported separately so the shipping
# decision can weigh it.
PRIMEKG_UMLS_CROSSWALK: set[str] = {"indication", "contraindication", "off-label use", "disease_protein"}

# Same-type relations whose meaning is symmetric: (a, r, b) and (b, r, a) are
# the same fact. Every other relation between two nodes of the same type is a
# hierarchy (parent-child, is_a) or another directional link, where the
# reversed reading is a different fact. Relations between different node types
# need no entry: the types fix the reading. Used by the directed-relation
# inventory (direction.py) and the per-subgraph inversion counts.
SYMMETRIC_SAME_TYPE: dict[str, set[str]] = {
    # protein_protein: "ppi" (physical interaction); drug_drug: "synergistic
    # interaction". Both symmetric by meaning.
    "primekg": {"protein_protein", "drug_drug"},
    # GEN-GEN/INTERACTS_WITH is flagged undirected by OptimusKG.
    # DRG-DRG/SYNERGISTIC_INTERACTION is flagged directed by OptimusKG, but a
    # synergistic interaction has no direction, and the file never stores both
    # orientations of a pair (checked in direction.py).
    "optimuskg": {"GEN-GEN/INTERACTS_WITH", "DRG-DRG/SYNERGISTIC_INTERACTION"},
}

# OptimusKG relations that carry Open Targets' association score
# (properties.evidence_score), for the reported-only score sensitivity.
SCORED_RELATIONS: dict[str, list[str]] = {
    "primekg": [],
    "optimuskg": ["DIS-GEN/ASSOCIATED_WITH", "PHE-GEN/ASSOCIATED_WITH"],
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
        SELECT row_number() OVER () AS rn, *
        FROM read_csv('{kg}', all_varchar=true, header=true,
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
               coalesce(s.sources, ['unmapped']) AS sources,
               NULL::DOUBLE AS score
        FROM pk_raw p LEFT JOIN relsrc s ON s.relation = p.relation
        """,
        [multi_rel],
    )
    # Parent-child relations: the orientation of each pair's first row in file
    # order (x = presumed parent). Row numbers follow the scan order of
    # read_csv, which duckdb preserves (preserve_insertion_order is on by
    # default). direction.py checks this orientation against OptimusKG's
    # ontology edges before anything relies on it.
    con.execute(
        """
        CREATE OR REPLACE TABLE pk_hier_first AS
        SELECT relation AS rel, x_index AS parent, y_index AS child, rn
        FROM (
            SELECT *, row_number() OVER (
                PARTITION BY relation, least(x_index, y_index), greatest(x_index, y_index)
                ORDER BY rn) AS k
            FROM pk_raw WHERE display_relation = 'parent-child'
        ) WHERE k = 1
        """
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


def apply_primekg_sources(con: Con) -> None:
    """Set ``edges.sources`` from ``PRIMEKG_RELATION_SOURCES`` by base relation.

    The mapping is relation-level, so it can be re-applied to an existing
    canonical ``edges`` table without reloading ``kg.csv``. ``measure.py``
    calls this on every open, so a corrected mapping takes effect on the
    next run. ``drug_protein/target`` and its siblings map through
    ``drug_protein``.
    """
    con.execute("CREATE OR REPLACE TEMP TABLE relsrc(relation VARCHAR, sources VARCHAR[])")
    con.executemany("INSERT INTO relsrc VALUES (?, ?)", list(PRIMEKG_RELATION_SOURCES.items()))
    con.execute(
        """
        UPDATE edges SET sources = coalesce(
            (SELECT s.sources FROM relsrc s WHERE s.relation = split_part(edges.rel, '/', 1)),
            ['unmapped'])
        """
    )


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
               ) AS sources,
               TRY_CAST(properties->>'$.evidence_score' AS DOUBLE) AS score
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

    ``nodes_csv``: id,type,name,source. ``edges_csv``: u,v,rel,directed,sources
    and an optional ``score`` column, with ``sources`` separated by ``;``. An
    empty or missing score is NULL.
    """
    con.execute(
        f"""
        CREATE OR REPLACE TABLE nodes AS
        SELECT id, type, name, source, id AS source_id
        FROM read_csv('{nodes_csv}', all_varchar=true, header=true)
        """
    )
    cols = [c[0] for c in con.execute(
        f"DESCRIBE SELECT * FROM read_csv('{edges_csv}', all_varchar=true, header=true)"
    ).fetchall()]
    score = "TRY_CAST(nullif(score, '') AS DOUBLE)" if "score" in cols else "NULL::DOUBLE"
    con.execute(
        f"""
        CREATE OR REPLACE TABLE raw_edges AS
        SELECT u, v, rel, lower(directed) = 'true' AS directed,
               string_split(sources, ';') AS sources,
               {score} AS score
        FROM read_csv('{edges_csv}', all_varchar=true, header=true)
        """
    )
    return {}
