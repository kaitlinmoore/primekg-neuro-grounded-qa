"""The system prompt describes exactly the output format ``parse_output`` accepts and
the tools the model actually gets."""

from __future__ import annotations

import json
import re

from mcp.server.mcpserver import MCPServer

from harness.helpers import make_toolset, run
from neuro_grounded_qa.contracts import Abstain, Answer, Claim, ParseFailure, parse_output
from neuro_grounded_qa.taskset import render_system_prompt
from neuro_grounded_qa.tools import TOOL_PREFIX, KGToolsetConfig

PROMPT = render_system_prompt(KGToolsetConfig())
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
    prompt = render_system_prompt(KGToolsetConfig(max_calls=5, max_results=7))
    assert "at most 5 tool calls" in prompt
    assert "at most 7 items" in prompt


def test_prompt_says_to_keep_the_order_of_directed_relations():
    lines = [line for line in PROMPT.splitlines() if "directed" in line]
    assert lines == [
        "- Some relations are directed. For those, keep the subject and the object "
        "in the order the tool result shows."
    ]
