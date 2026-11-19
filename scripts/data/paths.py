"""Where raw and derived graph data live.

Graph data never goes into git or the package (several PrimeKG/OptimusKG sources
are non-commercial or license-gated). Everything is read from and written to
``NGQ_DATA_DIR``. If the variable is unset, the default is ``<repo>/data``, which
is gitignored. In practice set it to a path on the WSL filesystem (for example
``$HOME/data/ngq``): the repo sits on ``/mnt/c``, where large-file I/O is slow.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """Return the data root, from ``NGQ_DATA_DIR`` or ``<repo>/data``."""
    env = os.environ.get("NGQ_DATA_DIR")
    return Path(env).expanduser() if env else REPO_ROOT / "data"


def raw_dir(graph: str) -> Path:
    """Directory holding the downloaded files for one graph (``primekg`` or ``optimuskg``)."""
    return data_dir() / "raw" / graph


def meta_dir() -> Path:
    """Directory holding the download manifest and Dataverse metadata."""
    return data_dir() / "meta"


def derived_dir() -> Path:
    """Directory holding measurement outputs (JSON results, subgraph extracts)."""
    return data_dir() / "derived"
