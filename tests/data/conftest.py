"""Setup for the data tests.

The code under test lives in ``scripts/data/`` (research scripts, not part of
the shipped package), so its directory is put on ``sys.path`` here. The tests
need duckdb from the ``data`` dependency group; without it they are skipped
with a message rather than failing on import:

    uv run --group data pytest tests/data
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS_DATA = Path(__file__).resolve().parents[2] / "scripts" / "data"
sys.path.insert(0, str(SCRIPTS_DATA))

duckdb = pytest.importorskip("duckdb", reason="install the data group: uv sync --group data")


@pytest.fixture
def tiny(fixtures_dir):
    """The synthetic graph in tests/fixtures/data, loaded and canonicalized, seed {D1, D2}.

    Hand-computed facts about it are in test_subgraph.py's module docstring.
    """
    import load
    import subgraph as sg

    con = duckdb.connect()
    d = fixtures_dir / "data"
    load.load_csv_graph(con, d / "tiny_nodes.csv", d / "tiny_edges.csv")
    stats = sg.canonicalize_edges(con)
    sg.build_adjacency(con)
    sg.compute_degree(con)
    sg.set_seed(con, ["D1", "D2"])
    yield SimpleNamespace(con=con, stats=stats)
    con.close()
