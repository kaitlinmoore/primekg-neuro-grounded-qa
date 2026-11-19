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
  mapping in `scripts/data/load.py`. The mapping follows the PrimeKG paper as
  recalled and was not re-checked against the paper in this run. Every
  `disease_protein` edge counts as DisGeNET, which is an upper bound if the
  paper names further sources for that relation.
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
| R3 two-hop path edges from a restricted source | 85.9% (1,739) | 69.6% (87,246) | 41.4% (2,190) | 29.8% (2,909) | 41.8% (19,784) |
| R3 subgraph edges from a restricted source | 84.7% (1,811) | 69.6% (87,246) | 41.6% (2,216) | 29.8% (2,919) | 41.8% (19,784) |

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
| R3 two-hop path edges from a restricted source | 86.4% (1,767) | 70.1% (86,474) | 41.0% (2,120) | 30.4% (2,823) | 41.8% (18,975) |
| R3 subgraph edges from a restricted source | 85.2% (1,767) | 70.1% (86,474) | 41.0% (2,120) | 30.4% (2,823) | 41.8% (18,975) |

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
| `disease_phenotype_positive` | 67 | 10,228 | 2,059 | 67 | HPO annotations |
| `anatomy_protein_present` | 0 | 8,603 | 1,959 | 0 | Bgee |
| `drug_effect` | 299 | 7,195 | 623 | 0 | SIDER |
| `protein_protein` | 91 | 7,106 | 741 | 0 | PPI resources (unverified) |
| `contraindication` | 59 | 4,011 | 226 | 58 | DrugCentral |
| `disease_protein` | 211 | 3,927 | 834 | 205 | DisGeNET |
| `bioprocess_protein` | 0 | 3,042 | 503 | 0 | GO annotations |
| `drug_protein/target` | 17 | 1,560 | 106 | 0 | DrugBank |
| `cellcomp_protein` | 0 | 1,015 | 229 | 0 | GO annotations |
| `molfunc_protein` | 0 | 760 | 150 | 0 | GO annotations |
| `drug_protein/enzyme` | 34 | 660 | 56 | 0 | DrugBank |
| `phenotype_protein` | 25 | 557 | 61 | 0 | HPO annotations |
| `pathway_protein` | 0 | 480 | 62 | 0 | Reactome |
| `exposure_protein` | 21 | 316 | 79 | 0 | CTD |
| `phenotype_phenotype` | 21 | 306 | 149 | 0 | HPO |
| `disease_disease` | 14 | 304 | 304 | 11 | MONDO |
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
| edges on two hop paths | 9,773 | 15.0% | 6.4% | 8.4% | 0.0% | 29.8% |
| all subgraph edges | 9,790 | 14.9% | 6.4% | 8.5% | 0.0% | 29.8% |

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
