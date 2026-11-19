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
