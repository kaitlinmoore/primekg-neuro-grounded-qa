# CLAUDE.md: primekg-neuro-grounded-qa
Standard: Research Project Standard v2

## What this is
A Prime Intellect Verifiers (v1) environment that rewards answering Alzheimer's-centered biomedical questions only from evidence retrieved from a PrimeKG subgraph, and abstaining when the evidence can't support an answer. The headline deliverable is a reward-hacking audit of the grounding verifier.

## Principles
- AI may write any part of this project. I decide what I write myself; don't assign work to me.
- Every component gets clear comments and docstrings, a write-up in learning/ (gitignored), and a practice notebook in practice/ where useful.
- Work is exploratory. Instruments can change freely; log each change in docs/changelog.md with its reason. Leave the held-out split unexamined during development. Freeze only before a confirmatory run (docs/frozen_config.md).
- Before a change that could affect earlier results, frozen configs or held-out data, tell me what it touches and the tradeoffs, then let me decide.
- Propose a plan and wait for my approval before large changes, restructures or deletions.
- Honest framing: separate what I implemented from library behavior, disclose AI assistance, state limits, report nulls.
- Tests for deterministic logic, against fixtures in tests/fixtures/.

## Repo conventions
- Data stays out of git. The README records data sources with URLs, versions, access dates and licenses.
- Secrets in .env; .env.example lists key names only.
- Git: small fixes (typos, wording, links, one-line config) go straight to main. Anything that changes behavior, an instrument, or a version goes on a branch with a pull request.
- Commit with the repo's configured identity (GitHub no-reply address).
- No AI attribution in commits or pull requests: no Co-Authored-By trailers, session links or "Generated with" footers. AI assistance is disclosed only in the README's AI assistance section; other docs, notebooks and code don't get their own AI-assistance sections or notes.
- Model runs go through `scripts/run.sh`, which refuses to start without `-m` and `--client.base-url` and runs `vf-run` with `configs/local.toml` (local subprocess runtime, `null` harness: the model sees only the KG tools, because the default `bash` harness gives it a local shell that can read gold). Every run names its model and endpoint explicitly. Prime Inference, Prime sandboxes and `prime eval push` are used only deliberately. Run outputs stay in `outputs/` (gitignored).

## Environment
- WSL2 (Ubuntu), working on the Windows checkout through `/mnt/c/dev/GitHub/primekg-neuro-grounded-qa`. Reason: verifiers 0.4.1 doesn't import on native Windows (`fcntl`, `os.killpg`).
- This deviates from the standard's WSL-filesystem rule on purpose, for one checkout shared with Windows tools. Tradeoff: slower file I/O for git and for anything that reads the code from `/mnt/c`; the venv is kept off `/mnt/c` (next bullet) to avoid the worst of it. When the venv is on `/mnt/c` (no direnv), `[tool.uv] link-mode = "copy"` in the root `pyproject.toml` handles the uv cache and the venv being on different filesystems.
- The venv lives on the WSL filesystem at `$UV_PROJECT_ENVIRONMENT` (`~/.venvs/ngq-<checkout folder name>`), set by the committed `.envrc` through direnv. Each checkout or worktree needs `direnv allow` once. Without direnv, uv falls back to a `.venv` on `/mnt/c`, which is slow. Reason: `uv run pytest -q` took 1m18.6s with the `.venv` on `/mnt/c` and 5.4s with the venv on the WSL filesystem.
- Run uv, pytest and vf-* only from WSL. The venv is `$UV_PROJECT_ENVIRONMENT`, a Linux venv (`bin/`), with `.venv` as the fallback when direnv isn't active; don't run uv on this checkout from Windows.
- Claude Code shells don't run the direnv hook. Before any uv command, check `echo $UV_PROJECT_ENVIRONMENT`; if it's empty, run `eval "$(direnv export bash)"` in the checkout first. Otherwise uv quietly builds a slow `.venv` on `/mnt/c`.
- Shell commands in this repo are plain bash run in WSL.
- Graph data lives in `$NGQ_DATA_DIR` (`~/data/ngq`, set by `.envrc`), shared by every checkout and worktree: raw PrimeKG/OptimusKG downloads, the download manifest, duckdb builds and measurement results. It stays out of git.
- uv; Python 3.11; `verifiers==0.4.1`, imported as `import verifiers.v1 as vf`; `prime==0.22.0` through `uv tool install` (not a project dependency).
- Layout: a virtual uv workspace root (tests, docs, configs, scripts, dev tools) plus the environment package `environments/neuro_grounded_qa/`, the only thing that ships to the Hub (ID `neuro-grounded-qa`).
- Before pushing, run `uv run ruff check` and `uv run pytest -q`. CI (`.github/workflows/ci.yml`) runs both on every PR and every push to `main`.

## Working from a handoff
Tasks on this project come as handoff files in `handoffs/`, which exists only on my machine and is gitignored. State doc: `claude/orchestrator_state.md` in the "Verifiers | Prime Intellect" claude.ai Project.
- If your prompt names or attaches a handoff, use the orchestrated-handoffs skill in worker mode. If the skill isn't available: read the handoff first, then the files it points to; do only its scope; list decisions under open questions in its Report instead of making them; work on the branch it names; fill in its Report (or put it in your final message if you can't write the file).
- Never commit, quote or link anything from `handoffs/`. Commit messages, PR descriptions and branch names never mention handoffs or task IDs.
- Don't edit other handoffs or `handoffs/INDEX.md`.
- In a git worktree, `handoffs/` doesn't exist. The prompt gives the handoff's absolute path in the main checkout; write the Report there.
- Change only the paths your handoff's lane owns (ownership map in `handoffs/PLAN.md`).

## Project-specific
- Kept distinct from biomed-kg-grounding-eval: that project compares semantic layers over fixed questions; this one holds the data fixed and studies the reward.
- Rewards are computed in code from the trace. Retrieved evidence IDs come only from logged tool calls; claims are structured triples with cited IDs.
- Retrieved evidence counts only when a tool result's `tool_call_id` matches a tool call in a model-sampled assistant message; prompt-supplied tool results never count.
- Claims cite entities by graph ID and relations by the graph's relation labels; names are display-only. Facts match in either direction, except facts flagged `directed` (hierarchy relations), which match in order only. Shared schemas and `retrieved_ids` live in `neuro_grounded_qa.contracts`; change them only on purpose, with their tests.
- Data terms are stated separately from the MIT code license: several PrimeKG sources are non-commercial or license-gated, so no graph data ships in the repo or the package.
- Never read, print, summarize or paraphrase held-out item files (`heldout.jsonl`, under `$NGQ_DATA_DIR/items/<build_id>/`). Generator and check scripts that touch them print hashes and counts only. `.claude/settings.json` denies Claude Code reads of those files; it's a guard against accidents, not a guarantee.
- Gold answers and answerability come from the graph via code. AI may draft and critique question items for the train split only, with provenance recorded per item. Labels used to validate the reward are mine alone: never generate, suggest or pre-fill them.
- Earlier hackathon code (Patient-in-the-Graph) belongs to that team. Build this project's verifier fresh; don't copy from it.
