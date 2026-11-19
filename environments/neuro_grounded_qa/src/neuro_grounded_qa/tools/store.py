"""Loading the evidence store the toolset serves.

The toolset runs as its own MCP server process (see ``tools.kg``). That process gets
only its JSON config, so it can't be handed a store object: it loads one from
``store_path`` at startup. This module is that one loading step, kept separate so the
real store, built from the downloaded graph, can plug in without touching the tools.

**Formats today:** a ``.json`` file in the ``InMemoryEvidenceStore.from_records``
format (``entities``, ``edges`` with entity-ID references, optional ``build_id``), the
format of ``tests/fixtures/contracts/mini_store.json``.

**Adding the real store:** whatever the store builder produces must satisfy
``contracts.EvidenceStore``. Add a branch to :func:`load_store` that recognizes its
path (a suffix such as ``.duckdb``, or a directory) and returns that store. The
toolset needs nothing else: it calls only ``build_id``, ``get``, ``entity``,
``search_entities``, ``neighbors`` and ``relations``. Loading happens once per tool
server process, inside ``Toolset.setup``, under the server's startup timeout
(600 s by default in verifiers 0.4.1).
"""

from __future__ import annotations

import json
from pathlib import Path

from neuro_grounded_qa.contracts import EvidenceStore, InMemoryEvidenceStore


def load_store(path: str | Path) -> EvidenceStore:
    """Load the evidence store at ``path``.

    Args:
        path: path to the store. Relative paths resolve against the current working
            directory, so callers that hand the path to another process should
            resolve it first (the taskset does).

    Raises:
        FileNotFoundError: if nothing exists at ``path``.
        ValueError: if the path's format isn't one this function knows, or the file
            isn't a valid store.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"evidence store not found: {path}")
    if path.is_file() and path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path}: expected a JSON object with entities and edges")
        return InMemoryEvidenceStore.from_records(data)
    raise ValueError(
        f"{path}: unknown evidence store format (supported: a .json records file; "
        "see neuro_grounded_qa.tools.store)"
    )
