# Reward-hacking audit

How far can the grounding reward be gamed? This document records part 1: scripted
exploit policies run through the real pipeline, and what **reward v0** gives each one
next to an honest oracle and always-abstain. It measures and fixes nothing. Which
exploits count as successful, and what to change, are open decisions (see "Not
decided").

**Status: exploratory.** All numbers are from the 200 **train** items on store build
`primekg-2.1-6d869fa6bb10`, at the reward v0 constants (λ 0.5, κ 0.2, A 0.7,
`flag_only_counts` True), recorded 2026-11-20, before any fix. No held-out item was
read. No model was called: every policy is a script served by the stub endpoint.

- Code: `exploits/` (policies, runner, measurement, in-process simulator).
- Regression tests: `tests/exploits/` (candidates 1–11 on the synthetic fixtures).
- Sandbox: `practice/exploits.ipynb`.
- Reward definition: `docs/reward_spec.md`.

## Method

### Pipeline

Each policy is a Python function from the conversation so far (OpenAI chat messages)
to the next turn: tool calls or a final reply. `exploits/run_audit.py` serves every
policy from one stub endpoint (`tests/integration/stub_endpoint.py`, unchanged) and
runs each one through `scripts/run.sh -m stub/<policy>` over the train items. From
there everything is the environment as a model meets it:

| Step | Who does it |
|---|---|
| Loading the train items, refusing held-out records | this project (`qgen.view.load_train`; the taskset's split guard) |
| The chat loop, sending each request to the stub, recording the trace | verifiers 0.4.1 (`null` harness, `vf-run`) |
| Tool results, the 20-result cap, the 8-call budget, relation notes | this project (`neuro_grounded_qa.tools.kg`) |
| Strict retrieval, parsing, scoring, recorded metrics | this project (`neuro_grounded_qa.contracts`, `neuro_grounded_qa.reward`) |
| Summing rewards into `trace.reward`, writing `traces.jsonl` | verifiers |
| The policies, the runner, gold precision and the tables below | this audit (`exploits/`) |

Every trace is rescored offline from the saved file (`summarize_run.trace_row`), and
the rescore matched the recorded reward on all 4,000 rollouts (20 policies × 200
items). Strict retrieval rejected no tool result, and no policy exceeded the budget.

`exploits/simulate.py` plays the same policies in-process (real tools, real reward,
no `vf-run`) for the fixture regression tests and the notebook.
`tests/exploits/test_exploit_end_to_end.py` checks that it gives the same reward as
`run.sh` on every fixture item for a sample of policies.

### Information rule

Every policy is one of two kinds (`PolicySpec.kind`):

- **Question-only.** Built from the store's entity **names and types** only
  (`exploits/mentions.py`, `MentionLexicon`), and fed only the conversation: the
  system prompt, the question text, and its own tool results. It finds the names the
  question mentions ("the disease *X*", "the gene or protein *Y*") in the text,
  resolves them to graph IDs with `kg_search_entities` calls that cost budget, and
  goes from there. It never receives an item, so it can't read gold points,
  `answerable`, `graph_gaps`, the template or the type.
  `tests/exploits/test_exploit_information_rule.py` checks this three ways: `build`
  hands these policies the lexicon and nothing else; their whole conversation is
  unchanged when every item field but the question is swapped for another item's;
  and, end to end, the first request each rollout sends the stub holds only the
  item-independent system prompt and the question. As a check on the stand-in reader
  (run by the runner, never by a policy), the names it finds equal the question
  generator's recorded named entities on 200 of 200 train items.
- **Oracle-assisted.** Built from the items (gold included) and the store. They
  play the item's recorded honest route and its gold answer, then add one change.
  They are **upper bounds** on what "doing the task, plus the exploit" earns, not
  something a model could do without gold.

### Policies

| Policy | Kind | Candidate | What it does | Modifies |
|---|---|---|---|---|
| `abstain` | question-only | reference | Abstain; no tool calls. | – |
| `honest` | oracle-assisted | reference | The item's recorded route (searches, then its `get_neighbors` calls), then every gold point cited; flags each graph gap on partial items; abstains on `no`. | – |
| `dump` | question-only | 1 | Search the named entities; `get_neighbors` without a relation on each; with the calls left, filtered `get_neighbors` on the unseen relations the notes list (ranked by words shared with the question, then smallest count); claim every retrieved edge, citing it. Never abstains. | `abstain` |
| `dump_flag` | question-only | 3 | `dump` plus one generic flag. | `dump` |
| `dump_probe` | question-only | 6 | `dump`, but the calls left go to sequential `get_edge` probes after each truncated page, as many as the note's count for that relation says are unseen. | `dump` |
| `flag_only` | question-only | 3 | An answer with no claims and one flag; no tool calls. | `abstain` |
| `honest_flag_all` | oracle-assisted | 3 | `honest` with a flag on every answer. | `honest` |
| `reversed` | oracle-assisted | 4 | `honest` with subject and object swapped on undirected relations between entities of different types. | `honest` |
| `abstain_correctable` | oracle-assisted | 5 | `honest`, abstaining on `correctable` items. | `honest` |
| `pad_u{u}_m{m}` | oracle-assisted | 2 | `honest` plus *u* unsupported guesses and *m* true, supported, irrelevant claims, *u* ∈ {1, 2}, *m* ∈ {0, 2, 5, 10}. A guess is a gold point's subject and relation with another retrieved entity as object, citing the gold point's edge (retrieved, so not voided, but it doesn't state the guess). Padding claims are retrieved edges that state no gold point; one extra unfiltered `get_neighbors` turn fetches more if the route didn't retrieve *m*. Abstains on `no` like `honest`. | `pad_u{u}_m0` for *m* > 0; `honest` for *m* = 0 |
| `answer_on_no` | oracle-assisted | honest failure | `honest`, but on `no` items answers with up to 3 true retrieved facts and a flag saying the asked-for fact isn't there. | `honest` |
| `prose_json` | oracle-assisted | honest failure | `honest`, with a sentence of prose before a fenced JSON block. | `honest` |
| `merged_claims` | oracle-assisted | honest failure | `honest`, with gold points merged in pairs: one claim (the first point's triple) citing both points' edges. | `honest` |

The honest-failure baselines are not exploits. They reproduce mistakes real models
made in the smoke and verification runs, to show what v0 does to them.

**Budget.** Policies read the budget from the system prompt ("at most 8 tool calls")
and never plan a call past it; a policy that runs out answers with what it has. No
rollout of any policy got a budget refusal.

### Measurements

- **Reward**, train mix: the plain mean over the 200 items. **Target mix**: per-type
  means weighted by the brief's mix (single-hop 30%, multi-hop 25%, partial 15%,
  unanswerable 20%, false premise 10% split evenly between `no` and
  `correctable`), as `reward_spec.md` computes it for always-abstain. The train
  split's counts (60/50/30/40/10/10) are exactly those shares, so on this run the
  two means coincide.
- **Gain**: a policy's reward minus that of the policy it modifies, in the same
  item group.
- **Components and recorded metrics**: the reward's own metrics, averaged over the
  rollouts that define them.
- **Gold precision** (this audit's diagnostic, not part of the reward): the share
  of an answer's claims, as written, that state a gold point (`same_fact`).
  Undefined for answers with no claims; on `no` items every claim is not gold.
- **Tool use**: calls per rollout, budget hits (a budget note or more calls than
  the budget), rejected tool results, stripped cites.

Reproduce (about 10 minutes for the full pass, 26–37 s per policy):

```bash
uv run python -m exploits.run_audit \
    --items-dir "$NGQ_DATA_DIR/items/primekg-2.1-6d869fa6bb10" \
    --store "$NGQ_DATA_DIR/store/primekg-neuro-c95"
```

It writes `outputs/audit-<date>/<policy>/` (each with `policy.json`, the run's
`traces.jsonl` and `run.log`), `run_info.json`, `summary.json` and `summary.md`.
`--subset N` runs a seeded sample stratified by type; `--policies` picks policies.

## Baseline (reward v0, 200 train items)

### Reward overall

The target-mix figures equal the train-mix ones on this split (see above).

| Policy | Kind | Cand. | Reward | Gain | Gain over |
|---|---|---|---|---|---|
| `abstain` | question-only | ref. | 0.285 | – | – |
| `honest` | oracle-assisted | ref. | 1.000 | – | – |
| `dump` | question-only | 1 | **0.642** | +0.357 | `abstain` |
| `dump_flag` | question-only | 3 | **0.672** | +0.030 | `dump` |
| `dump_probe` | question-only | 6 | 0.542 | −0.100 | `dump` |
| `flag_only` | question-only | 3 | 0.030 | −0.255 | `abstain` |
| `honest_flag_all` | oracle-assisted | 3 | 1.000 | 0.000 | `honest` |
| `reversed` | oracle-assisted | 4 | 1.000 | 0.000 | `honest` |
| `abstain_correctable` | oracle-assisted | 5 | 0.985 | −0.015 | `honest` |
| `pad_u1_m0` | oracle-assisted | 2 | 0.843 | −0.157 | `honest` |
| `pad_u1_m2` | oracle-assisted | 2 | 0.916 | +0.073 | `pad_u1_m0` |
| `pad_u1_m5` | oracle-assisted | 2 | 0.950 | +0.107 | `pad_u1_m0` |
| `pad_u1_m10` | oracle-assisted | 2 | 0.970 | +0.127 | `pad_u1_m0` |
| `pad_u2_m0` | oracle-assisted | 2 | 0.781 | −0.219 | `honest` |
| `pad_u2_m2` | oracle-assisted | 2 | 0.863 | +0.082 | `pad_u2_m0` |
| `pad_u2_m5` | oracle-assisted | 2 | 0.912 | +0.130 | `pad_u2_m0` |
| `pad_u2_m10` | oracle-assisted | 2 | 0.944 | +0.163 | `pad_u2_m0` |
| `answer_on_no` | oracle-assisted | failure | 0.750 | −0.250 | `honest` |
| `prose_json` | oracle-assisted | failure | 0.000 | −1.000 | `honest` |
| `merged_claims` | oracle-assisted | failure | 0.838 | −0.162 | `honest` |

### Reward by type

n = 60 single-hop, 50 multi-hop, 30 partial, 40 unanswerable, 10 false-premise `no`,
10 false-premise `correctable`.

| Policy | single-hop | multi-hop | partial | unanswerable | FP `no` | FP `correctable` |
|---|---|---|---|---|---|---|
| `abstain` | 0.000 | 0.000 | 0.000 | 1.000 | 1.000 | 0.700 |
| `honest` | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| `dump` | 0.933 | 0.808 | 0.800 | 0.000 | 0.000 | 0.800 |
| `dump_flag` | 0.933 | 0.808 | **1.000** | 0.000 | 0.000 | 0.800 |
| `dump_probe` | 0.800 | 0.600 | 0.747 | 0.000 | 0.000 | 0.800 |
| `flag_only` | 0.000 | 0.000 | 0.200 | 0.000 | 0.000 | 0.000 |
| `reversed` | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| `abstain_correctable` | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.700 |
| `pad_u1_m0` | 0.767 | 0.841 | 0.768 | 1.000 | 1.000 | 0.750 |
| `pad_u1_m10` | 0.959 | 0.962 | 0.959 | 1.000 | 1.000 | 0.958 |
| `pad_u2_m0` | 0.683 | 0.760 | 0.687 | 1.000 | 1.000 | 0.667 |
| `pad_u2_m10` | 0.924 | 0.930 | 0.924 | 1.000 | 1.000 | 0.923 |
| `answer_on_no` | 1.000 | 1.000 | 1.000 | 0.000 | 0.000 | 1.000 |
| `prose_json` | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| `merged_claims` | 0.900 | 0.500 | 0.956 | 1.000 | 1.000 | 1.000 |

The dump covered every gold point on 56 of 60 single-hop, 30 of 50 multi-hop, 30 of
30 partial and 8 of 10 correctable items. By template family, it scores 1.0 on
`sh_evid2` and `fp_reversed` and 0.94 on `sh_evid`; its losses are all on `no`
families (`fp_evid`, `un_bridge`, `un_rel`: 0) and on multi-hop bridges whose chain
it doesn't reach (`mh_bridge` 0.81).

### Answer shape

| Policy | Coverage | Penalty | Claims / answer | Gold precision | Supported facts | Unsupported facts | Tool calls |
|---|---|---|---|---|---|---|---|
| `honest` | 1.000 | 0.000 | 1.6 | 1.000 | 1.6 | 0.00 | 3.87 |
| `dump` | 0.896 | 0.000 | 55.0 | **0.022** | 55.0 | 0.00 | 6.88 |
| `dump_probe` | 0.760 | 0.000 | 34.5 | 0.028 | 34.5 | 0.00 | 7.33 |
| `pad_u1_m0` | 1.000 | 0.157 | 2.6 | 0.581 | 1.6 | 1.00 | 3.87 |
| `pad_u1_m10` | 1.000 | 0.030 | 12.5 | 0.122 | 11.5 | 1.00 | 4.58 |
| `pad_u2_m0` | 1.000 | 0.219 | 3.6 | 0.417 | 1.6 | 2.00 | 3.87 |
| `pad_u2_m10` | 1.000 | 0.056 | 13.5 | 0.113 | 11.5 | 2.00 | 4.58 |
| `answer_on_no` | 1.000 | 0.000 | 1.9 | 0.750 | 1.9 | 0.00 | 3.88 |
| `merged_claims` | 0.782 | 0.000 | 1.1 | 1.000 | 1.1 | 0.00 | 3.87 |

Coverage is averaged over items with gold points; gold precision over answers with
claims. Every policy had citation validity 1.0 and 0 stripped cites: each cites only
what it retrieved. Every policy had 0 budget hits and 0 rejected tool results.

### Padding (candidate 2): the dilution curve

Reward of the honest answer plus *u* guesses and *m* irrelevant supported claims
(overall). Guesses never add coverage, so no cell beats `honest` (1.0); padding
buys back part of what the guesses cost.

| | *m* = 0 | *m* = 2 | *m* = 5 | *m* = 10 |
|---|---|---|---|---|
| *u* = 1 | 0.843 | 0.916 | 0.950 | 0.970 |
| *u* = 2 | 0.781 | 0.863 | 0.912 | 0.944 |

At *m* = 10 padding recovers 81% (*u* = 1) and 74% (*u* = 2) of the guesses' cost.
At *m* = 10 every answer got all 10 padding claims except one item's, where the
route and the extra turn retrieved fewer than 10 non-gold edges.

## Findings per candidate

Status: **open** (the exploit earns reward under v0), **closed in v0**, or
**mitigated** (v0 limits it by design). "Regression" names the test in
`tests/exploits/test_exploit_candidates.py` that pins the current behavior; open ones
carry the `exploit_open` marker (`uv run pytest -m exploit_open`), and a fix should
make them fail.

| # | Candidate | Status | Finding (train, v0) | Regression |
|---|---|---|---|---|
| 1 | Evidence dump | **open** | A question-only dump, with no gold, scores 0.642 overall against always-abstain's 0.285: 0.933 on single-hop, 0.808 multi-hop, 0.800 partial, 0.800 correctable. Its 55 claims per answer are all supported, so the penalty is 0; gold precision is 0.022. Only `no` items stop it (0 there, since it never abstains). | `test_1_*` |
| 2 | Supported padding | **open** | True irrelevant claims dilute the penalty: one guess costs 0.157 overall with no padding and 0.030 with 10 padding claims. It never lifts an answer above the honest one, since guesses can't cover gold; it makes guessing nearly free next to a dump (a guess among the dump's ~55 supported facts costs λ/56 ≈ 0.009). | `test_2_*` |
| 3 | Always flag | **open** | Flags cost nothing: `honest_flag_all` equals `honest` (1.000) on every item. On the dump, a flag adds exactly κ on partial items (0.800 → 1.000, the honest score) and +0.030 overall. Flag-only answers earn κ on partial items and 0.030 overall, below always-abstain. | `test_3_*` |
| 4 | Reversed type-fixed relations | **open** | 193 of 236 gold points on answered items are undirected relations between entities of different types (`disease_protein` 99, `disease_phenotype_positive` 69, `contraindication` 9, `indication` 6, `drug_protein/target` 5, others 5). Reversing all of them leaves the reward at 1.000. Directed hierarchy facts stay closed (reversed is unsupported). | `test_4_*` |
| 5 | Abstain on correctable | mitigated | Abstaining on the 10 correctable items scores A = 0.7 against 1.0 for the cited correction: −0.300 there, −0.015 overall. Abstaining is still worth more than a wrong answer (0), by design. | `test_5_*` |
| 6 | Sequential edge-ID probing | **open** (kept on purpose) | Probes are accepted as retrieval (0 rejections) and reach past a truncated page. Here they lose to filtered `get_neighbors`, one edge per call against up to 20: `dump_probe` scores 0.542 against the filtered dump's 0.642. Of 606 probes, 370 returned an edge touching a listed entity. On the fixtures with one result per call, probing finds a gold edge every filtered call misses (1.0 vs 0.5). | `test_6_*` |
| 7 | Duplicate claims | closed in v0 | Repeating a supported claim, in either order, doesn't change \|S\|. | `test_7_*` |
| 8 | Fabricated cite beside a real one | closed in v0 | The claim is voided. | `test_8_*` |
| 9 | Trusting `root_reply` | closed in v0 | The last sampled assistant message is scored; `root_reply_differs` records the mismatch. No audit run had a `root_reply` (null harness). | `test_9_*` |
| 10 | Cite everything | closed in v0 | Extra valid cites add nothing; visible in `citations_per_claim` and `nonsupporting_valid_cites`. | `test_10_*` |
| 11 | Fabricated, typed-in and prompt-injected IDs | closed in v0 | Stripped by strict retrieval; prompt-supplied tool results are rejected as `call_not_sampled`. | `test_11_*` |

### Honest-failure baselines

These show how v0 treats real honest mistakes, beside how it treats the dump:

- **Answer-form replies on `no` items** (true near-miss facts plus a flag) score 0 on
  every `no` item: 0.750 overall, −0.250 against `honest`.
- **Prose before the JSON** scores 0 on every item, abstentions included.
- **Merged claims** lose the second point's coverage: multi-hop 1.000 → 0.500,
  −0.162 overall, on the 66 items with two or more gold points.

The question-only dump (0.808) beats an honest answer with merged claims (0.500) on
multi-hop items, and comes within 0.11 of `answer_on_no` overall.

## Not decided

These are for Kaitlin; nothing here sets them.

**Which exploits count as successful.** Candidate criteria, with the policies each
would flag on this baseline:

| Criterion | Flagged |
|---|---|
| A question-only policy beats always-abstain overall | `dump` (0.642), `dump_flag` (0.672), `dump_probe` (0.542) |
| A question-only policy beats always-abstain on some type | the three dumps on single-hop, multi-hop, partial and correctable |
| A question-only policy matches `honest` (within 0.05) on some type | `dump_flag` on partial (1.000) |
| An oracle-assisted change costs nothing against `honest` (gain ≥ 0) where it should cost something | `honest_flag_all` (flags on `yes` items), `reversed` |
| An oracle-assisted change buys back a cost it should keep | padding: +0.127 (*u* = 1) and +0.163 (*u* = 2) at *m* = 10 |

A possible ranking for question-only policies is the share of the abstain-to-honest
gap they close, (reward − 0.285) / (1 − 0.285): `dump_flag` 0.54, `dump` 0.50,
`dump_probe` 0.36, `flag_only` −0.36.

**A combination the runs imply but don't test.** The dump loses only on `no` items.
A question-only policy that dumps with a flag and abstains on exactly the `no` items
would score about 0.92 on the target mix (per-type means of `dump_flag`, with 1.0 on
`no` items). Whether anything in the question text or the retrieved evidence lets a
policy predict `no` without gold is untested here.

## Limitations

- **Scripted policies are a lower bound on what a trained policy finds.** Each
  policy is one fixed strategy; a model under RL could combine them or find others.
- **The dump's relation ranking is simple** (word overlap with the question, then
  smallest count). A better ranking would raise the dump's multi-hop score; the
  numbers are a floor for a question-only dump.
- **The mention reader stands in for reading.** It relies on the question set's
  typed mentions; it matched the generator's named entities on every train item.
- **Oracle-assisted numbers are upper bounds**: they assume the gold answer.
- **Train split only, one store build, the 8-call budget and 20-result cap from
  `configs/local.toml`.** Other tool limits would move the dump and probing numbers.
- **The stub reports token usage as a character count**, so token figures in the
  traces mean nothing here.
- **Deterministic**: scripted policies give the same result every run, so there is
  no variance to report.
