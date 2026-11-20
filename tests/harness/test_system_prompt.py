"""The system prompt describes exactly the output format ``parse_output`` accepts and
the tools the model actually gets."""

from __future__ import annotations

import json
import re

import pytest

from mcp.server.mcpserver import MCPServer

from harness.helpers import ITEMS_PATH, STORE_PATH, fixture_store, make_toolset, run
from neuro_grounded_qa.contracts import (
    Abstain,
    Answer,
    Claim,
    EdgeEvidence,
    ParseFailure,
    parse_output,
)
from neuro_grounded_qa.reward import abstention_correct
from neuro_grounded_qa.taskset import (
    format_relation_labels,
    load_items,
    render_system_prompt,
    store_relations,
)
from neuro_grounded_qa.tools import TOOL_PREFIX, KGToolsetConfig

RELATIONS = store_relations(STORE_PATH)
PROMPT = render_system_prompt(KGToolsetConfig(), RELATIONS)
EXAMPLES = [line for line in PROMPT.splitlines() if line.startswith("{")]


def _keys(obj, out=None):
    out = set() if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            _keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _keys(v, out)
    return out


def test_prompt_has_one_answer_and_one_abstain_example():
    assert len(EXAMPLES) == 2


def test_examples_parse_with_parse_output():
    answer, abstain = (parse_output(e) for e in EXAMPLES)
    assert isinstance(answer, Answer), answer
    assert isinstance(abstain, Abstain), abstain


def test_fenced_example_also_parses():
    assert not isinstance(parse_output(f"```json\n{EXAMPLES[0]}\n```"), ParseFailure)


def test_examples_use_only_schema_keys():
    schema = set(Claim.model_fields) | set(Answer.model_fields) | set(Abstain.model_fields)
    for example in EXAMPLES:
        assert _keys(json.loads(example)) <= schema


def test_prompt_names_every_schema_key():
    schema = set(Claim.model_fields) | set(Answer.model_fields) | set(Abstain.model_fields)
    quoted = set(re.findall(r'"([a-z_]+)"', PROMPT))
    assert schema <= quoted, schema - quoted


def test_prompt_names_the_registered_tools():
    mcp = MCPServer("kg")
    make_toolset().register(mcp)
    advertised = {f"{TOOL_PREFIX}_{t.name}" for t in run(mcp.list_tools())}
    named = set(re.findall(rf"\b({TOOL_PREFIX}_[a-z_]+)\(", PROMPT))
    assert named == advertised


def test_prompt_states_the_configured_limits():
    prompt = render_system_prompt(KGToolsetConfig(max_calls=5, max_results=7), RELATIONS)
    assert "at most 5 tool calls" in prompt
    assert "at most 7 items" in prompt


def test_prompt_says_to_keep_the_order_of_directed_edges():
    assert (
        '- Edges with "directed": true must be stated with subject and object in the order '
        "the tool result shows; other edges may be stated in either order."
    ) in PROMPT.splitlines()
    assert '"directed" (true or false)' in PROMPT  # the payload field is named


def test_prompt_states_strict_citations():
    assert (
        '- Every ID in "cites" must come from your own tool results; a claim with any '
        "other ID counts as unsupported."
    ) in PROMPT.splitlines()
    assert "count for nothing" not in PROMPT
    assert "Background knowledge counts for nothing." in PROMPT


def test_prompt_directed_field_matches_the_payload():
    """The field the prompt names is the one tool payloads carry."""
    assert "directed" in EdgeEvidence.model_fields


def test_prompt_states_the_relation_filter_hint():
    """Unfiltered neighbor lists from a well-connected entity are cut at the result
    cap, so they show only its first relations. The hint names the filter and that
    cut, and nothing about answers or answerability."""
    [hint] = [line for line in PROMPT.splitlines() if line.startswith("Without a relation")]
    assert hint == (
        f"Without a relation, {TOOL_PREFIX}_get_neighbors on an entity with many edges returns "
        "only the edges of its first few relations; pass a relation label to list the edges "
        "of one relation."
    )
    neutral = hint.lower()
    for word in ("answer", "abstain", "unsupported", "question", "evidence"):
        assert word not in neutral


# -- final-message format and the answer-or-abstain choice ---------------------------

FORMAT_LINE = (
    "When you are done, your final message must contain only one JSON object, with no "
    "text before or after it: no introduction, no summary, no notes. The object may be "
    "wrapped in one ```json code block, with nothing outside the block. Any other text "
    "in the final message makes the whole reply unreadable. The object is either an answer:"
)

CHOICE_RULES = [
    "- If no edge in your tool results states what the question asks, reply with the "
    "abstention, even if you found related facts. You may mention those facts in "
    '"reason".',
    "- If your tool results support some of what the question asks but not all of it, "
    'answer the supported part with claims and list the rest in "unsupported_parts".',
    "- If the question rests on a premise that an edge in your tool results contradicts, "
    "you may either abstain or answer with cited claims that correct the premise.",
]


def test_prompt_states_json_only_output():
    assert FORMAT_LINE in PROMPT.splitlines()


def test_parser_is_still_strict_about_text_around_the_json():
    """The prompt says text outside the object makes the reply unreadable; that holds
    because the parser is unchanged (one bare object, or one fenced block, only)."""
    fenced = f"```json\n{EXAMPLES[1]}\n```"
    assert isinstance(parse_output(fenced), Abstain)
    for text in (
        f"Here is my answer:\n{fenced}",
        f"{fenced}\nHope this helps.",
        f"Here is my answer: {EXAMPLES[1]}",
    ):
        failure = parse_output(text)
        assert isinstance(failure, ParseFailure) and failure.reason == "not_json", text


def test_prompt_states_the_answer_or_abstain_rules():
    lines = PROMPT.splitlines()
    start = lines.index("Choosing between an answer and an abstention:")
    assert [line for line in lines[start + 1 :] if line] == CHOICE_RULES


def test_choice_rules_name_no_question_types_or_labels():
    """The rules restate what the reward scores, in terms of the tool results only.
    They name no question type, template or answerability label, so they give no
    hint about any particular item."""
    text = " ".join(CHOICE_RULES).lower()
    for word in (
        "single_hop", "single-hop", "multi_hop", "multi-hop", "false_premise",
        "false premise", "unanswerable", "answerable", "correctable", "partial",
        "template", "bridge", "type", "score", "reward",
    ):  # fmt: skip
        assert word not in text, word
    # The same holds for the whole prompt.
    for word in ("unanswerable", "answerable", "correctable", "template", "bridge"):
        assert word not in PROMPT.lower(), word


def test_choice_rules_agree_with_the_reward():
    """Each rule matches ``abstention_correct``: abstain when nothing asked is stated
    (``no``), answer the supported part (``partial``), either for a contradicted
    premise (``correctable``)."""
    abstain, answer = parse_output(EXAMPLES[1]), parse_output(EXAMPLES[0])
    by_label = {item.answerable: item for item in load_items(ITEMS_PATH, "train")}
    assert {"yes", "partial", "no", "correctable"} <= by_label.keys()
    expected = {
        "yes": (False, True),
        "partial": (False, True),
        "no": (True, False),
        "correctable": (True, True),
    }
    for label, (abstain_ok, answer_ok) in expected.items():
        item = by_label[label]
        assert abstention_correct(item, abstain) is abstain_ok, label
        assert abstention_correct(item, answer) is answer_ok, label


# -- the store's relation labels ------------------------------------------------------

LABEL_LINE_PREFIX = "Relation labels in this graph: "
LABEL_LINE_SUFFIX = ". Pass these exactly as written."


def _label_line(prompt: str) -> str:
    [line] = [line for line in prompt.splitlines() if line.startswith(LABEL_LINE_PREFIX)]
    return line


def _listed_labels(prompt: str) -> list[str]:
    line = _label_line(prompt)
    assert line.endswith(LABEL_LINE_SUFFIX), line
    return re.findall(r"`([^`]+)`", line)


def test_prompt_lists_exactly_the_stores_relation_labels():
    """The labels are the store's vocabulary, which is what ``get_neighbors`` checks
    a relation against, so every listed label is one the tools accept."""
    listed = _listed_labels(PROMPT)
    assert listed == sorted(fixture_store().relations())
    assert set(listed) == make_toolset().relations
    assert len(listed) == 8  # the fixture store's labels


def test_label_line_is_the_labels_and_nothing_else():
    line = _label_line(PROMPT)
    expected = LABEL_LINE_PREFIX + ", ".join(f"`{r}`" for r in RELATIONS) + LABEL_LINE_SUFFIX
    assert line == expected


def test_label_order_is_alphabetical_whatever_the_input_order():
    shuffled = [RELATIONS[i] for i in (3, 0, 7, 5, 1, 6, 2, 4)]
    assert sorted(shuffled) == RELATIONS
    for relations in (RELATIONS, list(reversed(RELATIONS)), shuffled, RELATIONS * 2):
        assert render_system_prompt(KGToolsetConfig(), relations) == PROMPT


def test_store_relations_reads_the_store_file():
    assert store_relations(STORE_PATH) == RELATIONS == sorted(RELATIONS)


def test_labels_with_spaces_and_slashes_stay_whole():
    """Real labels include ``off-label use`` and ``drug_protein/target``: backticks
    keep each one in one piece."""
    text = format_relation_labels(["off-label use", "drug_protein/target", "indication"])
    assert text == "`drug_protein/target`, `indication`, `off-label use`"


def test_no_labels_is_an_error():
    with pytest.raises(ValueError, match="no relation labels"):
        format_relation_labels([])


def test_label_line_adds_no_item_words():
    """Same line for every item: it holds the store's labels and the fixed words
    around them, and none of the words the neutrality tests forbid."""
    words = _label_line(PROMPT).replace("`", " ").lower()
    for word in ("unanswerable", "answerable", "correctable", "template", "bridge",
                 "abstain", "question", "score", "reward"):  # fmt: skip
        assert word not in words, word
