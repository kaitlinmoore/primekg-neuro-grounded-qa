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
#
# Keys come from .env at the repo root (or the file NGQ_ENV_FILE names), if it
# exists. Pass the key's variable name with --client.api-key-var, for example
# NGQ_ANTHROPIC_API_KEY. Values are never printed.

set -euo pipefail

usage() {
  sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//' >&2
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

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Load KEY=VALUE lines from the env file into the environment of vf-run.
# - Exports only the names the file defines; blank lines and # comments are
#   skipped, an optional leading `export ` is allowed, and one pair of matching
#   surrounding quotes is removed from the value. The file is parsed, never
#   sourced, so nothing in it runs.
# - A variable already set in the environment wins over the file.
# - ANTHROPIC_API_KEY is never exported: Claude Code reads that name, and a
#   session that inherits it bills its own usage to the key. Name the key
#   something else (NGQ_ANTHROPIC_API_KEY) and pass it with --client.api-key-var.
# - Messages name variables only, never values.
load_env_file() {
  local file="$1" line name value
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line#export }"
    if [[ ! "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      echo "run.sh: skipping a line in $file that isn't NAME=VALUE" >&2
      continue
    fi
    name="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    if [[ "$name" == "ANTHROPIC_API_KEY" ]]; then
      echo "run.sh: not exporting ANTHROPIC_API_KEY from $file; name the key NGQ_ANTHROPIC_API_KEY instead" >&2
      continue
    fi
    if [[ "$value" =~ ^\"(.*)\"$ || "$value" =~ ^\'(.*)\'$ ]]; then
      value="${BASH_REMATCH[1]}"
    fi
    [[ -n "${!name+set}" ]] && continue
    export "$name=$value"
  done <"$file"
}

env_file="${NGQ_ENV_FILE:-$repo/.env}"
if [[ -f "$env_file" ]]; then
  load_env_file "$env_file"
elif [[ -n "${NGQ_ENV_FILE:-}" ]]; then
  die "NGQ_ENV_FILE names $NGQ_ENV_FILE, which doesn't exist."
fi

cd "$repo"
exec uv run vf-run neuro-grounded-qa @ configs/local.toml "$@"
