"""The label schema from ``docs/labeling_guide.md`` section 3, in code.

One graded record per trace, as the guide's "Machine-readable form" gives it. This
module holds the allowed values, the rules for which fields apply to a trace (the
guide's "Applies to" column), and a validator that checks a record's **shape and
allowed values only**. It never judges whether a grade is right, and nothing here
suggests or fills in a value: the viewer shows every field empty, and a field the
grader hasn't set is an error, not a default.

Applicability (guide section 3 and R8):

- ``earlier_answer`` applies when ``output_form`` is ``empty``.
- Claims are graded, and ``response`` applies, when the output is ``valid``,
  ``recoverable`` or ``mixed``, or ``empty`` with ``earlier_answer: present`` (the
  earlier answer is graded instead). Otherwise ``claims`` is ``[]`` and
  ``response`` is ``null``.
- ``abstain_reason_ok`` applies to abstentions; ``flag`` to ``answered`` and
  ``flag_only``; ``text_ok`` to ``answered``.
- ``text_issue`` is required (one line) when ``text_ok`` or ``abstain_reason_ok`` is
  ``minor`` or ``misleading``, and ``null`` otherwise.
- ``premise_handling`` and each claim's ``premise_restatement`` apply when the item
  has a premise. The validator can check this only when it is given the export
  record, which says whether the item has one.
- ``choice_correct`` and ``overall`` apply to every trace. ``item_issue`` and
  ``note`` are optional free text everywhere.

A field that doesn't apply must be ``null``, as the guide says ("never a guess").
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from grading_common import ROUND

OUTPUT_FORMS = ("valid", "recoverable", "mixed", "unusable", "empty")
EARLIER_ANSWER = ("none", "present")
RESPONSES = ("answered", "flag_only", "abstained")
YES_NO = ("yes", "no")
TEXT_SCALE = ("ok", "minor", "misleading")
FLAGS = ("none", "names_unsupported_part", "names_wrong_part", "vague")
PREMISE_HANDLING = (
    "abstained",
    "corrected_supported",
    "corrected_unsupported",
    "accepted",
    "corrected_and_accepted",
    "ignored",
)
OVERALL = (0, 1, 2, 3)
SUPPORTED = ("cited", "retrieved_not_cited", "no")
COVERED = ("yes", "stated_miscited", "stated_unsupported", "cited_not_stated", "no")

RECORD_KEYS = (
    "grading_id",
    "round",
    "guide_version",
    "graded_on",
    "output_form",
    "earlier_answer",
    "response",
    "choice_correct",
    "abstain_reason_ok",
    "flag",
    "premise_handling",
    "text_ok",
    "text_issue",
    "overall",
    "claims",
    "gold_points",
    "failures",
    "item_issue",
    "note",
)
"""Every key of a label record, in the guide's order. ``failures`` (section 4b) is
optional for the first round and may be ``null``."""

CLAIM_KEYS = (
    "claim_index",
    "supported",
    "cites_unretrieved",
    "relevant",
    "premise_restatement",
    "duplicate_of",
    "note",
)
GOLD_KEYS = ("gold_point_id", "covered")
FAILURE_KEYS = ("code", "claim_index", "note")

FREE_TEXT_KEYS = ("text_issue", "item_issue", "note")
"""Record-level free-text fields. With each claim's ``note`` and each failure's
``note``, these are what lock.py splits off the committed labels."""

GRADED_FORMS = ("valid", "recoverable", "mixed")
NOT_OK = ("minor", "misleading")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Applies:
    """Which fields apply to one trace, given the grader's answers so far."""

    earlier_answer: bool
    claims: bool
    response: bool
    abstain_reason_ok: bool
    flag: bool
    text_ok: bool
    text_issue: bool
    premise_handling: bool
    premise_restatement: bool


def applies(
    output_form: str | None,
    earlier_answer: str | None = None,
    response: str | None = None,
    has_premise: bool = False,
    text_ok: str | None = None,
    abstain_reason_ok: str | None = None,
) -> Applies:
    """The guide's "Applies to" rules for one trace (see the module docstring).

    Unset answers (``None``) make the fields that depend on them not apply yet; the
    viewer re-evaluates as the grader answers.
    """
    graded = output_form in GRADED_FORMS or (
        output_form == "empty" and earlier_answer == "present"
    )
    return Applies(
        earlier_answer=output_form == "empty",
        claims=graded,
        response=graded,
        abstain_reason_ok=graded and response == "abstained",
        flag=graded and response in ("answered", "flag_only"),
        text_ok=graded and response == "answered",
        text_issue=(graded and response == "answered" and text_ok in NOT_OK)
        or (graded and response == "abstained" and abstain_reason_ok in NOT_OK),
        premise_handling=has_premise,
        premise_restatement=has_premise,
    )


@dataclass(frozen=True)
class ExportContext:
    """What the validator needs from a trace's export record: whether the item has a
    premise, and its gold-point IDs in order. Read from step 2, never shown here."""

    has_premise: bool
    gold_point_ids: tuple[str, ...]

    @classmethod
    def from_export(cls, record: Mapping[str, Any]) -> ExportContext:
        step2 = record["step2"]
        return cls(
            has_premise=step2.get("premise") is not None,
            gold_point_ids=tuple(gp["id"] for gp in step2.get("gold_points") or []),
        )


def _is_text(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _check_enum(errors: list[str], where: str, value: Any, allowed: Sequence[Any],
                *, required: bool) -> None:
    """Required: value must be one of ``allowed``. Not required: value must be None."""
    if required:
        if value is None:
            errors.append(f"{where}: missing (must be one of {list(allowed)})")
        elif isinstance(value, bool) or value not in allowed:
            errors.append(f"{where}: {value!r} is not one of {list(allowed)}")
    elif value is not None:
        errors.append(f"{where}: must be null here (the field doesn't apply)")


def _check_optional_text(errors: list[str], where: str, value: Any) -> None:
    if value is not None and not _is_text(value):
        errors.append(f"{where}: must be null or non-blank text")


def validate_record(rec: Any, context: ExportContext | None = None) -> list[str]:
    """Errors in one label record: shape and allowed values only.

    Args:
        rec: the parsed record.
        context: the trace's export context. Without it the premise and gold-point
            checks are limited to shape.

    Returns:
        Human-readable errors, each prefixed with the field path. Empty if valid.
    """
    if not isinstance(rec, dict):
        return ["record: not a JSON object"]
    errors: list[str] = []
    gid = rec.get("grading_id")
    keys = set(rec)
    missing = [k for k in RECORD_KEYS if k not in keys and k != "failures"]
    extra = sorted(keys - set(RECORD_KEYS))
    if missing:
        errors.append(f"record: missing keys {missing}")
    if extra:
        errors.append(f"record: unknown keys {extra}")
    if not _is_text(gid):
        errors.append("grading_id: must be non-blank text")
    if rec.get("round") != ROUND:
        errors.append(f"round: must be {ROUND!r}")
    if not _is_text(rec.get("guide_version")):
        errors.append("guide_version: must be non-blank text")
    graded_on = rec.get("graded_on")
    if not (isinstance(graded_on, str) and _DATE_RE.match(graded_on)):
        errors.append("graded_on: must be a YYYY-MM-DD date")

    has_premise = context.has_premise if context is not None else None
    form = rec.get("output_form")
    _check_enum(errors, "output_form", form, OUTPUT_FORMS, required=True)
    a = applies(
        form,
        rec.get("earlier_answer"),
        rec.get("response"),
        bool(has_premise),
        rec.get("text_ok"),
        rec.get("abstain_reason_ok"),
    )
    _check_enum(errors, "earlier_answer", rec.get("earlier_answer"), EARLIER_ANSWER,
                required=a.earlier_answer)
    _check_enum(errors, "response", rec.get("response"), RESPONSES, required=a.response)
    _check_enum(errors, "choice_correct", rec.get("choice_correct"), YES_NO, required=True)
    _check_enum(errors, "abstain_reason_ok", rec.get("abstain_reason_ok"), TEXT_SCALE,
                required=a.abstain_reason_ok)
    _check_enum(errors, "flag", rec.get("flag"), FLAGS, required=a.flag)
    _check_enum(errors, "text_ok", rec.get("text_ok"), TEXT_SCALE, required=a.text_ok)
    if a.text_issue:
        if not _is_text(rec.get("text_issue")):
            errors.append("text_issue: required (one line) when text_ok or "
                          "abstain_reason_ok isn't ok")
    elif rec.get("text_issue") is not None:
        errors.append("text_issue: must be null when text_ok and abstain_reason_ok are "
                      "ok or don't apply")
    if has_premise is None:
        if rec.get("premise_handling") is not None:
            _check_enum(errors, "premise_handling", rec.get("premise_handling"),
                        PREMISE_HANDLING, required=True)
    else:
        _check_enum(errors, "premise_handling", rec.get("premise_handling"),
                    PREMISE_HANDLING, required=a.premise_handling)
    _check_enum(errors, "overall", rec.get("overall"), OVERALL, required=True)
    _check_optional_text(errors, "item_issue", rec.get("item_issue"))
    _check_optional_text(errors, "note", rec.get("note"))

    errors += _validate_claims(rec.get("claims"), a.claims, has_premise)
    errors += _validate_gold(rec.get("gold_points"), context)
    errors += _validate_failures(rec.get("failures"))
    return errors


def _validate_claims(claims: Any, graded: bool, has_premise: bool | None) -> list[str]:
    if not isinstance(claims, list):
        return ["claims: must be a list"]
    errors: list[str] = []
    if not graded and claims:
        errors.append("claims: must be [] when the output's claims aren't graded "
                      "(unusable, or empty with no earlier answer)")
    for n, c in enumerate(claims):
        where = f"claims[{n}]"
        if not isinstance(c, dict):
            errors.append(f"{where}: not a JSON object")
            continue
        if set(c) != set(CLAIM_KEYS):
            errors.append(f"{where}: keys must be {list(CLAIM_KEYS)}")
        idx = c.get("claim_index")
        if not (isinstance(idx, int) and not isinstance(idx, bool) and idx == n):
            errors.append(f"{where}.claim_index: must be {n} (claims numbered from 0, in order)")
        _check_enum(errors, f"{where}.supported", c.get("supported"), SUPPORTED, required=True)
        _check_enum(errors, f"{where}.cites_unretrieved", c.get("cites_unretrieved"), YES_NO,
                    required=True)
        _check_enum(errors, f"{where}.relevant", c.get("relevant"), YES_NO, required=True)
        pr = c.get("premise_restatement")
        if has_premise is None:
            if pr is not None:
                _check_enum(errors, f"{where}.premise_restatement", pr, YES_NO, required=True)
        else:
            _check_enum(errors, f"{where}.premise_restatement", pr, YES_NO,
                        required=has_premise)
        dup = c.get("duplicate_of")
        if dup is not None and not (
            isinstance(dup, int) and not isinstance(dup, bool) and 0 <= dup < n
        ):
            errors.append(f"{where}.duplicate_of: must be null or an earlier claim's index")
        _check_optional_text(errors, f"{where}.note", c.get("note"))
    return errors


def _validate_gold(gold: Any, context: ExportContext | None) -> list[str]:
    if not isinstance(gold, list):
        return ["gold_points: must be a list"]
    errors: list[str] = []
    ids = []
    for n, g in enumerate(gold):
        where = f"gold_points[{n}]"
        if not isinstance(g, dict):
            errors.append(f"{where}: not a JSON object")
            continue
        if set(g) != set(GOLD_KEYS):
            errors.append(f"{where}: keys must be {list(GOLD_KEYS)}")
        if not _is_text(g.get("gold_point_id")):
            errors.append(f"{where}.gold_point_id: must be non-blank text")
        ids.append(g.get("gold_point_id"))
        _check_enum(errors, f"{where}.covered", g.get("covered"), COVERED, required=True)
    if len(set(map(str, ids))) != len(ids):
        errors.append("gold_points: a gold point is listed twice")
    if context is not None and tuple(ids) != context.gold_point_ids:
        errors.append(
            f"gold_points: must list the item's {len(context.gold_point_ids)} gold "
            "point(s), each once, in the export's order"
        )
    return errors


def _validate_failures(failures: Any) -> list[str]:
    if failures is None:
        return []
    if not isinstance(failures, list):
        return ["failures: must be null or a list"]
    errors: list[str] = []
    for n, f in enumerate(failures):
        where = f"failures[{n}]"
        if not isinstance(f, dict) or set(f) != set(FAILURE_KEYS):
            errors.append(f"{where}: must be an object with keys {list(FAILURE_KEYS)}")
            continue
        if not _is_text(f.get("code")):
            errors.append(f"{where}.code: must be non-blank text")
        ci = f.get("claim_index")
        if ci is not None and not (isinstance(ci, int) and not isinstance(ci, bool) and ci >= 0):
            errors.append(f"{where}.claim_index: must be null or a claim index")
        _check_optional_text(errors, f"{where}.note", f.get("note"))
    return errors
