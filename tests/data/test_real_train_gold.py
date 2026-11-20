"""Opt-in: the gold-completeness check over every real **train** item.

Skipped unless ``NGQ_ITEMS_DIR`` names an item-set directory
(``$NGQ_DATA_DIR/items/<build_id>``), the same switch as the route replay
(``tests/integration/test_real_train_replay.py``). The store is ``NGQ_STORE_DIR`` if
set, else ``$NGQ_DATA_DIR/store/primekg-neuro-c95``. Run it with::

    NGQ_ITEMS_DIR="$NGQ_DATA_DIR/items/<build_id>" \\
        uv run pytest -q tests/data/test_real_train_gold.py

It reads ``train.jsonl`` only, through the train-only viewer's loader
(``qgen.view.load_train``), which refuses a held-out path or a train file holding a
held-out record. It reads the manifest for the train file's recorded SHA-256, and
writes nothing. Held-out items are never read: their completeness follows from the
same generator code (``qgen.answers``, run by ``build_pool`` on every candidate of
both splits) and is re-checked by ``question_set.py check-heldout``, which prints
counts only.

On failure it reports counts by template family and reason, no item content.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import pytest
from neuro_grounded_qa.data import store_files as sf
from neuro_grounded_qa.tools import load_store
from qgen import files as F
from qgen.answers import Adjacency, gold_completeness
from qgen.view import load_train

ITEMS_DIR = os.environ.get("NGQ_ITEMS_DIR")

pytestmark = pytest.mark.skipif(
    not ITEMS_DIR, reason="set NGQ_ITEMS_DIR to an item-set directory to check real train gold"
)


def items_dir() -> Path:
    d = Path(ITEMS_DIR or "").expanduser()
    if not d.is_dir() or "heldout" in d.name:
        pytest.fail("NGQ_ITEMS_DIR must be an item-set directory; this test reads its train.jsonl only")
    return d


def store_dir() -> Path:
    s = os.environ.get("NGQ_STORE_DIR")
    if s:
        return Path(s).expanduser()
    return Path(os.environ.get("NGQ_DATA_DIR", "data")).expanduser() / "store" / "primekg-neuro-c95"


def test_every_train_item_has_complete_gold():
    d = items_dir()
    train = d / F.TRAIN_FILE
    manifest = json.loads((d / F.MANIFEST).read_text(encoding="utf-8"))
    # the check reads the items; it must not have changed them
    assert sf.file_sha256(train) == manifest["files"][F.TRAIN_FILE]["sha256"]

    items = load_train(d)
    store = load_store(store_dir())
    assert all(i.build_id == store.build_id for i in items)
    adj = Adjacency(store.get(r["id"]) for r in sf.read_jsonl(store_dir() / sf.EDGES))

    results: Counter = Counter()
    for it in items:
        r = gold_completeness(it, json.loads(it.notes)["template_params"], adj)
        results[(it.template_id.split(".")[0], r.reason)] += 1
    failed = {k: v for k, v in results.items() if k[1] != "ok"}
    assert not failed, f"incomplete gold, by (family, reason): {sorted(failed.items())}"
    assert sum(results.values()) == len(items) == manifest["files"][F.TRAIN_FILE]["items"]
    print("train gold complete, by family:", dict(sorted((k[0], v) for k, v in results.items())))
