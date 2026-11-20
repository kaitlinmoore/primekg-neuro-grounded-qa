"""Shared paths and imports for the run-analysis tests.

The scripts under ``scripts/analysis/`` aren't a package; the tests import them by
putting that directory (and ``scripts/data``, for the train-only loader) on the path.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ANALYSIS = REPO / "scripts" / "analysis"
FIXTURES = REPO / "tests" / "fixtures" / "analysis"
STUB_RUN = FIXTURES / "stub_run"
"""A real ``vf-run`` of a scripted stub policy over the contracts' fixture items
(no model): budget overrun, relation filters and an unknown relation, a prose reply,
abstentions, a duplicate claim, a voided claim and a group-member ID."""

for p in (ANALYSIS, REPO / "scripts" / "data"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
