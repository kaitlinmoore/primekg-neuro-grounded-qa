"""``scripts/run.sh``: refuses without an explicit model and endpoint, passes through
to ``vf-run`` with them."""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from harness.helpers import REPO

RUN_SH = REPO / "scripts" / "run.sh"
MODEL = ["-m", "placeholder/any-model"]
ENDPOINT = ["--client.base-url", "http://localhost:1"]


def _run(args, env_overrides=None, timeout=300):
    env = {**os.environ, "UV_PROJECT_ENVIRONMENT": os.environ.get("UV_PROJECT_ENVIRONMENT", "x")}
    env.update(env_overrides or {})
    env = {k: v for k, v in env.items() if v is not None}
    return subprocess.run(
        ["bash", str(RUN_SH), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        cwd=REPO,
    )


def test_is_executable():
    assert os.access(RUN_SH, os.X_OK)
    assert RUN_SH.read_text().startswith("#!/usr/bin/env bash")


@pytest.mark.parametrize(
    "args, missing",
    [
        ([], "model"),
        ([*ENDPOINT], "model"),
        ([*MODEL], "endpoint"),
        (["-m", *ENDPOINT], "model"),  # flag with no value
        ([*MODEL, "--client.base-url"], "endpoint"),
        (["--model=", *ENDPOINT], "model"),
    ],
)
def test_refuses_without_model_and_endpoint(args, missing):
    result = _run([*args, "--dry-run", "True"])
    assert result.returncode == 2
    assert f"no {missing} given" in result.stderr
    assert "vf-run" not in result.stdout  # never reached vf-run


def test_refuses_without_uv_project_environment():
    result = _run([*MODEL, *ENDPOINT, "--dry-run", "True"], {"UV_PROJECT_ENVIRONMENT": None})
    assert result.returncode == 2
    assert "UV_PROJECT_ENVIRONMENT" in result.stderr


@pytest.mark.skipif(
    shutil.which("uv") is None or not os.environ.get("UV_PROJECT_ENVIRONMENT"),
    reason="needs uv and the project venv",
)
@pytest.mark.parametrize("model_flag", [MODEL, ["--model=placeholder/any-model"]])
def test_passes_through_to_vf_run_dry_run(tmp_path, model_flag):
    result = _run([*model_flag, *ENDPOINT, "-n", "1", "--dry-run", "True", "-o", str(tmp_path)])
    assert result.returncode == 0, result.stderr[-2000:]
    [run_json] = tmp_path.glob("*/configs/resolved/run.json")
    resolved = json.loads(run_json.read_text())
    assert resolved["model"] == "placeholder/any-model"
    assert resolved["client"]["base_url"] == "http://localhost:1"
    # configs/local.toml was applied
    assert resolved["env"]["agent"]["runtime"]["type"] == "subprocess"
    taskset = resolved["env"]["taskset"]
    assert taskset["id"] == "neuro-grounded-qa"
    assert taskset["split"] == "train"
    assert taskset["tools"]["runtime"]["type"] == "subprocess"
    assert taskset["tools"]["max_calls"] == 8
    assert taskset["tools"]["paginate"] is False
