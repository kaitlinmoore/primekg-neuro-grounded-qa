# primekg-neuro-grounded-qa

A [Verifiers](https://github.com/PrimeIntellect-ai/verifiers) environment for citation-grounded question answering over a neurodegeneration-focused subgraph of PrimeKG, with a reward-hacking audit of its grounding verifier.

**Status: in progress.** The environment runs end to end on synthetic fixtures. The evidence store and the question set build locally from the downloaded graph (see below). There are no results yet.

## Question

Can a reward computed in code from an agent's own tool-call trace score whether its answers are grounded in knowledge-graph evidence it actually retrieved, and whether it abstains when that evidence can't support an answer? Which policies can game that reward, and which of those exploits can be closed?

## Data sources

To be documented with URLs, versions, access dates and licenses before any data is used. Data is kept out of git.

## Setup

On Windows, the project runs in WSL2 (Ubuntu): verifiers 0.4.1 can't import its trace or CLI on native Windows. On Linux or WSL2, with [uv](https://docs.astral.sh/uv/) installed:

```bash
uv tool install prime==0.22.0   # Prime CLI, kept out of the project's dependencies
uv sync                         # Python 3.11 venv: environment package (editable) + dev tools
cp .env.example .env            # then fill in the keys you need
```

On WSL with the checkout under `/mnt/c`, [direnv](https://direnv.net/) is optional but recommended: the committed `.envrc` puts the venv on the WSL filesystem, which makes imports and tests much faster. Run `direnv allow` once in the repo before `uv sync`.

## How to run

```bash
# Tests (the end-to-end tests run scripts/run.sh against a scripted stub
# endpoint, no model; skip them with -m "not integration")
uv run pytest

# Validate the environment's tasks locally (no model, no agent)
uv run vf-validate neuro-grounded-qa --runtime.type subprocess

# Model runs go through the wrapper. It refuses to start without -m and
# --client.base-url (vf-run alone would fall back to its own default model on
# Prime Inference), then runs vf-run with configs/local.toml from the repo root.
scripts/run.sh -n 1 \
  -m <model> --client.base-url <endpoint-url> --client.api-key-var <ENV_VAR_NAME>

# For example, Claude through Anthropic's OpenAI-compatible endpoint, with the key
# in .env as NGQ_ANTHROPIC_API_KEY (never ANTHROPIC_API_KEY: Claude Code reads that
# name and would bill its own usage to the key). run.sh loads .env itself.
scripts/run.sh -n 1 \
  -m claude-haiku-5-5 --client.base-url https://api.anthropic.com/v1/ \
  --client.api-key-var NGQ_ANTHROPIC_API_KEY

# Summarize a run: summary.md and summary.json next to its traces.jsonl
uv run python scripts/analysis/summarize_run.py outputs/<run-folder>
```

Runs use verifiers' `null` harness (set in `configs/local.toml`): the model gets only the knowledge-graph tools. The default `bash` harness would also give it a shell on the local machine, where it could read the question items, gold answers included.

Add `--dry-run True` to the `scripts/run.sh` command to resolve and write the config without running anything. Other `vf-run` options pass through unchanged. Run outputs go to `outputs/` (gitignored).

With no data configured, the environment runs on the synthetic fixtures in `tests/fixtures/contracts/`. `configs/local.toml` sets the question items (`[env.taskset]`) and the evidence store and tool limits (`[env.taskset.tools]`).

### Building the data

Graph data stays out of git, in `$NGQ_DATA_DIR` (set by `.envrc`). From the repo root, after `uv sync`:

```bash
uv run python scripts/data/download.py                     # PrimeKG (and OptimusKG) raw files, checksummed
uv run python scripts/data/measure.py primekg --domain neuro   # full-graph duckdb (derived/primekg.duckdb) that absence checks read
uv run python -m neuro_grounded_qa.data.build --out "$NGQ_DATA_DIR/store/primekg-neuro-c95"   # evidence store
uv run python scripts/data/question_set.py generate        # items/<build_id>/{train,heldout}.jsonl + manifest.json
uv run python scripts/data/question_set.py check --build-id <build_id>   # re-check both splits (counts only)
NGQ_ITEMS_DIR="$NGQ_DATA_DIR/items/<build_id>" uv run pytest -q tests/integration/test_real_train_replay.py   # opt-in: replay every train route through run.sh
```

The question-set commands print counts and hashes only; the held-out file is never displayed. To run on the real data, pass `--env.taskset.items_path "$NGQ_DATA_DIR/items/<build_id>/train.jsonl"` and `--env.taskset.tools.store_path "$NGQ_DATA_DIR/store/primekg-neuro-c95"` to `scripts/run.sh`. Details: `docs/question_set_design.md` ("Evidence store", "Item set").

## Results, methodology

To come as the environment is built.

## License

Code is released under the MIT License. Data sources keep their own licenses.

## AI assistance

This project is built with AI assistance (Claude). What was AI-assisted, and how the work was checked, will be documented here.
