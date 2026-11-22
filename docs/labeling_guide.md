# Labeling guide

Nov 22, 2026 · @Kaitlin Moore

This guide sets out how to hand-grade a blind sample of about 65 traces so the grades can check reward v1.1 (`docs/reward_spec.md`, 2026-11-21). Kaitlin owns this guide. It contains no label for any real item or trace. Every example uses invented IDs in the style of the contract fixtures (`GENE_A`, `DIS_1`, `E:000001`).

## 1. Purpose and the circularity rule

**Grade the property, not the scoring rule.** The reward claims to measure one thing: did the model answer only from evidence it retrieved, and decline when that evidence couldn't support an answer? Your grades record your own judgment of that property. Where the reward made a design choice, the guide asks for the judgment underneath it, never for the rule itself. A disagreement between your grade and the code then shows something real: a reward bug, a design choice worth revisiting, or a label error.

Two consequences:

- If a field could be filled by re-running a reward rule in your head, it is the wrong field. Split it into the separate observations the rule combines, and let the analysis recombine them.
- Your grades come from you alone. No AI suggests, drafts, checks or pre-fills a grade, a gold judgment or a note. If AI helped make the labels, the check would be circular.

**Reward design choices (set in v0, kept in v1.1)****, and what the guide asks instead.**

| Reward choice | What you record instead |
| --- | --- |
| Strict citations: one cite outside the retrieved set voids the whole claim | Two separate per-claim questions: is the claim supported by retrieved evidence it cites (`supported`), and does it cite anything not retrieved (`cites_unretrieved`)? |
| Support needs a *cited* edge that states the claim; a retrieved but uncited edge doesn't count | `supported` distinguishes `cited` from `retrieved_not_cited`, so the analysis can test that choice |
| Relation labels match exactly; names are never compared | Does the evidence state *what the claim says*? Judge by IDs and meaning; note name mismatches (rules R3, R11) |
| Undirected facts match in either order; `directed` facts in order only | Read the claim as a sentence: does it state the edge's fact? (rules R1, R2) |
| Duplicate facts count once in the penalty | Mark duplicates (`duplicate_of`) and grade every copy on its own |
| Penalty λ·\|U\| / (\|S\| + \|U\|), λ = 0.5 | No penalty field. Per-claim support and relevance, plus the overall ordinal, carry how bad the ungrounded content is |
| No relevance term (true, retrieved, irrelevant claims cost nothing) | Per-claim `relevant` |
| Coverage counts only supported claims; all-or-nothing and equal-weighted per gold point | Per gold point: is it stated, and with what grounding (`covered`)? |
| Format gate: any parse failure scores 0, including prose around the JSON and answer-plus-abstain | `output_form`: can a reader tell what the model said, and in what form? Grade readable content anyway (rule R8) |
| Abstaining on a `no` item scores 1; any answer scores 0 | `choice_correct`: was answering or declining the right call for this item, judged on substance (rule R15)? |
| Abstaining on a `correctable` item earns A = 0.7; a cited correction up to 1.0 | `premise_handling`: did it abstain, correct with support, or accept the premise? No ranking between abstaining and correcting |
| Partial items: flag credit κ = 0.2; any non-blank `unsupported_parts` string counts | `flag`: does the flag name the part the evidence doesn't cover? Recorded for every answered item, not only partial ones |
| `answer_text` and the abstention `reason` are never scored | `text_ok` and `abstain_reason_ok`: is the free text accurate? |
| The final answer is the last sampled assistant message | The export shows that message as the final answer; see open question Q14 for empty final messages |
| Flag-only answers on partial items earn κ | `response` records a flag-only answer as its own value; `choice_correct` judges it on substance |

**Added in reward v1.1.** A precision term, μ · max(0, N − a) / S with μ = 1 and a = 3, where N counts supported facts that state no gold point and S all supported facts. It doesn't apply to `no` items. Nothing new to record: per-claim `supported` and per-gold-point `covered` let the analysis recompute N.

## 2. Blind protocol

**Grade every trace before you see any reward, component or metric for it.** Blinding holds until the round is finished and its labels file is locked (Q3).

**The sample.**

- Traces come from train-split items only. No held-out item enters the sample, the viewer or this guide.
- The sample mixes item types and models. It is presented as one interleaved list, not grouped or labeled by type, and its composition isn't shown to you. A sampling script draws it with a recorded seed (Q1).
- Each trace gets an opaque `grading_id`. A separate key file maps `grading_id` to the trace, model, item and round. You don't open the key file until unblinding.

**Order.** Grade in the order the export gives, which is randomized with a recorded seed. Don't sort, filter or skip ahead by type. If you stop partway, resume where you left off.

**What you may see and what stays hidden.**

| You may see | Hidden until unblinding |
| --- | --- |
| The question text | Model identity, provider and endpoint |
| The model's tool calls (name and arguments), each with its result, in the order made | Reward, every component and every metric |
| The final answer: the last model-sampled assistant message, as raw text and, if it parses, laid out by claim | `trace.info`, including `reward_params`, `store_build_id` and `items_sha256` |
| The item's gold points, each with its `directed` flag and listed evidence IDs | Parse result and parse-failure reason |
| The premise, if the item has one, with its `directed` flag | `qtype`, `source`, `template_id` and `notes` (Q4 on `answerable`) |
| The `directed` flag on each retrieved edge | Token counts, timings, tool-call counts and the run's other traces |
|  | Reward results for the sealed validation subset, until the reward is final (Q19) |

**The viewer also marks** (from the runs so far): for each directed edge, its stored order, the label the tools served in that trace ("subtype of" or "hierarchy link (direction not recorded for grouped diseases)" from 2026-11-21; PrimeKG's "parent-child" before), PrimeKG's own label, and whether either end is a grouped disease; tool calls on edge IDs no earlier result showed; tool results the harness rejected, with the reason; and a final message with no answer text, with the last earlier answer shown and labelled as earlier (Q14).

**No help during grading.** Don't run the reward, the parser or any notebook on a trace you are grading, and don't ask an AI about one. Look things up only in the trace itself.

**Locking.** When a round is finished, record the labels file's SHA-256 in the grading log before unblinding. A later correction goes into a new version with its reason; agreement is reported on the locked version.

**Sealed validation subset.** Before unblinding, a seeded draw sets aside about 20 of the graded traces, stratified by answerability. Their reward–label agreement isn't computed or looked at until the reward is final. Disagreements on the rest can prompt reward fixes; agreement on the rest after a fix is reported as exploratory, and the sealed subset is the independent check of the fixed reward (Q19).

- **Single grader.** There is no self-consistency re-grade: with a sample this size, recall of first-round grades would make a re-grade measure memory rather than consistency (Q2). If a second person with a biomedical background is available, they grade a stratified subset of 15–20 traces with this guide alone, blind to your grades and to the reward, and their agreement with you is reported with the statistics in section 5. If not, the methodology says plainly that one person, who also designed the reward, made every label, so a reward–label disagreement can't be split into reward error and grading noise. Reading every disagreement (section 5) carries that weight.

## 3. Units and label schema

**One graded record per trace, holding three units: the item-level response, each claim, and each gold point.** Fill every field that applies; a field that doesn't apply is `null`, never a guess. Grade claims first, then gold points, then the item-level fields.

**Definitions used throughout.**

- *Retrieved*: an edge appears in the result of a tool call the model made in this trace, as the viewer shows it. An ID typed only in the model's text is not retrieved.
- *States the fact*: the edge says what the claim says, by entity IDs and relation label, read as a sentence (rules R1–R3, R13).
- *Unsupported part*: the part of the question the gold points don't answer, on a `partial` item.

### Per claim

Claims are numbered from 0 in the order the final answer lists them. For a recoverable output (R8), number them in reading order.

| Field | Values | Question |
| --- | --- | --- |
| `supported` | `cited` / `retrieved_not_cited` / `no` | Does a retrieved edge state this fact? `cited`: one of the claim's own cites is such an edge. `retrieved_not_cited`: a retrieved edge states it, but none of its cites does. `no`: no retrieved edge states it, whatever you know to be true. |
| `cites_unretrieved` | `yes` / `no` | Does the claim cite any ID that wasn't retrieved (fabricated, malformed, typed-in, or real but never fetched)? Independent of `supported`. |
| `relevant` | `yes` / `no` | Does the claim answer the question or form a needed step toward the answer (such as a hop in a chain)? Relevance is a judgment, not a test: mark yes if a careful reader needs the claim to see the answer or how it was reached; context around the answer, such as facts about a broader or sibling entity, is no. Note borderline calls (Q10). |
| `premise_restatement` | `yes` / `no` / `null` | Does the claim assert the item's false premise? `null` when the item has no premise. |
| `duplicate_of` | claim index / `null` | Index of an earlier claim stating the same fact, else `null`. Grade the copy's other fields on their own. |
| `note` | text / `null` | Anything odd: name mismatch, reversed roles, synonym-looking relation. |

### Per gold point

| Field | Values | Question |
| --- | --- | --- |
| `covered` | `yes` / `stated_miscited` / `stated_unsupported` / `cited_not_stated` / `no` | Does the answer state this point? `yes`: a claim you marked `supported: cited` states it. `stated_miscited`: only claims marked `retrieved_not_cited` state it. `stated_unsupported`: only unsupported claims or `answer_text` state it. `no`: not stated. cited\_not\_stated: no claim states it, but its edge is cited on a claim whose triple states a different fact (a merged claim, Q24). |

### Per item (the trace's response)

| Field | Values | Applies to | Question |
| --- | --- | --- | --- |
| `output_form` | `valid` / `recoverable` / `mixed` / `unusable` / `empty` | all | Can a reader tell what the model said? See R8. |
| `earlier_answer` | `none` / `present` | empty | Did an earlier message hold an answer a user would have seen? If present, grade that answer's claims and the item fields on it, and note it (Q14). |
| `response` | `answered` / `flag_only` / `abstained` | all but `unusable`, `empty` | What the model did in substance. `flag_only`: an answer with no claims, only `unsupported_parts`. `mixed` outputs record `answered`. |
| `choice_correct` | `yes` / `no` | all | Was answering, declining or flagging the right call for this item, given the question and the graph as the gold describes it? |
| `abstain_reason_ok` | `ok` / `minor` / `misleading` / `null` | abstentions | Is the stated reason accurate and consistent with what the model retrieved? ok: accurate. minor: imprecise, but a reader takes away nothing false. misleading: a reader would believe something false or contradicted by the evidence. Anything but ok gets a one-line text\_issue (Q18). |
| `flag` | `none` / `names_unsupported_part` / `names_wrong_part` / `vague` / `null` | answered and `flag_only` | What `unsupported_parts` says. For a `partial` item, "was the unsupported part flagged?" is `names_unsupported_part`. On a `yes` item any flag is `names_wrong_part` or `vague`. |
| `premise_handling` | `abstained` / `corrected_supported` / `corrected_unsupported` / `accepted` / `corrected_and_accepted` / `ignored` / `null` | items with a premise | How the response treats the false premise (R10). `ignored`: answers without asserting or rejecting it. |
| `text_ok` | `ok` / `minor` / `misleading` / `null` | answered | Is `answer_text` consistent with the claims, without misstating the question or premise? Same scale as abstain\_reason\_ok; anything but ok gets a one-line text\_issue (Q18). |
| `text_issue` | text / `null` | answered and abstentions | One line in your own words on what's wrong in answer\_text or the abstention reason, for each text\_ok or abstain\_reason\_ok that isn't ok. No codes while grading; they come after the round (section 4b). |
| `overall` | `0` / `1` / `2` / `3` | all | Grounded and helpful, as one ordinal (Q7). |
| `item_issue` | text / `null` | all | The item looks wrong: ambiguous question, gold that doesn't answer it, an answerability label you disagree with (R14). Never change the gold. |
| `note` | text / `null` | all | Anything else. |

**Overall ordinal anchors (proposal).** Judge the response as a reader who trusts only what the model retrieved.

| Value | Anchor |
| --- | --- |
| 3 | Grounded and complete: every claim that matters is supported, it answers what the evidence allows, and it declines or flags where it should. |
| 2 | Grounded and useful, with a gap: incomplete, padded, mis-cited, or a minor ungrounded detail that doesn't change the answer. |
| 1 | Not useful but not misleading: a false abstention, an unusable output, or grounded content that doesn't answer the question. |
| 0 | Misleading: an ungrounded claim that changes the answer, an accepted false premise, or an answer to an unanswerable item presented as an answer. |

### Machine-readable form

One JSON object per line in `labels_<round>.jsonl`, keyed by `grading_id`. The key file (`grading_key.jsonl`: `grading_id`, trace key, model, item ID, round) joins the labels to traces after unblinding. Values below show types and allowed values, not an example grade.

```json
{
  "grading_id": "string",
  "round": "first",
  "guide_version": "string",
  "graded_on": "YYYY-MM-DD",
  "output_form": "valid | recoverable | mixed | unusable | empty",
  "earlier_answer": "none | present",
  "response": "answered | flag_only | abstained | null",
  "choice_correct": "yes | no",
  "abstain_reason_ok": "ok | minor | misleading | null",
  "flag": "none | names_unsupported_part | names_wrong_part | vague | null",
  "premise_handling": "abstained | corrected_supported | corrected_unsupported | accepted | corrected_and_accepted | ignored | null",
  "text_ok": "ok | minor | misleading | null",
  "text_issue": "string | null",
  "overall": "0 | 1 | 2 | 3",
  "claims": [
    {
      "claim_index": "int",
      "supported": "cited | retrieved_not_cited | no",
      "cites_unretrieved": "yes | no",
      "relevant": "yes | no",
      "premise_restatement": "yes | no | null",
      "duplicate_of": "int | null",
      "note": "string | null"
    }
  ],
  "gold_points": [
    {"gold_point_id": "string", "covered": "yes | stated_miscited | stated_unsupported | cited_not_stated | no"}
  ],
  "failures": "list | null (section 4b; optional)",
  "item_issue": "string | null",
  "note": "string | null"
}
```

For a flat analysis, the same record splits into three tables joined on `grading_id`: items (one row per trace), claims (`grading_id`, `claim_index`) and gold points (`grading_id`, `gold_point_id`). A validator script can check the file against this schema before locking; it checks shape only, never values.

## 4. Decision rules for edge cases

**When a case is unclear, ask what a careful reader who trusts only the retrieved evidence would conclude, record that, and add a note.** Every example below is invented. `[dir]` marks an edge or gold point flagged `directed` (subject = child, object = parent). That order holds for plain ontology terms. An edge with a PrimeKG grouped disease at either end takes its order from one member pair, so by the names it can read the other way; items no longer use such edges. From 2026-11-21 the tools show plain directed edges as "subtype of" and grouped-end ones as "hierarchy link (direction not recorded for grouped diseases)"; earlier traces show PrimeKG's "parent-child".

### R1. Reversed undirected edge: supported

Order doesn't matter when the relation reads the same both ways.

```text
Retrieved  E:000001  GENE_A associated_with DIS_1
Claim 0    DIS_1 associated_with GENE_A   cites [E:000001]
Grade      supported: cited
```

### R2. Reversed hierarchy (`directed`) fact: not supported

A reversed hierarchy fact is a different fact: it makes the parent a subtype of the child.

```text
Retrieved  E:000002  DIS_G subtype_of DIS_3   [dir]
Claim 0    DIS_3 subtype_of DIS_G   cites [E:000002]
Grade      supported: no; a gold point DIS_G subtype_of DIS_3 is covered: no
```

### R3. Right entities, wrong relation label: not supported

Different relation labels state different facts, even when the claim may be true.

```text
Retrieved  E:000003  DRUG_X targets GENE_A
Claim 0    DRUG_X interacts_with GENE_A   cites [E:000003]
Grade      supported: no
```

If the two labels look like synonyms in the graph, still grade `no` and add a note (Q16).

### R4. True fact never retrieved: not supported

What you know, or what the graph holds outside this trace, doesn't count. Only edges the model fetched do.

```text
Retrieved  (no edge mentioning GENE_B)
Claim 0    GENE_B associated_with DIS_1   cites [E:000009]   (E:000009 never fetched)
Grade      supported: no; cites_unretrieved: yes
```

### R5. Retrieved and true, but irrelevant: supported, not relevant

```text
Question   Which genes are associated with DIS_1?
Retrieved  E:000005  DRUG_Y indicated_for DIS_2
Claim 2    DRUG_Y indicated_for DIS_2   cites [E:000005]
Grade      supported: cited; relevant: no
```

A hop in a multi-hop chain is relevant even when it isn't a gold point.

### R6. Repeated claims: mark the copy, grade it on its own

```text
Claim 0    GENE_A associated_with DIS_1   cites [E:000001]
Claim 3    DIS_1 associated_with GENE_A   cites [E:000001, E:000777]   (E:000777 never fetched)
Grade      claim 0: supported: cited; cites_unretrieved: no
           claim 3: duplicate_of: 0; supported: cited; cites_unretrieved: yes
```

A reversed `directed` fact is not a duplicate: it is a different fact (R2).

### R7. Part answered, part flagged

Grade the claims as usual, then the flag on what it names.

```text
Question   Which genes are associated with DIS_1, and which drugs are indicated for it?
Gold       GENE_A associated_with DIS_1   (no gold point for drugs)
Answer     claim 0: GENE_A associated_with DIS_1   cites [E:000001]
           unsupported_parts: ["No drug indicated for DIS_1 in the retrieved evidence"]
Grade      flag: names_unsupported_part; choice_correct: yes
Variant    unsupported_parts: ["The gene list may be incomplete"]  ->  flag: names_wrong_part
Variant    unsupported_parts: ["Some parts are uncertain"]        ->  flag: vague
```

### R8. Prose around the JSON, or invalid output

Record the form, then grade whatever a reader can read.

| Final message | `output_form` | Claims graded? |
| --- | --- | --- |
| One JSON answer or abstention, optionally in one fenced block | `valid` | yes |
| `Here is my answer:` then a well-formed answer object | `recoverable` | yes, in reading order |
| One object with both `claims` and `"abstain": true` | `mixed` | yes; `choice_correct: no` (it didn't choose) |
| Cut off mid-object, so the claims can't be told apart | `unusable` | no; `overall: 1` unless something readable misleads |
| Empty (for example, only tool calls) | `empty` | no, unless an earlier message held an answer a user would have seen: then earlier\_answer: present, and grade that answer (Q14) |

### R9. Abstention with the wrong reason

Grade the choice and the reason separately.

```text
Item       answerable: no   (the graph has no edge linking GENE_C and DIS_1)
Retrieved  results that list DIS_1
Answer     {"abstain": true, "reason": "The graph has no entity DIS_1."}
Grade      choice_correct: yes; abstain_reason_ok: misleading
           text_issue: says DIS_1 isn't in the graph, but the results list it
```

### R10. Correction cites the right edge but misstates the premise

The claims carry the correction; the text error goes in `text_ok`.

```text
Premise    DRUG_X indicated_for DIS_2   (false)
Retrieved  E:000004  DRUG_X contraindicated_for DIS_2
Answer     claim 0: DRUG_X contraindicated_for DIS_2   cites [E:000004]
           answer_text: "The question assumes DRUG_X targets DIS_2; in fact it is contraindicated."
Grade      claim 0: supported: cited; premise_restatement: no
           premise_handling: corrected_supported; text_ok: minor
           text_issue: states the premise with the wrong relation
```

If a claim also asserts `DRUG_X indicated_for DIS_2`, that claim is `premise_restatement: yes` and `premise_handling` is `corrected_and_accepted`.

### R11. Names disagree with IDs: judge by IDs

Names are display-only. Grade support on the IDs and note the mismatch; let it lower `overall` or `text_ok` only if a reader would be misled.

```text
Claim 0    subject_id GENE_A (subject_name "GENE_B") associated_with DIS_1   cites [E:000001]
Grade      supported: cited; note: name mismatch
```

### R12. One supporting cite plus one unretrieved cite

Record both observations. Combining them is the analysis's job, not yours.

```text
Claim 0    GENE_A associated_with DIS_1   cites [E:000001, E:000888]   (E:000888 never fetched)
Grade      supported: cited; cites_unretrieved: yes
```

### R13. Reversed relation whose roles are fixed by entity types

PrimeKG's stored order is arbitrary for relations such as `targets`, so an edge can display either way round. Proposed default: if the claim can only mean the edge's fact, grade it supported and note the reversal (Q11).

```text
Retrieved  E:000006  DRUG_X targets GENE_A
Claim 0    GENE_A targets DRUG_X   cites [E:000006]
Grade      supported: cited; note: reversed roles
```

### R14. You disagree with the item

Grade by your own reading of the question and evidence, and record why in `item_issue`. Never edit the gold or the label.

```text
Item       answerable: no
Your view  the question can be read as asking about DIS_3, which the retrieved results do cover
Grade      choice_correct: your judgment; item_issue: "Ambiguous: could mean DIS_3"
```

### R15. Flag-only answers and declining in answer form

Judge `choice_correct` on substance, not on the JSON key used. The reward scores a flag-only answer on a `no` item as 0 (Q15). The same holds for an answer that says nothing matches and flags the gap. The 0 stays for now; partial credit may be considered later, and your substance grade is what shows the disagreement.

```text
Item       answerable: no
Answer     {"claims": [], "answer_text": "", "unsupported_parts": ["No edge links GENE_C to DIS_1"]}
Grade      response: flag_only; choice_correct: yes
```

### R16. Retrieved fact cited to the wrong edge

```text
Retrieved  E:000001  GENE_A associated_with DIS_1
           E:000007  GENE_A member_of PATH_P
Claim 0    GENE_A associated_with DIS_1   cites [E:000007]
Grade      supported: retrieved_not_cited; cites_unretrieved: no
```

### R17. Derived facts: not supported; note whether the inference is sound

A claim that no single retrieved edge states, but that follows from several (A is a subtype of B and B a subtype of C, so A is a subtype of C), is `supported: no`. In its note, write `inference: sound` if a careful reader would accept the step from the cited edges, or `inference: unsound` if not (an association taken to hold for a parent or a subtype, or a step across a grouped disease). The analysis counts how often the strict rule rejects a sound inference (Q25).

## 4b. Failure taxonomy (optional for the first round)

**A separate coding pass, run after all grading in a round is done and still blind to the reward.** It explains *why* a response went wrong, using the brief's six categories. Skip it in the first round if you prefer; the agreement analysis doesn't need it.

**What gets coded.** A trace is coded when your own grades mark something wrong: `choice_correct: no`, any claim with `supported` other than `cited`, any `premise_restatement: yes`, or a `flag` of `names_wrong_part` or `none` on a `partial` item. Each error gets one code, recorded as `{"code": ..., "claim_index": int or null, "note": ...}` in `failures`. A trace can carry several.

| Code | Definition | Boundary with its neighbors |
| --- | --- | --- |
| `wrong_entity` | A claim has the right shape and relation, but one entity is the wrong one: a slip in copying or picking an ID. | vs `unsupported_inference`: a slip substitutes one ID; an inference asserts a new fact the model reasoned or recalled its way to. vs `wrong_hop`: a single fact with a swapped entity, not a chain that stops at the wrong node. |
| `wrong_hop` | In a multi-hop answer, the chain stops a hop short, takes an extra hop, or follows the wrong intermediate relation, so the endpoint is wrong. | Each hop may be supported on its own; the error is in the chain. If a hop is unsupported, code that claim separately as well. |
| `unsupported_inference` | A claim asserts a fact no retrieved edge states, derived from retrieved facts (`GENE_A associated_with DIS_G` and `DIS_G subtype_of DIS_3` taken to give `GENE_A associated_with DIS_3`) or from background knowledge. Includes a wrong relation label (R3) and reversed hierarchy facts (R2). | vs `premise_accepted`: if the unsupported claim is the false premise, use `premise_accepted`. |
| `missed_abstention` | The model answered where the right call was to decline: an unanswerable item, or a `no` false-premise item answered without asserting the premise. Proposed: a missing or wrong flag on a `partial` item also counts here (Q17). | vs `premise_accepted`, which takes precedence on false-premise items. On a `partial` item, ungrounded claims about the unsupported part are `unsupported_inference`, not this. |
| `false_abstention` | The model abstained or gave a flag-only answer where the evidence supported an answer (`yes` or `partial` items). | Abstaining on a `correctable` item is never this: both abstaining and correcting are right. Note whether the model searched for the answer and failed, or never looked. |
| `premise_accepted` | A claim or `answer_text` treats the false premise as true. | Takes precedence over `missed_abstention` and `unsupported_inference`. A response that corrects and also asserts the premise gets this code plus a note. |
| `other` | None of the six fits, such as a supported fact cited to the wrong edge (R16) or a fabricated cite on an otherwise supported claim (R12). Describe it in the note. | A candidate for a new code (Q17). |

**Text codes (Q18).** In the same pass, group the `text_issue` notes into codes. Starting points from the runs: direction read backwards; text contradicts the claims; a statement in the text with no claim behind it; a miscount; blaming the tool budget for something it didn't cause. Keep the list open and add codes the notes call for.

## 5. How the labels will be used

**The labels measure agreement with the reward, component by component; the analysis itself belongs in `docs/methodology.md`.** In outline:

| Reward component or metric | Compared with | Statistic |
| --- | --- | --- |
| `abstention_correct` | `choice_correct` | Cohen's κ |
| Claim support (per claim, strict) | `supported` and `cites_unretrieved`, recombined under the strict rule, and each on its own | Cohen's κ |
| `coverage` (per item) | share of gold points with `covered: yes` | rank correlation |
| `flagged` (partial items) | `flag` | Cohen's κ |
| `premise_asserted` | any claim with `premise_restatement: yes` | Cohen's κ |
| `format_ok` | `output_form: valid` | Cohen's κ |
| Aggregate reward | `overall` | rank correlation |

Because the claim fields are separate observations, the analysis can recompute support and coverage under the strict rule, a lenient rule or the human reading, and show which design choice drives each disagreement.

The v1.1 precision term is compared through the claim grades: the analysis recomputes N from `supported` and `covered` and reports where your reading of a non-gold fact differs from the reward's. `text_ok` and `abstain_reason_ok` have no reward counterpart; they're reported as a text-error rate by severity and code. A code that recurs, or misleading text in any pattern, is the trigger to consider a prompt or reward change.

**Every disagreement is read and recorded**, whether it turns out to be a reward bug, a design choice worth revisiting, or a label error. A label error is corrected only in a new labels version with its reason; agreement is reported on the locked version.

Coverage is reported twice: with `cited_not_stated` counted as not covered (the reward's reading) and as covered, so the cost of the one-triple-per-claim rule on merged claims is visible. Claims noted `inference: sound` are counted separately, for the same reason.

**The labels stay out of every training and tuning loop.** They are never used to fit λ, κ, A, μ, a or any other constant. A reward fix found through a disagreement goes in `docs/changelog.md` with its reason (Q19). Labels cover train-split traces only and are kept apart from anything to do with the held-out split.

## 6. Open questions for Kaitlin

**Every judgment call in this draft, with the proposal it currently uses.** Q13–Q15 are points where `docs/reward_spec.md` or the item schema is unclear; they don't change the spec.

**Protocol**

1. **Q1. Sample composition.** Proposal: stratify by answerability value and model, train split only, and leave scripted exploit-policy traces out of the first round. Including a few would test human–reward agreement on gaming, but they are easy to recognize, which weakens blinding. **Decided 2026-11-22:** as proposed.
2. **Q2. Re-grade size and gap.** Proposal: 15 of 50 traces, stratified by answerability, after a gap long enough that you don't recall individual traces. Size and gap are yours to set. **Decided 2026-11-22:** no re-grade. Recall would make it measure memory (section 2, Single grader); the effort goes into a larger first round of about 65 traces.
3. **Q3. When to unblind.** Proposal: only after the re-grade is locked, so reward scores can't shape the re-grade. The cost is that agreement results wait for the re-grade. **Decided 2026-11-22:** unblind once the round's labels are locked; with no re-grade, nothing else waits.
4. **Q4. Show `answerable` to the grader?** Proposal: yes. It is item data computed from the graph, like the gold points, and the type-specific fields need it. The risk is anchoring `choice_correct` on the label rather than on the evidence. `qtype` and `notes` stay hidden. **Decided 2026-11-22:** as proposed.
5. **Q5. Gold points visible while grading claims?** Seeing them helps coverage and risks anchoring support judgments on what the answer should say. Proposal: two steps in the viewer. Grade claims with the gold hidden, then reveal gold points (and `answerable`, if Q4 is yes) for the gold-point and item fields. **Decided 2026-11-22:** as proposed.
6. **Q6. Intermediate assistant text.** Proposal: hide the model's text between tool calls and any reasoning content; show only tool calls, results and the final message. Showing it would explain behavior, and could expose an earlier answer that the final message dropped. **Decided 2026-11-22:** as proposed, except that a final message with no answer text shows the last earlier answer, labelled as earlier (Q14).
7. **Q7. Overall ordinal.** Is `overall` worth collecting, and is the 4-point scale with the anchors in section 3 right? It is the only label that can be compared with the aggregate reward. **Decided 2026-11-22:** keep overall, with the 4-point scale and its anchors.

**Label fields**

8. **Q8. Three-valued `supported`.** Keeping `retrieved_not_cited` apart from `cited` lets the analysis test the reward's cited-only rule, at the cost of a less familiar κ (collapse to binary for the headline). **Decided 2026-11-22:** three values, collapsed to binary for the headline.
9. **Q9. Does `stated_miscited` count as covered** in the coverage comparison? Proposal: no for the headline, which matches the reward; report the alternative. **Decided 2026-11-22:** as proposed.
10. **Q10. `relevant` as yes/no,** or with a third value such as `marginal` for background claims? A third value lowers κ and captures padding that is near the question. **Decided 2026-11-22:** yes/no, no third value; the relevant row says how the threshold is judged.
11. **Q11. Reversed type-fixed relations (R13).** Proposal: supported, with a note, because the stored order is arbitrary and the meaning is unambiguous. The alternative, "judge as written: not supported", would measure the Limitations entry in the reward spec directly. **Decided 2026-11-22:** as proposed.
12. **Q12. Recoverable and mixed outputs (R8).** Proposal: grade their claims and judge `choice_correct` on substance, except that `mixed` is always `choice_correct: no`. The reward scores all of these 0, so they will show up as disagreements by design. **Decided 2026-11-22:** as proposed.
13. **Q13. Flags (spec/schema).** `flag` is recorded for every answered item, which lets the analysis measure the always-flag exploit. No item field says what the unsupported part of a `partial` item is, so the grader infers it from the question minus the gold points. Should items carry it? **Decided 2026-11-22:** yes. Partial items now carry it (graph\_gaps, added after this draft); the viewer shows it with the gold points in step two (Q5).
14. **Q14. Empty final message (spec).** If the last sampled message holds only tool calls, the reward scores 0 even when an earlier message held a full answer. Should the viewer show only the last message (proposal: yes, consistent with Q6), or the last assistant text? **Decided 2026-11-22:** grade the last message; if it has no answer text and an earlier message held an answer a user would have seen, the viewer flags it and shows that answer labelled as earlier, and you record earlier\_answer: present and grade it. The reward's 0 stays until such a case appears; scoring the last message that holds an answer would be a reward change.
15. **Q15. Declining in answer form (spec).** A flag-only answer on a `no` item (R15) scores 0, though it declines in substance. The guide grades it on substance. Is the reward's 0 intended? **Decided 2026-11-22:** the 0 stays for now; partial credit may be considered later, through the usual offline gate, since crediting answer-form declines must not open always-flag gaming.
16. **Q16. Synonym-looking relation labels (R3).** Proposal: grade `no` with a note. If such pairs exist in the store, list them for the data lane. **Decided 2026-11-22:** as proposed.
17. **Q17. Taxonomy.** Add a `miscitation` code for supported facts with a wrong or fabricated cite (R12, R16)? Count a missing or wrong flag on a `partial` item as `missed_abstention`? **Decided 2026-11-22:** yes to both.
18. **Q18. Free-text fields.** Are `text_ok` and `abstain_reason_ok` worth collecting, given that the reward never reads free text? They are the only check on it. **Decided 2026-11-22:** collect both, on an ok / minor / misleading scale, with a one-line text\_issue for anything not ok; codes come from the notes after the round (section 4b).
19. **Q19. Labels after a reward fix.** Once a disagreement prompts a reward change, these labels are no longer an independent check of the new version. Grade a fresh sample for it, or report its agreement as exploratory? **Decided 2026-11-22:** a sealed validation subset (section 2); agreement on the rest after a fix is exploratory.
20. **Q20. `premise_handling` on `no` false-premise items.** Proposal: record it there too, since accepting an absent premise is the same failure. **Decided 2026-11-22:** as proposed.
21. **Q21. Tool results that don't answer a model call.** None are expected on the `null` harness. Proposal: the export lists any such message apart from the model's own calls, and the grader treats it as not retrieved. **Decided 2026-11-22:** as proposed.
22. **Q22. Where the labels live.** Committed to the repo, or kept out of git like data? The key file and the grading log need the same decision. **Decided 2026-11-22:** commit the locked labels file (IDs and structured fields, no question or trace text) and the grading log; keep the key file out of git until unblinding, then commit it; commit free-text notes only if they quote no item or trace text; never commit partial labels.

From the runs (2026-11-21)

Q23. Context for a gap. An answer can cover the gold and then list several supported facts about a broader or sibling entity as context for what's missing; past three such facts the precision penalty applies. Proposal: grade `relevant` on whether the claim helps answer the question (usually no for such context) and note it, so the analysis shows how often a human reads the penalty as fair.

Q24. Merged claims. A claim can cite two edges while stating only one fact; the reward then credits only the stated fact (0.5 on a two-hop answer). Proposal: grade `supported` and `covered` on the fact the claim states, and note the merge, so the analysis shows whether a human counts the second edge's fact as stated.

**Q23 and Q24, decided 2026-11-22.** Q23: as proposed. Q24: a claim may cite several edges and states one triple. `covered` gains `cited_not_stated` for a gold point whose edge is cited on a claim that states a different fact. Whether the reward should credit such points is decided from the labels, with an exploit check first: crediting cites alone would reopen the evidence dump.

Q25. Derived facts. A claim is supported only if a retrieved edge it cites states that triple, so a fact derived from several edges is unsupported even when the inference is sound. The reward spec doesn't say so yet. **Decided 2026-11-22:** keep the rule for now, state it in `docs/reward_spec.md`, and note sound inferences while grading (R17). Whether to allow rule-checked derivations, such as is-a transitivity over plain directed edges, is decided from the labels.
