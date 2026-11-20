"""Seeded, deterministic selection of items to the targets, per split, held-out first.

Settled with Kaitlin on 2026-11-19 (``docs/question_set_design.md``, "Balance and
sampling" and "Split plan and held-out protection"):

- 200 train + 100 held-out, by type 30/25/15/20/10% (single-hop, multi-hop,
  partial, unanswerable, false premise), false premises 50/50 ``no``/``correctable``;
- disease-group quotas AD 30, PD 20, ALS 15, FTD 15 (with the FTD/ALS nodes),
  HD 12, DLB 8 percent, per split and type, by largest-remainder rounding;
- no relation key over 40% of a type in a split; for false premises, 40% of each
  kind (``no`` and ``correctable`` separately; revision 2, ``Plan.fp_cap_per_kind``);
- split seed ``ngq-split-v1``, held-out share 1/3, held-out selected first.

The split itself is not made here: each candidate's split is ``checks.split_of`` of
its item ID, fixed before selection. Selection only picks, within each split's own
pool, which candidates become items. Held-out selection never looks at train; train
then excludes the facts and named-entity sets the held-out items used.

Everything this module returns besides the chosen candidates is a count.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field

from qgen import checks as qc
from qgen.candidates import Cand

SPLIT_SEED = "ngq-split-v1"
"""The split seed. Changing it reassigns every candidate, so it is set once."""
HELDOUT_SHARE = 1 / 3
"""Each candidate is held out with this probability (100 of 300 items)."""


@dataclass(frozen=True)
class Plan:
    """The item-set targets and the selection rules (defaults: the settled values)."""

    total: dict[str, int] = field(default_factory=lambda: {"train": 200, "heldout": 100})
    type_share: dict[str, float] = field(default_factory=lambda: {
        "single_hop": 0.30, "multi_hop": 0.25, "partial": 0.15, "unanswerable": 0.20, "false_premise": 0.10,
    })
    fp_correctable_share: float = 0.5
    group_share: dict[str, float] = field(default_factory=lambda: {
        "AD": 0.30, "PD": 0.20, "ALS": 0.15, "FTD": 0.15, "HD": 0.12, "DLB": 0.08,
    })
    relation_cap: float = 0.4
    fp_cap_per_kind: bool = True
    """Apply the relation cap within each false-premise kind (``no``,
    ``correctable``) rather than over all false premises (the first set's rule)."""
    text_dup_jaccard: float = 0.9
    split_seed: str = SPLIT_SEED
    heldout_share: float = HELDOUT_SHARE
    headroom: int = 3

    def as_json(self) -> dict:
        return asdict(self)


def apportion(total: int, shares: dict[str, float]) -> dict[str, int]:
    """Largest-remainder rounding of ``total`` by ``shares`` (deterministic)."""
    raw = {k: total * v / sum(shares.values()) for k, v in shares.items()}
    out = {k: int(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: (-(raw[k] - out[k]), k))[: total - sum(out.values())]:
        out[k] += 1
    return out


def targets(plan: Plan) -> dict[str, dict[str, int]]:
    """Items per split and type (false premises not yet split)."""
    return {split: apportion(n, plan.type_share) for split, n in plan.total.items()}


def subtype_targets(plan: Plan, split: str) -> dict[str, int]:
    """Items per type key for ``split``, false premises split into no/correctable."""
    tgt = targets(plan)[split]
    fp = tgt["false_premise"]
    out = {k: v for k, v in tgt.items() if k != "false_premise"}
    out["false_premise:correctable"] = round(fp * plan.fp_correctable_share)
    out["false_premise:no"] = fp - out["false_premise:correctable"]
    return out


def subtype_of(qtype: str, answerable: str) -> str:
    """The type key used for quotas: false premises split into no/correctable."""
    return f"false_premise:{answerable}" if qtype == "false_premise" else qtype


def subtype(c: Cand) -> str:
    return subtype_of(c.tpl.qtype, c.tpl.answerable)


def select(cands: list[Cand], split: str, used_facts: set, used_anchors: set,
           plan: Plan | None = None) -> tuple[list[Cand], dict]:
    """The seeded selection for one split.

    Order: scarcest (type, group) cell first (valid candidates per slot), then by
    ``sha256(seed/order, item ID)``. Accept a candidate if its type (and, in pass 1,
    its type-by-group cell) still has room, its relation key is under the cap, it
    shares no fact and no named-entity set with any selected item (either split:
    ``used_facts`` and ``used_anchors`` carry across calls), and its text isn't a
    near-duplicate of a selected item's. Pass 2 runs the same order again without
    the group cells, so a cell its group can't fill spills to others.

    Returns the chosen candidates (in selection order) and counts: items per type
    and per (type, group), the targets, what pass 1 alone selected, and why
    candidates were passed over.
    """
    plan = plan or Plan()
    type_tgt = subtype_targets(plan, split)
    fp_total = targets(plan)[split]["false_premise"]
    cell_tgt = {t: apportion(n, plan.group_share) for t, n in type_tgt.items()}
    joint_fp = not plan.fp_cap_per_kind
    rel_cap = {t: int(plan.relation_cap * (fp_total if joint_fp and t.startswith("false_premise") else n))
               for t, n in type_tgt.items()}
    have_type: Counter = Counter()
    have_cell: Counter = Counter()
    have_rel: Counter = Counter()
    have_type_rel: Counter = Counter()
    passed_over: dict[str, Counter] = defaultdict(Counter)
    toks: list[frozenset] = []
    # Scarcest cells pick first: a (type, group) cell with few candidates per slot
    # goes ahead of plentiful ones, so a shared cap (relation, facts) doesn't use
    # up what only it could fill. Ties and order within a cell follow the hash.
    pool: Counter = Counter((subtype(c), c.group) for c in cands)

    def scarcity(c: Cand) -> float:
        n = cell_tgt[subtype(c)].get(c.group, 0)
        return pool[(subtype(c), c.group)] / n if n else float("inf")

    order = sorted(cands, key=lambda c: (scarcity(c), qc.split_hash(c.iid, plan.split_seed + "/order")))
    taken: set[str] = set()
    chosen: list[Cand] = []
    pass1: dict = {}
    for group_quota, c in [(True, c) for c in order] + [(False, c) for c in order]:
        if not group_quota and not pass1:
            pass1 = {"selected": dict(have_type), "passed_over": {t: dict(v) for t, v in passed_over.items()}}
            passed_over = defaultdict(Counter)
        if c.iid in taken:
            continue
        t = subtype(c)
        why = None
        # The cap's unit: the type, or (per kind) the false-premise kind.
        rk = (c.tpl.qtype if joint_fp else t, c.tpl.relation_key)
        facts, anchors = c.facts(), frozenset(c.named)
        if have_type[t] >= type_tgt[t]:
            continue  # type full: not a shortfall reason
        tk = qc.tokens(c.text)
        if group_quota and have_cell[(t, c.group)] >= cell_tgt[t].get(c.group, 0):
            why = "group_cell_full"
        elif have_rel[rk] >= rel_cap[t]:
            why = "relation_cap"
        elif facts & used_facts:
            why = "fact_used"
        elif anchors in used_anchors:
            why = "anchors_used"
        elif any(qc.jaccard(tk, o) >= plan.text_dup_jaccard for o in toks):
            why = "text_near_duplicate"
        if why:
            passed_over[f"{t}|{c.group}"][why] += 1
            continue
        toks.append(tk)
        taken.add(c.iid)
        chosen.append(c)
        used_facts |= facts
        used_anchors.add(anchors)
        have_type[t] += 1
        have_cell[(t, c.group)] += 1
        have_rel[rk] += 1
        have_type_rel[f"{t}|{c.tpl.relation_key}"] += 1
    if not pass1:  # every candidate was taken or skipped in pass 1 with no pass 2 left
        pass1 = {"selected": dict(have_type), "passed_over": {t: dict(v) for t, v in passed_over.items()}}
    stats = {
        "target": type_tgt,
        "selected": {t: have_type[t] for t in type_tgt},
        "cell_target": {f"{t}|{g}": n for t in type_tgt for g, n in cell_tgt[t].items()},
        "cell_selected": {f"{t}|{g}": n for (t, g), n in sorted(have_cell.items())},
        "relation_selected": dict(sorted(have_type_rel.items())),
        "relation_cap": rel_cap,
        "pass1": pass1,
        "pass2_passed_over": {t: dict(v) for t, v in sorted(passed_over.items())},
    }
    return chosen, stats


def select_splits(valid: list[Cand], plan: Plan | None = None) -> tuple[dict[str, list[Cand]], dict[str, dict]]:
    """Split the valid pool by hash and select each split, held-out first.

    Returns the chosen candidates per split and the selection counts per split.
    """
    plan = plan or Plan()
    by_split: dict[str, list[Cand]] = {"train": [], "heldout": []}
    for c in valid:
        by_split[qc.split_of(c.iid, plan.split_seed, plan.heldout_share)].append(c)
    used_f: set = set()
    used_a: set = set()
    chosen: dict[str, list[Cand]] = {}
    stats: dict[str, dict] = {}
    for split in ("heldout", "train"):  # held-out first: never depends on train
        chosen[split], stats[split] = select(by_split[split], split, used_f, used_a, plan)
    return chosen, stats


def shortfalls(stats: dict[str, dict]) -> dict[str, dict[str, tuple[int, int]]]:
    """``{split: {type: (selected, target)}}`` for every type under its target."""
    out: dict[str, dict[str, tuple[int, int]]] = {}
    for split, s in stats.items():
        short = {t: (s["selected"][t], n) for t, n in s["target"].items() if s["selected"][t] < n}
        if short:
            out[split] = short
    return out
