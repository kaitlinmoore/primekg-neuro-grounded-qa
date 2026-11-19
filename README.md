# primekg-neuro-grounded-qa

A [Verifiers](https://github.com/PrimeIntellect-ai/verifiers) environment for citation-grounded question answering over a neurodegeneration-focused subgraph of PrimeKG, with a reward-hacking audit of its grounding verifier.

**Status: in progress.** The environment code is under construction and runs end to end on synthetic fixtures; the question set isn't built yet, and there are no results.

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
```

Runs use verifiers' `null` harness (set in `configs/local.toml`): the model gets only the knowledge-graph tools. The default `bash` harness would also give it a shell on the local machine, where it could read the question items, gold answers included.

Add `--dry-run True` to the `scripts/run.sh` command to resolve and write the config without running anything. Other `vf-run` options pass through unchanged. Run outputs go to `outputs/` (gitignored).

With no data configured, the environment runs on the synthetic fixtures in `tests/fixtures/contracts/`. `configs/local.toml` sets the question items (`[env.taskset]`) and the evidence store and tool limits (`[env.taskset.tools]`).

## Results, methodology

To come as the environment is built.

## License

Code is released under the MIT License. Data sources keep their own licenses.

## AI assistance

This project is built with AI assistance (Claude). What was AI-assisted, and how the work was checked, will be documented here.
