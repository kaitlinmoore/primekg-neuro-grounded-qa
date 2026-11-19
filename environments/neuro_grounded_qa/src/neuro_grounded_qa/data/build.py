"""Build the evidence store from the downloaded PrimeKG release.

Run (WSL, repo root, with the ``build`` extra or the root ``data`` group installed)::

    uv run python -m neuro_grounded_qa.data.build --out "$NGQ_DATA_DIR/store/primekg-neuro-c95"

It reads ``kg.csv`` and ``kg_grouped_diseases.csv`` from ``--raw`` (default
``$NGQ_DATA_DIR/raw/primekg``), checks their SHA-256 against the download manifest,
and writes a store directory in the format of :mod:`neuro_grounded_qa.data.store_files`.

**Pipeline**, in order (each step is a function below):

1. **Load** ``kg.csv`` into duckdb with each row's position in the file
   (:func:`_load_raw`). PrimeKG stores every fact twice, ``x→y`` then (later in the
   file) ``y→x``.
2. **Label** each row (:func:`_check_labels`). The label is PrimeKG's ``relation``,
   except ``drug_protein``, which covers four facts (target, enzyme, transporter,
   carrier) told apart only by ``display_relation``; it becomes
   ``drug_protein/<display_relation>``. The build fails unless every label has one
   ``display_relation`` and one pair of endpoint types.
3. **Deduplicate** to one row per (label, unordered node pair) (:func:`_canonical`),
   keeping the orientation of the pair's first row in file order and every raw row
   number.
4. **Select the subgraph** with the expansion test's definitions (:func:`_subgraph`):
   degree is distinct neighbours in the full graph; ``N1`` is the seed plus its
   neighbours; (c) keeps edges among ``N1`` plus every edge touching the seed or a
   1-hop node whose degree is at or below the chosen percentile.
5. **Entity IDs** (:func:`entity_ids`): the standard CURIE for each node's source
   ID (:func:`curie`; ``HPO`` ``2354`` → ``HP:0002354``, ``NCBI`` ``351`` →
   ``NCBIGene:351``), ``group:<slug of the name>`` for PrimeKG's grouped MONDO nodes
   (:func:`group_ids`), and ``primekg:<x_index>`` for CURIEs that collide. Computed
   over the whole graph, so a node's ID doesn't depend on the seed.
6. **Orient** each edge (:func:`orient`). Hierarchy relations become subject = child,
   object = parent, ``directed=True``, using PrimeKG's file-order convention. Other
   edges get a fixed, readable orientation (the relation name's first type is the
   subject; symmetric same-type relations order by ID) and stay undirected.
7. **Number** the edges ``E:000001``… in (relation, subject ID, object ID) order
   (:func:`assign_evidence_ids`).
8. **Write** the store files and ``build.json`` with the ``build_id``
   (:func:`compute_build_id`), counts and checks.

The same raw files and config always give byte-identical data files and the same
``build_id``. The subgraph SQL mirrors ``scripts/data/subgraph.py`` (the measurement
code, which isn't part of the package); ``scripts/data/check_store.py`` and the tests
check the two agree.

duckdb is needed only here: it's the package's optional ``build`` extra
(``pip install 'neuro-grounded-qa[build]'``). Loading a built store is pure Python.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from neuro_grounded_qa.data import store_files as sf

BUILDER_VERSION = "2.0.0"
"""Semantic version of this builder. Bump it with any change that can change a built
store's content; it is part of the ``build_id``."""

GRAPH = "primekg"
GRAPH_VERSION = "2.1"
KG_CSV = "kg.csv"
GROUPED_CSV = "kg_grouped_diseases.csv"
RAW_FILES = (KG_CSV, GROUPED_CSV)

# --------------------------------------------------------------------------------
# Normalization rules. All of them go into build.json and the build_id.
# --------------------------------------------------------------------------------

QUALIFIED_RELATIONS: tuple[str, ...] = ("drug_protein",)
"""Relations whose label is ``<relation>/<display_relation>``: one PrimeKG relation
that holds several different facts. The build checks every other relation has a
single ``display_relation``."""

HIERARCHY_CHILD_END: dict[str, str] = {
    "disease_disease": "y",
    "phenotype_phenotype": "y",
    "bioprocess_bioprocess": "y",
    "molfunc_molfunc": "y",
    "cellcomp_cellcomp": "y",
    "pathway_pathway": "y",
    "anatomy_anatomy": "x",
}
"""Hierarchy relations, and which end of a pair's **first** row in ``kg.csv`` file
order is the child. PrimeKG's build appends each edge's reversed copy after the
original, so the first copy keeps the source ontology's orientation: parent → child
(child = ``y``) for all of these except ``anatomy_anatomy``, whose build cell writes
child → parent (child = ``x``). Measured against OptimusKG's directed ontology edges
(at least 99.97% agreement; ``docs/question_set_design.md``, "Directed-relation
inventory"). These are stored subject = child, object = parent, ``directed=True``."""

UNDIRECTED_SAME_TYPE: tuple[str, ...] = ("protein_protein", "drug_drug", "exposure_exposure")
"""Same-type relations stored undirected. ``protein_protein`` and ``drug_drug`` are
symmetric. ``exposure_exposure`` is labelled parent-child but holds CTD
stressor/marker links, not a clean hierarchy, so it is not normalized (an open
question). Subject is the end with the smaller entity ID."""

SUBJECT_TYPE: dict[str, str] = {
    "drug_protein": "drug",
    "drug_effect": "drug",
    "indication": "drug",
    "contraindication": "drug",
    "off-label use": "drug",
    "disease_protein": "disease",
    "disease_phenotype_positive": "disease",
    "disease_phenotype_negative": "disease",
    "phenotype_protein": "effect/phenotype",
    "anatomy_protein_present": "anatomy",
    "anatomy_protein_absent": "anatomy",
    "bioprocess_protein": "biological_process",
    "cellcomp_protein": "cellular_component",
    "molfunc_protein": "molecular_function",
    "pathway_protein": "pathway",
    "exposure_protein": "exposure",
    "exposure_disease": "exposure",
    "exposure_bioprocess": "exposure",
    "exposure_molfunc": "exposure",
    "exposure_cellcomp": "exposure",
}
"""Relations between two node types: which type is the subject. Following the
relation's name (``drug_protein``: drug → protein) keeps tool output readable. These
edges stay undirected: the types already fix the reading, so a claim may state them
in either order."""

GROUPED_SOURCE = "MONDO_grouped"
GROUP_PREFIX = "group"
FALLBACK_PREFIX = "primekg"
MIN_ID_DIGITS = 6

SOURCE_PREFIXES: dict[str, dict[str, Any]] = {
    "CTD": {"prefix": "MESH", "pattern": "[CD][0-9]+"},
    "DrugBank": {"prefix": "DrugBank", "pattern": "DB[0-9]{5}"},
    "GO": {"prefix": "GO", "pattern": "[0-9]{1,7}", "zero_pad": 7},
    "HPO": {"prefix": "HP", "pattern": "[0-9]{1,7}", "zero_pad": 7},
    "MONDO": {"prefix": "MONDO", "pattern": "[0-9]{1,7}", "zero_pad": 7},
    "NCBI": {"prefix": "NCBIGene", "pattern": "[0-9]+"},
    "REACTOME": {"prefix": "REACT", "pattern": "R-HSA-[0-9]+"},
    "UBERON": {"prefix": "UBERON", "pattern": "[0-9]{1,7}", "zero_pad": 7},
}
"""PrimeKG ``x_source`` → the standard CURIE form of its IDs, for every source except
the grouped MONDO nodes (:func:`group_ids`). PrimeKG strips the ontologies' leading
zeros (``MONDO 7739``) and names sources its own way (``HPO``, ``NCBI``, ``CTD``); a
model that knows the standard form (``MONDO:0007739``, ``HP:0002354``,
``NCBIGene:351``) may "correct" an ID and then fail exact match, so the store uses the
standard form. The prefix is each namespace's most common form in OBO, Bioregistry
and Biolink use: ``HP`` (not ``HPO``), ``NCBIGene`` for NCBI Gene IDs, ``MESH`` for
CTD's chemical IDs (they are MeSH IDs, e.g. ``D007854``), ``REACT`` for Reactome;
``DrugBank`` keeps PrimeKG's spelling. ``zero_pad`` restores the ontology's 7-digit
local IDs. ``pattern`` is what the raw ``x_id`` must match; a mismatch, or a source
not listed here, fails the build instead of being guessed."""

RULES: dict[str, Any] = {
    "deduplicate": "one edge per (relation label, unordered node pair); PrimeKG's reversed copies collapse",
    "relation_label": {
        "form": "<relation>, or <relation>/<display_relation> for the qualified relations",
        "qualified": list(QUALIFIED_RELATIONS),
    },
    "hierarchy": {
        "convention": "subject = child (narrower), object = parent (broader), directed = true",
        "child_end_of_first_row_in_file_order": HIERARCHY_CHILD_END,
    },
    "undirected_same_type": {"relations": list(UNDIRECTED_SAME_TYPE), "subject": "smaller entity ID"},
    "subject_type": SUBJECT_TYPE,
    "entity_id": {
        "form": "standard CURIE <prefix>:<x_id, zero-padded where the ontology pads>",
        "prefixes": SOURCE_PREFIXES,
        "grouped": "group:<slug>; slug = node name lowercased, each run of characters outside "
        "[a-z0-9] -> '_', leading/trailing '_' stripped; nodes sharing a slug get _2, _3, ... "
        "in order of (smallest member MONDO number, x_index), the first keeps the bare slug",
        "collision_fallback": "primekg:<x_index>, for every node whose CURIE is shared with another node",
    },
    "evidence_id": {"form": "E:<zero-padded number>", "min_digits": MIN_ID_DIGITS,
                    "order": ["relation", "subject_id", "object_id"]},
}


# --------------------------------------------------------------------------------
# Seeds
# --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Seed:
    """One seed node: PrimeKG ``x_index``, its expected name (checked at build time,
    so a wrong ID fails loudly), and the disease group it stands for."""

    index: str
    name: str
    group: str


NAME_RULE_SEEDS: tuple[Seed, ...] = (
    Seed("28780", "Alzheimer disease", "AD"),
    Seed("83960", "Alzheimer disease without neurofibrillary tangles", "AD"),
    Seed("32654", "familial Alzheimer disease", "AD"),
    Seed("31527", "Alzheimer disease, familial early-onset, with coexisting amyloid and prion pathology", "AD"),
    Seed("94982", "Alzheimer disease, susceptibility to, mitochondrial", "AD"),
    Seed("28547", "Parkinson disease", "PD"),
    Seed("33241", "hereditary late onset Parkinson disease", "PD"),
    Seed("28801", "juvenile onset Parkinson disease 19A", "PD"),
    Seed("27576", "autosomal recessive Parkinson disease", "PD"),
    Seed("32468", "postencephalitic Parkinson disease", "PD"),
    Seed("28182", "Parkinson disease, autosomal dominant, susceptibility to", "PD"),
    Seed("32001", "parkinson disease, autosomal dominant", "PD"),
    Seed("35691", "frontotemporal dementia", "FTD"),
    Seed("28751", "inclusion body myopathy with Paget disease of bone and frontotemporal dementia", "FTD"),
    Seed("32688", "behavioral variant of frontotemporal dementia", "FTD"),
    Seed("27248", "amyotrophic lateral sclerosis", "ALS"),
    Seed("31996", "juvenile amyotrophic lateral sclerosis", "ALS"),
    Seed("38139", "familial amyotrophic lateral sclerosis", "ALS"),
    Seed("29634", "amyotrophic lateral sclerosis with polyglucosan bodies", "ALS"),
    Seed("31245", "Huntington disease", "HD"),
    Seed("33341", "juvenile Huntington disease", "HD"),
    Seed("29296", "Lewy body dementia", "DLB"),
)
"""The seed the name rule gives for the six diseases (each main node plus its
``disease_disease`` neighbours whose name contains the main node's name), minus
``38307`` "Huntington disease and related disorders" (a parent of HD). This is the
seed the expansion test measured; it is kept to reproduce those counts."""

ADDED_SEEDS: tuple[Seed, ...] = (
    Seed("28473", "Pick disease", "FTD"),
    Seed("28467", "Grn-related frontotemporal lobar degeneration with Tdp43 inclusions", "FTD"),
    Seed("30093", "parkinsonian-pyramidal syndrome", "PD"),
    Seed("29670", "Kufor-Rakeb syndrome", "PD"),
    Seed("28708", "frontotemporal dementia and/or amyotrophic lateral sclerosis", "FTD/ALS"),
    Seed("32696", "frontotemporal dementia with motor neuron disease", "FTD/ALS"),
)
"""Subtypes the name rule missed (children by file order and in MONDO), plus the two
combined FTD/ALS nodes. Settled 2026-11-19; logged as a seed-rule change."""

SEED_SETS: dict[str, tuple[Seed, ...]] = {
    "neuro": NAME_RULE_SEEDS + ADDED_SEEDS,
    "neuro-name-rule": NAME_RULE_SEEDS,
}
"""Named seed sets. ``neuro`` is the store's seed; ``neuro-name-rule`` reproduces the
measured subgraph for comparison."""


@dataclass(frozen=True)
class BuildConfig:
    """What to build. Everything here goes into ``build.json`` and the ``build_id``."""

    seeds: tuple[Seed, ...]
    seed_set: str = "custom"
    subgraph: str = "c"
    """``a`` (1-hop, induced on N1), ``b`` (every edge touching N1) or ``c`` (as b,
    expanding only through 1-hop nodes at or below the degree percentile)."""
    percentile: int = 95
    """Degree percentile for (c); ignored for a and b."""

    def subgraph_def(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.subgraph}
        if self.subgraph == "c":
            d["degree_percentile"] = self.percentile
        return d


# --------------------------------------------------------------------------------
# Pure helpers (tested directly)
# --------------------------------------------------------------------------------


def relation_label(relation: str, display_relation: str) -> str:
    """The store's relation label for a raw row."""
    return f"{relation}/{display_relation}" if relation in QUALIFIED_RELATIONS else relation


def curie(source: str, x_id: str) -> str:
    """The standard CURIE for a PrimeKG node's ``x_source`` and ``x_id``.

    Uses :data:`SOURCE_PREFIXES`: ``curie("HPO", "2354") == "HP:0002354"``,
    ``curie("NCBI", "351") == "NCBIGene:351"``.

    Raises:
        ValueError: for a source with no rule (including ``MONDO_grouped``, which
            :func:`group_ids` handles) or an ``x_id`` that doesn't match the source's
            pattern.
    """
    rule = SOURCE_PREFIXES.get(source)
    if rule is None:
        raise ValueError(f"no CURIE rule for source {source!r}")
    if not re.fullmatch(rule["pattern"], x_id):
        raise ValueError(f"{source} ID {x_id!r} doesn't match {rule['pattern']!r}")
    local = x_id.zfill(rule["zero_pad"]) if "zero_pad" in rule else x_id
    return f"{rule['prefix']}:{local}"


def slugify(name: str) -> str:
    """A group name as an ID slug: lowercase, each run of characters outside ``a-z0-9``
    replaced by ``_``, leading and trailing ``_`` removed.
    ``"Parkinson disease, autosomal dominant"`` → ``"parkinson_disease_autosomal_dominant"``.

    Raises:
        ValueError: if nothing is left (a name with no letters or digits).
    """
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not slug:
        raise ValueError(f"group name {name!r} has no letters or digits to make a slug")
    return slug


def _member_key(x_id: str, index: str) -> tuple[int, int, str]:
    """Order for groups sharing a slug: smallest member MONDO number, then x_index."""
    members = [int(m) for m in x_id.split("_") if m.isdigit()]
    return (min(members) if members else sys.maxsize, int(index) if index.isdigit() else sys.maxsize, index)


def group_ids(groups: Iterable[tuple[str, str, str]]) -> dict[str, tuple[str, int]]:
    """IDs for PrimeKG's grouped MONDO nodes: ``group:<slug of the node name>``.

    PrimeKG merges MONDO diseases with the same name into one node whose ``x_id``
    joins their numbers (``4975_11913_…``), too long to copy and not one MONDO term,
    so the ID comes from the name instead (``group:alzheimer_disease``) and the prefix
    keeps it from posing as a MONDO ID. If several groups share a slug, they are
    ordered by (smallest member MONDO number, ``x_index``); the first keeps the bare
    slug and the rest get ``_2``, ``_3``…, skipping any suffix another slug already
    uses.

    Args:
        groups: ``(x_index, x_id, x_name)`` for every grouped node in the graph.

    Returns:
        ``x_index`` → ``(entity_id, suffix)``, where suffix is 0 for a bare slug and
        2, 3, … for a suffixed one.
    """
    by_slug: dict[str, list[tuple[str, str]]] = {}
    for index, x_id, name in groups:
        by_slug.setdefault(slugify(name), []).append((index, x_id))
    taken = set(by_slug)
    out: dict[str, tuple[str, int]] = {}
    for slug in sorted(by_slug):
        ordered = sorted(by_slug[slug], key=lambda g: _member_key(g[1], g[0]))
        out[ordered[0][0]] = (f"{GROUP_PREFIX}:{slug}", 0)
        k = 1
        for index, _x_id in ordered[1:]:
            k += 1
            while f"{slug}_{k}" in taken:
                k += 1
            taken.add(f"{slug}_{k}")
            out[index] = (f"{GROUP_PREFIX}:{slug}_{k}", k)
    return out


def group_members(x_id: str) -> list[str]:
    """A grouped node's member MONDO terms as CURIEs, in PrimeKG's order."""
    return [curie("MONDO", m) for m in x_id.split("_")]


def entity_ids(nodes: Iterable[tuple[str, str, str, str]]) -> dict[str, tuple[str, str]]:
    """Assign every node its store entity ID.

    Args:
        nodes: ``(x_index, x_id, x_source, x_name)`` for every node in the graph.

    Returns:
        ``x_index`` → ``(entity_id, scheme)``, where scheme is ``curie``
        (:func:`curie`), ``group`` (:func:`group_ids`) or ``fallback``
        (``primekg:<x_index>``, used for every node whose CURIE is shared with
        another node; PrimeKG has a few, mostly one gene under an old and a new
        symbol).

    Raises:
        ValueError: from :func:`curie` or :func:`slugify` for a node no rule covers,
            or if the final IDs are not unique.
    """
    nodes = list(nodes)
    groups = group_ids((i, x, n) for i, x, s, n in nodes if s == GROUPED_SOURCE)
    preferred = {i: curie(s, x) for i, x, s, _n in nodes if s != GROUPED_SOURCE}
    counts = Counter(preferred.values())
    out = {i: (eid, "group") for i, (eid, _k) in groups.items()}
    for index, eid in preferred.items():
        out[index] = (eid, "curie") if counts[eid] == 1 else (f"{FALLBACK_PREFIX}:{index}", "fallback")
    final = Counter(eid for eid, _ in out.values())
    dupes = [eid for eid, n in final.items() if n > 1]
    if dupes:
        raise ValueError(f"entity IDs not unique after fallback: {dupes[:5]}")
    return out


def orient(
    relation: str,
    first: tuple[str, str],
    ids: Mapping[str, str],
    types: Mapping[str, str],
) -> tuple[str, str, bool]:
    """Subject, object and ``directed`` for one deduplicated edge.

    Args:
        relation: PrimeKG's base relation (not the qualified label).
        first: ``(x_index, y_index)`` of the pair's first row in file order.
        ids: ``x_index`` → entity ID.
        types: ``x_index`` → node type.

    Returns:
        ``(subject_index, object_index, directed)``.

    Raises:
        ValueError: for a relation no rule covers, or endpoint types that don't fit
            the rule (both caught at build time rather than guessed).
    """
    x, y = first
    if relation in HIERARCHY_CHILD_END:
        if types[x] != types[y]:
            raise ValueError(f"{relation} edge {first} joins {types[x]} and {types[y]}")
        child, parent = (y, x) if HIERARCHY_CHILD_END[relation] == "y" else (x, y)
        return child, parent, True
    if relation in UNDIRECTED_SAME_TYPE:
        s, o = sorted((x, y), key=lambda n: ids[n])
        return s, o, False
    if relation in SUBJECT_TYPE:
        st = SUBJECT_TYPE[relation]
        if types[x] == st and types[y] != st:
            return x, y, False
        if types[y] == st and types[x] != st:
            return y, x, False
        raise ValueError(f"{relation} edge {first}: types {types[x]}, {types[y]}; expected one {st}")
    raise ValueError(f"no orientation rule for relation {relation!r}")


def assign_evidence_ids(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort edges by (relation, subject, object) and give them ``E:`` IDs in that order.

    ``edges`` are dicts with ``relation``, ``subject`` and ``object`` (entity IDs).
    Python's string sort (by code point) fixes the order, independent of locale or
    database collation. Numbers start at 1 and are zero-padded to at least
    :data:`MIN_ID_DIGITS` digits. Returns new dicts with ``id`` first.

    Raises:
        ValueError: if two edges share (relation, subject, object).
    """
    ordered = sorted(edges, key=lambda e: (e["relation"], e["subject"], e["object"]))
    keys = [(e["relation"], e["subject"], e["object"]) for e in ordered]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate (relation, subject, object) among edges")
    width = max(MIN_ID_DIGITS, len(str(len(ordered))))
    return [{"id": f"E:{i:0{width}d}", **e} for i, e in enumerate(ordered, start=1)]


def compute_build_id(raw_sha256: Mapping[str, str], config: BuildConfig) -> tuple[str, dict[str, Any]]:
    """The ``build_id`` and the exact inputs it hashes.

    It hashes the raw files' SHA-256, the seed IDs (sorted), the subgraph definition,
    :data:`BUILDER_VERSION` and :data:`RULES`, as canonical JSON (sorted keys). Any
    change to the data, seed, definition or normalization changes it. Cheap: it
    needs the raw files' hashes, not their contents parsed.
    """
    basis = {
        "graph": GRAPH,
        "graph_version": GRAPH_VERSION,
        "raw_sha256": dict(sorted(raw_sha256.items())),
        "seeds": sorted(s.index for s in config.seeds),
        "subgraph": config.subgraph_def(),
        "builder_version": BUILDER_VERSION,
        "rules": RULES,
    }
    digest = hashlib.sha256(
        json.dumps(basis, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return f"{GRAPH}-{GRAPH_VERSION}-{digest[:12]}", basis


# --------------------------------------------------------------------------------
# duckdb steps
# --------------------------------------------------------------------------------


def _duckdb():
    try:
        import duckdb
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise SystemExit(
            "Building the store needs duckdb: install the package's build extra "
            "(pip install 'neuro-grounded-qa[build]') or, in this repo, `uv sync`."
        ) from e
    return duckdb


def _sql_path(p: Path) -> str:
    return str(p).replace("'", "''")


def _load_raw(con, kg_csv: Path) -> int:
    """Load ``kg.csv`` into ``raw`` (one row per file row, ``rn`` = 1-based data row)
    and ``nodes`` (one row per ``x_index``). Returns the raw row count.

    ``rn`` follows the scan order of ``read_csv``, which duckdb keeps (insertion
    order is preserved by default); ``scripts/data/check_store.py`` re-reads the
    file in Python and checks every recorded row number.
    """
    src = (
        f"read_csv('{_sql_path(kg_csv)}', all_varchar=true, header=true, "
        "quote='\"', escape='\"', delim=',')"
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE raw AS
        SELECT row_number() OVER () AS rn, relation, display_relation,
               x_index, x_type, y_index, y_type
        FROM {src}
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE nodes AS
        SELECT DISTINCT idx, xid, src, typ, nm FROM (
            SELECT x_index AS idx, x_id AS xid, x_source AS src, x_type AS typ, x_name AS nm FROM {src}
            UNION ALL
            SELECT y_index, y_id, y_source, y_type, y_name FROM {src}
        )
        """
    )
    bad = con.execute(
        "SELECT idx FROM nodes GROUP BY idx HAVING count(*) > 1 LIMIT 5"
    ).fetchall()
    if bad:
        raise ValueError(f"node indices with more than one attribute set: {[b[0] for b in bad]}")
    return con.execute("SELECT count(*) FROM raw").fetchone()[0]


def _check_labels(con) -> dict[str, Any]:
    """Add ``label`` to ``raw`` and verify the label scheme is unambiguous.

    Fails unless: every relation outside :data:`QUALIFIED_RELATIONS` has exactly one
    ``display_relation``; every label has one ``display_relation`` and one unordered
    pair of endpoint types; and every base relation has an orientation rule.
    """
    multi = con.execute(
        "SELECT relation FROM raw GROUP BY relation HAVING count(DISTINCT display_relation) > 1"
    ).fetchall()
    unexpected = sorted({r[0] for r in multi} - set(QUALIFIED_RELATIONS))
    if unexpected:
        raise ValueError(f"relations with several display_relation values but no qualifier: {unexpected}")
    con.execute("ALTER TABLE raw ADD COLUMN label VARCHAR")
    con.execute(
        """
        UPDATE raw SET label = CASE WHEN list_contains(?::VARCHAR[], relation)
                                    THEN relation || '/' || display_relation ELSE relation END
        """,
        [list(QUALIFIED_RELATIONS)],
    )
    rows = con.execute(
        """
        SELECT label, count(DISTINCT relation), count(DISTINCT display_relation),
               count(DISTINCT least(x_type, y_type) || ' | ' || greatest(x_type, y_type)),
               any_value(least(x_type, y_type) || ' | ' || greatest(x_type, y_type))
        FROM raw GROUP BY label ORDER BY label
        """
    ).fetchall()
    ambiguous = [r[0] for r in rows if r[1] != 1 or r[2] != 1 or r[3] != 1]
    if ambiguous:
        raise ValueError(f"ambiguous relation labels: {ambiguous}")
    bases = {r[0] for r in con.execute("SELECT DISTINCT relation FROM raw").fetchall()}
    uncovered = sorted(bases - set(HIERARCHY_CHILD_END) - set(UNDIRECTED_SAME_TYPE) - set(SUBJECT_TYPE))
    if uncovered:
        raise ValueError(f"relations with no orientation rule: {uncovered}")
    return {
        "labels": len(rows),
        "label_endpoint_types": {r[0]: r[4] for r in rows},
        "relations_qualified": sorted(r[0] for r in multi),
    }


def _canonical(con) -> dict[str, int]:
    """Build ``canon``: one row per (label, unordered pair), with the first row's
    orientation (``first_x``, ``first_y``) and every raw row number (``raw_rows``)."""
    con.execute(
        """
        CREATE OR REPLACE TABLE canon AS
        SELECT label, any_value(relation) AS relation, any_value(display_relation) AS display_relation,
               least(x_index, y_index) AS a, greatest(x_index, y_index) AS b,
               arg_min(x_index, rn) AS first_x, arg_min(y_index, rn) AS first_y,
               list(rn ORDER BY rn) AS raw_rows
        FROM raw
        GROUP BY label, least(x_index, y_index), greatest(x_index, y_index)
        """
    )
    n_canon = con.execute("SELECT count(*) FROM canon").fetchone()[0]
    not_two = con.execute("SELECT count(*) FROM canon WHERE len(raw_rows) <> 2").fetchone()[0]
    not_reversed = con.execute(
        """
        SELECT count(*) FROM canon c WHERE len(raw_rows) = 2 AND NOT EXISTS (
            SELECT 1 FROM raw r WHERE r.rn = c.raw_rows[2] AND r.x_index = c.first_y AND r.y_index = c.first_x)
        """
    ).fetchone()[0]
    loops = con.execute("SELECT count(*) FROM canon WHERE a = b").fetchone()[0]
    return {
        "canonical_edges": n_canon,
        "pairs_without_exactly_two_rows": not_two,
        "pairs_whose_second_row_is_not_the_reverse": not_reversed,
        "self_loops": loops,
    }


def _subgraph(con, config: BuildConfig) -> dict[str, Any]:
    """Build ``deg``, ``seed``, ``n1`` and ``sub`` (the selected ``canon`` rows).

    Mirrors ``scripts/data/subgraph.py``: degree = distinct neighbours over all
    relations in the full graph; the cap is the discrete percentile of that
    distribution over all nodes; seed nodes are never pruned; edges among N1 stay.
    """
    con.execute(
        """
        CREATE OR REPLACE TABLE adj AS
        SELECT a AS n, b AS m FROM canon WHERE a <> b
        UNION ALL SELECT b AS n, a AS m FROM canon WHERE a <> b
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE deg AS
        SELECT nodes.idx, coalesce(d.degree, 0) AS degree FROM nodes
        LEFT JOIN (SELECT n, count(DISTINCT m) AS degree FROM adj GROUP BY n) d ON d.n = nodes.idx
        """
    )
    seed_ids = [s.index for s in config.seeds]
    found = dict(con.execute(
        "SELECT idx, nm FROM nodes WHERE list_contains(?::VARCHAR[], idx)", [seed_ids]
    ).fetchall())
    problems = [
        f"{s.index} (expected {s.name!r}, found {found.get(s.index)!r})"
        for s in config.seeds if found.get(s.index) != s.name
    ]
    if problems:
        raise ValueError("seed nodes missing or renamed: " + "; ".join(problems))
    con.execute("CREATE OR REPLACE TABLE seed AS SELECT DISTINCT unnest(?::VARCHAR[]) AS idx", [seed_ids])
    con.execute(
        """
        CREATE OR REPLACE TABLE n1 AS
        SELECT idx FROM seed UNION SELECT adj.m FROM adj JOIN seed ON adj.n = seed.idx
        """
    )
    info: dict[str, Any] = {"n1_nodes": con.execute("SELECT count(*) FROM n1").fetchone()[0]}
    if config.subgraph == "a":
        where = "c.a IN (SELECT idx FROM n1) AND c.b IN (SELECT idx FROM n1)"
    elif config.subgraph == "b":
        where = "c.a IN (SELECT idx FROM n1) OR c.b IN (SELECT idx FROM n1)"
    elif config.subgraph == "c":
        cap = int(con.execute(
            "SELECT quantile_disc(degree, ?) FROM deg", [config.percentile / 100.0]
        ).fetchone()[0])
        con.execute(
            """
            CREATE OR REPLACE TABLE expand AS
            SELECT idx FROM seed UNION SELECT n1.idx FROM n1 JOIN deg USING (idx) WHERE deg.degree <= ?
            """,
            [cap],
        )
        info["degree_cap"] = cap
        info["expanded_nodes"] = con.execute("SELECT count(*) FROM expand").fetchone()[0]
        where = (
            "(c.a IN (SELECT idx FROM n1) AND c.b IN (SELECT idx FROM n1))"
            " OR c.a IN (SELECT idx FROM expand) OR c.b IN (SELECT idx FROM expand)"
        )
    else:
        raise ValueError(f"unknown subgraph kind {config.subgraph!r}")
    con.execute(f"CREATE OR REPLACE TABLE sub AS SELECT * FROM canon c WHERE {where}")
    return info


def _read_member_names(grouped_csv: Path) -> dict[str, str]:
    """MONDO number → name, from ``kg_grouped_diseases.csv`` (small; plain csv). Used
    to name each grouped node's members in the entity provenance file."""
    with grouped_csv.open(encoding="utf-8", newline="") as f:
        return {row["node_id"]: row["node_name"] for row in csv.DictReader(f)}


def _raw_hashes(raw_dir: Path, manifest: Path | None) -> dict[str, str]:
    """SHA-256 of each raw file, checked against the download manifest if given."""
    hashes = {name: sf.file_sha256(raw_dir / name) for name in RAW_FILES}
    if manifest is not None and manifest.is_file():
        files = json.loads(manifest.read_text(encoding="utf-8"))["datasets"][GRAPH]["files"]
        expected = {f["name"]: f["sha256"] for f in files}
        wrong = [n for n in RAW_FILES if expected.get(n) != hashes[n]]
        if wrong:
            raise ValueError(f"raw files differ from {manifest}: {wrong}")
    return hashes


# --------------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------------


def build_store(
    raw_dir: Path, out_dir: Path, config: BuildConfig, manifest: Path | None = None
) -> dict[str, Any]:
    """Build the store from ``raw_dir`` into ``out_dir``; return the ``build.json`` dict.

    ``out_dir`` is created if needed. An existing ``build.json`` there is removed
    first and written last, so an interrupted build never looks like a valid store.
    """
    duckdb = _duckdb()
    raw_dir, out_dir = Path(raw_dir), Path(out_dir)
    raw_sha = _raw_hashes(raw_dir, manifest)
    build_id, basis = compute_build_id(raw_sha, config)

    con = duckdb.connect()
    raw_rows = _load_raw(con, raw_dir / KG_CSV)
    labels = _check_labels(con)
    canon = _canonical(con)
    sub_info = _subgraph(con, config)

    # Entity IDs over the whole graph, so a node's ID doesn't depend on the seed.
    member_names = _read_member_names(raw_dir / GROUPED_CSV)
    all_nodes = con.execute("SELECT idx, xid, src, typ, nm FROM nodes").fetchall()
    ids = entity_ids((i, x, s, n) for i, x, s, _t, n in all_nodes)
    group_suffixed = sum(1 for _eid, k in group_ids(
        (i, x, n) for i, x, s, _t, n in all_nodes if s == GROUPED_SOURCE).values() if k)
    attrs = {i: (x, s, t, n) for i, x, s, t, n in all_nodes}
    types = {i: t for i, (_x, _s, t, _n) in attrs.items()}
    id_of = {i: eid for i, (eid, _scheme) in ids.items()}

    sub_rows = con.execute(
        "SELECT label, relation, display_relation, first_x, first_y, raw_rows FROM sub"
    ).fetchall()
    seed_idx = {s.index for s in config.seeds}
    con.close()

    edges, provenance = [], {}
    for label, relation, display, fx, fy, rows in sub_rows:
        s, o, directed = orient(relation, (fx, fy), id_of, types)
        edge = {"relation": label, "subject": id_of[s], "object": id_of[o],
                "display_relation": display, "directed": directed}
        edges.append(edge)
        provenance[(label, id_of[s], id_of[o])] = {
            "primekg_relation": relation, "display_relation": display, "kg_csv_rows": list(rows),
            "subject_index": s, "object_index": o,
        }
    numbered = assign_evidence_ids(edges)
    edge_records = [
        {"id": e["id"], "subject": e["subject"], "relation": e["relation"],
         "display_relation": e["display_relation"], "object": e["object"], "directed": e["directed"]}
        for e in numbered
    ]
    edge_sources = [
        {"id": e["id"], **provenance[(e["relation"], e["subject"], e["object"])]} for e in numbered
    ]

    store_idx = {p[k] for p in provenance.values() for k in ("subject_index", "object_index")} | seed_idx
    entity_rows = sorted(store_idx, key=lambda i: id_of[i])
    entity_records = [
        {"id": id_of[i], "name": attrs[i][3], "type": attrs[i][2], "source": attrs[i][1]} for i in entity_rows
    ]
    entity_sources = []
    for i in entity_rows:
        x_id, src, _t, _n = attrs[i]
        rec: dict[str, Any] = {"id": id_of[i], "primekg_index": i, "x_source": src, "x_id": x_id,
                               "id_scheme": ids[i][1]}
        if src == GROUPED_SOURCE:
            rec["members"] = [{"id": c, "name": member_names.get(n)}
                              for n, c in zip(x_id.split("_"), group_members(x_id), strict=True)]
        entity_sources.append(rec)

    # Write: data files first, build.json last.
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / sf.BUILD_JSON).unlink(missing_ok=True)
    hashes = {
        sf.ENTITIES: sf.write_jsonl(out_dir / sf.ENTITIES, entity_records),
        sf.EDGES: sf.write_jsonl(out_dir / sf.EDGES, edge_records),
        sf.ENTITY_SOURCES: sf.write_jsonl(out_dir / sf.ENTITY_SOURCES, entity_sources),
        sf.EDGE_SOURCES: sf.write_jsonl(out_dir / sf.EDGE_SOURCES, edge_sources),
    }

    schemes_graph = Counter(scheme for _eid, scheme in ids.values())
    schemes_store = Counter(ids[i][1] for i in entity_rows)
    by_rel = Counter(e["relation"] for e in edge_records)
    facts = [e for e in edge_sources if e["subject_index"] in seed_idx or e["object_index"] in seed_idx]
    meta = {
        "format": sf.FORMAT,
        "build_id": build_id,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "graph": {"name": "PrimeKG", "version": GRAPH_VERSION,
                  "doi": "10.7910/DVN/IXA7BM", "license": "CC0 1.0 (dataset); see README for source terms"},
        "builder_version": BUILDER_VERSION,
        "config": {
            "seed_set": config.seed_set,
            "seeds": [{"primekg_index": s.index, "id": id_of[s.index], "name": s.name, "group": s.group}
                      for s in config.seeds],
            "subgraph": {**config.subgraph_def(), **({"degree_cap": sub_info["degree_cap"]}
                                                     if "degree_cap" in sub_info else {})},
            "rules": RULES,
        },
        "raw_files": {name: {"sha256": h} for name, h in raw_sha.items()},
        "build_id_basis": basis,
        "counts": {
            "raw_rows": raw_rows,
            "graph_nodes": len(all_nodes),
            "graph_canonical_edges": canon["canonical_edges"],
            "n1_nodes": sub_info["n1_nodes"],
            **({"expanded_nodes": sub_info["expanded_nodes"]} if "expanded_nodes" in sub_info else {}),
            "nodes": len(entity_records),
            "edges": len(edge_records),
            "directed_edges": sum(e["directed"] for e in edge_records),
            "single_hop_facts": len(facts),
            "edges_by_relation": dict(sorted(by_rel.items())),
            "entity_id_schemes_graph": dict(sorted(schemes_graph.items())),
            "entity_id_schemes_store": dict(sorted(schemes_store.items())),
            "group_ids_suffixed_graph": group_suffixed,
        },
        "checks": {
            **{k: v for k, v in canon.items() if k != "canonical_edges"},
            "labels": labels["labels"],
            "relations_qualified": labels["relations_qualified"],
            "label_endpoint_types": labels["label_endpoint_types"],
        },
        "files": {name: {"sha256": h} for name, h in hashes.items()},
    }
    sf.write_build_meta(out_dir, meta)
    return meta


def main(argv: list[str] | None = None) -> None:
    """Command line: build a store directory."""
    data_root = os.environ.get("NGQ_DATA_DIR")
    p = argparse.ArgumentParser(prog="python -m neuro_grounded_qa.data.build", description=__doc__.split("\n\n")[0])
    p.add_argument("--out", type=Path, required=True, help="store directory to write")
    p.add_argument("--raw", type=Path, default=Path(data_root) / "raw" / GRAPH if data_root else None,
                   help="directory with kg.csv and kg_grouped_diseases.csv (default $NGQ_DATA_DIR/raw/primekg)")
    p.add_argument("--manifest", type=Path,
                   default=Path(data_root) / "meta" / "manifest.json" if data_root else None,
                   help="download manifest to check raw SHA-256 against (default $NGQ_DATA_DIR/meta/manifest.json)")
    p.add_argument("--seed-set", choices=sorted(SEED_SETS), default="neuro")
    p.add_argument("--subgraph", choices=["a", "b", "c"], default="c")
    p.add_argument("--percentile", type=int, default=95)
    p.add_argument("--force", action="store_true", help="overwrite an existing store directory")
    args = p.parse_args(argv)
    if args.raw is None:
        p.error("--raw is required when NGQ_DATA_DIR is unset")
    if (args.out / sf.BUILD_JSON).exists() and not args.force:
        p.error(f"{args.out} already holds a store; pass --force to rebuild it")
    if args.force and args.out.exists():
        for name in (*sf.DATA_FILES, sf.BUILD_JSON):
            (args.out / name).unlink(missing_ok=True)
    config = BuildConfig(seeds=SEED_SETS[args.seed_set], seed_set=args.seed_set,
                         subgraph=args.subgraph, percentile=args.percentile)
    meta = build_store(args.raw, args.out, config, args.manifest)
    c = meta["counts"]
    print(f"build_id {meta['build_id']}")
    print(f"{c['nodes']:,} nodes, {c['edges']:,} edges ({c['directed_edges']:,} directed), "
          f"{c['single_hop_facts']:,} single-hop facts -> {args.out}")


if __name__ == "__main__":
    main(sys.argv[1:])
