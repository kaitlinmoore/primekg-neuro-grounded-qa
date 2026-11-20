"""The question set: generate, check, view (train only) and spot-check.

Entry point for ``qgen.cli``; see that module for the commands. Run from the repo
root, for example::

    uv run python scripts/data/question_set.py generate
    uv run python scripts/data/question_set.py check-heldout --build-id <build_id>

Every command prints counts, hashes and paths only, and takes an items directory or
a build ID, never a held-out file path.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from qgen.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
