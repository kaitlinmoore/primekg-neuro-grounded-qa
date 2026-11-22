"""Export the drawn traces as the grader may see them, and nothing else.

    uv run python scripts/grading/export.py --items $NGQ_DATA_DIR/items/<build_id> \\
        [--dir $NGQ_DATA_DIR/grading/round-first] [--store <store dir>]
    uv run python scripts/grading/export.py --check [--dir ...] [--expect 64]

Reads the key file written by ``draw.py``, finds each trace in its run's
``traces.jsonl``, and writes ``export.jsonl`` in grading order: one record per
grading_id, built field by field from an allowlist (section 2 of
``docs/labeling_guide.md``):

- ``grading_id`` and ``position`` (1-based grading order).
- ``step1``, shown while claims are graded:

  - ``question``: the item's question text.
  - ``tool_calls``: every tool call in a model-sampled assistant message, in order,
    with its name, its arguments as the model sent them (a string), and its
    result(s). An accepted result is the tool payload; a result the harness
    rejected (``contracts.trace.audit_retrieval``) carries ``status: rejected``, the
    reason and its detail, and the payload or raw text the model saw. A call that
    names an edge ID no earlier tool result showed lists it in ``unseen_edge_ids``.
  - ``unmatched_tool_results``: tool messages that answer no call of the model's
    (none are expected on the ``null`` harness; the guide treats them as not
    retrieved, Q21), with the reason.
  - ``final_message``: the last model-sampled assistant message, ``raw`` and, if it
    parses as an answer or an abstention, ``layout`` (claims numbered from 0).
  - ``earlier_answer``: only when the final message has no text, the last earlier
    sampled assistant message with text, labelled as earlier (guide Q14); else null.

  Every directed edge in a tool payload gains ``direction``: the store's subject and
  object (``stored_order``), the label the tools served in this trace
  (``served_label``, read from the trace's own tool result), PrimeKG's own label
  (``primekg_label``, from the store), and ``grouped_end`` (either end is a PrimeKG
  grouped disease, ``group:``).

- ``step2``, revealed after claims are graded: ``answerable``, the gold points (with
  ``directed``, evidence IDs and display names from the store), the premise (or
  null) and ``graph_gaps``.

Never exported: model, provider, endpoint, reward, components, metrics,
``trace.info``, the parse result or its reason, ``qtype``, ``template_id``,
``source``, ``notes``, the item ID, token counts, timings, the system prompt, the
model's text between tool calls and any reasoning content. :func:`forbidden_keys`
finds any of those key names anywhere in a record, and ``--check`` runs it.

``--check`` re-reads everything by code and prints pass/fail only: the record count,
the grading order against the key file, forbidden keys, every step-2 block, and the
served label on every directed edge (against the trace's raw tool messages, and
consistent within each run: either all ``tools.kg.direction_label`` or all
PrimeKG's). It never prints a record.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

import verifiers.v1 as vf
from grading_common import (
    EXPORT_FILE,
    KEY_FILE,
    REPO,
    GradingError,
    atomic_write_text,
    default_round_dir,
    jsonl_text,
    read_jsonl,
    refuse_heldout,
    sha256_file,
)
from neuro_grounded_qa.contracts import (
    Abstain,
    Answer,
    EvidenceStore,
    QuestionItem,
    ToolResultPayload,
    audit_retrieval,
    parse_output,
)
from neuro_grounded_qa.reward import final_reply
from neuro_grounded_qa.tools.kg import GROUP_ID_PREFIX, direction_label
from neuro_grounded_qa.tools.store import load_store
from pydantic import ValidationError
from qgen.safety import ItemsError
from qgen.view import load_train, train_path
from verifiers.v1.types import content_text

EDGE_ID_IN_TEXT = re.compile(r"E:\d{6,}")
"""An edge ID inside a tool-call argument."""

TOP_KEYS = ("grading_id", "position", "step1", "step2")
STEP1_KEYS = ("question", "tool_calls", "unmatched_tool_results", "final_message",
              "earlier_answer")
STEP2_KEYS = ("answerable", "gold_points", "premise", "graph_gaps")

FORBIDDEN_KEYS = frozenset({
    # who and where
    "model", "provider", "endpoint", "base_url", "client", "agent", "run", "env",
    # scores
    "reward", "rewards", "grounding_reward", "score", "components", "metrics",
    "format_ok", "abstention_correct", "coverage", "claim_support", "citation_validity",
    "unsupported_penalty", "precision_penalty", "flagged", "premise_asserted",
    # trace.info and provenance
    "info", "reward_params", "store_build_id", "items_sha256", "build_id",
    # parse result
    "parse", "parsed", "parse_result", "parse_reason", "parse_failure", "outcome",
    # item fields kept hidden
    "qtype", "template_id", "template", "notes", "split", "item_id", "idx",
    "system_prompt", "evidence_scope", "gold_evidence",
    # usage and timing
    "usage", "tokens", "prompt_tokens", "completion_tokens", "num_input_tokens",
    "num_output_tokens", "num_total_tokens", "timing", "timings", "timestamp", "time",
    "calls", "sampling", "finish_reason", "logprobs", "token_ids",
    # hidden message content
    "reasoning_content", "provider_state",
})
"""Key names that must not appear anywhere in an export record. Tool-call arguments
are kept as strings, so a model can't put one of these keys in the export."""


# --- Building one record -----------------------------------------------------------------


def _entity_name(store: EvidenceStore, entity_id: str) -> str | None:
    ent = store.entity(entity_id)
    return ent.name if ent is not None else None


def annotate_payload(payload: ToolResultPayload, store: EvidenceStore) -> dict[str, Any]:
    """A tool payload as the grader sees it: the evidence, entities, ``truncated`` and
    ``note`` the model saw, with ``direction`` added to every directed edge."""
    evidence = []
    for ev in payload.evidence:
        d = ev.model_dump(mode="json")
        if ev.directed:
            stored = store.get(ev.id)
            d["direction"] = {
                "stored_order": (
                    {"subject_id": stored.subject.id, "object_id": stored.object.id}
                    if stored is not None else None
                ),
                "served_label": ev.display_relation,
                "primekg_label": stored.display_relation if stored is not None else None,
                "grouped_end": ev.subject.id.startswith(GROUP_ID_PREFIX)
                or ev.object.id.startswith(GROUP_ID_PREFIX),
            }
        evidence.append(d)
    return {
        "evidence": evidence,
        "entities": [e.model_dump(mode="json") for e in payload.entities],
        "truncated": payload.truncated,
        "note": payload.note,
    }


def _try_payload(text: str) -> ToolResultPayload | None:
    try:
        return ToolResultPayload.loads(text)
    except ValidationError:
        return None


def layout(text: str) -> dict[str, Any] | None:
    """The message laid out by claim if it parses as an answer or an abstention, else
    None. Only the model's own fields are kept; the parse outcome isn't recorded."""
    parsed = parse_output(text)
    if isinstance(parsed, Answer):
        return {
            "claims": [
                {"claim_index": i, **c.model_dump(mode="json")}
                for i, c in enumerate(parsed.claims)
            ],
            "answer_text": parsed.answer_text,
            "unsupported_parts": list(parsed.unsupported_parts),
        }
    if isinstance(parsed, Abstain):
        return {"abstain": True, "reason": parsed.reason}
    return None


def _ancestors(trace: vf.Trace, node: int) -> list[int]:
    """Nodes above ``node`` on its parent chain, nearest first (cycle-safe)."""
    chain: list[int] = []
    seen: set[int] = set()
    current = trace.nodes[node].parent
    while current is not None and current not in seen and 0 <= current < len(trace.nodes):
        chain.append(current)
        seen.add(current)
        current = trace.nodes[current].parent
    return chain


def _edge_ids_in(text: str) -> list[str]:
    out: list[str] = []
    for m in EDGE_ID_IN_TEXT.findall(text or ""):
        if m not in out:
            out.append(m)
    return out


def step1_block(trace: vf.Trace, item: QuestionItem, store: EvidenceStore) -> dict[str, Any]:
    """The step-1 block for one trace (see the module docstring)."""
    audit = audit_retrieval(trace)
    accepted = {a.node: a for a in audit.accepted}
    rejected = {r.node: r for r in audit.rejected}
    issuers: dict[str, list[int]] = defaultdict(list)
    calls: list[dict[str, Any]] = []
    call_at: dict[tuple[int, str], dict[str, Any]] = {}
    unmatched: list[dict[str, Any]] = []
    shown: set[str] = set()

    for i, node in enumerate(trace.nodes):
        msg = node.message
        if isinstance(msg, vf.AssistantMessage) and node.sampled:
            for call in msg.tool_calls or []:
                entry = {
                    "call": len(calls) + 1,
                    "name": call.name,
                    "arguments": call.arguments,
                    "unseen_edge_ids": [e for e in _edge_ids_in(call.arguments) if e not in shown],
                    "results": [],
                }
                calls.append(entry)
                issuers[call.id].append(i)
                call_at[(i, call.id)] = entry
        elif isinstance(msg, vf.ToolMessage):
            text = content_text(msg.content)
            payload = _try_payload(text)
            if payload is not None:
                shown.update(ev.id for ev in payload.evidence)
            if i in accepted:
                a = accepted[i]
                call_at[(a.issuer, a.tool_call_id)]["results"].append(
                    {"status": "accepted", "payload": annotate_payload(payload, store)}
                )
                continue
            r = rejected[i]
            result = {
                "status": "rejected",
                "reason": r.reason,
                "detail": r.detail,
                "payload": annotate_payload(payload, store) if payload is not None else None,
                "raw": None if payload is not None else text,
            }
            issuer = next((n for n in _ancestors(trace, i) if n in issuers.get(r.tool_call_id, ())), None)
            if r.reason in ("duplicate_result", "unparseable") and issuer is not None:
                call_at[(issuer, r.tool_call_id)]["results"].append(result)
            else:
                unmatched.append({"tool_name": r.tool_name, **result})

    raw_final = final_reply(trace)
    earlier = None
    if not raw_final:
        for msg in reversed(trace.assistant_messages[:-1]):
            text = content_text(msg.content).strip() if msg.content else ""
            if text:
                earlier = {"raw": text, "layout": layout(text)}
                break
    return {
        "question": item.prompt_text,
        "tool_calls": calls,
        "unmatched_tool_results": unmatched,
        "final_message": {"raw": raw_final, "layout": layout(raw_final) if raw_final else None},
        "earlier_answer": earlier,
    }


def step2_block(item: QuestionItem, store: EvidenceStore) -> dict[str, Any]:
    """The step-2 block: gold points, answerability, graph gaps and the premise."""

    def triple(t: Any) -> dict[str, Any]:
        return {
            "subject_id": t.subject_id,
            "subject_name": _entity_name(store, t.subject_id),
            "relation": t.relation,
            "object_id": t.object_id,
            "object_name": _entity_name(store, t.object_id),
            "directed": t.directed,
        }

    return {
        "answerable": item.answerable,
        "gold_points": [
            {"id": gp.id, **triple(gp), "evidence": list(gp.evidence)} for gp in item.gold_points
        ],
        "premise": triple(item.premise) if item.premise is not None else None,
        "graph_gaps": [
            {"description": g.description,
             "triple": triple(g.triple) if g.triple is not None else None}
            for g in item.graph_gaps
        ],
    }


def build_record(saved: Mapping[str, Any], item: QuestionItem, store: EvidenceStore,
                 grading_id: str, position: int) -> dict[str, Any]:
    """One export record. ``item`` comes from the train file, not from the trace."""
    if saved["task"]["data"]["id"] != item.id:
        raise GradingError(f"{grading_id}: the trace is on a different item than the key says")
    trace = vf.Trace.model_validate(saved)
    return {
        "grading_id": grading_id,
        "position": position,
        "step1": step1_block(trace, item, store),
        "step2": step2_block(item, store),
    }


# --- Whole export ----------------------------------------------------------------------


def _walk_keys(obj: Any, path: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield f"{path}.{k}" if path else k, k
            yield from _walk_keys(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for n, v in enumerate(obj):
            yield from _walk_keys(v, f"{path}[{n}]")


def forbidden_keys(record: Mapping[str, Any]) -> list[str]:
    """Paths of forbidden keys anywhere in ``record``, plus any top-level, step-1 or
    step-2 key outside the allowlist. Empty for a clean record."""
    bad = [p for p, k in _walk_keys(record) if k in FORBIDDEN_KEYS]
    for keys, block, where in (
        (TOP_KEYS, record, "record"),
        (STEP1_KEYS, record.get("step1") or {}, "step1"),
        (STEP2_KEYS, record.get("step2") or {}, "step2"),
    ):
        bad += [f"{where}: unexpected key {k!r}" for k in block if k not in keys]
    return bad


def resolve_run(run: str) -> Path:
    """A key file's ``run`` as a path (relative ones are under the repo)."""
    p = Path(run)
    return p if p.is_absolute() else REPO / p


def load_saved(run: str, line: int, index: int, trace_id: str) -> dict[str, Any]:
    """The saved trace a key line points at; checks its trace ID."""
    path = resolve_run(run) / "traces.jsonl"
    with path.open(encoding="utf-8") as f:
        for n, text in enumerate(f, start=1):
            if n == line:
                saved = json.loads(text)["traces"][index]
                if str(saved["id"]) != trace_id:
                    raise GradingError(f"{run}:{line}: trace ID differs from the key file")
                return saved
    raise GradingError(f"{run}: line {line} not found")


def store_path_for(run: str) -> Path:
    """The store a run used, from its resolved config."""
    cfg = json.loads((resolve_run(run) / "configs" / "resolved" / "run.json").read_text("utf-8"))
    raw = cfg["env"]["taskset"]["tools"]["store_path"]
    p = Path(raw).expanduser()
    return p if p.is_absolute() else REPO / p


class Stores:
    """Stores by path, each loaded once."""

    def __init__(self, override: Path | None = None):
        self.override = override
        self._by_path: dict[Path, EvidenceStore] = {}

    def for_run(self, run: str) -> EvidenceStore:
        path = self.override or store_path_for(run)
        if path not in self._by_path:
            self._by_path[path] = load_store(path)
        return self._by_path[path]


def load_items(items_path: Path) -> dict[str, QuestionItem]:
    """Train items by ID, through the train-only loader."""
    refuse_heldout(items_path)
    try:
        return {it.id: it for it in load_train(train_path(items_path))}
    except ItemsError as e:
        raise GradingError(f"train items: {e}") from None


def run_export(round_dir: Path, items_path: Path, store: Path | None = None) -> Path:
    """Build and write ``export.jsonl`` from the key file. Returns its path."""
    key = read_jsonl(round_dir / KEY_FILE)
    items = load_items(items_path)
    stores = Stores(store)
    records = []
    for k in sorted(key, key=lambda k: k["position"]):
        item = items.get(k["item_id"])
        if item is None:
            raise GradingError(f"{k['grading_id']}: item not in the train file")
        st = stores.for_run(k["run"])
        if st.build_id != item.build_id:
            raise GradingError(f"{k['grading_id']}: store build differs from the item's")
        saved = load_saved(k["run"], k["trace_line"], k["trace_index"], k["trace_id"])
        records.append(build_record(saved, item, st, k["grading_id"], k["position"]))
    for r in records:
        if bad := forbidden_keys(r):
            raise GradingError(f"{r['grading_id']}: forbidden keys in export: {bad}")
    out = round_dir / EXPORT_FILE
    atomic_write_text(out, jsonl_text(records))
    return out


# --- Checks (code only; nothing is printed from a record) ------------------------------


def directed_edges(record: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    """Every directed edge in a record's tool payloads."""
    step1 = record["step1"]
    results = [r for c in step1["tool_calls"] for r in c["results"]]
    results += list(step1["unmatched_tool_results"])
    for r in results:
        for ev in (r.get("payload") or {}).get("evidence") or []:
            if ev.get("directed"):
                yield ev


def _raw_served_labels(saved: Mapping[str, Any]) -> set[tuple[str, str | None]]:
    """(edge ID, display_relation) of every directed edge in the trace's raw tool
    messages, read with plain JSON, independently of the export code."""
    out: set[tuple[str, str | None]] = set()
    for node in saved["nodes"]:
        msg = node.get("message") or {}
        if msg.get("role") != "tool":
            continue
        content = msg.get("content")
        if not isinstance(content, str):
            content = "".join(p.get("text", "") for p in content or [])
        try:
            payload = json.loads(content)
        except ValueError:
            continue
        for ev in (payload.get("evidence") or []) if isinstance(payload, dict) else []:
            if isinstance(ev, dict) and ev.get("directed"):
                out.add((ev.get("id"), ev.get("display_relation")))
    return out


def check_export(round_dir: Path, expect: int | None = None,
                 store: Path | None = None) -> list[tuple[str, bool, str]]:
    """Code checks on a written export. Returns (check, passed, safe detail) rows; the
    details hold counts and run names only."""
    key = sorted(read_jsonl(round_dir / KEY_FILE), key=lambda k: k["position"])
    export = read_jsonl(round_dir / EXPORT_FILE)
    rows: list[tuple[str, bool, str]] = []
    n = len(export)
    rows.append(("record count", n == len(key) and (expect is None or n == expect),
                 f"{n} records, key {len(key)}" + (f", expected {expect}" if expect else "")))
    rows.append(("grading order matches the key file",
                 [r.get("grading_id") for r in export] == [k["grading_id"] for k in key]
                 and [r.get("position") for r in export] == list(range(1, n + 1)), ""))
    bad = sum(1 for r in export if forbidden_keys(r))
    rows.append(("no forbidden key", bad == 0, f"{bad} record(s) with one"))
    step2_ok = sum(
        1 for r in export
        if isinstance(r.get("step2"), dict) and all(k in r["step2"] for k in STEP2_KEYS)
        and r["step2"]["answerable"] in ("yes", "partial", "correctable", "no")
    )
    rows.append(("every step-2 block present", step2_ok == n, f"{step2_ok} of {n}"))

    stores = Stores(store)
    by_id = {k["grading_id"]: k for k in key}
    mismatched = labels_off = 0
    eras: dict[str, set[str]] = defaultdict(set)
    n_directed = 0
    for r in export:
        k = by_id.get(r["grading_id"])
        if k is None:
            mismatched += 1
            continue
        saved = load_saved(k["run"], k["trace_line"], k["trace_index"], k["trace_id"])
        raw = _raw_served_labels(saved)
        st = stores.for_run(k["run"])
        for ev in directed_edges(r):
            n_directed += 1
            d = ev["direction"]
            if (ev["id"], d["served_label"]) not in raw or ev["display_relation"] != d["served_label"]:
                mismatched += 1
            stored = st.get(ev["id"])
            grouped = ev["subject"]["id"].startswith(GROUP_ID_PREFIX) or ev["object"]["id"].startswith(GROUP_ID_PREFIX)
            if stored is None or d["primekg_label"] != stored.display_relation or d["grouped_end"] != grouped:
                labels_off += 1
                continue
            if d["served_label"] == direction_label(stored):
                eras[k["run"]].add("direction labels")
            elif d["served_label"] == stored.display_relation:
                eras[k["run"]].add("PrimeKG labels")
            else:
                eras[k["run"]].add("other")
    rows.append(("served label on every directed edge matches the trace",
                 mismatched == 0, f"{n_directed} directed edges, {mismatched} mismatched"))
    rows.append(("PrimeKG label and grouped flag match the store", labels_off == 0,
                 f"{labels_off} off"))
    for run in sorted({k["run"] for k in key}):
        e = eras.get(run, set())
        rows.append((f"served labels consistent within {run}",
                     len(e) <= 1 and "other" not in e, ", ".join(sorted(e)) or "no directed edges"))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--items", type=Path, help="item-set directory or its train.jsonl")
    ap.add_argument("--dir", type=Path, default=None,
                    help="round directory (default: $NGQ_DATA_DIR/grading/round-first)")
    ap.add_argument("--store", type=Path, default=None,
                    help="evidence store (default: each run config's store_path)")
    ap.add_argument("--check", action="store_true",
                    help="check a written export by code; print pass/fail only")
    ap.add_argument("--expect", type=int, default=None, help="expected record count")
    args = ap.parse_args(argv)
    try:
        round_dir = args.dir or default_round_dir()
        if args.check:
            rows = check_export(round_dir, args.expect, args.store)
            for name, ok, detail in rows:
                print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" ({detail})" if detail else ""))
            ok = all(ok for _, ok, _ in rows)
            print(f"export check: {'PASS' if ok else 'FAIL'}")
            print(f"{EXPORT_FILE}: sha256 {sha256_file(round_dir / EXPORT_FILE)}")
            return 0 if ok else 1
        if args.items is None:
            ap.error("--items is required unless --check")
        out = run_export(round_dir, args.items, args.store)
    except GradingError as e:
        print(f"export: FAIL: {e}")
        return 1
    print("export: PASS")
    print(f"{EXPORT_FILE}: sha256 {sha256_file(out)}")
    return 0


def iter_records(path: Path) -> Iterable[dict[str, Any]]:
    """Export records in grading order (for the viewer)."""
    return sorted(read_jsonl(path), key=lambda r: r["position"])


if __name__ == "__main__":
    sys.exit(main())

