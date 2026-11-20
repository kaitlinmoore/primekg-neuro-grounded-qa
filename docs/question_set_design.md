# Question set design

This document holds the design of the question set. Its first part is the
**expansion test**: the criteria that decide whether an Alzheimer's-centered
subgraph holds enough material for the question set, or whether the domain
expands to broader neurodegeneration. The test is written down before any
question is drafted and before either graph is measured, so the domain choice
rests on what the graph contains rather than on model results.

## Expansion test

**Status: every threshold below is proposed, pending Kaitlin's approval.** If a
threshold changes after the measurement results are in, the change goes in
`docs/changelog.md` with its reason, and the results section says which version
of the test it was checked against.

### Sizing

The design brief's target mix, at 200 development items plus a held-out split:

- **Held-out size (proposed):** 100 items, so 300 items in all. The held-out
  split draws from the same candidate pool, so the pool has to cover both
  splits.
- **Headroom multiple (proposed): 3×.** Candidates are lost to deduplication,
  quality filtering (awkward or trivially guessable items), near-duplicate
  removal across the split boundary, and per-relation balancing. 3× leaves two
  candidates in reserve for every item kept.

| Question type | Share | Items (200 + 100) | Minimum candidates (3×) | Correct behavior |
|---|---|---|---|---|
| single-hop | 30% | 90 | 270 | answer, cite the edge |
| multi-hop | 25% | 75 | 225 | answer, cite every edge in the chain |
| partially answerable | 15% | 45 | 135 | answer the supported part, flag the rest |
| unanswerable | 20% | 60 | 180 | abstain |
| false premise | 10% | 30 | 90 | abstain or correct the premise |

### Definitions

- **Seed set.** The disease node(s) that stand for Alzheimer's disease in a
  graph. The seed is chosen by Kaitlin from the full candidate list in the
  results; the test is run for each seed set reported.
- **Edge.** One fact `(subject, relation, object)`. Edges are canonicalized
  before counting:
  - PrimeKG `kg.csv` stores each edge in both directions, so its edges are
    keyed as unordered pairs per relation.
  - OptimusKG marks direction per edge (`undirected`); undirected edges are
    keyed as unordered pairs, directed ones as ordered pairs.
  - A relation is identified by its full label: PrimeKG's `relation` column;
    OptimusKG's edge `label` plus `relation` (for example
    `DIS-GEN/ASSOCIATED_WITH`), because OptimusKG reuses relation strings
    across edge types.
  - Exact duplicate edges count once.
- **Degree.** The number of distinct neighbors of a node in the full graph,
  over all relations.
- **Subgraph definitions.** `N1` is the seed set plus every node one edge away.
  - **(a) 1-hop:** the subgraph induced on `N1` (all edges with both endpoints
    in `N1`).
  - **(b) 2-hop:** every edge on a walk of length ≤ 2 from the seed, that is,
    every edge with an endpoint in `N1`. Edges between two nodes that are both
    two hops out are not on any such walk and are excluded.
  - **(c) 2-hop, hub-pruned:** as (b), but the second hop expands only through
    1-hop nodes whose degree is at or below the 95th percentile of the full
    graph's degree distribution. Seed nodes are never pruned. Edges among `N1`
    stay. The 90th and 99th percentiles are also reported, as a sensitivity
    check. (The percentile is a measurement parameter, set here before any
    measurement; choosing it is Kaitlin's decision.)
- **Single-hop fact.** A distinct canonical edge with at least one endpoint in
  the seed set.
- **Two-hop path.** A distinct chain `s –r1– m –r2– t` inside the subgraph, with
  `s` in the seed set, `m` and `t` outside it, and `t ≠ m`. Its **pattern** is
  the relation pair `(r1, r2)`; its **endpoint** is `t`.
- **Code-verified absence.** A pair `(u, v)` has no edge between them under any
  relation **in the full graph**, not only in the subgraph. Checking against
  the full graph keeps an item from being "unanswerable" just because the
  subgraph cut the edge out.

### Criteria

Each criterion is checked per graph and per subgraph definition. "Gating"
criteria decide pass or fail; "reported" ones inform the decision but don't
gate it.

| ID | Criterion | Measure | Proposed threshold | Gating |
|---|---|---|---|---|
| C1 | Single-hop capacity | distinct single-hop facts | ≥ 270 | yes |
| C2 | Single-hop relation diversity | relations with ≥ 30 single-hop facts each; and the **balanced capacity**, Σ over relations of min(nᵣ, 0.4 × 270), the count still available if no relation may supply more than 40% of candidates | ≥ 4 relations, and balanced capacity ≥ 270 | yes |
| C3 | Multi-hop capacity | distinct two-hop paths; distinct endpoints | ≥ 225 paths and ≥ 225 endpoints | yes |
| C4 | Multi-hop pattern diversity | patterns `(r1, r2)` with ≥ 20 paths each; balanced capacity Σ min(n_p, 0.4 × 225) | ≥ 3 patterns, and balanced capacity ≥ 225 | yes |
| C5 | Hub dominance | share of distinct two-hop paths through the top intermediate `m`; share through the top 5 | top 1 ≤ 20%, top 5 ≤ 50% | yes |
| C6 | Partially answerable capacity | single-hop facts `(s, r, o)` that have at least one **absent sibling** `o'`: same type as `o`, inside the subgraph, with a code-verified absence between `s` and `o'` (so "s r o and s r o'?" has one supported and one unsupported part) | ≥ 135 | yes |
| C7 | Unanswerable capacity | pairs `(s, r, y)`: `s` in the seed set, `r` a relation `s` takes part in, `y` of the type `r` links `s` to, `y` inside the subgraph, code-verified absence between `s` and `y`. Counted as distinct `(r, y)` | ≥ 180, from ≥ 3 relations with ≥ 20 each | yes |
| C8 | False-premise capacity | (i) **graph-contradicted** pairs: the graph holds the opposite of a premise (a drug–disease `contraindication` edge where the premise asserts an indication, or the reverse; a "phenotype absent" or "expression absent" edge where the premise asserts presence), with at least one endpoint in `N1`; plus (ii) absence-based premises from the C7 pool, drawn disjoint from the unanswerable items | (i) + (ii) ≥ 90, with (i) ≥ 30 | yes |
| R1 | Shared-neighbor pairs | pairs `(s, u)` with `u` a disease node outside the seed set that shares ≥ 1 neighbor with `s`; distribution of shared-neighbor counts; pairs with 1 to 20 shared neighbors | none | reported |
| R2 | Tool-cap fit | for each seed node and relation, the number of neighbors (the fan-out a `get_neighbors` call would page through); share of single-hop facts in (node, relation) groups of ≤ 20 edges, the per-result cap | none | reported |
| R3 | Licensing | share of single-hop facts, and of subgraph edges, whose source is DrugBank, SIDER, DisGeNET or UMLS-derived | none | reported (informs the shipping decision) |
| R4 | Size | nodes by type, edges by relation with source databases, degree distribution and the top 20 hubs | none | reported |

C6 to C8 count candidates only. Whether a candidate reads as a natural question
is settled later, when items are drafted; the headroom multiple covers that
loss.

### Decision rule

1. Kaitlin chooses a graph, a seed set and a subgraph definition.
2. If the Alzheimer's-centered subgraph **passes every gating criterion** for
   that choice, the domain stays Alzheimer's disease.
3. If it **fails any gating criterion**, the seed set expands to **broader
   neurodegeneration**: Alzheimer's disease plus Parkinson disease,
   frontotemporal dementia, amyotrophic lateral sclerosis, Huntington disease
   and dementia with Lewy bodies, each with its subtypes under the same seed
   rules as Alzheimer's. The test is then rerun on the expanded seed.
4. If the expanded seed also fails, the result goes back to Kaitlin, who
   decides whether to change a threshold, the subgraph definition or the
   question mix. Any such change is logged.

Switching graph or subgraph definition to make a failing choice pass is
Kaitlin's call, not an automatic step of the rule.

## Expansion test: results

Checked on 2026-11-19 against the expansion test as committed above, with
every threshold still **proposed**. Produced by `scripts/data/measure.py` and
`scripts/data/report.py` from the files listed under "Data obtained"; the raw
and derived data stay in `NGQ_DATA_DIR` and are not committed. No question was
drafted, and no model was called.

How to read these results:

- **Seed sets.** Each graph is measured with the proposed seed set and with the
  main Alzheimer disease node alone. Choosing between them is Kaitlin's call.
- **Definition (c)** is reported at the 95th percentile, as the test sets out,
  with the 90th and 99th as a sensitivity check.
- **PrimeKG sources.** `kg.csv` has no per-edge provenance, so PrimeKG's
  sources (relation table, licensing shares) come from a relation-level
  mapping in `scripts/data/load.py`. Every `disease_protein` edge counts as
  DisGeNET.
- **Updated 2026-11-19: PrimeKG source mapping corrected, R3 recomputed.** The
  mapping was first written from memory. It has since been checked against the
  PrimeKG paper and build code (see "PrimeKG relation sources, verified"
  below), and one relation was wrong: `phenotype_protein` comes from DisGeNET,
  not from HPO annotations. Other entries gained sources without changing
  R3 (HPO for `disease_disease` and `disease_phenotype_positive`; the four PPI
  resources for `protein_protein`). In the PrimeKG tables below, the R3 rows,
  the relation table's Sources column and the licensing table were recomputed
  in place. Single-hop R3 is unchanged (56.3% proposed seed, 43.3% main node
  alone), because no seed fact is a `phenotype_protein` edge. The two-hop and
  subgraph shares rise by 0.5 to 1.2 points: for example (c) p95, proposed
  seed, from 29.8% to 30.4%. The rerun reproduced every other count in this
  section exactly; two tables differ only in the order of tied rows, which was
  left as first published.
- **OptimusKG sources** are per edge: `sources.direct` ∪ `sources.indirect`.
- **C7** counts distinct `(relation, y)` pairs. **C8 (ii)** is the C7 pool
  beyond the 180 candidates C7 itself needs, so the two never draw on the same
  candidates.
- **"Distinct (partner, relation)"** is reported only, not gating. It is the
  number of single-hop facts left once facts that differ only in which seed
  node they attach to are merged.
### Data obtained

| Graph | File | URL | Dataverse version | Bytes | sha256 | md5 matches Dataverse | Accessed | Dataset license (Dataverse) |
|---|---|---|---|---|---|---|---|---|
| PrimeKG | `kg.csv` | https://dataverse.harvard.edu/api/access/datafile/6180620 | 2.1 | 981,751,236 | `0a5a010fcd88dcec572a573d98c26c1c244a11567092b6161d3ded611cfb5c69` | yes | 2026-11-19 | CC0 1.0 |
| PrimeKG | `kg_grouped_diseases.csv` | https://dataverse.harvard.edu/api/access/datafile/6180624?format=original | 2.1 | 2,710,666 | `f669919e86e56f500350e8ca78194c2e7bd27f9bc21ca21d3b72ed80e7a08443` | yes | 2026-11-19 | CC0 1.0 |
| PrimeKG | `README.txt` | https://dataverse.harvard.edu/api/access/datafile/6191270 | 2.1 | 3,137 | `974bf811451c89c5ed9f4343d0ed0f855391d29f1ffa1e238065f61d0f46b927` | yes | 2026-11-19 | CC0 1.0 |
| OptimusKG | `nodes.parquet` | https://dataverse.harvard.edu/api/access/datafile/14220908 | 3.2 | 116,094,167 | `aa081998eca8e9d0cd201986f4d1fb4579157a307c00e90791d643a3abba6502` | yes | 2026-11-19 | CC BY-NC-SA 4.0 |
| OptimusKG | `edges.parquet` | https://dataverse.harvard.edu/api/access/datafile/14220909 | 3.2 | 179,606,962 | `4a101918fa24d662bf046835d9f20ac5d263183d7377771612d8cbaa0e8727af` | yes | 2026-11-19 | CC BY-NC-SA 4.0 |

### Load checks

- **PrimeKG:** 129,375 nodes; 8,100,498 edge rows → 4,050,249 canonical edges (4,050,249 rows collapsed); 33 relation labels; 606 node pairs carry more than one relation. Full-graph degree percentiles (distinct neighbors): p50 = 4, p90 = 136, p95 = 206, p99 = 991. Other load checks: relations_split_by_display_relation = ['drug_protein']; rows_without_reverse_copy = 0; relations_without_source_mapping = [].
- **OptimusKG:** 192,813 nodes; 21,834,669 edge rows → 21,834,669 canonical edges (0 rows collapsed); 67 relation labels; 5 node pairs carry more than one relation. Full-graph degree percentiles (distinct neighbors): p50 = 9, p90 = 501, p95 = 906, p99 = 2,337. Other load checks: edges_with_unknown_endpoint = 0; duplicate_node_ids = 0.

### Seed candidates: PrimeKG

| ID | Name | Type | Source | Degree | Edges by relation | In proposed seed |
|---|---|---|---|---|---|---|
| `28780` | Alzheimer disease | disease | MONDO_grouped | 233 | disease_protein 101, contraindication 56, disease_phenotype_positive 50, disease_disease 10, exposure_disease 8, indication 8 | yes |
| `83960` | Alzheimer disease without neurofibrillary tangles | disease | MONDO | 111 | disease_protein 101, indication 7, contraindication 2, disease_disease 1 | yes |
| `32654` | familial Alzheimer disease | disease | MONDO_grouped | 15 | disease_phenotype_positive 12, disease_disease 2, disease_protein 1 | yes |
| `31527` | Alzheimer disease, familial early-onset, with coexisting amyloid and prion pathology | disease | MONDO | 6 | disease_phenotype_positive 5, disease_disease 1 | yes |
| `28310` | dementia/parkinsonism with non-Alzheimer amyloid plaques | disease | MONDO | 5 | disease_phenotype_positive 4, disease_disease 1 | no |
| `94982` | Alzheimer disease, susceptibility to, mitochondrial | disease | MONDO | 3 | disease_protein 2, disease_disease 1 | yes |
| `129024` | Deregulated CDK5 triggers multiple neurodegenerative pathways in Alzheimer's disease models | pathway | REACTOME | 23 | pathway_protein 22, pathway_pathway 1 | no |

Proposed seed (5 nodes): `28780`, `31527`, `32654`, `83960`, `94982`. Rule: 28780 plus its disease_disease neighbors named '*Alzheimer*' (PrimeKG stores this relation undirected). N1 = 248 nodes.  
Main node alone: `28780`; N1 = 234 nodes.

### Seed candidates: OptimusKG

| ID | Name | Type | Source | Degree | Edges by relation | In proposed seed |
|---|---|---|---|---|---|---|
| `MONDO_0004975` | Alzheimer disease | DIS | OPEN_TARGETS;DRUG_CENTRAL;MONDO | 12,760 | DIS-GEN/ASSOCIATED_WITH 12414, DRG-DIS/INDICATION 314, DIS-PHE/PHENOTYPE_PRESENT 16, EXP-DIS/LINKED_TO 10, DIS-DIS/PARENT 5, DRG-DIS/CONTRAINDICATION 1 | yes |
| `MONDO_0018899` | posterior cortical atrophy | DIS | OPEN_TARGETS;DRUG_CENTRAL;MONDO | 6,837 | DIS-GEN/ASSOCIATED_WITH 6835, DIS-DIS/PARENT 2 | no |
| `EFO_0006792` | Lewy body dementia | DIS | OPEN_TARGETS;DRUG_CENTRAL;MONDO | 1,281 | DIS-GEN/ASSOCIATED_WITH 1258, DRG-DIS/INDICATION 10, DIS-PHE/PHENOTYPE_PRESENT 7, DRG-DIS/OFF_LABEL_USE 2, DRG-DIS/CONTRAINDICATION 2, DIS-DIS/PARENT 2 | no |
| `MONDO_0100087` | familial Alzheimer disease | DIS | OPEN_TARGETS;DRUG_CENTRAL;MONDO | 409 | DIS-GEN/ASSOCIATED_WITH 405, DIS-DIS/PARENT 4 | yes |
| `EFO_0006514` | Alzheimer's disease biomarker measurement | DIS | OPEN_TARGETS;DRUG_CENTRAL;MONDO | 383 | DIS-GEN/ASSOCIATED_WITH 361, DIS-DIS/PARENT 22 | no |
| `EFO_1001870` | late-onset Alzheimers disease | DIS | OPEN_TARGETS;DRUG_CENTRAL;MONDO | 259 | DIS-GEN/ASSOCIATED_WITH 258, DIS-DIS/PARENT 1 | yes |
| `MONDO_0015140` | early-onset autosomal dominant Alzheimer disease | DIS | OPEN_TARGETS;DRUG_CENTRAL;MONDO | 171 | DIS-GEN/ASSOCIATED_WITH 167, DIS-DIS/PARENT 4 | yes |
| `MONDO_0011913` | Alzheimer disease 3 | DIS | OPEN_TARGETS;DRUG_CENTRAL;MONDO | 98 | DIS-GEN/ASSOCIATED_WITH 75, DIS-PHE/PHENOTYPE_PRESENT 22, DIS-DIS/PARENT 1 | yes |
| `EFO_0009268` | family history of Alzheimer’s disease | DIS | OPEN_TARGETS;DRUG_CENTRAL;MONDO | 97 | DIS-GEN/ASSOCIATED_WITH 96, DIS-DIS/PARENT 1 | no |
| `OBA_2001000` | age of onset of Alzheimer disease | DIS | OPEN_TARGETS;DRUG_CENTRAL;MONDO | 57 | DIS-GEN/ASSOCIATED_WITH 56, DIS-DIS/PARENT 1 | no |
| `MONDO_0007088` | Alzheimer disease type 1 | DIS | OPEN_TARGETS;DRUG_CENTRAL;MONDO | 40 | DIS-GEN/ASSOCIATED_WITH 30, DIS-PHE/PHENOTYPE_PRESENT 9, DIS-DIS/PARENT 1 | yes |
| `MONDO_0014265` | Alzheimer disease 18 | DIS | OPEN_TARGETS;DRUG_CENTRAL;MONDO | 22 | DIS-GEN/ASSOCIATED_WITH 19, DIS-DIS/PARENT 3 | yes |
| `EFO_0006801` | Alzheimer's disease neuropathologic change | DIS | OPEN_TARGETS;DRUG_CENTRAL;MONDO | 6 | DIS-GEN/ASSOCIATED_WITH 4, DIS-DIS/PARENT 2 | no |
| `HP_0002511` | Alzheimer disease | PHE | OPEN_TARGETS;HPO;MEDDRA | 5 | DIS-PHE/PHENOTYPE_PRESENT 4, PHE-PHE/PARENT 1 | no |
| `REACT:R-HSA-8862803` | Deregulated CDK5 triggers multiple neurodegenerative pathways in Alzheimer's disease models | PWY | REACTOME;OPEN_TARGETS | 24 | PWY-GEN/INTERACTS_WITH 23, PWY-PWY/PARENT 1 | no |

Proposed seed (7 nodes): `EFO_1001870`, `MONDO_0004975`, `MONDO_0007088`, `MONDO_0011913`, `MONDO_0014265`, `MONDO_0015140`, `MONDO_0100087`. Rule: MONDO_0004975 plus all its descendants over DIS-DIS/PARENT (parent -> child). N1 = 12,781 nodes.  
Main node alone: `MONDO_0004975`; N1 = 12,761 nodes.

### Pass/fail

| Graph | Seed | Definition | C1 | C2 | C3 | C4 | C5 | C6 | C7 | C8 | All gating |
|---|---|---|---|---|---|---|---|---|---|---|---|
| PrimeKG | proposed | (a) 1-hop | pass | **fail** | **fail** | pass | pass | pass | pass | pass | **fail** |
| PrimeKG | proposed | (b) 2-hop | pass | **fail** | pass | pass | pass | pass | pass | pass | **fail** |
| PrimeKG | proposed | (c) p90 | pass | **fail** | pass | pass | pass | pass | pass | pass | **fail** |
| PrimeKG | proposed | (c) p95 | pass | **fail** | pass | pass | pass | pass | pass | pass | **fail** |
| PrimeKG | proposed | (c) p99 | pass | **fail** | pass | pass | pass | pass | pass | pass | **fail** |
| PrimeKG | main only | (a) 1-hop | **fail** | **fail** | **fail** | pass | pass | **fail** | **fail** | pass | **fail** |
| PrimeKG | main only | (b) 2-hop | **fail** | **fail** | pass | pass | pass | pass | pass | pass | **fail** |
| PrimeKG | main only | (c) p90 | **fail** | **fail** | pass | pass | pass | pass | pass | pass | **fail** |
| PrimeKG | main only | (c) p95 | **fail** | **fail** | pass | pass | pass | pass | pass | pass | **fail** |
| PrimeKG | main only | (c) p99 | **fail** | **fail** | pass | pass | pass | pass | pass | pass | **fail** |
| OptimusKG | proposed | (a) 1-hop | pass | **fail** | pass | pass | pass | pass | **fail** | pass | **fail** |
| OptimusKG | proposed | (b) 2-hop | pass | **fail** | pass | pass | pass | pass | pass | pass | **fail** |
| OptimusKG | proposed | (c) p90 | pass | **fail** | pass | pass | pass | pass | pass | pass | **fail** |
| OptimusKG | proposed | (c) p95 | pass | **fail** | pass | pass | pass | pass | pass | pass | **fail** |
| OptimusKG | proposed | (c) p99 | pass | **fail** | pass | pass | pass | pass | pass | pass | **fail** |
| OptimusKG | main only | (a) 1-hop | pass | **fail** | pass | pass | pass | **fail** | **fail** | pass | **fail** |
| OptimusKG | main only | (b) 2-hop | pass | **fail** | pass | pass | pass | pass | pass | pass | **fail** |
| OptimusKG | main only | (c) p90 | pass | **fail** | pass | pass | pass | pass | pass | pass | **fail** |
| OptimusKG | main only | (c) p95 | pass | **fail** | pass | pass | pass | pass | pass | pass | **fail** |
| OptimusKG | main only | (c) p99 | pass | **fail** | pass | pass | pass | pass | pass | pass | **fail** |

### Metrics: PrimeKG, proposed seed

| Metric (proposed threshold) | (a) 1-hop | (b) 2-hop | (c) p90 | (c) p95 | (c) p99 |
|---|---|---|---|---|---|
| Nodes | 248 | 17,934 | 2,266 | 4,157 | 13,509 |
| Edges | 2,139 | 125,277 | 5,326 | 9,790 | 47,381 |
| Degree cap for pruning | n/a | n/a | 136 | 206 | 991 |
| C1 single-hop facts (≥ 270) | 364 | 364 | 364 | 364 | 364 |
| single-hop facts, distinct (partner, relation) over seed nodes (reported) | 243 | 243 | 243 | 243 | 243 |
| C2 relations with ≥ 30 facts (≥ 4) | 3 | 3 | 3 | 3 | 3 |
| C2 balanced capacity (≥ 270) | 267 | 267 | 267 | 267 | 267 |
| C3 two-hop paths (≥ 225) | 4,226 | 166,901 | 8,867 | 16,440 | 76,685 |
| C3 distinct endpoints (≥ 225) | 174 | 17,860 | 2,192 | 4,083 | 13,435 |
| C4 patterns with ≥ 20 paths (≥ 3) | 15 | 39 | 31 | 31 | 36 |
| C4 balanced capacity (≥ 225) | 932 | 3,160 | 2,194 | 2,411 | 2,914 |
| C5 top-1 intermediate path share (≤ 20%) | 3.5% | 2.8% | 3.0% | 2.5% | 2.5% |
| C5 top-5 path share (≤ 50%) | 14.1% | 12.0% | 14.1% | 12.0% | 9.5% |
| top-20 path share | 37.2% | 32.7% | 39.7% | 41.2% | 28.3% |
| top-1 / top-5 / top-20 endpoint share | 42.0% / 52.9% / 54.6% | 13.2% / 34.6% / 52.6% | 6.0% / 11.1% / 55.9% | 4.9% / 12.8% / 40.4% | 7.1% / 19.3% / 40.1% |
| endpoints reachable only via top 5 | 0.6% | 8.9% | 6.4% | 6.8% | 4.4% |
| C6 facts with an absent sibling (≥ 135) | 288 | 360 | 360 | 360 | 360 |
| C7 unanswerable (r, y) pairs (≥ 180) | 281 | 18,609 | 2,045 | 3,677 | 13,417 |
| C7 relations with ≥ 20 (≥ 3) | 4 | 6 | 6 | 6 | 6 |
| C8 (i) contradicted pairs (≥ 30) | 141 | 23,355 | 1,765 | 4,351 | 15,293 |
| C8 (i) of which touch the seed | 140 | 140 | 140 | 140 | 140 |
| C8 (ii) spare absence premises | 101 | 18,429 | 1,865 | 3,497 | 13,237 |
| C8 total (≥ 90) | 242 | 41,784 | 3,630 | 7,848 | 28,530 |
| R1 shared-neighbor disease pairs | 7 (7 with 1–20 shared) | 10,659 (10,538 with 1–20 shared) | 1,128 (1,120 with 1–20 shared) | 2,392 (2,384 with 1–20 shared) | 5,180 (5,128 with 1–20 shared) |
| R2 facts in seed groups of ≤ 20 edges | 56 | 56 | 56 | 56 | 56 |
| R2 facts reachable in one page from either end | 364 | 170 | 348 | 323 | 217 |
| R3 single-hop facts from a restricted source | 56.3% (205) | 56.3% (205) | 56.3% (205) | 56.3% (205) | 56.3% (205) |
| R3 two-hop path edges from a restricted source | 87.1% (1,764) | 70.1% (87,803) | 42.0% (2,219) | 30.4% (2,970) | 42.5% (20,129) |
| R3 subgraph edges from a restricted source | 85.8% (1,836) | 70.1% (87,803) | 42.2% (2,245) | 30.4% (2,980) | 42.5% (20,129) |

### Metrics: PrimeKG, main only seed

| Metric (proposed threshold) | (a) 1-hop | (b) 2-hop | (c) p90 | (c) p95 | (c) p99 |
|---|---|---|---|---|---|
| Nodes | 234 | 17,821 | 2,195 | 3,909 | 13,195 |
| Edges | 2,074 | 123,309 | 5,176 | 9,280 | 45,376 |
| Degree cap for pruning | n/a | n/a | 136 | 206 | 991 |
| C1 single-hop facts (≥ 270) | 233 | 233 | 233 | 233 | 233 |
| single-hop facts, distinct (partner, relation) over seed nodes (reported) | 233 | 233 | 233 | 233 | 233 |
| C2 relations with ≥ 30 facts (≥ 4) | 3 | 3 | 3 | 3 | 3 |
| C2 balanced capacity (≥ 270) | 233 | 233 | 233 | 233 | 233 |
| C3 two-hop paths (≥ 225) | 3,682 | 124,917 | 6,784 | 10,888 | 46,984 |
| C3 distinct endpoints (≥ 225) | 205 | 17,792 | 2,166 | 3,880 | 13,166 |
| C4 patterns with ≥ 20 paths (≥ 3) | 15 | 37 | 30 | 31 | 34 |
| C4 balanced capacity (≥ 225) | 934 | 3,151 | 2,002 | 2,248 | 2,885 |
| C5 top-1 intermediate path share (≤ 20%) | 3.0% | 2.1% | 1.9% | 1.9% | 2.0% |
| C5 top-5 path share (≤ 50%) | 10.4% | 10.1% | 9.6% | 9.1% | 10.0% |
| top-20 path share | 35.1% | 34.6% | 32.5% | 33.1% | 31.5% |
| top-1 / top-5 / top-20 endpoint share | 53.7% / 85.9% / 86.8% | 14.5% / 17.5% / 40.4% | 6.1% / 25.4% / 66.9% | 5.2% / 17.0% / 52.7% | 7.3% / 15.4% / 41.0% |
| endpoints reachable only via top 5 | 18.0% | 0.3% | 13.5% | 7.2% | 1.0% |
| C6 facts with an absent sibling (≥ 135) | 0 | 233 | 233 | 233 | 233 |
| C7 unanswerable (r, y) pairs (≥ 180) | 0 | 18,223 | 1,674 | 3,137 | 12,813 |
| C7 relations with ≥ 20 (≥ 3) | 0 | 6 | 6 | 6 | 6 |
| C8 (i) contradicted pairs (≥ 30) | 130 | 22,250 | 1,740 | 3,975 | 14,188 |
| C8 (i) of which touch the seed | 114 | 114 | 114 | 114 | 114 |
| C8 (ii) spare absence premises | 0 | 18,043 | 1,494 | 2,957 | 12,633 |
| C8 total (≥ 90) | 130 | 40,293 | 3,234 | 6,932 | 26,821 |
| R1 shared-neighbor disease pairs | 5 (4 with 1–20 shared) | 5,335 (5,235 with 1–20 shared) | 989 (984 with 1–20 shared) | 1,608 (1,603 with 1–20 shared) | 3,118 (3,085 with 1–20 shared) |
| R2 facts in seed groups of ≤ 20 edges | 26 | 26 | 26 | 26 | 26 |
| R2 facts reachable in one page from either end | 233 | 93 | 219 | 203 | 138 |
| R3 single-hop facts from a restricted source | 43.3% (101) | 43.3% (101) | 43.3% (101) | 43.3% (101) | 43.3% (101) |
| R3 two-hop path edges from a restricted source | 87.6% (1,792) | 70.6% (87,015) | 41.6% (2,149) | 31.1% (2,884) | 42.5% (19,304) |
| R3 subgraph edges from a restricted source | 86.4% (1,792) | 70.6% (87,015) | 41.5% (2,149) | 31.1% (2,884) | 42.5% (19,304) |

### Metrics: OptimusKG, proposed seed

| Metric (proposed threshold) | (a) 1-hop | (b) 2-hop | (c) p90 | (c) p95 | (c) p99 |
|---|---|---|---|---|---|
| Nodes | 12,781 | 75,461 | 32,648 | 52,814 | 70,621 |
| Edges | 269,384 | 12,300,795 | 1,062,429 | 4,278,211 | 10,777,329 |
| Degree cap for pruning | n/a | n/a | 501 | 906 | 2,337 |
| C1 single-hop facts (≥ 270) | 13,753 | 13,753 | 13,753 | 13,753 | 13,753 |
| single-hop facts, distinct (partner, relation) over seed nodes (reported) | 12,774 | 12,774 | 12,774 | 12,774 | 12,774 |
| C2 relations with ≥ 30 facts (≥ 4) | 3 | 3 | 3 | 3 | 3 |
| C2 balanced capacity (≥ 270) | 287 | 287 | 287 | 287 | 287 |
| C3 two-hop paths (≥ 225) | 591,483 | 14,242,617 | 1,401,506 | 4,739,615 | 12,043,622 |
| C3 distinct endpoints (≥ 225) | 12,607 | 75,287 | 32,474 | 52,640 | 70,447 |
| C4 patterns with ≥ 20 paths (≥ 3) | 37 | 70 | 50 | 51 | 64 |
| C4 balanced capacity (≥ 225) | 2,654 | 5,511 | 3,987 | 4,218 | 5,230 |
| C5 top-1 intermediate path share (≤ 20%) | 3.0% | 0.2% | 1.3% | 0.4% | 0.1% |
| C5 top-5 path share (≤ 50%) | 10.7% | 0.9% | 4.5% | 1.3% | 0.6% |
| top-20 path share | 17.0% | 2.7% | 7.2% | 2.5% | 1.7% |
| top-1 / top-5 / top-20 endpoint share | 70.2% / 95.0% / 96.5% | 8.2% / 27.3% / 36.4% | 27.3% / 36.9% / 39.9% | 16.8% / 22.7% / 28.5% | 12.6% / 19.8% / 26.8% |
| endpoints reachable only via top 5 | 4.7% | 0.3% | 1.8% | 1.1% | 0.8% |
| C6 facts with an absent sibling (≥ 135) | 1,008 | 13,747 | 13,747 | 13,747 | 13,747 |
| C7 unanswerable (r, y) pairs (≥ 180) | 12,440 | 65,452 | 29,957 | 43,660 | 59,469 |
| C7 relations with ≥ 20 (≥ 3) | 2 | 6 | 6 | 6 | 6 |
| C8 (i) contradicted pairs (≥ 30) | 331 | 2,975,313 | 434,652 | 1,597,826 | 2,838,442 |
| C8 (i) of which touch the seed | 315 | 315 | 315 | 315 | 315 |
| C8 (ii) spare absence premises | 12,260 | 65,272 | 29,777 | 43,480 | 59,289 |
| C8 total (≥ 90) | 12,591 | 3,040,585 | 464,429 | 1,641,306 | 2,897,731 |
| R1 shared-neighbor disease pairs | 35 (5 with 1–20 shared) | 91,147 (67,476 with 1–20 shared) | 12,701 (11,005 with 1–20 shared) | 32,697 (26,493 with 1–20 shared) | 67,348 (49,846 with 1–20 shared) |
| R2 facts in seed groups of ≤ 20 edges | 68 | 68 | 68 | 68 | 68 |
| R2 facts reachable in one page from either end | 13,753 | 417 | 11,585 | 6,612 | 1,144 |
| R3 single-hop facts from a restricted source | 0.9% (127) | 0.9% (127) | 0.9% (127) | 0.9% (127) | 0.9% (127) |
| R3 two-hop path edges from a restricted source | 2.0% (5,352) | 1.4% (168,994) | 0.9% (9,712) | 0.7% (31,916) | 1.4% (154,063) |
| R3 subgraph edges from a restricted source | 2.6% (7,062) | 1.4% (170,704) | 1.1% (11,422) | 0.8% (33,626) | 1.4% (155,773) |

### Metrics: OptimusKG, main only seed

| Metric (proposed threshold) | (a) 1-hop | (b) 2-hop | (c) p90 | (c) p95 | (c) p99 |
|---|---|---|---|---|---|
| Nodes | 12,761 | 74,902 | 32,058 | 52,539 | 70,499 |
| Edges | 242,068 | 12,289,345 | 1,035,152 | 4,258,605 | 10,772,681 |
| Degree cap for pruning | n/a | n/a | 501 | 906 | 2,337 |
| C1 single-hop facts (≥ 270) | 12,760 | 12,760 | 12,760 | 12,760 | 12,760 |
| single-hop facts, distinct (partner, relation) over seed nodes (reported) | 12,760 | 12,760 | 12,760 | 12,760 | 12,760 |
| C2 relations with ≥ 30 facts (≥ 4) | 2 | 2 | 2 | 2 | 2 |
| C2 balanced capacity (≥ 270) | 248 | 248 | 248 | 248 | 248 |
| C3 two-hop paths (≥ 225) | 449,424 | 12,496,701 | 1,242,508 | 4,465,961 | 10,980,037 |
| C3 distinct endpoints (≥ 225) | 12,587 | 74,728 | 31,884 | 52,365 | 70,325 |
| C4 patterns with ≥ 20 paths (≥ 3) | 33 | 66 | 47 | 49 | 62 |
| C4 balanced capacity (≥ 225) | 2,407 | 5,249 | 3,724 | 3,986 | 5,084 |
| C5 top-1 intermediate path share (≤ 20%) | 2.6% | 0.1% | 0.9% | 0.3% | 0.1% |
| C5 top-5 path share (≤ 50%) | 6.8% | 0.4% | 2.4% | 0.7% | 0.3% |
| top-20 path share | 9.4% | 1.0% | 3.4% | 1.0% | 0.6% |
| top-1 / top-5 / top-20 endpoint share | 93.2% / 94.8% / 96.0% | 18.5% / 30.8% / 37.6% | 36.8% / 37.4% / 37.9% | 22.4% / 22.8% / 29.2% | 16.7% / 21.7% / 29.1% |
| endpoints reachable only via top 5 | 7.1% | 1.8% | 2.8% | 1.7% | 1.2% |
| C6 facts with an absent sibling (≥ 135) | 0 | 12,760 | 12,760 | 12,760 | 12,760 |
| C7 unanswerable (r, y) pairs (≥ 180) | 0 | 52,468 | 16,936 | 30,961 | 46,925 |
| C7 relations with ≥ 20 (≥ 3) | 0 | 6 | 6 | 6 | 6 |
| C8 (i) contradicted pairs (≥ 30) | 327 | 2,975,301 | 434,637 | 1,597,811 | 2,838,430 |
| C8 (i) of which touch the seed | 315 | 315 | 315 | 315 | 315 |
| C8 (ii) spare absence premises | 0 | 52,288 | 16,756 | 30,781 | 46,745 |
| C8 total (≥ 90) | 327 | 3,027,589 | 451,393 | 1,628,592 | 2,885,175 |
| R1 shared-neighbor disease pairs | 5 (1 with 1–20 shared) | 21,770 (10,338 with 1–20 shared) | 8,890 (7,241 with 1–20 shared) | 16,394 (11,066 with 1–20 shared) | 21,166 (10,720 with 1–20 shared) |
| R2 facts in seed groups of ≤ 20 edges | 32 | 32 | 32 | 32 | 32 |
| R2 facts reachable in one page from either end | 12,760 | 377 | 10,645 | 5,850 | 906 |
| R3 single-hop facts from a restricted source | 0.7% (94) | 0.7% (94) | 0.7% (94) | 0.7% (94) | 0.7% (94) |
| R3 two-hop path edges from a restricted source | 2.2% (5,143) | 1.4% (168,977) | 0.9% (9,504) | 0.7% (31,752) | 1.4% (154,021) |
| R3 subgraph edges from a restricted source | 2.8% (6,853) | 1.4% (170,687) | 1.1% (11,214) | 0.8% (33,462) | 1.4% (155,731) |

### Detail: PrimeKG, proposed seed

**Nodes by type**

| Type | (a) 1-hop | (b) 2-hop | (c) p95 |
|---|---|---|---|
| disease | 12 | 5,399 | 1,812 |
| gene/protein | 103 | 4,837 | 776 |
| drug | 64 | 3,670 | 413 |
| biological_process | 0 | 1,852 | 443 |
| effect/phenotype | 61 | 801 | 214 |
| molecular_function | 0 | 373 | 104 |
| pathway | 0 | 318 | 56 |
| cellular_component | 0 | 273 | 118 |
| exposure | 8 | 263 | 80 |
| anatomy | 0 | 148 | 141 |

**Edges by relation**, with the relation's sources (top six by edge count in (b))

| Relation | (a) 1-hop | (b) 2-hop | (c) p95 | single-hop facts | Sources |
|---|---|---|---|---|---|
| `drug_drug` | 1,250 | 73,807 | 1,290 | 0 | DrugBank |
| `disease_phenotype_positive` | 67 | 10,228 | 2,059 | 67 | HPO, HPO annotations |
| `anatomy_protein_present` | 0 | 8,603 | 1,959 | 0 | Bgee |
| `drug_effect` | 299 | 7,195 | 623 | 0 | SIDER |
| `protein_protein` | 91 | 7,106 | 741 | 0 | BioGRID, HuRI, Menche et al. interactome, STRING |
| `contraindication` | 59 | 4,011 | 226 | 58 | DrugCentral |
| `disease_protein` | 211 | 3,927 | 834 | 205 | DisGeNET |
| `bioprocess_protein` | 0 | 3,042 | 503 | 0 | GO annotations (NCBI gene2go) |
| `drug_protein/target` | 17 | 1,560 | 106 | 0 | DrugBank |
| `cellcomp_protein` | 0 | 1,015 | 229 | 0 | GO annotations (NCBI gene2go) |
| `molfunc_protein` | 0 | 760 | 150 | 0 | GO annotations (NCBI gene2go) |
| `drug_protein/enzyme` | 34 | 660 | 56 | 0 | DrugBank |
| `phenotype_protein` | 25 | 557 | 61 | 0 | DisGeNET |
| `pathway_protein` | 0 | 480 | 62 | 0 | Reactome |
| `exposure_protein` | 21 | 316 | 79 | 0 | CTD |
| `phenotype_phenotype` | 21 | 306 | 149 | 0 | HPO |
| `disease_disease` | 14 | 304 | 304 | 11 | HPO, MONDO |
| `exposure_exposure` | 6 | 289 | 74 | 0 | CTD |
| `indication` | 15 | 287 | 37 | 15 | DrugCentral |
| `exposure_disease` | 9 | 225 | 113 | 8 | CTD |
| `disease_phenotype_negative` | 0 | 156 | 32 | 0 | HPO annotations |
| `exposure_bioprocess` | 0 | 146 | 42 | 0 | CTD |
| `anatomy_protein_absent` | 0 | 134 | 46 | 0 | Bgee |
| `drug_protein/transporter` | 0 | 64 | 10 | 0 | DrugBank |
| `off-label use` | 0 | 57 | 5 | 0 | DrugCentral |
| `drug_protein/carrier` | 0 | 33 | 0 | 0 | DrugBank |
| `exposure_molfunc` | 0 | 7 | 0 | 0 | CTD |
| `exposure_cellcomp` | 0 | 2 | 0 | 0 | CTD |

**Degree within the subgraph** (distinct neighbors)

| Definition | min | median | p90 | p99 | max | mean |
|---|---|---|---|---|---|---|
| (a) 1-hop | 100.0% | 4.0 | 56.0 | 75.0 | 233.0 | 17.2 |
| (b) 2-hop | 100.0% | 2.0 | 25.0 | 117.0 | 2,589.0 | 14.0 |
| (c) p95 | 100.0% | 100.0% | 5.0 | 101.0 | 233.0 | 4.7 |

**Top 20 hubs, (a) 1-hop** (by degree within the subgraph)

| ID | Name | Type | Degree in subgraph | Degree in full graph | Seed |
|---|---|---|---|---|---|
| `28780` | Alzheimer disease | disease | 233 | 233 | yes |
| `83960` | Alzheimer disease without neurofibrillary tangles | disease | 111 | 111 | yes |
| `14323` | Aripiprazole | drug | 75 | 2,360 |  |
| `14999` | Quetiapine | drug | 72 | 2,464 |  |
| `14161` | Olanzapine | drug | 70 | 2,139 |  |
| `14176` | Fluoxetine | drug | 70 | 2,157 |  |
| `14223` | Risperidone | drug | 69 | 2,081 |  |
| `15093` | Clozapine | drug | 69 | 2,575 |  |
| `15126` | Donepezil | drug | 69 | 1,498 |  |
| `14140` | Ziprasidone | drug | 65 | 2,226 |  |
| `14178` | Chlorpromazine | drug | 65 | 2,589 |  |
| `15001` | Paliperidone | drug | 65 | 2,398 |  |
| `14952` | Haloperidol | drug | 64 | 2,429 |  |
| `14156` | Amitriptyline | drug | 63 | 2,541 |  |
| `14380` | Asenapine | drug | 63 | 1,830 |  |
| `14988` | Pimozide | drug | 60 | 1,702 |  |
| `14993` | Phenobarbital | drug | 59 | 1,981 |  |
| `14182` | Oxycodone | drug | 58 | 1,458 |  |
| `14351` | Thiothixene | drug | 58 | 1,314 |  |
| `14913` | Trifluoperazine | drug | 58 | 1,592 |  |

**Top 20 hubs, (b) 2-hop** (by degree within the subgraph)

| ID | Name | Type | Degree in subgraph | Degree in full graph | Seed |
|---|---|---|---|---|---|
| `14178` | Chlorpromazine | drug | 2,589 | 2,589 |  |
| `15093` | Clozapine | drug | 2,575 | 2,575 |  |
| `14156` | Amitriptyline | drug | 2,541 | 2,541 |  |
| `14999` | Quetiapine | drug | 2,464 | 2,464 |  |
| `14952` | Haloperidol | drug | 2,429 | 2,429 |  |
| `15001` | Paliperidone | drug | 2,398 | 2,398 |  |
| `14323` | Aripiprazole | drug | 2,360 | 2,360 |  |
| `14140` | Ziprasidone | drug | 2,226 | 2,226 |  |
| `22759` | Autosomal dominant inheritance | effect/phenotype | 2,205 | 2,205 |  |
| `14176` | Fluoxetine | drug | 2,157 | 2,157 |  |
| `14161` | Olanzapine | drug | 2,139 | 2,139 |  |
| `23000` | Seizure | effect/phenotype | 2,109 | 2,109 |  |
| `14223` | Risperidone | drug | 2,081 | 2,081 |  |
| `14993` | Phenobarbital | drug | 1,981 | 1,981 |  |
| `15098` | Promazine | drug | 1,934 | 1,934 |  |
| `15006` | Iloperidone | drug | 1,842 | 1,842 |  |
| `15089` | Codeine | drug | 1,833 | 1,833 |  |
| `14380` | Asenapine | drug | 1,830 | 1,830 |  |
| `15165` | Methotrimeprazine | drug | 1,818 | 1,818 |  |
| `15114` | Thioridazine | drug | 1,719 | 1,719 |  |

**Top 20 hubs, (c) p95** (by degree within the subgraph)

| ID | Name | Type | Degree in subgraph | Degree in full graph | Seed |
|---|---|---|---|---|---|
| `28780` | Alzheimer disease | disease | 233 | 233 | yes |
| `6329` | BDNF | gene/protein | 204 | 204 |  |
| `4352` | TPP1 | gene/protein | 199 | 199 |  |
| `8855` | HFE | gene/protein | 198 | 198 |  |
| `2606` | APOC1 | gene/protein | 196 | 196 |  |
| `6751` | TFAM | gene/protein | 196 | 196 |  |
| `22442` | Dementia | effect/phenotype | 195 | 195 |  |
| `24262` | Cerebral cortical atrophy | effect/phenotype | 188 | 188 |  |
| `84695` | Depressivity | effect/phenotype | 188 | 188 |  |
| `9689` | ADAMTS1 | gene/protein | 187 | 187 |  |
| `22679` | Sleep disturbance | effect/phenotype | 186 | 186 |  |
| `4278` | NECTIN2 | gene/protein | 183 | 183 |  |
| `3837` | ABI3 | gene/protein | 181 | 181 |  |
| `25254` | Specific learning disability | effect/phenotype | 179 | 179 |  |
| `22692` | Abnormality of extrapyramidal motor function | effect/phenotype | 178 | 178 |  |
| `12825` | ABCA7 | gene/protein | 175 | 175 |  |
| `85516` | Babinski sign | effect/phenotype | 170 | 170 |  |
| `9021` | CRH | gene/protein | 170 | 170 |  |
| `13566` | CHRNA7 | gene/protein | 169 | 169 |  |
| `84887` | Heterogeneous | effect/phenotype | 165 | 165 |  |

**Top 10 two-hop intermediates, (a) 1-hop**

| ID | Name | Type | Paths | Path share | Endpoint share |
|---|---|---|---|---|---|
| `14323` | Aripiprazole | drug | 146 | 3.5% | 42.0% |
| `15126` | Donepezil | drug | 134 | 3.2% | 38.5% |
| `14380` | Asenapine | drug | 122 | 2.9% | 35.1% |
| `15113` | Galantamine | drug | 104 | 2.5% | 29.9% |
| `23000` | Seizure | effect/phenotype | 88 | 2.1% | 25.3% |
| `17174` | Rivastigmine | drug | 78 | 1.8% | 22.4% |
| `14999` | Quetiapine | drug | 71 | 1.7% | 40.8% |
| `14161` | Olanzapine | drug | 69 | 1.6% | 39.7% |
| `14176` | Fluoxetine | drug | 69 | 1.6% | 39.7% |
| `14223` | Risperidone | drug | 68 | 1.6% | 39.1% |

**Top 10 two-hop intermediates, (b) 2-hop**

| ID | Name | Type | Paths | Path share | Endpoint share |
|---|---|---|---|---|---|
| `14323` | Aripiprazole | drug | 4,716 | 2.8% | 13.2% |
| `22759` | Autosomal dominant inheritance | effect/phenotype | 4,406 | 2.6% | 12.3% |
| `23000` | Seizure | effect/phenotype | 4,228 | 2.5% | 11.8% |
| `14380` | Asenapine | drug | 3,656 | 2.2% | 10.2% |
| `15126` | Donepezil | drug | 2,992 | 1.8% | 8.4% |
| `611` | APP | gene/protein | 2,646 | 1.6% | 7.4% |
| `14178` | Chlorpromazine | drug | 2,588 | 1.6% | 14.5% |
| `15093` | Clozapine | drug | 2,574 | 1.5% | 14.4% |
| `14156` | Amitriptyline | drug | 2,540 | 1.5% | 14.2% |
| `375` | ESR1 | gene/protein | 2,536 | 1.5% | 7.1% |

**Top 10 two-hop intermediates, (c) p95**

| ID | Name | Type | Paths | Path share | Endpoint share |
|---|---|---|---|---|---|
| `6329` | BDNF | gene/protein | 404 | 2.5% | 4.9% |
| `4352` | TPP1 | gene/protein | 394 | 2.4% | 4.8% |
| `8855` | HFE | gene/protein | 392 | 2.4% | 4.8% |
| `2606` | APOC1 | gene/protein | 388 | 2.4% | 4.8% |
| `6751` | TFAM | gene/protein | 388 | 2.4% | 4.8% |
| `84695` | Depressivity | effect/phenotype | 372 | 2.3% | 4.6% |
| `9689` | ADAMTS1 | gene/protein | 370 | 2.3% | 4.5% |
| `4278` | NECTIN2 | gene/protein | 362 | 2.2% | 4.4% |
| `3837` | ABI3 | gene/protein | 358 | 2.2% | 4.4% |
| `12825` | ABCA7 | gene/protein | 346 | 2.1% | 4.2% |

**Candidate breakdowns, (c) p95**

| Breakdown | Counts |
|---|---|
| Single-hop facts by relation | `disease_protein` 205; `disease_phenotype_positive` 67; `contraindication` 58; `indication` 15; `disease_disease` 11; `exposure_disease` 8 |
| Top two-hop patterns | `disease_protein | anatomy_protein_present` 3,918; `contraindication | drug_drug` 2,492; `disease_phenotype_positive | disease_phenotype_positive` 2,189; `disease_protein | protein_protein` 1,668; `disease_protein | disease_protein` 1,226; `disease_protein | bioprocess_protein` 1,006; `disease_phenotype_positive | drug_effect` 644; `disease_protein | cellcomp_protein` 458 |
| Unanswerable candidates by relation (distinct y) | `disease_disease` 1,807; `disease_protein` 776; `contraindication` 404; `indication` 404; `disease_phenotype_positive` 214; `exposure_disease` 72 |
| Contradicted pairs by relation | `disease_phenotype_positive` 2,055; `anatomy_protein_present` 1,959; `contraindication` 226; `anatomy_protein_absent` 46; `indication` 37; `disease_phenotype_negative` 28 |
| Seed fan-out (seed, relation): edges | 28780 | disease_protein: 101; 83960 | disease_protein: 101; 28780 | contraindication: 56; 28780 | disease_phenotype_positive: 50; 32654 | disease_phenotype_positive: 12; 28780 | disease_disease: 10; 28780 | exposure_disease: 8; 28780 | indication: 8; 83960 | indication: 7; 31527 | disease_phenotype_positive: 5; 94982 | disease_protein: 2; 83960 | contraindication: 2 |
| Shared-neighbor disease pairs by shared count | pairs 2,392; shared_1 1,733; shared_2_5 603; shared_6_20 48; shared_gt_20 8; pairs_with_1_to_20_shared 2,384; distinct_other_diseases 1,804 |

**Licensing, (c) p95**: share of edges with each restricted source

| Edge set | Edges | DrugBank | SIDER | DisGeNET | UMLS | Any |
|---|---|---|---|---|---|---|
| single hop facts | 364 | 0.0% | 0.0% | 56.3% | 0.0% | 56.3% |
| edges on two hop paths | 9,773 | 15.0% | 6.4% | 9.1% | 0.0% | 30.4% |
| all subgraph edges | 9,790 | 14.9% | 6.4% | 9.1% | 0.0% | 30.4% |

### Detail: OptimusKG, proposed seed

**Nodes by type**

| Type | (a) 1-hop | (b) 2-hop | (c) p95 |
|---|---|---|---|
| DIS | 12 | 21,812 | 16,589 |
| GEN | 12,414 | 20,158 | 17,538 |
| BPO | 0 | 11,721 | 6,888 |
| DRG | 315 | 10,998 | 4,266 |
| MFN | 0 | 4,359 | 2,765 |
| PWY | 0 | 2,188 | 1,527 |
| CCO | 0 | 1,770 | 1,276 |
| PHE | 30 | 1,723 | 1,309 |
| EXP | 10 | 419 | 348 |
| ANA | 0 | 313 | 308 |

**Edges by relation**, with the relation's sources (top six by edge count in (b))

| Relation | (a) 1-hop | (b) 2-hop | (c) p95 | single-hop facts | Sources |
|---|---|---|---|---|---|
| `DIS-GEN/ASSOCIATED_WITH` | 60,710 | 7,853,878 | 2,140,895 | 13,368 | OPEN_TARGETS, DISGENET, CTD, GENOMICS_ENGLAND, ORPHANET, UNIPROT |
| `ANA-GEN/EXPRESSION_PRESENT` | 0 | 2,488,565 | 1,337,490 | 0 | BGEE |
| `PHE-GEN/ASSOCIATED_WITH` | 6,860 | 693,734 | 159,782 | 0 | OPEN_TARGETS, DISGENET, CTD, GENOMICS_ENGLAND, ORPHANET, UNIPROT |
| `ANA-GEN/EXPRESSION_ABSENT` | 0 | 478,929 | 256,567 | 0 | BGEE |
| `GEN-GEN/INTERACTS_WITH` | 196,007 | 304,399 | 236,728 | 0 | PRIMEKG, INTACT, APID, INWEB, HIPPIE, HIUNION |
| `BPO-GEN/INTERACTS_WITH` | 0 | 126,282 | 36,181 | 0 | OPEN_TARGETS, GO_REF:0000107, GO_REF:0000033, GO_REF:0000024, GO_REF:0000043, GO_REF:0000117 |
| `DRG-DRG/SYNERGISTIC_INTERACTION` | 2,918 | 119,432 | 22,990 | 0 | DRUG_BANK, OPEN_TARGETS |
| `CCO-GEN/INTERACTS_WITH` | 0 | 76,035 | 33,505 | 0 | OPEN_TARGETS, GO_REF:0000043, GO_REF:0000044, GO_REF:0000107, GO_REF:0000033, GO_REF:0000052 |
| `MFN-GEN/INTERACTS_WITH` | 0 | 66,682 | 26,718 | 0 | OPEN_TARGETS, GO_REF:0000043, GO_REF:0000002, GO_REF:0000033, GO_REF:0000107, GO_REF:0000117 |
| `PWY-GEN/INTERACTS_WITH` | 0 | 37,799 | 12,119 | 0 | OPEN_TARGETS, REACTOME |
| `DRG-GEN/TARGET` | 570 | 12,888 | 2,409 | 0 | DRUG_BANK, WIKIPEDIA, DAILY_MED, OPEN_TARGETS |
| `DIS-PHE/PHENOTYPE_PRESENT` | 47 | 7,395 | 2,679 | 47 | OPEN_TARGETS, HPO |
| `DRG-DIS/INDICATION` | 315 | 5,900 | 3,292 | 314 | OPEN_TARGETS, CLINICAL_TRIALS, DRUG_CENTRAL, DAILY_MED, ATC, USAN |
| `DRG-GEN/INHIBITOR` | 138 | 5,591 | 564 | 0 | OPEN_TARGETS, PUBMED, OTHER, DAILY_MED, DRUG_BANK, FDA |
| `DRG-GEN/ENZYME` | 353 | 4,788 | 587 | 0 | DRUG_BANK |
| `DRG-GEN/TRANSPORTER` | 195 | 2,927 | 965 | 0 | DRUG_BANK |
| `EXP-GEN/INTERACTS_WITH` | 799 | 2,546 | 1,393 | 0 | OPEN_TARGETS, CTD |
| `DRG-GEN/ANTAGONIST` | 65 | 1,839 | 325 | 0 | OPEN_TARGETS, PUBMED, DRUG_BANK, DAILY_MED, WIKIPEDIA, ISBN |
| `DRG-GEN/AGONIST` | 71 | 1,640 | 154 | 0 | OPEN_TARGETS, PUBMED, DRUG_BANK, DAILY_MED, FDA, ISBN |
| `DRG-GEN/BLOCKER` | 15 | 1,430 | 172 | 0 | OPEN_TARGETS, PUBMED, DAILY_MED, WIKIPEDIA, FDA, DRUG_BANK |
| `DRG-DIS/CONTRAINDICATION` | 4 | 1,006 | 261 | 1 | DRUG_CENTRAL |
| `DRG-GEN/POSITIVE_ALLOSTERIC_MODULATOR` | 56 | 985 | 59 | 0 | OPEN_TARGETS, PUBMED, DRUG_BANK, WIKIPEDIA, ISBN, FDA |
| `DRG-GEN/CARRIER` | 45 | 851 | 58 | 0 | DRUG_BANK |
| `DRG-PHE/CONTRAINDICATION` | 12 | 829 | 198 | 0 | DRUG_CENTRAL |
| `DRG-PHE/ASSOCIATED_WITH` | 49 | 642 | 299 | 0 | OPEN_TARGETS, ClinicalTrials, DailyMed, DRUG_CENTRAL, ATC, USAN |
| `EXP-EXP/PARENT` | 17 | 538 | 538 | 0 | CTD |
| `DRG-GEN/MODULATOR` | 8 | 405 | 160 | 0 | OPEN_TARGETS, PUBMED, OTHER, WIKIPEDIA, FDA, EMA |
| `EXP-DIS/LINKED_TO` | 12 | 354 | 344 | 10 | CTD, MONDO |
| `EXP-BPO/INTERACTS_WITH` | 0 | 348 | 348 | 0 | CTD, GO |
| `DIS-DIS/PARENT` | 14 | 281 | 14 | 13 | OPEN_TARGETS |
| `DRG-GEN/BINDING_AGENT` | 6 | 254 | 14 | 0 | OPEN_TARGETS, PUBMED, OTHER, FDA, DAILY_MED, PMC |
| `DRG-GEN/POSITIVE_MODULATOR` | 1 | 195 | 1 | 0 | OPEN_TARGETS, PUBMED, DRUG_BANK, ISBN, FDA, WIKIPEDIA |
| `DRG-DRG/PARENT` | 33 | 191 | 99 | 0 | OPEN_TARGETS |
| `PHE-PHE/PARENT` | 2 | 168 | 132 | 0 | HPO, OPEN_TARGETS |
| `DRG-GEN/PARTIAL_AGONIST` | 20 | 141 | 38 | 0 | OPEN_TARGETS, PUBMED, WIKIPEDIA, DRUG_BANK, OTHER, ISBN |
| `DRG-GEN/STABILISER` | 2 | 123 | 2 | 0 | OPEN_TARGETS, PUBMED, DRUG_BANK, OTHER, FDA, EMA |
| `DRG-GEN/ACTIVATOR` | 1 | 90 | 12 | 0 | OPEN_TARGETS, PUBMED, DAILY_MED, FDA, OTHER, WIKIPEDIA |
| `DRG-PHE/INDICATION` | 0 | 84 | 18 | 0 | DRUG_CENTRAL |
| `DRG-GEN/HYDROLYTIC_ENZYME` | 0 | 71 | 13 | 0 | OPEN_TARGETS, DAILY_MED, PUBMED |
| `DRG-GEN/NEGATIVE_ALLOSTERIC_MODULATOR` | 14 | 64 | 21 | 0 | OPEN_TARGETS, PUBMED, WIKIPEDIA, DAILY_MED, DRUG_BANK, FDA |
| `DRG-GEN/DISRUPTING_AGENT` | 1 | 62 | 1 | 0 | PUBMED, OPEN_TARGETS, OTHER |
| `DRG-DIS/OFF_LABEL_USE` | 0 | 58 | 12 | 0 | DRUG_CENTRAL |
| `DRG-GEN/EXOGENOUS_PROTEIN` | 0 | 41 | 0 | 0 | OPEN_TARGETS, EMA, FDA, PUBMED, DAILY_MED, OTHER |
| `DRG-GEN/OPENER` | 0 | 38 | 0 | 0 | OPEN_TARGETS, PUBMED, DRUG_BANK, FDA, WIKIPEDIA, OTHER |
| `DRG-GEN/CROSS_LINKING_AGENT` | 0 | 37 | 1 | 0 | OPEN_TARGETS, PUBMED, OTHER, FDA |
| `DRG-GEN/INVERSE_AGONIST` | 22 | 37 | 22 | 0 | PUBMED, OPEN_TARGETS, WIKIPEDIA, ISBN, DRUG_BANK, DAILY_MED |
| `DRG-GEN/ANTISENSE_INHIBITOR` | 0 | 35 | 1 | 0 | OPEN_TARGETS, PUBMED, OTHER, EMA, FDA, CLINICAL_TRIALS |
| `DRG-GEN/OTHER` | 0 | 32 | 2 | 0 | OPEN_TARGETS, PUBMED, OTHER, PMC, WIKIPEDIA |
| `DRG-GEN/RELEASING_AGENT` | 0 | 31 | 0 | 0 | PUBMED, OPEN_TARGETS, WIKIPEDIA, KEGG, DAILY_MED, DRUG_BANK |
| `DRG-GEN/RNAI_INHIBITOR` | 0 | 21 | 2 | 0 | OPEN_TARGETS, PUBMED, FDA, EMA, OTHER, DAILY_MED |
| `DRG-GEN/SUBSTRATE` | 0 | 16 | 2 | 0 | OPEN_TARGETS, PUBMED, ISBN, DAILY_MED, DRUG_BANK, WIKIPEDIA |
| `DRG-GEN/ALLOSTERIC_ANTAGONIST` | 0 | 16 | 0 | 0 | OPEN_TARGETS, DRUG_BANK, FDA |
| `DRG-PHE/OFF_LABEL_USE` | 0 | 15 | 4 | 0 | DRUG_CENTRAL |
| `DRG-GEN/EXOGENOUS_GENE` | 0 | 11 | 0 | 0 | OPEN_TARGETS, EMA, FDA, CLINICAL_TRIALS, PUBMED |
| `DRG-BPO/INDICATION` | 0 | 10 | 6 | 0 | OPEN_TARGETS, CLINICAL_TRIALS |
| `DRG-GEN/VACCINE_ANTIGEN` | 2 | 9 | 2 | 0 | PUBMED, OPEN_TARGETS |
| `EXP-MFN/INTERACTS_WITH` | 0 | 8 | 8 | 0 | CTD, GO |
| `DRG-GEN/NEGATIVE_MODULATOR` | 0 | 6 | 0 | 0 | OPEN_TARGETS, PUBMED, DRUG_BANK, PATENT |
| `DRG-GEN/DEGRADER` | 0 | 6 | 0 | 0 | OPEN_TARGETS, PUBMED, DRUG_BANK, FDA, OTHER |
| `EXP-CCO/INTERACTS_WITH` | 0 | 4 | 4 | 0 | CTD, GO |
| `DRG-GEN/PROTEOLYTIC_ENZYME` | 0 | 3 | 0 | 0 | OPEN_TARGETS, PUBMED, FDA, DAILY_MED |

**Degree within the subgraph** (distinct neighbors)

| Definition | min | median | p90 | p99 | max | mean |
|---|---|---|---|---|---|---|
| (a) 1-hop | 100.0% | 20.0 | 78.0 | 286.0 | 12,760.0 | 41.4 |
| (b) 2-hop | 100.0% | 11.0 | 932.0 | 4,575.0 | 13,789.0 | 325.9 |
| (c) p95 | 100.0% | 8.0 | 560.0 | 2,102.0 | 12,760.0 | 161.8 |

**Top 20 hubs, (a) 1-hop** (by degree within the subgraph)

| ID | Name | Type | Degree in subgraph | Degree in full graph | Seed |
|---|---|---|---|---|---|
| `MONDO_0004975` | Alzheimer disease | DIS | 12,760 | 12,760 | yes |
| `MONDO_0001627` | dementia | DIS | 11,734 | 13,789 |  |
| `EFO_0005815` | tauopathy | DIS | 11,526 | 11,627 |  |
| `MONDO_0024237` | inherited neurodegenerative disorder | DIS | 8,857 | 11,702 |  |
| `MONDO_0000426` | autosomal dominant disease | DIS | 8,028 | 10,709 |  |
| `MONDO_0015547` | hereditary dementia | DIS | 7,212 | 9,129 |  |
| `ENSG00000150991` | ubiquitin C | GEN | 4,085 | 7,875 |  |
| `HP_0001300` | Parkinsonism | PHE | 1,794 | 2,016 |  |
| `HP_0001250` | Seizure | PHE | 1,413 | 2,968 |  |
| `ENSG00000134954` | ETS proto-oncogene 1, transcription factor | GEN | 1,234 | 3,550 |  |
| `ENSG00000179348` | GATA binding protein 2 | GEN | 1,111 | 3,400 |  |
| `HP_0000726` | Dementia | PHE | 999 | 1,323 |  |
| `ENSG00000169083` | androgen receptor | GEN | 968 | 4,960 |  |
| `ENSG00000100393` | E1A binding protein p300 | GEN | 900 | 3,851 |  |
| `ENSG00000102974` | CCCTC-binding factor | GEN | 887 | 2,935 |  |
| `HP_0001332` | Dystonia | PHE | 884 | 1,306 |  |
| `ENSG00000136997` | MYC proto-oncogene, bHLH transcription factor | GEN | 834 | 4,673 |  |
| `ENSG00000142192` | amyloid beta precursor protein | GEN | 764 | 3,808 |  |
| `ENSG00000065978` | Y-box binding protein 1 | GEN | 723 | 1,950 |  |
| `ENSG00000091831` | estrogen receptor 1 | GEN | 700 | 5,290 |  |

**Top 20 hubs, (b) 2-hop** (by degree within the subgraph)

| ID | Name | Type | Degree in subgraph | Degree in full graph | Seed |
|---|---|---|---|---|---|
| `MONDO_0001627` | dementia | DIS | 13,789 | 13,789 |  |
| `MONDO_0004975` | Alzheimer disease | DIS | 12,760 | 12,760 | yes |
| `EFO_0000618` | nervous system disease | DIS | 12,296 | 20,468 |  |
| `EFO_0000616` | neoplasm | DIS | 12,267 | 24,478 |  |
| `EFO_0009386` | central nervous system disease | DIS | 12,266 | 19,797 |  |
| `UBERON_0005386` | olfactory segment of nasal mucosa | ANA | 12,261 | 49,204 |  |
| `UBERON_0001987` | placenta | ANA | 12,253 | 49,941 |  |
| `UBERON_0002114` | duodenum | ANA | 12,250 | 46,172 |  |
| `UBERON_0001052` | rectum | ANA | 12,249 | 48,226 |  |
| `UBERON_0002110` | gallbladder | ANA | 12,247 | 48,022 |  |
| `UBERON_0001199` | mucosa of stomach | ANA | 12,219 | 44,221 |  |
| `UBERON_0001135` | smooth muscle tissue | ANA | 12,218 | 46,858 |  |
| `UBERON_0004991` | mucosa of transverse colon | ANA | 12,218 | 43,697 |  |
| `MONDO_0045024` | cancer or benign tumor | DIS | 12,217 | 22,728 |  |
| `UBERON_0001154` | vermiform appendix | ANA | 12,216 | 46,923 |  |
| `MONDO_0023370` | neoplastic disease or syndrome | DIS | 12,214 | 22,685 |  |
| `UBERON_0001302` | right fallopian tube | ANA | 12,212 | 44,369 |  |
| `UBERON_0002098` | apex of heart | ANA | 12,212 | 43,755 |  |
| `UBERON_0000468` | multicellular organism | ANA | 12,206 | 48,457 |  |
| `UBERON_0035834` | lower esophagus mucosa | ANA | 12,191 | 43,069 |  |

**Top 20 hubs, (c) p95** (by degree within the subgraph)

| ID | Name | Type | Degree in subgraph | Degree in full graph | Seed |
|---|---|---|---|---|---|
| `MONDO_0004975` | Alzheimer disease | DIS | 12,760 | 12,760 | yes |
| `MONDO_0001627` | dementia | DIS | 11,734 | 13,789 |  |
| `EFO_0005815` | tauopathy | DIS | 11,526 | 11,627 |  |
| `MONDO_0024237` | inherited neurodegenerative disorder | DIS | 8,857 | 11,702 |  |
| `MONDO_0000426` | autosomal dominant disease | DIS | 8,028 | 10,709 |  |
| `MONDO_0015547` | hereditary dementia | DIS | 7,212 | 9,129 |  |
| `EFO_0000618` | nervous system disease | DIS | 6,865 | 20,468 |  |
| `UBERON_0001987` | placenta | ANA | 6,841 | 49,941 |  |
| `UBERON_0005386` | olfactory segment of nasal mucosa | ANA | 6,841 | 49,204 |  |
| `UBERON_0002114` | duodenum | ANA | 6,835 | 46,172 |  |
| `EFO_0009386` | central nervous system disease | DIS | 6,834 | 19,797 |  |
| `UBERON_0001052` | rectum | ANA | 6,832 | 48,226 |  |
| `UBERON_0002110` | gallbladder | ANA | 6,829 | 48,022 |  |
| `UBERON_0001135` | smooth muscle tissue | ANA | 6,816 | 46,858 |  |
| `EFO_0000616` | neoplasm | DIS | 6,814 | 24,478 |  |
| `UBERON_0001154` | vermiform appendix | ANA | 6,811 | 46,923 |  |
| `UBERON_0001295` | endometrium | ANA | 6,804 | 53,523 |  |
| `UBERON_0001199` | mucosa of stomach | ANA | 6,803 | 44,221 |  |
| `UBERON_0004991` | mucosa of transverse colon | ANA | 6,801 | 43,697 |  |
| `UBERON_0002098` | apex of heart | ANA | 6,798 | 43,755 |  |

**Top 10 two-hop intermediates, (a) 1-hop**

| ID | Name | Type | Paths | Path share | Endpoint share |
|---|---|---|---|---|---|
| `MONDO_0024237` | inherited neurodegenerative disorder | DIS | 17,710 | 3.0% | 70.2% |
| `MONDO_0015547` | hereditary dementia | DIS | 14,420 | 2.4% | 57.2% |
| `MONDO_0001627` | dementia | DIS | 11,733 | 2.0% | 93.1% |
| `EFO_0005815` | tauopathy | DIS | 11,525 | 1.9% | 91.4% |
| `ENSG00000150991` | ubiquitin C | GEN | 8,166 | 1.4% | 32.4% |
| `MONDO_0000426` | autosomal dominant disease | DIS | 8,027 | 1.4% | 63.7% |
| `ENSG00000142192` | amyloid beta precursor protein | GEN | 5,299 | 0.9% | 6.0% |
| `HP_0001300` | Parkinsonism | PHE | 3,584 | 0.6% | 14.2% |
| `HP_0000726` | Dementia | PHE | 2,988 | 0.5% | 7.9% |
| `ENSG00000170345` | Fos proto-oncogene, AP-1 transcription factor subunit | GEN | 2,148 | 0.4% | 4.3% |

**Top 10 two-hop intermediates, (b) 2-hop**

| ID | Name | Type | Paths | Path share | Endpoint share |
|---|---|---|---|---|---|
| `ENSG00000232810` | tumor necrosis factor | GEN | 30,685 | 0.2% | 8.2% |
| `ENSG00000130203` | apolipoprotein E | GEN | 26,978 | 0.2% | 5.1% |
| `ENSG00000142192` | amyloid beta precursor protein | GEN | 26,607 | 0.2% | 5.0% |
| `MONDO_0024237` | inherited neurodegenerative disorder | DIS | 23,400 | 0.2% | 15.5% |
| `ENSG00000105329` | transforming growth factor beta 1 | GEN | 21,440 | 0.2% | 7.1% |
| `ENSG00000186868` | microtubule associated protein tau | GEN | 19,894 | 0.1% | 3.8% |
| `ENSG00000148120` | aminopeptidase O (putative) | GEN | 18,767 | 0.1% | 3.6% |
| `ENSG00000159640` | angiotensin I converting enzyme | GEN | 18,310 | 0.1% | 4.9% |
| `MONDO_0015547` | hereditary dementia | DIS | 18,254 | 0.1% | 12.1% |
| `ENSG00000110680` | calcitonin related polypeptide alpha | GEN | 18,240 | 0.1% | 4.8% |

**Top 10 two-hop intermediates, (c) p95**

| ID | Name | Type | Paths | Path share | Endpoint share |
|---|---|---|---|---|---|
| `MONDO_0024237` | inherited neurodegenerative disorder | DIS | 17,710 | 0.4% | 16.8% |
| `MONDO_0015547` | hereditary dementia | DIS | 14,420 | 0.3% | 13.7% |
| `MONDO_0001627` | dementia | DIS | 11,733 | 0.2% | 22.3% |
| `EFO_0005815` | tauopathy | DIS | 11,525 | 0.2% | 21.9% |
| `ENSG00000150991` | ubiquitin C | GEN | 8,166 | 0.2% | 7.8% |
| `MONDO_0000426` | autosomal dominant disease | DIS | 8,027 | 0.2% | 15.2% |
| `ENSG00000142192` | amyloid beta precursor protein | GEN | 5,299 | 0.1% | 1.4% |
| `ENSG00000234066` | taste 2 receptor member 62, pseudogene | GEN | 4,485 | 0.1% | 1.7% |
| `HP_0001300` | Parkinsonism | PHE | 3,584 | 0.1% | 3.4% |
| `ENSG00000126214` | kinesin light chain 1 | GEN | 3,572 | 0.1% | 1.7% |

**Candidate breakdowns, (c) p95**

| Breakdown | Counts |
|---|---|
| Single-hop facts by relation | `DIS-GEN/ASSOCIATED_WITH` 13,368; `DRG-DIS/INDICATION` 314; `DIS-PHE/PHENOTYPE_PRESENT` 47; `DIS-DIS/PARENT` 13; `EXP-DIS/LINKED_TO` 10; `DRG-DIS/CONTRAINDICATION` 1 |
| Top two-hop patterns | `DIS-GEN/ASSOCIATED_WITH | DIS-GEN/ASSOCIATED_WITH` 2,210,381; `DIS-GEN/ASSOCIATED_WITH | ANA-GEN/EXPRESSION_PRESENT` 1,376,910; `DIS-GEN/ASSOCIATED_WITH | GEN-GEN/INTERACTS_WITH` 484,583; `DIS-GEN/ASSOCIATED_WITH | ANA-GEN/EXPRESSION_ABSENT` 266,098; `DIS-GEN/ASSOCIATED_WITH | PHE-GEN/ASSOCIATED_WITH` 168,548; `DIS-DIS/PARENT | DIS-GEN/ASSOCIATED_WITH` 63,406; `DIS-GEN/ASSOCIATED_WITH | BPO-GEN/INTERACTS_WITH` 37,483; `DIS-GEN/ASSOCIATED_WITH | CCO-GEN/INTERACTS_WITH` 34,605 |
| Unanswerable candidates by relation (distinct y) | `DIS-GEN/ASSOCIATED_WITH` 17,534; `DIS-DIS/PARENT` 16,582; `DRG-DIS/INDICATION` 3,951; `DRG-DIS/CONTRAINDICATION` 3,951; `DIS-PHE/PHENOTYPE_PRESENT` 1,304; `EXP-DIS/LINKED_TO` 338 |
| Contradicted pairs by relation | `ANA-GEN/EXPRESSION_PRESENT` 1,337,490; `ANA-GEN/EXPRESSION_ABSENT` 256,567; `DRG-DIS/INDICATION` 3,292; `DRG-DIS/CONTRAINDICATION` 261; `DRG-PHE/CONTRAINDICATION` 198; `DRG-PHE/INDICATION` 18 |
| Seed fan-out (seed, relation): edges | MONDO_0004975 | DIS-GEN/ASSOCIATED_WITH: 12,414; MONDO_0100087 | DIS-GEN/ASSOCIATED_WITH: 405; MONDO_0004975 | DRG-DIS/INDICATION: 314; EFO_1001870 | DIS-GEN/ASSOCIATED_WITH: 258; MONDO_0015140 | DIS-GEN/ASSOCIATED_WITH: 167; MONDO_0011913 | DIS-GEN/ASSOCIATED_WITH: 75; MONDO_0007088 | DIS-GEN/ASSOCIATED_WITH: 30; MONDO_0011913 | DIS-PHE/PHENOTYPE_PRESENT: 22; MONDO_0014265 | DIS-GEN/ASSOCIATED_WITH: 19; MONDO_0004975 | DIS-PHE/PHENOTYPE_PRESENT: 16; MONDO_0004975 | EXP-DIS/LINKED_TO: 10; MONDO_0007088 | DIS-PHE/PHENOTYPE_PRESENT: 9 |
| Shared-neighbor disease pairs by shared count | pairs 32,697; shared_1 10,669; shared_2_5 10,072; shared_6_20 5,752; shared_gt_20 6,204; pairs_with_1_to_20_shared 26,493; distinct_other_diseases 16,582 |

**Licensing, (c) p95**: share of edges with each restricted source

| Edge set | Edges | DrugBank | SIDER | DisGeNET | UMLS | Any |
|---|---|---|---|---|---|---|
| single hop facts | 13,753 | 0.0% | 0.0% | 0.9% | 0.0% | 0.9% |
| edges on two hop paths | 4,273,588 | 0.6% | 0.0% | 0.1% | 0.0% | 0.7% |
| all subgraph edges | 4,278,211 | 0.6% | 0.1% | 0.1% | 0.0% | 0.8% |

### What this means for the graph choice

These are facts only, with no recommendation.

**Outcome.** Neither graph passes every gating criterion, for either seed set,
under any reported definition. Every case fails C2 (single-hop relation
diversity), because the seed's facts sit in three relations:

| Graph (proposed seed) | Three largest relations | Balanced capacity (needs 270) |
|---|---|---|
| PrimeKG | `disease_protein` 205, `disease_phenotype_positive` 67, `contraindication` 58 | 267 |
| OptimusKG | `DIS-GEN/ASSOCIATED_WITH` 13,368, `DRG-DIS/INDICATION` 314, `DIS-PHE/PHENOTYPE_PRESENT` 47 | 287 |

OptimusKG passes the capacity half of C2 but has fewer than four relations
with 30 facts each.

For (b) and (c) with the proposed seed, C2 is the only gating failure in both
graphs. The other failures:

- **(a) 1-hop:** PrimeKG also fails C3 (174 endpoints), and OptimusKG fails
  C7 (2 relations with ≥ 20 candidates).
- **PrimeKG, main node alone:** fails C1 (233 single-hop facts).
- **PrimeKG, proposed seed:** passes C1 with 364 facts, but 110 of the 111
  edges on "Alzheimer disease without neurofibrillary tangles" repeat edges of
  the main node. Merged by (partner, relation), the seed has 243 distinct
  facts.

**Size.** The two graphs differ by orders of magnitude around Alzheimer's
disease.

| | PrimeKG | OptimusKG |
|---|---|---|
| Main AD node degree | 233 | 12,760 |
| Proposed seed, (c) p95 | 4,157 nodes, 9,790 edges | 52,814 nodes, 4,278,211 edges |

In OptimusKG, 12,320 of the main node's 12,414 gene associations come from
Open Targets alone. Their median `evidence_score` is 0.011; 1,621 score ≥ 0.1
and 76 score ≥ 0.5. No score filter is applied in these counts.

**Licensing.** The licenses the Dataverse datasets state differ, and so does
the share of candidate facts from restricted sources (DrugBank, SIDER,
DisGeNET, UMLS).

| | PrimeKG | OptimusKG |
|---|---|---|
| Dataset license (Dataverse) | CC0 1.0 | CC BY-NC-SA 4.0 |
| Single-hop facts from a restricted source (proposed seed) | 56.3%, all DisGeNET, by the relation-level mapping | 0.9% |

**Tool caps.** The toolset returns about 20 edges per result.

| Proposed seed | Single-hop facts in a (seed, relation) group of ≤ 20 edges |
|---|---|
| PrimeKG | 56 of 364 |
| OptimusKG | 68 of 13,753 |

**What happens next under the rule.** A failing result for the chosen graph
and definition triggers the expansion to the broader neurodegeneration seed.
That seed was not measured here.

## PrimeKG relation sources, verified

Checked on 2026-11-19. `kg.csv` carries no per-edge source, so every PrimeKG
licensing number rests on this relation-level table
(`PRIMEKG_RELATION_SOURCES` in `scripts/data/load.py`). It was first written
from memory, and has now been checked against two sources:

- **[P]** the PrimeKG paper: Chandak, Huang & Zitnik, "Building a knowledge
  graph to enable precision medicine", *Scientific Data* 10:67 (2023),
  doi:10.1038/s41597-023-01960-3, Methods, read through PubMed Central
  (PMC9893183).
- **[R]** the build code in https://github.com/mims-harvard/PrimeKG at commit
  `32a3818`: `knowledge_graph/build_graph.ipynb` (one cell per relation, then
  one cell that concatenates the 28 dataframes into the graph) and
  `datasets/primary_data_resources.sh`. The repo was refreshed in July 2023
  with new download links and source releases. The Dataverse v2.1 files
  predate that refresh and, per the repo README, were not changed; the
  refresh did not change which source feeds which relation.

Where [P] and [R] disagree, [R] is taken, since it built the file.

| Relation | Source(s) | Restricted (R3) | Where it is stated |
|---|---|---|---|
| `protein_protein` | Menche et al. interactome, BioGRID, STRING, HuRI | no | [P] "Four sources of physical protein-protein interactions"; [R] reads a manually copied `ppi/protein_protein.csv` |
| `drug_protein` (target, enzyme, carrier, transporter) | DrugBank | DrugBank | [P] "DrugBank"; [R] cell "Drug protein interactions (DrugBank)" |
| `drug_drug` | DrugBank | DrugBank | [P] "DrugBank"; [R] cell "Drug drug interactions (DrugBank)" |
| `drug_effect` | SIDER | SIDER | [P] "Side effect knowledgebases"; [R] cell "Drug effect interactions (SIDER)" |
| `indication`, `contraindication`, `off-label use` | DrugCentral | no (see the UMLS note) | [P] "Drug Central"; [R] cell "Drug disease interactions" |
| `disease_protein` | DisGeNET | DisGeNET | [P] "DisGeNET" and "Resolving overlap between phenotype and disease nodes"; [R] cells "Disease protein interactions (DisGenNet)" and "HANDLE PROTEIN EFFECT" |
| `phenotype_protein` | DisGeNET (**was: HPO annotations**) | DisGeNET | [R] cell "Effect protein interactions (DisGenNet)": DisGeNET curated rows with `diseaseType == "phenotype"`, mapped to HPO. [P]'s HPO section lists protein-phenotype edges among HPO's outputs, but the code builds this relation only from DisGeNET, and [P] says "Protein-phenotype edges extracted from DisGeNet were converted to protein-disease relations" where the phenotype is also a MONDO disease |
| `disease_phenotype_positive` | HPO annotations (`phenotype.hpoa`), HPO | no | [P] "Human phenotype ontology"; [R] cells "Disease effect interactions (HPO-A)" and "HANDLE EFFECT EFFECT" (HPO parent-child edges with one end in MONDO become disease-phenotype edges) |
| `disease_phenotype_negative` | HPO annotations | no | as above |
| `disease_disease` | MONDO, HPO | no | [P] "MONDO disease ontology"; [R] cells "Disease disease interactions (MONDO)" and "HANDLE EFFECT EFFECT" (HPO parent-child edges with both ends in MONDO) |
| `phenotype_phenotype` | HPO | no | [P] "Human phenotype ontology"; [R] cell "Effect effect interactions (HPO)" |
| `anatomy_anatomy` | UBERON | no | [P] "Uberon"; [R] cell "Anatomy anatomy interactions (UBERON)" |
| `anatomy_protein_present`, `anatomy_protein_absent` | Bgee | no | [P] "Bgee"; [R] cell "Anatomy Protein (BGEE)" |
| `bioprocess_protein`, `cellcomp_protein`, `molfunc_protein` | GO annotations (NCBI gene2go) | no | [P] "Entrez gene"; [R] cell "Go protein interactions (Gene2GO)" |
| `bioprocess_bioprocess`, `cellcomp_cellcomp`, `molfunc_molfunc` | GO | no | [P] "Gene ontology"; [R] cell "Go terms interactions (GO)" |
| `pathway_protein`, `pathway_pathway` | Reactome | no | [P] "Reactome"; [R] cells "Pathways", "Pathway protein interactions" |
| `exposure_*` (six relations) | CTD | no | [P] "Comparative toxicogenomics database"; [R] cells "Exposure ... interactions (CTD)" |

**The UMLS crosswalk.** [R] maps DrugCentral's and DisGeNET's disease
identifiers (UMLS CUIs) to MONDO through a crosswalk it builds from the UMLS
Metathesaurus (`MRCONSO.RRF`, which `primary_data_resources.sh` says must be
downloaded "manually after authentication at uts.nlm.nih.gov";
`datasets/processing_scripts/umls.py` and `map_umls_mondo.py`). So the disease
end of every `indication`, `contraindication`, `off-label use` and
`disease_protein` edge was placed using UMLS-licensed data, although the facts
come from DrugCentral and DisGeNET. R3's "UMLS-derived" category is read here
as "the fact comes from UMLS", which no relation meets. The crosswalk share is
reported beside R3, never inside it: 76.4% of the Alzheimer's proposed seed's
single-hop facts and 53.3% of the broader seed's. Whether that counts as
UMLS-derived for the shipping decision is an open question. Other joins, for
completeness: drugs reach DrugBank IDs through DrugBank's vocabulary file (by
CAS number, and by ATC code for SIDER), and SIDER's MedDRA terms reach HPO
through HPO's own cross-references. Their terms were not checked here.

**What changed.** Only `phenotype_protein` moved into a restricted source. It
has no seed facts, so single-hop R3 is unchanged for every seed. Two-hop and
subgraph R3 rise slightly (see the dated note under "Expansion test:
results"). In the full graph, the share of edges from a restricted source
goes from 37.2% to 37.3%.

## Expansion test: broader neurodegeneration results

Checked on 2026-11-19 against the expansion test **exactly as committed**
above: the same metrics, the same criteria C1 to C8 with the same thresholds,
the same R1 to R4, the same 3× headroom and held-out size of 100. Every
threshold is still **proposed**. Nothing in the "Expansion test" section
changed. The run follows step 3 of the decision rule: the Alzheimer's-only
subgraph failed C2 in both graphs, so the seed expands to Alzheimer's
disease, Parkinson disease, frontotemporal dementia, amyotrophic lateral
sclerosis, Huntington disease and dementia with Lewy bodies, each with its
subtypes under the Alzheimer's seed rule. Produced by
`scripts/data/measure.py`, `scripts/data/direction.py` and
`scripts/data/report.py` from the same files as the Alzheimer's-only results
(sha256 rechecked against the manifest; nothing was downloaded again). No
question was drafted, and no model was called.

How to read these results:

- **Seed rule, unchanged in substance.** For each disease: the main disease
  node plus its subtypes in the graph's own disease hierarchy.
  - **OptimusKG:** the main node and all its descendants over
    `DIS-DIS/PARENT`, as for Alzheimer's.
  - **PrimeKG:** the main node and its `disease_disease` neighbors whose name
    contains the **main node's name**. For Alzheimer's that is "Alzheimer
    disease", which picks the same four neighbors as the first run's
    "Alzheimer" filter. For Parkinson disease it keeps the name filter from
    taking parents named "parkinsonian disorder". One node the filter matches
    is excluded by hand, with the reason given: `38307` "Huntington disease
    and related disorders", a parent of Huntington disease (degree 3).
- **Candidate tables** list, per disease, every node whose name (or, in
  OptimusKG, a synonym) contains the disease's search term, plus the main
  node's hierarchy neighbors. For each one they give its role, whether it is
  in the proposed seed and, if not, why. Two PrimeKG columns are
  informational only. **File order** is the node's role read from `kg.csv`'s
  row order (see "Directed-relation inventory"); PrimeKG's grouped disease
  nodes merge several MONDO diseases, so a grouped neighbor can read as a
  parent. **MONDO (via OptimusKG)** places the node relative to the disease's
  main node in MONDO's hierarchy as OptimusKG carries it. Neither column
  chooses the seed.
- **Dementia with Lewy bodies** is named "Lewy body dementia" in both graphs
  (PrimeKG `29296`, MONDO 7488; OptimusKG `EFO_0006792`). Neither graph gives
  it subtypes.
- **Seed sets.** "Proposed (all six)" is the union of the six proposed seeds.
  "Main nodes only" is the six main nodes. Choosing between them, and among
  the per-disease seeds, is Kaitlin's call.
- **Per-disease contribution** is reported only. It describes what each
  disease adds to the single-hop pool and to C2's inputs; it is not a
  criterion and defines no new test.
- **R3** uses the verified PrimeKG mapping (see "PrimeKG relation sources,
  verified"). The extra "R3 note" rows give the share of PrimeKG facts whose
  disease end was placed through the UMLS crosswalk. They are not counted in
  R3.

### Broader seed: seeds by disease

| Disease | PrimeKG main | PrimeKG seed nodes | PrimeKG single-hop facts | OptimusKG main | OptimusKG seed nodes | OptimusKG single-hop facts |
|---|---|---|---|---|---|---|
| Alzheimer's disease (AD) | `28780` | 5 | 364 | `MONDO_0004975` | 7 | 13,753 |
| Parkinson disease (PD) | `28547` | 7 | 728 | `MONDO_0005180` | 16 | 8,878 |
| frontotemporal dementia (FTD) | `35691` | 3 | 129 | `MONDO_0017276` | 10 | 5,359 |
| amyotrophic lateral sclerosis (ALS) | `27248` | 4 | 354 | `MONDO_0004976` | 17 | 4,487 |
| Huntington disease (HD) | `31245` | 2 | 118 | `MONDO_0007739` | 2 | 5,165 |
| dementia with Lewy bodies (DLB) | `29296` | 1 | 36 | `EFO_0006792` | 1 | 1,281 |
| **Combined (proposed)** |  | 22 | 1,729 |  | 53 | 38,923 |

### Broader seed: candidates, PrimeKG

#### Alzheimer's disease (AD), PrimeKG

| ID | Name | Degree | Role | File order | MONDO (via OptimusKG) | Edges by relation | In seed | Excluded because |
|---|---|---|---|---|---|---|---|---|
| `28780` | Alzheimer disease | 233 | main |  | same | disease_protein 101, contraindication 56, disease_phenotype_positive 50 (+3 more) | yes |  |
| `83960` | Alzheimer disease without neurofibrillary tangles | 111 | disease_disease neighbor | child | not in OptimusKG | disease_protein 101, indication 7, contraindication 2 (+1 more) | yes |  |
| `32654` | familial Alzheimer disease | 15 | disease_disease neighbor | parent | descendant | disease_phenotype_positive 12, disease_disease 2, disease_protein 1 | yes |  |
| `31527` | Alzheimer disease, familial early-onset, with coexisting amyloid and prion pathology | 6 | disease_disease neighbor | child | not in OptimusKG | disease_phenotype_positive 5, disease_disease 1 | yes |  |
| `94982` | Alzheimer disease, susceptibility to, mitochondrial | 3 | disease_disease neighbor | child | not in OptimusKG | disease_protein 2, disease_disease 1 | yes |  |
| `35655` | autosomal dominant disease | 133 | disease_disease neighbor | parent | unrelated | disease_disease 133 | no | disease_disease neighbor whose name lacks the main node's name |
| `37573` | dementia (disease) | 69 | disease_disease neighbor | parent | ancestor | contraindication 28, disease_protein 17, exposure_disease 12 (+2 more) | no | disease_disease neighbor whose name lacks the main node's name |
| `35439` | inherited disease susceptibility | 67 | disease_disease neighbor | parent | not in OptimusKG | disease_disease 67 | no | disease_disease neighbor whose name lacks the main node's name |
| `35480` | inherited neurodegenerative disorder | 65 | disease_disease neighbor | parent | unrelated | disease_disease 65 | no | disease_disease neighbor whose name lacks the main node's name |
| `38241` | genetic dementia | 16 | disease_disease neighbor | parent | unrelated | disease_disease 16 | no | disease_disease neighbor whose name lacks the main node's name |
| `28310` | dementia/parkinsonism with non-Alzheimer amyloid plaques | 5 | name match |  | not in OptimusKG | disease_phenotype_positive 4, disease_disease 1 | no | not a disease_disease neighbor of the main node |
| `37652` | tauopathy | 2 | disease_disease neighbor | parent | not in OptimusKG | disease_disease 2 | no | disease_disease neighbor whose name lacks the main node's name |

Non-disease nodes matching the search terms (not in the seed: not a disease node): `129024` Deregulated CDK5 triggers multiple neurodegenerative pathways in Alzheimer's disease models (pathway).

Proposed seed (5 nodes): `28780`, `31527`, `32654`, `83960`, `94982`. Rule: 28780 plus its disease_disease neighbors whose name contains 'Alzheimer disease'.

Grouped node `28780` (Alzheimer disease) merges 21 MONDO diseases: MONDO:11913 Alzheimer disease 3; MONDO:7088 Alzheimer disease type 1; MONDO:11743 Alzheimer disease 4; MONDO:12153 Alzheimer disease 9; MONDO:7089 Alzheimer disease 2; MONDO:11194 Alzheimer disease 5; MONDO:12321 Alzheimer disease 10; MONDO:15140 early-onset autosomal dominant Alzheimer disease; MONDO:4975 Alzheimer disease; MONDO:11561 Alzheimer disease 6; MONDO:11647 Alzheimer disease 7; MONDO:11777 Alzheimer disease 8; MONDO:12344 Alzheimer disease 11; MONDO:12609 Alzheimer disease 12; MONDO:12630 Alzheimer disease 13; MONDO:12631 Alzheimer disease 14; MONDO:14265 Alzheimer disease 18; MONDO:10422 Alzheimer disease 16; MONDO:14036 Alzheimer disease 17; MONDO:14316 Alzheimer disease 19; MONDO:100088 late-onset familial alzheimer disease.

Grouped node `32654` (familial Alzheimer disease) merges 2 MONDO diseases: MONDO:17233 familial Alzheimer-like prion disease; MONDO:100087 familial Alzheimer disease.

#### Parkinson disease (PD), PrimeKG

| ID | Name | Degree | Role | File order | MONDO (via OptimusKG) | Edges by relation | In seed | Excluded because |
|---|---|---|---|---|---|---|---|---|
| `28547` | Parkinson disease | 305 | main |  | same | disease_protein 104, disease_phenotype_positive 73, contraindication 45 (+3 more) | yes |  |
| `33241` | hereditary late onset Parkinson disease | 145 | disease_disease neighbor | parent | not in OptimusKG | disease_protein 85, disease_phenotype_positive 32, indication 22 (+2 more) | yes |  |
| `28801` | juvenile onset Parkinson disease 19A | 127 | disease_disease neighbor | child | not in OptimusKG | disease_protein 86, indication 22, disease_phenotype_positive 16 (+2 more) | yes |  |
| `27576` | autosomal recessive Parkinson disease | 84 | disease_disease neighbor | child | descendant | disease_phenotype_positive 43, disease_protein 35, disease_disease 4 (+1 more) | yes |  |
| `32468` | postencephalitic Parkinson disease | 51 | disease_disease neighbor | child | not in OptimusKG | disease_phenotype_positive 41, indication 8, disease_disease 2 | yes |  |
| `28182` | Parkinson disease, autosomal dominant, susceptibility to | 17 | disease_disease neighbor | child | not in OptimusKG | disease_phenotype_positive 11, disease_disease 3, disease_protein 3 | yes |  |
| `32001` | parkinson disease, autosomal dominant | 7 | disease_disease neighbor | child | not in OptimusKG | disease_phenotype_positive 5, disease_disease 1, disease_protein 1 | yes |  |
| `30093` | parkinsonian-pyramidal syndrome | 141 | disease_disease neighbor | child | descendant | disease_protein 86, disease_phenotype_positive 30, indication 22 (+2 more) | no | disease_disease neighbor whose name lacks the main node's name |
| `32974` | atypical juvenile parkinsonism | 138 | name match |  | unrelated | disease_protein 88, disease_phenotype_positive 25, indication 22 (+2 more) | no | not a disease_disease neighbor of the main node |
| `28974` | early-onset parkinsonism-intellectual disability syndrome | 132 | name match |  | unrelated | disease_protein 86, indication 22, disease_phenotype_positive 21 (+2 more) | no | not a disease_disease neighbor of the main node |
| `31408` | X-linked parkinsonism-spasticity syndrome | 127 | name match |  | unrelated | disease_protein 86, indication 22, disease_phenotype_positive 16 (+2 more) | no | not a disease_disease neighbor of the main node |
| `32759` | hemiparkinsonism-hemiatrophy syndrome | 124 | name match |  | unrelated | disease_protein 85, indication 22, disease_phenotype_positive 15 (+2 more) | no | not a disease_disease neighbor of the main node |
| `28149` | orthostatic hypotension | 100 | disease_disease neighbor | parent | unrelated | contraindication 77, disease_disease 10, disease_phenotype_positive 6 (+3 more) | no | disease_disease neighbor whose name lacks the main node's name |
| `37706` | parkinsonian disorder | 94 | disease_disease neighbor | parent | ancestor | contraindication 45, disease_protein 30, indication 12 (+2 more) | no | disease_disease neighbor whose name lacks the main node's name |
| `29670` | Kufor-Rakeb syndrome | 73 | disease_disease neighbor | child | descendant | disease_phenotype_positive 67, disease_disease 4, disease_protein 2 | no | disease_disease neighbor whose name lacks the main node's name |
| `35480` | inherited neurodegenerative disorder | 65 | disease_disease neighbor | parent | unrelated | disease_disease 65 | no | disease_disease neighbor whose name lacks the main node's name |
| `29875` | infantile dystonia-parkinsonism | 45 | name match |  | unrelated | disease_phenotype_positive 40, disease_disease 3, disease_protein 2 | no | not a disease_disease neighbor of the main node |
| `31354` | amyotrophic lateral sclerosis-parkinsonism-dementia complex | 44 | name match |  | unrelated | disease_protein 34, disease_phenotype_positive 9, disease_disease 1 | no | not a disease_disease neighbor of the main node |
| `27642` | progressive supranuclear palsy-parkinsonism syndrome | 35 | name match |  | unrelated | disease_phenotype_positive 27, disease_protein 6, disease_disease 1 (+1 more) | no | not a disease_disease neighbor of the main node |
| `33015` | multiple system atrophy, parkinsonian type | 32 | name match |  | not in OptimusKG | disease_phenotype_positive 29, disease_disease 2, disease_protein 1 | no | not a disease_disease neighbor of the main node |
| `28439` | X-linked dystonia-parkinsonism | 31 | name match |  | unrelated | disease_phenotype_positive 20, disease_protein 8, disease_disease 3 | no | not a disease_disease neighbor of the main node |
| `28540` | Wolff-Parkinson-white syndrome (disease) | 28 | name match |  | not in OptimusKG | contraindication 15, disease_phenotype_positive 10, disease_disease 2 (+1 more) | no | not a disease_disease neighbor of the main node |
| `27210` | Charcot-Marie-Tooth disease with ptosis and parkinsonism | 27 | name match |  | not in OptimusKG | disease_phenotype_positive 25, disease_disease 1, disease_phenotype_negative 1 | no | not a disease_disease neighbor of the main node |
| `27682` | parkinsonism with dementia of Guadeloupe | 27 | name match |  | not in OptimusKG | disease_phenotype_positive 24, disease_disease 2, disease_phenotype_negative 1 | no | not a disease_disease neighbor of the main node |
| `33265` | parkinsonism due to ATP13A2 deficiency | 25 | name match |  | descendant | disease_phenotype_positive 17, disease_protein 6, disease_disease 2 | no | not a disease_disease neighbor of the main node |
| `35693` | neurodegenerative disease | 23 | disease_disease neighbor | parent | not in OptimusKG | disease_disease 23 | no | disease_disease neighbor whose name lacks the main node's name |
| `32700` | cyanide-induced parkinsonism | 22 | name match |  | not in OptimusKG | disease_phenotype_positive 20, disease_disease 2 | no | not a disease_disease neighbor of the main node |
| `36447` | parkinsonian syndrome due to neurodegenerative disease | 19 | disease_disease neighbor | parent | not in OptimusKG | disease_disease 19 | no | disease_disease neighbor whose name lacks the main node's name |
| `32705` | carbon monoxide-induced parkinsonism | 11 | name match |  | unrelated | disease_phenotype_positive 9, disease_disease 2 | no | not a disease_disease neighbor of the main node |
| `28310` | dementia/parkinsonism with non-Alzheimer amyloid plaques | 5 | name match |  | not in OptimusKG | disease_phenotype_positive 4, disease_disease 1 | no | not a disease_disease neighbor of the main node |
| `37707` | synucleinopathy | 4 | disease_disease neighbor | parent | not in OptimusKG | disease_disease 4 | no | disease_disease neighbor whose name lacks the main node's name |
| `99983` | TH-deficient infantile parkinsonism and motor delay | 1 | name match |  | not in OptimusKG | disease_disease 1 | no | not a disease_disease neighbor of the main node |

Non-disease nodes matching the search terms (not in the seed: not a disease node): `23153` Parkinsonism (effect/phenotype); `85184` Parkinsonism with favorable response to dopaminergic medication (effect/phenotype); `128675` Defective SLC6A3 causes Parkinsonism-dystonia infantile (PKDYS) (pathway); `128704` Defective SLC6A3 causes Parkinsonism-dystonia infantile (PKDYS) (pathway); `61894` Antiparkinson Agents (exposure).

Proposed seed (7 nodes): `27576`, `28182`, `28547`, `28801`, `32001`, `32468`, `33241`. Rule: 28547 plus its disease_disease neighbors whose name contains 'Parkinson disease'.

Grouped node `28547` (Parkinson disease) merges 18 MONDO diseases: MONDO:11764 autosomal dominant Parkinson disease 8; MONDO:11658 autosomal recessive early-onset Parkinson disease 7; MONDO:13625 Parkinson disease 17; MONDO:8199 late-onset Parkinson disease; MONDO:14604 Parkinson disease 21; MONDO:11613 autosomal recessive early-onset Parkinson disease 6; MONDO:14233 early-onset Parkinson disease 20; MONDO:11562 autosomal dominant Parkinson disease 4; MONDO:8200 autosomal dominant Parkinson disease 1; MONDO:14796 autosomal recessive early-onset Parkinson disease 23; MONDO:17279 young-onset Parkinson disease; MONDO:5180 Parkinson disease; MONDO:6966 secondary Parkinson disease; MONDO:828 juvenile-onset Parkinson disease; MONDO:10796 Parkinson disease, mitochondrial; MONDO:10360 parkinson disease 12; MONDO:11737 parkinson disease 10; MONDO:13167 parkinson disease 16.

Grouped node `27576` (autosomal recessive Parkinson disease) merges 2 MONDO diseases: MONDO:10820 autosomal recessive juvenile Parkinson disease 2; MONDO:13060 autosomal recessive Parkinson disease 14.

Grouped node `28182` (Parkinson disease, autosomal dominant, susceptibility to) merges 4 MONDO diseases: MONDO:13340 Parkinson disease 5, autosomal dominant, susceptibility to; MONDO:12466 Parkinson disease 13, autosomal dominant, susceptibility to; MONDO:13653 Parkinson disease 18, autosomal dominant, susceptibility to; MONDO:11896 Parkinson disease 11, autosomal dominant, susceptibility to.

Grouped node `32001` (parkinson disease, autosomal dominant) merges 2 MONDO diseases: MONDO:14742 Parkinson disease 22, autosomal dominant; MONDO:11220 parkinson disease 3, autosomal dominant.

#### frontotemporal dementia (FTD), PrimeKG

| ID | Name | Degree | Role | File order | MONDO (via OptimusKG) | Edges by relation | In seed | Excluded because |
|---|---|---|---|---|---|---|---|---|
| `35691` | frontotemporal dementia | 15 | main |  | same | disease_protein 8, disease_disease 7 | yes |  |
| `28751` | inclusion body myopathy with Paget disease of bone and frontotemporal dementia | 69 | disease_disease neighbor | child | descendant | disease_phenotype_positive 60, disease_disease 6, disease_protein 3 | yes |  |
| `32688` | behavioral variant of frontotemporal dementia | 47 | disease_disease neighbor | child | descendant | disease_phenotype_positive 33, disease_protein 9, disease_disease 5 | yes |  |
| `28473` | Pick disease | 155 | disease_disease neighbor | child | not in OptimusKG | disease_protein 101, contraindication 27, disease_phenotype_positive 17 (+2 more) | no | disease_disease neighbor whose name lacks the main node's name |
| `28708` | frontotemporal dementia and/or amyotrophic lateral sclerosis | 71 | name match |  | unrelated | disease_phenotype_positive 59, disease_protein 8, disease_disease 4 | no | not a disease_disease neighbor of the main node |
| `35480` | inherited neurodegenerative disorder | 65 | disease_disease neighbor | parent | ancestor | disease_disease 65 | no | disease_disease neighbor whose name lacks the main node's name |
| `32696` | frontotemporal dementia with motor neuron disease | 47 | name match |  | unrelated | disease_phenotype_positive 32, disease_protein 8, disease_disease 7 | no | not a disease_disease neighbor of the main node |
| `28467` | Grn-related frontotemporal lobar degeneration with Tdp43 inclusions | 28 | disease_disease neighbor | child | descendant | disease_phenotype_positive 24, disease_disease 3, disease_protein 1 | no | disease_disease neighbor whose name lacks the main node's name |
| `30418` | inclusion body myopathy with early-onset Paget disease with or without frontotemporal dementia | 22 | name match |  | not in OptimusKG | disease_phenotype_positive 19, disease_protein 2, disease_disease 1 | no | not a disease_disease neighbor of the main node |
| `38241` | genetic dementia | 16 | disease_disease neighbor | parent | ancestor | disease_disease 16 | no | disease_disease neighbor whose name lacks the main node's name |
| `31852` | autosomal recessive frontotemporal pachygyria | 13 | name match |  | unrelated | disease_phenotype_positive 12, disease_disease 1 | no | not a disease_disease neighbor of the main node |
| `39112` | frontotemporal neurodegeneration with movement disorder | 6 | name match |  | not in OptimusKG | disease_disease 6 | no | not a disease_disease neighbor of the main node |
| `39362` | frontotemporal degeneration with dementia | 5 | disease_disease neighbor | parent | not in OptimusKG | disease_disease 5 | no | disease_disease neighbor whose name lacks the main node's name |
| `98286` | frontotemporal dementia, right temporal atrophy variant | 1 | name match |  | descendant | disease_disease 1 | no | not a disease_disease neighbor of the main node |

Non-disease nodes matching the search terms (not in the seed: not a disease node): `25957` Frontotemporal dementia (effect/phenotype); `86833` Frontotemporal cerebral atrophy (effect/phenotype); `94366` Frontotemporal hypertrichosis (effect/phenotype).

Proposed seed (3 nodes): `28751`, `32688`, `35691`. Rule: 35691 plus its disease_disease neighbors whose name contains 'frontotemporal dementia'.

Grouped node `28751` (inclusion body myopathy with Paget disease of bone and frontotemporal dementia) merges 2 MONDO diseases: MONDO:8178 inclusion body myopathy with Paget disease of bone and frontotemporal dementia type 1; MONDO:507 inclusion body myopathy with Paget disease of bone and frontotemporal dementia.

#### amyotrophic lateral sclerosis (ALS), PrimeKG

| ID | Name | Degree | Role | File order | MONDO (via OptimusKG) | Edges by relation | In seed | Excluded because |
|---|---|---|---|---|---|---|---|---|
| `27248` | amyotrophic lateral sclerosis | 289 | main |  | same | disease_protein 108, disease_phenotype_positive 106, exposure_disease 64 (+3 more) | yes |  |
| `31996` | juvenile amyotrophic lateral sclerosis | 53 | disease_disease neighbor | parent | descendant | disease_phenotype_positive 43, disease_protein 8, disease_disease 2 | yes |  |
| `38139` | familial amyotrophic lateral sclerosis | 7 | disease_disease neighbor | child | not in OptimusKG | disease_disease 7 | yes |  |
| `29634` | amyotrophic lateral sclerosis with polyglucosan bodies | 6 | disease_disease neighbor | child | not in OptimusKG | disease_phenotype_positive 5, disease_disease 1 | yes |  |
| `28708` | frontotemporal dementia and/or amyotrophic lateral sclerosis | 71 | name match |  | descendant | disease_phenotype_positive 59, disease_protein 8, disease_disease 4 | no | not a disease_disease neighbor of the main node |
| `32696` | frontotemporal dementia with motor neuron disease | 47 | disease_disease neighbor | parent | unrelated | disease_phenotype_positive 32, disease_protein 8, disease_disease 7 | no | disease_disease neighbor whose name lacks the main node's name |
| `31354` | amyotrophic lateral sclerosis-parkinsonism-dementia complex | 44 | name match |  | unrelated | disease_protein 34, disease_phenotype_positive 9, disease_disease 1 | no | not a disease_disease neighbor of the main node |
| `35442` | motor neuron disease | 18 | disease_disease neighbor | parent | unrelated | disease_disease 12, disease_protein 5, exposure_disease 1 | no | disease_disease neighbor whose name lacks the main node's name |
| `36248` | anterior horn disease | 16 | disease_disease neighbor | parent | ancestor | contraindication 8, disease_disease 4, disease_protein 4 | no | disease_disease neighbor whose name lacks the main node's name |
| `29752` | amyotrophic lateral sclerosis, susceptibility to | 10 | name match |  | not in OptimusKG | disease_phenotype_positive 9, disease_disease 1 | no | not a disease_disease neighbor of the main node |
| `39700` | progressive muscular atrophy | 9 | disease_disease neighbor | child | not in OptimusKG | disease_protein 7, disease_disease 2 | no | disease_disease neighbor whose name lacks the main node's name |

Non-disease nodes matching the search terms (not in the seed: not a disease node): `86998` Amyotrophic lateral sclerosis (effect/phenotype).

Proposed seed (4 nodes): `27248`, `29634`, `31996`, `38139`. Rule: 27248 plus its disease_disease neighbors whose name contains 'amyotrophic lateral sclerosis'.

Grouped node `27248` (amyotrophic lateral sclerosis) merges 21 MONDO diseases: MONDO:12945 amyotrophic lateral sclerosis type 11; MONDO:11691 amyotrophic lateral sclerosis type 3; MONDO:12077 amyotrophic lateral sclerosis type 8; MONDO:8780 amyotrophic lateral sclerosis type 2; MONDO:11196 amyotrophic lateral sclerosis type 5; MONDO:27694 amyotrophic lateral sclerosis type 23; MONDO:11951 amyotrophic lateral sclerosis type 6; MONDO:10459 amyotrophic lateral sclerosis type 15; MONDO:11223 amyotrophic lateral sclerosis type 4; MONDO:11632 amyotrophic lateral sclerosis type 21; MONDO:14223 amyotrophic lateral sclerosis type 19; MONDO:12790 amyotrophic lateral sclerosis type 10; MONDO:12753 amyotrophic lateral sclerosis type 9; MONDO:13715 amyotrophic lateral sclerosis type 16; MONDO:7103 amyotrophic lateral sclerosis type 1; MONDO:13264 amyotrophic lateral sclerosis type 12; MONDO:13891 amyotrophic lateral sclerosis type 18; MONDO:14181 amyotrophic lateral sclerosis type 20; MONDO:4976 amyotrophic lateral sclerosis; MONDO:5145 sporadic amyotrophic lateral sclerosis; MONDO:11952 amyotrophic lateral sclerosis type 7.

Grouped node `31996` (juvenile amyotrophic lateral sclerosis) merges 2 MONDO diseases: MONDO:8781 juvenile amyotrophic lateral sclerosis with dementia; MONDO:17593 juvenile amyotrophic lateral sclerosis.

#### Huntington disease (HD), PrimeKG

| ID | Name | Degree | Role | File order | MONDO (via OptimusKG) | Edges by relation | In seed | Excluded because |
|---|---|---|---|---|---|---|---|---|
| `31245` | Huntington disease | 78 | main |  | same | disease_phenotype_positive 55, disease_protein 17, disease_disease 4 (+1 more) | yes |  |
| `33341` | juvenile Huntington disease | 41 | disease_disease neighbor | child | descendant | disease_phenotype_positive 21, disease_protein 17, indication 2 (+1 more) | yes |  |
| `29652` | Huntington disease-like | 113 | name match |  | unrelated | disease_phenotype_positive 79, disease_protein 19, disease_disease 13 (+1 more) | no | not a disease_disease neighbor of the main node |
| `36976` | inherited tremor disorder | 54 | disease_disease neighbor | parent | not in OptimusKG | disease_disease 54 | no | disease_disease neighbor whose name lacks the main node's name |
| `32740` | Huntington disease-like syndrome due to C9ORF72 expansions | 33 | name match |  | unrelated | disease_protein 17, disease_phenotype_positive 11, disease_disease 3 (+1 more) | no | not a disease_disease neighbor of the main node |
| `36291` | neurodegenerative disease with chorea | 9 | disease_disease neighbor | parent | not in OptimusKG | disease_disease 9 | no | disease_disease neighbor whose name lacks the main node's name |
| `38307` | Huntington disease and related disorders | 3 | disease_disease neighbor | parent | ancestor | disease_disease 3 | no | excluded by hand: 'Huntington disease and related disorders' contains the main node's name, but MONDO places it above Huntington disease (OptimusKG DIS-DIS/PARENT MONDO_0000167 -> MONDO_0007739), and kg.csv's file order also gives it as the parent. Degree 3. |

Proposed seed (2 nodes): `31245`, `33341`. Rule: 31245 plus its disease_disease neighbors whose name contains 'Huntington disease', minus 38307 (see exclusions).

Excluded by hand: `38307`. 'Huntington disease and related disorders' contains the main node's name, but MONDO places it above Huntington disease (OptimusKG DIS-DIS/PARENT MONDO_0000167 -> MONDO_0007739), and kg.csv's file order also gives it as the parent. Degree 3.

#### dementia with Lewy bodies (DLB), PrimeKG

| ID | Name | Degree | Role | File order | MONDO (via OptimusKG) | Edges by relation | In seed | Excluded because |
|---|---|---|---|---|---|---|---|---|
| `29296` | Lewy body dementia | 36 | main |  | not in OptimusKG | disease_protein 22, disease_phenotype_positive 7, off-label use 3 (+2 more) | yes |  |
| `37573` | dementia (disease) | 69 | disease_disease neighbor | parent | ancestor | contraindication 28, disease_protein 17, exposure_disease 12 (+2 more) | no | disease_disease neighbor whose name lacks the main node's name |
| `37707` | synucleinopathy | 4 | disease_disease neighbor | parent | not in OptimusKG | disease_disease 4 | no | disease_disease neighbor whose name lacks the main node's name |

Non-disease nodes matching the search terms (not in the seed: not a disease node): `93302` Lewy bodies (effect/phenotype); `56343` Lewy body (cellular_component); `124457` Lewy body core (cellular_component); `47630` regulation of Lewy body formation (biological_process); `106989` positive regulation of Lewy body formation (biological_process); `106990` negative regulation of Lewy body formation (biological_process); `124375` Lewy body corona (cellular_component); `126115` Lewy neurite (cellular_component); `127258` classical Lewy body (cellular_component); `108568` Lewy body formation (biological_process); `124608` Lewy body-like hyaline inclusion (cellular_component); `127257` cortical Lewy body (cellular_component).

Proposed seed (1 node): `29296`. Rule: 29296 plus its disease_disease neighbors whose name contains 'Lewy body dementia'.

### Broader seed: candidates, OptimusKG

#### Alzheimer's disease (AD), OptimusKG

| ID | Name | Degree | Role | Edges by relation | In seed | Excluded because |
|---|---|---|---|---|---|---|
| `MONDO_0004975` | Alzheimer disease | 12,760 | main | DIS-GEN/ASSOCIATED_WITH 12,414, DRG-DIS/INDICATION 314, DIS-PHE/PHENOTYPE_PRESENT 16 (+3 more) | yes |  |
| `MONDO_0100087` | familial Alzheimer disease | 409 | descendant (depth 1) | DIS-GEN/ASSOCIATED_WITH 405, DIS-DIS/PARENT 4 | yes |  |
| `EFO_1001870` | late-onset Alzheimers disease | 259 | descendant (depth 1) | DIS-GEN/ASSOCIATED_WITH 258, DIS-DIS/PARENT 1 | yes |  |
| `MONDO_0015140` | early-onset autosomal dominant Alzheimer disease | 171 | descendant (depth 2) | DIS-GEN/ASSOCIATED_WITH 167, DIS-DIS/PARENT 4 | yes |  |
| `MONDO_0011913` | Alzheimer disease 3 | 98 | descendant (depth 3) | DIS-GEN/ASSOCIATED_WITH 75, DIS-PHE/PHENOTYPE_PRESENT 22, DIS-DIS/PARENT 1 | yes |  |
| `MONDO_0007088` | Alzheimer disease type 1 | 40 | descendant (depth 3) | DIS-GEN/ASSOCIATED_WITH 30, DIS-PHE/PHENOTYPE_PRESENT 9, DIS-DIS/PARENT 1 | yes |  |
| `MONDO_0014265` | Alzheimer disease 18 | 22 | descendant (depth 1) | DIS-GEN/ASSOCIATED_WITH 19, DIS-DIS/PARENT 3 | yes |  |
| `MONDO_0001627` | dementia | 13,789 | parent | DIS-GEN/ASSOCIATED_WITH 13,741, DRG-DIS/CONTRAINDICATION 28, EXP-DIS/LINKED_TO 12 (+2 more) | no | parent of the main node |
| `EFO_0005815` | tauopathy | 11,627 | parent | DIS-GEN/ASSOCIATED_WITH 11,621, DIS-DIS/PARENT 5, DRG-DIS/INDICATION 1 | no | parent of the main node |
| `MONDO_0018899` | posterior cortical atrophy | 6,837 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 6,835, DIS-DIS/PARENT 2 | no | not a descendant of the main node |
| `EFO_0006792` | Lewy body dementia | 1,281 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 1,258, DRG-DIS/INDICATION 10, DIS-PHE/PHENOTYPE_PRESENT 7 (+3 more) | no | not a descendant of the main node |
| `EFO_0006514` | Alzheimer's disease biomarker measurement | 383 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 361, DIS-DIS/PARENT 22 | no | not a descendant of the main node |
| `EFO_0009268` | family history of Alzheimer’s disease | 97 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 96, DIS-DIS/PARENT 1 | no | not a descendant of the main node |
| `OBA_2001000` | age of onset of Alzheimer disease | 57 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 56, DIS-DIS/PARENT 1 | no | not a descendant of the main node |
| `EFO_0006801` | Alzheimer's disease neuropathologic change | 6 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 4, DIS-DIS/PARENT 2 | no | not a descendant of the main node |

Non-disease nodes matching the search terms (not in the seed: not a disease node): `REACT:R-HSA-8862803` Deregulated CDK5 triggers multiple neurodegenerative pathways in Alzheimer's disease models (PWY); `HP_0002511` Alzheimer disease (PHE).

Proposed seed (7 nodes): `EFO_1001870`, `MONDO_0004975`, `MONDO_0007088`, `MONDO_0011913`, `MONDO_0014265`, `MONDO_0015140`, `MONDO_0100087`. Rule: MONDO_0004975 plus all its descendants over DIS-DIS/PARENT (parent -> child).

#### Parkinson disease (PD), OptimusKG

| ID | Name | Degree | Role | Edges by relation | In seed | Excluded because |
|---|---|---|---|---|---|---|
| `MONDO_0005180` | Parkinson disease | 6,920 | main | DIS-GEN/ASSOCIATED_WITH 6,584, DRG-DIS/INDICATION 246, EXP-DIS/LINKED_TO 41 (+3 more) | yes |  |
| `Orphanet_2828` | Young adult-onset Parkinsonism | 435 | descendant (depth 1) | DIS-GEN/ASSOCIATED_WITH 352, DIS-PHE/PHENOTYPE_PRESENT 81, DIS-DIS/PARENT 2 | yes |  |
| `Orphanet_411602` | Hereditary late-onset Parkinson disease | 283 | descendant (depth 1) | DIS-GEN/ASSOCIATED_WITH 238, DIS-PHE/PHENOTYPE_PRESENT 43, DIS-DIS/PARENT 2 | yes |  |
| `MONDO_0011706` | Kufor-Rakeb syndrome | 242 | descendant (depth 3) | DIS-GEN/ASSOCIATED_WITH 197, DIS-PHE/PHENOTYPE_PRESENT 42, DIS-DIS/PARENT 3 | yes |  |
| `MONDO_0017279` | young-onset Parkinson disease | 228 | descendant (depth 1) | DIS-GEN/ASSOCIATED_WITH 226, DIS-DIS/PARENT 2 | yes |  |
| `MONDO_0000828` | juvenile-onset Parkinson disease | 210 | descendant (depth 2) | DIS-GEN/ASSOCIATED_WITH 207, DIS-DIS/PARENT 3 | yes |  |
| `MONDO_0957576` | parkinson disease 25, autosomal recessive early-onset, with impaired intellectual development | 164 | descendant (depth 1) | DIS-GEN/ASSOCIATED_WITH 151, DIS-PHE/PHENOTYPE_PRESENT 12, DIS-DIS/PARENT 1 | yes |  |
| `MONDO_0017809` | parkinsonism due to ATP13A2 deficiency | 137 | descendant (depth 4) | DIS-GEN/ASSOCIATED_WITH 135, DIS-DIS/PARENT 2 | yes |  |
| `MONDO_0008199` | late-onset Parkinson disease | 98 | descendant (depth 1) | DIS-GEN/ASSOCIATED_WITH 66, DIS-PHE/PHENOTYPE_PRESENT 30, DIS-DIS/PARENT 2 | yes |  |
| `MONDO_0009830` | parkinsonian-pyramidal syndrome | 44 | descendant (depth 1) | DIS-PHE/PHENOTYPE_PRESENT 23, DIS-GEN/ASSOCIATED_WITH 20, DIS-DIS/PARENT 1 | yes |  |
| `MONDO_0013060` | autosomal recessive Parkinson disease 14 | 43 | descendant (depth 2) | DIS-PHE/PHENOTYPE_PRESENT 40, DIS-DIS/PARENT 2, DIS-GEN/ASSOCIATED_WITH 1 | yes |  |
| `EFO_1001175` | secondary Parkinson disease | 39 | descendant (depth 1) | DIS-GEN/ASSOCIATED_WITH 32, DRG-DIS/INDICATION 4, DIS-DIS/PARENT 3 | yes |  |
| `EFO_1001402` | postencephalitic Parkinson disease | 30 | descendant (depth 2) | DIS-GEN/ASSOCIATED_WITH 18, DRG-DIS/INDICATION 10, DIS-DIS/PARENT 2 | yes |  |
| `MONDO_0010796` | Parkinson disease, mitochondrial | 11 | descendant (depth 1) | DIS-GEN/ASSOCIATED_WITH 10, DIS-DIS/PARENT 1 | yes |  |
| `EFO_0022609` | Parkinson disease, dominant | 7 | descendant (depth 1) | DIS-GEN/ASSOCIATED_WITH 6, DIS-DIS/PARENT 1 | yes |  |
| `MONDO_0800369` | parkinson disease 19B, early-onset | 2 | descendant (depth 3) | DIS-DIS/PARENT 1, DIS-GEN/ASSOCIATED_WITH 1 | yes |  |
| `MONDO_0100545` | hereditary neurological disease | 16,111 | parent | DIS-GEN/ASSOCIATED_WITH 15,867, DIS-DIS/PARENT 244 | no | parent of the main node |
| `MONDO_0021095` | parkinsonian disorder | 6,782 | parent | DIS-GEN/ASSOCIATED_WITH 6,708, DRG-DIS/CONTRAINDICATION 45, DIS-DIS/PARENT 16 (+2 more) | no | parent of the main node |
| `Orphanet_307052` | Rare genetic parkinsonian disorder | 1,042 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 1,039, DIS-DIS/PARENT 3 | no | not a descendant of the main node |
| `Orphanet_307055` | Rare parkinsonian syndrome due to genetic neurodegenerative disease | 1,032 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 1,023, DIS-DIS/PARENT 9 | no | not a descendant of the main node |
| `MONDO_0013150` | parkinsonism-dystonia, infantile | 758 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 753, DIS-DIS/PARENT 5 | no | not a descendant of the main node |
| `Orphanet_238455` | Infantile dystonia-parkinsonism | 710 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 658, DIS-PHE/PHENOTYPE_PRESENT 50, DIS-DIS/PARENT 2 | no | not a descendant of the main node |
| `EFO_1001450` | Wolff-Parkinson-White Syndrome | 514 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 481, DIS-PHE/PHENOTYPE_PRESENT 14, DRG-DIS/CONTRAINDICATION 14 (+2 more) | no | not a descendant of the main node |
| `MONDO_0007495` | dystonia 5 | 369 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 342, DIS-PHE/PHENOTYPE_PRESENT 25, DIS-DIS/PARENT 2 | no | not a descendant of the main node |
| `MONDO_0010747` | X-linked dystonia-parkinsonism | 294 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 284, DIS-PHE/PHENOTYPE_PRESENT 7, DIS-DIS/PARENT 3 | no | not a descendant of the main node |
| `Orphanet_199351` | Adult-onset dystonia-parkinsonism | 283 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 240, DIS-PHE/PHENOTYPE_PRESENT 40, DIS-DIS/PARENT 3 | no | not a descendant of the main node |
| `MONDO_0010482` | X-linked parkinsonism-spasticity syndrome | 248 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 233, DIS-PHE/PHENOTYPE_PRESENT 13, DIS-DIS/PARENT 2 | no | not a descendant of the main node |
| `MONDO_0012789` | dystonia 16 | 235 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 206, DIS-PHE/PHENOTYPE_PRESENT 26, DIS-DIS/PARENT 3 | no | not a descendant of the main node |
| `MONDO_0018321` | atypical juvenile parkinsonism | 184 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 183, DIS-DIS/PARENT 1 | no | not a descendant of the main node |
| `MONDO_0030676` | parkinsonism-dystonia 3, childhood-onset | 180 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 151, DIS-PHE/PHENOTYPE_PRESENT 28, DIS-DIS/PARENT 1 | no | not a descendant of the main node |
| `Orphanet_71517` | Rapid-onset dystonia-parkinsonism | 171 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 148, DIS-PHE/PHENOTYPE_PRESENT 21, DIS-DIS/PARENT 2 | no | not a descendant of the main node |
| `MONDO_0007496` | dystonia 12 | 167 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 143, DIS-PHE/PHENOTYPE_PRESENT 21, DIS-DIS/PARENT 3 | no | not a descendant of the main node |
| `MONDO_0010857` | semantic dementia | 161 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 137, DIS-PHE/PHENOTYPE_PRESENT 20, DIS-DIS/PARENT 3 (+1 more) | no | not a descendant of the main node |
| `MONDO_0010709` | early-onset parkinsonism-intellectual disability syndrome | 155 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 131, DIS-PHE/PHENOTYPE_PRESENT 22, DIS-DIS/PARENT 2 | no | not a descendant of the main node |
| `MONDO_0958323` | neurodevelopmental disorder with early-onset parkinsonism and behavioral abnormalities | 142 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 109, DIS-PHE/PHENOTYPE_PRESENT 32, DIS-DIS/PARENT 1 | no | not a descendant of the main node |
| `MONDO_0008201` | Perry syndrome | 140 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 108, DIS-PHE/PHENOTYPE_PRESENT 30, DIS-DIS/PARENT 2 | no | not a descendant of the main node |
| `MONDO_0054835` | classic dopamine transporter deficiency syndrome | 128 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 98, DIS-PHE/PHENOTYPE_PRESENT 28, DIS-DIS/PARENT 2 | no | not a descendant of the main node |
| `MONDO_0859257` | intellectual developmental disorder with language impairment and early-onset DOPA-responsive dystonia-parkinsonism | 125 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 102, DIS-PHE/PHENOTYPE_PRESENT 22, DIS-DIS/PARENT 1 | no | not a descendant of the main node |
| `Orphanet_240085` | Progressive supranuclear palsy - parkinsonism | 91 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 79, DIS-PHE/PHENOTYPE_PRESENT 11, DIS-DIS/PARENT 1 | no | not a descendant of the main node |
| `Orphanet_2379` | Early-onset parkinsonism - intellectual disability | 82 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 58, DIS-PHE/PHENOTYPE_PRESENT 22, DIS-DIS/PARENT 2 | no | not a descendant of the main node |
| `MONDO_0018130` | brain dopamine-serotonin vesicular transport disease | 81 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 48, DIS-PHE/PHENOTYPE_PRESENT 31, DIS-DIS/PARENT 2 | no | not a descendant of the main node |
| `MONDO_0036193` | parkinsonism with polyneuropathy | 81 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 65, DIS-PHE/PHENOTYPE_PRESENT 14, DIS-DIS/PARENT 2 | no | not a descendant of the main node |
| `MONDO_0007104` | amyotrophic lateral sclerosis-parkinsonism-dementia complex | 70 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 57, DIS-PHE/PHENOTYPE_PRESENT 12, DIS-DIS/PARENT 1 | no | not a descendant of the main node |
| `MONDO_0017636` | hemiparkinsonism-hemiatrophy syndrome | 38 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 37, DIS-DIS/PARENT 1 | no | not a descendant of the main node |
| `MONDO_0011551` | TH-deficient dopa-responsive dystonia | 26 | name or synonym match | DIS-PHE/PHENOTYPE_PRESENT 18, DIS-DIS/PARENT 5, DIS-GEN/ASSOCIATED_WITH 3 | no | not a descendant of the main node |
| `MONDO_0009839` | progressive supranuclear palsy-parkinsonism syndrome | 13 | name or synonym match | DIS-PHE/PHENOTYPE_PRESENT 11, DIS-DIS/PARENT 1, DIS-GEN/ASSOCIATED_WITH 1 | no | not a descendant of the main node |
| `OBA_2001009` | age of onset of Parkinson disease | 12 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 11, DIS-DIS/PARENT 1 | no | not a descendant of the main node |
| `MONDO_0017639` | carbon monoxide-induced parkinsonism | 9 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 7, DIS-DIS/PARENT 2 | no | not a descendant of the main node |
| `EFO_0600011` | Parkinson's disease symptom measurement | 6 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 5, DIS-DIS/PARENT 1 | no | not a descendant of the main node |
| `MONDO_0006966` | secondary Parkinson disease | 1 | name or synonym match | EXP-DIS/LINKED_TO 1 | no | not a descendant of the main node |
| `OBA_2052551` | amount of parkinson disease protein 7 (human) in blood | 1 | name or synonym match | DIS-DIS/PARENT 1 | no | not a descendant of the main node |

Non-disease nodes matching the search terms (not in the seed: not a disease node): `HP_0001300` Parkinsonism (PHE); `ENSG00000116288` Parkinsonism associated deglycase (GEN); `HP_0001716` Wolff-Parkinson-White syndrome (PHE); `HP_0002548` Parkinsonism with favorable response to dopaminergic medication (PHE); `REACT:R-HSA-5619081` Defective SLC6A3 causes Parkinsonism-dystonia infantile (PKDYS) (PWY); `REACT:R-HSA-5660724` Defective SLC6A3 causes Parkinsonism-dystonia infantile (PKDYS) (PWY); `MESH:D000978` Antiparkinson Agents (EXP); `meddra:10034010` Parkinsonism (PHE).

Proposed seed (16 nodes): `EFO_0022609`, `EFO_1001175`, `EFO_1001402`, `MONDO_0000828`, `MONDO_0005180`, `MONDO_0008199`, `MONDO_0009830`, `MONDO_0010796`, `MONDO_0011706`, `MONDO_0013060`, `MONDO_0017279`, `MONDO_0017809`, `MONDO_0800369`, `MONDO_0957576`, `Orphanet_2828`, `Orphanet_411602`. Rule: MONDO_0005180 plus all its descendants over DIS-DIS/PARENT (parent -> child).

#### frontotemporal dementia (FTD), OptimusKG

| ID | Name | Degree | Role | Edges by relation | In seed | Excluded because |
|---|---|---|---|---|---|---|
| `MONDO_0017276` | frontotemporal dementia | 1,689 | main | DIS-GEN/ASSOCIATED_WITH 1,668, DRG-DIS/INDICATION 15, DIS-DIS/PARENT 6 | yes |  |
| `EFO_0003096` | Pick disease | 2,165 | descendant (depth 1) | DIS-GEN/ASSOCIATED_WITH 2,142, DIS-PHE/PHENOTYPE_PRESENT 19, DIS-DIS/PARENT 3 (+1 more) | yes |  |
| `MONDO_0017160` | behavioral variant of frontotemporal dementia | 415 | descendant (depth 1) | DIS-GEN/ASSOCIATED_WITH 412, DIS-DIS/PARENT 3 | yes |  |
| `MONDO_0000507` | inclusion body myopathy with Paget disease of bone and frontotemporal dementia | 239 | descendant (depth 1) | DIS-GEN/ASSOCIATED_WITH 235, DIS-DIS/PARENT 4 | yes |  |
| `MONDO_0011842` | GRN-related frontotemporal lobar degeneration with Tdp43 inclusions | 226 | descendant (depth 1) | DIS-GEN/ASSOCIATED_WITH 199, DIS-PHE/PHENOTYPE_PRESENT 24, DIS-DIS/PARENT 3 | yes |  |
| `MONDO_0850514` | inclusion body myopathy and brain white matter abnormalities | 186 | descendant (depth 2) | DIS-GEN/ASSOCIATED_WITH 158, DIS-PHE/PHENOTYPE_PRESENT 26, DIS-DIS/PARENT 2 | yes |  |
| `MONDO_0015059` | progressive non-fluent aphasia | 173 | descendant (depth 2) | DIS-GEN/ASSOCIATED_WITH 170, DIS-DIS/PARENT 3 | yes |  |
| `MONDO_0010857` | semantic dementia | 161 | descendant (depth 2) | DIS-GEN/ASSOCIATED_WITH 137, DIS-PHE/PHENOTYPE_PRESENT 20, DIS-DIS/PARENT 3 (+1 more) | yes |  |
| `MONDO_0008178` | inclusion body myopathy with Paget disease of bone and frontotemporal dementia type 1 | 114 | descendant (depth 2) | DIS-GEN/ASSOCIATED_WITH 78, DIS-PHE/PHENOTYPE_PRESENT 35, DIS-DIS/PARENT 1 | yes |  |
| `MONDO_0017399` | frontotemporal dementia, right temporal atrophy variant | 1 | descendant (depth 3) | DIS-DIS/PARENT 1 | yes |  |
| `MONDO_0024237` | inherited neurodegenerative disorder | 11,702 | parent | DIS-GEN/ASSOCIATED_WITH 11,636, DIS-DIS/PARENT 66 | no | parent of the main node |
| `MONDO_0015547` | hereditary dementia | 9,129 | parent | DIS-GEN/ASSOCIATED_WITH 9,111, DIS-DIS/PARENT 18 | no | parent of the main node |
| `MONDO_0017161` | frontotemporal dementia with motor neuron disease | 149 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 143, DIS-DIS/PARENT 6 | no | not a descendant of the main node |
| `MONDO_0030923` | frontotemporal dementia and/or amyotrophic lateral sclerosis | 145 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 141, DIS-DIS/PARENT 4 | no | not a descendant of the main node |
| `Orphanet_276061` | Genetic frontotemporal degeneration with dementia | 109 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 105, DIS-DIS/PARENT 4 | no | not a descendant of the main node |
| `Orphanet_306708` | Frontotemporal neurodegeneration with movement disorder | 108 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 105, DIS-DIS/PARENT 3 | no | not a descendant of the main node |
| `MONDO_0010459` | amyotrophic lateral sclerosis type 15 | 105 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 88, DIS-PHE/PHENOTYPE_PRESENT 16, DIS-DIS/PARENT 1 | no | not a descendant of the main node |
| `MONDO_0013501` | frontotemporal dementia and/or amyotrophic lateral sclerosis 6 | 102 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 72, DIS-PHE/PHENOTYPE_PRESENT 27, DIS-DIS/PARENT 3 | no | not a descendant of the main node |
| `MONDO_0007105` | frontotemporal dementia and/or amyotrophic lateral sclerosis 1 | 79 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 52, DIS-PHE/PHENOTYPE_PRESENT 24, DIS-DIS/PARENT 3 | no | not a descendant of the main node |
| `MONDO_0014641` | frontotemporal dementia and/or amyotrophic lateral sclerosis 4 | 54 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 31, DIS-PHE/PHENOTYPE_PRESENT 20, DIS-DIS/PARENT 3 | no | not a descendant of the main node |
| `MONDO_0014404` | Webb-Dattani syndrome | 34 | name or synonym match | DIS-PHE/PHENOTYPE_PRESENT 28, DIS-GEN/ASSOCIATED_WITH 5, DIS-DIS/PARENT 1 | no | not a descendant of the main node |
| `MONDO_0030885` | amyotrophic lateral sclerosis 26 with or without frontotemporal dementia | 33 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 20, DIS-PHE/PHENOTYPE_PRESENT 12, DIS-DIS/PARENT 1 | no | not a descendant of the main node |
| `MONDO_0012462` | autosomal recessive frontotemporal pachygyria | 10 | name or synonym match | DIS-PHE/PHENOTYPE_PRESENT 7, DIS-GEN/ASSOCIATED_WITH 2, DIS-DIS/PARENT 1 | no | not a descendant of the main node |
| `OBA_2001022` | age of onset of frontotemporal dementia | 1 | name or synonym match | DIS-DIS/PARENT 1 | no | not a descendant of the main node |

Non-disease nodes matching the search terms (not in the seed: not a disease node): `HP_0002145` Frontotemporal dementia (PHE); `HP_0006892` Frontotemporal cerebral atrophy (PHE); `HP_0032313` Frontotemporal hypertrichosis (PHE).

Proposed seed (10 nodes): `EFO_0003096`, `MONDO_0000507`, `MONDO_0008178`, `MONDO_0010857`, `MONDO_0011842`, `MONDO_0015059`, `MONDO_0017160`, `MONDO_0017276`, `MONDO_0017399`, `MONDO_0850514`. Rule: MONDO_0017276 plus all its descendants over DIS-DIS/PARENT (parent -> child).

#### amyotrophic lateral sclerosis (ALS), OptimusKG

| ID | Name | Degree | Role | Edges by relation | In seed | Excluded because |
|---|---|---|---|---|---|---|
| `MONDO_0004976` | amyotrophic lateral sclerosis | 1,738 | main | DIS-GEN/ASSOCIATED_WITH 1,484, DRG-DIS/INDICATION 153, EXP-DIS/LINKED_TO 65 (+2 more) | yes |  |
| `EFO_0001356` | familial amyotrophic lateral sclerosis | 861 | descendant (depth 1) | DIS-GEN/ASSOCIATED_WITH 740, DIS-PHE/PHENOTYPE_PRESENT 105, DIS-DIS/PARENT 14 (+1 more) | yes |  |
| `MONDO_0008458` | spinocerebellar ataxia type 2 | 350 | descendant (depth 2) | DIS-GEN/ASSOCIATED_WITH 304, DIS-PHE/PHENOTYPE_PRESENT 41, DIS-DIS/PARENT 3 (+1 more) | yes |  |
| `EFO_0001357` | sporadic amyotrophic lateral sclerosis | 323 | descendant (depth 1) | DIS-GEN/ASSOCIATED_WITH 302, DIS-PHE/PHENOTYPE_PRESENT 20, DIS-DIS/PARENT 1 | yes |  |
| `MONDO_0017593` | juvenile amyotrophic lateral sclerosis | 197 | descendant (depth 2) | DIS-GEN/ASSOCIATED_WITH 194, DIS-DIS/PARENT 3 | yes |  |
| `MONDO_0011223` | amyotrophic lateral sclerosis type 4 | 181 | descendant (depth 2) | DIS-GEN/ASSOCIATED_WITH 159, DIS-PHE/PHENOTYPE_PRESENT 21, DIS-DIS/PARENT 1 | yes |  |
| `MONDO_0008780` | amyotrophic lateral sclerosis type 2, juvenile | 171 | descendant (depth 3) | DIS-GEN/ASSOCIATED_WITH 117, DIS-PHE/PHENOTYPE_PRESENT 52, DIS-DIS/PARENT 2 | yes |  |
| `MONDO_0012945` | amyotrophic lateral sclerosis type 11 | 110 | descendant (depth 2) | DIS-GEN/ASSOCIATED_WITH 101, DIS-PHE/PHENOTYPE_PRESENT 8, DIS-DIS/PARENT 1 | yes |  |
| `MONDO_0010459` | amyotrophic lateral sclerosis type 15 | 105 | descendant (depth 2) | DIS-GEN/ASSOCIATED_WITH 88, DIS-PHE/PHENOTYPE_PRESENT 16, DIS-DIS/PARENT 1 | yes |  |
| `MONDO_0013501` | frontotemporal dementia and/or amyotrophic lateral sclerosis 6 | 102 | descendant (depth 2) | DIS-GEN/ASSOCIATED_WITH 72, DIS-PHE/PHENOTYPE_PRESENT 27, DIS-DIS/PARENT 3 | yes |  |
| `MONDO_0012077` | amyotrophic lateral sclerosis type 8 | 85 | descendant (depth 2) | DIS-GEN/ASSOCIATED_WITH 62, DIS-PHE/PHENOTYPE_PRESENT 22, DIS-DIS/PARENT 1 | yes |  |
| `MONDO_0007105` | frontotemporal dementia and/or amyotrophic lateral sclerosis 1 | 79 | descendant (depth 2) | DIS-GEN/ASSOCIATED_WITH 52, DIS-PHE/PHENOTYPE_PRESENT 24, DIS-DIS/PARENT 3 | yes |  |
| `MONDO_0011196` | amyotrophic lateral sclerosis type 5 | 60 | descendant (depth 3) | DIS-GEN/ASSOCIATED_WITH 39, DIS-PHE/PHENOTYPE_PRESENT 20, DIS-DIS/PARENT 1 | yes |  |
| `MONDO_0014641` | frontotemporal dementia and/or amyotrophic lateral sclerosis 4 | 54 | descendant (depth 2) | DIS-GEN/ASSOCIATED_WITH 31, DIS-PHE/PHENOTYPE_PRESENT 20, DIS-DIS/PARENT 3 | yes |  |
| `MONDO_0013891` | amyotrophic lateral sclerosis type 18 | 42 | descendant (depth 2) | DIS-GEN/ASSOCIATED_WITH 33, DIS-PHE/PHENOTYPE_PRESENT 8, DIS-DIS/PARENT 1 | yes |  |
| `MONDO_0030885` | amyotrophic lateral sclerosis 26 with or without frontotemporal dementia | 33 | descendant (depth 2) | DIS-GEN/ASSOCIATED_WITH 20, DIS-PHE/PHENOTYPE_PRESENT 12, DIS-DIS/PARENT 1 | yes |  |
| `EFO_0022918` | amyotrophic lateral sclerosis, dominant | 12 | descendant (depth 2) | DIS-GEN/ASSOCIATED_WITH 11, DIS-DIS/PARENT 1 | yes |  |
| `EFO_0003782` | motor neuron disease | 2,543 | parent | DIS-GEN/ASSOCIATED_WITH 2,513, DIS-DIS/PARENT 27, DRG-DIS/INDICATION 3 | no | parent of the main node |
| `MONDO_0003182` | anterior horn disorder | 2,052 | parent | DIS-GEN/ASSOCIATED_WITH 2,047, DIS-DIS/PARENT 4, EXP-DIS/LINKED_TO 1 | no | parent of the main node |
| `MONDO_0000507` | inclusion body myopathy with Paget disease of bone and frontotemporal dementia | 239 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 235, DIS-DIS/PARENT 4 | no | not a descendant of the main node |
| `MONDO_0017161` | frontotemporal dementia with motor neuron disease | 149 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 143, DIS-DIS/PARENT 6 | no | not a descendant of the main node |
| `MONDO_0030923` | frontotemporal dementia and/or amyotrophic lateral sclerosis | 145 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 141, DIS-DIS/PARENT 4 | no | not a descendant of the main node |
| `MONDO_0008178` | inclusion body myopathy with Paget disease of bone and frontotemporal dementia type 1 | 114 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 78, DIS-PHE/PHENOTYPE_PRESENT 35, DIS-DIS/PARENT 1 | no | not a descendant of the main node |
| `MONDO_0007104` | amyotrophic lateral sclerosis-parkinsonism-dementia complex | 70 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 57, DIS-PHE/PHENOTYPE_PRESENT 12, DIS-DIS/PARENT 1 | no | not a descendant of the main node |
| `OBA_2001018` | age of onset of amyotrophic lateral sclerosis | 1 | name or synonym match | DIS-DIS/PARENT 1 | no | not a descendant of the main node |

Non-disease nodes matching the search terms (not in the seed: not a disease node): `HP_0007354` Amyotrophic lateral sclerosis (PHE); `ENSG00000271238` ALS2 amyotrophic lateral sclerosis 2 (juvenile)  (GEN); `ENSG00000235105` amyotrophic lateral sclerosis 2 (juvenile) chromosome region, candidate 2 (ALS2CR2) pseudogene (GEN).

Proposed seed (17 nodes): `EFO_0001356`, `EFO_0001357`, `EFO_0022918`, `MONDO_0004976`, `MONDO_0007105`, `MONDO_0008458`, `MONDO_0008780`, `MONDO_0010459`, `MONDO_0011196`, `MONDO_0011223`, `MONDO_0012077`, `MONDO_0012945`, `MONDO_0013501`, `MONDO_0013891`, `MONDO_0014641`, `MONDO_0017593`, `MONDO_0030885`. Rule: MONDO_0004976 plus all its descendants over DIS-DIS/PARENT (parent -> child).

#### Huntington disease (HD), OptimusKG

| ID | Name | Degree | Role | Edges by relation | In seed | Excluded because |
|---|---|---|---|---|---|---|
| `MONDO_0007739` | Huntington disease | 5,010 | main | DIS-GEN/ASSOCIATED_WITH 4,935, DRG-DIS/INDICATION 58, DIS-PHE/PHENOTYPE_PRESENT 14 (+1 more) | yes |  |
| `MONDO_0016621` | juvenile Huntington disease | 156 | descendant (depth 1) | DIS-GEN/ASSOCIATED_WITH 155, DIS-DIS/PARENT 1 | yes |  |
| `EFO_0004280` | movement disorder | 8,910 | parent | DIS-GEN/ASSOCIATED_WITH 8,839, DIS-DIS/PARENT 47, DRG-DIS/INDICATION 24 | no | parent of the main node |
| `MONDO_0000167` | Huntington disease and related disorders | 5,373 | parent | DIS-GEN/ASSOCIATED_WITH 5,370, DIS-DIS/PARENT 3 | no | parent of the main node |
| `MONDO_0015548` | Huntington disease-like syndrome | 1,639 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 1,624, DIS-DIS/PARENT 15 | no | not a descendant of the main node |
| `MONDO_0011781` | spinocerebellar ataxia type 17 | 303 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 260, DIS-PHE/PHENOTYPE_PRESENT 40, DIS-DIS/PARENT 3 | no | not a descendant of the main node |
| `MONDO_0018425` | Huntington disease-like syndrome due to C9ORF72 expansions | 249 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 247, DIS-DIS/PARENT 2 | no | not a descendant of the main node |
| `MONDO_0011671` | Huntington disease-like 2 | 167 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 145, DIS-PHE/PHENOTYPE_PRESENT 21, DIS-DIS/PARENT 1 | no | not a descendant of the main node |
| `MONDO_0011299` | Huntington disease-like 1 | 86 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 66, DIS-PHE/PHENOTYPE_PRESENT 18, DIS-DIS/PARENT 2 | no | not a descendant of the main node |
| `MONDO_0011487` | Huntington disease-like 3 | 62 | name or synonym match | DIS-GEN/ASSOCIATED_WITH 42, DIS-PHE/PHENOTYPE_PRESENT 18, DIS-DIS/PARENT 2 | no | not a descendant of the main node |
| `OBA_2001024` | age of onset of Huntington disease | 2 | name or synonym match | DIS-DIS/PARENT 1, DIS-GEN/ASSOCIATED_WITH 1 | no | not a descendant of the main node |

Proposed seed (2 nodes): `MONDO_0007739`, `MONDO_0016621`. Rule: MONDO_0007739 plus all its descendants over DIS-DIS/PARENT (parent -> child).

#### dementia with Lewy bodies (DLB), OptimusKG

| ID | Name | Degree | Role | Edges by relation | In seed | Excluded because |
|---|---|---|---|---|---|---|
| `EFO_0006792` | Lewy body dementia | 1,281 | main | DIS-GEN/ASSOCIATED_WITH 1,258, DRG-DIS/INDICATION 10, DIS-PHE/PHENOTYPE_PRESENT 7 (+3 more) | yes |  |
| `MONDO_0015547` | hereditary dementia | 9,129 | parent | DIS-GEN/ASSOCIATED_WITH 9,111, DIS-DIS/PARENT 18 | no | parent of the main node |
| `DOID_0050890` | synucleinopathy | 2,041 | parent | DIS-GEN/ASSOCIATED_WITH 2,036, DIS-DIS/PARENT 5 | no | parent of the main node |
| `EFO_0006799` | Lewy body dementia measurement | 2 | name or synonym match | DIS-DIS/PARENT 1, DIS-GEN/ASSOCIATED_WITH 1 | no | not a descendant of the main node |
| `OBA_2045212` | Lewy body attribute | 1 | name or synonym match | DIS-DIS/PARENT 1 | no | not a descendant of the main node |

Non-disease nodes matching the search terms (not in the seed: not a disease node): `HP_0100315` Lewy bodies (PHE); `GO_0097413` Lewy body (CCO); `GO_1990037` Lewy body core (CCO); `GO_0140122` regulation of Lewy body formation (BPO); `GO_0140123` negative regulation of Lewy body formation (BPO); `GO_0097414` classical Lewy body (CCO); `GO_0097462` Lewy neurite (CCO); `GO_0140124` positive regulation of Lewy body formation (BPO); `GO_1990038` Lewy body corona (CCO); `GO_0097415` cortical Lewy body (CCO); `GO_0097416` Lewy body-like hyaline inclusion (CCO); `GO_0140121` Lewy body formation (BPO).

Proposed seed (1 node): `EFO_0006792`. Rule: EFO_0006792 plus all its descendants over DIS-DIS/PARENT (parent -> child).

### Overlap between the diseases' seeds

| Graph | Pair | Shared seed nodes | Shared single-hop facts | Shared (partner, relation) | Jaccard of (partner, relation) |
|---|---|---|---|---|---|
| PrimeKG | AD–PD | none | 0 | 67 | 11.8% |
| PrimeKG | AD–FTD | none | 0 | 16 | 4.7% |
| PrimeKG | AD–ALS | none | 0 | 29 | 5.4% |
| PrimeKG | AD–HD | none | 0 | 17 | 5.4% |
| PrimeKG | AD–DLB | none | 0 | 12 | 4.5% |
| PrimeKG | PD–FTD | none | 0 | 15 | 3.1% |
| PrimeKG | PD–ALS | none | 0 | 56 | 8.5% |
| PrimeKG | PD–HD | none | 0 | 31 | 7.0% |
| PrimeKG | PD–DLB | none | 0 | 26 | 6.5% |
| PrimeKG | FTD–ALS | none | 0 | 30 | 7.4% |
| PrimeKG | FTD–HD | none | 0 | 13 | 6.9% |
| PrimeKG | FTD–DLB | none | 0 | 2 | 1.4% |
| PrimeKG | ALS–HD | none | 0 | 17 | 4.3% |
| PrimeKG | ALS–DLB | none | 0 | 6 | 1.7% |
| PrimeKG | HD–DLB | none | 0 | 3 | 2.5% |
| OptimusKG | AD–PD | none | 0 | 5,973 | 43.3% |
| OptimusKG | AD–FTD | none | 0 | 2,265 | 17.2% |
| OptimusKG | AD–ALS | none | 0 | 1,386 | 10.4% |
| OptimusKG | AD–HD | none | 0 | 4,545 | 34.3% |
| OptimusKG | AD–DLB | none | 0 | 1,234 | 9.6% |
| OptimusKG | PD–FTD | none | 0 | 1,615 | 20.1% |
| OptimusKG | PD–ALS | none | 0 | 1,136 | 14.6% |
| OptimusKG | PD–HD | none | 0 | 3,505 | 41.2% |
| OptimusKG | PD–DLB | none | 0 | 1,133 | 15.8% |
| OptimusKG | FTD–ALS | none | 0 | 761 | 20.1% |
| OptimusKG | FTD–HD | none | 0 | 1,400 | 22.5% |
| OptimusKG | FTD–DLB | none | 0 | 609 | 18.5% |
| OptimusKG | ALS–HD | none | 0 | 922 | 15.3% |
| OptimusKG | ALS–DLB | none | 0 | 348 | 12.2% |
| OptimusKG | HD–DLB | none | 0 | 960 | 18.0% |

### Broader seed: pass/fail

| Graph | Seed | Definition | C1 | C2 | C3 | C4 | C5 | C6 | C7 | C8 | All gating |
|---|---|---|---|---|---|---|---|---|---|---|---|
| PrimeKG | proposed (all six) | (a) 1-hop | pass | pass | pass | pass | pass | pass | pass | pass | pass |
| PrimeKG | proposed (all six) | (b) 2-hop | pass | pass | pass | pass | pass | pass | pass | pass | pass |
| PrimeKG | proposed (all six) | (c) p90 | pass | pass | pass | pass | pass | pass | pass | pass | pass |
| PrimeKG | proposed (all six) | (c) p95 | pass | pass | pass | pass | pass | pass | pass | pass | pass |
| PrimeKG | proposed (all six) | (c) p99 | pass | pass | pass | pass | pass | pass | pass | pass | pass |
| PrimeKG | main nodes only | (a) 1-hop | pass | pass | pass | pass | pass | pass | pass | pass | pass |
| PrimeKG | main nodes only | (b) 2-hop | pass | pass | pass | pass | pass | pass | pass | pass | pass |
| PrimeKG | main nodes only | (c) p90 | pass | pass | pass | pass | pass | pass | pass | pass | pass |
| PrimeKG | main nodes only | (c) p95 | pass | pass | pass | pass | pass | pass | pass | pass | pass |
| PrimeKG | main nodes only | (c) p99 | pass | pass | pass | pass | pass | pass | pass | pass | pass |
| OptimusKG | proposed (all six) | (a) 1-hop | pass | pass | pass | pass | pass | pass | pass | pass | pass |
| OptimusKG | proposed (all six) | (b) 2-hop | pass | pass | pass | pass | pass | pass | pass | pass | pass |
| OptimusKG | proposed (all six) | (c) p90 | pass | pass | pass | pass | pass | pass | pass | pass | pass |
| OptimusKG | proposed (all six) | (c) p95 | pass | pass | pass | pass | pass | pass | pass | pass | pass |
| OptimusKG | proposed (all six) | (c) p99 | pass | pass | pass | pass | pass | pass | pass | pass | pass |
| OptimusKG | main nodes only | (a) 1-hop | pass | pass | pass | pass | pass | pass | pass | pass | pass |
| OptimusKG | main nodes only | (b) 2-hop | pass | pass | pass | pass | pass | pass | pass | pass | pass |
| OptimusKG | main nodes only | (c) p90 | pass | pass | pass | pass | pass | pass | pass | pass | pass |
| OptimusKG | main nodes only | (c) p95 | pass | pass | pass | pass | pass | pass | pass | pass | pass |
| OptimusKG | main nodes only | (c) p99 | pass | pass | pass | pass | pass | pass | pass | pass | pass |

### Broader seed metrics: PrimeKG, proposed (all six)

| Metric (proposed threshold) | (a) 1-hop | (b) 2-hop | (c) p90 | (c) p95 | (c) p99 |
|---|---|---|---|---|---|
| Nodes | 952 | 29,351 | 7,604 | 11,579 | 25,731 |
| Edges | 9,406 | 266,675 | 26,272 | 44,451 | 138,603 |
| Degree cap for pruning | n/a | n/a | 136 | 206 | 991 |
| C1 single-hop facts (≥ 270) | 1,729 | 1,729 | 1,729 | 1,729 | 1,729 |
| single-hop facts, distinct (partner, relation) over seed nodes (reported) | 943 | 943 | 943 | 943 | 943 |
| C2 relations with ≥ 30 facts (≥ 4) | 6 | 6 | 6 | 6 | 6 |
| C2 balanced capacity (≥ 270) | 610 | 610 | 610 | 610 | 610 |
| C3 two-hop paths (≥ 225) | 32,422 | 593,267 | 62,160 | 102,815 | 314,530 |
| C3 distinct endpoints (≥ 225) | 794 | 29,193 | 7,446 | 11,421 | 25,573 |
| C4 patterns with ≥ 20 paths (≥ 3) | 34 | 56 | 45 | 47 | 52 |
| C4 balanced capacity (≥ 225) | 2,842 | 4,476 | 3,776 | 3,847 | 4,194 |
| C5 top-1 intermediate path share (≤ 20%) | 1.7% | 3.3% | 1.5% | 2.0% | 1.3% |
| C5 top-5 path share (≤ 50%) | 7.4% | 10.8% | 5.7% | 6.8% | 5.9% |
| top-20 path share | 24.4% | 24.5% | 16.4% | 17.9% | 18.8% |
| top-1 / top-5 / top-20 endpoint share | 10.1% / 27.2% / 29.7% | 7.5% / 26.0% / 29.5% | 1.6% / 5.1% / 14.0% | 1.6% / 6.7% / 16.0% | 2.3% / 9.6% / 22.2% |
| endpoints reachable only via top 5 | 0.5% | 4.9% | 0.5% | 0.9% | 0.9% |
| C6 facts with an absent sibling (≥ 135) | 1,710 | 1,710 | 1,710 | 1,710 | 1,710 |
| C7 unanswerable (r, y) pairs (≥ 180) | 1,496 | 33,647 | 8,601 | 12,395 | 28,795 |
| C7 relations with ≥ 20 (≥ 3) | 8 | 8 | 8 | 8 | 8 |
| C8 (i) contradicted pairs (≥ 30) | 1,387 | 65,861 | 11,735 | 22,548 | 51,057 |
| C8 (i) of which touch the seed | 826 | 826 | 826 | 826 | 826 |
| C8 (ii) spare absence premises | 1,316 | 33,467 | 8,421 | 12,215 | 28,615 |
| C8 total (≥ 90) | 2,703 | 99,328 | 20,156 | 34,763 | 79,672 |
| R1 shared-neighbor disease pairs | 299 (266 with 1–20 shared) | 62,795 (62,480 with 1–20 shared) | 9,780 (9,726 with 1–20 shared) | 15,702 (15,639 with 1–20 shared) | 30,157 (29,968 with 1–20 shared) |
| R2 facts in seed groups of ≤ 20 edges | 252 | 252 | 252 | 252 | 252 |
| R2 facts reachable in one page from either end | 1,729 | 927 | 1,504 | 1,372 | 1,019 |
| R3 single-hop facts from a restricted source | 41.1% (711) | 41.1% (711) | 41.1% (711) | 41.1% (711) | 41.1% (711) |
| R3 two-hop path edges from a restricted source | 72.5% (6,696) | 59.1% (157,703) | 32.3% (8,470) | 24.9% (11,066) | 34.3% (47,561) |
| R3 subgraph edges from a restricted source | 71.6% (6,731) | 59.1% (157,703) | 32.3% (8,487) | 24.9% (11,068) | 34.3% (47,561) |
| R3 note: single-hop facts whose disease end was mapped through the UMLS crosswalk (not counted in R3) | 53.3% (921) | 53.3% (921) | 53.3% (921) | 53.3% (921) | 53.3% (921) |
| R3 note: subgraph edges mapped through the UMLS crosswalk (not counted in R3) | 15.6% (1,472) | 5.7% (15,199) | 9.2% (2,420) | 8.6% (3,830) | 7.8% (10,777) |

### Broader seed metrics: PrimeKG, main nodes only

| Metric (proposed threshold) | (a) 1-hop | (b) 2-hop | (c) p90 | (c) p95 | (c) p99 |
|---|---|---|---|---|---|
| Nodes | 780 | 27,842 | 6,450 | 10,433 | 23,822 |
| Edges | 8,182 | 247,418 | 21,643 | 38,305 | 124,542 |
| Degree cap for pruning | n/a | n/a | 136 | 206 | 991 |
| C1 single-hop facts (≥ 270) | 959 | 959 | 959 | 959 | 959 |
| single-hop facts, distinct (partner, relation) over seed nodes (reported) | 786 | 786 | 786 | 786 | 786 |
| C2 relations with ≥ 30 facts (≥ 4) | 6 | 6 | 6 | 6 | 6 |
| C2 balanced capacity (≥ 270) | 518 | 518 | 518 | 518 | 518 |
| C3 two-hop paths (≥ 225) | 19,536 | 344,526 | 35,737 | 57,343 | 174,188 |
| C3 distinct endpoints (≥ 225) | 679 | 27,741 | 6,349 | 10,332 | 23,721 |
| C4 patterns with ≥ 20 paths (≥ 3) | 31 | 54 | 43 | 43 | 51 |
| C4 balanced capacity (≥ 225) | 2,634 | 4,351 | 3,570 | 3,688 | 4,074 |
| C5 top-1 intermediate path share (≤ 20%) | 2.1% | 3.2% | 1.2% | 2.0% | 1.2% |
| C5 top-5 path share (≤ 50%) | 8.0% | 10.1% | 5.4% | 5.6% | 5.8% |
| top-20 path share | 23.5% | 24.6% | 15.3% | 15.4% | 18.8% |
| top-1 / top-5 / top-20 endpoint share | 20.5% / 23.9% / 25.3% | 7.9% / 18.8% / 30.2% | 2.2% / 6.7% / 15.4% | 1.8% / 5.8% / 20.2% | 2.2% / 11.0% / 21.4% |
| endpoints reachable only via top 5 | 0.0% | 3.4% | 0.8% | 0.5% | 0.7% |
| C6 facts with an absent sibling (≥ 135) | 959 | 959 | 959 | 959 | 959 |
| C7 unanswerable (r, y) pairs (≥ 180) | 1,106 | 31,838 | 7,095 | 10,990 | 26,592 |
| C7 relations with ≥ 20 (≥ 3) | 8 | 8 | 8 | 8 | 8 |
| C8 (i) contradicted pairs (≥ 30) | 966 | 54,949 | 8,697 | 18,312 | 43,290 |
| C8 (i) of which touch the seed | 432 | 432 | 432 | 432 | 432 |
| C8 (ii) spare absence premises | 926 | 31,658 | 6,915 | 10,810 | 26,412 |
| C8 total (≥ 90) | 1,892 | 86,607 | 15,612 | 29,122 | 69,702 |
| R1 shared-neighbor disease pairs | 144 (124 with 1–20 shared) | 24,540 (24,294 with 1–20 shared) | 4,606 (4,573 with 1–20 shared) | 7,315 (7,271 with 1–20 shared) | 12,538 (12,406 with 1–20 shared) |
| R2 facts in seed groups of ≤ 20 edges | 106 | 106 | 106 | 106 | 106 |
| R2 facts reachable in one page from either end | 959 | 507 | 845 | 778 | 584 |
| R3 single-hop facts from a restricted source | 37.5% (360) | 37.5% (360) | 37.5% (360) | 37.5% (360) | 37.5% (360) |
| R3 two-hop path edges from a restricted source | 75.0% (6,065) | 61.6% (152,432) | 34.9% (7,532) | 26.3% (10,058) | 35.6% (44,312) |
| R3 subgraph edges from a restricted source | 74.4% (6,091) | 61.6% (152,432) | 34.9% (7,543) | 26.3% (10,061) | 35.6% (44,312) |
| R3 note: single-hop facts whose disease end was mapped through the UMLS crosswalk (not counted in R3) | 52.7% (505) | 52.7% (505) | 52.7% (505) | 52.7% (505) | 52.7% (505) |
| R3 note: subgraph edges mapped through the UMLS crosswalk (not counted in R3) | 15.8% (1,290) | 5.9% (14,719) | 9.8% (2,130) | 9.1% (3,490) | 8.3% (10,278) |

### Broader seed metrics: OptimusKG, proposed (all six)

| Metric (proposed threshold) | (a) 1-hop | (b) 2-hop | (c) p90 | (c) p95 | (c) p99 |
|---|---|---|---|---|---|
| Nodes | 14,892 | 77,769 | 38,574 | 56,673 | 72,878 |
| Edges | 436,522 | 13,258,273 | 1,425,222 | 5,055,488 | 11,733,113 |
| Degree cap for pruning | n/a | n/a | 501 | 906 | 2,337 |
| C1 single-hop facts (≥ 270) | 38,923 | 38,923 | 38,923 | 38,923 | 38,923 |
| single-hop facts, distinct (partner, relation) over seed nodes (reported) | 14,842 | 14,842 | 14,842 | 14,842 | 14,842 |
| C2 relations with ≥ 30 facts (≥ 4) | 5 | 5 | 5 | 5 | 5 |
| C2 balanced capacity (≥ 270) | 527 | 527 | 527 | 527 | 527 |
| C3 two-hop paths (≥ 225) | 2,779,176 | 48,158,298 | 4,203,772 | 11,589,681 | 37,513,473 |
| C3 distinct endpoints (≥ 225) | 14,506 | 77,383 | 38,188 | 56,287 | 72,492 |
| C4 patterns with ≥ 20 paths (≥ 3) | 58 | 87 | 71 | 73 | 85 |
| C4 balanced capacity (≥ 225) | 4,711 | 7,328 | 5,781 | 5,932 | 7,006 |
| C5 top-1 intermediate path share (≤ 20%) | 3.0% | 0.3% | 2.0% | 0.7% | 0.2% |
| C5 top-5 path share (≤ 50%) | 7.4% | 1.2% | 4.9% | 1.8% | 0.8% |
| top-20 path share | 15.5% | 3.4% | 10.2% | 4.0% | 2.6% |
| top-1 / top-5 / top-20 endpoint share | 30.5% / 74.6% / 95.2% | 10.2% / 17.3% / 27.5% | 11.6% / 28.3% / 36.2% | 7.9% / 19.2% / 28.1% | 6.1% / 11.5% / 17.0% |
| endpoints reachable only via top 5 | 0.0% | 0.1% | 0.0% | 0.0% | 0.0% |
| C6 facts with an absent sibling (≥ 135) | 38,875 | 38,875 | 38,875 | 38,875 | 38,875 |
| C7 unanswerable (r, y) pairs (≥ 180) | 16,079 | 79,664 | 39,897 | 52,952 | 71,635 |
| C7 relations with ≥ 20 (≥ 3) | 7 | 7 | 7 | 7 | 7 |
| C8 (i) contradicted pairs (≥ 30) | 992 | 3,285,796 | 539,868 | 1,869,602 | 3,148,604 |
| C8 (i) of which touch the seed | 820 | 820 | 820 | 820 | 820 |
| C8 (ii) spare absence premises | 15,899 | 79,484 | 39,717 | 52,772 | 71,455 |
| C8 total (≥ 90) | 16,891 | 3,365,280 | 579,585 | 1,922,374 | 3,220,059 |
| R1 shared-neighbor disease pairs | 1,321 (310 with 1–20 shared) | 600,653 (461,193 with 1–20 shared) | 78,517 (73,882 with 1–20 shared) | 220,142 (196,318 with 1–20 shared) | 470,390 (368,253 with 1–20 shared) |
| R2 facts in seed groups of ≤ 20 edges | 457 | 457 | 457 | 457 | 457 |
| R2 facts reachable in one page from either end | 28,698 | 1,494 | 24,769 | 14,960 | 2,608 |
| R3 single-hop facts from a restricted source | 1.0% (398) | 1.0% (398) | 1.0% (398) | 1.0% (398) | 1.0% (398) |
| R3 two-hop path edges from a restricted source | 4.1% (17,814) | 2.2% (296,412) | 1.8% (25,651) | 1.2% (61,362) | 2.4% (279,749) |
| R3 subgraph edges from a restricted source | 4.5% (19,615) | 2.2% (298,213) | 1.9% (27,452) | 1.2% (63,163) | 2.4% (281,550) |
| R3 note: single-hop facts whose disease end was mapped through the UMLS crosswalk (not counted in R3) | n/a | n/a | n/a | n/a | n/a |
| R3 note: subgraph edges mapped through the UMLS crosswalk (not counted in R3) | n/a | n/a | n/a | n/a | n/a |

### Broader seed metrics: OptimusKG, main nodes only

| Metric (proposed threshold) | (a) 1-hop | (b) 2-hop | (c) p90 | (c) p95 | (c) p99 |
|---|---|---|---|---|---|
| Nodes | 14,477 | 76,985 | 36,608 | 55,672 | 72,231 |
| Edges | 367,878 | 13,156,808 | 1,322,447 | 4,930,747 | 11,635,125 |
| Degree cap for pruning | n/a | n/a | 501 | 906 | 2,337 |
| C1 single-hop facts (≥ 270) | 29,398 | 29,398 | 29,398 | 29,398 | 29,398 |
| single-hop facts, distinct (partner, relation) over seed nodes (reported) | 14,474 | 14,474 | 14,474 | 14,474 | 14,474 |
| C2 relations with ≥ 30 facts (≥ 4) | 5 | 5 | 5 | 5 | 5 |
| C2 balanced capacity (≥ 270) | 467 | 467 | 467 | 467 | 467 |
| C3 two-hop paths (≥ 225) | 1,606,585 | 34,565,131 | 2,800,938 | 8,897,010 | 27,943,406 |
| C3 distinct endpoints (≥ 225) | 14,257 | 76,765 | 36,388 | 55,452 | 72,011 |
| C4 patterns with ≥ 20 paths (≥ 3) | 55 | 87 | 66 | 67 | 83 |
| C4 balanced capacity (≥ 225) | 4,368 | 7,155 | 5,431 | 5,600 | 6,793 |
| C5 top-1 intermediate path share (≤ 20%) | 1.6% | 0.1% | 0.9% | 0.3% | 0.1% |
| C5 top-5 path share (≤ 50%) | 4.8% | 0.5% | 2.8% | 0.9% | 0.3% |
| top-20 path share | 10.0% | 1.7% | 5.7% | 1.9% | 1.0% |
| top-1 / top-5 / top-20 endpoint share | 30.8% / 93.7% / 95.0% | 10.3% / 19.4% / 25.8% | 12.1% / 36.7% / 37.2% | 7.9% / 24.1% / 28.0% | 6.1% / 17.9% / 23.8% |
| endpoints reachable only via top 5 | 2.8% | 0.1% | 1.1% | 0.7% | 0.0% |
| C6 facts with an absent sibling (≥ 135) | 29,398 | 29,398 | 29,398 | 29,398 | 29,398 |
| C7 unanswerable (r, y) pairs (≥ 180) | 15,461 | 78,650 | 37,649 | 51,624 | 70,742 |
| C7 relations with ≥ 20 (≥ 3) | 7 | 7 | 7 | 7 | 7 |
| C8 (i) contradicted pairs (≥ 30) | 912 | 3,249,815 | 520,095 | 1,834,396 | 3,112,630 |
| C8 (i) of which touch the seed | 800 | 800 | 800 | 800 | 800 |
| C8 (ii) spare absence premises | 15,281 | 78,470 | 37,469 | 51,444 | 70,562 |
| C8 total (≥ 90) | 16,193 | 3,328,285 | 557,564 | 1,885,840 | 3,183,192 |
| R1 shared-neighbor disease pairs | 179 (21 with 1–20 shared) | 115,812 (62,148 with 1–20 shared) | 28,893 (25,507 with 1–20 shared) | 66,614 (51,320 with 1–20 shared) | 105,930 (61,722 with 1–20 shared) |
| R2 facts in seed groups of ≤ 20 edges | 109 | 109 | 109 | 109 | 109 |
| R2 facts reachable in one page from either end | 28,339 | 917 | 25,108 | 16,122 | 2,860 |
| R3 single-hop facts from a restricted source | 0.9% (271) | 0.9% (271) | 0.9% (271) | 0.9% (271) | 0.9% (271) |
| R3 two-hop path edges from a restricted source | 4.9% (17,651) | 2.3% (296,163) | 1.9% (25,316) | 1.2% (60,991) | 2.4% (279,461) |
| R3 subgraph edges from a restricted source | 5.3% (19,449) | 2.3% (297,961) | 2.1% (27,114) | 1.3% (62,789) | 2.4% (281,259) |
| R3 note: single-hop facts whose disease end was mapped through the UMLS crosswalk (not counted in R3) | n/a | n/a | n/a | n/a | n/a |
| R3 note: subgraph edges mapped through the UMLS crosswalk (not counted in R3) | n/a | n/a | n/a | n/a | n/a |

### Per-disease contribution (reported)

| Graph | Disease | Seed nodes | Single-hop facts | Distinct (partner, relation) | Facts no other disease reaches | Relations | Largest relations | C2 inputs alone: relations ≥ 30 / balanced capacity | C2 inputs, other five without it | It adds (all six − other five) | C2 inputs, AD plus this disease |
|---|---|---|---|---|---|---|---|---|---|---|---|
| PrimeKG | AD | 5 | 364 | 243 | 364 | 6 | disease_protein 205, disease_phenotype_positive 67, contraindication 58, indication 15 | 3 / 267 | 6 / 524 | +0 / +86 | n/a |
| PrimeKG | PD | 7 | 728 | 390 | 728 | 7 | disease_protein 314, disease_phenotype_positive 221, indication 79, contraindication 47 | 5 / 409 | 5 / 423 | +1 / +187 | 6 / 501 |
| PrimeKG | FTD | 3 | 129 | 114 | 129 | 3 | disease_phenotype_positive 93, disease_protein 20, disease_disease 16 | 1 / 129 | 6 / 594 | +0 / +16 | 3 / 324 |
| PrimeKG | ALS | 4 | 354 | 322 | 354 | 6 | disease_phenotype_positive 154, disease_protein 116, exposure_disease 64, disease_disease 13 | 3 / 300 | 6 / 532 | +0 / +78 | 4 / 392 |
| PrimeKG | HD | 2 | 118 | 87 | 118 | 4 | disease_phenotype_positive 76, disease_protein 34, indication 4, disease_disease 4 | 2 / 118 | 6 / 602 | +0 / +8 | 3 / 316 |
| PrimeKG | DLB | 1 | 36 | 36 | 36 | 5 | disease_protein 22, disease_phenotype_positive 7, off-label use 3, disease_disease 2 | 0 / 36 | 6 / 603 | +0 / +7 | 3 / 281 |
| PrimeKG | **all six** | 22 | 1,729 |  |  |  |  | 6 / 610 |  |  |  |
| OptimusKG | AD | 7 | 13,753 | 12,774 | 13,753 | 6 | DIS-GEN/ASSOCIATED_WITH 13,368, DRG-DIS/INDICATION 314, DIS-PHE/PHENOTYPE_PRESENT 47, DIS-DIS/PARENT 13 | 3 / 287 | 5 / 511 | +0 / +16 | n/a |
| OptimusKG | PD | 16 | 8,878 | 7,007 | 8,878 | 6 | DIS-GEN/ASSOCIATED_WITH 8,244, DIS-PHE/PHENOTYPE_PRESENT 308, DRG-DIS/INDICATION 260, EXP-DIS/LINKED_TO 41 | 4 / 390 | 5 / 469 | +0 / +58 | 5 / 414 |
| OptimusKG | FTD | 10 | 5,359 | 2,627 | 5,359 | 4 | DIS-GEN/ASSOCIATED_WITH 5,199, DIS-PHE/PHENOTYPE_PRESENT 124, DIS-DIS/PARENT 19, DRG-DIS/INDICATION 17 | 2 / 252 | 5 / 508 | +0 / +19 | 4 / 367 |
| OptimusKG | ALS | 17 | 4,487 | 1,921 | 4,487 | 5 | DIS-GEN/ASSOCIATED_WITH 3,809, DIS-PHE/PHENOTYPE_PRESENT 428, DRG-DIS/INDICATION 157, EXP-DIS/LINKED_TO 65 | 4 / 417 | 5 / 442 | +0 / +85 | 5 / 441 |
| OptimusKG | HD | 2 | 5,165 | 5,009 | 5,165 | 4 | DIS-GEN/ASSOCIATED_WITH 5,090, DRG-DIS/INDICATION 58, DIS-PHE/PHENOTYPE_PRESENT 14, DIS-DIS/PARENT 3 | 2 / 183 | 5 / 524 | +0 / +3 | 3 / 304 |
| OptimusKG | DLB | 1 | 1,281 | 1,281 | 1,281 | 6 | DIS-GEN/ASSOCIATED_WITH 1,258, DRG-DIS/INDICATION 10, DIS-PHE/PHENOTYPE_PRESENT 7, DRG-DIS/OFF_LABEL_USE 2 | 1 / 131 | 5 / 521 | +0 / +6 | 3 / 300 |
| OptimusKG | **all six** | 53 | 38,923 |  |  |  |  | 5 / 527 |  |  |  |

How to read the per-disease table:

- A disease's facts are the single-hop facts on its seed nodes.
- "Distinct (partner, relation)" counts each partner and relation once,
  ignoring which of the disease's seed nodes the fact attaches to.
- "C2 inputs" are the two numbers C2 tests: relations with at least 30 facts
  (C2 needs 4 or more) and balanced capacity (C2 needs 270 or more).
- "Other five without it" is the combined pool with that disease's facts
  removed. "It adds" is the difference from all six.
- "AD plus this disease" is the pool of Alzheimer's and that one disease.
  These columns recompute **C2 only**. C1 and C3 to C8 were not recomputed for
  any subset, so they say nothing about whether a subset passes the test.

### Broader seed detail: PrimeKG, proposed seed

**Nodes by type**

| Type | (a) 1-hop | (b) 2-hop | (c) p95 |
|---|---|---|---|
| gene/protein | 327 | 9,299 | 2,571 |
| disease | 61 | 7,793 | 3,904 |
| drug | 116 | 4,202 | 1,176 |
| biological_process | 0 | 3,661 | 1,511 |
| effect/phenotype | 351 | 1,754 | 1,025 |
| molecular_function | 0 | 761 | 355 |
| pathway | 0 | 756 | 243 |
| cellular_component | 0 | 493 | 270 |
| exposure | 97 | 480 | 381 |
| anatomy | 0 | 152 | 143 |

**Edges by relation**, with the relation's sources (top six by edge count in (b))

| Relation | (a) 1-hop | (b) 2-hop | (c) p95 | single-hop facts | Sources |
|---|---|---|---|---|---|
| `drug_drug` | 3,593 | 123,371 | 4,099 | 0 | DrugBank |
| `disease_phenotype_positive` | 1,002 | 30,543 | 11,268 | 618 | HPO, HPO annotations |
| `anatomy_protein_present` | 0 | 28,515 | 10,167 | 0 | Bgee |
| `protein_protein` | 623 | 22,643 | 3,258 | 0 | BioGRID, HuRI, Menche et al. interactome, STRING |
| `drug_effect` | 1,672 | 19,989 | 2,770 | 0 | SIDER |
| `disease_protein` | 1,082 | 8,887 | 3,061 | 711 | DisGeNET |
| `bioprocess_protein` | 0 | 7,811 | 2,047 | 0 | GO annotations (NCBI gene2go) |
| `contraindication` | 223 | 5,436 | 566 | 107 | DrugCentral |
| `cellcomp_protein` | 0 | 2,912 | 976 | 0 | GO annotations (NCBI gene2go) |
| `drug_protein/target` | 119 | 2,633 | 512 | 0 | DrugBank |
| `molfunc_protein` | 0 | 2,284 | 662 | 0 | GO annotations (NCBI gene2go) |
| `disease_disease` | 91 | 1,404 | 559 | 69 | HPO, MONDO |
| `pathway_protein` | 0 | 1,388 | 301 | 0 | Reactome |
| `phenotype_protein` | 170 | 1,106 | 415 | 0 | DisGeNET |
| `phenotype_phenotype` | 162 | 1,099 | 815 | 0 | HPO |
| `exposure_disease` | 121 | 1,041 | 817 | 114 | CTD |
| `drug_protein/enzyme` | 67 | 1,038 | 137 | 0 | DrugBank |
| `exposure_exposure` | 161 | 946 | 643 | 0 | CTD |
| `indication` | 164 | 758 | 194 | 100 | DrugCentral |
| `exposure_protein` | 113 | 747 | 358 | 0 | CTD |
| `drug_protein/transporter` | 28 | 627 | 72 | 0 | DrugBank |
| `exposure_bioprocess` | 0 | 578 | 352 | 0 | CTD |
| `anatomy_protein_absent` | 0 | 432 | 277 | 0 | Bgee |
| `disease_phenotype_negative` | 12 | 295 | 106 | 7 | HPO annotations |
| `off-label use` | 3 | 118 | 9 | 3 | DrugCentral |
| `drug_protein/carrier` | 0 | 52 | 2 | 0 | DrugBank |
| `exposure_molfunc` | 0 | 19 | 8 | 0 | CTD |
| `exposure_cellcomp` | 0 | 3 | 0 | 0 | CTD |

**Degree within the subgraph** (distinct neighbors)

| Definition | min | median | p90 | p99 | max | mean |
|---|---|---|---|---|---|---|
| (a) 1-hop | 100.0% | 5.0 | 69.0 | 150.0 | 305.0 | 19.7 |
| (b) 2-hop | 100.0% | 2.0 | 30.0 | 256.0 | 2,589.0 | 18.2 |
| (c) p95 | 100.0% | 100.0% | 10.0 | 142.0 | 305.0 | 7.7 |

**Top 20 hubs, (a) 1-hop** (by degree within the subgraph)

| ID | Name | Type | Degree in subgraph | Degree in full graph | Seed |
|---|---|---|---|---|---|
| `28547` | Parkinson disease | disease | 305 | 305 | yes |
| `27248` | amyotrophic lateral sclerosis | disease | 289 | 289 | yes |
| `28780` | Alzheimer disease | disease | 233 | 233 | yes |
| `14323` | Aripiprazole | drug | 166 | 2,360 |  |
| `14223` | Risperidone | drug | 157 | 2,081 |  |
| `14999` | Quetiapine | drug | 154 | 2,464 |  |
| `14161` | Olanzapine | drug | 153 | 2,139 |  |
| `15001` | Paliperidone | drug | 152 | 2,398 |  |
| `15138` | Selegiline | drug | 151 | 2,065 |  |
| `28473` | Pick disease | disease | 150 | 155 |  |
| `15093` | Clozapine | drug | 148 | 2,575 |  |
| `33241` | hereditary late onset Parkinson disease | disease | 145 | 145 | yes |
| `14140` | Ziprasidone | drug | 142 | 2,226 |  |
| `14176` | Fluoxetine | drug | 142 | 2,157 |  |
| `16842` | Pramipexole | drug | 141 | 1,786 |  |
| `14952` | Haloperidol | drug | 136 | 2,429 |  |
| `30093` | parkinsonian-pyramidal syndrome | disease | 136 | 141 |  |
| `15333` | Rasagiline | drug | 135 | 2,112 |  |
| `27402` | spinocerebellar ataxia | disease | 135 | 419 |  |
| `32974` | atypical juvenile parkinsonism | disease | 133 | 138 |  |

**Top 20 hubs, (b) 2-hop** (by degree within the subgraph)

| ID | Name | Type | Degree in subgraph | Degree in full graph | Seed |
|---|---|---|---|---|---|
| `14178` | Chlorpromazine | drug | 2,589 | 2,589 |  |
| `15093` | Clozapine | drug | 2,575 | 2,575 |  |
| `14156` | Amitriptyline | drug | 2,541 | 2,541 |  |
| `14999` | Quetiapine | drug | 2,464 | 2,464 |  |
| `14952` | Haloperidol | drug | 2,429 | 2,429 |  |
| `26180` | Autosomal recessive inheritance | effect/phenotype | 2,399 | 2,399 |  |
| `15001` | Paliperidone | drug | 2,398 | 2,398 |  |
| `14323` | Aripiprazole | drug | 2,360 | 2,360 |  |
| `14140` | Ziprasidone | drug | 2,226 | 2,226 |  |
| `22759` | Autosomal dominant inheritance | effect/phenotype | 2,205 | 2,205 |  |
| `14176` | Fluoxetine | drug | 2,157 | 2,157 |  |
| `14158` | Indomethacin | drug | 2,147 | 2,147 |  |
| `14161` | Olanzapine | drug | 2,139 | 2,139 |  |
| `14276` | Hydrochlorothiazide | drug | 2,129 | 2,129 |  |
| `15333` | Rasagiline | drug | 2,112 | 2,112 |  |
| `23000` | Seizure | effect/phenotype | 2,109 | 2,109 |  |
| `14223` | Risperidone | drug | 2,081 | 2,081 |  |
| `15138` | Selegiline | drug | 2,065 | 2,065 |  |
| `14546` | Metoclopramide | drug | 2,055 | 2,055 |  |
| `14993` | Phenobarbital | drug | 1,981 | 1,981 |  |

**Top 20 hubs, (c) p95** (by degree within the subgraph)

| ID | Name | Type | Degree in subgraph | Degree in full graph | Seed |
|---|---|---|---|---|---|
| `28547` | Parkinson disease | disease | 305 | 305 | yes |
| `27248` | amyotrophic lateral sclerosis | disease | 289 | 289 | yes |
| `28780` | Alzheimer disease | disease | 233 | 233 | yes |
| `9196` | CNR1 | gene/protein | 206 | 206 |  |
| `6329` | BDNF | gene/protein | 204 | 204 |  |
| `3533` | RCAN1 | gene/protein | 202 | 202 |  |
| `4173` | TALDO1 | gene/protein | 200 | 200 |  |
| `2730` | TLE3 | gene/protein | 199 | 199 |  |
| `4352` | TPP1 | gene/protein | 199 | 199 |  |
| `557` | DDIT4 | gene/protein | 198 | 198 |  |
| `8855` | HFE | gene/protein | 198 | 198 |  |
| `23096` | Dyskinesia | effect/phenotype | 196 | 196 |  |
| `2606` | APOC1 | gene/protein | 196 | 196 |  |
| `4261` | ENO2 | gene/protein | 196 | 196 |  |
| `6035` | SS18L1 | gene/protein | 196 | 196 |  |
| `6116` | SYNJ1 | gene/protein | 196 | 196 |  |
| `6751` | TFAM | gene/protein | 196 | 196 |  |
| `22442` | Dementia | effect/phenotype | 195 | 195 |  |
| `23172` | Hyporeflexia | effect/phenotype | 194 | 194 |  |
| `4917` | LAT | gene/protein | 193 | 193 |  |

**Top 10 two-hop intermediates, (a) 1-hop**

| ID | Name | Type | Paths | Path share | Endpoint share |
|---|---|---|---|---|---|
| `23000` | Seizure | effect/phenotype | 567 | 1.7% | 10.1% |
| `14830` | Amantadine | drug | 468 | 1.4% | 14.7% |
| `14223` | Risperidone | drug | 462 | 1.4% | 19.4% |
| `15001` | Paliperidone | drug | 447 | 1.4% | 18.8% |
| `15138` | Selegiline | drug | 444 | 1.4% | 18.6% |
| `15493` | Bromocriptine | drug | 440 | 1.4% | 13.9% |
| `14139` | Benzatropine | drug | 436 | 1.3% | 13.7% |
| `16842` | Pramipexole | drug | 414 | 1.3% | 17.4% |
| `15333` | Rasagiline | drug | 396 | 1.2% | 16.6% |
| `15291` | Ropinirole | drug | 387 | 1.2% | 16.2% |

**Top 10 two-hop intermediates, (b) 2-hop**

| ID | Name | Type | Paths | Path share | Endpoint share |
|---|---|---|---|---|---|
| `22759` | Autosomal dominant inheritance | effect/phenotype | 19,764 | 3.3% | 7.5% |
| `23000` | Seizure | effect/phenotype | 14,763 | 2.5% | 7.2% |
| `26180` | Autosomal recessive inheritance | effect/phenotype | 14,358 | 2.4% | 8.2% |
| `14139` | Benzatropine | drug | 7,760 | 1.3% | 6.6% |
| `15493` | Bromocriptine | drug | 7,336 | 1.2% | 6.3% |
| `15001` | Paliperidone | drug | 7,185 | 1.2% | 8.2% |
| `15333` | Rasagiline | drug | 6,327 | 1.1% | 7.2% |
| `14223` | Risperidone | drug | 6,237 | 1.1% | 7.1% |
| `14830` | Amantadine | drug | 6,216 | 1.0% | 5.3% |
| `15138` | Selegiline | drug | 6,186 | 1.0% | 7.1% |

**Top 10 two-hop intermediates, (c) p95**

| ID | Name | Type | Paths | Path share | Endpoint share |
|---|---|---|---|---|---|
| `22442` | Dementia | effect/phenotype | 2,057 | 2.0% | 1.6% |
| `84695` | Depressivity | effect/phenotype | 1,611 | 1.6% | 1.6% |
| `23118` | Rigidity | effect/phenotype | 1,368 | 1.3% | 1.5% |
| `6329` | BDNF | gene/protein | 995 | 1.0% | 1.7% |
| `8855` | HFE | gene/protein | 965 | 0.9% | 1.7% |
| `23153` | Parkinsonism | effect/phenotype | 960 | 0.9% | 1.1% |
| `1012` | GDNF | gene/protein | 936 | 0.9% | 1.4% |
| `5877` | NGF | gene/protein | 900 | 0.9% | 1.6% |
| `6739` | TH | gene/protein | 900 | 0.9% | 1.6% |
| `4278` | NECTIN2 | gene/protein | 890 | 0.9% | 1.6% |

**Candidate breakdowns, (c) p95**

| Breakdown | Counts |
|---|---|
| Single-hop facts by relation | `disease_protein` 711; `disease_phenotype_positive` 618; `exposure_disease` 114; `contraindication` 107; `indication` 100; `disease_disease` 69; `disease_phenotype_negative` 7; `off-label use` 3 |
| Top two-hop patterns | `disease_phenotype_positive | disease_phenotype_positive` 22,791; `disease_protein | anatomy_protein_present` 18,955; `disease_protein | protein_protein` 8,009; `contraindication | drug_drug` 7,584; `disease_phenotype_positive | drug_effect` 6,928; `indication | drug_drug` 6,928; `disease_protein | disease_protein` 6,103; `disease_protein | bioprocess_protein` 4,700 |
| Unanswerable candidates by relation (distinct y) | `disease_disease` 3,882; `disease_protein` 2,571; `indication` 1,176; `contraindication` 1,176; `off-label use` 1,171; `disease_phenotype_positive` 1,025; `disease_phenotype_negative` 1,014; `exposure_disease` 380 |
| Contradicted pairs by relation | `disease_phenotype_positive` 11,253; `anatomy_protein_present` 10,167; `contraindication` 566; `anatomy_protein_absent` 277; `indication` 194; `disease_phenotype_negative` 91 |
| Seed fan-out (seed, relation): edges | 27248 | disease_protein: 108; 27248 | disease_phenotype_positive: 106; 28547 | disease_protein: 104; 83960 | disease_protein: 101; 28780 | disease_protein: 101; 28801 | disease_protein: 86; 33241 | disease_protein: 85; 28547 | disease_phenotype_positive: 73; 27248 | exposure_disease: 64; 28751 | disease_phenotype_positive: 60; 28780 | contraindication: 56; 31245 | disease_phenotype_positive: 55 |
| Shared-neighbor disease pairs by shared count | pairs 15,702; shared_1 10,255; shared_2_5 4,701; shared_6_20 683; shared_gt_20 63; pairs_with_1_to_20_shared 15,639; distinct_other_diseases 3,875 |

**Licensing, (c) p95**: share of edges with each restricted source

| Edge set | Edges | DrugBank | SIDER | DisGeNET | UMLS | Any |
|---|---|---|---|---|---|---|
| single hop facts | 1,729 | 0.0% | 0.0% | 41.1% | 0.0% | 41.1% |
| edges on two hop paths | 44,412 | 10.9% | 6.2% | 7.8% | 0.0% | 24.9% |
| all subgraph edges | 44,451 | 10.8% | 6.2% | 7.8% | 0.0% | 24.9% |

### Broader seed detail: OptimusKG, proposed seed

**Nodes by type**

| Type | (a) 1-hop | (b) 2-hop | (c) p95 |
|---|---|---|---|
| DIS | 79 | 21,970 | 17,521 |
| GEN | 13,786 | 20,912 | 18,287 |
| BPO | 0 | 11,927 | 7,358 |
| DRG | 631 | 11,327 | 4,926 |
| MFN | 0 | 4,457 | 2,944 |
| PHE | 299 | 2,328 | 1,930 |
| PWY | 0 | 2,197 | 1,558 |
| CCO | 0 | 1,790 | 1,329 |
| EXP | 97 | 548 | 511 |
| ANA | 0 | 313 | 309 |

**Edges by relation**, with the relation's sources (top six by edge count in (b))

| Relation | (a) 1-hop | (b) 2-hop | (c) p95 | single-hop facts | Sources |
|---|---|---|---|---|---|
| `DIS-GEN/ASSOCIATED_WITH` | 148,550 | 8,281,987 | 2,509,344 | 36,968 | OPEN_TARGETS, DISGENET, CTD, GENOMICS_ENGLAND, ORPHANET, UNIPROT |
| `ANA-GEN/EXPRESSION_PRESENT` | 0 | 2,734,901 | 1,555,132 | 0 | BGEE |
| `PHE-GEN/ASSOCIATED_WITH` | 46,546 | 722,139 | 209,562 | 0 | OPEN_TARGETS, DISGENET, CTD, GENOMICS_ENGLAND, ORPHANET, UNIPROT |
| `ANA-GEN/EXPRESSION_ABSENT` | 0 | 536,860 | 307,800 | 0 | BGEE |
| `GEN-GEN/INTERACTS_WITH` | 220,409 | 312,270 | 258,105 | 0 | PRIMEKG, INTACT, APID, INWEB, HIUNION, HIPPIE |
| `DRG-DRG/SYNERGISTIC_INTERACTION` | 13,375 | 245,546 | 49,927 | 0 | DRUG_BANK, OPEN_TARGETS |
| `BPO-GEN/INTERACTS_WITH` | 0 | 133,502 | 41,820 | 0 | OPEN_TARGETS, GO_REF:0000107, GO_REF:0000033, GO_REF:0000024, GO_REF:0000043, GO_REF:0000117 |
| `CCO-GEN/INTERACTS_WITH` | 0 | 81,803 | 38,434 | 0 | OPEN_TARGETS, GO_REF:0000043, GO_REF:0000044, GO_REF:0000107, GO_REF:0000033, GO_REF:0000052 |
| `MFN-GEN/INTERACTS_WITH` | 0 | 71,807 | 31,049 | 0 | OPEN_TARGETS, GO_REF:0000043, GO_REF:0000002, GO_REF:0000033, GO_REF:0000107, GO_REF:0000117 |
| `PWY-GEN/INTERACTS_WITH` | 0 | 39,732 | 13,592 | 0 | OPEN_TARGETS, REACTOME |
| `DIS-PHE/PHENOTYPE_PRESENT` | 928 | 38,809 | 19,339 | 928 | HPO, OPEN_TARGETS |
| `DRG-GEN/TARGET` | 1,156 | 13,056 | 2,986 | 0 | DRUG_BANK, DAILY_MED, OPEN_TARGETS, WIKIPEDIA |
| `DRG-DIS/INDICATION` | 853 | 10,389 | 5,528 | 816 | OPEN_TARGETS, CLINICAL_TRIALS, DRUG_CENTRAL, DAILY_MED, ATC, USAN |
| `DRG-GEN/INHIBITOR` | 301 | 5,713 | 762 | 0 | OPEN_TARGETS, PUBMED, OTHER, DAILY_MED, DRUG_BANK, FDA |
| `DRG-GEN/ENZYME` | 803 | 5,124 | 1,138 | 0 | DRUG_BANK |
| `DRG-GEN/TRANSPORTER` | 421 | 2,978 | 1,188 | 0 | DRUG_BANK |
| `EXP-GEN/INTERACTS_WITH` | 1,383 | 2,758 | 1,962 | 0 | CTD, OPEN_TARGETS |
| `DRG-GEN/ANTAGONIST` | 174 | 1,842 | 428 | 0 | OPEN_TARGETS, PUBMED, DRUG_BANK, DAILY_MED, WIKIPEDIA, ISBN |
| `DRG-DIS/CONTRAINDICATION` | 16 | 1,786 | 401 | 4 | DRUG_CENTRAL |
| `DRG-PHE/CONTRAINDICATION` | 102 | 1,689 | 654 | 0 | DRUG_CENTRAL |
| `DRG-GEN/AGONIST` | 168 | 1,640 | 246 | 0 | OPEN_TARGETS, PUBMED, DRUG_BANK, DAILY_MED, FDA, ISBN |
| `DRG-GEN/BLOCKER` | 103 | 1,454 | 284 | 0 | OPEN_TARGETS, PUBMED, DAILY_MED, WIKIPEDIA, FDA, DRUG_BANK |
| `DRG-PHE/ASSOCIATED_WITH` | 274 | 1,337 | 856 | 0 | OPEN_TARGETS, ClinicalTrials, DailyMed, ATC, DRUG_CENTRAL, EMA |
| `EXP-EXP/PARENT` | 168 | 1,089 | 1,089 | 0 | CTD |
| `EXP-DIS/LINKED_TO` | 123 | 1,064 | 1,057 | 116 | CTD, MONDO |
| `PHE-PHE/PARENT` | 136 | 1,038 | 913 | 0 | HPO, OPEN_TARGETS |
| `DRG-GEN/POSITIVE_ALLOSTERIC_MODULATOR` | 76 | 989 | 82 | 0 | OPEN_TARGETS, PUBMED, DRUG_BANK, WIKIPEDIA, ISBN, FDA |
| `DRG-GEN/CARRIER` | 97 | 852 | 111 | 0 | DRUG_BANK |
| `DIS-DIS/PARENT` | 111 | 795 | 147 | 89 | OPEN_TARGETS |
| `EXP-BPO/INTERACTS_WITH` | 0 | 794 | 794 | 0 | CTD, GO |
| `DRG-GEN/MODULATOR` | 14 | 418 | 177 | 0 | OPEN_TARGETS, PUBMED, OTHER, WIKIPEDIA, FDA, EMA |
| `DRG-DRG/PARENT` | 77 | 378 | 206 | 0 | OPEN_TARGETS |
| `DRG-GEN/BINDING_AGENT` | 8 | 255 | 17 | 0 | OPEN_TARGETS, PUBMED, OTHER, FDA, DAILY_MED, PMC |
| `DRG-GEN/POSITIVE_MODULATOR` | 20 | 195 | 20 | 0 | OPEN_TARGETS, PUBMED, DRUG_BANK, ISBN, FDA, WIKIPEDIA |
| `DRG-PHE/INDICATION` | 21 | 171 | 87 | 0 | DRUG_CENTRAL |
| `DRG-GEN/PARTIAL_AGONIST` | 28 | 141 | 46 | 0 | OPEN_TARGETS, PUBMED, WIKIPEDIA, DRUG_BANK, OTHER, FDA |
| `DRG-GEN/STABILISER` | 2 | 132 | 2 | 0 | OPEN_TARGETS, PUBMED, DRUG_BANK, OTHER, FDA, EMA |
| `DRG-GEN/ACTIVATOR` | 7 | 120 | 42 | 0 | OPEN_TARGETS, PUBMED, DAILY_MED, WIKIPEDIA, FDA, OTHER |
| `DRG-DIS/OFF_LABEL_USE` | 2 | 105 | 21 | 2 | DRUG_CENTRAL |
| `DRG-GEN/HYDROLYTIC_ENZYME` | 0 | 71 | 13 | 0 | OPEN_TARGETS, DAILY_MED, PUBMED |
| `DRG-GEN/DISRUPTING_AGENT` | 1 | 67 | 1 | 0 | OPEN_TARGETS, PUBMED, OTHER |
| `DRG-GEN/NEGATIVE_ALLOSTERIC_MODULATOR` | 15 | 64 | 22 | 0 | OPEN_TARGETS, PUBMED, WIKIPEDIA, DAILY_MED, DRUG_BANK, FDA |
| `DRG-GEN/EXOGENOUS_PROTEIN` | 0 | 41 | 0 | 0 | OPEN_TARGETS, EMA, FDA, PUBMED, DAILY_MED, OTHER |
| `DRG-GEN/OPENER` | 7 | 38 | 7 | 0 | OPEN_TARGETS, PUBMED, DRUG_BANK, FDA, WIKIPEDIA, OTHER |
| `DRG-GEN/CROSS_LINKING_AGENT` | 0 | 37 | 1 | 0 | OPEN_TARGETS, PUBMED, OTHER, FDA |
| `DRG-GEN/INVERSE_AGONIST` | 24 | 37 | 24 | 0 | OPEN_TARGETS, PUBMED, WIKIPEDIA, ISBN, DRUG_BANK, DAILY_MED |
| `DRG-PHE/OFF_LABEL_USE` | 3 | 36 | 13 | 0 | DRUG_CENTRAL |
| `DRG-GEN/ANTISENSE_INHIBITOR` | 2 | 36 | 4 | 0 | OPEN_TARGETS, PUBMED, OTHER, CLINICAL_TRIALS, EMA, DAILY_MED |
| `DRG-GEN/OTHER` | 0 | 32 | 2 | 0 | OPEN_TARGETS, PUBMED, OTHER, PMC, WIKIPEDIA |
| `DRG-GEN/RELEASING_AGENT` | 0 | 31 | 0 | 0 | OPEN_TARGETS, PUBMED, WIKIPEDIA, KEGG, DAILY_MED, DRUG_BANK |
| `DRG-GEN/RNAI_INHIBITOR` | 0 | 21 | 2 | 0 | OPEN_TARGETS, PUBMED, FDA, EMA, OTHER, DAILY_MED |
| `EXP-MFN/INTERACTS_WITH` | 0 | 19 | 19 | 0 | CTD, GO |
| `DRG-GEN/ALLOSTERIC_ANTAGONIST` | 16 | 16 | 16 | 0 | DRUG_BANK, FDA, OPEN_TARGETS |
| `DRG-GEN/SUBSTRATE` | 0 | 16 | 2 | 0 | OPEN_TARGETS, PUBMED, ISBN, DAILY_MED, DRUG_BANK, FDA |
| `DRG-BPO/INDICATION` | 0 | 14 | 8 | 0 | CLINICAL_TRIALS, OPEN_TARGETS |
| `DRG-GEN/EXOGENOUS_GENE` | 0 | 11 | 0 | 0 | OPEN_TARGETS, EMA, FDA, CLINICAL_TRIALS, PUBMED |
| `DRG-GEN/VACCINE_ANTIGEN` | 2 | 9 | 2 | 0 | OPEN_TARGETS, PUBMED |
| `DRG-GEN/NEGATIVE_MODULATOR` | 0 | 6 | 0 | 0 | OPEN_TARGETS, PUBMED, DRUG_BANK, PATENT |
| `DRG-GEN/DEGRADER` | 0 | 6 | 0 | 0 | OPEN_TARGETS, PUBMED, DRUG_BANK, FDA, OTHER |
| `EXP-CCO/INTERACTS_WITH` | 0 | 4 | 4 | 0 | CTD, GO |
| `DRG-GEN/PROTEOLYTIC_ENZYME` | 0 | 3 | 0 | 0 | OPEN_TARGETS, PUBMED, DAILY_MED, FDA |

**Degree within the subgraph** (distinct neighbors)

| Definition | min | median | p90 | p99 | max | mean |
|---|---|---|---|---|---|---|
| (a) 1-hop | 100.0% | 29.0 | 99.0 | 383.0 | 13,185.0 | 58.0 |
| (b) 2-hop | 100.0% | 13.0 | 937.0 | 4,673.0 | 16,716.0 | 340.8 |
| (c) p95 | 100.0% | 10.0 | 578.0 | 2,170.0 | 13,185.0 | 178.2 |

**Top 20 hubs, (a) 1-hop** (by degree within the subgraph)

| ID | Name | Type | Degree in subgraph | Degree in full graph | Seed |
|---|---|---|---|---|---|
| `EFO_0005772` | neurodegenerative disease | DIS | 13,185 | 16,716 |  |
| `MONDO_0004975` | Alzheimer disease | DIS | 12,760 | 12,760 | yes |
| `MONDO_0001627` | dementia | DIS | 12,283 | 13,789 |  |
| `MONDO_0100545` | hereditary neurological disease | DIS | 12,215 | 16,111 |  |
| `EFO_0005815` | tauopathy | DIS | 11,615 | 11,627 |  |
| `MONDO_0024237` | inherited neurodegenerative disorder | DIS | 9,740 | 11,702 |  |
| `MONDO_0000426` | autosomal dominant disease | DIS | 8,631 | 10,709 |  |
| `EFO_0004280` | movement disorder | DIS | 7,842 | 8,910 |  |
| `MONDO_0015547` | hereditary dementia | DIS | 7,742 | 9,129 |  |
| `MONDO_0005180` | Parkinson disease | DIS | 6,920 | 6,920 | yes |
| `MONDO_0021095` | parkinsonian disorder | DIS | 6,698 | 6,782 |  |
| `HP_0100543` | Cognitive impairment | PHE | 6,202 | 6,947 |  |
| `MONDO_0000167` | Huntington disease and related disorders | DIS | 5,315 | 5,373 |  |
| `MONDO_0007739` | Huntington disease | DIS | 5,010 | 5,010 | yes |
| `ENSG00000150991` | ubiquitin C | GEN | 4,442 | 7,875 |  |
| `HP_0001249` | Intellectual disability | PHE | 3,089 | 5,031 |  |
| `HP_0001268` | Mental deterioration | PHE | 2,769 | 2,943 |  |
| `HP_0001263` | Global developmental delay | PHE | 2,539 | 4,458 |  |
| `EFO_0003782` | motor neuron disease | DIS | 2,416 | 2,543 |  |
| `HP_0007354` | Amyotrophic lateral sclerosis | PHE | 2,251 | 2,316 |  |

**Top 20 hubs, (b) 2-hop** (by degree within the subgraph)

| ID | Name | Type | Degree in subgraph | Degree in full graph | Seed |
|---|---|---|---|---|---|
| `EFO_0005772` | neurodegenerative disease | DIS | 16,716 | 16,716 |  |
| `MONDO_0100545` | hereditary neurological disease | DIS | 16,111 | 16,111 |  |
| `MONDO_0001627` | dementia | DIS | 13,789 | 13,789 |  |
| `EFO_0000618` | nervous system disease | DIS | 13,632 | 20,468 |  |
| `UBERON_0005386` | olfactory segment of nasal mucosa | ANA | 13,601 | 49,204 |  |
| `UBERON_0001987` | placenta | ANA | 13,596 | 49,941 |  |
| `EFO_0000616` | neoplasm | DIS | 13,593 | 24,478 |  |
| `EFO_0009386` | central nervous system disease | DIS | 13,590 | 19,797 |  |
| `UBERON_0002114` | duodenum | ANA | 13,583 | 46,172 |  |
| `UBERON_0001052` | rectum | ANA | 13,580 | 48,226 |  |
| `UBERON_0002110` | gallbladder | ANA | 13,577 | 48,022 |  |
| `UBERON_0001135` | smooth muscle tissue | ANA | 13,546 | 46,858 |  |
| `UBERON_0001154` | vermiform appendix | ANA | 13,545 | 46,923 |  |
| `UBERON_0001199` | mucosa of stomach | ANA | 13,539 | 44,221 |  |
| `UBERON_0004991` | mucosa of transverse colon | ANA | 13,536 | 43,697 |  |
| `UBERON_0001302` | right fallopian tube | ANA | 13,528 | 44,369 |  |
| `UBERON_0002098` | apex of heart | ANA | 13,528 | 43,755 |  |
| `UBERON_0000468` | multicellular organism | ANA | 13,520 | 48,457 |  |
| `MONDO_0045024` | cancer or benign tumor | DIS | 13,508 | 22,728 |  |
| `MONDO_0023370` | neoplastic disease or syndrome | DIS | 13,505 | 22,685 |  |

**Top 20 hubs, (c) p95** (by degree within the subgraph)

| ID | Name | Type | Degree in subgraph | Degree in full graph | Seed |
|---|---|---|---|---|---|
| `EFO_0005772` | neurodegenerative disease | DIS | 13,185 | 16,716 |  |
| `MONDO_0004975` | Alzheimer disease | DIS | 12,760 | 12,760 | yes |
| `MONDO_0001627` | dementia | DIS | 12,283 | 13,789 |  |
| `MONDO_0100545` | hereditary neurological disease | DIS | 12,215 | 16,111 |  |
| `EFO_0005815` | tauopathy | DIS | 11,615 | 11,627 |  |
| `MONDO_0024237` | inherited neurodegenerative disorder | DIS | 9,740 | 11,702 |  |
| `MONDO_0000426` | autosomal dominant disease | DIS | 8,631 | 10,709 |  |
| `EFO_0000618` | nervous system disease | DIS | 8,056 | 20,468 |  |
| `UBERON_0001987` | placenta | ANA | 8,042 | 49,941 |  |
| `UBERON_0005386` | olfactory segment of nasal mucosa | ANA | 8,040 | 49,204 |  |
| `UBERON_0002114` | duodenum | ANA | 8,027 | 46,172 |  |
| `UBERON_0001052` | rectum | ANA | 8,021 | 48,226 |  |
| `UBERON_0002110` | gallbladder | ANA | 8,018 | 48,022 |  |
| `EFO_0009386` | central nervous system disease | DIS | 8,015 | 19,797 |  |
| `UBERON_0001135` | smooth muscle tissue | ANA | 8,003 | 46,858 |  |
| `UBERON_0001154` | vermiform appendix | ANA | 7,999 | 46,923 |  |
| `UBERON_0001295` | endometrium | ANA | 7,994 | 53,523 |  |
| `UBERON_0001199` | mucosa of stomach | ANA | 7,982 | 44,221 |  |
| `EFO_0000616` | neoplasm | DIS | 7,978 | 24,478 |  |
| `UBERON_0004991` | mucosa of transverse colon | ANA | 7,977 | 43,697 |  |

**Top 10 two-hop intermediates, (a) 1-hop**

| ID | Name | Type | Paths | Path share | Endpoint share |
|---|---|---|---|---|---|
| `ENSG00000150991` | ubiquitin C | GEN | 84,037 | 3.0% | 30.5% |
| `HP_0007354` | Amyotrophic lateral sclerosis | PHE | 33,540 | 1.2% | 15.4% |
| `MONDO_0015547` | hereditary dementia | DIS | 30,952 | 1.1% | 53.3% |
| `MONDO_0024237` | inherited neurodegenerative disorder | DIS | 29,211 | 1.1% | 67.1% |
| `HP_0001300` | Parkinsonism | PHE | 27,930 | 1.0% | 12.8% |
| `HP_0100543` | Cognitive impairment | PHE | 24,792 | 0.9% | 42.7% |
| `EFO_0004280` | movement disorder | DIS | 23,517 | 0.8% | 54.0% |
| `EFO_0005815` | tauopathy | DIS | 23,226 | 0.8% | 80.1% |
| `ENSG00000142192` | amyloid beta precursor protein | GEN | 17,640 | 0.6% | 5.8% |
| `MONDO_0000426` | autosomal dominant disease | DIS | 17,258 | 0.6% | 59.5% |

**Top 10 two-hop intermediates, (b) 2-hop**

| ID | Name | Type | Paths | Path share | Endpoint share |
|---|---|---|---|---|---|
| `ENSG00000150991` | ubiquitin C | GEN | 149,264 | 0.3% | 10.2% |
| `ENSG00000232810` | tumor necrosis factor | GEN | 128,541 | 0.3% | 7.9% |
| `ENSG00000186868` | microtubule associated protein tau | GEN | 112,360 | 0.2% | 3.6% |
| `ENSG00000145335` | synuclein alpha | GEN | 104,875 | 0.2% | 5.4% |
| `ENSG00000142168` | superoxide dismutase 1 | GEN | 88,205 | 0.2% | 5.0% |
| `ENSG00000196876` | sodium voltage-gated channel alpha subunit 8 | GEN | 86,130 | 0.2% | 3.4% |
| `ENSG00000197746` | prosaposin | GEN | 84,024 | 0.2% | 4.0% |
| `ENSG00000112715` | vascular endothelial growth factor A | GEN | 82,464 | 0.2% | 6.7% |
| `ENSG00000142192` | amyloid beta precursor protein | GEN | 79,527 | 0.2% | 4.9% |
| `ENSG00000130203` | apolipoprotein E | GEN | 76,820 | 0.2% | 5.0% |

**Top 10 two-hop intermediates, (c) p95**

| ID | Name | Type | Paths | Path share | Endpoint share |
|---|---|---|---|---|---|
| `ENSG00000150991` | ubiquitin C | GEN | 84,037 | 0.7% | 7.9% |
| `HP_0007354` | Amyotrophic lateral sclerosis | PHE | 33,540 | 0.3% | 4.0% |
| `MONDO_0015547` | hereditary dementia | DIS | 30,952 | 0.3% | 13.7% |
| `MONDO_0024237` | inherited neurodegenerative disorder | DIS | 29,211 | 0.3% | 17.3% |
| `HP_0001300` | Parkinsonism | PHE | 27,930 | 0.2% | 3.3% |
| `HP_0100543` | Cognitive impairment | PHE | 24,792 | 0.2% | 11.0% |
| `EFO_0004280` | movement disorder | DIS | 23,517 | 0.2% | 13.9% |
| `EFO_0005815` | tauopathy | DIS | 23,226 | 0.2% | 20.6% |
| `ENSG00000142192` | amyloid beta precursor protein | GEN | 17,640 | 0.2% | 1.5% |
| `ENSG00000198862` | listerin E3 ubiquitin protein ligase 1 | GEN | 17,490 | 0.2% | 1.4% |

**Candidate breakdowns, (c) p95**

| Breakdown | Counts |
|---|---|
| Single-hop facts by relation | `DIS-GEN/ASSOCIATED_WITH` 36,968; `DIS-PHE/PHENOTYPE_PRESENT` 928; `DRG-DIS/INDICATION` 816; `EXP-DIS/LINKED_TO` 116; `DIS-DIS/PARENT` 89; `DRG-DIS/CONTRAINDICATION` 4; `DRG-DIS/OFF_LABEL_USE` 2 |
| Top two-hop patterns | `DIS-GEN/ASSOCIATED_WITH | DIS-GEN/ASSOCIATED_WITH` 5,064,623; `DIS-GEN/ASSOCIATED_WITH | ANA-GEN/EXPRESSION_PRESENT` 2,806,675; `DIS-GEN/ASSOCIATED_WITH | GEN-GEN/INTERACTS_WITH` 1,674,025; `DIS-GEN/ASSOCIATED_WITH | PHE-GEN/ASSOCIATED_WITH` 579,188; `DIS-GEN/ASSOCIATED_WITH | ANA-GEN/EXPRESSION_ABSENT` 549,510; `DIS-PHE/PHENOTYPE_PRESENT | PHE-GEN/ASSOCIATED_WITH` 227,960; `DIS-DIS/PARENT | DIS-GEN/ASSOCIATED_WITH` 198,442; `DIS-PHE/PHENOTYPE_PRESENT | DIS-PHE/PHENOTYPE_PRESENT` 91,349 |
| Unanswerable candidates by relation (distinct y) | `DIS-GEN/ASSOCIATED_WITH` 18,287; `DIS-DIS/PARENT` 17,468; `DRG-DIS/INDICATION` 4,926; `DRG-DIS/CONTRAINDICATION` 4,921; `DRG-DIS/OFF_LABEL_USE` 4,912; `DIS-PHE/PHENOTYPE_PRESENT` 1,930; `EXP-DIS/LINKED_TO` 508 |
| Contradicted pairs by relation | `ANA-GEN/EXPRESSION_PRESENT` 1,555,132; `ANA-GEN/EXPRESSION_ABSENT` 307,800; `DRG-DIS/INDICATION` 5,528; `DRG-PHE/CONTRAINDICATION` 654; `DRG-DIS/CONTRAINDICATION` 401; `DRG-PHE/INDICATION` 87 |
| Seed fan-out (seed, relation): edges | MONDO_0004975 | DIS-GEN/ASSOCIATED_WITH: 12,414; MONDO_0005180 | DIS-GEN/ASSOCIATED_WITH: 6,584; MONDO_0007739 | DIS-GEN/ASSOCIATED_WITH: 4,935; EFO_0003096 | DIS-GEN/ASSOCIATED_WITH: 2,142; MONDO_0017276 | DIS-GEN/ASSOCIATED_WITH: 1,668; MONDO_0004976 | DIS-GEN/ASSOCIATED_WITH: 1,484; EFO_0006792 | DIS-GEN/ASSOCIATED_WITH: 1,258; EFO_0001356 | DIS-GEN/ASSOCIATED_WITH: 740; MONDO_0017160 | DIS-GEN/ASSOCIATED_WITH: 412; MONDO_0100087 | DIS-GEN/ASSOCIATED_WITH: 405; Orphanet_2828 | DIS-GEN/ASSOCIATED_WITH: 352; MONDO_0004975 | DRG-DIS/INDICATION: 314 |
| Shared-neighbor disease pairs by shared count | pairs 220,142; shared_1 88,082; shared_2_5 73,387; shared_6_20 34,849; shared_gt_20 23,824; pairs_with_1_to_20_shared 196,318; distinct_other_diseases 17,468 |

**Licensing, (c) p95**: share of edges with each restricted source

| Edge set | Edges | DrugBank | SIDER | DisGeNET | UMLS | Any |
|---|---|---|---|---|---|---|
| single hop facts | 38,923 | 0.0% | 0.0% | 1.0% | 0.0% | 1.0% |
| edges on two hop paths | 5,050,459 | 1.1% | 0.0% | 0.1% | 0.0% | 1.2% |
| all subgraph edges | 5,055,488 | 1.1% | 0.1% | 0.1% | 0.0% | 1.2% |

### OptimusKG score sensitivity (reported only, not gating)

**Reported only, not gating.** The pass/fail table above uses the test as
written, with no score filter.

**What the score is.** In OptimusKG, two relations carry a score:
`DIS-GEN/ASSOCIATED_WITH` and `PHE-GEN/ASSOCIATED_WITH`. Every edge of both
has `properties.evidence_score`, documented as the "Aggregated association
evidence score" (OptimusKG docs, `docs/content/docs/graph-schema/edges.mdx`).
OptimusKG's pipeline fills it from the Open Targets Platform 25.06 dataset
`association_by_overall_indirect` (field `score`;
`conf/base/catalog/landing/opentargets/target_disease_associations.yml` and
`optimuskg/pipelines/silver/nodes/edges/disease_gene.py`, repo commit
`e07f048`).
- That is Open Targets' overall association score, indirect: evidence for a
  disease's descendants counts toward the disease itself. This is why broad
  disease nodes such as Alzheimer disease carry thousands of low-scoring
  associations.
- 25,378 DIS-GEN and 1,317 PHE-GEN edges also carry a DisGeNET
  `disgenet_score`. It was not used.
- Other OptimusKG properties are not association-confidence scores, and none
  was filtered on: Bgee's `expression_rank` on ANA-GEN, and DrugCentral's
  `highest_clinical_trial_phase` on drug–disease edges.
- PrimeKG has no edge scores.

**How the filter works.** The filter drops scored edges below the cutoff from
the graph the subgraphs are cut from, so N1, the facts and the paths all
shrink. Two things stay on the unfiltered graph:
- the hub-pruning degree caps, so (c) prunes the same hubs;
- every absence check, so a pair joined only by a low-scoring edge is never
  counted as unanswerable.

Scored edges kept in the whole graph: ≥ 0.1: `DIS-GEN/ASSOCIATED_WITH` 1,842,190 of 9,734,774, `PHE-GEN/ASSOCIATED_WITH` 89,572 of 793,279; ≥ 0.5: `DIS-GEN/ASSOCIATED_WITH` 215,943 of 9,734,774, `PHE-GEN/ASSOCIATED_WITH` 8,326 of 793,279.

**Alzheimer's-only, proposed seed** (reported only, not gating)

| Definition | Cutoff | Nodes | Edges | C1 facts | C2 rels ≥ 30 / balanced | C3 paths / endpoints | C4 patterns / balanced | C5 top-1 / top-5 | C6 | C7 pairs / rels ≥ 20 | C8 total / contradicted | Gating criteria met (reported only) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| (a) 1-hop | none | 12,781 | 269,384 | 13,753 | 3 / 287 | 591,483 / 12,607 | 37 / 2,654 | 3.0% / 10.7% | 1,008 | 12,440 / 2 | 12,591 / 331 | 6 of 8 (fails C2, C7) |
| (a) 1-hop | ≥ 0.1 | 1,988 | 19,046 | 2,151 | 3 / 287 | 35,598 / 1,891 | 31 / 2,129 | 4.6% / 14.1% | 199 | 1,647 / 2 | 1,798 / 331 | 6 of 8 (fails C2, C7) |
| (a) 1-hop | ≥ 0.5 | 443 | 3,956 | 476 | 3 / 270 | 7,242 / 286 | 16 / 1,079 | 2.2% / 8.2% | 69 | 104 / 2 | 331 / 331 | 5 of 8 (fails C2, C6, C7) |
| (b) 2-hop | none | 75,461 | 12,300,795 | 13,753 | 3 / 287 | 14,242,617 / 75,287 | 70 / 5,511 | 0.2% / 0.9% | 13,747 | 65,452 / 6 | 3,040,585 / 2,975,313 | 7 of 8 (fails C2) |
| (b) 2-hop | ≥ 0.1 | 47,790 | 1,017,379 | 2,151 | 3 / 287 | 1,121,642 / 47,693 | 60 / 4,943 | 0.7% / 2.8% | 2,145 | 43,290 / 6 | 451,511 / 408,401 | 7 of 8 (fails C2) |
| (b) 2-hop | ≥ 0.5 | 19,439 | 177,202 | 476 | 3 / 270 | 198,862 / 19,282 | 48 / 3,961 | 4.2% / 11.3% | 470 | 20,558 / 6 | 47,924 / 27,546 | 7 of 8 (fails C2) |
| (c) p90 | none | 32,648 | 1,062,429 | 13,753 | 3 / 287 | 1,401,506 / 32,474 | 50 / 3,987 | 1.3% / 4.5% | 13,747 | 29,957 / 6 | 464,429 / 434,652 | 7 of 8 (fails C2) |
| (c) p90 | ≥ 0.1 | 11,043 | 76,708 | 2,151 | 3 / 287 | 98,710 / 10,946 | 46 / 3,494 | 1.6% / 5.6% | 2,145 | 11,563 / 6 | 48,411 / 37,028 | 7 of 8 (fails C2) |
| (c) p90 | ≥ 0.5 | 5,652 | 13,771 | 476 | 3 / 270 | 17,346 / 5,495 | 28 / 2,179 | 2.5% / 11.7% | 470 | 7,197 / 6 | 10,063 / 3,046 | 7 of 8 (fails C2) |
| (c) p95 | none | 52,814 | 4,278,211 | 13,753 | 3 / 287 | 4,739,615 / 52,640 | 51 / 4,218 | 0.4% / 1.3% | 13,747 | 43,660 / 6 | 1,641,306 / 1,597,826 | 7 of 8 (fails C2) |
| (c) p95 | ≥ 0.1 | 22,447 | 310,363 | 2,151 | 3 / 287 | 346,664 / 22,350 | 49 / 3,738 | 0.5% / 1.9% | 2,145 | 21,378 / 6 | 191,587 / 170,389 | 7 of 8 (fails C2) |
| (c) p95 | ≥ 0.5 | 8,990 | 38,889 | 476 | 3 / 270 | 43,010 / 8,833 | 37 / 2,905 | 2.1% / 10.2% | 470 | 10,948 / 6 | 18,569 / 7,801 | 7 of 8 (fails C2) |
| (c) p99 | none | 70,621 | 10,777,329 | 13,753 | 3 / 287 | 12,043,622 / 70,447 | 64 / 5,230 | 0.1% / 0.6% | 13,747 | 59,469 / 6 | 2,897,731 / 2,838,442 | 7 of 8 (fails C2) |
| (c) p99 | ≥ 0.1 | 38,225 | 807,768 | 2,151 | 3 / 287 | 878,171 / 38,128 | 56 / 4,647 | 0.5% / 1.5% | 2,145 | 34,137 / 6 | 386,226 / 352,269 | 7 of 8 (fails C2) |
| (c) p99 | ≥ 0.5 | 14,157 | 160,099 | 476 | 3 / 270 | 168,925 / 14,000 | 43 / 3,687 | 1.5% / 6.5% | 470 | 15,642 / 6 | 39,299 / 23,837 | 7 of 8 (fails C2) |

**Broader neurodegeneration, proposed seed** (reported only, not gating)

| Definition | Cutoff | Nodes | Edges | C1 facts | C2 rels ≥ 30 / balanced | C3 paths / endpoints | C4 patterns / balanced | C5 top-1 / top-5 | C6 | C7 pairs / rels ≥ 20 | C8 total / contradicted | Gating criteria met (reported only) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| (a) 1-hop | none | 14,892 | 436,522 | 38,923 | 5 / 527 | 2,779,176 / 14,506 | 58 / 4,711 | 3.0% / 7.4% | 38,875 | 16,079 / 7 | 16,891 / 992 | 8 of 8 |
| (a) 1-hop | ≥ 0.1 | 3,269 | 47,312 | 5,626 | 5 / 527 | 150,182 / 2,964 | 52 / 4,173 | 2.2% / 8.3% | 5,578 | 4,456 / 7 | 5,268 / 992 | 8 of 8 |
| (a) 1-hop | ≥ 0.5 | 1,272 | 18,425 | 2,300 | 5 / 527 | 53,141 / 876 | 38 / 3,076 | 2.6% / 8.0% | 2,252 | 2,459 / 7 | 3,271 / 992 | 8 of 8 |
| (b) 2-hop | none | 77,769 | 13,258,273 | 38,923 | 5 / 527 | 48,158,298 / 77,383 | 87 / 7,328 | 0.3% / 1.2% | 38,875 | 79,664 / 7 | 3,365,280 / 3,285,796 | 8 of 8 |
| (b) 2-hop | ≥ 0.1 | 55,262 | 1,466,717 | 5,626 | 5 / 527 | 2,775,764 / 54,957 | 77 / 6,323 | 2.6% / 5.1% | 5,578 | 58,959 / 7 | 605,938 / 547,159 | 8 of 8 |
| (b) 2-hop | ≥ 0.5 | 29,704 | 394,459 | 2,300 | 5 / 527 | 857,641 / 29,308 | 67 / 5,462 | 8.4% / 15.4% | 2,252 | 35,819 / 7 | 92,292 / 56,653 | 8 of 8 |
| (c) p90 | none | 38,574 | 1,425,222 | 38,923 | 5 / 527 | 4,203,772 / 38,188 | 71 / 5,781 | 2.0% / 4.9% | 38,875 | 39,897 / 7 | 579,585 / 539,868 | 8 of 8 |
| (c) p90 | ≥ 0.1 | 17,745 | 145,194 | 5,626 | 5 / 527 | 325,828 / 17,440 | 65 / 5,279 | 1.8% / 6.5% | 5,578 | 22,461 / 7 | 76,829 / 54,548 | 8 of 8 |
| (c) p90 | ≥ 0.5 | 11,156 | 47,771 | 2,300 | 5 / 527 | 130,124 / 10,760 | 51 / 4,048 | 4.3% / 15.5% | 2,252 | 16,960 / 7 | 22,307 / 5,527 | 8 of 8 |
| (c) p95 | none | 56,673 | 5,055,488 | 38,923 | 5 / 527 | 11,589,681 / 56,287 | 73 / 5,932 | 0.7% / 1.8% | 38,875 | 52,952 / 7 | 1,922,374 / 1,869,602 | 8 of 8 |
| (c) p95 | ≥ 0.1 | 30,291 | 476,248 | 5,626 | 5 / 527 | 885,175 / 29,986 | 65 / 5,398 | 1.2% / 3.9% | 5,578 | 33,780 / 7 | 272,401 / 238,801 | 8 of 8 |
| (c) p95 | ≥ 0.5 | 14,912 | 96,087 | 2,300 | 5 / 527 | 235,842 / 14,516 | 56 / 4,588 | 4.2% / 14.3% | 2,252 | 21,819 / 7 | 35,852 / 14,213 | 8 of 8 |
| (c) p99 | none | 72,878 | 11,733,113 | 38,923 | 5 / 527 | 37,513,473 / 72,492 | 85 / 7,006 | 0.2% / 0.8% | 38,875 | 71,635 / 7 | 3,220,059 / 3,148,604 | 8 of 8 |
| (c) p99 | ≥ 0.1 | 46,528 | 1,222,815 | 5,626 | 5 / 527 | 2,215,653 / 46,223 | 76 / 6,113 | 0.5% / 2.2% | 5,578 | 48,880 / 7 | 535,727 / 487,027 | 8 of 8 |
| (c) p99 | ≥ 0.5 | 23,779 | 353,687 | 2,300 | 5 / 527 | 673,205 / 23,383 | 67 / 5,317 | 1.5% / 5.7% | 2,252 | 30,337 / 7 | 79,962 / 49,805 | 8 of 8 |

### What this means for the domain and graph choice

These are facts only, with no recommendation.

**Outcome.**
- **Broader seed:** passes every gating criterion in both graphs, under every
  definition ((a), (b), and (c) at p90, p95 and p99), for the proposed seed
  and for the main nodes alone.
- **Alzheimer's-only:** unchanged. It fails C2 in both graphs under every
  definition.
- The test is the one committed above, with every threshold still proposed.

**Where C2 moves.** C2 needs at least 4 relations with 30 or more single-hop
facts, and a balanced capacity of at least 270.

| | PrimeKG | OptimusKG |
|---|---|---|
| Alzheimer's-only, proposed seed | 3 relations, 267 | 3 relations, 287 |
| Broader, proposed seed | 6 relations, 610 | 5 relations, 527 |
| Broader, main nodes only | 6 relations, 518 | 5 relations, 467 |

- **PrimeKG's six relations:** `disease_protein` 711, `disease_phenotype_positive` 618, `exposure_disease` 114, `contraindication` 107, `indication` 100, `disease_disease` 69.
- **OptimusKG's five:** `DIS-GEN/ASSOCIATED_WITH` 36,968, `DIS-PHE/PHENOTYPE_PRESENT` 928, `DRG-DIS/INDICATION` 816, `EXP-DIS/LINKED_TO` 116, `DIS-DIS/PARENT` 89.
- **A hierarchy relation in each list.** `disease_disease` and `DIS-DIS/PARENT` are parent-child relations, where direction-insensitive matching can accept a reversed claim (see "Directed-relation inventory"). Without them, PrimeKG has 5 relations at 30 or more and OptimusKG has 4.

**Per disease (reported).**
- **No shared facts.** No two diseases share a seed node or a single-hop
  fact. They overlap only through shared partners. The Jaccard overlap of
  (partner, relation) pairs reaches 11.8% in PrimeKG and 43.3% in OptimusKG,
  both for Alzheimer's and Parkinson disease.
- **Any five of the six still meet both C2 thresholds**, in both graphs.
  Parkinson disease is the only disease whose removal lowers the number of
  relations with 30 or more facts (PrimeKG, 6 to 5).
- **Alzheimer's plus one other disease, C2 inputs only:**

  | Graph | Meets both C2 thresholds | Has 3 relations with ≥ 30 facts |
  |---|---|---|
  | PrimeKG | PD (6 relations, 501), ALS (4, 392) | FTD, HD, DLB |
  | OptimusKG | PD (5, 414), FTD (4, 367), ALS (5, 441) | HD, DLB |

  No other criterion was recomputed for these pairs.
- **Size per disease, in single-hop facts:**

  | Graph | PD | ALS | AD | FTD | HD | DLB |
  |---|---|---|---|---|---|---|
  | PrimeKG | 728 | 354 | 364 | 129 | 118 | 36 |
  | OptimusKG | 8,878 | 4,487 | 13,753 | 5,359 | 5,165 | 1,281 |

  In OptimusKG, most of every disease's facts are Open Targets gene
  associations.

**Seed questions the rule leaves open** (the candidate tables give the
details).

*PrimeKG.* The name filter excludes some neighbors that both file order and
MONDO place below the main node, and keeps one whose role is unclear:
- Parkinson disease: excludes parkinsonian-pyramidal syndrome (`30093`,
  degree 141) and Kufor-Rakeb syndrome (`29670`, degree 73). It keeps
  "hereditary late onset Parkinson disease" (`33241`), which file order gives
  as a parent of the grouped Parkinson disease node.
- Frontotemporal dementia: excludes Pick disease (`28473`, degree 155) and
  GRN-related frontotemporal lobar degeneration (`28467`). The main node
  itself has degree 15.
- The combined "frontotemporal dementia and/or amyotrophic lateral sclerosis"
  node (`28708`) is not a neighbor of either main node, so it is in neither
  seed.

*OptimusKG.* The descendant rule includes:
- for ALS: spinocerebellar ataxia type 2 (degree 350);
- for Parkinson disease: secondary and postencephalitic Parkinson disease;
- for FTD: Pick disease, semantic dementia and progressive non-fluent aphasia.

It excludes "frontotemporal dementia and/or amyotrophic lateral sclerosis"
(`MONDO_0030923`) and "frontotemporal dementia with motor neuron disease"
(`MONDO_0017161`), which descend from neither main node. Numbered FTD/ALS
types 1, 4 and 6 descend from ALS only.

**Size.** The broader seed's subgraphs:

| Definition | PrimeKG nodes | PrimeKG edges | OptimusKG nodes | OptimusKG edges |
|---|---|---|---|---|
| (a) | 952 | 9,406 | 14,892 | 436,522 |
| (b) | 29,351 | 266,675 | 77,769 | 13,258,273 |
| (c) p95 | 11,579 | 44,451 | 56,673 | 5,055,488 |

For comparison, the Alzheimer's-only proposed seed at (c) p95 was 4,157
nodes and 9,790 edges in PrimeKG, and 52,814 nodes and 4,278,211 edges in
OptimusKG.

**Licensing (R3, single-hop facts from a restricted source).**

| | PrimeKG | OptimusKG |
|---|---|---|
| Alzheimer's-only, proposed seed | 56.3% | 0.9% |
| Broader, proposed seed | 41.1% | 1.0% |

- PrimeKG's broader count is all DisGeNET (`disease_protein`), by the
  relation-level mapping.
- Separately, 53.3% of PrimeKG's broader single-hop facts had their disease
  end mapped through the UMLS crosswalk.

**Tool caps.** Single-hop facts in a (seed node, relation) group of 20 edges
or fewer, the per-result cap:

| | PrimeKG | OptimusKG |
|---|---|---|
| Broader, proposed seed | 252 of 1,729 | 457 of 38,923 |
| Largest group | ALS `disease_protein`, 108 | Alzheimer's `DIS-GEN/ASSOCIATED_WITH`, 12,414 |

**OptimusKG score sensitivity (reported only).** An `evidence_score` cutoff
cuts the broader seed's single-hop facts:

| Cutoff | Broader seed: single-hop facts | Broader seed: criteria met | Alzheimer's-only: C2 |
|---|---|---|---|
| none | 38,923 | all eight, every definition | fails |
| ≥ 0.1 | 5,626 | all eight, every definition | fails |
| ≥ 0.5 | 2,300 | all eight, every definition | fails |

The Alzheimer's-only seed also fails C7 under (a) at every cutoff, as it does
unfiltered, and fails C6 under (a) at ≥ 0.5.

## Directed-relation inventory

Checked on 2026-11-19. The shared contracts
(`neuro_grounded_qa.contracts.facts`) match a claim triple to an edge in
either order. That is safe for a relation only if the reversed triple can
never be a different fact. This section records, per relation and for both
graphs, what decides that. It proposes no contract change; the decision is
raised separately.

For each relation:

- **Source marks direction.** OptimusKG flags each edge `undirected` or not.
  PrimeKG's `kg.csv` stores every edge twice, once per orientation, and flags
  nothing.
- **Meaning.** If the two ends have different types, the types fix the
  reading. "Gene X targets drug Y" can only mean the drug-targets-gene fact,
  so matching in either order cannot support a wrong claim. Between two nodes
  of the same type, a relation is either symmetric (protein–protein
  interaction, drug–drug synergy) or directional (parent-child, is_a).
- **Ignoring direction could wrongly support a claim** only for the
  same-type, directional relations. For those, "A is a subtype of B" and "B
  is a subtype of A" are both well-formed claims between same-typed entities,
  and at most one is true.

Produced by `scripts/data/direction.py` (relation level) and
`scripts/data/measure.py` (counts per subgraph).

Findings:

- **Which relations are at risk.** The at-risk relations are the
  hierarchies:
  - PrimeKG: `disease_disease`, `phenotype_phenotype`, `anatomy_anatomy`, the
    three GO hierarchies, `pathway_pathway` and `exposure_exposure`;
  - OptimusKG: every `PARENT` and `IS_A` relation.

  Neither graph's symmetric relations are at risk.
- **Mixed and mislabelled relations.**
  - OptimusKG's `DIS-DIS/PARENT` also holds 142 phenotype → disease edges,
    whose types fix their reading.
  - OptimusKG flags `DRG-DRG/SYNERGISTIC_INTERACTION` as directed, although a
    synergy has no direction. It never stores both orientations of a pair.
  - OptimusKG flags `PHE-GEN/ASSOCIATED_WITH` as directed but
    `DIS-GEN/ASSOCIATED_WITH` as undirected; both join different types.
  - `EXP-EXP/PARENT` and PrimeKG's `exposure_exposure` are CTD exposure links
    labelled parent-child. They mix class membership (alachlor → Herbicides)
    with stressor → marker links (alachlor → Thyroxine), so they are
    directional but not a clean hierarchy.
- **Conventions differ by relation.** In OptimusKG:
  - `PARENT` edges run parent → child;
  - `IS_A` edges (the GO hierarchies) run child → parent.
- **PrimeKG's file order recovers direction.** The build appends each edge's
  reversed copy after the original, so the first copy of a pair in file order
  keeps the source ontology's orientation.
  - Against OptimusKG's edges between the same ontology IDs, the first copy
    agrees in at least 99.97% of checkable pairs, for all seven hierarchies
    with a counterpart. The most disagreements are 4 of 40,062 GO
    biological-process pairs.
  - It reads parent → child for every hierarchy except `anatomy_anatomy`,
    which reads child → parent, matching its build cell (`x = id`,
    `y = is_a`).
  - This is a property of the file layout, not a documented field.
- **In the broader subgraph** (proposed seed), the at-risk relations that
  touch the seed directly are:
  - PrimeKG `disease_disease`: 69 single-hop facts;
  - OptimusKG `DIS-DIS/PARENT`: 89 single-hop facts.

  For every one of them, the reversed reading is a different fact. No pair is
  stored in both directions. Both relations count toward C2 in the broader
  results. The other at-risk relations appear only on second hops (table
  below).

**Relation inventory, PrimeKG**

| Relation | Edges (canonical) | Endpoint types | Source marks direction | Meaning | Ignoring direction could wrongly support a claim |
|---|---|---|---|---|---|
| `anatomy_anatomy` | 14,032 | anatomy / anatomy | both orientations stored (no flag) | same type, hierarchy (parent-child / is_a) | yes |
| `anatomy_protein_absent` | 19,887 | anatomy / gene/protein; gene/protein / anatomy | both orientations stored (no flag) | between different types: the types fix the reading | no |
| `anatomy_protein_present` | 1,518,203 | anatomy / gene/protein; gene/protein / anatomy | both orientations stored (no flag) | between different types: the types fix the reading | no |
| `bioprocess_bioprocess` | 52,886 | biological_process / biological_process | both orientations stored (no flag) | same type, hierarchy (parent-child / is_a) | yes |
| `bioprocess_protein` | 144,805 | biological_process / gene/protein; gene/protein / biological_process | both orientations stored (no flag) | between different types: the types fix the reading | no |
| `cellcomp_cellcomp` | 4,845 | cellular_component / cellular_component | both orientations stored (no flag) | same type, hierarchy (parent-child / is_a) | yes |
| `cellcomp_protein` | 83,402 | cellular_component / gene/protein; gene/protein / cellular_component | both orientations stored (no flag) | between different types: the types fix the reading | no |
| `contraindication` | 30,675 | drug / disease | both orientations stored (no flag) | between different types: the types fix the reading | no |
| `disease_disease` | 32,194 | disease / disease | both orientations stored (no flag) | same type, hierarchy (parent-child / is_a) | yes |
| `disease_phenotype_negative` | 1,193 | disease / effect/phenotype; effect/phenotype / disease | both orientations stored (no flag) | between different types: the types fix the reading | no |
| `disease_phenotype_positive` | 150,317 | disease / effect/phenotype; effect/phenotype / disease | both orientations stored (no flag) | between different types: the types fix the reading | no |
| `disease_protein` | 80,411 | disease / gene/protein; gene/protein / disease | both orientations stored (no flag) | between different types: the types fix the reading | no |
| `drug_drug` | 1,336,314 | drug / drug | both orientations stored (no flag) | same type, symmetric | no |
| `drug_effect` | 64,784 | drug / effect/phenotype; effect/phenotype / drug | both orientations stored (no flag) | between different types: the types fix the reading | no |
| `drug_protein/carrier` | 864 | drug / gene/protein; gene/protein / drug | both orientations stored (no flag) | between different types: the types fix the reading | no |
| `drug_protein/enzyme` | 5,317 | drug / gene/protein; gene/protein / drug | both orientations stored (no flag) | between different types: the types fix the reading | no |
| `drug_protein/target` | 16,380 | drug / gene/protein; gene/protein / drug | both orientations stored (no flag) | between different types: the types fix the reading | no |
| `drug_protein/transporter` | 3,092 | drug / gene/protein; gene/protein / drug | both orientations stored (no flag) | between different types: the types fix the reading | no |
| `exposure_bioprocess` | 1,625 | biological_process / exposure | both orientations stored (no flag) | between different types: the types fix the reading | no |
| `exposure_cellcomp` | 10 | cellular_component / exposure | both orientations stored (no flag) | between different types: the types fix the reading | no |
| `exposure_disease` | 2,304 | disease / exposure; exposure / disease | both orientations stored (no flag) | between different types: the types fix the reading | no |
| `exposure_exposure` | 2,070 | exposure / exposure | both orientations stored (no flag) | same type, directional (CTD exposure links labelled parent-child) | yes |
| `exposure_molfunc` | 45 | molecular_function / exposure | both orientations stored (no flag) | between different types: the types fix the reading | no |
| `exposure_protein` | 1,212 | exposure / gene/protein; gene/protein / exposure | both orientations stored (no flag) | between different types: the types fix the reading | no |
| `indication` | 9,388 | drug / disease | both orientations stored (no flag) | between different types: the types fix the reading | no |
| `molfunc_molfunc` | 13,574 | molecular_function / molecular_function | both orientations stored (no flag) | same type, hierarchy (parent-child / is_a) | yes |
| `molfunc_protein` | 69,530 | gene/protein / molecular_function; molecular_function / gene/protein | both orientations stored (no flag) | between different types: the types fix the reading | no |
| `off-label use` | 2,568 | drug / disease | both orientations stored (no flag) | between different types: the types fix the reading | no |
| `pathway_pathway` | 2,535 | pathway / pathway | both orientations stored (no flag) | same type, hierarchy (parent-child / is_a) | yes |
| `pathway_protein` | 42,646 | gene/protein / pathway; pathway / gene/protein | both orientations stored (no flag) | between different types: the types fix the reading | no |
| `phenotype_phenotype` | 18,736 | effect/phenotype / effect/phenotype | both orientations stored (no flag) | same type, hierarchy (parent-child / is_a) | yes |
| `phenotype_protein` | 3,330 | effect/phenotype / gene/protein; gene/protein / effect/phenotype | both orientations stored (no flag) | between different types: the types fix the reading | no |
| `protein_protein` | 321,075 | gene/protein / gene/protein | both orientations stored (no flag) | same type, symmetric | no |

**Relation inventory, OptimusKG**

| Relation | Edges (canonical) | Endpoint types | Source marks direction | Meaning | Ignoring direction could wrongly support a claim |
|---|---|---|---|---|---|
| `ANA-ANA/PARENT` | 17,082 | ANA / ANA | directed | same type, hierarchy (parent-child / is_a) | yes |
| `ANA-GEN/* (2: EXPRESSION_ABSENT, EXPRESSION_PRESENT)` | 8,787,955 | GEN / ANA | undirected | between different types: the types fix the reading | no |
| `BPO-BPO/IS_A` | 44,494 | BPO / BPO | directed | same type, hierarchy (parent-child / is_a) | yes |
| `BPO-GEN/INTERACTS_WITH` | 158,410 | GEN / BPO | undirected | between different types: the types fix the reading | no |
| `CCO-CCO/IS_A` | 4,639 | CCO / CCO | directed | same type, hierarchy (parent-child / is_a) | yes |
| `CCO-GEN/INTERACTS_WITH` | 105,309 | GEN / CCO | undirected | between different types: the types fix the reading | no |
| `DIS-DIS/PARENT` | 47,892 | DIS / DIS; PHE / DIS | directed | same type, hierarchy (parent-child / is_a) (47,750 of 47,892 edges join one type; the rest are fixed by their types) | yes |
| `DIS-GEN/ASSOCIATED_WITH` | 9,734,774 | DIS / GEN; GEN / DIS | undirected | between different types: the types fix the reading | no |
| `DIS-PHE/PHENOTYPE_PRESENT` | 157,144 | DIS / PHE; PHE / DIS | undirected | between different types: the types fix the reading | no |
| `DRG-BPO/INDICATION` | 62 | DRG / BPO | undirected | between different types: the types fix the reading | no |
| `DRG-DIS/* (2: CONTRAINDICATION, OFF_LABEL_USE)` | 12,206 | DRG / DIS | undirected | between different types: the types fix the reading | no |
| `DRG-DIS/INDICATION` | 58,174 | DIS / DRG; DRG / DIS | undirected | between different types: the types fix the reading | no |
| `DRG-DRG/PARENT` | 4,290 | DRG / DRG | directed | same type, hierarchy (parent-child / is_a) | yes |
| `DRG-DRG/SYNERGISTIC_INTERACTION` | 1,341,086 | DRG / DRG | directed | same type, symmetric | no |
| `DRG-GEN/* (33: ACTIVATOR, AGONIST, ALLOSTERIC_ANTAGONIST, ANTAGONIST, ANTISENSE_INHIBITOR, BINDING_AGENT, BLOCKER, CARRIER, CROSS_LINKING_AGENT, DEGRADER, DISRUPTING_AGENT, ENZYME, EXOGENOUS_GENE, EXOGENOUS_PROTEIN, HYDROLYTIC_ENZYME, INHIBITOR, INVERSE_AGONIST, MODULATOR, NEGATIVE_ALLOSTERIC_MODULATOR, NEGATIVE_MODULATOR, OPENER, OTHER, PARTIAL_AGONIST, POSITIVE_ALLOSTERIC_MODULATOR, POSITIVE_MODULATOR, PROTEOLYTIC_ENZYME, RELEASING_AGENT, RNAI_INHIBITOR, STABILISER, SUBSTRATE, TARGET, TRANSPORTER, VACCINE_ANTIGEN)` | 36,611 | DRG / GEN | directed | between different types: the types fix the reading | no |
| `DRG-PHE/* (5: ADVERSE_DRUG_REACTION, ASSOCIATED_WITH, CONTRAINDICATION, INDICATION, OFF_LABEL_USE)` | 13,758 | DRG / PHE | undirected | between different types: the types fix the reading | no |
| `EXP-BPO/INTERACTS_WITH` | 2,260 | BPO / EXP | undirected | between different types: the types fix the reading | no |
| `EXP-CCO/INTERACTS_WITH` | 13 | CCO / EXP | undirected | between different types: the types fix the reading | no |
| `EXP-DIS/LINKED_TO` | 2,391 | EXP / DIS | directed | between different types: the types fix the reading | no |
| `EXP-EXP/PARENT` | 2,443 | EXP / EXP | directed | same type, directional (CTD exposure links labelled parent-child) | yes |
| `EXP-GEN/INTERACTS_WITH` | 2,989 | EXP / GEN | directed | between different types: the types fix the reading | no |
| `EXP-MFN/INTERACTS_WITH` | 47 | MFN / EXP | undirected | between different types: the types fix the reading | no |
| `GEN-GEN/INTERACTS_WITH` | 327,924 | GEN / GEN | undirected | same type, symmetric | no |
| `MFN-GEN/INTERACTS_WITH` | 90,933 | GEN / MFN | undirected | between different types: the types fix the reading | no |
| `MFN-MFN/IS_A` | 12,587 | MFN / MFN | directed | same type, hierarchy (parent-child / is_a) | yes |
| `PHE-GEN/ASSOCIATED_WITH` | 793,279 | PHE / GEN | directed | between different types: the types fix the reading | no |
| `PHE-PHE/PARENT` | 26,121 | PHE / PHE | directed | same type, hierarchy (parent-child / is_a) | yes |
| `PWY-GEN/INTERACTS_WITH` | 46,977 | GEN / PWY | undirected | between different types: the types fix the reading | no |
| `PWY-PWY/PARENT` | 2,819 | PWY / PWY | directed | same type, hierarchy (parent-child / is_a) | yes |

**OptimusKG hierarchy convention** (which end of an edge is the parent, read from an ontology root, which sits only on the parent side, or from a known parent -> child pair)

| Relation | Probe | Evidence | Convention |
|---|---|---|---|
| `DIS-DIS/PARENT` | Alzheimer disease -> familial Alzheimer disease | edge parent->child: 1; child->parent: 0 | from = parent |
| `PHE-PHE/PARENT` | HPO root 'All' | root as from: 7; as to: 0 | from = parent |
| `ANA-ANA/PARENT` | UBERON root 'anatomical entity' | root as from: 6; as to: 0 | from = parent |
| `BPO-BPO/IS_A` | GO root 'biological_process' | root as from: 0; as to: 18 | from = child |
| `MFN-MFN/IS_A` | GO root 'molecular_function' | root as from: 0; as to: 32 | from = child |
| `CCO-CCO/IS_A` | GO root 'cellular_component' | root as from: 0; as to: 3 | from = child |
| `PWY-PWY/PARENT` | Reactome top-level pathway 'Disease' | root as from: 12; as to: 0 | from = parent |
| `DRG-DRG/PARENT` | nalmefene -> nalmefene hydrochloride (parent molecule -> salt) | edge parent->child: 1; child->parent: 0 | from = parent |
| `EXP-EXP/PARENT` | alachlor -> Herbicides; alachlor -> Thyroxine | both stored from the chemical | mixed (not a clean hierarchy) |

**PrimeKG file order vs OptimusKG direction**: for each PrimeKG hierarchy pair, the orientation of its first copy in `kg.csv`, compared with OptimusKG's edge between the same ontology IDs

| PrimeKG relation | Pairs | Pairs with ontology IDs | Found in OptimusKG, same order | Found in OptimusKG, opposite order | Agreement | First copy reads |
|---|---|---|---|---|---|---|
| `disease_disease` | 32,194 | 24,726 | 5,545 | 1 | 100.0% | parent -> child |
| `phenotype_phenotype` | 18,736 | 18,736 | 17,665 | 0 | 100.0% | parent -> child |
| `anatomy_anatomy` | 14,032 | 14,032 | 0 | 10,864 | 100.0% | child -> parent |
| `bioprocess_bioprocess` | 52,886 | 52,886 | 4 | 40,058 | 100.0% | parent -> child |
| `molfunc_molfunc` | 13,574 | 13,574 | 0 | 10,533 | 100.0% | parent -> child |
| `cellcomp_cellcomp` | 4,845 | 4,845 | 1 | 4,242 | 100.0% | parent -> child |
| `pathway_pathway` | 2,535 | 2,535 | 2,485 | 0 | 100.0% | parent -> child |

**Broader neurodegeneration subgraph, proposed seed**: edges of each directional same-type relation whose reversed reading is a different fact ("x of y": x such edges among the relation's y edges)

| Graph | Relation | (a) 1-hop | (b) 2-hop | (c) p95 | Single-hop facts (any definition) | Both directions stored, (b) |
|---|---|---|---|---|---|---|
| PrimeKG | `anatomy_anatomy` | 0 of 0 | 0 of 0 | 0 of 0 | 0 of 0 | 0 |
| PrimeKG | `bioprocess_bioprocess` | 0 of 0 | 0 of 0 | 0 of 0 | 0 of 0 | 0 |
| PrimeKG | `cellcomp_cellcomp` | 0 of 0 | 0 of 0 | 0 of 0 | 0 of 0 | 0 |
| PrimeKG | `disease_disease` | 91 of 91 | 1,404 of 1,404 | 559 of 559 | 69 of 69 | 0 |
| PrimeKG | `exposure_exposure` | 161 of 161 | 946 of 946 | 643 of 643 | 0 of 0 | 0 |
| PrimeKG | `molfunc_molfunc` | 0 of 0 | 0 of 0 | 0 of 0 | 0 of 0 | 0 |
| PrimeKG | `pathway_pathway` | 0 of 0 | 0 of 0 | 0 of 0 | 0 of 0 | 0 |
| PrimeKG | `phenotype_phenotype` | 162 of 162 | 1,099 of 1,099 | 815 of 815 | 0 of 0 | 0 |
| OptimusKG | `ANA-ANA/PARENT` | 0 of 0 | 0 of 0 | 0 of 0 | 0 of 0 | 0 |
| OptimusKG | `BPO-BPO/IS_A` | 0 of 0 | 0 of 0 | 0 of 0 | 0 of 0 | 0 |
| OptimusKG | `CCO-CCO/IS_A` | 0 of 0 | 0 of 0 | 0 of 0 | 0 of 0 | 0 |
| OptimusKG | `DIS-DIS/PARENT` | 111 of 111 | 790 of 790 | 143 of 143 | 89 of 89 | 0 |
| OptimusKG | `DRG-DRG/PARENT` | 77 of 77 | 378 of 378 | 206 of 206 | 0 of 0 | 0 |
| OptimusKG | `EXP-EXP/PARENT` | 168 of 168 | 1,089 of 1,089 | 1,089 of 1,089 | 0 of 0 | 0 |
| OptimusKG | `MFN-MFN/IS_A` | 0 of 0 | 0 of 0 | 0 of 0 | 0 of 0 | 0 |
| OptimusKG | `PHE-PHE/PARENT` | 136 of 136 | 1,038 of 1,038 | 913 of 913 | 0 of 0 | 0 |
| OptimusKG | `PWY-PWY/PARENT` | 0 of 0 | 0 of 0 | 0 of 0 | 0 of 0 | 0 |

## Evidence store

Built on 2026-11-19 by `python -m neuro_grounded_qa.data.build` (builder version
2.0.0) from PrimeKG v2.1 (`kg.csv` and `kg_grouped_diseases.csv`, SHA-256 checked
against the download manifest). Settled inputs: PrimeKG, the broader
neurodegeneration domain, subgraph definition (c) at the 95th degree percentile,
and the seed below. The store holds no data in the repo; it is built into
`$NGQ_DATA_DIR/store/primekg-neuro-c95/`.

| | Value |
|---|---|
| `build_id` | `primekg-2.1-6d869fa6bb10` |
| Nodes | 11,789 |
| Edges | 45,521 (1,494 directed) |
| Single-hop facts (edges with a seed at one end) | 2,236 |
| Degree cap for (c) p95 | 206 distinct neighbours |
| N1 / 1-hop nodes expanded | 993 / 657 |
| Files | `edges.jsonl` 7.3 MB, `entities.jsonl` 1.1 MB, provenance 9.5 MB, `build.json` 15 KB |
| Tool server load | about 6 s to build the in-memory store (plus about 4 s of Python imports), about 265 MB resident; `get_neighbors` about 5–7 ms per call |

### Final seed

28 PrimeKG nodes. The name rule's 22 (each main node plus its `disease_disease`
neighbours whose name contains the main node's name, minus `38307`), plus six added
on 2026-11-19 (logged as a seed-rule change). "Store ID" is the entity ID claims
use; "Edges" counts store edges touching the node.

| Group | PrimeKG index | Store ID | Name | Edges | Added |
|---|---|---|---|---|---|
| AD | `28780` | `group:alzheimer_disease` | Alzheimer disease | 233 | |
| AD | `83960` | `MONDO:0011401` | Alzheimer disease without neurofibrillary tangles | 111 | |
| AD | `32654` | `group:familial_alzheimer_disease` | familial Alzheimer disease | 15 | |
| AD | `31527` | `MONDO:0011513` | Alzheimer disease, familial early-onset, with coexisting amyloid and prion pathology | 6 | |
| AD | `94982` | `MONDO:0010783` | Alzheimer disease, susceptibility to, mitochondrial | 3 | |
| PD | `28547` | `group:parkinson_disease` | Parkinson disease | 305 | |
| PD | `33241` | `MONDO:0018466` | hereditary late onset Parkinson disease | 145 | |
| PD | `28801` | `MONDO:0014231` | juvenile onset Parkinson disease 19A | 127 | |
| PD | `27576` | `group:autosomal_recessive_parkinson_disease` | autosomal recessive Parkinson disease | 84 | |
| PD | `32468` | `MONDO:0001945` | postencephalitic Parkinson disease | 51 | |
| PD | `28182` | `group:parkinson_disease_autosomal_dominant_susceptibility_to` | Parkinson disease, autosomal dominant, susceptibility to | 17 | |
| PD | `32001` | `group:parkinson_disease_autosomal_dominant` | parkinson disease, autosomal dominant | 7 | |
| PD | `30093` | `MONDO:0009830` | parkinsonian-pyramidal syndrome | 141 | yes |
| PD | `29670` | `MONDO:0011706` | Kufor-Rakeb syndrome | 73 | yes |
| FTD | `35691` | `MONDO:0017276` | frontotemporal dementia | 15 | |
| FTD | `28751` | `group:inclusion_body_myopathy_with_paget_disease_of_bone_and_frontotemporal_dementia` | inclusion body myopathy with Paget disease of bone and frontotemporal dementia | 69 | |
| FTD | `32688` | `MONDO:0017160` | behavioral variant of frontotemporal dementia | 47 | |
| FTD | `28473` | `MONDO:0008243` | Pick disease | 155 | yes |
| FTD | `28467` | `MONDO:0011842` | Grn-related frontotemporal lobar degeneration with Tdp43 inclusions | 28 | yes |
| FTD/ALS | `28708` | `group:frontotemporal_dementia_and_or_amyotrophic_lateral_sclerosis` | frontotemporal dementia and/or amyotrophic lateral sclerosis | 71 | yes |
| FTD/ALS | `32696` | `MONDO:0017161` | frontotemporal dementia with motor neuron disease | 47 | yes |
| ALS | `27248` | `group:amyotrophic_lateral_sclerosis` | amyotrophic lateral sclerosis | 292 | |
| ALS | `31996` | `group:juvenile_amyotrophic_lateral_sclerosis` | juvenile amyotrophic lateral sclerosis | 53 | |
| ALS | `38139` | `MONDO:0005144` | familial amyotrophic lateral sclerosis | 7 | |
| ALS | `29634` | `MONDO:0008782` | amyotrophic lateral sclerosis with polyglucosan bodies | 6 | |
| HD | `31245` | `MONDO:0007739` | Huntington disease | 78 | |
| HD | `33341` | `MONDO:0016621` | juvenile Huntington disease | 41 | |
| DLB | `29296` | `MONDO:0007488` | Lewy body dementia | 36 | |

- **Excluded:** `38307` "Huntington disease and related disorders" (a parent of
  HD in MONDO and by file order).
- **Combined FTD/ALS nodes:** `28708` and `32696` are the two PrimeKG disease nodes
  that name both FTD and ALS/motor neuron disease. They form their own group here.
  `31354` "amyotrophic lateral sclerosis-parkinsonism-dementia complex" (the Guam
  complex) is not an FTD/ALS node and is not in the seed.
- **Single-hop facts by group** (a fact on two groups' seeds counts in both): AD
  364, PD 940, FTD 310, FTD/ALS 117, ALS 354, HD 118, DLB 36. 27 facts join two seed
  nodes. By relation: `disease_protein` 917, `disease_phenotype_positive` 847,
  `contraindication` 135, `indication` 129, `exposure_disease` 114,
  `disease_disease` 84, `disease_phenotype_negative` 7, `off-label use` 3.

### Counts against the expansion test

The builder reproduces the measured (c) p95 subgraph exactly. Built with the name
rule's 22 seeds (`--seed-set neuro-name-rule`), it gives the expansion test's
numbers; the six added seeds bring the rest.

| Seed | Nodes | Edges | Single-hop facts | `disease_disease` edges | `phenotype_phenotype` edges |
|---|---|---|---|---|---|
| Name rule, 22 nodes (expansion test, (c) p95) | 11,579 | 44,451 | 1,729 | 559 | 815 |
| Final, 28 nodes (the store) | 11,789 | 45,521 | 2,236 | 582 | 912 |

`scripts/data/check_store.py` also checks the store's (relation, node pair) set
against the measurement code (`scripts/data/subgraph.py`) for the final seed: the
same 45,521 edges and 11,789 nodes, none only in one, and the same degree cap.

### ID and label scheme

- **Entity IDs** are standard CURIEs (revised 2026-11-19; the first build used
  PrimeKG's own `<x_source>:<x_id>`, unpadded). PrimeKG strips the ontologies' zero
  padding and names some sources its own way; a model that knows the standard form
  may "correct" an ID and fail exact match, so the store uses the standard form.
  The mapping (`build.SOURCE_PREFIXES`, part of the `build_id`), for every PrimeKG
  source:

  | PrimeKG `x_source` | Node type | Store prefix | Local ID | Example |
  |---|---|---|---|---|
  | `MONDO` | disease | `MONDO` | padded to 7 digits | `MONDO:0007739` |
  | `HPO` | effect/phenotype | `HP` | padded to 7 digits | `HP:0002354` |
  | `GO` | biological process, cellular component, molecular function | `GO` | padded to 7 digits | `GO:0098793` |
  | `UBERON` | anatomy | `UBERON` | padded to 7 digits | `UBERON:0000955` |
  | `NCBI` | gene/protein | `NCBIGene` | unchanged | `NCBIGene:351` |
  | `DrugBank` | drug | `DrugBank` | unchanged | `DrugBank:DB00843` |
  | `REACTOME` | pathway | `REACT` | unchanged | `REACT:R-HSA-109582` |
  | `CTD` | exposure | `MESH` | unchanged (CTD's chemical IDs are MeSH IDs) | `MESH:D007854` |
  | `MONDO_grouped` | disease | `group` | slug of the node name (below) | `group:alzheimer_disease` |

  Each prefix is the namespace's most common form in OBO, Bioregistry and Biolink
  use. `NCBIGene` (not PrimeKG's `NCBI`) is the standard prefix for NCBI Gene IDs;
  `DrugBank` keeps PrimeKG's spelling (Biolink writes `DRUGBANK`). The build checks
  each raw `x_id` against the source's expected pattern and fails on a mismatch or
  an unlisted source. Entity `source` still records PrimeKG's `x_source`. IDs are
  computed over the whole graph, so a node's ID doesn't depend on the seed.
- **Collisions.** 63 CURIEs name two nodes each (61 `NCBIGene`, 2 `UBERON`), 126
  nodes in all, the same as before the prefix change (the mapping is one-to-one per
  source). They are mostly the same gene under an old and a new symbol (`ND1` and
  `MT-ND1` share `NCBIGene:4535`) and two UBERON synonyms (`multi-cellular
  organism` / `multicellular organism`). All 126 fall back to `primekg:<x_index>`;
  9 of them are in the store. No other node falls back.
- **Grouped MONDO nodes** (PrimeKG merges diseases with the same name into one
  node; `x_id` joins up to 30 MONDO numbers with `_`) get `group:<slug>`: the node
  name lowercased, each run of characters outside `a-z0-9` replaced by `_`,
  leading and trailing `_` dropped (`Parkinson disease, autosomal dominant` →
  `group:parkinson_disease_autosomal_dominant`). The `group` prefix keeps a merged
  node from posing as one MONDO term. If several groups shared a slug, they would
  be ordered by (smallest member MONDO number, `x_index`), the first keeping the
  bare slug and the rest getting `_2`, `_3`…; over all 1,267 grouped nodes no two
  share a slug, so none is suffixed. 690 grouped nodes are in the store. Each
  group's member MONDO terms (as `MONDO:` CURIEs, with their names from
  `kg_grouped_diseases.csv`) are listed in `entity_sources.jsonl`.
- **Relation labels** are PrimeKG's `relation`, except `drug_protein`, which is
  `drug_protein/<display_relation>` (`target`, `enzyme`, `transporter`, `carrier`).
  The build fails unless each label has one `display_relation` and one pair of
  endpoint types; it found 33 labels, all unambiguous. `display_relation` is kept
  on every edge for display.
- **Evidence IDs** are `E:` plus a zero-padded number (six digits here), assigned
  in (relation, subject ID, object ID) order. The same raw files and config always
  give the same IDs: two builds give byte-identical edge files.
- **`build_id`** is `primekg-2.1-` plus the first 12 hex digits of a SHA-256 over
  the raw files' SHA-256, the seed IDs, the subgraph definition, the builder version
  and the normalization rules. It is in `build.json`, which
  `tools.store.read_build_id` reads without touching the edges.

### Direction

- **Deduplication.** PrimeKG writes every fact twice (`x→y`, then later `y→x`).
  The store keeps one edge per (label, unordered pair): 8,100,498 raw rows become
  4,050,249 facts, and every pair has exactly two rows, each the other's reverse.
- **Hierarchy relations** are stored subject = child (narrower), object = parent
  (broader), with `directed` true, so claims match them in that order only. The
  orientation comes from each pair's first row in file order: parent → child for
  `disease_disease`, `phenotype_phenotype`, `bioprocess_bioprocess`,
  `molfunc_molfunc`, `cellcomp_cellcomp` and `pathway_pathway`; child → parent for
  `anatomy_anatomy` (see "Directed-relation inventory"). In this store only
  `disease_disease` (582) and `phenotype_phenotype` (912) occur.
  - Check against OptimusKG's `PARENT` edges (parent → child) between the same
    terms, for edges whose two ends are plain MONDO or HPO terms:
    `disease_disease` 366 such edges, 138 agree, 0 reversed, 228 with no OptimusKG
    edge either way; `phenotype_phenotype` 912 such edges, 790 agree, 0 reversed,
    122 with no OptimusKG edge.
- **`exposure_exposure`** (643 edges, none single-hop) stays undirected: it holds
  CTD stressor/marker links, not a hierarchy (open question).
- **Every other edge** is undirected. Its subject is, for readability, the end
  whose type the relation's name puts first (`drug_protein`: the drug;
  `anatomy_protein_present`: the anatomy), or the smaller entity ID for
  `protein_protein`, `drug_drug` and `exposure_exposure`. Claims may state these
  in either order.

### Files

A store directory (layout in `neuro_grounded_qa.data.store_files`):
`build.json` (build ID, config, raw hashes, counts, checks, file hashes, creation
time); `entities.jsonl` and `edges.jsonl` (what the tools load); and
`entity_sources.jsonl` and `edge_sources.jsonl` (provenance: PrimeKG node index and
source ID; the two `kg.csv` row numbers of each edge). `load_store` checks the
loaded files' SHA-256 against `build.json`, so a store can't serve edges under the
wrong `build_id`. Loading is pure Python; duckdb is needed only to build (the
package's optional `build` extra).

To point a run at the store instead of the fixture, override the store path (the
committed `configs/local.toml` stays on the fixtures):

```bash
scripts/run.sh -m <model> --client.base-url <url> --client.api-key-var <VAR> \
  --env.taskset.tools.store_path "$NGQ_DATA_DIR/store/primekg-neuro-c95"
```

Real question items are needed too (`--env.taskset.items_path`); see "Item set"
below.

### Checks

`scripts/data/check_store.py` (re-reads `kg.csv` with Python's `csv` module,
independently of the builder's duckdb) passes on the store:

- every one of the 45,521 edges is a raw fact in one direction, with the same
  relation and display relation, and its two recorded row numbers hold that fact
  and its reverse; no fact appears twice;
- every one of the 11,789 entities matches its raw node's name, type and source,
  and is on an edge or is a seed; its ID is re-derived from the raw source and ID
  with the script's own prefix table and slug rule (a CURIE is unique over the
  whole graph, a fallback really is shared, a group lists its members); IDs are
  unique;
- every hierarchy edge is directed with the file-order child as subject; no other
  edge is directed;
- the edge set equals the measurement code's.

A copy with one hierarchy edge reversed and one row number shifted fails both
checks. The 50-edge spot-check sample (`spot_check.md`, seed 20261119) is in the
store directory, not the repo.

### Reachability (for the pagination decision)

Reported only. `scripts/data/store_report.py` calls the real `get_neighbors` body
with the default tool config (20 results, 16,000-byte payload cap, no pagination)
for every seed node and every partner of a single-hop fact, with and without a
relation filter. A fact counts as reachable by a route if that one call returns it.
Edges come back in evidence-ID order, which is relation order, so an unfiltered
call from a seed returns the alphabetically first relations
(`contraindication`, `disease_disease`, ...).

- 2,072 calls; the largest payload was 10,222 bytes, and no page was cut by the
  payload cap, so the 20-result cap is the only limit here.
- Without pagination, 2,027 of 2,236 single-hop facts (91%) are reachable in one
  call by some route, but only 462 (21%) from the seed with no filter, and 1,011
  (45%) from the seed with a relation filter. The partner end with a filter reaches
  1,877 (84%).
- The 209 unreached facts are in (seed, relation) groups over 20 whose partner
  also has more than 20 edges: `disease_phenotype_positive` 141 (a phenotype shared
  by many diseases), `disease_protein` 54, `contraindication` 8,
  `exposure_disease` 6.
- The partner-end numbers depend on edge order, which follows the IDs: with the
  first build's IDs they were 1,862 (83%) with a filter and 2,010 (90%) by any
  route. The seed-end numbers are the same under both.
- All 66 (seed, relation) groups with fan-out ≤ 20 are fully reachable from the
  seed with a filter. The largest groups are `disease_protein` on ALS (108),
  Parkinson disease (104), and Alzheimer disease, Alzheimer disease without
  neurofibrillary tangles and Pick disease (101 each), and
  `disease_phenotype_positive` on ALS (106): 6 pages each at 20 per page.

| Route | Facts reachable in one call | Share |
|---|---|---|
| seed end, no filter | 462 of 2,236 | 21% |
| seed end, relation filter | 1,011 of 2,236 | 45% |
| partner end, no filter | 1,458 of 2,236 | 65% |
| partner end, relation filter | 1,877 of 2,236 | 84% |
| any of the four | 2,027 of 2,236 | 91% |

**By relation** (all seeds; a fact counts once)

| Relation | Facts | Seed end | Seed end, filter | Partner end | Partner end, filter | Any |
|---|---|---|---|---|---|---|
| `disease_protein` | 917 | 5% | 31% | 62% | 92% | 94% |
| `disease_phenotype_positive` | 847 | 33% | 47% | 69% | 70% | 83% |
| `contraindication` | 135 | 50% | 50% | 92% | 92% | 94% |
| `indication` | 129 | 0% | 90% | 5% | 100% | 100% |
| `exposure_disease` | 114 | 0% | 42% | 86% | 91% | 95% |
| `disease_disease` | 84 | 83% | 100% | 80% | 86% | 100% |
| `disease_phenotype_negative` | 7 | 100% | 100% | 100% | 100% | 100% |
| `off-label use` | 3 | 0% | 100% | 0% | 100% | 100% |

**By seed node and relation** (fan-out = the seed's facts of that relation)

| Group | Seed | Relation | Fan-out | Seed end | Seed end, filter | Partner end | Partner end, filter | Any |
|---|---|---|---|---|---|---|---|---|
| AD | `MONDO:0010783` Alzheimer disease, susceptibility to, mitochondrial | `disease_disease` | 1 | 100% | 100% | 0% | 100% | 100% |
| AD | `MONDO:0010783` Alzheimer disease, susceptibility to, mitochondrial | `disease_protein` | 2 | 100% | 100% | 100% | 100% | 100% |
| AD | `MONDO:0011401` Alzheimer disease without neurofibrillary tangles | `contraindication` | 2 | 100% | 100% | 100% | 100% | 100% |
| AD | `MONDO:0011401` Alzheimer disease without neurofibrillary tangles | `disease_disease` | 1 | 100% | 100% | 0% | 100% | 100% |
| AD | `MONDO:0011401` Alzheimer disease without neurofibrillary tangles | `disease_protein` | 101 | 17% | 20% | 72% | 95% | 96% |
| AD | `MONDO:0011401` Alzheimer disease without neurofibrillary tangles | `indication` | 7 | 0% | 100% | 29% | 100% | 100% |
| AD | `MONDO:0011513` Alzheimer disease, familial early-onset, with coexisting amyloid and prion pathology | `disease_disease` | 1 | 100% | 100% | 0% | 100% | 100% |
| AD | `MONDO:0011513` Alzheimer disease, familial early-onset, with coexisting amyloid and prion pathology | `disease_phenotype_positive` | 5 | 100% | 100% | 100% | 100% | 100% |
| AD | `group:alzheimer_disease` Alzheimer disease | `contraindication` | 56 | 36% | 36% | 96% | 96% | 96% |
| AD | `group:alzheimer_disease` Alzheimer disease | `disease_disease` | 10 | 0% | 100% | 60% | 70% | 100% |
| AD | `group:alzheimer_disease` Alzheimer disease | `disease_phenotype_positive` | 50 | 0% | 40% | 68% | 68% | 84% |
| AD | `group:alzheimer_disease` Alzheimer disease | `disease_protein` | 101 | 0% | 20% | 69% | 93% | 95% |
| AD | `group:alzheimer_disease` Alzheimer disease | `exposure_disease` | 8 | 0% | 100% | 75% | 75% | 100% |
| AD | `group:alzheimer_disease` Alzheimer disease | `indication` | 8 | 0% | 100% | 38% | 100% | 100% |
| AD | `group:familial_alzheimer_disease` familial Alzheimer disease | `disease_disease` | 2 | 100% | 100% | 50% | 100% | 100% |
| AD | `group:familial_alzheimer_disease` familial Alzheimer disease | `disease_phenotype_positive` | 12 | 100% | 100% | 75% | 75% | 100% |
| AD | `group:familial_alzheimer_disease` familial Alzheimer disease | `disease_protein` | 1 | 100% | 100% | 100% | 100% | 100% |
| ALS | `MONDO:0005144` familial amyotrophic lateral sclerosis | `disease_disease` | 7 | 100% | 100% | 100% | 100% | 100% |
| ALS | `MONDO:0008782` amyotrophic lateral sclerosis with polyglucosan bodies | `disease_disease` | 1 | 100% | 100% | 100% | 100% | 100% |
| ALS | `MONDO:0008782` amyotrophic lateral sclerosis with polyglucosan bodies | `disease_phenotype_positive` | 5 | 100% | 100% | 100% | 100% | 100% |
| ALS | `group:amyotrophic_lateral_sclerosis` amyotrophic lateral sclerosis | `disease_disease` | 7 | 100% | 100% | 100% | 100% | 100% |
| ALS | `group:amyotrophic_lateral_sclerosis` amyotrophic lateral sclerosis | `disease_phenotype_negative` | 5 | 100% | 100% | 100% | 100% | 100% |
| ALS | `group:amyotrophic_lateral_sclerosis` amyotrophic lateral sclerosis | `disease_phenotype_positive` | 106 | 8% | 19% | 62% | 62% | 70% |
| ALS | `group:amyotrophic_lateral_sclerosis` amyotrophic lateral sclerosis | `disease_protein` | 108 | 0% | 19% | 58% | 98% | 98% |
| ALS | `group:amyotrophic_lateral_sclerosis` amyotrophic lateral sclerosis | `exposure_disease` | 64 | 0% | 31% | 88% | 94% | 95% |
| ALS | `group:amyotrophic_lateral_sclerosis` amyotrophic lateral sclerosis | `indication` | 2 | 0% | 100% | 0% | 100% | 100% |
| ALS | `group:juvenile_amyotrophic_lateral_sclerosis` juvenile amyotrophic lateral sclerosis | `disease_disease` | 2 | 100% | 100% | 100% | 100% | 100% |
| ALS | `group:juvenile_amyotrophic_lateral_sclerosis` juvenile amyotrophic lateral sclerosis | `disease_phenotype_positive` | 43 | 42% | 47% | 58% | 58% | 79% |
| ALS | `group:juvenile_amyotrophic_lateral_sclerosis` juvenile amyotrophic lateral sclerosis | `disease_protein` | 8 | 0% | 100% | 62% | 100% | 100% |
| DLB | `MONDO:0007488` Lewy body dementia | `contraindication` | 2 | 100% | 100% | 100% | 100% | 100% |
| DLB | `MONDO:0007488` Lewy body dementia | `disease_disease` | 2 | 100% | 100% | 50% | 100% | 100% |
| DLB | `MONDO:0007488` Lewy body dementia | `disease_phenotype_positive` | 7 | 100% | 100% | 100% | 100% | 100% |
| DLB | `MONDO:0007488` Lewy body dementia | `disease_protein` | 22 | 41% | 91% | 64% | 95% | 100% |
| DLB | `MONDO:0007488` Lewy body dementia | `off-label use` | 3 | 0% | 100% | 0% | 100% | 100% |
| FTD | `MONDO:0008243` Pick disease | `contraindication` | 27 | 74% | 74% | 93% | 93% | 93% |
| FTD | `MONDO:0008243` Pick disease | `disease_disease` | 3 | 0% | 100% | 100% | 100% | 100% |
| FTD | `MONDO:0008243` Pick disease | `disease_phenotype_positive` | 17 | 0% | 100% | 94% | 94% | 100% |
| FTD | `MONDO:0008243` Pick disease | `disease_protein` | 101 | 0% | 20% | 73% | 95% | 96% |
| FTD | `MONDO:0008243` Pick disease | `indication` | 7 | 0% | 100% | 29% | 100% | 100% |
| FTD | `MONDO:0011842` Grn-related frontotemporal lobar degeneration with Tdp43 inclusions | `disease_disease` | 3 | 100% | 100% | 100% | 100% | 100% |
| FTD | `MONDO:0011842` Grn-related frontotemporal lobar degeneration with Tdp43 inclusions | `disease_phenotype_positive` | 24 | 71% | 83% | 92% | 92% | 100% |
| FTD | `MONDO:0011842` Grn-related frontotemporal lobar degeneration with Tdp43 inclusions | `disease_protein` | 1 | 0% | 100% | 100% | 100% | 100% |
| FTD | `MONDO:0017160` behavioral variant of frontotemporal dementia | `disease_disease` | 5 | 100% | 100% | 80% | 80% | 100% |
| FTD | `MONDO:0017160` behavioral variant of frontotemporal dementia | `disease_phenotype_positive` | 33 | 45% | 61% | 64% | 67% | 94% |
| FTD | `MONDO:0017160` behavioral variant of frontotemporal dementia | `disease_protein` | 9 | 0% | 100% | 78% | 100% | 100% |
| FTD | `MONDO:0017276` frontotemporal dementia | `disease_disease` | 7 | 100% | 100% | 71% | 86% | 100% |
| FTD | `MONDO:0017276` frontotemporal dementia | `disease_protein` | 8 | 100% | 100% | 75% | 100% | 100% |
| FTD | `group:inclusion_body_myopathy_with_paget_disease_of_bone_and_frontotemporal_dementia` inclusion body myopathy with Paget disease of bone and frontotemporal dementia | `disease_disease` | 6 | 100% | 100% | 83% | 83% | 100% |
| FTD | `group:inclusion_body_myopathy_with_paget_disease_of_bone_and_frontotemporal_dementia` inclusion body myopathy with Paget disease of bone and frontotemporal dementia | `disease_phenotype_positive` | 60 | 23% | 33% | 53% | 53% | 72% |
| FTD | `group:inclusion_body_myopathy_with_paget_disease_of_bone_and_frontotemporal_dementia` inclusion body myopathy with Paget disease of bone and frontotemporal dementia | `disease_protein` | 3 | 0% | 100% | 100% | 100% | 100% |
| FTD/ALS | `MONDO:0017161` frontotemporal dementia with motor neuron disease | `disease_disease` | 7 | 100% | 100% | 71% | 71% | 100% |
| FTD/ALS | `MONDO:0017161` frontotemporal dementia with motor neuron disease | `disease_phenotype_positive` | 32 | 41% | 62% | 59% | 62% | 88% |
| FTD/ALS | `MONDO:0017161` frontotemporal dementia with motor neuron disease | `disease_protein` | 8 | 0% | 100% | 75% | 100% | 100% |
| FTD/ALS | `group:frontotemporal_dementia_and_or_amyotrophic_lateral_sclerosis` frontotemporal dementia and/or amyotrophic lateral sclerosis | `disease_disease` | 4 | 100% | 100% | 75% | 75% | 100% |
| FTD/ALS | `group:frontotemporal_dementia_and_or_amyotrophic_lateral_sclerosis` frontotemporal dementia and/or amyotrophic lateral sclerosis | `disease_phenotype_positive` | 59 | 27% | 34% | 56% | 56% | 64% |
| FTD/ALS | `group:frontotemporal_dementia_and_or_amyotrophic_lateral_sclerosis` frontotemporal dementia and/or amyotrophic lateral sclerosis | `disease_protein` | 8 | 0% | 100% | 75% | 100% | 100% |
| HD | `MONDO:0007739` Huntington disease | `disease_disease` | 4 | 100% | 100% | 100% | 100% | 100% |
| HD | `MONDO:0007739` Huntington disease | `disease_phenotype_positive` | 55 | 29% | 36% | 100% | 100% | 100% |
| HD | `MONDO:0007739` Huntington disease | `disease_protein` | 17 | 0% | 100% | 35% | 94% | 100% |
| HD | `MONDO:0007739` Huntington disease | `indication` | 2 | 0% | 100% | 0% | 100% | 100% |
| HD | `MONDO:0016621` juvenile Huntington disease | `disease_disease` | 1 | 100% | 100% | 100% | 100% | 100% |
| HD | `MONDO:0016621` juvenile Huntington disease | `disease_phenotype_positive` | 21 | 90% | 95% | 57% | 57% | 100% |
| HD | `MONDO:0016621` juvenile Huntington disease | `disease_protein` | 17 | 0% | 100% | 35% | 88% | 100% |
| HD | `MONDO:0016621` juvenile Huntington disease | `indication` | 2 | 0% | 100% | 0% | 100% | 100% |
| PD | `MONDO:0001945` postencephalitic Parkinson disease | `disease_disease` | 2 | 100% | 100% | 50% | 100% | 100% |
| PD | `MONDO:0001945` postencephalitic Parkinson disease | `disease_phenotype_positive` | 41 | 44% | 49% | 100% | 100% | 100% |
| PD | `MONDO:0001945` postencephalitic Parkinson disease | `indication` | 8 | 0% | 100% | 0% | 100% | 100% |
| PD | `MONDO:0009830` parkinsonian-pyramidal syndrome | `contraindication` | 1 | 100% | 100% | 100% | 100% | 100% |
| PD | `MONDO:0009830` parkinsonian-pyramidal syndrome | `disease_disease` | 2 | 100% | 100% | 50% | 100% | 100% |
| PD | `MONDO:0009830` parkinsonian-pyramidal syndrome | `disease_phenotype_positive` | 30 | 57% | 67% | 87% | 93% | 100% |
| PD | `MONDO:0009830` parkinsonian-pyramidal syndrome | `disease_protein` | 86 | 0% | 23% | 58% | 93% | 93% |
| PD | `MONDO:0009830` parkinsonian-pyramidal syndrome | `indication` | 22 | 0% | 91% | 0% | 100% | 100% |
| PD | `MONDO:0011706` Kufor-Rakeb syndrome | `disease_disease` | 4 | 100% | 100% | 75% | 100% | 100% |
| PD | `MONDO:0011706` Kufor-Rakeb syndrome | `disease_phenotype_positive` | 67 | 24% | 30% | 81% | 81% | 84% |
| PD | `MONDO:0011706` Kufor-Rakeb syndrome | `disease_protein` | 2 | 0% | 100% | 50% | 100% | 100% |
| PD | `MONDO:0014231` juvenile onset Parkinson disease 19A | `contraindication` | 1 | 100% | 100% | 0% | 0% | 100% |
| PD | `MONDO:0014231` juvenile onset Parkinson disease 19A | `disease_disease` | 2 | 100% | 100% | 50% | 100% | 100% |
| PD | `MONDO:0014231` juvenile onset Parkinson disease 19A | `disease_phenotype_positive` | 16 | 100% | 100% | 75% | 75% | 100% |
| PD | `MONDO:0014231` juvenile onset Parkinson disease 19A | `disease_protein` | 86 | 1% | 23% | 57% | 91% | 91% |
| PD | `MONDO:0014231` juvenile onset Parkinson disease 19A | `indication` | 22 | 0% | 91% | 0% | 100% | 100% |
| PD | `MONDO:0018466` hereditary late onset Parkinson disease | `contraindication` | 1 | 100% | 100% | 0% | 0% | 100% |
| PD | `MONDO:0018466` hereditary late onset Parkinson disease | `disease_disease` | 5 | 100% | 100% | 60% | 100% | 100% |
| PD | `MONDO:0018466` hereditary late onset Parkinson disease | `disease_phenotype_positive` | 32 | 44% | 62% | 53% | 53% | 91% |
| PD | `MONDO:0018466` hereditary late onset Parkinson disease | `disease_protein` | 85 | 0% | 24% | 56% | 87% | 89% |
| PD | `MONDO:0018466` hereditary late onset Parkinson disease | `indication` | 22 | 0% | 91% | 0% | 100% | 100% |
| PD | `group:autosomal_recessive_parkinson_disease` autosomal recessive Parkinson disease | `disease_disease` | 4 | 100% | 100% | 75% | 100% | 100% |
| PD | `group:autosomal_recessive_parkinson_disease` autosomal recessive Parkinson disease | `disease_phenotype_negative` | 2 | 100% | 100% | 100% | 100% | 100% |
| PD | `group:autosomal_recessive_parkinson_disease` autosomal recessive Parkinson disease | `disease_phenotype_positive` | 43 | 33% | 47% | 72% | 74% | 84% |
| PD | `group:autosomal_recessive_parkinson_disease` autosomal recessive Parkinson disease | `disease_protein` | 35 | 0% | 57% | 49% | 83% | 94% |
| PD | `group:parkinson_disease` Parkinson disease | `contraindication` | 45 | 44% | 44% | 89% | 89% | 91% |
| PD | `group:parkinson_disease` Parkinson disease | `disease_disease` | 14 | 0% | 100% | 71% | 86% | 100% |
| PD | `group:parkinson_disease` Parkinson disease | `disease_phenotype_positive` | 73 | 0% | 27% | 51% | 51% | 63% |
| PD | `group:parkinson_disease` Parkinson disease | `disease_protein` | 104 | 0% | 19% | 55% | 84% | 87% |
| PD | `group:parkinson_disease` Parkinson disease | `exposure_disease` | 42 | 0% | 48% | 86% | 90% | 93% |
| PD | `group:parkinson_disease` Parkinson disease | `indication` | 27 | 0% | 74% | 0% | 100% | 100% |
| PD | `group:parkinson_disease_autosomal_dominant` parkinson disease, autosomal dominant | `disease_disease` | 1 | 100% | 100% | 0% | 100% | 100% |
| PD | `group:parkinson_disease_autosomal_dominant` parkinson disease, autosomal dominant | `disease_phenotype_positive` | 5 | 100% | 100% | 60% | 60% | 100% |
| PD | `group:parkinson_disease_autosomal_dominant` parkinson disease, autosomal dominant | `disease_protein` | 1 | 100% | 100% | 0% | 100% | 100% |
| PD | `group:parkinson_disease_autosomal_dominant_susceptibility_to` Parkinson disease, autosomal dominant, susceptibility to | `disease_disease` | 3 | 100% | 100% | 33% | 67% | 100% |
| PD | `group:parkinson_disease_autosomal_dominant_susceptibility_to` Parkinson disease, autosomal dominant, susceptibility to | `disease_phenotype_positive` | 11 | 100% | 100% | 45% | 45% | 100% |
| PD | `group:parkinson_disease_autosomal_dominant_susceptibility_to` Parkinson disease, autosomal dominant, susceptibility to | `disease_protein` | 3 | 100% | 100% | 100% | 100% | 100% |

## Question generation design

**Status: approved by Kaitlin on 2026-11-19 with the decisions in "Settled
decisions", and implemented; the generated set is described in "Item set" below.**
This section fixed how the question set would be built from the evidence store
(`primekg-2.1-6d869fa6bb10`) before any item existed. The templates and the
generation rules are instruments, so they were settled first; and once real items
exist the held-out split must stay unexamined, so the design rests only on synthetic
examples and aggregate counts.

Code: `scripts/data/qgen_templates.py` (the template catalog, as data: the single
place any wording lives), the shared module `scripts/data/qgen/` (`checks.py` the
reusable checks, `candidates.py` the shape generators, filters and check pipeline,
`selection.py` the targets and seeded selection, `files.py` the item files and
manifest, `verify.py` the re-check of written files, `view.py` the train-only viewer,
`safety.py` the no-text-out guard, `cli.py` the commands),
`scripts/data/question_set.py` (the commands' entry point) and
`scripts/data/qgen_feasibility.py` (counts per template after every check, on the
real store). Tests: `tests/data/test_qgen_checks.py` and
`tests/data/test_question_set.py`, on the synthetic fixtures in
`tests/fixtures/data/qgen/`.

### Principles

- **Gold comes from code.** The query that defines a candidate computes its gold
  points and gold evidence. Nothing about truth or answerability is judged by a
  person or a model.
- **Gold lives in the store; absence is checked in the full graph.** A model can
  retrieve only store edges, so gold answers are computed from the store. Every
  claim that something is *not* in the graph (unanswerable pairs, absent premises,
  graph gaps, empty lists, empty chains) is checked against the full PrimeKG graph
  (`derived/primekg.duckdb`), so the subgraph cut can never make an item
  unanswerable.
- **Reachable under the run's tool config.** Pagination is off. Every gold edge is
  returned by some call, and every absence is visible on some complete page, within
  the default budget (8 calls, 20 results). Each item records its route.
- **Names in questions, IDs in gold.** Questions name entities by name only; gold
  points use entity IDs and the store's relation labels, as claims must.
- **Neutral wording.** Every text pattern is shared by at least two answerability
  classes (tested: `test_every_text_pattern_is_shared_by_two_answerability_classes`),
  so the wording never tells a model whether the graph supports an answer.
- **One build.** Items cite `E:` IDs that exist only in one store build. Item files
  live under that `build_id`, every item carries it in its `build_id` field, and the
  taskset refuses items whose `build_id` differs from the store's.
- **Held-out text is never seen.** The split is made by code before anyone reads
  item text, and held-out items are never displayed, logged or shown to an AI (see
  "Split plan and held-out protection").

### Item shapes

Each template has one of six shapes. The shape fixes how candidates are drawn and
how gold is computed. "Seed" means one of the store's 28 seed nodes; every item is
anchored on a seed, and its **disease group** (AD, PD, FTD, ALS, HD, DLB; the two
FTD/ALS nodes count under FTD) is the anchor's. Examples use invented entities, like
the contract fixtures.

| Shape | Question pattern | Defining query (store) | Gold points |
|---|---|---|---|
| `list` | "Which *T* are *phrase* *S*?" | all edges of relation *r* at seed *S* (in a fixed role for the directed `disease_disease`) | every such edge; only if there are 1 to 5 (`K_LIST`) |
| `list2` | "Which *T1* are *phrase1* *S*, and which *T2* are *phrase2* it?" | the two lists for relations *r1*, *r2* | both lists' edges, at most 6 in all |
| `evid` | "Which edge in the knowledge graph shows that *statement*?" | one fact (*a*, *r*, *b*) | the edge stating it |
| `evid2` | "Which edges in the knowledge graph show that *statement1*, and that *statement2*?" | two facts on one seed, same relation | the edges stating them |
| `rel` | "What direct relation does the knowledge graph record between *A* and *B*?" | every edge between seed *A* and partner *B*, over the relations a disease and *B*'s type can share | every such edge |
| `bridge` | pattern-specific, naming both ends *x* and *y*, asking for the intermediates | every *m* with *x* –*r_x*– *m* –*r_y*– *y*, types and hierarchy roles enforced | both edges of every chain; only if there are 1 to 3 intermediates (`K_BRIDGE`), so at most 6 gold points |

Gold points are equal-weighted and all-or-nothing in reward v0, so the bounds keep
an item's gold to a size a complete answer can state. List and bridge questions ask
for *all* answers; a page that isn't truncated is what lets a model see it has them.

**Relation phrases** (one per relation, close to PrimeKG's `display_relation`):

| Relation | List clause (seed *S*) | Statement |
|---|---|---|
| `disease_protein` | which genes or proteins are associated with *S* | *G* is associated with *D* |
| `disease_phenotype_positive` | which phenotypes are recorded as present in *S* | *D* has the phenotype *P* |
| `disease_phenotype_negative` | which phenotypes are recorded as absent in *S* | the phenotype *P* is recorded as absent in *D* |
| `indication` | which drugs are indicated for *S* | *X* is indicated for *D* |
| `contraindication` | which drugs are contraindicated in *S* | *X* is contraindicated in *D* |
| `off-label use` | which drugs are used off-label for *S* | *X* is used off-label for *D* |
| `exposure_disease` | which exposures are linked to *S* | *E* is linked to *D* |
| `disease_disease` (directed) | which diseases are listed as subtypes of *S* / which diseases is *S* listed as a subtype of | *child* is a subtype of *parent* |

**Bridge patterns** (*x* and *y* named; the intermediates are the answer):

| Key | Question | *r_x* | *r_y* | Seed end |
|---|---|---|---|---|
| `drug_target_assoc` | Which genes or proteins targeted by *x* are associated with *y*? | `drug_protein/target` | `disease_protein` | *y* |
| `ppi_assoc` | Which proteins that interact with *x* are associated with *y*? | `protein_protein` | `disease_protein` | *y* |
| `exposure_assoc` | Which genes or proteins that interact with *x* are associated with *y*? | `exposure_protein` | `disease_protein` | *y* |
| `pathway_assoc` | Which genes or proteins in the pathway *x* are associated with *y*? | `pathway_protein` | `disease_protein` | *y* |
| `indication_target` | Which drugs indicated for *x* target *y*? | `indication` | `drug_protein/target` | *x* |
| `subtype_assoc` | Which subtypes of *x* are associated with *y*? | `disease_disease`, *m* the child | `disease_protein` | *x* |
| `phenotype_kind` | Which phenotypes of *x* are listed as kinds of *y*? | `disease_phenotype_positive` | `phenotype_phenotype`, *m* the child | *x* |
| `shared_protein` | Which genes or proteins are associated with both *x* and *y*? | `disease_protein` | `disease_protein` | *x* |
| `shared_phenotype` | Which phenotypes are recorded as present in both *x* and *y*? | `disease_phenotype_positive` | `disease_phenotype_positive` | *x* |

### Templates per type

A template is `<family>.<key>`: one pattern applied to one relation, relation pair or
bridge pattern. The catalog (`qgen_templates.catalog()`) has 75. Examples are
synthetic.

**Single-hop** (`answerable="yes"`):

| Family | Keys | Synthetic example | Gold |
|---|---|---|---|
| `sh_list` | each list clause (9) | Which drugs are indicated for Disease One? | (Drug X, `indication`, Disease One), (Drug Y, `indication`, Disease One) |
| `sh_evid` | each statement relation (8) | Which edge in the knowledge graph shows that Gene A is associated with Disease One? | (Disease One, `disease_protein`, Gene A) |
| `sh_rel` | partner type: protein, phenotype, drug, exposure, disease | What direct relation does the knowledge graph record between Disease One and Drug X? | every edge between them, e.g. (Drug X, `contraindication`, Disease One) |
| `sh_evid2` | `disease_protein`, `disease_phenotype_positive`, `exposure_disease` | Which edges in the knowledge graph show that Gene A is associated with Disease One, and that Gene B is associated with Disease One? | both facts |
| `sh_list2` | any two list clauses | Which drugs are indicated for Disease One, and which exposures are linked to it? | both lists |

**Multi-hop** (`yes`): `mh_bridge.<pattern>` for the nine bridge patterns. Example
(`drug_target_assoc`): "Which genes or proteins targeted by Drug X are associated
with Disease One?" Store: Drug X targets Gene A and Gene C; Gene A and Gene B are
associated with Disease One. Gold: (Drug X, `drug_protein/target`, Gene A) and
(Disease One, `disease_protein`, Gene A).

**Partial** (`partial`, one graph gap):

| Family | Construction | Synthetic example | Gold / gap |
|---|---|---|---|
| `pa_evid2.<r>` | one true fact and one absent fact on the same seed and relation, at a hashed position (first or second) | Which edges in the knowledge graph show that Gene A is associated with Disease One, and that Gene D is associated with Disease One? | gold (Disease One, `disease_protein`, Gene A); gap "no edge states that Gene D is associated with Disease One", triple (Disease One, `disease_protein`, Gene D) |
| `pa_list2` | one non-empty list and one relation the seed has no edges of in the full graph, either order | Which genes or proteins are associated with Disease One, and which drugs are indicated for it? | gold: the gene list; gap "which drugs are indicated for Disease One: the graph records none", no triple (no single absent fact) |

**Unanswerable** (`no`):

| Family | Construction | Synthetic example |
|---|---|---|
| `un_list.<clause>` | the seed has no edge of the relation in the full graph, while a sibling seed does | Which drugs are indicated for Disease Two? |
| `un_rel.<type>` | no edge of any relation between the seed and a near-miss partner, in the full graph | What direct relation does the knowledge graph record between Disease One and Gene D? |
| `un_bridge.<pattern>` | no chain of the pattern between *x* and *y* in the full graph; each end has edges of its relation | Which genes or proteins targeted by Drug Z are associated with Disease One? |

**False premise**:

| Family | `answerable` | Construction | Synthetic example | Gold |
|---|---|---|---|---|
| `fp_evid.<r>` | `no` | the premise's two entities share no edge of any relation in the full graph; near-miss partner | Which edge in the knowledge graph shows that Drug Z is indicated for Disease One? | none |
| `fp_contra.<r>` | `correctable` | the store holds the opposite relation between the same two entities (indication ↔ contraindication; phenotype present ↔ absent), and the premise's relation is absent in the full graph | Which edge in the knowledge graph shows that Drug X is indicated for Disease One? (the store says contraindicated) | (Drug X, `contraindication`, Disease One) |
| `fp_reversed.disease_disease` | `correctable` | the premise reverses a stored subtype edge | Which edge in the knowledge graph shows that Disease One is a subtype of Disease One type 1? | (Disease One type 1, `disease_disease`, Disease One), `directed` |

The `evid` pattern is shared with `sh_evid`: a true statement is a single-hop item, a
false one a false premise, and the text can't tell them apart.

### Rules for each type

**Multi-hop.**
- Patterns are the nine bridges above: four protein bridges into a seed
  (`drug_target_assoc`, `ppi_assoc`, `exposure_assoc`, `pathway_assoc`), a drug
  bridge out of a seed (`indication_target`), two hierarchy bridges
  (`subtype_assoc`, `phenotype_kind`) and two shared-neighbor bridges.
- Both ends are named; intermediates are never named (they are the answer, and the
  leak check rejects a question that contains an intermediate's name).
- Gold points include every edge of every chain: two per intermediate. A hierarchy
  edge keeps its `directed` flag and child → parent order.
- 1 to 3 intermediates. More would mean up to 6+ gold points, each worth the same
  as the others in v0.

**Partial.**
- One supported clause plus one unsupported clause on the same seed, combined in one
  sentence in a fixed pattern (`evid2`, `list2`) whose wording is shared with a
  fully supported single-hop template.
- One graph gap per item (proposed; see decisions). The gap's description names the
  unsupported clause; its triple is set when the clause is one checkable fact
  (`pa_evid2`), and None when it asks for a list the graph has no members of
  (`pa_list2`).
- A gap triple is absent from the full graph as a pair (no edge of any relation) and
  carries the relation's `directed` flag.
- The position of the unsupported clause is hashed, so it isn't always last.

**Unanswerable.**
- Absence is checked against the full graph: no edge of *any* relation between the
  pair (`un_rel`), no edge of the relation at the seed (`un_list`), or no chain of
  the pattern (`un_bridge`).
- **Plausible pairs.** A partner must have the right type for the relation, and must
  be a **near miss**: linked by the same relation (or chain) to a *sibling* seed of
  the anchor's disease group (any other seed for a one-seed group). So a model
  can't dismiss the pair on priors. `un_list` requires a sibling to have edges of
  the relation.
- The absence is visible within the budget: some complete page would have shown the
  edge or chain (see "Reachability" below).
- `un_list.disease_disease:*` yields nothing: the full-graph degree check ignores
  the hierarchy role, and every seed has some `disease_disease` edge. It is
  conservative and the templates are not needed.

**False premise.**
- `no`: the premise is a near-miss pair with no edge of any relation in the full
  graph.
- `correctable`: the store holds an edge that contradicts the premise, and that
  edge is the gold correction. Contradictions are opposite relations on the same
  pair (indication ↔ contraindication; phenotype present ↔ absent), or the reverse
  of a directed subtype edge. In every case the premise triple itself is absent from
  the full graph (in order for the directed relation).
- Reversed hierarchy premises are legitimate correctable premises, because
  `same_fact` compares directed facts in order. They are `fp_reversed`; whether to
  use them is a decision below.
- The premise's `directed` flag follows the relation's convention, so a reversed
  subtype premise is `directed=True` with the parent as subject.

### Validity checks

The generator runs every check on every candidate, in this order, and drops a
candidate at the first failure (`qgen.candidates.build_pool`). The feasibility script
runs the same pipeline; `question_set.py check` re-runs the per-item checks on the
written files.

0. **Domain filters** (added 2026-11-19, Kaitlin's decisions): no entity in the
   item (named, answer, gold, premise or gap) is a phenotype in HPO's
   mode-of-inheritance (`HP:0000005`) or clinical-modifier (`HP:0012823`, which
   holds clinical course, onset, severity) subtree, walked in the **full** graph's
   `phenotype_phenotype` hierarchy (`FullGraph.subtree_entities`; 32 and 156 terms in
   PrimeKG, 25 of them in the store); and a reversed-subtype premise
   (`fp_reversed`) is used only on an edge with no grouped (`group:`) node at either
   end (`qgen.candidates.domain_problem`).
1. **No fallback entities.** No entity in the question, gold, premise or gaps has a
   `primekg:` ID (`qgen.checks.has_fallback`).
2. **Unambiguous names.** Every named entity passes the name rule
   (`qgen.checks.resolve_mention`). Strict form (`name_check`): search its exact
   name with the real `search_entities` tool body (default config, 20 results); the
   intended entity must be the **first** result, and no other result may have the
   same name, ignoring case. The store ranks exact matches first and breaks ties by
   name, then ID, so a second node with the same name can be second only because of
   its ID; the model can't tell them apart from the name. **Type-qualified form**
   (`typed_name_check`), the rule in force since revision 2 (2026-11-19): every
   question names every entity with its type ("the disease *X*", "the phenotype
   *Y*", `qgen_templates.mention`), and among the same search's results *of that
   type*, the intended entity is first and no other has the same name. The tool has
   no type filter, so this is the search a model would read with the type in hand.
   Where the template's wording already names the type ("has the phenotype {obj}",
   "in the pathway {x}"), the slot keeps the bare name. Phenotype names are written
   in lowercase, except acronyms and mixed-case tokens (`display_name`); search is
   case-insensitive. (The first generated set typed a mention only when the strict
   form failed; that made typed mentions more common on answerable items.)
3. **No answer in the question.** No gold entity the question doesn't name (list
   members, bridge intermediates) appears in its text, case-insensitively on word
   boundaries (`qgen.checks.answer_leaks`).
4. **Full-graph absence** (`qgen.checks.FullGraph`, batched over all candidates):
   - pair absence: no edge of any relation between the two nodes, either order
     (unanswerable pairs, `no` premises, gap triples);
   - triple absence: no edge of that relation between them, either order for
     undirected relations and in order (child → parent, by each relation's file
     convention) for hierarchy relations (correctable premises);
   - zero degree: the seed has no edge of the relation (`un_list`, `pa_list2` gaps);
   - empty chain: no node links *x* and *y* by the pattern (`un_bridge`), with
     direction ignored, which only makes it stricter.
5. **Flags.** Every gold point's evidence exists in the store, at least one cited
   edge states it under `supports` (so a directed gold point must match the edge's
   order), and its `directed` equals the edge's. Every premise and gap triple's
   `directed` equals its relation's flag in the store, and the relation must be in
   the store (`gold_point_problems`, `triple_flag_problems`).
6. **Reachability and observability.** A **route** is a set of tool calls within
   the default budget that meets every requirement of the item
   (`qgen.checks.plan_route`):
   - each named entity costs one `search_entities` call;
   - **Show**: each gold edge is on the first page of some `get_neighbors` call
     (filtered or not) from one of its ends;
   - **RuleOut**: each absent fact is on no page, *visibly*: some call on one of its
     ends, filtered to its relation (or unfiltered), returns a complete page;
   - **Complete**: each list is on one complete page (list and `list2` questions);
   - bridges add a completeness condition. Either both ends' filtered pages are
     complete, so the intersection is visible, or one end's page is complete and
     every intermediate on it is then checked toward the other end (shown if it is
     gold, ruled out if not).
   The planner takes the cheapest set of calls (minimum cover, deterministic order).
   An item stores it in `notes`. Calls are made through the real tool bodies, so the
   result cap, the payload cap and the order are exactly those of a run.
7. **Item validation.** The candidate validates as a `QuestionItem` (labels, gold
   evidence, premise, `graph_gaps`, flag consistency per relation). Validation
   errors are counted, never printed: pydantic echoes input values.
8. **Gold completeness** (added 2026-11-20). The gold points are exactly the store
   facts that answer the question under the template's answer rule: no true answer
   missing, no non-answer included (`qgen.answers.gold_completeness`; see "Gold
   completeness" below).
9. **Duplicates and leakage across items.**
   - Identical question text: the later candidate is dropped.
   - At selection: no two items share a fact (gold point, premise or gap triple, by
     `fact_key`, which is equal exactly when `same_fact` holds), or the same set of
     named entities, **across both splits**. A question-token Jaccard of 0.9 or more
     with a selected item in the same split also excludes a candidate.
   - Fact-disjointness across splits also keeps a held-out item's gold out of train.

### Gold completeness

Reward v1 penalizes supported facts that state no gold point. That is fair only if
an item's gold is the **complete** set of true answers the store holds; otherwise an
honest answer naming a true answer the gold left out would be penalized as padding.
So every template states what answers its question, as a rule per template family in
`qgen_templates.ANSWER_RULE` (descriptions in `ANSWER_RULES`), and the generator
checks every candidate's gold against it:

| Families | Rule | The answer set (store facts) |
|---|---|---|
| `sh_list`, `un_list` | `list` | every edge of the clause's relation at the seed, in the clause's role for `disease_disease` |
| `sh_list2`, `pa_list2` | `list2` | both clauses' lists (an empty clause adds nothing) |
| `sh_evid`, `fp_evid`, `sh_evid2`, `pa_evid2` | `stated_facts` | the edges stating each asked statement: same relation and entities, in order for a hierarchy relation |
| `fp_contra` | `opposite_fact` | the edges between the statement's entities under the opposite relation (the correction) |
| `fp_reversed` | `reversed_fact` | the edge stating the hierarchy statement with its ends swapped (the correction) |
| `sh_rel`, `un_rel` | `pair_any_relation` | every edge between the two named entities, under any relation |
| `mh_bridge`, `un_bridge` | `bridge_paths` | both edges of every path *x* –*r_x*– *m* –*r_y*– *y* with the pattern's types and roles |

`qgen.answers` computes the answer set from the template and the item's recorded
template parameters (what the question text was rendered from, never the gold),
over its own index of the store's edges, and compares it with the gold points as
facts (`fact_key`, so two edges stating one fact count once). Gold is complete when
no answer fact is missing from it and none of its facts is a non-answer. For `un_*`
and `fp_evid` items the gold is empty and the rule says the store holds no answer,
which the full-graph absence checks already establish for the whole graph. No
template asks for a subset of its answers: lists and bridges are generated only when
the whole list fits the gold bounds. Completeness is over the **store**, the only
evidence a model can retrieve; a fact only in the full graph can't be cited, so it
can't be penalized as padding.

Where it runs: `build_pool` drops a failing candidate at stage `gold_complete`,
counts it by template and reason (manifest `pool.gold_completeness_failures`), and
`generate` prints those counts; `question_set.py check` re-runs it on written items
(item check `gold_complete`). A template family without a rule is refused
(`answer_rule` raises), so a new template has to state its answer set.

Held-out: completeness of held-out items follows from the same code. The generator
runs the check on every candidate of both splits, so no item set generated from now
on can hold an item with incomplete gold, and `check-heldout` re-runs it with counts
only. The current set was generated before the check existed: its train items were
checked (below), its held-out items were not read, checked or counted, so for them
completeness rests on the same shape generators that produced complete gold on all
200 train items until `check-heldout` is run on them. During development the check
was run on train only (`tests/data/test_real_train_gold.py`, opt-in with
`NGQ_ITEMS_DIR`, through the train-only loader).

### Balance and sampling

- **Type targets:** 30/25/15/20/10%, so 60/50/30/40/20 train and 30/25/15/20/10
  held out (the expansion test's sizing). False premises split `no`/`correctable`
  50/50 (proposed).
- **Disease-group quotas (proposed):** AD 30%, PD 20%, ALS 15%, FTD 15% (with the
  FTD/ALS nodes), HD 12%, DLB 8%, applied per split and type with largest-remainder
  rounding. AD gets the largest share to keep the set Alzheimer's-centered.
- **Relation cap:** no relation key (relation, list clause or bridge pattern)
  supplies more than 40% of a type in a split, the expansion test's C2 logic.
- **Seeded, deterministic selection,** per split, held-out first:
  1. order candidates by scarcity of their (type, group) cell (valid candidates per
     slot, scarcest first), then by `sha256(seed/order, item ID)`;
  2. pass 1: take a candidate if its type and its (type, group) cell have room, its
     relation key is under the cap, and it shares no fact or named-entity set with
     any selected item and isn't a text near-duplicate;
  3. pass 2: the same order without the cell quotas, so a cell its group can't fill
     spills to other groups. Spills are reported.
  Held-out selection never depends on train. Train then excludes the facts and
  anchors held-out items used.
- **Headroom.** At least 3 valid candidates per item, per split and type. Reserves
  aren't stored: the selection order is deterministic, so a regeneration after a
  code change takes the next candidates in order.
- Scarcity-first ordering matters: plain hash order left train partial AD at 2 of 9
  in pass 1, because other groups filled the shared relation cap first.

### Split plan and held-out protection

- **Split by code, before anyone reads item text.** The generator assigns each
  candidate's split as a pure function of its item ID, the split seed and the
  held-out share: `split_of(id, seed, 1/3)` is held-out when
  `sha256(seed, id) < 1/3` (`qgen.checks.split_of`). Stratification by type and
  group comes from selecting each split's targets from its own pool. Because the
  split depends on nothing else, no change to templates, filters or the pool can move
  a candidate from held-out to train. This is what lets train review change the
  generator without exposing held-out items.
- **One process, no text out.** Generation, checks, split, selection and writing
  happen in one run that prints only aggregate counts (per type, group, relation
  and check). Exceptions are caught and re-raised with the check name, template ID
  and opaque item ID only (`ValidationError.errors(include_input=False)`, never
  `str(e)`).
- **Separate files.** `train.jsonl` and `heldout.jsonl`. The held-out file is
  never printed, logged, summarized beyond aggregate counts, paraphrased, critiqued
  or opened by any AI, including the generator's own worker. Items carry
  `split="heldout"`, and the taskset already refuses to load that split without
  `allow_heldout=true`.
- **Manifest.** `manifest.json` records the SHA-256 of each file, counts per split ×
  type × group × relation key, the generator version and git commit, the split seed
  and share, the `build_id`, the store's `build.json` SHA-256 and the template
  catalog's SHA-256. Checking the held-out file means comparing its hash and its
  counts (a script that validates every held-out item and prints pass/fail and
  counts only), never reading it.
- **Review is train only.** Reviewing items (by Kaitlin, or AI critique) reads
  `train.jsonl` only, through a viewer that refuses `heldout`. Any change prompted by
  review is a code change (template wording, a filter, a check) applied to both
  splits by regenerating. Held-out items are never edited one by one.
- **Tests never touch real items.** The generator's tests run on the synthetic
  fixtures. The real-data tests check only the manifest (hashes, counts), not item
  text. The feasibility script has a test that its output holds no entity name or
  ID (`test_feasibility_reports_no_names_or_ids`).
- **Guards beyond procedure (proposed; outside this design's files):** a Claude Code
  deny rule for reads of `$NGQ_DATA_DIR/items/**/heldout.jsonl` in the project's
  `.claude/settings.json`, and a CLAUDE.md line naming the file as never-read. File
  permissions can't stop an agent running as the same user, and access times on WSL
  aren't a reliable audit, so neither is offered as a guarantee.

### File layout and provenance

- `$NGQ_DATA_DIR/items/<build_id>/train.jsonl`, `heldout.jsonl` and `manifest.json`.
  Not committed (data stays out of git). Whether the question set ships in the Hub
  package is still Kaitlin's open decision.
- **Item ID:** `q-` plus 12 hex digits of SHA-256 over (`build_id`, template ID,
  template parameters) (`qgen.checks.item_id`). It is stable across regenerations
  and says nothing about type or split, which keeps blind grading blind.
- `build_id` is the item's own field (`QuestionItem.build_id`), written from the
  store's `build.json`, never typed by hand. The generator refuses to write unless
  the directory name, the manifest's `build_id` and every item's agree.
- `source="template"`, `template_id` the template's ID, `evidence_scope="graph"`,
  `split` from the hash.
- `notes` is a JSON string (`qgen.candidates.item_notes`), IDs and relation labels
  only: generator version, template parameters (entity IDs and relation keys), the
  disease group, the relation key, the named entities and which of them are typed
  mentions, the route (`Route.as_notes()`: the searches and the `get_neighbors`
  calls, by ID), the probes' allowed start entities, the requirements the route
  meets, and the full-graph conditions checked. It no longer carries `build_id`.
- Every item file is valid only for its `build_id`.

### AI paraphrase (later, optional, train only)

Not implemented. If it is wanted later:

- It runs only on `train.jsonl`, after the split, and never sees `heldout.jsonl`.
- A paraphrased item keeps its ID, gold and template, gets
  `source="template+llm_paraphrase"`, and keeps the original text in `notes`
  (`template_text`), with the model family and version that wrote the paraphrase.
- The paraphrasing model family is one that is not evaluated, or is excluded from
  the evaluation and the exclusion stated (the brief's guardrail).
- Each paraphrase is checked to still ask exactly what the query answers:
  - every named entity's name appears verbatim, so the name rule still holds;
  - no answer entity's name appears;
  - the relation phrase's meaning is unchanged;
  - it still asks for *all* answers on list and bridge items, and for a *direct*
    relation on `rel` items.
  Code checks the first two; Kaitlin decides how the last two are checked. A
  failing paraphrase is dropped and the template text kept.
- Scores are reported by `source`, so any gain on AI-worded items shows up.
- Held-out items always keep template wording. Train and held-out wording then
  differ in style only when paraphrase is used; that is a confound to report.

### Feasibility on the built store

**Design stage, before the domain filters and the type-qualified name rule.** The
counts after them are in "Feasibility after the filters" below.

`qgen_feasibility.py` on `primekg-2.1-6d869fa6bb10`, default tool config (8 calls,
20 results, no pagination), split seed `ngq-split-v1`, held-out share 1/3. Run on
2026-11-19 in about 3 minutes. Two runs give identical aggregates. Counts only; the
JSON is `$NGQ_DATA_DIR/derived/qgen_feasibility_design.json` (it was
`qgen_feasibility.json` until the filtered run replaced that file). The shared
module reproduces these numbers exactly with `--no-filters`.

- 81,844 candidates generated, 45,868 pass every check.
- Absence templates (`un_*`, `fp_evid`, `pa_evid2`) consider at most 300 near-miss
  partners per (template, seed), so their counts are lower bounds.
- `sh_evid` and `sh_rel` count the same facts twice (as two templates). Selection is
  fact-disjoint, so the pool is smaller than the sum suggests, but every type still
  has 16× or more of its target.
- **Every target is met** in the simulated selection, in pass 1 (with the
  disease-group quotas), for both splits. The only cell under 3× headroom is train
  partial HD (11 valid for 4 slots).

**Funnel per template** (candidates left after each check, in order)

| Template | generated | no_fallback | names | no_leak | full_graph | flags | route | item_valid | valid |
|---|---|---|---|---|---|---|---|---|---|
| `fp_contra.contraindication` | 129 | 129 | 127 | 127 | 127 | 127 | 127 | 127 | 127 |
| `fp_contra.disease_phenotype_negative` | 847 | 847 | 714 | 714 | 714 | 714 | 606 | 606 | 606 |
| `fp_contra.disease_phenotype_positive` | 7 | 7 | 2 | 2 | 2 | 2 | 1 | 1 | 1 |
| `fp_contra.indication` | 135 | 135 | 133 | 133 | 133 | 133 | 127 | 127 | 127 |
| `fp_evid.contraindication` | 819 | 819 | 782 | 782 | 782 | 782 | 782 | 782 | 782 |
| `fp_evid.disease_disease` | 274 | 274 | 252 | 252 | 252 | 252 | 252 | 252 | 252 |
| `fp_evid.disease_phenotype_negative` | 25 | 25 | 25 | 25 | 25 | 25 | 25 | 25 | 25 |
| `fp_evid.disease_phenotype_positive` | 2912 | 2912 | 2639 | 2639 | 2639 | 2639 | 2082 | 2082 | 2082 |
| `fp_evid.disease_protein` | 2347 | 2331 | 2226 | 2226 | 2226 | 2226 | 2165 | 2165 | 2165 |
| `fp_evid.exposure_disease` | 657 | 657 | 637 | 637 | 637 | 637 | 637 | 637 | 637 |
| `fp_evid.indication` | 251 | 251 | 244 | 244 | 244 | 244 | 244 | 244 | 244 |
| `fp_reversed.disease_disease` | 111 | 111 | 88 | 88 | 88 | 88 | 88 | 88 | 69 |
| `mh_bridge.drug_target_assoc` | 1300 | 1300 | 1204 | 1204 | 1204 | 1204 | 834 | 834 | 834 |
| `mh_bridge.exposure_assoc` | 192 | 192 | 178 | 178 | 178 | 178 | 73 | 73 | 73 |
| `mh_bridge.indication_target` | 58 | 58 | 58 | 58 | 58 | 58 | 37 | 37 | 37 |
| `mh_bridge.pathway_assoc` | 784 | 784 | 660 | 626 | 626 | 626 | 379 | 379 | 379 |
| `mh_bridge.phenotype_kind` | 625 | 625 | 548 | 531 | 531 | 531 | 276 | 276 | 276 |
| `mh_bridge.ppi_assoc` | 7086 | 7082 | 6115 | 6114 | 6114 | 6114 | 4318 | 4318 | 4318 |
| `mh_bridge.shared_phenotype` | 15398 | 15398 | 13827 | 13238 | 13238 | 13238 | 2851 | 2851 | 2851 |
| `mh_bridge.shared_protein` | 3120 | 3112 | 2770 | 2755 | 2755 | 2755 | 824 | 824 | 824 |
| `mh_bridge.subtype_assoc` | 841 | 838 | 722 | 410 | 410 | 410 | 384 | 384 | 384 |
| `pa_evid2.disease_phenotype_positive` | 684 | 684 | 612 | 612 | 612 | 612 | 336 | 336 | 336 |
| `pa_evid2.disease_protein` | 286 | 277 | 269 | 269 | 269 | 269 | 230 | 230 | 230 |
| `pa_list2` | 320 | 310 | 298 | 188 | 168 | 168 | 168 | 168 | 168 |
| `sh_evid.contraindication` | 135 | 135 | 133 | 133 | 133 | 133 | 127 | 127 | 127 |
| `sh_evid.disease_disease` | 111 | 111 | 88 | 88 | 88 | 88 | 88 | 88 | 69 |
| `sh_evid.disease_phenotype_negative` | 7 | 7 | 2 | 2 | 2 | 2 | 2 | 2 | 2 |
| `sh_evid.disease_phenotype_positive` | 847 | 847 | 714 | 714 | 714 | 714 | 606 | 606 | 606 |
| `sh_evid.disease_protein` | 917 | 914 | 798 | 798 | 798 | 798 | 746 | 746 | 746 |
| `sh_evid.exposure_disease` | 114 | 114 | 49 | 49 | 49 | 49 | 46 | 46 | 46 |
| `sh_evid.indication` | 129 | 129 | 127 | 127 | 127 | 127 | 127 | 127 | 127 |
| `sh_evid.off-label use` | 3 | 3 | 3 | 3 | 3 | 3 | 3 | 3 | 3 |
| `sh_evid2.disease_phenotype_positive` | 416 | 416 | 336 | 336 | 336 | 336 | 250 | 250 | 250 |
| `sh_evid2.disease_protein` | 452 | 450 | 392 | 392 | 392 | 392 | 341 | 341 | 341 |
| `sh_evid2.exposure_disease` | 57 | 57 | 24 | 24 | 24 | 24 | 21 | 21 | 21 |
| `sh_list.contraindication` | 5 | 5 | 5 | 5 | 5 | 5 | 5 | 5 | 5 |
| `sh_list.disease_disease:children` | 14 | 14 | 12 | 9 | 9 | 9 | 9 | 9 | 9 |
| `sh_list.disease_disease:parents` | 26 | 26 | 24 | 11 | 11 | 11 | 11 | 11 | 11 |
| `sh_list.disease_phenotype_negative` | 2 | 2 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| `sh_list.disease_phenotype_positive` | 3 | 3 | 3 | 2 | 2 | 2 | 2 | 2 | 2 |
| `sh_list.disease_protein` | 7 | 6 | 6 | 5 | 5 | 5 | 5 | 5 | 5 |
| `sh_list.indication` | 3 | 3 | 2 | 2 | 2 | 2 | 2 | 2 | 2 |
| `sh_list.off-label use` | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| `sh_list2` | 58 | 56 | 52 | 26 | 26 | 26 | 26 | 26 | 26 |
| `sh_rel.disease` | 111 | 111 | 88 | 88 | 88 | 88 | 88 | 88 | 88 |
| `sh_rel.drug` | 267 | 267 | 263 | 263 | 263 | 263 | 257 | 257 | 257 |
| `sh_rel.effect/phenotype` | 851 | 851 | 716 | 716 | 716 | 716 | 607 | 607 | 607 |
| `sh_rel.exposure` | 114 | 114 | 49 | 49 | 49 | 49 | 46 | 46 | 46 |
| `sh_rel.gene/protein` | 917 | 914 | 798 | 798 | 798 | 798 | 746 | 746 | 746 |
| `un_bridge.drug_target_assoc` | 2162 | 2162 | 2023 | 2023 | 1962 | 1962 | 1705 | 1705 | 1705 |
| `un_bridge.exposure_assoc` | 378 | 378 | 351 | 351 | 346 | 346 | 282 | 282 | 282 |
| `un_bridge.indication_target` | 7 | 7 | 7 | 7 | 3 | 3 | 3 | 3 | 3 |
| `un_bridge.pathway_assoc` | 1343 | 1343 | 1228 | 1228 | 1173 | 1173 | 1082 | 1082 | 1082 |
| `un_bridge.phenotype_kind` | 5852 | 5852 | 5649 | 5649 | 4886 | 4886 | 4371 | 4371 | 4371 |
| `un_bridge.ppi_assoc` | 5231 | 5227 | 4927 | 4927 | 4336 | 4336 | 3955 | 3955 | 3955 |
| `un_bridge.shared_phenotype` | 6754 | 6754 | 6536 | 6536 | 3408 | 3408 | 1429 | 1429 | 1429 |
| `un_bridge.shared_protein` | 4724 | 4724 | 4380 | 4380 | 3706 | 3706 | 3113 | 3113 | 3113 |
| `un_bridge.subtype_assoc` | 3278 | 3259 | 3050 | 3050 | 1746 | 1746 | 1731 | 1731 | 1731 |
| `un_list.contraindication` | 14 | 14 | 13 | 13 | 13 | 13 | 13 | 13 | 13 |
| `un_list.disease_disease:children` | 13 | 13 | 13 | 13 | 0 | 0 | 0 | 0 | 0 |
| `un_list.disease_phenotype_negative` | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 |
| `un_list.disease_phenotype_positive` | 4 | 4 | 3 | 3 | 3 | 3 | 3 | 3 | 3 |
| `un_list.disease_protein` | 4 | 4 | 4 | 4 | 4 | 4 | 4 | 4 | 4 |
| `un_list.exposure_disease` | 16 | 16 | 16 | 16 | 16 | 16 | 16 | 16 | 16 |
| `un_list.indication` | 17 | 17 | 16 | 16 | 16 | 16 | 16 | 16 | 16 |
| `un_rel.disease` | 274 | 274 | 252 | 252 | 252 | 252 | 252 | 252 | 252 |
| `un_rel.drug` | 1067 | 1067 | 1023 | 1023 | 1023 | 1023 | 1023 | 1023 | 1023 |
| `un_rel.effect/phenotype` | 2915 | 2915 | 2642 | 2642 | 2642 | 2642 | 2085 | 2085 | 2085 |
| `un_rel.exposure` | 657 | 657 | 637 | 637 | 637 | 637 | 637 | 637 | 637 |
| `un_rel.gene/protein` | 2347 | 2331 | 2226 | 2226 | 2226 | 2226 | 2165 | 2165 | 2165 |

**Valid candidates per split and type, against targets**

| Type | train valid | train target | multiple | held-out valid | held-out target | multiple |
|---|---|---|---|---|---|---|
| single_hop | 2729 | 60 | 45x | 1415 | 30 | 47x |
| multi_hop | 6684 | 50 | 134x | 3292 | 25 | 132x |
| partial | 482 | 30 | 16x | 252 | 15 | 17x |
| unanswerable | 15878 | 40 | 397x | 8019 | 20 | 401x |
| false_premise:no | 4136 | 10 | 414x | 2051 | 5 | 410x |
| false_premise:correctable | 631 | 10 | 63x | 299 | 5 | 60x |

**Valid candidates per type and relation key, both splits**

| Type | Relation key | Valid |
|---|---|---|
| false_premise:correctable | contraindication | 127 |
| false_premise:correctable | disease_disease | 69 |
| false_premise:correctable | disease_phenotype_negative | 606 |
| false_premise:correctable | disease_phenotype_positive | 1 |
| false_premise:correctable | indication | 127 |
| false_premise:no | contraindication | 782 |
| false_premise:no | disease_disease | 252 |
| false_premise:no | disease_phenotype_negative | 25 |
| false_premise:no | disease_phenotype_positive | 2082 |
| false_premise:no | disease_protein | 2165 |
| false_premise:no | exposure_disease | 637 |
| false_premise:no | indication | 244 |
| multi_hop | drug_target_assoc | 834 |
| multi_hop | exposure_assoc | 73 |
| multi_hop | indication_target | 37 |
| multi_hop | pathway_assoc | 379 |
| multi_hop | phenotype_kind | 276 |
| multi_hop | ppi_assoc | 4318 |
| multi_hop | shared_phenotype | 2851 |
| multi_hop | shared_protein | 824 |
| multi_hop | subtype_assoc | 384 |
| partial | disease_phenotype_positive | 336 |
| partial | disease_protein | 230 |
| partial | list2 | 168 |
| single_hop | contraindication | 132 |
| single_hop | disease | 88 |
| single_hop | disease_disease | 69 |
| single_hop | disease_disease:children | 9 |
| single_hop | disease_disease:parents | 11 |
| single_hop | disease_phenotype_negative | 3 |
| single_hop | disease_phenotype_positive | 858 |
| single_hop | disease_protein | 1092 |
| single_hop | drug | 257 |
| single_hop | effect/phenotype | 607 |
| single_hop | exposure | 46 |
| single_hop | exposure_disease | 67 |
| single_hop | gene/protein | 746 |
| single_hop | indication | 129 |
| single_hop | list2 | 26 |
| single_hop | off-label use | 4 |
| unanswerable | contraindication | 13 |
| unanswerable | disease | 252 |
| unanswerable | disease_phenotype_negative | 12 |
| unanswerable | disease_phenotype_positive | 3 |
| unanswerable | disease_protein | 4 |
| unanswerable | drug | 1023 |
| unanswerable | drug_target_assoc | 1705 |
| unanswerable | effect/phenotype | 2085 |
| unanswerable | exposure | 637 |
| unanswerable | exposure_assoc | 282 |
| unanswerable | exposure_disease | 16 |
| unanswerable | gene/protein | 2165 |
| unanswerable | indication | 16 |
| unanswerable | indication_target | 3 |
| unanswerable | pathway_assoc | 1082 |
| unanswerable | phenotype_kind | 4371 |
| unanswerable | ppi_assoc | 3955 |
| unanswerable | shared_phenotype | 1429 |
| unanswerable | shared_protein | 3113 |
| unanswerable | subtype_assoc | 1731 |

**Valid candidates per type and disease group, both splits**

| Type | AD | PD | ALS | FTD | HD | DLB |
|---|---|---|---|---|---|---|
| single_hop | 808 | 1998 | 119 | 829 | 295 | 95 |
| multi_hop | 2056 | 4284 | 271 | 2014 | 1065 | 286 |
| partial | 45 | 405 | 43 | 177 | 18 | 46 |
| unanswerable | 3038 | 9385 | 2065 | 7146 | 143 | 2120 |
| false_premise:no | 829 | 2513 | 831 | 1173 | 34 | 807 |
| false_premise:correctable | 138 | 436 | 42 | 220 | 83 | 11 |

**Simulated selection** (pass 1 with disease-group cells; pass 2 spills)

| Split | Type | Target | After pass 1 | Selected | Cells short (selected/target) |
|---|---|---|---|---|---|
| heldout | single_hop | 30 | 30 | 30 | none |
| heldout | multi_hop | 25 | 25 | 25 | none |
| heldout | partial | 15 | 15 | 15 | none |
| heldout | unanswerable | 20 | 20 | 20 | none |
| heldout | false_premise:no | 5 | 5 | 5 | none |
| heldout | false_premise:correctable | 5 | 5 | 5 | none |
| train | single_hop | 60 | 60 | 60 | none |
| train | multi_hop | 50 | 50 | 50 | none |
| train | partial | 30 | 30 | 30 | none |
| train | unanswerable | 40 | 40 | 40 | none |
| train | false_premise:no | 10 | 10 | 10 | none |
| train | false_premise:correctable | 10 | 10 | 10 | none |

**Cells under 3x headroom** (valid candidates per item, by split, type and group)

| Split | Type | Group | Target | Valid | Multiple |
|---|---|---|---|---|---|
| train | partial | HD | 4 | 11 | 2.8x |

**Route length** (tool calls in the cheapest route, valid candidates)

| Calls | Candidates |
|---|---|
| 2 | 158 |
| 3 | 18654 |
| 4 | 22899 |
| 5 | 3171 |
| 6 | 525 |
| 7 | 303 |
| 8 | 196 |

**Name-rule failures** (candidates dropped, by reason): not_first 6401, shared_name 519


**Where candidates are lost.**
- **Names.** 6,920 candidate drops: 6,401 `not_first` and 519 `shared_name`. All
  come from 48 names shared by 96 store nodes, mostly across types: 26
  disease/phenotype pairs, 13 drug/exposure pairs, 6 process/pathway pairs. A
  question that names the type ("the phenotype …") could disambiguate them; the
  rule is kept strict for now (decision 8).
- **Routes.** These are mostly shared-neighbor bridges (`mh_bridge.shared_phenotype`
  keeps 2,851 of 13,238) and phenotype items: a phenotype shared by many diseases
  has a truncated page, and a big seed's phenotype page is truncated too, so neither
  end can show the edge or rule an absence out.
- **Leaks.** `subtype_assoc` loses 312 of 722 and `pa_list2` and `sh_list2` lose a third to a half:
  a subtype's name usually contains its parent's ("familial *X*" under *X*), so the
  answer would be in the question. Working as intended.

### Shortfalls and how to fill them

There is no shortfall against 200 + 100 at the type level, and none at the cell level
after pass 1. The thin spots:

- **Partial is the tightest type** (16× overall, all three relation keys needed
  under the 40% cap). HD has only `disease_phenotype_positive` partial candidates
  (18 in all), so train partial HD sits at 2.8×. To fill: let the HD cell spill
  (already the pass-2 rule), or add partial templates that combine a supported
  multi-hop clause with an absent one, which would draw on the much larger bridge
  pool.
- **`pa_evid2.exposure_disease` yields nothing.** Exposure edges sit on main
  disease nodes, whose siblings have none, so no near-miss partner exists. Drop the
  template, or allow near misses through any seed for this relation.
- **`sh_list` is small** (36 candidates over all clauses). Few seeds have 1 to 5
  edges of a relation. It isn't needed for volume (`sh_evid` and `sh_rel` fill
  single-hop), but list questions are the most natural wording. Raising `K_LIST` to
  8 would add some, at the cost of larger gold sets.
- **Few `indication_target` bridges** (37 answerable, 3 unanswerable), and few
  `off-label use` or `disease_phenotype_negative` items: these relations are rare
  around the seeds. They add variety and don't carry volume.
- **Correctable false premises lean on phenotypes:** 606 of 930 are "the phenotype
  *P* is recorded as absent in *D*" against a stored "present" edge. The 40% cap
  keeps them to 40% of false premises; indication/contraindication (254) and
  reversed subtypes (69) supply the rest.

### Settled decisions

Kaitlin, 2026-11-19, on the list below:

- **Size:** 200 train + 100 held-out, at 30/25/15/20/10% by type.
- **False premises:** 50/50 `no`/`correctable`. "Phenotype recorded as absent"
  premises stay under the 40% relation cap, as designed.
- **Gaps:** one per partial item.
- **Disease-group quotas:** AD 30, PD 20, ALS 15, FTD (with the FTD/ALS nodes) 15,
  HD 12, DLB 8 percent.
- **Bounds:** at most 5 answers per list and 3 intermediates per bridge.
- **Absence items:** near-miss partners.
- **Split:** seed `ngq-split-v1`, held-out share 1/3, held-out selected first.
- **Domain filters:** exclude HPO's mode-of-inheritance and clinical-modifier
  subtrees from phenotype templates; use reversed-subtype false premises only for
  edges that don't touch a grouped (`group:`) node.
- **Names:** a name shared by two nodes is allowed if the question names the entity
  type and a search read that way resolves to the intended node; otherwise the
  strict rule applies.
- **Prompt hint and held-out guards:** added to the taskset prompt and the project
  settings (see `docs/changelog.md`).

Revision 2 (Kaitlin, 2026-11-19, after reviewing the first generated set):

- **Wording:** every entity mention is typed, in every template and class (the
  name rule is the type-qualified one alone); phenotype names in lowercase.
- **False premises:** the 40% relation cap applies within each kind (`no`,
  `correctable`) separately.
- **Shared-neighbor bridges** (`shared_phenotype`, `shared_protein`) stay as they
  are; the smoke run reports by template family to check them.
- **Left as is:** the relation cap's rounding, and the thin partial AD and HD
  cells.

### Decisions for Kaitlin (as raised at design time; settled above)

1. **Size.** 200 + 100 is feasible with headroom everywhere but train partial HD.
   The pool would support more (partial is the limit, at roughly 16× now).
2. **False premises:** the `no`/`correctable` mix (proposed 50/50, which the reward
   spec's baseline assumes), and whether reversed subtype premises
   (`fp_reversed`, 69 candidates) are used.
3. **Graph gaps per partial item:** proposed one. Two would need three-clause
   templates.
4. **Disease-group quotas** (proposed AD 30, PD 20, ALS 15, FTD 15, HD 12, DLB 8).
5. **Bounds:** `K_LIST` 5, `K_BRIDGE` 3 intermediates.
6. **Near-miss plausibility** for absence items (partners linked to a sibling seed),
   rather than any type-matched node.
7. **Domain review of the wording.** Templates to look at:
   - `disease_disease` as "is a subtype of": some MONDO parent-child links read oddly
     as subtypes. "Hereditary late onset Parkinson disease" is a *parent* of the
     grouped Parkinson disease node (expansion test). Reversed-subtype premises built
     on such an edge would be confusing rather than false.
   - `disease_phenotype_positive` as "phenotype": about 52 of the 847 seed phenotype facts (by a name match)
     are HPO inheritance, onset or course terms (e.g. "autosomal dominant
     inheritance", "adult onset", "progressive"). Questions such as "Which phenotypes
     are recorded as present in both *x* and *y*?" then have inheritance modes as
     answers. A filter on HPO's mode-of-inheritance and clinical-modifier subtrees
     would remove them (not applied; counts above include them).
   - `fp_contra.disease_phenotype_negative`: "the phenotype *P* is recorded as absent
     in *D*", false because the graph records it as present. Accurate, but it is the
     awkward direction of the opposition.
   - `exposure_disease` as "linked to" (CTD associations, not causal claims), and
     `exposure_protein` as "interacts with" in `exposure_assoc`.
   - `contraindication` as "contraindicated in", and `indication_target` ("Which drugs
     indicated for *x* target *y*?"): DrugBank targets include non-mechanistic ones.
   - `shared_phenotype` and `shared_protein` with an arbitrary second disease: some
     pairs are biologically unrelated, so the question reads as arbitrary.
8. **Name rule:** keep it strict, or allow a cross-type duplicate when the question
   names the type (would recover part of the 6,920 drops).
9. **Phenotype filter** for HPO inheritance and modifier terms (decision 7).
10. **Relation-filter hint in the system prompt.** Every route in the design uses
    filtered `get_neighbors` calls; an unfiltered call from a big seed shows only its
    alphabetically first relations. A one-line hint ("pass `relation` to narrow a
    long list") would make the designed routes discoverable. It is a change to the
    taskset's prompt, not to question generation.
11. **Held-out guards:** the Claude Code deny rule and the CLAUDE.md line (project
    settings, outside the data code).
12. **Split seed** (`ngq-split-v1`) and held-out share (1/3). Set once; changing the
    seed reassigns every candidate.

### What the implementation needed (done)

- A generator that reuses the template catalog, the checks, the shape generators and
  the selection from one shared module: `scripts/data/qgen/`, used by
  `question_set.py generate` and the feasibility script. It writes the two files and
  the manifest, and prints only counts.
- A train-only viewer (`view`, `spot-check`), and a held-out integrity check
  (`check-heldout`) that prints hashes, counts and pass/fail only.
- Tests on synthetic fixtures for writing, the manifest, determinism, split
  stability under pool changes, and the no-text-output property of every command
  that touches items (`tests/data/test_question_set.py`).
- `QuestionItem` now has a `build_id` field, which items carry; `notes` is still a
  JSON string (a structured `notes` remains a possible contract change).
- Context mode (the brief's tool-free mode) needs each item's candidate evidence;
  this design doesn't define it. The route's pages are a natural source.
- `configs/local.toml` must point `items_path` at `train.jsonl` and `store_path` at
  the store for real runs. The committed config stays on fixtures.

### Feasibility after the filters

(Revision 1 rules: names typed only when shared, as stored; one cap over all false
premises.)

`qgen_feasibility.py` on the same store and tool config, 2026-11-19, with the domain
filters and the type-qualified name rule on (`Filters()`, the generator's default).
Counts only; the JSON is `$NGQ_DATA_DIR/derived/qgen_feasibility.json`.

| | Before (design stage) | After |
|---|---|---|
| Candidates generated | 81,844 | 81,844 |
| Dropped by the domain filters | n/a | 3,097 (3,022 HPO subtree, 75 reversed premise on a grouped node) |
| Dropped by the name rule | 6,920 (6,401 `not_first`, 519 `shared_name`) | 18 (9 `not_first_of_type`, 9 `shared_name_in_type`) |
| Valid | 45,868 | 49,003 |
| Targets met in pass 1 (both splits) | yes | yes |

- **HPO filter.** 188 PrimeKG terms are in the two subtrees (32 under "Mode of
  inheritance", 156 under "Clinical modifier", which holds "Clinical course",
  onset, pace of progression and severity); 25 of them are in the store. Every
  candidate that touches one is dropped, whole: a list or bridge whose answers
  include one is not trimmed, since its gold would then be incomplete.
  `sh_list.disease_phenotype_positive` goes from 2 valid to 0.
- **Grouped nodes.** `fp_reversed.disease_disease` goes from 69 valid to 32.
- **Type-qualified names** recover most of the 6,920 name drops. ALS single-hop
  candidates go from 119 to 735: the grouped ALS seed shares its name with an HPO
  phenotype, so under the strict rule it could never be named.
- **Typed mentions by answerability** (valid candidates naming at least one entity
  with its type): `yes` 2,022 of 15,579 (13%), `partial` 59 of 741 (8%),
  `correctable` 104 of 953 (11%), `no` 1,987 of 31,730 (6%). The rule depends on the
  entity alone, but entities aren't spread evenly over the classes (answerable items
  name seeds more often, and some seeds share a phenotype's name), so a typed
  mention is mildly more common on answerable candidates. Settled in revision 2 by
  typing every mention (next section).

**Valid candidates per split and type, against targets**

| Type | train valid | train target | multiple | held-out valid | held-out target | multiple |
|---|---|---|---|---|---|---|
| single_hop | 3147 | 60 | 52x | 1626 | 30 | 54x |
| multi_hop | 7251 | 50 | 145x | 3555 | 25 | 142x |
| partial | 494 | 30 | 16x | 247 | 15 | 16x |
| unanswerable | 16790 | 40 | 420x | 8446 | 20 | 422x |
| false_premise:no | 4349 | 10 | 435x | 2145 | 5 | 429x |
| false_premise:correctable | 644 | 10 | 64x | 309 | 5 | 62x |

**Valid candidates per type and disease group, both splits**

| Type | AD | PD | ALS | FTD | HD | DLB |
|---|---|---|---|---|---|---|
| single_hop | 799 | 1963 | 735 | 889 | 295 | 92 |
| multi_hop | 1985 | 4117 | 1353 | 2004 | 1069 | 278 |
| partial | 36 | 389 | 52 | 202 | 17 | 45 |
| unanswerable | 3053 | 9431 | 2247 | 8240 | 144 | 2121 |
| false_premise:no | 822 | 2507 | 852 | 1472 | 34 | 807 |
| false_premise:correctable | 124 | 402 | 110 | 224 | 83 | 10 |

**Cells under 3x headroom**

| Split | Type | Group | Target | Valid | Multiple |
|---|---|---|---|---|---|
| train | partial | AD | 9 | 24 | 2.7x |
| train | partial | HD | 4 | 11 | 2.8x |
| heldout | partial | AD | 5 | 12 | 2.4x |

Partial AD lost candidates to the HPO filter (AD's phenotype lists include
inheritance and onset terms). Every cell still fills in pass 1.

### Feasibility under the revision 2 rules

Same store and tool config, 2026-11-19, with every mention typed, phenotype names
in lowercase and the per-kind false-premise cap (`--rules current`, the default).
The previous filtered result is kept as
`$NGQ_DATA_DIR/derived/qgen_feasibility_rev1.json`; `--rules revision1` reproduces
it exactly.

| | Revision 1 | Revision 2 |
|---|---|---|
| Valid candidates | 49,003 | 49,003 |
| Name-rule drops | 18 (9 `not_first_of_type`, 9 `shared_name_in_type`) | 18 (same) |
| Valid candidates with every named entity typed, per class | 13% `yes`, 8% `partial`, 11% `correctable`, 6% `no` | 100% in every class |
| Targets met in pass 1 (both splits) | yes | yes |

- The pool is the same: an entity passes the type-qualified rule exactly when it
  passed one of the earlier two forms, and item IDs don't depend on wording. Only
  the question text changes.
- **False-premise relation mix** (selected; cap per kind 4 in train, 2 in
  held-out):

  | Kind | Relation | Train, rev. 1 | Train, rev. 2 | Held-out, rev. 1 | Held-out, rev. 2 |
  |---|---|---|---|---|---|
  | correctable | `disease_phenotype_negative` | 7 | 4 | 3 | 2 |
  | correctable | `indication` | 2 | 3 | 0 | 0 |
  | correctable | `contraindication` | 1 | 2 | 2 | 2 |
  | correctable | `disease_disease` (reversed subtype) | 0 | 1 | 0 | 1 |
  | no | `disease_phenotype_positive` | 4 | 4 | 1 | 1 |
  | no | `disease_protein` | 5 | 4 | 2 | 2 |
  | no | `exposure_disease` | 1 | 2 | 1 | 1 |
  | no | `disease_disease` | 0 | 0 | 1 | 1 |

- Cells under 3x headroom are unchanged (partial AD in both splits, partial HD in
  train).

## Item set

Generated on 2026-11-19 by `scripts/data/question_set.py generate` (generator
1.1.0, commit `e6560ee`, clean tree) on store `primekg-2.1-6d869fa6bb10`, with the
settled decisions above, revision 2 included. It replaces the first set (generator
1.0.0, commit `48082fb`; train `13c94dd5…`, held-out `7357994b…`) in the same
directory. Not committed: the files live in
`$NGQ_DATA_DIR/items/primekg-2.1-6d869fa6bb10/`.

| File | Items | SHA-256 |
|---|---|---|
| `train.jsonl` | 200 | `caf0480d7ce00741b0bf851a844c1e7920e3979f81144c784d3b7de936831d1c` |
| `heldout.jsonl` | 100 | `af7cac000eb7cc879b354a52aa63ce6f42b83bb101a84da86201f7eab6ee0fe7` |
| `manifest.json` | | `bb57005e0444772e2c2c43cb50bccdf4e34e6b6516d74416c359ea3b38af291a` |
| `train_spot_check.md` | 20 (train) | seeded sample for domain review (`spot-check`), refreshed |

**Counts** (from the manifest; held-out by aggregate only)

| Type | train | held-out |
|---|---|---|
| single_hop | 60 | 30 |
| multi_hop | 50 | 25 |
| partial | 30 | 15 |
| unanswerable | 40 | 20 |
| false_premise:no | 10 | 5 |
| false_premise:correctable | 10 | 5 |

| Group | train | held-out |
|---|---|---|
| AD | 60 | 29 |
| PD | 40 | 20 |
| ALS | 32 | 16 |
| FTD (with FTD/ALS) | 28 | 15 |
| HD | 24 | 13 |
| DLB | 16 | 7 |

- Every (type, group) cell is at its quota in both splits, filled in pass 1: no cell
  spilled. Group totals differ from the plain percentages because quotas are
  apportioned per type (largest remainder), and a split's type targets are small.
- No relation key is over 40% of its type, or of its kind for false premises. The
  largest shares: train `multi_hop|ppi_assoc` 20 of 50; held-out
  `multi_hop|ppi_assoc` 10 of 25. False-premise mix: see "Feasibility under the
  revision 2 rules".
- Every item names every entity with its type (300 of 300; the manifest's
  `typed_mentions|answerable` equals `answerable` in both splits).
- The manifest also records each file's SHA-256 and count, counts per split × type ×
  group × relation key, the generator version and commit, the split seed and share,
  the `build_id`, the store's `build.json` SHA-256
  (`ff0fc5d4de74f378f83eaf13312e485e4578e685e29d9ffbdef2b5462cbf1c0c`), the template
  catalog's SHA-256, the filters, plan and tool config, the selection counts
  (including the relation mix and caps per type) and the pool's funnel.

**Checks on the written set**

- `check` (both splits) and `check-heldout`: every item check passes (schema, split
  and `build_id`, template, ID and split hash, gold points stated by store edges,
  flags, route within budget and meeting its requirements when re-run against the
  real tool bodies, full-graph conditions, premise and gap absence, names in the
  set's naming mode, leaks, domain filters); file hashes and counts match the
  manifest; no item ID, fact or named-entity set is shared across the splits.
- Gold completeness (the check was added on 2026-11-20, after this set was
  generated; no item changed): `check --split train` passes `gold_complete` on all
  200 train items, and `tests/data/test_real_train_gold.py` agrees (by family:
  `fp_contra` 9, `fp_evid` 10, `fp_reversed` 1, `mh_bridge` 50, `pa_evid2` 24,
  `pa_list2` 6, `sh_evid` 17, `sh_evid2` 12, `sh_rel` 31, `un_bridge` 32, `un_rel` 8;
  train has no `sh_list`, `sh_list2` or `un_list` items). Not run on held-out.
- Determinism: a second run gives a byte-identical manifest (so identical item-file
  hashes).
- The taskset loads all 200 train items against the real store, with the
  `build_id` check passing; the fixture store is refused. A `scripts/run.sh
  --dry-run True` resolves the real paths (a dry run doesn't load items).
- Route replay (train only), `tests/integration/test_real_train_replay.py`: an
  honest scripted policy per train item (search each named entity, make each
  recorded `get_neighbors` call, then claim every gold point citing its edge, flag
  the gaps on partial items, abstain on `no` items), run through `scripts/run.sh`
  against the stub endpoint on the real store. It passes: all 200 train items reach
  reward 1, and on each of the 150 items with gold the route's own calls retrieve
  every gold edge within the 8-call budget (the 50 `no` items show only that
  abstaining scores 1). Held-out items are not replayed.

**Regenerate** (deterministic; refuses to overwrite without `--force`):

```bash
uv run python scripts/data/question_set.py generate --force
uv run python scripts/data/question_set.py check --build-id primekg-2.1-6d869fa6bb10
uv run python scripts/data/question_set.py spot-check --build-id primekg-2.1-6d869fa6bb10
NGQ_ITEMS_DIR="$NGQ_DATA_DIR/items/primekg-2.1-6d869fa6bb10" \
  uv run pytest -q tests/integration/test_real_train_replay.py
```

The replay test is opt-in: it is skipped unless `NGQ_ITEMS_DIR` names an item-set
directory (store: `NGQ_STORE_DIR`, default `$NGQ_DATA_DIR/store/primekg-neuro-c95`).
It reads `train.jsonl` only, through the viewer's train-only loader, and refuses a
file path or a train file holding a held-out record.

Commands take an items directory or a build ID, never a held-out file path, so the
project's deny rules don't block them. `view --build-id <id> [--qtype ...]
[--template ...] [--group ...] [--contains ...]` renders train items; it refuses
the held-out file and any file holding a held-out record.

**Point a run at the items** (the committed `configs/local.toml` stays on the
fixtures):

```bash
scripts/run.sh -m <model> --client.base-url <url> --client.api-key-var <VAR> \
  --env.taskset.items_path "$NGQ_DATA_DIR/items/primekg-2.1-6d869fa6bb10/train.jsonl" \
  --env.taskset.tools.store_path "$NGQ_DATA_DIR/store/primekg-neuro-c95"
```

The taskset refuses the held-out split unless `--env.taskset.split heldout` comes
with `--env.taskset.allow_heldout true`, and refuses items from another store build.

**Open questions after the first set, and how they were settled** (Kaitlin,
2026-11-19):

- Typed mentions were more common on answerable items: settled by typing every
  mention (revision 2).
- Correctable premises leaned on "phenotype recorded as absent" (7 of 10 in train):
  settled by the per-kind cap (now 4 of 10).
- Capitalized HPO names mid-sentence: settled by lowercase phenotype names.
- Shared-neighbor bridges can pair unrelated diseases: kept; the smoke run reports
  by template family.
- The relation cap rounds down (`int(0.4 × target)`, 0 for targets under 3; the
  smallest cap at 200 + 100 is 2, held-out per false-premise kind): left as is.
