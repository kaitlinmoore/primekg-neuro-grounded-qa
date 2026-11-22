# Validation: hand grading of real traces

Hand labels check the reward against Kaitlin's own judgment of the property it claims to measure. `docs/labeling_guide.md` says how to grade; this folder holds the committed products of each locked grading round, and this file says how a round runs. The tools are in `scripts/grading/`.

No AI views a grading export, or writes, suggests, checks or edits a label. The tools prepare the sample and check the labels' shape; every grade is Kaitlin's.

## Files

In the round directory, `$NGQ_DATA_DIR/grading/round-first/` (outside the repo, not committed until the stage named):

| File | Written by | What it holds | Committed |
|---|---|---|---|
| `grading_key.jsonl` | `draw.py` | `grading_id` → position, model, run, trace, item, `answerable` | after unblinding |
| `sealed.json` | `draw.py` | the sealed validation subset: a list of `grading_id`s | after unblinding |
| `draw.json` | `draw.py` | seeds, requested cell sizes, source-file hashes, the exclusion list's size and hash, cell counts | after unblinding |
| `export.jsonl` | `export.py` | what the grader sees: step 1 (question, tool calls and results, final message) and step 2 (gold, `answerable`, premise, graph gaps) | never (it holds item and trace text) |
| `labels_first.jsonl` | the viewer | one label record per graded trace, in grading order | no; the locked, stripped copy is |
| `progress.json` | the viewer | the trace in progress: step-1 answers, locked at reveal | never |
| `notes_first.jsonl` | `lock.py` | the free-text fields, split off the labels | after unblinding, only if no note quotes item or trace text |

In this folder (committed):

| File | Written by | What it holds |
|---|---|---|
| `labels_first.jsonl` | `lock.py` | the locked labels without free text (`text_issue`, `item_issue`, `note`, claim and failure notes): IDs and structured fields only |
| `grading_log.md` | `lock.py` | one entry per locked labels file: date, count, guide version, and the SHA-256 of the labels, notes, export, key file, sealed list and draw record |

Partial labels are never committed.

## Steps

Run from the repo root in WSL, with the env loaded (`echo $UV_PROJECT_ENVIRONMENT` must print a path). No API key is needed or used.

**1. Draw.** A seeded sample, stratified by `answerable` and model, each item at most once, with items named in four committed docs left out (`docs/methodology.md`, `docs/reward_hacking_audit.md`, `docs/question_set_design.md`, `docs/changelog.md`: they discuss items and their scores). For each `answerable` value it draws the items with one seed and splits them between the models with another, so neither model fills first. It prints pass/fail and hashes only, and stops, writing nothing, if a cell can't be filled.

```bash
uv run python scripts/grading/draw.py \
  --items $NGQ_DATA_DIR/items/primekg-2.1-6d869fa6bb10 \
  --runs outputs/haiku-train-4-2026-11-21/pass-a \
         outputs/sonnet-train-2026-11-21/c1 outputs/sonnet-train-2026-11-21/c2 \
         outputs/sonnet-train-2026-11-21/c3 outputs/sonnet-train-2026-11-21/c4
```

Defaults: seed `ngq-grade-first-v1` (assign, order, ID and sealed seeds derive from it), `--cells correctable=4,no=8,partial=8,yes=12` (traces per model: 64 in all; unequal because the train set has only 10 `correctable` items), `--sealed-per-answerable 5`. It refuses to replace an existing draw unless `--force` is given.

**2. Export.** Builds `export.jsonl` from the key file, then checks it by code (count, order, no forbidden key, every step-2 block, the served label on every directed edge). Neither command prints a record.

```bash
uv run python scripts/grading/export.py --items $NGQ_DATA_DIR/items/primekg-2.1-6d869fa6bb10
uv run python scripts/grading/export.py --check --expect 64
```

**3. Grade.** Start the viewer on this machine only. It refuses to run unless bound to localhost with usage statistics off.

```bash
uv run python scripts/grading/app.py --check    # optional: loads the export, prints pass/fail
uv run streamlit run scripts/grading/app.py \
  --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false
```

Then open <http://127.0.0.1:8501>. One trace at a time, in export order: set the output form and grade each claim with the gold hidden; **Reveal step 2** locks those answers and shows the gold points, `answerable`, premise and graph gaps; grade gold points and the item fields; **Save** validates the record and appends it to the labels file. Close the viewer any time; it resumes at the first ungraded trace. A saved trace isn't reopened.

**4. Validate.** Shape and allowed values only, never content.

```bash
uv run python scripts/grading/validate.py              # any time; reports traces graded
uv run python scripts/grading/validate.py --complete   # before locking
```

**5. Lock.** Once every trace is graded. Writes `validation/labels_first.jsonl` (free text removed), `notes_first.jsonl` in the round directory and an entry in `validation/grading_log.md`, then prints the `git add` command. It never commits, and refuses to replace a locked file: a correction is a new labels version with its reason.

```bash
uv run python scripts/grading/lock.py
```

**6. Unblind.** After the lock is committed: open the key file and the sealed list, commit them with the draw record (`lock.py` prints the commands), and commit the notes only if none quotes item or trace text. Reward–label agreement on the sealed subset isn't computed until the reward is final (guide section 2).
