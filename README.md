# primekg-neuro-grounded-qa

A [Verifiers](https://github.com/PrimeIntellect-ai/verifiers) environment for citation-grounded question answering over a neurodegeneration-focused subgraph of PrimeKG, with a reward-hacking audit of its grounding verifier.

**Status: in progress.** The repository is being set up; there is no environment code and there are no results yet.

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
# Tests
uv run pytest

# Validate the environment's tasks locally (no model, no agent)
uv run vf-validate neuro-grounded-qa --runtime.type subprocess

# Run with the project's local config. The model and endpoint are always explicit:
# configs/local.toml sets neither, and vf-run falls back to its own default model
# on Prime Inference if -m is omitted.
uv run vf-run neuro-grounded-qa @ configs/local.toml -n 1 \
  -m <model> --client.base-url <endpoint-url> --client.api-key-var <ENV_VAR_NAME>
```

Add `--dry-run True` to the `vf-run` command to resolve and write the config without running anything. Run outputs go to `outputs/` (gitignored).

## Results, methodology

To come as the environment is built.

## License

Code is released under the MIT License. Data sources keep their own licenses.

## AI assistance

This project is built with AI assistance (Claude). What was AI-assisted, and how the work was checked, will be documented here.
