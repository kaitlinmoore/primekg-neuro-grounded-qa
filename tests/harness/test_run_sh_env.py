"""``scripts/run.sh`` loads keys from an env file into ``vf-run``'s environment and
never prints their values.

These tests put a fake ``uv`` first on ``PATH``, so ``run.sh``'s final
``exec uv run vf-run ...`` runs the fake instead. The fake writes the variables it
sees to a file the test reads, so the test can check what reached ``vf-run``
without anything being printed. All values are dummies.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from harness.helpers import REPO

RUN_SH = REPO / "scripts" / "run.sh"
ARGS = ["-m", "placeholder/any-model", "--client.base-url", "http://localhost:1"]
DUMMY = "dummy-key-value-0123456789"
WATCHED = ["NGQ_ANTHROPIC_API_KEY", "NGQ_OTHER", "NGQ_QUOTED", "NGQ_SINGLE", "NGQ_EXPORTED",
           "ANTHROPIC_API_KEY", "NGQ_PRESET"]

FAKE_UV = """#!/usr/bin/env python3
import json, os, sys
names = {names!r}
seen = {{n: os.environ.get(n) for n in names}}
with open(os.environ["FAKE_UV_OUT"], "w") as f:
    json.dump({{"argv": sys.argv[1:], "env": seen}}, f)
"""


@pytest.fixture
def fake_uv(tmp_path: Path) -> Path:
    """A directory holding a fake ``uv`` that records the watched variables."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(FAKE_UV.format(names=WATCHED))
    uv.chmod(0o755)
    return bin_dir


def run(fake_uv: Path, tmp_path: Path, env_text: str | None, extra_env: dict | None = None):
    """Run ``run.sh`` with the fake ``uv`` and an env file holding ``env_text``.

    Returns the completed process and what the fake ``uv`` saw (or None if it never
    ran).
    """
    out = tmp_path / "seen.json"
    env = {k: v for k, v in os.environ.items() if k not in WATCHED}
    env.update({
        "PATH": f"{fake_uv}{os.pathsep}{env.get('PATH', '')}",
        "UV_PROJECT_ENVIRONMENT": env.get("UV_PROJECT_ENVIRONMENT", "x"),
        "FAKE_UV_OUT": str(out),
    })
    if env_text is not None:
        env_file = tmp_path / "dummy.env"
        env_file.write_text(env_text)
        env["NGQ_ENV_FILE"] = str(env_file)
    env.update(extra_env or {})
    result = subprocess.run(["bash", str(RUN_SH), *ARGS], capture_output=True, text=True,
                            env=env, cwd=REPO, timeout=60)
    seen = json.loads(out.read_text()) if out.exists() else None
    return result, seen


def test_key_reaches_vf_run_and_is_never_printed(fake_uv, tmp_path):
    result, seen = run(fake_uv, tmp_path, f"NGQ_ANTHROPIC_API_KEY={DUMMY}\n")
    assert result.returncode == 0, result.stderr
    assert seen["env"]["NGQ_ANTHROPIC_API_KEY"] == DUMMY
    assert seen["argv"][:3] == ["run", "vf-run", "neuro-grounded-qa"]
    assert DUMMY not in result.stdout + result.stderr
    assert DUMMY[:8] not in result.stdout + result.stderr


def test_parses_comments_quotes_and_export(fake_uv, tmp_path):
    text = (
        "# a comment\n"
        "\n"
        f"NGQ_ANTHROPIC_API_KEY={DUMMY}\r\n"  # CRLF from a Windows editor
        'NGQ_QUOTED="has spaces = and equals"\n'
        "NGQ_SINGLE='single'\n"
        "export NGQ_EXPORTED=yes\n"
        "NGQ_OTHER=\n"
    )
    result, seen = run(fake_uv, tmp_path, text)
    assert result.returncode == 0, result.stderr
    env = seen["env"]
    assert env["NGQ_ANTHROPIC_API_KEY"] == DUMMY  # no trailing \r
    assert env["NGQ_QUOTED"] == "has spaces = and equals"
    assert env["NGQ_SINGLE"] == "single"
    assert env["NGQ_EXPORTED"] == "yes"
    assert env["NGQ_OTHER"] == ""
    assert result.stderr == ""


def test_never_exports_anthropic_api_key(fake_uv, tmp_path):
    result, seen = run(fake_uv, tmp_path, f"ANTHROPIC_API_KEY={DUMMY}\nNGQ_OTHER=1\n")
    assert result.returncode == 0, result.stderr
    assert seen["env"]["ANTHROPIC_API_KEY"] is None
    assert seen["env"]["NGQ_OTHER"] == "1"
    assert "not exporting ANTHROPIC_API_KEY" in result.stderr
    assert DUMMY not in result.stdout + result.stderr


def test_environment_wins_over_file(fake_uv, tmp_path):
    result, seen = run(fake_uv, tmp_path, "NGQ_PRESET=from-file\n", {"NGQ_PRESET": "from-env"})
    assert result.returncode == 0, result.stderr
    assert seen["env"]["NGQ_PRESET"] == "from-env"


def test_malformed_line_is_skipped_without_echoing_it(fake_uv, tmp_path):
    result, seen = run(fake_uv, tmp_path, f"not a pair {DUMMY}\nNGQ_OTHER=1\n")
    assert result.returncode == 0, result.stderr
    assert seen["env"]["NGQ_OTHER"] == "1"
    assert "isn't NAME=VALUE" in result.stderr
    assert DUMMY not in result.stdout + result.stderr


def test_missing_named_env_file_refuses(fake_uv, tmp_path):
    result, seen = run(fake_uv, tmp_path, None, {"NGQ_ENV_FILE": str(tmp_path / "absent.env")})
    assert result.returncode == 2
    assert "NGQ_ENV_FILE" in result.stderr
    assert seen is None  # vf-run never ran


def test_refusal_checks_still_come_first(fake_uv, tmp_path):
    """No model: refused before the env file is read or vf-run starts."""
    out = tmp_path / "seen.json"
    env_file = tmp_path / "dummy.env"
    env_file.write_text(f"NGQ_ANTHROPIC_API_KEY={DUMMY}\n")
    env = {**os.environ, "PATH": f"{fake_uv}{os.pathsep}{os.environ['PATH']}",
           "UV_PROJECT_ENVIRONMENT": "x", "FAKE_UV_OUT": str(out), "NGQ_ENV_FILE": str(env_file)}
    result = subprocess.run(["bash", str(RUN_SH), "--client.base-url", "http://localhost:1"],
                            capture_output=True, text=True, env=env, cwd=REPO, timeout=60)
    assert result.returncode == 2
    assert not out.exists()
    assert DUMMY not in result.stdout + result.stderr
