"""Re-check a written item set from its files: counts and pass/fail only.

``question_set.py check`` (and ``check-heldout``, its held-out-only form) reads the
item files back and checks every item again, against the store and the full graph,
without trusting the generator's in-memory state:

- **file**: the SHA-256 matches the manifest; the counts per type, group and
  relation key match the manifest; item IDs are unique;
- **per item**: it parses and validates as a ``QuestionItem``; its ``split`` and
  ``build_id`` match the file and the store; its template exists and agrees on type
  and answerability; its ID and split are the hash functions of its template and
  parameters; gold points are stated by store edges with matching ``directed``
  flags; premise and gap triples carry their relation's flag; the recorded route is
  within budget, searches exactly the named entities, starts probes only where
  allowed, and meets every recorded requirement when its calls are made again with
  the real tool bodies; the recorded full-graph conditions hold; the premise is
  absent from the full graph (as a triple, and as a pair for ``no`` items) and gap
  triples are absent as pairs; every named entity passes the name rule with the
  recorded mention form and its name is in the question; no answer entity's name is
  in the question; no entity is in an excluded HPO subtree; the gold points are
  exactly the store facts that answer the question under its template's answer rule
  (``qgen.answers``; ``gold_complete``);
- **across splits** (when both are checked): no shared item ID, fact or
  named-entity set.

**Output is counts only**: per check, how many items passed and failed. A failing
item is never identified, not even by its opaque ID, so the held-out check prints
nothing about any one item.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import qgen_templates as qt
from neuro_grounded_qa.contracts import QuestionItem
from neuro_grounded_qa.data import store_files as sf
from pydantic import ValidationError

from qgen import checks as qc
from qgen.answers import Adjacency, gold_completeness
from qgen.candidates import GROUP_PREFIX
from qgen.files import SPLIT_FILES, item_counts, template_relation_keys

ITEM_CHECKS = (
    "json", "schema", "split_field", "build_id", "template", "notes", "item_id", "split_hash",
    "gold_points", "triple_flags", "route_budget", "route_meets", "full_graph", "premise_gap_absent",
    "names", "no_leak", "domain", "gold_complete",
)


def _entities(item: QuestionItem, named: list[str]) -> set[str]:
    ids = set(named)
    for gp in item.gold_points:
        ids |= {gp.subject_id, gp.object_id}
    if item.premise:
        ids |= {item.premise.subject_id, item.premise.object_id}
    for gap in item.graph_gaps:
        if gap.triple:
            ids |= {gap.triple.subject_id, gap.triple.object_id}
    return ids


def item_facts(item: QuestionItem) -> frozenset:
    """Fact keys of an item's gold points, premise and gap triples."""
    ts = [*item.gold_points, *([item.premise] if item.premise else []),
          *(g.triple for g in item.graph_gaps if g.triple)]
    return frozenset(qc.fact_key(t.subject_id, t.relation, t.object_id, t.directed) for t in ts)


class Verifier:
    """Checks item files against one store and the full graph.

    ``adjacency`` indexes the store's edges for the gold-completeness check."""

    def __init__(self, store, prober: qc.Prober, fg: qc.FullGraph, manifest: dict,
                 directed_by_rel: dict[str, bool], adjacency: Adjacency) -> None:
        self.store = store
        self.adjacency = adjacency
        self.prober = prober
        self.fg = fg
        self.manifest = manifest
        self.directed_by_rel = directed_by_rel
        self.templates = template_relation_keys()
        filters = manifest.get("filters", {})
        roots = filters.get("hpo_excluded_roots", [])
        self.excluded = fg.subtree_entities("phenotype_phenotype", "HPO", roots) if roots else set()
        self.skip_grouped = bool(filters.get("reversed_premise_skip_grouped", False))
        self.naming = filters.get("naming", "strict")
        self.lowercase = bool(filters.get("lowercase_phenotypes", False))
        self.split_seed = manifest["split"]["seed"]
        self.heldout_share = manifest["split"]["heldout_share"]
        self.max_calls = manifest["tool_config"]["max_calls"]
        self._mentions: dict[str, qc.Mention] = {}

    def _mention(self, eid: str) -> qc.Mention | None:
        if eid not in self._mentions:
            ent = self.store.entity(eid)
            if ent is None:
                return None
            self._mentions[eid] = qc.resolve_mention(self.prober.search, ent, self.naming)
        return self._mentions[eid]

    def check_split(self, d: Path, split: str) -> tuple[dict, list[QuestionItem]]:
        """Check one split's file. Returns (report, valid items); the report is counts."""
        path = d / SPLIT_FILES[split]
        fails: Counter = Counter()
        passes: Counter = Counter()
        schema_errors: Counter = Counter()
        items: list[QuestionItem] = []
        notes_of: list[dict] = []

        def tally(name: str, ok: bool) -> bool:
            (passes if ok else fails)[name] += 1
            return ok

        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        for line in lines:
            try:
                record = json.loads(line)
            except ValueError:
                tally("json", False)
                continue
            tally("json", True)
            try:
                item = QuestionItem.model_validate(record)
            except ValidationError as e:
                tally("schema", False)
                for err in e.errors(include_input=False, include_url=False, include_context=False):
                    schema_errors[str(err.get("type", "?"))] += 1
                continue
            tally("schema", True)
            tally("split_field", item.split == split)
            tally("build_id", item.build_id == self.store.build_id == self.manifest["build_id"])
            tpl = self.templates.get(item.template_id or "")
            tally("template", tpl is not None and tpl.qtype == item.qtype
                  and tpl.answerable == item.answerable and item.source == "template")
            try:
                notes = json.loads(item.notes)
                ok = isinstance(notes, dict) and tpl is not None and notes["relation_key"] == tpl.relation_key \
                    and isinstance(notes["template_params"], list) and "group" in notes
            except (ValueError, KeyError, TypeError):
                notes, ok = None, False
            if not tally("notes", ok):
                continue
            items.append(item)
            notes_of.append(notes)
            tally("item_id", item.id == qc.item_id(item.build_id, item.template_id, notes["template_params"]))
            tally("split_hash", qc.split_of(item.id, self.split_seed, self.heldout_share) == split)
            tally("gold_points", not any(qc.gold_point_problems(gp, self.store) for gp in item.gold_points))
            trips = ([item.premise] if item.premise else []) + [g.triple for g in item.graph_gaps if g.triple]
            tally("triple_flags", not any(qc.triple_flag_problems(t, self.directed_by_rel) for t in trips))
            try:
                route = qc.Route.from_notes(notes["route"])
                reqs = [qc.requirement_from_json(r) for r in notes["requirements"]]
                allowed = notes["route_allowed"]
                named = notes["named"]
                budget_ok = route.calls <= self.max_calls and set(route.searches) == set(named) \
                    and len(route.searches) == len(set(named))
                meets = qc.route_meets(self.prober, route, reqs) and (
                    allowed is None or all(p[0] in allowed for p in route.probes))
                # independent of the recorded requirements: every gold edge is on
                # the page of some call the route makes
                pages = [self.prober.page(e, r) for e, r in route.probes]
                meets = meets and all(any(eid in p.ids for p in pages) for eid in item.gold_evidence)
            except (KeyError, TypeError, ValueError):
                budget_ok = meets = False
                named = []
            tally("route_budget", budget_ok)
            tally("route_meets", meets)
            # Every named entity passes the name rule in the set's mode, its recorded
            # mention form matches the rule, and its displayed name is in the text.
            ok = notes.get("naming", self.naming) == self.naming
            typed_rec = set(notes.get("typed_mentions", []))
            for n in named:
                mn = self._mention(n)
                ent = self.store.entity(n)
                shown = qt.display_name(ent.name, ent.type, self.lowercase) if ent is not None else ""
                if mn is None or not mn.ok or mn.typed != (n in typed_rec) \
                        or shown.casefold() not in item.prompt_text.casefold():
                    ok = False
            tally("names", ok)
            answers = {x for gp in item.gold_points for x in (gp.subject_id, gp.object_id)} - set(named)
            names = [self.store.entity(a).name for a in answers if self.store.entity(a) is not None]
            tally("no_leak", not qc.answer_leaks(item.prompt_text, names))
            ents = _entities(item, named)
            dom_ok = not (ents & self.excluded)
            if self.skip_grouped and (item.template_id or "").startswith("fp_reversed.") and item.premise:
                dom_ok = dom_ok and not any(x.startswith(GROUP_PREFIX)
                                            for x in (item.premise.subject_id, item.premise.object_id))
            tally("domain", dom_ok)
            tally("gold_complete", gold_completeness(item, notes["template_params"], self.adjacency).ok)

        # full-graph conditions, batched over the file
        fg_ok = self._full_graph(items, notes_of)
        for item, (cond_ok, absent_ok) in zip(items, fg_ok):
            tally("full_graph", cond_ok)
            tally("premise_gap_absent", absent_ok)

        sha = sf.file_sha256(path)
        man_file = self.manifest["files"].get(SPLIT_FILES[split], {})
        valid_items = items
        file_checks = {
            "sha256_matches_manifest": sha == man_file.get("sha256"),
            "count_matches_manifest": len(lines) == man_file.get("items"),
            "counts_match_manifest": item_counts(valid_items) == self.manifest["counts"].get(split),
            "unique_ids": len({i.id for i in valid_items}) == len(valid_items),
        }
        report = {
            "file": SPLIT_FILES[split],
            "sha256": sha,
            "items": len(lines),
            "file_checks": file_checks,
            "item_checks": {k: {"pass": passes[k], "fail": fails[k]} for k in ITEM_CHECKS},
            "schema_error_types": dict(sorted(schema_errors.items())),
        }
        report["ok"] = all(file_checks.values()) and not sum(fails.values())
        return report, valid_items

    def _full_graph(self, items: list[QuestionItem], notes_of: list[dict]) -> list[tuple[bool, bool]]:
        pairs: set[tuple[str, str]] = set()
        trips: set[tuple[str, str, str, bool]] = set()
        zero: dict[str, set[str]] = {}
        chains: dict[tuple[str, str], set[tuple[str, str]]] = {}
        parsed = []
        for item, notes in zip(items, notes_of):
            fgc = notes.get("full_graph", {})
            ap = [tuple(p) for p in fgc.get("absent_pairs", [])]
            at = [tuple(t) for t in fgc.get("absent_triples", [])]
            zd = [tuple(z) for z in fgc.get("zero_degree", [])]
            nc = [tuple(c) for c in fgc.get("no_chain", [])]
            # independent of the recorded conditions: premise and gap triples
            prem_t = []
            prem_p = []
            if item.premise:
                p = item.premise
                prem_t.append((p.subject_id, p.relation, p.object_id, p.directed))
                if item.answerable == "no":
                    prem_p.append((p.subject_id, p.object_id))
            gap_p = [(g.triple.subject_id, g.triple.object_id) for g in item.graph_gaps if g.triple]
            pairs |= set(ap) | set(prem_p) | set(gap_p)
            trips |= set(at) | set(prem_t)
            for e, r in zd:
                zero.setdefault(r, set()).add(e)
            for x, y, rx, ry in nc:
                chains.setdefault((rx, ry), set()).add((x, y))
            parsed.append((ap, at, zd, nc, prem_t, prem_p + gap_p))
        try:
            absent = self.fg.absent_pairs(pairs)
            present = self.fg.present_triples(trips)
            degree = {r: self.fg.relation_degree(es, r) for r, es in zero.items()}
            chain_n = {k: self.fg.shared_neighbors(v, *k) for k, v in chains.items()}
        except (KeyError, ValueError):
            return [(False, False)] * len(items)
        out = []
        for ap, at, zd, nc, prem_t, abs_p in parsed:
            cond = all(p in absent for p in ap) and not any(t in present for t in at) \
                and all(degree[r][e] == 0 for e, r in zd) \
                and all(chain_n[(rx, ry)][(x, y)] == 0 for x, y, rx, ry in nc)
            absent_ok = not any(t in present for t in prem_t) and all(p in absent for p in abs_p)
            out.append((cond, absent_ok))
        return out


def cross_split(train: list[QuestionItem], heldout: list[QuestionItem]) -> dict[str, bool]:
    """Disjointness across splits: item IDs, facts, and named-entity sets."""
    def named(i: QuestionItem) -> frozenset:
        return frozenset(json.loads(i.notes).get("named", []))
    tf = set().union(*(item_facts(i) for i in train)) if train else set()
    hf = set().union(*(item_facts(i) for i in heldout)) if heldout else set()
    return {
        "ids_disjoint": not ({i.id for i in train} & {i.id for i in heldout}),
        "facts_disjoint": not (tf & hf),
        "named_sets_disjoint": not ({named(i) for i in train} & {named(i) for i in heldout}),
    }


def verify(d: Path, splits: list[str], verifier: Verifier) -> dict:
    """Check the named splits of the set in ``d``; return the counts-only report."""
    reports: dict = {}
    valid: dict[str, list[QuestionItem]] = {}
    for split in splits:
        reports[split], valid[split] = verifier.check_split(d, split)
    out: dict = {"build_id": verifier.manifest["build_id"], "splits": reports}
    ok = all(r["ok"] for r in reports.values())
    if set(splits) == {"train", "heldout"}:
        out["cross_split"] = cross_split(valid["train"], valid["heldout"])
        ok = ok and all(out["cross_split"].values())
    out["ok"] = ok
    return out
