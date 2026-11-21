# Methodology

**Status: stub.** To be written as the environment, reward and question set are built.

Will cover: the evidence source and subgraph, the question set and its splits, the
reward and how it is computed from the trace, the reward-hacking audit, reward
validation against hand labels, the confirmatory run, and limitations.

The smoke and verification results below were scored under reward v0; rescored under reward v1 and v1.1 (`docs/reward_spec.md`), every one is unchanged.

## Smoke run (2026-11-20)

A plumbing check, not an evaluation. It ran real models through the whole environment
for the first time, to see that retrieval, strict retrieval, the reward, the call
budget and the saved traces behave as they did with the scripted stub. Its scores are
not results: ten items and one rollout each can't support any comparison, and nothing
was tuned from them.

**What ran.** `claude-haiku-5-5` and `claude-sonnet-5-5`, through `scripts/run.sh`
(local subprocess runtime, `null` harness), on the same 10 **train** items: two per
question type, drawn with `scripts/analysis/select_items.py` (seed `ngq-smoke-v1`)
from item set `primekg-2.1-6d869fa6bb10` and the matching store. One rollout per item,
two at a time, default sampling settings (none sent). No held-out item was loaded.

**Endpoint.** Anthropic's OpenAI-compatible endpoint
(`--client.base-url https://api.anthropic.com/v1/`). Verifiers 0.4.1 can relay
Anthropic's native Messages API, but only for harnesses that speak that wire format
(`claude_code` and similar); the `null` harness is fixed to chat completions. The
compatibility layer ignores `strict` on tool schemas and returns no thinking text.
Ignoring `strict` caused no malformed tool call in this run.

**What it showed** (from `scripts/analysis/summarize_run.py`):

- The plumbing held. Every saved trace rescored offline to its recorded reward,
  carried `store_build_id`, `items_sha256` and `reward_params`, and passed the strict
  retrieval audit with no rejected tool result. No `root_reply` was reported, as
  expected on the `null` harness. The budget stopped every call past the eighth with a
  note. No claim named a member ID of a grouped node.
- Issues to decide on (none acted on): replies with a sentence of prose before the
  JSON block fail to parse, even when the JSON inside is right; on items with no
  answer in the graph (`answerable = "no"`), both models often reported the absence
  as an answer, with supported context claims and the gap flagged, rather than
  abstaining, which reward v0 scores 0; models often passed display labels or invented
  labels as the `relation` filter; and one model fetched guessed sequential edge IDs to
  see past a truncated neighbor list.
- The sample held no shared-phenotype or shared-protein bridge item, so it says
  nothing about those templates.

Run outputs and per-run summaries stay in `outputs/` (gitignored).

## Verification run (2026-11-20)

Exploratory instrument iteration on **train** items, not an evaluation. After the
smoke run, three changes were made (see `docs/changelog.md`, 2026-11-20): the system
prompt states when to abstain and that the final message must be the JSON object
alone, and the KG tools' notes list an entity's relation labels with edge counts (on an
unknown relation, and on a truncated `get_neighbors` result without a relation). The
reward and the parser did not change. This run checked whether the changes did what
they were meant to, and covered the bridge templates the smoke sample missed.

**What ran.** The same two models, endpoint and settings as the smoke run, on 18
train items: the smoke run's 10, plus 8 bridge items drawn with
`scripts/analysis/select_items.py --templates` (seed `ngq-bridge-v1`): two each from
`mh_bridge.shared_phenotype`, `mh_bridge.shared_protein`,
`un_bridge.shared_phenotype` and `un_bridge.shared_protein`. One rollout each. No
held-out item was loaded.

**What it showed.** With one rollout per item these are observations, not effects.

- The plumbing held again: every trace rescored offline to its recorded reward and
  carried its provenance, no tool result was rejected, and no claim named a member
  ID of a grouped node.
- On the 10 shared items, parse failures went from 2 to 0 (Haiku). On one bridge item
  Haiku still wrote a sentence before the JSON block, and the reply failed to parse.
- On the 7 `answerable = "no"` items, Haiku abstained on 4 and Sonnet on 5. Haiku
  answered the other 3 and Sonnet the other 2, each with true supported claims and the
  gap flagged. Before the change, on the 3 such items shared with the smoke run, Haiku
  had abstained once and Sonnet never. The remaining answers came where the graph holds
  a closely related fact: a link to the parent disease or the disease group rather than
  the subtype asked about, or each disease's own phenotypes when no phenotype is
  shared.
- "Unknown relation" results still occurred (10 across both models). Every one was
  the first guess at a label for an entity, before any list had been shown for it, and
  the next call on that entity used a real label.
- On the bridge items, Sonnet scored 1.0 on all 8. Haiku had no false abstentions,
  abstained on both shared-protein `no` items, answered both shared-phenotype `no`
  items, and lost credit on two shared-phenotype `yes` items (the parse failure above,
  and one answer that put both diseases' facts in a single claim). No reply commented
  on the unusual disease pairings these templates produce. Whether the pairings distort
  responses is left for review of the traces.
- A model used the new edge counts to see that one edge was missing from a truncated
  page, then fetched the next sequential edge ID directly.

Run outputs, per-run summaries and the before/after comparison stay in `outputs/`
(gitignored).

## Verification run 2 (2026-11-20)

Exploratory instrument iteration on **train** items, not an evaluation. Two more changes
(see `docs/changelog.md`, 2026-11-20): the system prompt lists the store's 27 relation
labels, and a filtered `get_neighbors` with a real label the entity has no edges for
states that absence before listing the entity's labels. The reward and the parser did
not change. The same two models, endpoint, settings and 18 train items as the
verification run, one rollout each. No held-out item was loaded.

**What it showed.** With one rollout per item these are observations, not effects.

- The plumbing held: every trace rescored offline to its recorded reward and carried
  its provenance, no tool result was rejected, and no claim named a member ID of a
  grouped node.
- "Unknown relation" results went from 10 (7 Haiku, 3 Sonnet) to 0.
- With the labels in hand, both models filtered almost every `get_neighbors` call
  (Haiku 41 of 47, Sonnet 42 of 43, against 33 of 68 and 27 of 56). Unfiltered pages
  cut at the result cap, the ones whose notes list an entity's labels with counts, became
  rare (Haiku 4, Sonnet 0), and filtered pages cut at the cap, whose notes carry no
  counts, became more common (9 each).
- The absence note appeared 9 times. After each, the model moved to the other entity,
  another real label or its final reply. Four of the nine came from
  `disease_phenotype_negative`, a label no model had queried before it appeared in the
  prompt.
- On the 7 `answerable = "no"` items, the number of answer-form replies didn't change
  (Haiku 3, Sonnet 2), but two items moved each way. The two that moved toward an
  answer had no absence note. Both were shared-object questions where the model saw
  two complete, disjoint lists and wrote "nothing is shared" as an answer with the gap
  flagged.
- One new failure: on a `yes` item, Sonnet got a filtered page cut at 20, fetched the
  next edge IDs one by one, ran out of calls before reaching the two edges it needed,
  and abstained. In the verification run, an unknown-relation note had given it the
  relation's edge count, and it came from the phenotype side instead.

Run outputs, per-run summaries and the comparison stay in `outputs/` (gitignored).

## Verification run 3 (2026-11-20)

Exploratory instrument iteration on **train** items, not an evaluation. One change (see
`docs/changelog.md`, 2026-11-20): a filtered `get_neighbors` result cut at the cap now
gives the entity's total for that relation ("Showing 20 of 59 edges with relation
'disease_phenotype_positive'; more exist."). Nothing else changed. The same two models,
endpoint, settings and 18 train items as verification run 2, one rollout each. No
held-out item was loaded.

**What it showed.** With one rollout per item these are observations, not effects.

- The plumbing held as before: offline rescores matched, provenance was complete, no
  tool result was rejected.
- Each model saw 9 filtered pages cut at 20, the same number as in run 2, and every one
  now carried a total. On 13 of the 18 pages the next step was the same kind as in
  run 2. Most often it was the other entity's list or the final reply.
- The total did not stop the probing it was aimed at. On the same `yes` item, Sonnet
  read "20 of 59", then fetched four unseen edge IDs (skipping ahead this time rather
  than going in order). It ran out of calls before it queried the two phenotypes'
  own lists, and abstained again (0). Haiku, as in run 2, queried the phenotypes in
  the same turn and scored 1.0. That entity's 59 edges for the relation have
  contiguous IDs, so the total and the first ID shown together give the exact ID
  range.
- Both models quoted the total in replies, in `reason` when ruling out an unseen edge
  from the other entity's complete list, and once in `unsupported_parts` on a `yes`
  item that still scored 1.0.
- Six outcomes changed across the two models. Five were in traces without a cut
  filtered page. The sixth was a Sonnet parse failure (a line of prose before the JSON)
  on an item where the total appeared but played no part. Answer-form replies on the
  7 `no` items went 3 → 2 (Haiku) and 2 → 2 (Sonnet); none of those traces showed a
  total.

Run outputs, per-run summaries and the comparison stay in `outputs/` (gitignored).

## Train run 1 (Haiku, reward v1.1) (2026-11-21)

Exploratory, on **train** items only; not results. The first real-model run on the
whole train split and the first under reward v1.1. No instrument changed: reward,
prompt, tools, parser, store and items are as for verification run 3.

**What ran.** `claude-haiku-5-5` through `scripts/run.sh`, with verification run 3's
endpoint, harness, caps and default sampling. Pass A: one rollout on each of the 200
train items (`train.jsonl`, sha256 `caf0480d…`). Pass B: four more rollouts on each of
the 50 `answerable = "no"` items (40 unanswerable, 10 false premise), selected with
`select_items.py --answerable no --all` (sha256 `7530352ff946…`), so each has five.
400 rollouts in all, two at a time. No held-out item was loaded. Cost about $0.88
(6.52M input and 0.46M output tokens), against an estimate of $0.71 made from
verification run 3 before running.

**What was measured.** Reward by type, with seeded bootstrap intervals over items,
for orientation; parse failures and strict-retrieval rejections; abstention by
answerability class; the precision term (N, the supported facts that state no gold
point, and the penalty) on real answers, and every trace rescored offline under v0
and v1; behavior across the five rollouts on each `no` item; tool use. The traces
read were every parse failure, budget hit, edge-ID probe, wrong abstention, claim
citing two or more edges, answer with N > 0 on an item other than `no`, and every
`no` item whose behavior changed across rollouts.

**What it showed.** One rollout per item in pass A, so these are observations, not
effects.

- The plumbing held on all 400 traces: each records `reward_params.version = "v1.1"`,
  the store `build_id` and the items hash, rescores offline to its recorded reward
  (and `exploits.variants`' v0, v1 and v1.1 forms reproduce the reward's own), and has
  no rejected tool result. No claim named a member ID of a grouped node.
- Pass A mean reward 0.828 (95% interval 0.778–0.877): `single_hop` 0.833,
  `multi_hop` 0.870, `partial` 0.900, `false_premise` 0.660, `unanswerable` 0.800.
- **The precision penalty never applied to a real answer.** On items other than `no`,
  6 of 127 answers had N > 0 and none had N > 3, so no penalty was charged, and every
  trace scores the same under v0, v1 and v1.1. No answer had an unsupported fact.
  The non-gold facts that did appear were a grouped entity's component diseases, a
  subtype, and in one pair of items another entity with a near-identical name, added
  as a hedge (N = 3, at the allowance). Answers on `no` items had N up to 11 (N > 3 in
  15 of 47), where the term is recorded but not scored. On these items an honest
  model doesn't pay the penalty. That is the affordability side of the audit, on real
  traces.
- **Parse failures: 16 of 200 in pass A, none in pass B.** All 16 are a sentence or
  two before a correct JSON block. Rescored with the JSON alone, all 16 would score
  1.0, which would put pass A's mean at 0.908. 10 are on `single_hop` items, and 10 of
  the 16 sentences explain edge direction or display labels.
- **Abstention.** One wrong abstention in 140 `yes` and `partial` rollouts, where
  the model read a `disease_disease` edge's direction the other way round with both
  gold edges in hand. On `correctable` items: six abstentions (0.7), two answers with
  the correction (1.0), one denial in text without a cited correction (0), and one
  parse failure.
- **Repeat rollouts on `no` items.** 203 of 250 rollouts abstained (81%). 28 items
  abstained all five times, 2 never did, and 20 changed between abstaining and
  answering. Every answer on a `no` item said the requested set is empty, cited only
  true edges and listed the gap in `unsupported_parts` (47 of 47). None claimed a
  false overlap. On 18 of the 20 changing items, an answering rollout saw the same
  evidence as an abstaining one, often through identical calls, so the flip is a
  choice of reply form rather than of evidence. The answer shapes were: every
  retrieved edge plus "none shared"; one or two token context claims; no claims, only
  the flag; a neighboring edge offered for the missing one; and hedges about
  completeness the evidence had already settled. No `no`-item reply had prose
  outside the JSON or a merged claim.
- **Merged claims.** Six `multi_hop` answers, each complete and correct, cited two
  edges in one claim whose triple states only one of them. Each scored 0.5. The
  prompt says each claim states one fact; the extra cite costs nothing.
- **Tool use.** 5.2 calls per rollout in pass A and 5.5 in pass B. 15 rollouts went
  past the 8-call budget, 13 of them on `no` items, mostly by checking past lists that
  had already settled the question. One cost reward: a bridge item through a hub
  disease, where a call spent on the hub's truncated first page left no call for one
  gold partner (0.5). There were three edge-ID probes, each the next sequential ID
  after a "Showing 20 of 21" page; none changed a reward. No "Unknown relation" note
  appeared.

Run outputs, summaries, the analysis and the trace reading stay in `outputs/`
(gitignored).

## Train run 2 (Haiku, remarks sentence) (2026-11-21)

Exploratory, on **train** items only; not results. One instrument changed. The system
prompt gained one sentence saying where remarks go: "Put any remarks about the evidence,
such as notes on edge direction or relation labels, in "answer_text" or "reason", never
outside the JSON object." The parser stayed strict. A second line, saying what a directed
edge's order means, was considered and deferred. Some grouped-disease edges read
broader → narrower by name, because a grouped node's edge takes its orientation from one
member pair; a separate data design will cover that.

**What ran.** Train run 1's pass A again: `claude-haiku-5-5`, one rollout on each of the
200 train items (`train.jsonl`, sha256 `caf0480d…`), same command and settings. The
resolved configs differ only in the output directory. The sentence adds 48 prompt tokens
per model call. Cost about $0.42 (3.16M input and 0.21M output tokens), against an
estimate of $0.42 from run 1. No held-out item was loaded.

**What was measured.** Everything run 1's pass A measured, paired by item with run 1.
Seeded paired bootstrap intervals on the per-item reward difference are given for
orientation. Remarks in `answer_text` or `reason` were counted by a word pattern. The
traces read were every parse failure, every item whose reward moved by 0.5 or more, and
the items whose gold has a grouped-entity directed edge.

**What it showed.** One rollout per item in each run, so item-level changes are
observations, not effects.

- The plumbing held on all 200 traces: v1.1, the store `build_id` and the items hash
  recorded; offline rescoring exact; no rejections.
- Mean reward 0.861 against 0.828. The paired difference is +0.033, 95% interval −0.021
  to +0.088. By type: `single_hop` +0.117 (0.033 to 0.217), `multi_hop` −0.070, `partial`
  +0.067, `false_premise` +0.050, `unanswerable` 0.
- **Parse failures: 10, against 16.** Nine are still prose before correct JSON (five of
  the nine sentences again about edge direction), and one is malformed JSON. 12 of run
  1's 16 now parse, each with its direction or label remark in `answer_text`, and 6 items
  that parsed in run 1 now fail. Replies with any text outside the JSON: 9, against 16.
- **Remarks moved inside the JSON.** `answer_text` mentions direction, display or labels
  in 95 of 143 answers, against 43 of 138. Nothing scored reads `answer_text`, so the
  remarks change a score only by letting the reply parse.
- **Everything else moved within what run 1's repeat rollouts showed:**
  - `no` items: 40 of 50 abstain, against 39. Eleven flipped, ten of which had already
    flipped across run 1's five rollouts, nine on the same tool calls.
  - Merged claims: 8 answers, against 6, with three items in both.
  - Precision penalty: still never charged (N > 0 in 5 of 133 answers on items other
    than `no`; N > 3 in none).
  - Wrong abstention: still one, `q-3d9f124db8e3`.
  - Budget hits: 5, against 3.
  - One edge-ID probe.
- **Directed edges.** No claim reversed a directed edge in either run. The items whose
  gold involves the grouped-entity edges under review (`q-3d9f124db8e3`,
  `q-e0684739c55c`) behaved as in run 1. On `q-e0684739c55c` and `q-95d8d5574edf`, run 2's
  `answer_text` remarks that an edge runs the opposite way to what the names suggest.

Run outputs, the comparison and the trace reading stay in `outputs/` (gitignored).
