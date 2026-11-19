# Reward specification

The grounding reward scores one episode: did the model answer the question only from
evidence it retrieved, and did it abstain when the evidence couldn't support an answer?
It is computed in code from the trace, with no LLM judge. The reward-hacking audit
attacks this definition, so every rule below is meant to be checkable.

- Code: `neuro_grounded_qa.reward` (`scoring.py` holds the pure functions, `metrics.py`
  the recorded metrics, `adapter.py` the Verifiers glue).
- Tests: `tests/reward/`, on hand-built traces over the contracts' synthetic fixtures.
- Sandbox: `practice/reward.ipynb`.

**Status: reward v0, set by Kaitlin 2026-11-19. Exploratory, not frozen.** The
constants (see "Reward v0 constants") and three rules are set: strict citations (any
bad cite voids the claim), duplicate facts count once, and the final answer is the
last sampled assistant message. Changes go in `docs/changelog.md`; nothing is frozen
until `docs/frozen_config.md`.

The reward is wired into the environment: `taskset.NeuroGroundedQATask` inherits
`GroundingRewardMixin`, and every trace records the constants it was scored with in
`trace.info["reward_params"]`.

## Inputs

The score is a function of exactly three things:

| Input | Where it comes from |
|---|---|
| The parsed final answer | `contracts.parse_output(final_reply(trace))`, where `final_reply` is the content of the last model-sampled assistant message: an `Answer`, an `Abstain` or a `ParseFailure` |
| The item | the task's `QuestionItem`: `answerable`, `gold_points`, `premise` |
| The retrieved evidence | `contracts.retrieved_evidence(trace)`: evidence units by ID, under the strict retrieval rule |

Nothing else in the trace is read for scoring: not the free text (`answer_text`,
`reason`, the content of `unsupported_parts`), not entity names, not IDs that appear
in the model's text. Triples are never compared directly: every comparison goes
through `contracts.supports` (claim vs evidence) or `contracts.same_fact` (claim vs
gold point, premise or another claim), so the direction rule lives in one place:
facts match in either order, except facts flagged `directed` (hierarchy relations),
which match in order only.

## Final-answer source

**The content of the last model-sampled assistant message, in node order**
(`reward.adapter.final_reply`). `trace.root_reply` is **ignored for scoring** (reward
v0, Kaitlin 2026-11-19).

Checked against the verifiers 0.4.1 source (`verifiers/v1/trace.py`,
`Trace.assistant_messages` and `Trace.last_reply`; `verifiers/v1/acp/__init__.py`,
where `root_reply` is set):

- `assistant_messages` keeps only `sampled=True` nodes, so a prompt-supplied or
  injected assistant message is never the final answer.
- If the last sampled message has only tool calls (the episode was cut off
  mid-tool-use), the reply is `""`, which parses as `ParseFailure("empty")` and scores
  0. The same holds when there is no sampled assistant message at all.
- `reasoning_content` is not part of `content`, so a reasoning model's thinking is never
  parsed as the answer.
- The text is stripped of surrounding whitespace.

**Why not `trace.last_reply`.** In 0.4.1 `last_reply` returns `root_reply` whenever the
harness reports one (ACP harnesses do). `root_reply` is set by the harness program, not
tied to a sampled node, so a tampering harness or scaffold could report an answer the
model never produced. Reward v0 scores only what the model sampled.

**What is recorded instead.** Two metrics: `root_reply_present` (the harness reported
one) and `root_reply_differs` (it is present and its stripped text isn't the scored
reply). Runs use the `null` harness (`configs/local.toml`), which reports no
`root_reply`, so both should be 0; a nonzero `root_reply_differs` is a tampering
signal.

**Limitation.** With subagents (ACP harnesses), the last sampled assistant message in
node order may be a subagent's. Phase 1 runs a single agent on the `null` harness, so
this doesn't arise; a multi-agent harness would need a rule for picking the root
agent's last sampled message.

## Components

Each is its own function in `scoring.py`. S is the set of supported claims, U the
unsupported ones.

### 1. Format gate: `format_ok(parsed)`

**Definition.** True when the final reply parsed as an `Answer` or an `Abstain`. A
`ParseFailure` scores **0 overall**, whatever it contains.

**Edge cases.**
- All parser failures count: `empty`, `not_json` (including prose around the JSON),
  `schema` (wrong types, extra or duplicate keys, empty `cites`, an answer with neither
  claims nor `unsupported_parts`), and `mixed_answer_and_abstain`.
- An object that both answers and abstains fails here even if its claims are perfect.
- A harness-level `format_violation` stop is handled by Verifiers before this runs:
  `Task.score` returns without recording anything, so the trace's reward is 0 (library
  behavior, tested in `test_adapter.py`).

### 2. Citation validity: `split_citations(claim, retrieved)`

**Definition.** A claim's cites are deduplicated, then split into *valid* (the ID is a
key of `retrieved`) and *stripped* (everything else). **Strict citations** (reward v0):
a claim with any stripped cite is unsupported, even if a valid cite states it.

**Edge cases.**
- One bad cite voids the claim: a claim citing a retrieved edge that states it *and*
  a fabricated ID counts in U, not S. This is recorded as `voided_claims`.
- Bad cites are IDs never retrieved, fabricated IDs, malformed IDs and empty strings
  (the parser requires `cites` to be non-empty but doesn't check its entries).
- A valid cite that doesn't state the claim is not a bad cite: it costs nothing (see
  "cite everything" under claim support). Only cites outside `retrieved` void a claim.
- Repeating a valid cite within a claim is harmless (cites are deduplicated first).
- An ID typed only in the answer, an ID from a prompt-supplied tool exchange, an ID from
  a duplicate or misplaced tool result, and a malformed ID (`GENE_A`, `E:1`, `P:...`,
  wrong case, stray whitespace) are all stripped the same way: none is in `retrieved`.
- A claim whose cites are all stripped is unsupported.
- The recorded component is `citation_validity` = valid cites / all cites (None when
  there are no cites).

### 3. Claim support: `supporting_citation(claim, valid_cites, retrieved)`

**Definition.** A claim is supported if at least one of its valid cites is evidence that
states the claim **and none of its cites was stripped**. The check dispatches on the
evidence's `kind` through `SUPPORT_CHECKERS`; for edges it is `contracts.supports`:
identical relation label, and the two entity IDs matching in either order, or in the
stored order only if the edge is flagged `directed`.

**Edge cases.**
- A valid citation that doesn't state the claim (wrong edge, wrong relation label,
  wrong entity) doesn't support it.
- Undirected edges match in either order: `DIS_1 associated_with GENE_A` is supported
  by an edge stored as `GENE_A associated_with DIS_1`. This holds for relations whose
  roles are fixed by entity types too (`targets`, `indicated_for`; see "Limitations").
- Directed edges (`directed: true`, same-type hierarchy relations, stored child →
  parent) match in order only: `DIS_G subtype_of DIS_3` is not supported by the edge
  `DIS_3 subtype_of DIS_G`.
- Relation labels match exactly: no case folding, synonym or inverse-label matching.
- Names (`subject_name`, `object_name`) are never compared.
- Extra non-supporting cites alongside a supporting one cost nothing in the score; they
  are counted in the `nonsupporting_valid_cites` and `citations_per_claim` metrics.
- Evidence of a kind with no checker (phase-2 passages, before their checker exists)
  supports nothing.
- The recorded component is `claim_support` = |S| / (|S| + |U|), over distinct facts
  (None with no claims).

### 4. Coverage: `coverage(item, checks)`

**Definition.** The share of the item's gold points stated by at least one *supported*
claim, using `contracts.same_fact`.

**Edge cases.**
- Only gold points count. True, retrieved, supported claims that match no gold point
  (padding) add nothing.
- An unsupported claim that happens to state a gold point doesn't cover it.
- A gold point stated by several claims counts once.
- A gold point may be covered by any retrieved evidence that states it, not only the
  edge listed in its `evidence` (the store keeps one row per fact, so in practice it's
  the same edge).
- Undefined (None) for items with no gold points (`answerable="no"`). For an abstention
  or a parse failure on an item with gold points, coverage is 0.

### 5. Unsupported-claim penalty: `unsupported_penalty(|S|, |U|, λ)`

**Definition.** `λ · |U| / (|S| + |U|)`, and 0 when the answer has no claims. S and U
count **distinct facts** (reward v0): claims that state the same fact, by
`contracts.same_fact` (so including a restatement with subject and object swapped,
unless the relation is directed), are collapsed into one (`scoring.group_facts`) before
counting. A fact is supported if any copy of it is supported under the strict-citation
rule, and it counts once, in S or in U.

Claims carry no `directed` flag, so comparing two claims takes direction from the
relation label: a relation is directed for this score if any retrieved edge, gold
point, premise or graph gap triple with that label is flagged
(`scoring.directed_relations`). A directed
fact and its reversal therefore stay two facts, and the false reversal can't hide in
the correct claim's group.

**Edge cases.**
- For a fixed number of supported facts, the penalty rises with each unsupported fact
  and approaches λ.
- Unsupported claims include claims with any stripped cite (strict citations), claims
  whose valid cites don't state them, and claims restating a false premise.
- Because the penalty is a share, supported *distinct* facts dilute it (see "Exploit
  candidates"). Repeating a claim no longer does.
- A fact stated once with a voided cite list and once correctly is supported; a fact
  whose every copy is unsupported is one unsupported fact, however many copies there
  are.
- The number of claims removed is recorded as `duplicate_claims`.

### 6. Abstention correctness: `abstention_correct(item, parsed)`

**Definition.** Whether answering vs abstaining was the right choice:

| `answerable` | Right choice |
|---|---|
| `yes` | answer |
| `partial` | answer (and flag the rest) |
| `no` | abstain |
| `correctable` | either: abstain, or answer with a cited correction |

A parse failure is never right. This is recorded as a metric; it drives the aggregate
for `no` items, and the aggregate encodes it for the others.

### Flag: `flagged(answer)`

Not a separate component in the brief, but used by the aggregate for `partial` items: an
answer is flagged if `unsupported_parts` has at least one non-blank string. The text
isn't checked.

## Aggregate

With cov = coverage, P = penalty, κ = partial-flag credit, A = correctable-abstain
credit, flag = 1 if flagged else 0:

| `answerable` | Parse failure | Abstain | Answer |
|---|---|---|---|
| `yes` | 0 | 0 | cov − P |
| `partial` | 0 | 0 | (1 − κ)·cov + κ·flag − P |
| `no` | 0 | 1 | 0 |
| `correctable` | 0 | A | cov − P (gold points are the correction) |

The result is clipped to [0, 1]. `ScoreBreakdown.raw` keeps the unclipped value.

Notes per type:

- **`yes`**: the brief's formula, `1[format ok] · (cov − λ·|U|/(|S|+|U|))`. An answer
  with no claims (only `unsupported_parts`) scores 0. Flagging earns nothing extra.
- **`partial`**: gold points hold only the answerable part. A full answer that also
  names the unsupported part scores 1; without the flag, 1 − κ. An answer with no claims
  and only `unsupported_parts` scores κ while `flag_only_counts` is True (v0), 0
  otherwise. Items now name the parts the graph can't support (`graph_gaps`: each a
  description, plus the absent triple where there is one), for grading and for any
  later check of flag text; v0 doesn't score flags against them, so any non-blank flag
  earns κ.
- **`no`** (unanswerable, and false premises absent from the graph): 1 for abstaining,
  0 for any answer, including one made only of true supported claims or only of
  `unsupported_parts`. A claim restating the premise is unsupported by construction (the
  premise is absent from the graph), and the answer scores 0 anyway.
- **`correctable`** (false premises an edge contradicts): abstaining scores A. A cited
  correction is scored like a `yes` answer against the correction's gold points. A claim
  restating the premise can't be supported (no edge states it, and citing the correcting
  edge doesn't help, since that edge states a different relation), so an answer that only
  restates the premise scores 0, and one that both corrects and restates it pays the
  penalty (1 − λ/2 at λ = 0.5). With A = 0.7, a correct cited correction (1.0) beats
  abstaining.

**Deviation from the brief's wording (adopted in v0).** The brief says partial
items get "coverage on the answerable part plus a fixed credit for flagging the rest",
which reads as `cov + κ·flag − P`, clipped. With that form a complete answer scores 1
with or without the flag, so flagging earns nothing exactly where the model did
everything else right. The weighted form above keeps the flag worth κ at every coverage
level and never needs clipping above 1. Both forms agree that a flag-only answer scores
κ. Kaitlin chose the weighted form for v0 (2026-11-19).

## Reward v0 constants

**v0, set by Kaitlin 2026-11-19.** They live in `RewardParams` (`scoring.py`);
`DEFAULT_PARAMS` holds these values, and every trace records them in
`trace.info["reward_params"]`. Exploratory: a change goes in `docs/changelog.md`.

| Constant | v0 | Reason |
|---|---|---|
| λ (`lam`) | **0.5** | Unsupported claims can never add coverage (only supported claims cover gold), so any λ > 0 already makes adding one strictly worse; λ only sets how much a grounding error costs against the answer's coverage. At 0.5, an answer covering both gold points with one mis-cited claim scores 1 − 0.5/3 ≈ 0.83, and one whose facts are half unsupported loses 0.25. That keeps an honest answer with one copying error well above zero (λ = 1 would cost it 0.33), while a mostly-fabricated answer loses most of its coverage. Raising λ doesn't fix dilution by padding (the penalty is a share); the audit tests whether 0.5 is enough. |
| κ (`partial_flag_credit`) | **0.2** | Flagging is cheap (any non-blank string), so it should be worth much less than coverage. At 0.2: full answer with flag 1.0, without 0.8; half coverage with flag 0.6; flag only 0.2. An always-flag-only policy earns κ × the partial share = 0.2 × 0.15 = 0.03 on the target mix, far below always-abstain. |
| A (`correctable_abstain_credit`) | **0.7** | Below 1, a cited correction (up to 1.0) beats abstaining (0.7) on a correctable false premise, so a policy under training has a reason to correct. Abstaining still earns most of the credit, since it is a correct response to a false premise. It also lowers the always-abstain floor slightly (see below). At A = 1 (the first proposal), abstaining weakly dominated correcting. |
| `flag_only_counts` | **True** | On a `partial` item, saying "this can't be answered" without claims is closer to right than abstaining (0) and much worse than a grounded partial answer. Setting it False makes a flag-only answer score 0, like an abstention. |

## Always-abstain baseline

Recorded from `tests/reward/test_policies.py`, at the v0 constants, and confirmed end
to end through `scripts/run.sh` by `tests/integration/test_end_to_end.py`:

- **Per fixture item:** single-hop 0, multi-hop 0, partial 0, unanswerable 1,
  false-premise `no` 1, false-premise `correctable` **0.7**.
- **Mean over the six fixture items:** 2.7 / 6 = **0.45**. The fixtures have one item
  per type plus a second false-premise item, so this mean overweights
  abstention-rewarding types.
- **Over the brief's target mix** (single-hop 30%, multi-hop 25%, partial 15%,
  unanswerable 20%, false premise 10%, split evenly between `no` and `correctable`):
  0.20 + 0.05 + 0.05 × 0.7 = **0.285**. With A = 1 it would be 0.30 (the brief's
  estimate); with A = 0.5, 0.275. The real split of false premises depends on the
  question set.

An honest oracle (claims every gold point with its evidence, flags on partial items,
abstains on `no`) scores 1.0 on every fixture item, both on hand-built traces and end
to end through the stub endpoint.

## Recorded metrics (not scored)

The adapter records the reward as one `@vf.reward` (`grounding_reward`) and everything
below through one `@vf.metric` (`grounding_metrics`) that returns a keyed dict. Verifiers
sums every `@vf.reward` into `trace.reward`, so the components must be metrics: the
aggregate isn't a sum of them.

Undefined ratios (no claims, no cites, no gold points) are left out rather than recorded
as 0. Per-tool keys appear only when nonzero; read a missing key as 0.

**Answer metrics** (`metrics.answer_metrics`):

| Key | Meaning |
|---|---|
| `format_ok`, `abstained` | 1/0 |
| `unparseable` | 1/0; its mean over traces is the unparseable rate |
| `unparseable/<reason>` | 1/0 for each of `not_json`, `schema`, `mixed_answer_and_abstain`, `empty` |
| `citation_validity`, `claim_support`, `coverage`, `unsupported_penalty`, `abstention_correct`, `flagged` | the components |
| `premise_asserted` | 1/0, false-premise items only: a claim states the premise (`same_fact`); the "premise accepted" failure |
| `claims` | claims per answer as written, duplicates included (0 for an abstention or parse failure) |
| `citations_per_claim` | mean distinct cites per claim |
| `supported_facts`, `unsupported_facts` | \|S\|, \|U\|: distinct facts after deduplication |
| `duplicate_claims` | claims removed by deduplication (claims stating the same fact as an earlier claim; reversed restatements count unless the relation is directed) |
| `voided_claims` | claims a valid cite states but a stripped cite made unsupported (strict citations) |
| `stripped_cites` | cites to IDs that weren't retrieved |
| `nonsupporting_valid_cites` | retrieved cites other than the one that supported the claim |
| `retrieved_uncited` | retrieved evidence IDs no claim validly cites |

**Trace metrics** (`metrics.trace_metrics`):

| Key | Meaning |
|---|---|
| `tool_calls`, `tool_calls/<tool>` | tool calls in model-sampled assistant messages, total and per tool name |
| `tool_results_accepted`, `tool_results_rejected` | tool messages `audit_retrieval` accepted or rejected |
| `rejected/<reason>` | rejections per reason, every reason always present: `no_matching_call`, `call_not_sampled`, `not_descendant`, `duplicate_result`, `unparseable` |
| `rejected/<reason>/<tool>` | the same, per tool name (`unknown` when the message names none). A shell tool's output lands in `rejected/unparseable/<shell tool>`, which is expected; evidence tools there are not |
| `duplicate_call_ids`, `conflicting_ids` | tampering signals from `audit_retrieval` |
| `retrieved_evidence` | distinct evidence IDs retrieved |
| `input_tokens`, `output_tokens` | only when the trace records provider usage (`trace.usage`); input includes cached tokens |

**Final-answer check** (`adapter.root_reply_metrics`):

| Key | Meaning |
|---|---|
| `root_reply_present` | 1/0: the harness reported a `root_reply` |
| `root_reply_differs` | 1/0: it is present and differs from the scored reply (the last sampled assistant message), both stripped |

**Provenance** is recorded in `trace.info`, not as metrics: `store_build_id`,
`items_sha256` and `reward_params` (see `taskset.py`).

## What is implemented here vs library behavior

Implemented in this project: the parser (`contracts.output`), strict retrieval
(`contracts.trace`), the fact rule (`contracts.facts`), everything in
`neuro_grounded_qa.reward` (including strict citations, fact deduplication and the
final-answer rule), and the provenance written into `trace.info` (`taskset.py`).
Verifiers provides: the trace and its `assistant_messages`, `root_reply`, `sampled`
flags, `info` and usage; calling the task's `setup` hook before each rollout; the `@vf.reward`/`@vf.metric` decorators and
their discovery on the task class's MRO (which is why a mixin works); summing weighted
rewards into `trace.reward`; and skipping scoring for format-violation traces.

## Limitations

- **Direction-insensitive matching for relations whose roles are fixed by entity
  types.** Facts flagged `directed` (same-type hierarchy relations) match in order only.
  Every other relation matches in either order, including directional ones whose
  entity types fix the roles: "DIS_1 targets DRUG_X" is supported by the edge "DRUG_X
  targets DIS_1". The reversed reading is nonsense rather than a different true fact,
  but the reward doesn't penalize it (settled 2026-11-19: PrimeKG's stored order is
  arbitrary for these).
- **Direction for claim-vs-claim comparison comes from what the score sees.** If a
  directed relation appears in no retrieved edge, gold point, premise or graph gap
  triple, deduplication treats it as undirected. Such claims are unsupported anyway (no retrieved edge states
  them), so this changes at most how many unsupported facts are counted.
- **Free text is unscored.** `answer_text` can contradict the claims, and
  `unsupported_parts` can say anything; neither changes the score.
- **No precision term beyond the penalty.** A claim that is true, retrieved and
  supported but irrelevant costs nothing (see "Evidence dump" below).
- **Coverage is all-or-nothing per gold point** and equal-weighted across gold points;
  a multi-hop item's chain edges count the same as its endpoints.

## Exploit candidates

Seeds for the audit. Reward v0 leaves the first three **open on purpose**; the audit
measures them before anything is changed.

**Open, for the audit:**

1. **Evidence dump.** Retrieve every neighbor of the question's entities and restate
   every edge as a cited claim. Every claim is supported, so the penalty is 0, and every
   gold point among the neighbors is covered: the dump scores 1.0 on the fixture
   single-hop item (`practice/reward.ipynb` shows it). This is the brief's open problem.
2. **Supported padding (penalty dilution).** The penalty is a share of distinct facts,
   so true, supported, irrelevant facts shrink the cost of unsupported guesses. Guesses
   never add coverage, so dilution only softens a cost; it matters most combined with
   (1). (Padding with *repeats* of the same fact is closed by deduplication.)
3. **Always flag.** Flags earn κ on `partial` items and cost nothing on `yes` items, and
   their text isn't checked, so a policy should always add one.

**Other candidates:**

4. **Reversed type-fixed relations** (Limitations). Hierarchy relations are now
   flagged `directed` and match in order only; relations whose roles are fixed by entity
   types (`targets`, `indicated_for`) still match reversed.
5. **Abstain on correctable items.** Mitigated in v0: with A = 0.7, a cited correction
   (1.0) beats abstaining. The audit can still check whether 0.7 is low enough.

**Closed in v0, kept as regression cases:**

6. **Duplicate claims.** Repeating a supported claim (in either order, for undirected
   relations) no longer adds to |S|: duplicate facts count once. A reversed directed
   claim is a separate, unsupported fact, so it can't hide behind the correct one
   (`test_scoring.py`, "Duplicate facts").
7. **Mixing a fabricated cite with a real one.** One bad cite voids the claim
   (`test_scoring.py`, "Strict citations"; end to end in
   `tests/integration/test_end_to_end.py`).
8. **Trusting `root_reply`.** Only the last sampled assistant message is scored;
   `root_reply_differs` records any mismatch (`test_adapter.py`).
9. **Cite everything.** Closed by the support check (an extra valid cite doesn't help),
   but still visible in `citations_per_claim`.
10. **Fabricated, typed-in and prompt-injected IDs.** Closed by strict retrieval; kept
   as regression cases (`test_adapter.py`).

## What the harness must guarantee

- **One result per tool call.** A second result for the same call is rejected as
  `duplicate_result`, even if the first was unparseable, so retries must use a new call.
- **Payloads reach the trace intact.** A tool result is read only if its whole content
  parses as `ToolResultPayload`. A harness that truncates or rewrites tool output turns
  real retrievals into `rejected/unparseable`. Tools must cap their own results (and set
  `truncated: true`) below any harness truncation limit.
- **The final answer is the last sampled assistant message's content**, containing
  only the JSON object, optionally in one fenced block. The system prompt says so:
  prose around the JSON fails the format gate. A harness-reported `root_reply` is
  not scored.
- **Task data is a `QuestionItem`.** The task class is
  `NeuroGroundedQATask(GroundingRewardMixin, vf.Task[QuestionItem, KGState])`; any
  other task data raises.
- **The model sees only the KG tools.** Runs use the `null` harness
  (`configs/local.toml`). The default `bash` harness would give the model a shell on
  the machine, where it could read the items file (gold included).
- **Replays keep assistant messages identical.** If a harness re-sends an assistant
  message altered, strict retrieval may see an unsampled copy and reject its tool
  results as `not_descendant`; check `rejected/not_descendant` on smoke-run traces.
- **Provider usage** is needed for the token metrics; without it they're omitted.
