"""Graph data: building the evidence store from PrimeKG, and its on-disk format.

The tools search, and the reward checks claims against, an evidence store: a fixed
subgraph of PrimeKG v2.1 around six neurodegenerative diseases (Alzheimer's,
Parkinson's, frontotemporal dementia, ALS, Huntington's and dementia with Lewy
bodies), where every edge has a stable ``E:`` evidence ID. This package turns the
downloaded release into that store, deterministically, so anyone with the same raw
files gets the same store and the same ``build_id``. No graph data is committed or
shipped in the package: it is downloaded and built locally (several of PrimeKG's
sources have their own terms).

- :mod:`~neuro_grounded_qa.data.build`: the builder and its command line
  (``python -m neuro_grounded_qa.data.build --out <dir>``). It needs duckdb, the
  package's optional ``build`` extra. It deduplicates PrimeKG's reversed rows,
  normalizes hierarchy relations to child → parent and flags them ``directed``,
  assigns entity IDs (``<source>:<id>``) and relation labels, numbers the edges, and
  records a ``build_id``.
- :mod:`~neuro_grounded_qa.data.store_files`: the store directory's layout
  (``build.json`` plus JSON Lines files), shared by the builder and the loader.
  Pure standard library.

Loading a built store is ``neuro_grounded_qa.tools.load_store`` (pure Python, no
duckdb); ``neuro_grounded_qa.tools.read_build_id`` reads only its ``build.json``.
Choices behind the store (seed, subgraph definition, ID and label scheme, direction)
are documented in ``docs/question_set_design.md``, "Evidence store".
"""
