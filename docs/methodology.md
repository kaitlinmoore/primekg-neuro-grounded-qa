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
