"""The model's final output format and its strict parser.

The model ends an episode with one JSON object: either an answer made of structured
claims that cite evidence IDs, or an abstention. The harness prompt tells the model
this format, the reward scores what ``parse_output`` returns, and the reward-hacking
exploits try to slip past it, so all three must agree on one definition: this one.

The parser is strict on purpose. Every leniency (prose around the JSON, extra keys,
an object that both answers and abstains) is a place where the parser and the reward
could read the same text differently, and a policy under RL will find that gap. A
failed parse is a result in its own right (``ParseFailure`` with a reason), reported
as a metric, never silently dropped or repaired.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


class Claim(BaseModel):
    """One claimed fact: a graph triple plus the evidence IDs cited for it.

    Entities are referenced by graph ID and the relation by the graph's own label,
    both copied from tool results, so the support check is exact equality. The
    ``*_name`` fields are for readability only and are never scored.

    ``cites`` must be non-empty, but its entries are not format-checked here: a
    cite of a fabricated or malformed ID is a grounding failure for the reward to
    score, not a parse failure that would hide it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_id: str = Field(min_length=1)
    relation: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    subject_name: str | None = None
    object_name: str | None = None
    cites: list[str] = Field(min_length=1)


class Answer(BaseModel):
    """An answer: claims, a short prose answer, and the parts left unsupported.

    ``unsupported_parts`` is how a partially answerable question is answered: claim
    what the evidence supports, and name what it doesn't. ``claims`` may be empty only
    when ``unsupported_parts`` isn't; an answer with neither says nothing and should
    have been an abstention.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    claims: list[Claim]
    answer_text: str
    unsupported_parts: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _not_vacuous(self) -> Answer:
        if not self.claims and not self.unsupported_parts:
            raise ValueError("an answer needs claims or unsupported_parts")
        return self


class Abstain(BaseModel):
    """An abstention: the model says the evidence can't support an answer, and why."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    abstain: Literal[True]
    reason: str

    @field_validator("abstain", mode="before")
    @classmethod
    def _exactly_true(cls, v: object) -> object:
        # Literal[True] alone accepts 1 (1 == True in Python); only JSON true counts.
        if v is not True:
            raise ValueError("abstain must be the JSON literal true")
        return v


ParseFailureReason = Literal["not_json", "schema", "mixed_answer_and_abstain", "empty"]


class ParseFailure(BaseModel):
    """Why the output couldn't be read as an answer or an abstention.

    - ``empty``: no text, or only whitespace.
    - ``not_json``: not exactly one JSON object (optionally in one fenced block);
      this includes prose before or after the JSON.
    - ``schema``: valid JSON that isn't a valid ``Answer`` or ``Abstain`` (wrong
      types, missing or extra keys, empty ``cites``, duplicate keys, not an object).
    - ``mixed_answer_and_abstain``: one object holding both answer keys and
      ``abstain``. Kept separate from ``schema`` because hedging both ways is a
      likely exploit and the audit wants to count it on its own.

    ``detail`` is a human-readable explanation for debugging; don't branch on it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: ParseFailureReason
    detail: str


ParseResult = Answer | Abstain | ParseFailure

ANSWER_KEYS = frozenset({"claims", "answer_text", "unsupported_parts"})
"""Keys that mark an object as an answer attempt, for the mixed-output check."""

# One fenced block around the whole output, with an optional ``json`` tag. The body
# may not contain another fence, so two blocks never parse as one.
_FENCE_RE = re.compile(r"\A```(?:json)?[ \t]*\n(?P<body>(?:(?!```).)*?)\n?```\Z", re.S | re.I)


class _DuplicateKeyError(ValueError):
    pass


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    # Python's json keeps the last duplicate silently; other parsers keep the first.
    # A policy could exploit that disagreement, so duplicates are rejected.
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise _DuplicateKeyError(f"duplicate key {key!r}")
        out[key] = value
    return out


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-standard JSON constant {name}")


def _schema_detail(err: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}"
        for e in err.errors()
    )


def parse_output(text: str | None) -> ParseResult:
    """Parse the model's final reply into an ``Answer``, ``Abstain`` or ``ParseFailure``.

    Rules, in order:

    1. Empty or whitespace-only text is ``empty``.
    2. Surrounding whitespace is ignored. If the whole text is one fenced code block
       (optionally tagged ``json``), its body is used.
    3. The remaining text must be exactly one JSON value, with no other text, no
       duplicate keys and no ``NaN``/``Infinity``. Otherwise ``not_json`` (or
       ``schema`` for duplicate keys).
    4. It must be an object. One with ``abstain`` and any of ``ANSWER_KEYS`` is
       ``mixed_answer_and_abstain``.
    5. An object with ``abstain`` is validated as ``Abstain``; any other as
       ``Answer``. Validation is strict (no type coercion) and extra keys are
       forbidden; a failure is ``schema``.

    Never raises on bad input: every outcome is a ``ParseResult``.
    """
    if text is None or not text.strip():
        return ParseFailure(reason="empty", detail="no text")
    body = text.strip()
    fenced = _FENCE_RE.match(body)
    if fenced:
        body = fenced.group("body").strip()
        if not body:
            return ParseFailure(reason="empty", detail="empty code block")

    try:
        obj = json.loads(
            body, object_pairs_hook=_no_duplicate_keys, parse_constant=_reject_constant
        )
    except _DuplicateKeyError as e:
        return ParseFailure(reason="schema", detail=str(e))
    except ValueError as e:  # json.JSONDecodeError is a ValueError
        return ParseFailure(reason="not_json", detail=str(e))
    except RecursionError:  # deeply nested arrays blow the decoder's stack
        return ParseFailure(reason="not_json", detail="nesting too deep")

    if not isinstance(obj, dict):
        return ParseFailure(
            reason="schema", detail=f"expected a JSON object, got {type(obj).__name__}"
        )
    if "abstain" in obj and ANSWER_KEYS & obj.keys():
        return ParseFailure(
            reason="mixed_answer_and_abstain",
            detail=f"abstain together with {sorted(ANSWER_KEYS & obj.keys())}",
        )
    model = Abstain if "abstain" in obj else Answer
    try:
        return model.model_validate(obj, strict=True)
    except ValidationError as e:
        return ParseFailure(reason="schema", detail=_schema_detail(e))
