"""Loading the evidence store the toolset serves.

The toolset runs as its own MCP server process (see ``tools.kg``). That process gets
only its JSON config, so it can't be handed a store object: it loads one from
``store_path`` at startup. This module is that one loading step, kept separate so the
real store, built from the downloaded graph, can plug in without touching the tools.

**Formats:**

- A **store directory**, as written by the store builder
  (``python -m neuro_grounded_qa.data.build``): ``build.json`` plus JSON Lines files,
  layout in :mod:`neuro_grounded_qa.data.store_files`. ``store_path`` may name the
  directory or its ``build.json``. This is the real PrimeKG store.
- A ``.json`` file in the ``InMemoryEvidenceStore.from_records`` format
  (``entities``, ``edges`` with entity-ID references, optional ``build_id``), the
  format of ``tests/fixtures/contracts/mini_store.json``.

Both load into an :class:`~neuro_grounded_qa.contracts.InMemoryEvidenceStore`, in
pure Python: no database or other dependency at run time. :func:`read_build_id` reads
only the store's build ID, for run provenance; for a store directory it reads
``build.json`` alone and never touches the edges. Both functions take the ID from the
same place, so they agree.

The toolset needs nothing else from a store: it calls only ``build_id``, ``get``,
``entity``, ``search_entities``, ``neighbors`` and ``relations``. Loading happens once
per tool server process, inside ``Toolset.setup``, under the server's startup timeout
(600 s by default in verifiers 0.4.1); the PrimeKG store (about 45,000 edges) loads in
a few seconds.
"""

from __future__ import annotations

import json
from pathlib import Path

from neuro_grounded_qa.contracts import EvidenceStore, InMemoryEvidenceStore
from neuro_grounded_qa.data import store_files

_UNKNOWN = (
    "unknown evidence store format (supported: a store directory with build.json, "
    "or a .json records file; see neuro_grounded_qa.tools.store)"
)


def _store_dir(path: Path) -> Path | None:
    """The store directory ``path`` names (the directory or its ``build.json``), or None."""
    if path.is_file() and path.name == store_files.BUILD_JSON:
        path = path.parent
    return path if store_files.is_store_dir(path) else None


def load_store(path: str | Path) -> EvidenceStore:
    """Load the evidence store at ``path``.

    Args:
        path: path to the store: a store directory (or its ``build.json``), or a
            ``.json`` records file. Relative paths resolve against the current working
            directory, so callers that hand the path to another process should
            resolve it first (the taskset does).

    Raises:
        FileNotFoundError: if nothing exists at ``path``.
        ValueError: if the path's format isn't one this function knows, the file
            isn't a valid store, or a store directory's files don't match the hashes
            in its ``build.json``.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"evidence store not found: {path}")
    if (store_dir := _store_dir(path)) is not None:
        return InMemoryEvidenceStore.from_records(store_files.read_records(store_dir))
    if path.is_file() and path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path}: expected a JSON object with entities and edges")
        return InMemoryEvidenceStore.from_records(data)
    raise ValueError(f"{path}: {_UNKNOWN}")


def read_build_id(path: str | Path) -> str:
    """The ``build_id`` of the store at ``path``, without building the store.

    Used by the host process to record which store a run used (``trace.info``),
    where loading the whole store would be wasted work: only the tool server needs
    it. For a store directory it reads ``build.json`` only (a few kilobytes), so it
    stays cheap however large the store is. For a ``.json`` records file it parses
    the JSON but builds no indexes; a missing ``build_id`` reads as ``"fixture"``, as
    in ``InMemoryEvidenceStore.from_records``.

    Raises:
        FileNotFoundError: if nothing exists at ``path``.
        ValueError: as in :func:`load_store`.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"evidence store not found: {path}")
    if (store_dir := _store_dir(path)) is not None:
        return store_files.read_build_meta(store_dir)["build_id"]
    if path.is_file() and path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path}: expected a JSON object with entities and edges")
        return str(data.get("build_id", "fixture"))
    raise ValueError(f"{path}: {_UNKNOWN}")
