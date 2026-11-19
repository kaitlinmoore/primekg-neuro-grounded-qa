#!/usr/bin/env bash
# Entry point for model runs of the neuro-grounded-qa environment.
#
#   scripts/run.sh -m <model> --client.base-url <endpoint-url> \
#     [--client.api-key-var <ENV_VAR_NAME>] [other vf-run options]
#
# Runs `uv run vf-run neuro-grounded-qa @ configs/local.toml <your arguments>` from
# the repo root. It refuses to start unless the model (-m/--model) and the
# endpoint (--client.base-url) are both given on the command line, because
# vf-run silently falls back to a default model on Prime Inference when they
# are missing. It also refuses when UV_PROJECT_ENVIRONMENT is unset, since uv
# would then build a slow .venv on /mnt/c (see CLAUDE.md, Environment).
#
# Add `--dry-run True` to resolve and write the config without running anything.

set -euo pipefail

usage() {
  sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//' >&2
}

die() {
  echo "run.sh: $*" >&2
  echo >&2
  usage
  exit 2
}

if [[ -z "${UV_PROJECT_ENVIRONMENT:-}" ]]; then
  die 'UV_PROJECT_ENVIRONMENT is not set. Load the env first: eval "$(direnv export bash)"'
fi

model=""
base_url=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  arg="${args[i]}"
  next="${args[i + 1]:-}"
  case "$arg" in
    -m | --model) model="$next" ;;
    --model=*) model="${arg#--model=}" ;;
    --client.base-url) base_url="$next" ;;
    --client.base-url=*) base_url="${arg#--client.base-url=}" ;;
  esac
done

# A value that is empty or looks like another flag means the flag had no value.
if [[ -z "$model" || "$model" == -* ]]; then
  die "no model given: pass -m <model> (or --model <model>)."
fi
if [[ -z "$base_url" || "$base_url" == -* ]]; then
  die "no endpoint given: pass --client.base-url <endpoint-url>."
fi

cd "$(dirname "${BASH_SOURCE[0]}")/.."
exec uv run vf-run neuro-grounded-qa @ configs/local.toml "$@"
