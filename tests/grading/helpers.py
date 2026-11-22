"""Shared paths and setup for the grading-tool tests.

The scripts under ``scripts/grading/`` aren't a package; importing ``grading_common``
puts that directory and ``scripts/data`` on the path. All tests run on the synthetic
fixtures in ``tests/fixtures/grading/`` (rebuild them with its
``build_fixtures.py``); no real data is read.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
GRADING = REPO / "scripts" / "grading"
if str(GRADING) not in sys.path:
    sys.path.insert(0, str(GRADING))

FIXTURES = REPO / "tests" / "fixtures" / "grading"
ITEMS = FIXTURES / "items"
STORE = FIXTURES / "store.json"
RUN_PARENT_CHILD = FIXTURES / "runs" / "parent-child"
"""Served directed edges with PrimeKG's label, like train run 3."""
RUN_DIRECTION = FIXTURES / "runs" / "direction-labels"
"""Served directed edges through ``tools.kg.served_edge``, like train run 4."""
RUNS = (RUN_PARENT_CHILD, RUN_DIRECTION)
EXCLUDE = ("docs/*.md",)
"""Exclusion globs relative to FIXTURES: ``docs/mentions.md`` names one item."""
MENTIONED_ITEM = "q-0000000000a3"
PER_CELL = 2
SEALED = 1

SENTINEL_STRINGS = (
    "sentinel-model-older",
    "sentinel-model-newer",
    "tpl_SENTINEL_TEMPLATE",
    "SENTINEL_NOTES_TEXT",
    "SENTINEL_REWARD_PARAM",
    "SENTINEL_ENDPOINT",
    "sentinel-endpoint.invalid",
    "SENTINEL_SYSTEM_PROMPT",
    "SENTINEL_INTERMEDIATE_TEXT",
    "SENTINEL_REASONING_TEXT",
    "0.987654",
    "0.123457",
    "7777777",
    "fixture-grading-store-v1",
    "single_hop",
    "unanswerable",
    "false_premise",
)
"""Values of hidden fields in the fixture traces and items; none may reach an export.
(``partial`` is also a qtype, but it is an ``answerable`` value the grader sees.)"""


def run_dir(parent: Path) -> Path:
    """The one run directory under a fixture run parent."""
    (found,) = sorted(p.parent for p in parent.glob("*/traces.jsonl"))
    return found


def saved_traces(parent: Path) -> list[dict[str, Any]]:
    """Every saved trace of a fixture run, in file order."""
    path = run_dir(parent) / "traces.jsonl"
    return [json.loads(line)["traces"][0] for line in path.read_text("utf-8").splitlines() if line]


def make_round(tmp: Path, *, per_cell: int = PER_CELL, sealed: int = SEALED) -> Path:
    """Draw and export the fixture runs into ``tmp/round``; returns the round dir."""
    import draw
    import export

    out = tmp / "round"
    draw.run_draw(ITEMS, list(RUNS), out, per_cell=per_cell, sealed_per_answerable=sealed,
                  exclude_from=EXCLUDE, exclude_root=FIXTURES, today="2026-01-01")
    export.run_export(out, ITEMS)
    return out
