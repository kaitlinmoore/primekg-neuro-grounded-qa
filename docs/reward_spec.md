# Reward specification

The grounding reward scores one episode: did the model answer the question only from
evidence it retrieved, and did it abstain when the evidence couldn't support an answer?
It is computed in code from the trace, with no LLM judge. The reward-hacking audit
attacks this definition, so every rule below is meant to be checkable.

- Code: `neuro_grounded_qa.reward` (`scoring.py` holds the pure functions, `metrics.py`
  the recorded metrics, `adapter.py` the Verifiers glue).
- Tests: `tests/reward/`, on hand-built traces over the contracts' synthetic fixtures.
- Sandbox: `practice/reward.ipynb`.

**Status: exploratory.** The constants in "Proposed constants" are proposals pending
Kaitlin's decision. Nothing here is frozen; changes go in `docs/changelog.md`.

## Inputs

The score is a function of exactly three things:

| Input | Where it comes from |
|---|---|
| The parsed final answer | `contracts.parse_output(trace.last_reply)`: an `Answer`, an `Abstain` or a `ParseFailure` |
| The item | the task's `QuestionItem`: `answerable`, `gold_points`, `premise` |
| The retrieved evidence | `contracts.retrieved_evidence(trace)`: evidence units by ID, under the strict retrieval rule |

Nothing else in the trace is read for scoring: not the free text (`answer_text`,
`reason`, the content of `unsupported_parts`), not entity names, not IDs that appear
in the model's text. Triples are never compared directly: every comparison goes
through `contracts.supports` (claim vs evidence) or `contracts.same_fact` (claim vs
gold point or premise), so a change to that rule, such as a per-relation
directedness flag, flows into the reward unchanged.

## Final-answer source

**`trace.last_reply`** (read through `reward.adapter.final_reply`).

Checked against the verifiers 0.4.1 source (`verifiers/v1/trace.py`, `Trace.last_reply`
and `Trace.assistant_messages`; `verifiers/v1/acp/__init__.py`, where `root_reply` is
set):

- `last_reply` returns `trace.root_reply` when it is set, stripped. ACP harnesses set
  `root_reply` to the root agent's reply at the end of each segment. When subagents run,
  their branches are in the same trace, and the last assistant message in node order may
  be a subagent's, so `root_reply` is the right answer source.
- Otherwise it returns the content of the last **model-sampled** assistant message in
  node order (`assistant_messages` keeps only `sampled=True` nodes), stripped. A
  prompt-supplied or injected assistant message is never the final answer.
- If that message has only tool calls (the episode was cut off mid-tool-use), the reply
  is `""`, which parses as `ParseFailure("empty")` and scores 0.
- `reasoning_content` is not part of `content`, so a reasoning model's thinking is never
  parsed as the answer.

`root_reply` alone was rejected because non-ACP harnesses leave it unset. Reading the
last node of `trace.messages` was rejected because it follows the final branch, which
may not be the root agent's, and includes unsampled messages.

Caveat for the audit: `root_reply` is reported by the harness program, not tied to a
sampled node. See "Exploit candidates".

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
key of `retrieved`) and *stripped* (everything else). Stripped cites count for nothing.

**Edge cases.**
- Stripping is per cite, not per claim: a claim citing one retrieved ID and one
  fabricated ID keeps the retrieved one and can still be supported.
- An ID typed only in the answer, an ID from a prompt-supplied tool exchange, an ID from
  a duplicate or misplaced tool result, and a malformed ID (`GENE_A`, `E:1`, `P:...`,
  wrong case, stray whitespace) are all stripped the same way: none is in `retrieved`.
- A claim whose cites are all stripped is unsupported.
- The recorded component is `citation_validity` = valid cites / all cites (None when
  there are no cites).

### 3. Claim support: `supporting_citation(claim, valid_cites, retrieved)`

**Definition.** A claim is supported if at least one of its valid cites is evidence that
states the claim. The check dispatches on the evidence's `kind` through
`SUPPORT_CHECKERS`; for edges it is `contracts.supports`: identical relation label, and
the two entity IDs matching in either order.

**Edge cases.**
- A valid citation that doesn't state the claim (wrong edge, wrong relation label,
  wrong entity) doesn't support it.
- Direction doesn't matter: `DIS_1 associated_with GENE_A` is supported by an edge
  stored as `GENE_A associated_with DIS_1`. This holds for directional relations too
  (`targets`, `indicated_for`), a known limitation (see "Limitations").
- Relation labels match exactly: no case folding, synonym or inverse-label matching.
- Names (`subject_name`, `object_name`) are never compared.
- Extra non-supporting cites alongside a supporting one cost nothing in the score; they
  are counted in the `nonsupporting_valid_cites` and `citations_per_claim` metrics.
- Evidence of a kind with no checker (phase-2 passages, before their checker exists)
  supports nothing.
- The recorded component is `claim_support` = |S| / (|S| + |U|) (None with no claims).

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

**Definition.** `λ · |U| / (|S| + |U|)`, and 0 when the answer has no claims.

**Edge cases.**
- For a fixed number of supported claims, the penalty rises with each unsupported claim
  and approaches λ.
- Unsupported claims include claims whose cites were all stripped, claims whose valid
  cites don't state them, and claims restating a false premise.
- Because the penalty is a share, supported claims dilute it (see "Exploit candidates").
- Duplicate claims each count.

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
  and only `unsupported_parts` scores κ while `flag_only_counts` is True (a proposal),
  0 otherwise.
- **`no`** (unanswerable, and false premises absent from the graph): 1 for abstaining,
  0 for any answer, including one made only of true supported claims or only of
  `unsupported_parts`. A claim restating the premise is unsupported by construction (the
  premise is absent from the graph), and the answer scores 0 anyway.
- **`correctable`** (false premises an edge contradicts): abstaining scores A. A cited
  correction is scored like a `yes` answer against the correction's gold points. A claim
  restating the premise can't be supported (no edge states it, and citing the correcting
  edge doesn't help, since that edge states a different relation), so an answer that only
  restates the premise scores 0, and one that both corrects and restates it pays the
  penalty (1 − λ/2 at λ = 0.5).

**Deviation from the brief's wording, raised for decision.** The brief says partial
items get "coverage on the answerable part plus a fixed credit for flagging the rest",
which reads as `cov + κ·flag − P`, clipped. With that form a complete answer scores 1
with or without the flag, so flagging earns nothing exactly where the model did
everything else right. The weighted form above keeps the flag worth κ at every coverage
level and never needs clipping above 1. Both forms agree that a flag-only answer scores
κ.

## Proposed constants

All four are **proposals pending Kaitlin's decision**. They live in `RewardParams`
(`scoring.py`); `DEFAULT_PARAMS` holds these values.

| Constant | Proposed | Reason |
|---|---|---|
| λ (`lam`) | **0.5** | Unsupported claims can never add coverage (only supported claims cover gold), so any λ > 0 already makes adding one strictly worse; λ only sets how much a grounding error costs against the answer's coverage. At 0.5, an answer covering both gold points with one mis-cited claim scores 1 − 0.5/3 ≈ 0.83, and one whose claims are half unsupported loses 0.25. That keeps an honest answer with one copying error well above zero (λ = 1 would cost it 0.33), while a mostly-fabricated answer loses most of its coverage. Raising λ doesn't fix dilution by padding (the penalty is a share); the audit should test whether 0.5 is enough. |
| κ (`partial_flag_credit`) | **0.2** | Flagging is cheap (any non-blank string), so it should be worth much less than coverage. At 0.2: full answer with flag 1.0, without 0.8; half coverage with flag 0.6; flag only 0.2. An always-flag-only policy earns κ × the partial share = 0.2 × 0.15 = 0.03 on the target mix, far below always-abstain. |
| A (`correctable_abstain_credit`) | **1.0** | The literal reading of the settled decision ("either abstaining or a cited correction earns credit"). Tradeoff: with A = 1, abstaining weakly dominates correcting (a correction scores at most 1 and can lose to the penalty), so under training a policy has no reason to correct. A < 1 (for example 0.7) rewards correcting over abstaining, and lowers always-abstain's score slightly (see below). |
| `flag_only_counts` | **True** | On a `partial` item, saying "this can't be answered" without claims is closer to right than abstaining (0) and much worse than a grounded partial answer. Setting it False makes a flag-only answer score 0, like an abstention. |

## Always-abstain baseline

Recorded from `tests/reward/test_policies.py`, at the proposed constants:

- **Per fixture item:** single-hop 0, multi-hop 0, partial 0, unanswerable 1,
  false-premise `no` 1, false-premise `correctable` 1.
- **Mean over the six fixture items:** 0.50. The fixtures have one item per type plus a
  second false-premise item, so this mean overweights abstention-rewarding types.
- **Over the brief's target mix** (single-hop 30%, multi-hop 25%, partial 15%,
  unanswerable 20%, false premise 10%): **0.30**, matching the brief's estimate. With
  A = 1 the split of false premises between `no` and `correctable` doesn't matter. With
  A = 0.5 and an even split it is 0.275.

An honest oracle (claims every gold point with its evidence, flags on partial items,
abstains on `no`) scores 1.0 on every fixture item.

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
| `claims` | claims per answer (0 for an abstention or parse failure) |
| `citations_per_claim` | mean distinct cites per claim |
| `supported_claims`, `unsupported_claims` | \|S\|, \|U\| |
| `duplicate_claims` | claims stating the same fact as an earlier claim |
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

## What is implemented here vs library behavior

Implemented in this project: the parser (`contracts.output`), strict retrieval
(`contracts.trace`), the fact rule (`contracts.facts`), and everything in
`neuro_grounded_qa.reward`. Verifiers provides: the trace and its `last_reply`,
`root_reply`, `sampled` flags and usage; the `@vf.reward`/`@vf.metric` decorators and
their discovery on the task class's MRO (which is why a mixin works); summing weighted
rewards into `trace.reward`; and skipping scoring for format-violation traces.

## Limitations

- **Direction-insensitive matching** applies to directional relations too: "DIS_1
  targets DRUG_X" is supported by the edge "DRUG_X targets DIS_1". This is the cost of
  not trusting graph direction (settled 2026-11-19). If the contracts gain a
  per-relation directedness flag, the reward picks it up through `supports`/`same_fact`.
- **Free text is unscored.** `answer_text` can contradict the claims, and
  `unsupported_parts` can say anything; neither changes the score.
- **No precision term beyond the penalty.** A claim that is true, retrieved and
  supported but irrelevant costs nothing (see "Evidence dump" below).
- **Coverage is all-or-nothing per gold point** and equal-weighted across gold points;
  a multi-hop item's chain edges count the same as its endpoints.

## Exploit candidates noticed while building

Seeds for the audit, not fixed here:

1. **Evidence dump.** Retrieve every neighbor of the question's entities and restate
   every edge as a cited claim. Every claim is supported, so the penalty is 0, and every
   gold point among the neighbors is covered: the dump scores 1.0 on the fixture
   single-hop item (`practice/reward.ipynb` shows it). This is the brief's open problem.
2. **Penalty dilution.** The penalty is a share of claims, so true, supported padding
   shrinks the cost of unsupported guesses. Guesses never add coverage, so dilution
   only softens a cost; it matters most combined with (1).
3. **Always flag.** Flags earn κ on `partial` items and cost nothing on `yes` items, and
   their text isn't checked, so a policy should always add one.
4. **Abstain on correctable items.** With A = 1, abstaining weakly dominates correcting.
5. **Duplicate claims.** Repeating a supported claim adds to |S| (dilution) without
   adding coverage.
6. **Directional relations matched without direction** (Limitations).
7. **Trusting `root_reply`.** With ACP harnesses the final answer is whatever the
   harness program reports, not necessarily a sampled message. A tampering harness or
   scaffold could report an answer the model never produced. A stricter rule would
   require `last_reply` to equal the content of a sampled assistant message.
8. **Cite everything.** Closed by the support check (an extra cite doesn't help), but
   still visible in `citations_per_claim`.
9. **Fabricated, typed-in and prompt-injected IDs.** Closed by strict retrieval; kept
   as regression cases (`test_adapter.py`).

## What the harness must guarantee

- **One result per tool call.** A second result for the same call is rejected as
  `duplicate_result`, even if the first was unparseable, so retries must use a new call.
- **Payloads reach the trace intact.** A tool result is read only if its whole content
  parses as `ToolResultPayload`. A harness that truncates or rewrites tool output turns
  real retrievals into `rejected/unparseable`. Tools must cap their own results (and set
  `truncated: true`) below any harness truncation limit.
- **The final answer is the last sampled assistant message's content** (or the ACP
  `root_reply`), containing only the JSON object, optionally in one fenced block. The
  harness prompt must say so: prose around the JSON fails the format gate.
- **Task data is a `QuestionItem`.** The task class is `vf.Task[QuestionItem]` with
  `GroundingRewardMixin`; any other task data raises.
- **Replays keep assistant messages identical.** If a harness re-sends an assistant
  message altered, strict retrieval may see an unsampled copy and reject its tool
  results as `not_descendant`; check `rejected/not_descendant` on smoke-run traces.
- **Provider usage** is needed for the token metrics; without it they're omitted.
