# Changelog

Instrument and design changes, one line each: date | instrument | change | reason. Each line notes whether held-out data was involved.

- 2026-11-18 | platform | runs move to WSL2 (Ubuntu), working on the Windows checkout through /mnt/c; uv, pytest and vf-* run only from WSL | verifiers 0.4.1 imports fcntl and uses os.killpg, so its trace and CLI can't run on native Windows; /mnt/c keeps one checkout shared with Windows tools, at the cost of slower file I/O (no held-out data involved)
- 2026-11-18 | pins | Python 3.11, verifiers==0.4.1 (environment package), prime==0.22.0 via uv tool install (not a project dependency) | exact pins so results are tied to a known library version; verifiers needs >=3.11,<3.15 and prime <3.14 (no held-out data involved)
- 2026-11-18 | layout | virtual uv workspace root (research code, dev tools, one lockfile) plus the environment package environments/neuro_grounded_qa/ (Hub ID neuro-grounded-qa) | only the environment package ships to the Hub; tests, docs and scripts stay out of it (no held-out data involved)
- 2026-11-18 | run config | configs/local.toml as the default: local subprocess agent runtime, outputs/ gitignored, no model or endpoint set | vf-run's defaults send prompts to Prime Inference and run the agent in a Prime sandbox; every run should name its model and endpoint explicitly (no held-out data involved)
- 2026-11-18 | imports | import verifiers.v1 as vf | the top-level verifiers package re-exports nothing in 0.4.1 (no held-out data involved)
- 2026-11-18 | platform | venv moved to the WSL filesystem (~/.venvs/ngq-<checkout>) through a committed .envrc read by direnv; the code stays on /mnt/c | uv run pytest -q took 1m18.6s with the .venv on /mnt/c and 5.4s with the venv on the WSL filesystem (no held-out data involved)
