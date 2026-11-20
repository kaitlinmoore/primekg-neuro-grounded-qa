# Methodology

**Status: stub.** To be written as the environment, reward and question set are built.

Will cover: the evidence source and subgraph, the question set and its splits, the
reward and how it is computed from the trace, the reward-hacking audit, reward
validation against hand labels, the confirmatory run, and limitations.

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
