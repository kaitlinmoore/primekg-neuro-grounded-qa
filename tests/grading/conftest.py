"""Fixtures for the grading-tool tests: one drawn and exported fixture round per
session, copied fresh for each test that writes to it."""

from __future__ import annotations

import shutil

import pytest

from .helpers import make_round


@pytest.fixture(scope="session")
def pristine_round(tmp_path_factory):
    """The fixture runs drawn and exported once (read-only for tests)."""
    return make_round(tmp_path_factory.mktemp("pristine"))


@pytest.fixture
def rd(pristine_round, tmp_path):
    """A fresh copy of the fixture round for one test."""
    out = tmp_path / "round"
    shutil.copytree(pristine_round, out)
    return out
