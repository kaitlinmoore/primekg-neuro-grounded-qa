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
- No AI attribution in commits or pull requests: no Co-Authored-By trailers, session links or "Generated with" footers. AI assistance is disclosed in the README instead.

## Environment
Git Bash on Windows, uv, Python version to be confirmed against current Verifiers requirements. Key deps: verifiers (v1, version pinned once confirmed).

## Working from a handoff
Tasks on this project come as handoff files in `handoffs/`, which exists only on my machine and is gitignored. State doc: `claude/orchestrator_state.md` in the "Verifiers | Prime Intellect" claude.ai Project.
- If your prompt names or attaches a handoff, use the orchestrated-handoffs skill in worker mode. If the skill isn't available: read the handoff first, then the files it points to; do only its scope; list decisions under open questions in its Report instead of making them; work on the branch it names; fill in its Report (or put it in your final message if you can't write the file).
- Never commit, quote or link anything from `handoffs/`. Commit messages, PR descriptions and branch names never mention handoffs or task IDs.
- Don't edit other handoffs or `handoffs/INDEX.md`.

## Project-specific
- Kept distinct from biomed-kg-grounding-eval: that project compares semantic layers over fixed questions; this one holds the data fixed and studies the reward.
- Rewards are computed in code from the trace. Retrieved evidence IDs come only from logged tool calls; claims are structured triples with cited IDs.
- Gold answers and answerability come from the graph via code. AI may draft and critique question items for the train split only, with provenance recorded per item. Labels used to validate the reward are mine alone: never generate, suggest or pre-fill them.
- Earlier hackathon code (Patient-in-the-Graph) belongs to that team. Build this project's verifier fresh; don't copy from it.
