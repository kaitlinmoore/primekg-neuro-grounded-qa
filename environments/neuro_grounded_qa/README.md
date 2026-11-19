# neuro-grounded-qa

**Status: in progress.** The environment runs end to end on synthetic fixtures. The question set isn't built yet, and there are no results. Don't use it for evaluation yet.

A [Verifiers](https://github.com/PrimeIntellect-ai/verifiers) (v1) environment for answering neurodegeneration questions (Alzheimer's, Parkinson's, FTD, ALS, Huntington's and Lewy body dementia) only from evidence the model retrieves from a PrimeKG subgraph, and for abstaining when that evidence can't support an answer. The reward is computed in code from the trace, with no LLM judge. The project's headline deliverable is a reward-hacking audit of that reward.

## Tools

The model reaches the graph only through three MCP tools. Results are JSON, capped in count and size, and each rollout has a call budget.

- `kg_search_entities(text)`: find entities by name. Returns graph IDs. Entities aren't evidence.
- `kg_get_neighbors(entity_id, relation)`: the edges touching an entity, optionally of one relation.
- `kg_get_edge(edge_id)`: one edge by its evidence ID (such as `E:000001`).

Each edge has an ID, a subject, a relation, an object and a `directed` flag. The intended harness is verifiers' `null` harness, so the model gets these tools and no shell.

## Output format

The final reply is exactly one JSON object, optionally inside one fenced block. It's either an answer:

```json
{"claims": [{"subject_id": "...", "relation": "...", "object_id": "...", "cites": ["E:..."]}],
 "answer_text": "...", "unsupported_parts": ["..."]}
```

or an abstention:

```json
{"abstain": true, "reason": "..."}
```

Claims use graph IDs and relation labels copied from tool results. A cite counts only if it was returned by the model's own tool calls in that rollout. A claim with any other cite counts as unsupported.

## Reward (in brief)

The reward combines four parts:
- **Format gate:** an unparseable reply scores 0.
- **Coverage:** the share of the gold facts stated by supported claims.
- **Penalty:** a penalty for unsupported facts.
- **Answerability:** credit for abstaining on unanswerable items, and for abstaining or correcting on false-premise items.

The full definition is `docs/reward_spec.md` in the source repository.

## Data

No graph data ships in this package. Several PrimeKG sources are non-commercial or license-gated, so the evidence store and question items are built locally from the downloaded graph. The defaults point at the source repository's synthetic test fixtures, which aren't part of this package. Outside the repository, set `items_path` and `tools.store_path`.

Source repository: https://github.com/kaitlinmoore/primekg-neuro-grounded-qa
