"""The item files: layout, records, counts and the manifest.

An item set lives in ``$NGQ_DATA_DIR/items/<build_id>/`` (never in git):

``train.jsonl``, ``heldout.jsonl``
    One ``QuestionItem`` per line, sorted by item ID, with the item's own fields only
    (:data:`ITEM_FIELDS`), compact JSON, ``\\n`` endings. The held-out file is never
    printed, logged, summarized beyond counts or shown to any AI.
``manifest.json``
    Everything needed to check the set without reading the held-out file: each
    file's SHA-256 and item count, counts per split by type, disease group and
    relation key, the generator version and git commit, the split seed and share, the
    ``build_id``, the store's ``build.json`` SHA-256, the template catalog's SHA-256,
    the filters, plan and tool config, and the selection and funnel counts.
``train_spot_check.md``
    Optional: a seeded sample of train items rendered for review
    (``question_set.py spot-check``).

The directory's name, every item's ``build_id`` and the manifest's ``build_id`` must
agree; :func:`assert_build_ids_agree` checks it before anything is written.
"""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

import qgen_templates as qt
from neuro_grounded_qa.contracts import QuestionItem
from neuro_grounded_qa.data import store_files as sf

from qgen.safety import ItemsError
from qgen.selection import subtype_of

TRAIN_FILE = "train.jsonl"
HELDOUT_FILE = "heldout.jsonl"
MANIFEST = "manifest.json"
SPOT_CHECK = "train_spot_check.md"
SPLIT_FILES = {"train": TRAIN_FILE, "heldout": HELDOUT_FILE}
MANIFEST_FORMAT = "neuro-grounded-qa/question-items-v1"

ITEM_FIELDS = (
    "id", "prompt", "qtype", "answerable", "evidence_scope", "gold_points", "gold_evidence",
    "premise", "graph_gaps", "build_id", "source", "split", "template_id", "notes",
)
"""The fields an item record carries: ``QuestionItem``'s own, not the inherited
``vf.TaskData`` runtime fields (scoring, timeouts, ...), which stay at their defaults."""

REPO_ROOT = Path(__file__).resolve().parents[3]


def items_dir(root: Path, build_id: str) -> Path:
    """``<root>/<build_id>``: where one build's item set lives."""
    return root / build_id


def item_record(item: QuestionItem) -> dict:
    """The JSON record written for one item, keys in :data:`ITEM_FIELDS` order."""
    d = item.model_dump(mode="json", include=set(ITEM_FIELDS))
    return {k: d[k] for k in ITEM_FIELDS}


def write_items(path: Path, items: Iterable[QuestionItem]) -> str:
    """Write items sorted by ID; return the file's SHA-256."""
    return sf.write_jsonl(path, (item_record(i) for i in sorted(items, key=lambda i: i.id)))


def item_meta(item: QuestionItem) -> tuple[str, str, str]:
    """(type key, disease group, relation key) of an item, from its notes and template."""
    notes = json.loads(item.notes)
    return subtype_of(item.qtype, item.answerable), notes["group"], notes["relation_key"]


def item_counts(items: Iterable[QuestionItem]) -> dict:
    """Aggregate counts for one split: total, and by type, group, relation key and
    their combinations (keys joined with ``|``). ``typed_mentions|answerable`` counts
    items that name at least one entity with its type, by answerability class, so the
    typed wording's spread over the classes can be checked without reading items."""
    by: dict[str, Counter] = {k: Counter() for k in (
        "type", "group", "relation_key", "type|group", "type|relation_key", "type|group|relation_key",
        "answerable", "typed_mentions|answerable",
    )}
    n = 0
    for item in items:
        t, g, r = item_meta(item)
        n += 1
        by["answerable"][item.answerable] += 1
        if json.loads(item.notes).get("typed_mentions"):
            by["typed_mentions|answerable"][item.answerable] += 1
        by["type"][t] += 1
        by["group"][g] += 1
        by["relation_key"][r] += 1
        by["type|group"][f"{t}|{g}"] += 1
        by["type|relation_key"][f"{t}|{r}"] += 1
        by["type|group|relation_key"][f"{t}|{g}|{r}"] += 1
    return {"total": n, **{k: dict(sorted(v.items())) for k, v in by.items()}}


def git_state() -> dict:
    """The repo's commit and whether the working tree has changes (None outside git)."""
    def run(*args: str) -> str | None:
        try:
            return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True,
                                  check=True, timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
    commit = run("rev-parse", "HEAD")
    status = run("status", "--porcelain", "--untracked-files=no")
    return {"git_commit": commit, "git_dirty": None if status is None else bool(status)}


def assert_build_ids_agree(out_dir: Path, store_build_id: str, items: Iterable[QuestionItem],
                           manifest_build_id: str) -> None:
    """Refuse to write unless the directory name, the store, the manifest and every
    item name the same ``build_id``. The message names build IDs only."""
    if out_dir.name != store_build_id:
        raise ItemsError(f"output directory {out_dir.name!r} is not named after the store's build_id "
                         f"{store_build_id!r}")
    if manifest_build_id != store_build_id:
        raise ItemsError("manifest build_id differs from the store's")
    bad = sum(1 for i in items if i.build_id != store_build_id)
    if bad:
        raise ItemsError(f"{bad} items carry a build_id other than the store's {store_build_id!r}")


def write_manifest(out_dir: Path, manifest: dict) -> str:
    """Write ``manifest.json`` (indented, deterministic); return its SHA-256."""
    text = json.dumps(manifest, ensure_ascii=False, indent=1) + "\n"
    path = out_dir / MANIFEST
    path.write_text(text, encoding="utf-8", newline="\n")
    return sf.file_sha256(path)


def read_manifest(d: Path) -> dict:
    path = d / MANIFEST
    if not path.is_file():
        raise ItemsError(f"no {MANIFEST} in {d}")
    m = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(m, dict) or m.get("format") != MANIFEST_FORMAT:
        raise ItemsError(f"{path}: not an item-set manifest ({MANIFEST_FORMAT})")
    return m


def template_relation_keys() -> dict[str, qt.Template]:
    """Template ID -> template, for checking an item's template fields."""
    return {t.id: t for t in qt.catalog()}
