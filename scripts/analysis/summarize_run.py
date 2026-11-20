"""Summarize a model run of neuro-grounded-qa from its ``traces.jsonl``.

    uv run python scripts/analysis/summarize_run.py outputs/smoke-<model>-<date>/<run-dir> [more run dirs]

For each run directory (the one ``vf-run`` writes, holding ``traces.jsonl`` and
``configs/resolved/run.json``) it writes ``summary.md`` and ``summary.json`` next to
``traces.jsonl``, and prints the markdown. With several runs it also prints a
side-by-side table, one column per run.

What it reports, overall, by question type (``qtype``), by template family (the
``template_id`` prefix before the dot, such as ``mh_bridge``) and by template:

- **Reward** mean and the components the reward records (``citation_validity``,
  ``claim_support``, ``coverage``, ``unsupported_penalty``, ``abstention_correct``,
  ``flagged``, ``premise_asserted``), each averaged over the traces that define it.
- **Parse failures**: the rate and the count per reason.
- **Abstention**: the abstention rate by answerability class, and how often it was
  correct.
- **Strict retrieval**: ``audit_retrieval`` rejections per reason and per tool name,
  recomputed offline from the saved trace and compared with the recorded metrics;
  ``duplicate_result`` is one of the reasons.
- **Tool use**: calls per rollout, calls per tool, budget hits (more sampled calls
  than ``max_calls``, and results carrying the budget note), how many
  ``kg_get_neighbors`` calls passed a relation filter, and how many got an
  "Unknown relation" note.
- **Final answers**: ``root_reply`` presence and mismatch; claims, voided claims,
  duplicate claims and stripped cites.
- **Group-ID misuse**: claims whose subject or object ID is a *member* ID of a
  grouped node (``group:...``) in the store, rather than the group node itself.
  Members come from the store's ``entity_sources.jsonl``.
- **Offline rescore**: the saved trace rescored with the recorded
  ``reward_params`` must give the recorded reward.
- **Provenance**: ``store_build_id``, ``items_sha256`` and ``reward_params`` present
  in every trace's ``info``.
- **Tokens and calls**: model calls per rollout, input tokens as billed (the sum of
  each call's ``prompt_tokens``; verifiers' ``num_input_tokens`` counts new tokens
  only), output tokens, and agent time.

It prints item IDs and template IDs, never question text. It reads only what the
run wrote; it never reads an items file, so it can't touch held-out items.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, get_args

import verifiers.v1 as vf
from neuro_grounded_qa.contracts import (
    Answer,
    QuestionItem,
    RejectionReason,
    audit_retrieval,
    parse_output,
)
from neuro_grounded_qa.contracts.output import ParseFailureReason
from neuro_grounded_qa.reward import RewardParams, evaluate, final_reply

REWARD_NAME = "grounding_reward"
COMPONENTS = (
    "citation_validity", "claim_support", "coverage", "unsupported_penalty",
    "abstention_correct", "flagged", "premise_asserted",
)
PROVENANCE_KEYS = ("store_build_id", "items_sha256", "reward_params")
BUDGET_NOTE = "Tool call budget used up"
UNKNOWN_RELATION_NOTE = "Unknown relation"
NEIGHBORS_TOOL = "kg_get_neighbors"
DEFAULT_MAX_CALLS = 8


# --- Reading ---------------------------------------------------------------------------


def load_traces(run_dir: Path) -> list[dict[str, Any]]:
    """Every trace in ``run_dir/traces.jsonl``, as saved (one per rollout)."""
    out: list[dict[str, Any]] = []
    for line in (run_dir / "traces.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.extend(json.loads(line)["traces"])
    return out


def load_config(run_dir: Path) -> dict[str, Any]:
    """The run's resolved config, or ``{}`` if it isn't there."""
    path = run_dir / "configs" / "resolved" / "run.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def group_members(entity_sources: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Member ID → group node ID, from the store's ``entity_sources.jsonl`` records.

    Grouped nodes carry ``members``: the source IDs (MONDO diseases) merged into the
    one ``group:`` node the graph uses.
    """
    out: dict[str, str] = {}
    for rec in entity_sources:
        for member in rec.get("members") or []:
            out[member["id"]] = rec["id"]
    return out


def load_group_members(store_path: Path | None) -> dict[str, str] | None:
    """Group members of a store directory, ``{}`` for a store file with no groups (the
    JSON fixture store), or None if the store can't be found."""
    if store_path is None:
        return None
    if store_path.is_file():
        return {}
    sources = store_path / "entity_sources.jsonl"
    if not sources.exists():
        return None
    with sources.open(encoding="utf-8") as f:
        return group_members(json.loads(line) for line in f if line.strip())


# --- One trace ----------------------------------------------------------------------------


@dataclass
class TraceRow:
    """What the summary needs from one rollout."""

    item_id: str
    qtype: str
    answerable: str
    template_id: str
    family: str
    reward: float
    metrics: dict[str, float]
    outcome: str  # answer | abstain | parse_failure
    parse_reason: str | None
    tool_calls: int
    calls_by_tool: dict[str, int]
    neighbors_calls: int
    neighbors_with_relation: int
    unknown_relation_notes: int
    budget_notes: int
    over_budget: bool
    rejections: dict[str, int]  # reason -> n, recomputed offline
    rejections_by_tool: dict[str, int]  # "reason/tool" -> n
    recorded_rejections_match: bool
    group_misuse: list[dict[str, str]] = field(default_factory=list)
    rescore: float = math.nan
    rescore_matches: bool = False
    provenance_missing: list[str] = field(default_factory=list)
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    agent_seconds: float = 0.0
    errors: int = 0


def family_of(template_id: str | None) -> str:
    """The template family: the template ID up to its first dot."""
    return (template_id or "none").split(".", 1)[0]


def _tool_results(saved: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Parsed payloads of the trace's tool messages (unparseable ones skipped)."""
    out = []
    for node in saved["nodes"]:
        msg = node.get("message") or {}
        if msg.get("role") != "tool":
            continue
        content = msg.get("content")
        if not isinstance(content, str):
            content = "".join(part.get("text", "") for part in content or [])
        try:
            payload = json.loads(content)
        except ValueError:
            continue
        if isinstance(payload, dict):
            out.append(payload)
    return out


def _sampled_tool_calls(saved: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """(name, arguments) of every tool call in a model-sampled assistant message.
    Arguments that aren't a JSON object come back as ``{}``."""
    out = []
    for node in saved["nodes"]:
        msg = node.get("message") or {}
        if not node.get("sampled") or msg.get("role") != "assistant":
            continue
        for call in msg.get("tool_calls") or []:
            try:
                args = json.loads(call.get("arguments") or "{}")
            except ValueError:
                args = {}
            out.append((call["name"], args if isinstance(args, dict) else {}))
    return out


def _misused_ids(parsed: Any, members: Mapping[str, str]) -> list[dict[str, str]]:
    """Claim entity IDs that are members of a group node."""
    if not isinstance(parsed, Answer):
        return []
    out = []
    for i, claim in enumerate(parsed.claims):
        for role in ("subject_id", "object_id"):
            eid = getattr(claim, role)
            if eid in members:
                out.append({"claim": str(i), "role": role, "member": eid, "group": members[eid]})
    return out


def trace_row(saved: Mapping[str, Any], members: Mapping[str, str], max_calls: int) -> TraceRow:
    """Summarize one saved trace: read what was recorded, recompute what can be
    recomputed offline, and compare."""
    trace = vf.Trace.model_validate(saved)
    item = QuestionItem.model_validate(saved["task"]["data"])
    metrics = {k: float(v) for k, v in (saved.get("metrics") or {}).items()}
    reward = float(saved["rewards"][REWARD_NAME]["score"])
    info = saved.get("info") or {}

    # Offline rescore with the recorded constants.
    params = RewardParams(**info["reward_params"]) if "reward_params" in info else RewardParams()
    rescore = evaluate(trace, item, params).breakdown.reward

    parsed = parse_output(final_reply(trace))
    outcome = {"Answer": "answer", "Abstain": "abstain"}.get(type(parsed).__name__, "parse_failure")
    parse_reason = getattr(parsed, "reason", None) if outcome == "parse_failure" else None

    calls = _sampled_tool_calls(saved)
    by_tool = Counter(name for name, _ in calls)
    neighbors = [args for name, args in calls if name == NEIGHBORS_TOOL]
    payloads = _tool_results(saved)
    notes = [p.get("note") or "" for p in payloads]

    audit = audit_retrieval(trace)
    rejections = dict(audit.rejection_counts())
    by_reason_tool = Counter(f"{r.reason}/{r.tool_name or 'unknown'}" for r in audit.rejected)
    recorded = {reason: metrics.get(f"rejected/{reason}", 0.0) for reason in get_args(RejectionReason)}
    recorded_match = all(recorded[r] == rejections.get(r, 0) for r in recorded)

    usage = [c.get("usage") or {} for c in saved.get("calls") or []]
    agent = (saved.get("timing") or {}).get("agent") or {}
    return TraceRow(
        item_id=item.id,
        qtype=item.qtype,
        answerable=item.answerable,
        template_id=item.template_id or "none",
        family=family_of(item.template_id),
        reward=reward,
        metrics=metrics,
        outcome=outcome,
        parse_reason=parse_reason,
        tool_calls=len(calls),
        calls_by_tool=dict(by_tool),
        neighbors_calls=len(neighbors),
        neighbors_with_relation=sum(1 for a in neighbors if a.get("relation")),
        unknown_relation_notes=sum(1 for n in notes if n.startswith(UNKNOWN_RELATION_NOTE)),
        budget_notes=sum(1 for n in notes if n.startswith(BUDGET_NOTE)),
        over_budget=len(calls) > max_calls,
        rejections=rejections,
        rejections_by_tool=dict(by_reason_tool),
        recorded_rejections_match=recorded_match,
        group_misuse=_misused_ids(parsed, members),
        rescore=rescore,
        rescore_matches=math.isclose(rescore, reward, abs_tol=1e-9),
        provenance_missing=[k for k in PROVENANCE_KEYS if k not in info],
        model_calls=len(saved.get("calls") or []),
        input_tokens=sum(int(u.get("prompt_tokens") or 0) for u in usage),
        output_tokens=sum(int(u.get("completion_tokens") or 0) for u in usage),
        agent_seconds=float(agent.get("end", 0) - agent.get("start", 0)) if agent else 0.0,
        errors=len(saved.get("errors") or []),
    )


# --- Aggregates ---------------------------------------------------------------------------


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def aggregate(rows: Sequence[TraceRow]) -> dict[str, Any]:
    """Aggregate statistics over a group of rollouts.

    Components are averaged over the rollouts that define them (``n_<component>``
    says how many). Rates are fractions of the group.
    """
    n = len(rows)
    out: dict[str, Any] = {"n": n, "reward": _mean([r.reward for r in rows])}
    for comp in COMPONENTS:
        vals = [r.metrics[comp] for r in rows if comp in r.metrics]
        out[comp] = _mean(vals)
        out[f"n_{comp}"] = len(vals)
    out["format_ok"] = _mean([r.metrics.get("format_ok", 0.0) for r in rows])
    out["parse_failure_rate"] = _mean([float(r.outcome == "parse_failure") for r in rows])
    out["parse_failure_reasons"] = dict(Counter(r.parse_reason for r in rows if r.parse_reason))
    out["abstain_rate"] = _mean([float(r.outcome == "abstain") for r in rows])
    out["abstention_by_answerable"] = {
        a: {"n": len(g), "abstained": sum(r.outcome == "abstain" for r in g)}
        for a, g in sorted(_group(rows, lambda r: r.answerable).items())
    }
    out["tool_calls_mean"] = _mean([float(r.tool_calls) for r in rows])
    out["tool_calls_max"] = max((r.tool_calls for r in rows), default=0)
    out["calls_by_tool"] = dict(sum((Counter(r.calls_by_tool) for r in rows), Counter()))
    out["over_budget"] = sum(r.over_budget for r in rows)
    out["budget_notes"] = sum(r.budget_notes for r in rows)
    out["neighbors_calls"] = sum(r.neighbors_calls for r in rows)
    out["neighbors_with_relation"] = sum(r.neighbors_with_relation for r in rows)
    out["unknown_relation_notes"] = sum(r.unknown_relation_notes for r in rows)
    reasons = Counter({reason: 0 for reason in get_args(RejectionReason)})
    for r in rows:
        reasons.update(r.rejections)
    out["rejections"] = dict(reasons)
    out["rejections_by_tool"] = dict(sum((Counter(r.rejections_by_tool) for r in rows), Counter()))
    out["recorded_rejections_match"] = all(r.recorded_rejections_match for r in rows)
    for key in ("claims", "supported_facts", "unsupported_facts", "voided_claims",
                "duplicate_claims", "stripped_cites", "root_reply_present", "root_reply_differs"):
        out[key] = sum(r.metrics.get(key, 0.0) for r in rows)
    out["group_misuse_claims"] = len({(r.item_id, m["claim"]) for r in rows for m in r.group_misuse})
    out["group_misuse_rollouts"] = sum(bool(r.group_misuse) for r in rows)
    out["rescore_matches"] = sum(r.rescore_matches for r in rows)
    out["provenance_complete"] = sum(not r.provenance_missing for r in rows)
    out["model_calls"] = sum(r.model_calls for r in rows)
    out["input_tokens"] = sum(r.input_tokens for r in rows)
    out["output_tokens"] = sum(r.output_tokens for r in rows)
    out["agent_seconds_mean"] = _mean([r.agent_seconds for r in rows])
    out["errors"] = sum(r.errors for r in rows)
    return out


def _group(rows: Iterable[TraceRow], key) -> dict[str, list[TraceRow]]:
    groups: dict[str, list[TraceRow]] = defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)
    return groups


def summarize(saved_traces: Sequence[Mapping[str, Any]], config: Mapping[str, Any],
              members: Mapping[str, str] | None) -> dict[str, Any]:
    """The whole summary of one run, as a JSON-ready dict."""
    tools = ((config.get("env") or {}).get("taskset") or {}).get("tools") or {}
    max_calls = int(tools.get("max_calls", DEFAULT_MAX_CALLS))
    rows = [trace_row(t, members or {}, max_calls) for t in saved_traces]
    return {
        "model": config.get("model") or _model_from_calls(saved_traces),
        "base_url": (config.get("client") or {}).get("base_url"),
        "max_calls": max_calls,
        "store_build_ids": sorted({str(t.get("info", {}).get("store_build_id")) for t in saved_traces}),
        "items_sha256": sorted({str(t.get("info", {}).get("items_sha256")) for t in saved_traces}),
        "group_check": members is not None,
        "overall": aggregate(rows),
        "by_qtype": {k: aggregate(g) for k, g in sorted(_group(rows, lambda r: r.qtype).items())},
        "by_family": {k: aggregate(g) for k, g in sorted(_group(rows, lambda r: r.family).items())},
        "by_template": {k: aggregate(g) for k, g in sorted(_group(rows, lambda r: r.template_id).items())},
        "rollouts": [asdict(r) for r in sorted(rows, key=lambda r: (r.qtype, r.item_id))],
    }


def _model_from_calls(saved_traces: Sequence[Mapping[str, Any]]) -> str | None:
    models = {c.get("model") for t in saved_traces for c in t.get("calls") or []}
    return ", ".join(sorted(m for m in models if m)) or None


# --- Markdown -------------------------------------------------------------------------------


def _fmt(x: Any, digits: int = 3) -> str:
    if x is None:
        return "–"
    if isinstance(x, float):
        return f"{x:.{digits}f}"
    return str(x)


def _table(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_fmt(c) for c in row) + " |" for row in rows]
    return lines


def _score_rows(groups: Mapping[str, Mapping[str, Any]]) -> list[list[Any]]:
    return [
        [name, g["n"], g["reward"], g["format_ok"], g["parse_failure_rate"], g["abstain_rate"],
         *(g[c] for c in COMPONENTS)]
        for name, g in groups.items()
    ]


SCORE_HEADER = ["group", "n", "reward", "format_ok", "parse fail", "abstained",
                "cit. valid", "claim supp.", "coverage", "penalty", "abst. correct",
                "flagged", "premise asserted"]


def to_markdown(s: Mapping[str, Any], title: str) -> str:
    """The summary of one run as markdown."""
    o = s["overall"]
    n = o["n"]
    out = [f"# Run summary: {title}", ""]
    out += [
        f"- Model: `{s['model']}`; endpoint `{s['base_url']}`; {n} rollouts; `max_calls` {s['max_calls']}.",
        f"- Store build: {', '.join(f'`{b}`' for b in s['store_build_ids'])}; "
        f"items sha256: {', '.join(f'`{h[:12]}…`' for h in s['items_sha256'])}.",
        f"- Provenance (`{'`, `'.join(PROVENANCE_KEYS)}`) present: {o['provenance_complete']}/{n}.",
        f"- Offline rescore equals the recorded reward: {o['rescore_matches']}/{n}.",
        f"- Recorded rejection metrics equal the offline audit: {'yes' if o['recorded_rejections_match'] else 'NO'}.",
        f"- Rollout errors: {o['errors']}.",
        f"- Model calls {o['model_calls']}; input tokens (billed, summed per call) {o['input_tokens']}; "
        f"output tokens {o['output_tokens']}; mean agent time {_fmt(o['agent_seconds_mean'], 1)} s.",
        "",
        "Components are means over the rollouts that define them; a dash means none did.",
        "",
        "## Reward and components",
        "",
    ]
    out += _table(SCORE_HEADER, _score_rows({"overall": o, **s["by_qtype"]}))
    out += ["", "### By template family", ""]
    out += _table(SCORE_HEADER, _score_rows(s["by_family"]))
    out += ["", "### By template", ""]
    out += _table(["template", "n", "reward", "abstained", "parse fail", "answerable classes"], [
        [k, g["n"], g["reward"], g["abstain_rate"], g["parse_failure_rate"],
         ", ".join(f"{a} {v['n']}" for a, v in g["abstention_by_answerable"].items())]
        for k, g in s["by_template"].items()
    ])
    out += ["", "## Parse failures and abstention", ""]
    reasons = o["parse_failure_reasons"]
    out.append(f"- Parse-failure rate {_fmt(o['parse_failure_rate'])}; reasons: "
               + (", ".join(f"`{k}` {v}" for k, v in sorted(reasons.items())) or "none") + ".")
    out.append(f"- Reason vocabulary: {', '.join(f'`{r}`' for r in get_args(ParseFailureReason))}.")
    out += [""]
    out += _table(["answerable", "n", "abstained", "abstain rate"], [
        [a, v["n"], v["abstained"], v["abstained"] / v["n"] if v["n"] else None]
        for a, v in o["abstention_by_answerable"].items()
    ])
    out += ["", "## Strict retrieval audit (recomputed offline)", ""]
    out += _table(["reason", "rejected tool results"], sorted(o["rejections"].items()))
    if o["rejections_by_tool"]:
        out += ["", *_table(["reason/tool", "n"], sorted(o["rejections_by_tool"].items()))]
    out += ["", "## Tool use", ""]
    out += [
        f"- Tool calls per rollout: mean {_fmt(o['tool_calls_mean'], 2)}, max {o['tool_calls_max']}.",
        "- Calls by tool: " + ", ".join(f"`{k}` {v}" for k, v in sorted(o["calls_by_tool"].items())) + ".",
        f"- Budget: {o['over_budget']} rollouts made more than {s['max_calls']} calls; "
        f"{o['budget_notes']} tool results carried the budget note.",
        f"- `{NEIGHBORS_TOOL}`: {o['neighbors_calls']} calls, {o['neighbors_with_relation']} with a relation "
        f"filter; {o['unknown_relation_notes']} results said \"Unknown relation\".",
        "",
        "## Final answers",
        "",
        f"- `root_reply` present in {int(o['root_reply_present'])} rollouts; differs from the scored "
        f"reply in {int(o['root_reply_differs'])}.",
        f"- Claims {int(o['claims'])}: supported facts {int(o['supported_facts'])}, unsupported facts "
        f"{int(o['unsupported_facts'])}, voided {int(o['voided_claims'])}, duplicates "
        f"{int(o['duplicate_claims'])}, stripped cites {int(o['stripped_cites'])}.",
    ]
    if s["group_check"]:
        out.append(f"- Group-ID misuse (a claim names a member ID of a `group:` node): "
                   f"{o['group_misuse_claims']} claims in {o['group_misuse_rollouts']} rollouts.")
    else:
        out.append("- Group-ID misuse: not checked (store not found).")
    out += ["", "## Rollouts", ""]
    out += _table(
        ["item", "type", "answerable", "template", "reward", "outcome", "calls", "rel. filter",
         "budget notes", "rejected", "voided", "dup.", "group misuse", "rescore ok", "in tok", "out tok"],
        [[r["item_id"], r["qtype"], r["answerable"], r["template_id"], r["reward"],
          r["outcome"] + (f" ({r['parse_reason']})" if r["parse_reason"] else ""),
          r["tool_calls"], f"{r['neighbors_with_relation']}/{r['neighbors_calls']}", r["budget_notes"],
          sum(r["rejections"].values()), int(r["metrics"].get("voided_claims", 0)),
          int(r["metrics"].get("duplicate_claims", 0)),
          ", ".join(m["member"] for m in r["group_misuse"]) or "", "yes" if r["rescore_matches"] else "NO",
          r["input_tokens"], r["output_tokens"]]
         for r in s["rollouts"]],
    )
    return "\n".join(out) + "\n"


def comparison_markdown(summaries: Mapping[str, Mapping[str, Any]]) -> str:
    """Side by side: one column per run, overall and per question type."""
    names = list(summaries)
    keys = [("rollouts", lambda g: g["n"]), ("reward", lambda g: g["reward"]),
            ("parse fail", lambda g: g["parse_failure_rate"]), ("abstained", lambda g: g["abstain_rate"]),
            ("tool calls/rollout", lambda g: g["tool_calls_mean"]), ("over budget", lambda g: g["over_budget"]),
            ("rejected results", lambda g: sum(g["rejections"].values())),
            ("group misuse claims", lambda g: g["group_misuse_claims"])]
    out = ["# Runs side by side", ""]
    out += _table(["overall", *names], [[label, *(f(summaries[n]["overall"]) for n in names)]
                                        for label, f in keys])
    qtypes = sorted({q for s in summaries.values() for q in s["by_qtype"]})
    out += ["", *_table(["reward by type", *names], [
        [q, *(summaries[n]["by_qtype"].get(q, {}).get("reward") for n in names)] for q in qtypes])]
    return "\n".join(out) + "\n"


# --- CLI --------------------------------------------------------------------------------


def run_dir_of(path: Path) -> Path:
    """Accept a run directory, its traces.jsonl, or a parent holding exactly one run."""
    if path.is_file():
        return path.parent
    if (path / "traces.jsonl").exists():
        return path
    found = sorted(path.glob("*/traces.jsonl"))
    if len(found) != 1:
        raise SystemExit(f"{path}: expected one run directory with traces.jsonl, found {len(found)}")
    return found[0].parent


def store_path_of(config: Mapping[str, Any], override: Path | None) -> Path | None:
    if override is not None:
        return override
    raw = (((config.get("env") or {}).get("taskset") or {}).get("tools") or {}).get("store_path")
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.is_absolute() else Path(__file__).resolve().parents[2] / p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("runs", nargs="+", type=Path, help="run directories (or their traces.jsonl)")
    ap.add_argument("--store", type=Path, default=None,
                    help="evidence store for the group-ID check (default: the run config's store_path)")
    ap.add_argument("--no-write", action="store_true", help="print only; don't write summary files")
    args = ap.parse_args(argv)

    summaries: dict[str, dict[str, Any]] = {}
    for given in args.runs:
        run_dir = run_dir_of(given)
        config = load_config(run_dir)
        members = load_group_members(store_path_of(config, args.store))
        summary = summarize(load_traces(run_dir), config, members)
        md = to_markdown(summary, run_dir.name)
        if not args.no_write:
            (run_dir / "summary.md").write_text(md, encoding="utf-8")
            (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(md)
        summaries[str(summary["model"] or run_dir.name)] = summary
    if len(summaries) > 1:
        print(comparison_markdown(summaries))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
