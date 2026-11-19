"""Contract tests for the model output format and its strict parser.

Each fixture in ``tests/fixtures/contracts/model_outputs/`` holds the raw model text
and the ``ParseResult`` type (and failure reason) it must produce.
"""

import json

import pytest

from contracts.builders import FIXTURES
from neuro_grounded_qa.contracts import (
    Abstain,
    Answer,
    ParseFailure,
    parse_output,
)

OUTPUTS = FIXTURES / "model_outputs"
TYPES = {"Answer": Answer, "Abstain": Abstain, "ParseFailure": ParseFailure}
CASES = sorted(OUTPUTS.glob("*.json"))

REQUIRED_CASES = {
    "valid_answer",
    "valid_abstain",
    "partial_answer",
    "mixed_answer_and_abstain",
    "prose_around_json",
    "not_json",
    "extra_keys",
    "empty_cites",
    "empty_input",
}


def test_required_fixtures_present():
    assert REQUIRED_CASES <= {p.stem for p in CASES}


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_fixture_parses_as_expected(path):
    case = json.loads(path.read_text())
    result = parse_output(case["text"])
    assert type(result) is TYPES[case["expected_type"]], getattr(result, "detail", result)
    if case["expected_reason"] is not None:
        assert result.reason == case["expected_reason"]


def test_valid_answer_content():
    case = json.loads((OUTPUTS / "valid_answer.json").read_text())
    result = parse_output(case["text"])
    assert isinstance(result, Answer)
    first = result.claims[0]
    assert (first.subject_id, first.relation, first.object_id) == (
        "GENE_A", "associated_with", "DIS_1",
    )
    assert first.cites == ["E:000001"]
    assert result.unsupported_parts == []


def test_fenced_and_plain_parse_identically():
    plain = json.loads((OUTPUTS / "valid_answer.json").read_text())["text"]
    fenced = json.loads((OUTPUTS / "valid_answer_fenced.json").read_text())["text"]
    assert parse_output(plain) == parse_output(fenced)


def test_partial_answer_keeps_unsupported_parts():
    case = json.loads((OUTPUTS / "partial_answer.json").read_text())
    result = parse_output(case["text"])
    assert isinstance(result, Answer) and result.claims and result.unsupported_parts


def test_answer_with_only_unsupported_parts_is_valid():
    text = json.dumps({"claims": [], "answer_text": "x", "unsupported_parts": ["all of it"]})
    assert isinstance(parse_output(text), Answer)


@pytest.mark.parametrize(
    "text, reason",
    [
        (None, "empty"),
        ("", "empty"),
        ("```json\n\n```", "empty"),
        ("[1, 2]", "schema"),
        ('"just a string"', "schema"),
        ("{}", "schema"),
        ('{"abstain": true}', "schema"),
        ('{"abstain": true, "reason": "x", "claims": []}', "mixed_answer_and_abstain"),
        ('{"abstain": 1, "reason": "x"}', "schema"),
        ('{"claims": [], "answer_text": "x", "unsupported_parts": ["y"], "x": NaN}', "not_json"),
        ("```python\n{}\n```", "not_json"),
        ('{"abstain": true, "reason": "x"} {"abstain": true, "reason": "y"}', "not_json"),
    ],
)
def test_edge_cases(text, reason):
    result = parse_output(text)
    assert isinstance(result, ParseFailure) and result.reason == reason, result


def test_cites_are_not_format_checked_by_the_parser():
    # A fabricated cite is a grounding failure the reward must see, not a parse
    # failure that hides it.
    text = json.dumps(
        {
            "claims": [
                {"subject_id": "A", "relation": "r", "object_id": "B", "cites": ["made-up"]}
            ],
            "answer_text": "x",
        }
    )
    result = parse_output(text)
    assert isinstance(result, Answer) and result.claims[0].cites == ["made-up"]


def test_parser_never_raises_on_junk():
    for text in ["{", "}", "```", "``````", "\x00", "null", "true", "{" * 5000, "[" * 100_000]:
        assert isinstance(parse_output(text), ParseFailure)
