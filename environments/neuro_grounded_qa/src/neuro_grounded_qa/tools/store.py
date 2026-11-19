"""Loading the evidence store the toolset serves.

The toolset runs as its own MCP server process (see ``tools.kg``). That process gets
only its JSON config, so it can't be handed a store object: it loads one from
``store_path`` at startup. This module is that one loading step, kept separate so the
real store, built from the downloaded graph, can plug in without touching the tools.

**Formats today:** a ``.json`` file in the ``InMemoryEvidenceStore.from_records``
format (``entities``, ``edges`` with entity-ID references, optional ``build_id``), the
format of ``tests/fixtures/contracts/mini_store.json``. :func:`read_build_id` reads
only the store's build ID, for run provenance.

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


def read_build_id(path: str | Path) -> str:
    """The ``build_id`` of the store at ``path``, without building the store.

    Used by the host process to record which store a run used (``trace.info``),
    where loading the whole store would be wasted work: only the tool server needs
    it. For a ``.json`` records file this parses the JSON but builds no indexes; a
    missing ``build_id`` reads as ``"fixture"``, as in
    ``InMemoryEvidenceStore.from_records``.

    **Adding the real store:** give it a branch here too, reading the ID from the
    store's own metadata (for example a one-row metadata table, or a small sidecar
    file next to it) so this stays cheap however large the store is.

    Raises:
        FileNotFoundError: if nothing exists at ``path``.
        ValueError: as in :func:`load_store`.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"evidence store not found: {path}")
    if path.is_file() and path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path}: expected a JSON object with entities and edges")
        return str(data.get("build_id", "fixture"))
    raise ValueError(
        f"{path}: unknown evidence store format (supported: a .json records file; "
        "see neuro_grounded_qa.tools.store)"
    )
