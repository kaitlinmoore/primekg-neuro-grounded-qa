"""The evidence store's on-disk format: one directory, plain JSON Lines, pure Python.

The builder (:mod:`neuro_grounded_qa.data.build`) writes a store directory; the tool
server reads it through ``tools.store.load_store``. This module is the one place both
sides get the file names and record shapes from, so the writer and the reader can't
drift apart. It needs nothing beyond the standard library and the contracts, so
loading a store adds no runtime dependency to the environment package.

A store directory holds:

``build.json``
    Small metadata: ``build_id``, the full build config, raw-file hashes, counts,
    checks, creation time, and the SHA-256 of each data file below. Read on its own
    by :func:`read_build_meta`, which is how a host process records which store a run
    used without loading the graph.
``entities.jsonl``
    One :class:`~neuro_grounded_qa.contracts.Entity` per line (``id``, ``name``,
    ``type``, ``source``), sorted by ``id``.
``edges.jsonl``
    One edge per line in the ``InMemoryEvidenceStore.from_records`` shape (``id``,
    ``subject`` and ``object`` as entity IDs, ``relation``, ``display_relation``,
    ``directed``), sorted by evidence ID.
``entity_sources.jsonl``, ``edge_sources.jsonl``
    Provenance, not loaded by the tools: where each entity and edge came from in the
    raw release (PrimeKG node index and source ID; raw ``kg.csv`` row numbers). Used
    by the cross-check and the spot-check sample.

Every file is written with sorted rows, a fixed key order, ``\\n`` line endings and no
timestamps (except ``build.json``'s ``created``), so the same build config produces
byte-identical data files.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

BUILD_JSON = "build.json"
ENTITIES = "entities.jsonl"
EDGES = "edges.jsonl"
ENTITY_SOURCES = "entity_sources.jsonl"
EDGE_SOURCES = "edge_sources.jsonl"

DATA_FILES = (ENTITIES, EDGES, ENTITY_SOURCES, EDGE_SOURCES)
"""The files whose hashes ``build.json`` records."""

LOADED_FILES = (ENTITIES, EDGES)
"""The files :func:`read_records` reads and checks against ``build.json``."""

FORMAT = "neuro-grounded-qa/evidence-store-jsonl"
"""``build.json``'s ``format`` value, so a reader can tell this layout from others."""


def is_store_dir(path: Path) -> bool:
    """True if ``path`` is a directory holding a ``build.json``."""
    return path.is_dir() and (path / BUILD_JSON).is_file()


def dumps_line(record: Mapping[str, Any]) -> str:
    """One JSON Lines row: compact, key order as given, non-ASCII kept as is."""
    return json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"


def write_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> str:
    """Write ``records`` as JSON Lines (UTF-8, ``\\n`` endings); return the file's SHA-256.

    The caller sorts the records: this function keeps their order, so output is
    deterministic exactly when the input order is.
    """
    h = hashlib.sha256()
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for record in records:
            line = dumps_line(record)
            f.write(line)
            h.update(line.encode("utf-8"))
    return h.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSON Lines file into a list of dicts (blank lines skipped)."""
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def file_sha256(path: Path) -> str:
    """SHA-256 of a file's bytes, read in chunks."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def write_build_meta(store_dir: Path, meta: Mapping[str, Any]) -> None:
    """Write ``build.json`` (indented, keys in the order given)."""
    text = json.dumps(meta, ensure_ascii=False, indent=1) + "\n"
    (store_dir / BUILD_JSON).write_text(text, encoding="utf-8", newline="\n")


def read_build_meta(store_dir: Path) -> dict[str, Any]:
    """Read ``build.json`` alone. Cheap: it never touches the edge files.

    Raises:
        ValueError: if the file isn't a JSON object of this format with a
            ``build_id``.
    """
    path = store_dir / BUILD_JSON
    meta = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(meta, dict) or meta.get("format") != FORMAT:
        raise ValueError(f"{path}: not an evidence store build file (format {FORMAT!r})")
    if not isinstance(meta.get("build_id"), str) or not meta["build_id"]:
        raise ValueError(f"{path}: missing build_id")
    return meta


def read_records(store_dir: Path) -> dict[str, Any]:
    """Read a store directory into the ``InMemoryEvidenceStore.from_records`` dict.

    The ``build_id`` comes from ``build.json``, the same place :func:`read_build_meta`
    reads it, so a loaded store and a provenance record always agree on the ID.
    Each loaded file's SHA-256 is checked against ``build.json`` first, so a store
    whose files were edited or mixed with another build's fails to load instead of
    serving edges under the wrong ``build_id``.

    Raises:
        ValueError: if ``build.json`` is invalid or a file's hash doesn't match.
    """
    meta = read_build_meta(store_dir)
    hashes = meta.get("files", {})
    for name in LOADED_FILES:
        expected = hashes.get(name, {}).get("sha256")
        actual = file_sha256(store_dir / name)
        if expected != actual:
            raise ValueError(
                f"{store_dir / name}: SHA-256 {actual} does not match build.json "
                f"({expected}); the file is not from build {meta['build_id']}"
            )
    return {
        "build_id": meta["build_id"],
        "entities": read_jsonl(store_dir / ENTITIES),
        "edges": read_jsonl(store_dir / EDGES),
    }
