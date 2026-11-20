"""The train-only viewer: train items rendered as readable markdown.

For reviewing item quality (Kaitlin's domain review; possibly AI critique of train
items later). It reads **only** ``train.jsonl``:

- given an items directory, it opens ``<dir>/train.jsonl`` and nothing else;
- given a file, it refuses unless the file is named ``train.jsonl``;
- it refuses the whole file, before rendering anything, if any record's ``split``
  is not ``"train"``, and says only which line, never what is on it.

Names come from the store when one is given (gold points then read "Gene X
(NCBIGene:1) — disease_protein — Disease Y (MONDO:2)"); without a store the viewer
shows IDs only.

The spot check (:func:`spot_check_sample`) is a seeded sample stratified by type,
for the same review. It is not a source of reward-validation labels.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from neuro_grounded_qa.contracts import QuestionItem
from pydantic import ValidationError

from qgen import checks as qc
from qgen.files import TRAIN_FILE, item_meta
from qgen.safety import ItemsError
from qgen.selection import Plan, apportion, subtype_targets

SPOT_SEED = "ngq-spot-v1"
"""Seed of the spot-check sample. Independent of the split seed."""


def train_path(path: Path) -> Path:
    """The train file ``path`` names: ``<dir>/train.jsonl`` or the file itself.

    Raises ItemsError for any other file name (the held-out file included)."""
    if path.is_dir():
        return path / TRAIN_FILE
    if path.name != TRAIN_FILE:
        raise ItemsError(f"the viewer reads {TRAIN_FILE} only; refusing {path.name!r}")
    return path


def load_train(path: Path) -> list[QuestionItem]:
    """Read train items, refusing the file if any record isn't a train item."""
    p = train_path(path)
    records = []
    for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            raise ItemsError(f"{p.name}:{lineno}: not JSON") from None
        if not isinstance(rec, dict) or rec.get("split") != "train":
            raise ItemsError(f"{p.name}:{lineno}: not a train item; refusing to show this file")
        records.append((lineno, rec))
    items = []
    for lineno, rec in records:
        try:
            items.append(QuestionItem.model_validate(rec))
        except ValidationError:
            raise ItemsError(f"{p.name}:{lineno}: invalid item") from None
    return items


def select_items(items: Iterable[QuestionItem], *, qtype: str | None = None, answerable: str | None = None,
                 template: str | None = None, group: str | None = None, ids: Iterable[str] = (),
                 contains: str | None = None) -> list[QuestionItem]:
    """Filter items. ``template`` matches a template ID or its family prefix
    (``sh_evid`` matches ``sh_evid.indication``); ``contains`` searches the question
    text, case-insensitively."""
    want_ids = set(ids)
    out = []
    for it in items:
        _, g, _ = item_meta(it)
        if qtype and it.qtype != qtype:
            continue
        if answerable and it.answerable != answerable:
            continue
        if template and not (it.template_id == template or (it.template_id or "").startswith(template + ".")):
            continue
        if group and g != group:
            continue
        if want_ids and it.id not in want_ids:
            continue
        if contains and contains.casefold() not in it.prompt_text.casefold():
            continue
        out.append(it)
    return out


def _ent(store, eid: str) -> str:
    e = store.entity(eid) if store is not None else None
    return f"{e.name} (`{eid}`)" if e is not None else f"`{eid}`"


def _triple(store, s: str, r: str, o: str, directed: bool) -> str:
    arrow = " (directed: subject is the child)" if directed else ""
    return f"{_ent(store, s)} — `{r}` — {_ent(store, o)}{arrow}"


def render_item(item: QuestionItem, store=None) -> str:
    """One item as markdown: question, labels, gold, premise, gaps and route."""
    t, g, rk = item_meta(item)
    notes = json.loads(item.notes)
    out = [f"### `{item.id}` · {t} · `{item.template_id}` · {g}", "", f"**Question:** {item.prompt_text}", ""]
    out.append(f"- answerable: `{item.answerable}`; relation key: `{rk}`")
    untyped = [x for x in notes.get("named", []) if x not in notes.get("typed_mentions", [])]
    if notes.get("typed_mentions") and untyped:  # only under the older mixed rule
        out.append("- typed mentions (name shared with another node): "
                   + ", ".join(_ent(store, x) for x in notes["typed_mentions"]))
    if item.gold_points:
        out.append("- gold points:")
        for gp in item.gold_points:
            out.append(f"  - {_triple(store, gp.subject_id, gp.relation, gp.object_id, gp.directed)} "
                       f"[{', '.join(gp.evidence)}]")
    else:
        out.append("- gold points: none (abstain)")
    if item.premise:
        p = item.premise
        out.append(f"- premise (false): {_triple(store, p.subject_id, p.relation, p.object_id, p.directed)}")
    for gap in item.graph_gaps:
        tr = gap.triple
        extra = f"; triple {_triple(store, tr.subject_id, tr.relation, tr.object_id, tr.directed)}" if tr else ""
        out.append(f"- graph gap: {gap.description}{extra}")
    route = qc.Route.from_notes(notes["route"])
    steps = [f"search {_ent(store, s)}" for s in route.searches]
    steps += [f"get_neighbors({_ent(store, e)}" + (f", `{r}`)" if r else ")") for e, r in route.probes]
    out.append(f"- route ({route.calls} calls): " + "; ".join(steps))
    out.append("")
    return "\n".join(out)


def render(items: list[QuestionItem], store=None, title: str = "Train items") -> str:
    head = [f"# {title}", "", f"{len(items)} items. Train split only.", ""]
    return "\n".join(head + [render_item(i, store) for i in items])


def spot_check_sample(items: list[QuestionItem], n: int = 20, seed: str = SPOT_SEED,
                      plan: Plan | None = None) -> list[QuestionItem]:
    """A seeded sample of ``n`` train items, stratified by type at the plan's shares.

    Per type key, the items come in ``sha256(seed, item ID)`` order. A type with too
    few items gives its remaining slots to the next types in order."""
    shares = subtype_targets(plan or Plan(), "train")
    quota = apportion(n, {k: float(v) for k, v in shares.items()})
    by_type: dict[str, list[QuestionItem]] = {}
    for it in items:
        by_type.setdefault(item_meta(it)[0], []).append(it)
    for v in by_type.values():
        v.sort(key=lambda i: qc.split_hash(i.id, seed))
    out: list[QuestionItem] = []
    spare = 0
    for t in shares:
        k = quota.get(t, 0) + spare
        got = by_type.get(t, [])[:k]
        spare = k - len(got)
        out += got
    return out
