"""The question templates: wording, shape and relations, as data.

Each template is one question pattern. Its **shape** says how candidates are drawn
from the store and how gold is computed (``qgen/candidates.py`` implements the
shapes; the generator and the feasibility script share it). The **text** names
entities only by name, through ``{slots}``; IDs never appear in a question. This file
is the single template catalog: every wording the question set uses is here.

Wording rules (see "Question generation design" in ``docs/question_set_design.md``):

- **Shared patterns.** Every text pattern is used by at least two answerability
  classes, so the wording can't tell a model whether the graph supports an answer.
  ``evid`` reads the same for a true fact (single-hop) and a false one (false
  premise); ``list`` and ``bridge`` read the same with and without answers
  (unanswerable); ``evid2`` and ``list2`` read the same when both parts or one part
  are supported (single-hop vs partial).
- **No answer in the question.** List members and bridge intermediates are never
  named. ``qgen.checks.answer_leaks`` checks it per candidate.
- **Neutral verbs.** Each relation has one fixed phrase, close to its PrimeKG
  ``display_relation`` ("associated with", "indicated for", "linked to"), with no
  hedging ("is it true that...") or emphasis that would hint at the answer.
- **"Direct".** Relationship questions ask for a *direct* relation, so a path
  through a third entity is not an answer to them.
- **Typed mentions.** An entity whose name another store node shares (mostly a
  disease and a phenotype with one name) is named with its type, "the phenotype
  *X*" (:func:`mention`), when a search for the name, read by type, finds it
  unambiguously (``qgen.checks.resolve_mention``). Whether a mention is typed
  depends on the entity alone, never on the template or the answer, so it can't
  carry answerability. Where the wording already names the type ("has the
  phenotype {obj}", "the pathway {x}"), the bare name is used
  (:data:`TYPE_NAMED_BY_WORDING`).

The phrases are the only biology-facing wording in the set; Kaitlin reviews them
for domain correctness (open questions in the design doc).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

# --------------------------------------------------------------------------------------
# Relation phrases
# --------------------------------------------------------------------------------------

LIST_CLAUSE: dict[str, tuple[str, str]] = {
    # relation (seed role) -> (clause naming the seed, clause referring back with "it")
    "disease_protein": (
        "which genes or proteins are associated with {s}",
        "which genes or proteins are associated with it",
    ),
    "disease_phenotype_positive": (
        "which phenotypes are recorded as present in {s}",
        "which phenotypes are recorded as present in it",
    ),
    "disease_phenotype_negative": (
        "which phenotypes are recorded as absent in {s}",
        "which phenotypes are recorded as absent in it",
    ),
    "indication": ("which drugs are indicated for {s}", "which drugs are indicated for it"),
    "contraindication": (
        "which drugs are contraindicated in {s}",
        "which drugs are contraindicated in it",
    ),
    "off-label use": (
        "which drugs are used off-label for {s}",
        "which drugs are used off-label for it",
    ),
    "exposure_disease": ("which exposures are linked to {s}", "which exposures are linked to it"),
    # disease_disease is directed (subject = child). Two list readings:
    "disease_disease:children": (
        "which diseases are listed as subtypes of {s}",
        "which diseases are listed as subtypes of it",
    ),
    "disease_disease:parents": (
        "which diseases is {s} listed as a subtype of",
        "which diseases is it listed as a subtype of",
    ),
}
"""List clauses, keyed by relation (with a role suffix for the directed one)."""

EVID_STATEMENT: dict[str, str] = {
    # relation -> statement with {subj} and {obj} in the store's subject/object order
    "disease_protein": "{obj} is associated with {subj}",  # subject = disease
    "disease_phenotype_positive": "{subj} has the phenotype {obj}",
    "disease_phenotype_negative": "the phenotype {obj} is recorded as absent in {subj}",
    "indication": "{subj} is indicated for {obj}",  # subject = drug
    "contraindication": "{subj} is contraindicated in {obj}",
    "off-label use": "{subj} is used off-label for {obj}",
    "exposure_disease": "{subj} is linked to {obj}",  # subject = exposure
    "disease_disease": "{subj} is a subtype of {obj}",  # directed: subject = child
}
"""Statements asserting one fact, used by ``evid`` and ``evid2``."""

EVID_TEXT = "Which edge in the knowledge graph shows that {statement}?"
EVID2_TEXT = "Which edges in the knowledge graph show that {statement1}, and that {statement2}?"
REL_TEXT = "What direct relation does the knowledge graph record between {a} and {b}?"

ADMISSIBLE: dict[str, tuple[str, ...]] = {
    # partner type of a disease -> relations a disease and that type can share
    "gene/protein": ("disease_protein",),
    "effect/phenotype": ("disease_phenotype_positive", "disease_phenotype_negative"),
    "drug": ("indication", "contraindication", "off-label use"),
    "exposure": ("exposure_disease",),
    "disease": ("disease_disease",),
}
"""For relationship questions: the relations that can link a disease to each type.
A relationship answer is visibly complete only when each of these is checked."""

OPPOSITE: dict[str, str] = {
    "indication": "contraindication",
    "contraindication": "indication",
    "disease_phenotype_positive": "disease_phenotype_negative",
    "disease_phenotype_negative": "disease_phenotype_positive",
}
"""Relation pairs that contradict each other for the same two entities. A store edge
under one contradicts a premise stating the other (a correctable false premise)."""


# --------------------------------------------------------------------------------------
# Bridge (two-hop) patterns
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Bridge:
    """A two-hop pattern ``x -r_x- m -r_y- y``. ``x`` and ``y`` are named; the
    intermediates ``m`` are the answer.

    ``seed_end`` is the end that must be a seed node (``"x"``, ``"y"`` or ``"both"``
    when either may be). ``x_type``/``y_type``/``m_type`` are node types.
    ``m_role_x``/``m_role_y`` fix the intermediate's role on a directed edge
    (``"child"`` or ``"parent"``), None for undirected relations.
    """

    key: str
    text: str
    r_x: str
    r_y: str
    x_type: str
    m_type: str
    y_type: str
    seed_end: str
    m_role_x: str | None = None
    m_role_y: str | None = None


BRIDGES: tuple[Bridge, ...] = (
    Bridge(
        "drug_target_assoc",
        "Which genes or proteins targeted by {x} are associated with {y}?",
        "drug_protein/target", "disease_protein", "drug", "gene/protein", "disease", "y",
    ),
    Bridge(
        "ppi_assoc",
        "Which proteins that interact with {x} are associated with {y}?",
        "protein_protein", "disease_protein", "gene/protein", "gene/protein", "disease", "y",
    ),
    Bridge(
        "exposure_assoc",
        "Which genes or proteins that interact with {x} are associated with {y}?",
        "exposure_protein", "disease_protein", "exposure", "gene/protein", "disease", "y",
    ),
    Bridge(
        "pathway_assoc",
        "Which genes or proteins in the pathway {x} are associated with {y}?",
        "pathway_protein", "disease_protein", "pathway", "gene/protein", "disease", "y",
    ),
    Bridge(
        "indication_target",
        "Which drugs indicated for {x} target {y}?",
        "indication", "drug_protein/target", "disease", "drug", "gene/protein", "x",
    ),
    Bridge(
        "subtype_assoc",
        "Which subtypes of {x} are associated with {y}?",
        "disease_disease", "disease_protein", "disease", "disease", "gene/protein", "x",
        m_role_x="child",
    ),
    Bridge(
        "phenotype_kind",
        "Which phenotypes of {x} are listed as kinds of {y}?",
        "disease_phenotype_positive", "phenotype_phenotype", "disease", "effect/phenotype",
        "effect/phenotype", "x", m_role_y="child",
    ),
    Bridge(
        "shared_protein",
        "Which genes or proteins are associated with both {x} and {y}?",
        "disease_protein", "disease_protein", "disease", "gene/protein", "disease", "x",
    ),
    Bridge(
        "shared_phenotype",
        "Which phenotypes are recorded as present in both {x} and {y}?",
        "disease_phenotype_positive", "disease_phenotype_positive", "disease",
        "effect/phenotype", "disease", "x",
    ),
)


# --------------------------------------------------------------------------------------
# The catalog
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Template:
    """One question template.

    ``id`` is ``<family>.<key>``; ``qtype`` and ``answerable`` are what items from it
    carry; ``shape`` names the candidate generator; ``relation_key`` is what the
    40% per-relation cap groups by (a relation, a list role or a bridge pattern).
    """

    id: str
    qtype: str
    answerable: str
    shape: str
    relation_key: str


def catalog() -> tuple[Template, ...]:
    """Every template, in a fixed order."""
    t: list[Template] = []
    list_keys = list(LIST_CLAUSE)
    evid_rels = list(EVID_STATEMENT)
    # single-hop
    for k in list_keys:
        t.append(Template(f"sh_list.{k}", "single_hop", "yes", "list", k))
    for r in evid_rels:
        t.append(Template(f"sh_evid.{r}", "single_hop", "yes", "evid", r))
    for ptype in ADMISSIBLE:
        t.append(Template(f"sh_rel.{ptype}", "single_hop", "yes", "rel", ptype))
    for r in ("disease_protein", "disease_phenotype_positive", "exposure_disease"):
        t.append(Template(f"sh_evid2.{r}", "single_hop", "yes", "evid2", r))
    t.append(Template("sh_list2", "single_hop", "yes", "list2", "list2"))
    # multi-hop
    for b in BRIDGES:
        t.append(Template(f"mh_bridge.{b.key}", "multi_hop", "yes", "bridge", b.key))
    # partial
    for r in ("disease_protein", "disease_phenotype_positive", "exposure_disease"):
        t.append(Template(f"pa_evid2.{r}", "partial", "partial", "evid2", r))
    t.append(Template("pa_list2", "partial", "partial", "list2", "list2"))
    # unanswerable
    for k in list_keys:
        t.append(Template(f"un_list.{k}", "unanswerable", "no", "list", k))
    for ptype in ADMISSIBLE:
        t.append(Template(f"un_rel.{ptype}", "unanswerable", "no", "rel", ptype))
    for b in BRIDGES:
        t.append(Template(f"un_bridge.{b.key}", "unanswerable", "no", "bridge", b.key))
    # false premise
    for r in evid_rels:
        t.append(Template(f"fp_evid.{r}", "false_premise", "no", "evid", r))
    for r in OPPOSITE:
        t.append(Template(f"fp_contra.{r}", "false_premise", "correctable", "evid", r))
    t.append(Template("fp_reversed.disease_disease", "false_premise", "correctable", "evid", "disease_disease"))
    return tuple(t)


# --------------------------------------------------------------------------------------
# Typed mentions
# --------------------------------------------------------------------------------------

TYPE_WORD: dict[str, str] = {
    # store node type -> the word a question uses for it ("the <word> <name>")
    "disease": "disease",
    "effect/phenotype": "phenotype",
    "gene/protein": "gene or protein",
    "drug": "drug",
    "exposure": "exposure",
    "pathway": "pathway",
    "biological_process": "biological process",
    "molecular_function": "molecular function",
    "cellular_component": "cellular component",
    "anatomy": "anatomical structure",
}
"""How a typed mention names each node type. Covers every type in the store."""

TYPE_NAMED_BY_WORDING: dict[tuple[str, str], frozenset[str]] = {
    # (statement relation or bridge key, slot) pairs whose surrounding words already
    # name the entity's type, so the slot takes the bare name even when typed.
    ("disease_phenotype_positive", "obj"): frozenset({"effect/phenotype"}),  # "has the phenotype {obj}"
    ("disease_phenotype_negative", "obj"): frozenset({"effect/phenotype"}),  # "the phenotype {obj} is ..."
    ("pathway_assoc", "x"): frozenset({"pathway"}),  # "in the pathway {x}"
}
"""Slots where the wording names the type. Keyed by the statement's relation (for
``evid``/``evid2``) or the bridge key, and the slot (``subj``/``obj``/``x``/``y``)."""


def wording_names_type(key: str, slot: str, etype: str) -> bool:
    """Does the wording around ``slot`` of statement/bridge ``key`` already say ``etype``?"""
    return etype in TYPE_NAMED_BY_WORDING.get((key, slot), frozenset())


def mention(name: str, etype: str, typed: bool) -> str:
    """How a question names an entity: ``name``, or ``the <type word> <name>``."""
    return f"the {TYPE_WORD[etype]} {name}" if typed else name


def catalog_sha256() -> str:
    """SHA-256 over every wording and template definition in this file.

    Recorded in the item manifest, so an item set can be tied to the exact wording it
    was generated with. It hashes content (canonical JSON), not the file's bytes, so a
    comment edit doesn't change it and a wording edit always does.
    """
    content = {
        "list_clause": LIST_CLAUSE,
        "evid_statement": EVID_STATEMENT,
        "evid_text": EVID_TEXT,
        "evid2_text": EVID2_TEXT,
        "rel_text": REL_TEXT,
        "admissible": ADMISSIBLE,
        "opposite": OPPOSITE,
        "bridges": [asdict(b) for b in BRIDGES],
        "catalog": [asdict(t) for t in catalog()],
        "type_word": TYPE_WORD,
        "type_named_by_wording": {f"{k}|{s}": sorted(v) for (k, s), v in TYPE_NAMED_BY_WORDING.items()},
    }
    blob = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def cap_first(s: str) -> str:
    """Capitalize the first character only (entity names keep their own case)."""
    return s[:1].upper() + s[1:]


def list_text(key: str, s: str) -> str:
    return cap_first(LIST_CLAUSE[key][0].format(s=s)) + "?"


def list2_text(key1: str, key2: str, s: str) -> str:
    return cap_first(LIST_CLAUSE[key1][0].format(s=s)) + ", and " + LIST_CLAUSE[key2][1] + "?"


def evid_statement(relation: str, subj: str, obj: str) -> str:
    return EVID_STATEMENT[relation].format(subj=subj, obj=obj)


def evid_text(relation: str, subj: str, obj: str) -> str:
    return EVID_TEXT.format(statement=evid_statement(relation, subj, obj))


def evid2_text(relation: str, pairs: list[tuple[str, str]]) -> str:
    (s1, o1), (s2, o2) = pairs
    return EVID2_TEXT.format(
        statement1=evid_statement(relation, s1, o1), statement2=evid_statement(relation, s2, o2)
    )


def rel_text(a: str, b: str) -> str:
    return REL_TEXT.format(a=a, b=b)


def bridge_text(b: Bridge, x: str, y: str) -> str:
    return b.text.format(x=x, y=y)
