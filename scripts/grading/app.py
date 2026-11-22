"""The local grading viewer: one trace at a time, in export order, writing the labels.

    uv run streamlit run scripts/grading/app.py \\
        --server.address 127.0.0.1 --server.headless true \\
        --browser.gatherUsageStats false -- [--dir $NGQ_DATA_DIR/grading/round-first]

    uv run python scripts/grading/app.py --check [--dir ...]

The first form starts the viewer on this machine only. The app refuses to show
anything unless Streamlit is bound to localhost and usage statistics are off; it
makes no network calls of its own. The second form loads the export and the labels
without starting a server and prints pass/fail (no trace content).

The viewer follows ``docs/labeling_guide.md``:

1. **Step 1** shows the question, the tool calls with their results (with the
   direction details on directed edges, unseen-edge-ID marks and rejected results),
   and the final message (laid out by claim when it parses; the earlier answer when
   the final message has no text). The grader sets the output form, then grades each
   claim: ``supported``, ``cites_unretrieved``, ``relevant``, ``duplicate_of`` and a
   note.
2. **Reveal** locks the step-1 answers (written to ``progress.json``) and shows step
   2: ``answerable``, the gold points, the premise and the graph gaps. The grader
   then sets each gold point's ``covered``, each claim's ``premise_restatement``
   (when the item has a premise, which step 2 reveals) and the item fields.
3. **Save** validates the record against the schema and appends it to the labels
   file (``label_store.save_label``), then moves to the next trace. Saved traces are never
   reopened or overwritten; closing the viewer and starting it again resumes at the
   first ungraded trace.

Every field starts empty: nothing is pre-selected or suggested. A field shows only
where the guide says it applies (``label_schema.applies``). The viewer shows no reward,
score, model, item type or sample composition: only "Trace n of N".
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from grading_common import GradingError, default_round_dir, guide_version
from label_schema import (
    COVERED,
    EARLIER_ANSWER,
    FLAGS,
    OUTPUT_FORMS,
    OVERALL,
    PREMISE_HANDLING,
    RESPONSES,
    SUPPORTED,
    TEXT_SCALE,
    YES_NO,
    applies,
)

import label_store as L

LOCAL_ADDRESSES = ("localhost", "127.0.0.1")

OVERALL_ANCHORS = {
    3: "3 · grounded and complete",
    2: "2 · grounded and useful, with a gap",
    1: "1 · not useful but not misleading",
    0: "0 · misleading",
}


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", type=Path, default=None,
                    help="round directory (default: $NGQ_GRADING_DIR if set, else "
                         "$NGQ_DATA_DIR/grading/round-first)")
    ap.add_argument("--check", action="store_true",
                    help="load the export and labels headlessly; print pass/fail")
    # parse_known_args: under Streamlit's AppTest the process's own arguments are
    # pytest's, which this parser must ignore.
    args, _ = ap.parse_known_args(argv)
    return args


def check(round_dir: Path) -> int:
    """Headless check: the export loads, the labels (if any) validate and follow the
    export's order, the guide is readable, and Streamlit imports. Prints counts only."""
    from validate import validate_labels

    try:
        export = L.load_export(round_dir)
        labels = L.load_labels(round_dir)
        nxt = L.next_index(export, labels)
        rep = validate_labels(labels, export)
        version = guide_version()
        import streamlit
    except (GradingError, OSError, ImportError) as e:
        print(f"app check: FAIL: {e}")
        return 1
    if not rep.ok:
        print(f"app check: FAIL: labels don't validate ({len(rep.errors)} error(s))")
        return 1
    at = "all graded" if nxt is None else f"next is trace {nxt + 1}"
    print(f"app check: PASS (export loads: {len(export)} traces; {len(labels)} graded, "
          f"{at}; guide {version}; streamlit {streamlit.__version__})")
    return 0


# --- Streamlit UI ------------------------------------------------------------------------


def _radio(st: Any, label: str, options: Any, key: str, help: str | None = None,
           fmt: Any = None) -> Any:
    """A horizontal radio with nothing selected until the grader picks."""
    return st.radio(label, options, index=None, key=key, horizontal=True, help=help,
                    format_func=fmt or str)


def _entity(e: dict[str, Any] | None) -> str:
    if not e:
        return ""
    return f"{e.get('name')} ({e.get('id')})"


def render_payload(st: Any, payload: dict[str, Any]) -> None:
    """A tool payload: note, entities and evidence, with direction details."""
    if payload.get("note"):
        st.caption(f"Note to the model: {payload['note']}")
    if payload.get("truncated"):
        st.caption("Truncated: the tool capped this result.")
    if payload.get("entities"):
        st.dataframe([{"id": e["id"], "name": e["name"], "type": e["type"]}
                      for e in payload["entities"]], hide_index=True, width="stretch")
    rows = []
    for ev in payload.get("evidence") or []:
        d = ev.get("direction") or {}
        stored = d.get("stored_order") or {}
        rows.append({
            "edge": ev["id"],
            "subject": _entity(ev["subject"]),
            "relation": ev["relation"],
            "label shown": ev.get("display_relation") or "",
            "object": _entity(ev["object"]),
            "directed": "yes" if ev.get("directed") else "no",
            "stored order": (f"{stored.get('subject_id')} → {stored.get('object_id')}"
                             if ev.get("directed") else ""),
            "PrimeKG label": d.get("primekg_label") or "" if ev.get("directed") else "",
            "grouped end": ("yes" if d.get("grouped_end") else "no") if ev.get("directed") else "",
        })
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    if not rows and not payload.get("entities") and not payload.get("note"):
        st.caption("Empty result.")


def render_result(st: Any, result: dict[str, Any]) -> None:
    if result["status"] == "rejected":
        st.error(f"Rejected by the harness ({result['reason']}): not retrieved. "
                 f"{result.get('detail') or ''}")
    if result.get("payload") is not None:
        render_payload(st, result["payload"])
    elif result.get("raw") is not None:
        st.code(result["raw"], language=None, wrap_lines=True)


def render_message(st: Any, msg: dict[str, Any]) -> None:
    """A final (or earlier) message: raw text, then the layout when it parses."""
    st.code(msg["raw"], language=None, wrap_lines=True)
    lay = msg.get("layout")
    if not lay:
        return
    if lay.get("abstain"):
        st.markdown(f"**Abstains.** Reason: {lay['reason']}")
        return
    if lay["claims"]:
        st.dataframe([
            {"claim": c["claim_index"],
             "subject": f"{c.get('subject_name') or ''} ({c['subject_id']})",
             "relation": c["relation"],
             "object": f"{c.get('object_name') or ''} ({c['object_id']})",
             "cites": ", ".join(c["cites"])}
            for c in lay["claims"]], hide_index=True, width="stretch")
    else:
        st.markdown("_No claims._")
    st.markdown(f"**answer_text:** {lay['answer_text'] or '_(empty)_'}")
    parts = lay.get("unsupported_parts") or []
    st.markdown("**unsupported_parts:** " + ("; ".join(parts) if parts else "_(none)_"))


def render_step1(st: Any, rec: dict[str, Any]) -> None:
    s1 = rec["step1"]
    st.subheader("Question")
    st.markdown(s1["question"])
    st.subheader("Tool calls")
    if not s1["tool_calls"]:
        st.markdown("_No tool calls._")
    for c in s1["tool_calls"]:
        st.markdown(f"**Call {c['call']}: `{c['name']}`** `{c['arguments']}`")
        if c["unseen_edge_ids"]:
            st.warning("Calls edge ID(s) no earlier result showed: "
                       + ", ".join(c["unseen_edge_ids"]))
        if not c["results"]:
            st.caption("No result.")
        for r in c["results"]:
            render_result(st, r)
    if s1["unmatched_tool_results"]:
        st.subheader("Tool results that answer no call of the model's (not retrieved)")
        for r in s1["unmatched_tool_results"]:
            render_result(st, r)
    st.subheader("Final message")
    if s1["final_message"]["raw"]:
        render_message(st, s1["final_message"])
    else:
        st.info("The final message has no text.")
    if s1.get("earlier_answer"):
        st.warning("Earlier message, shown because the final message has no text. "
                   "Record whether it held an answer a user would have seen.")
        render_message(st, s1["earlier_answer"])


def step1_form(st: Any, rec: dict[str, Any]) -> dict[str, Any] | None:
    """The step-1 fields. Returns the answers, or None until a claim count is set."""
    gid = rec["grading_id"]
    st.header("Step 1: output form and claims (gold hidden)")
    form = _radio(st, "output_form", OUTPUT_FORMS, f"{gid}:output_form",
                  "Can a reader tell what the model said? (R8)")
    earlier = None
    if applies(form).earlier_answer:
        earlier = _radio(st, "earlier_answer", EARLIER_ANSWER, f"{gid}:earlier_answer",
                         "Did an earlier message hold an answer a user would have seen? (Q14)")
    a = applies(form, earlier)
    claims: list[dict[str, Any]] = []
    if a.claims:
        msg = L.graded_message(rec, earlier) or {}
        lay = msg.get("layout") or {}
        if "claims" in lay:
            n = len(lay["claims"])
        elif lay.get("abstain"):
            n = 0
        else:
            n = st.number_input("Number of claims you can read (number them in reading order)",
                                min_value=0, step=1, value=None, key=f"{gid}:n_claims")
            if n is None:
                return None
        for i in range(int(n)):
            st.markdown(f"**Claim {i}**")
            cols = st.columns(3)
            sup = _radio(cols[0], "supported", SUPPORTED, f"{gid}:c{i}:supported",
                         "Does a retrieved edge state this fact? (R1–R4, R13, R16, R17)")
            cun = _radio(cols[1], "cites_unretrieved", YES_NO, f"{gid}:c{i}:cites_unretrieved",
                         "Does the claim cite any ID that wasn't retrieved?")
            rel = _radio(cols[2], "relevant", YES_NO, f"{gid}:c{i}:relevant",
                         "Does the claim answer the question or form a needed step? (Q10, Q23)")
            dup: Any = None
            if i > 0:
                dup = st.selectbox("duplicate_of", ["not a duplicate", *range(i)], index=None,
                                   key=f"{gid}:c{i}:duplicate_of",
                                   help="Index of an earlier claim stating the same fact")
            note = st.text_input("note (optional)", key=f"{gid}:c{i}:note")
            claims.append({"supported": sup, "cites_unretrieved": cun, "relevant": rel,
                           "duplicate_of": None if dup in (None, "not a duplicate") else dup,
                           "duplicate_set": i == 0 or dup is not None, "note": note})
    return {"output_form": form, "earlier_answer": earlier, "claims": claims}


def render_step2(st: Any, rec: dict[str, Any]) -> None:
    s2 = rec["step2"]
    st.header("Step 2: gold and item")
    st.markdown(f"**answerable:** `{s2['answerable']}`")
    if s2["gold_points"]:
        st.dataframe([
            {"gold point": g["id"],
             "subject": f"{g.get('subject_name') or ''} ({g['subject_id']})",
             "relation": g["relation"],
             "object": f"{g.get('object_name') or ''} ({g['object_id']})",
             "directed": "yes" if g["directed"] else "no",
             "evidence": ", ".join(g["evidence"])}
            for g in s2["gold_points"]], hide_index=True, width="stretch")
    else:
        st.markdown("_No gold points._")
    if s2["premise"]:
        p = s2["premise"]
        st.markdown(
            f"**Premise (false):** {p.get('subject_name') or ''} ({p['subject_id']}) "
            f"`{p['relation']}` {p.get('object_name') or ''} ({p['object_id']})"
            f"{' · directed' if p['directed'] else ''}")
    for g in s2["graph_gaps"]:
        t = g.get("triple")
        extra = (f" — {t['subject_id']} `{t['relation']}` {t['object_id']}"
                 f"{' · directed' if t['directed'] else ''}") if t else ""
        st.markdown(f"**Graph gap:** {g['description']}{extra}")


def step2_form(st: Any, rec: dict[str, Any], step1: dict[str, Any]) -> dict[str, Any]:
    gid = rec["grading_id"]
    s2 = rec["step2"]
    has_premise = s2["premise"] is not None
    out: dict[str, Any] = {"gold_points": []}
    for g in s2["gold_points"]:
        out["gold_points"].append({"gold_point_id": g["id"], "covered": _radio(
            st, f"covered: {g['id']}", COVERED, f"{gid}:gp:{g['id']}",
            "Does the answer state this point, and with what grounding? (Q24)")})
    a0 = applies(step1["output_form"], step1["earlier_answer"], None, has_premise)
    if a0.premise_restatement and step1["claims"]:
        out["premise_restatement"] = [
            _radio(st, f"claim {i}: premise_restatement", YES_NO, f"{gid}:c{i}:premise",
                   "Does the claim assert the item's false premise?")
            for i in range(len(step1["claims"]))]
    response = None
    if a0.response:
        response = _radio(st, "response", RESPONSES, f"{gid}:response",
                          "What the model did in substance (R15)")
    out["response"] = response
    out["choice_correct"] = _radio(st, "choice_correct", YES_NO, f"{gid}:choice_correct",
                                   "Was answering, declining or flagging the right call?")
    a = applies(step1["output_form"], step1["earlier_answer"], response, has_premise)
    if a.abstain_reason_ok:
        out["abstain_reason_ok"] = _radio(st, "abstain_reason_ok", TEXT_SCALE,
                                          f"{gid}:abstain_reason_ok", "Q18")
    if a.flag:
        out["flag"] = _radio(st, "flag", FLAGS, f"{gid}:flag", "What unsupported_parts says (R7)")
    if a.text_ok:
        out["text_ok"] = _radio(st, "text_ok", TEXT_SCALE, f"{gid}:text_ok", "Q18")
    a = applies(step1["output_form"], step1["earlier_answer"], response, has_premise,
                out.get("text_ok"), out.get("abstain_reason_ok"))
    if a.text_issue:
        out["text_issue"] = st.text_input("text_issue (one line, required)", key=f"{gid}:text_issue")
    if a.premise_handling:
        out["premise_handling"] = _radio(st, "premise_handling", PREMISE_HANDLING,
                                         f"{gid}:premise_handling", "R10")
    out["overall"] = _radio(st, "overall", OVERALL, f"{gid}:overall", "Q7",
                            fmt=lambda v: OVERALL_ANCHORS[v])
    out["item_issue"] = st.text_input("item_issue (optional)", key=f"{gid}:item_issue")
    out["note"] = st.text_input("note (optional)", key=f"{gid}:note")
    return out


def render(round_dir: Path) -> None:
    import streamlit as st

    st.set_page_config(page_title="Grading", layout="wide")
    if (st.get_option("server.address") not in LOCAL_ADDRESSES
            or st.get_option("browser.gatherUsageStats")):
        st.error("Start the viewer bound to localhost with usage statistics off: "
                 "--server.address 127.0.0.1 --browser.gatherUsageStats false "
                 "(see validation/README.md).")
        st.stop()
    try:
        export = L.load_export(round_dir)
        nxt = L.next_index(export, L.load_labels(round_dir))
    except GradingError as e:
        st.error(str(e))
        st.stop()
    if nxt is None:
        st.success("Every trace is graded. Run validate.py --complete, then lock.py.")
        st.stop()
    rec = export[nxt]
    gid = rec["grading_id"]
    st.title(f"Trace {nxt + 1} of {len(export)}")
    render_step1(st, rec)

    try:
        draft = L.load_progress(round_dir, gid)
    except GradingError as e:
        st.error(str(e))
        st.stop()
    if draft is None:
        step1 = step1_form(st, rec)
        if st.button("Reveal step 2 (locks the step-1 answers)", type="primary"):
            problems = [] if step1 is not None else ["claims: set the number of claims"]
            if step1 is not None:
                problems += L.step1_errors(step1)
                problems += [f"claims[{i}].duplicate_of: not set"
                             for i, c in enumerate(step1["claims"]) if not c["duplicate_set"]]
            if problems:
                st.error("; ".join(problems))
            else:
                for c in step1["claims"]:
                    c.pop("duplicate_set", None)
                try:
                    L.reveal(round_dir, gid, step1)
                except GradingError as e:
                    st.error(str(e))
                else:
                    st.rerun()
        return

    step1 = draft["step1"]
    st.header("Step 1 answers (locked)")
    st.markdown(f"output_form: `{step1['output_form']}`"
                + (f" · earlier_answer: `{step1['earlier_answer']}`"
                   if step1.get("earlier_answer") else ""))
    if step1["claims"]:
        st.dataframe([{"claim": i, **c} for i, c in enumerate(step1["claims"])],
                     hide_index=True, width="stretch")
    render_step2(st, rec)
    step2 = step2_form(st, rec, step1)
    if st.button("Save and go to the next trace", type="primary"):
        record = L.assemble_record(gid, step1, step2, guide_version=guide_version(),
                                   has_premise=rec["step2"]["premise"] is not None)
        try:
            L.save_label(round_dir, record)
        except GradingError as e:
            st.error(str(e))
        else:
            for k in [k for k in st.session_state if str(k).startswith(f"{gid}:")]:
                del st.session_state[k]
            st.rerun()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        env_dir = os.environ.get("NGQ_GRADING_DIR")
        round_dir = args.dir or (Path(env_dir) if env_dir else default_round_dir())
    except GradingError as e:
        print(f"app: FAIL: {e}")
        return 1
    if args.check:
        return check(round_dir)
    from streamlit.runtime import exists

    if not exists():
        print("Start the viewer with `uv run streamlit run scripts/grading/app.py ...` "
              "(see validation/README.md), or pass --check.")
        return 2
    render(round_dir)
    return 0


if __name__ == "__main__":
    # Under `streamlit run` this runs on every rerun and returns 0; exit only on failure.
    if code := main():
        sys.exit(code)
