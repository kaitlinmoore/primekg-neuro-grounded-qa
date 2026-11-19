"""Shared pytest setup for the project's tests.

Tests run from the workspace root with ``uv run pytest``. The environment package
is installed editable into the workspace venv by ``uv sync``, so tests import it
as ``neuro_grounded_qa`` with no path manipulation.

Small committed fixtures live in ``tests/fixtures/<area>/``. Shared fixtures go
here as they are needed.
"""

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """Path to ``tests/fixtures/``."""
    return FIXTURES_DIR
