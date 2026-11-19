"""The stub endpoint itself: scripted policies, the wire format, and the server.

Fast and in-process (no ``vf-run``), so not marked ``integration``.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest
from verifiers.v1.dialects.chat import ChatDialect

from .stub_endpoint import (
    HOST,
    PolicyError,
    StubEndpoint,
    calls,
    completion,
    final,
    policies_from_json,
    scripted,
    step,
    stream_events,
    tool_call,
)

Q1 = [{"role": "system", "content": "sys"}, {"role": "user", "content": "question one"}]
SCRIPT = {"question one": [calls(tool_call("kg_get_edge", edge_id="E:000001")), final("done")]}


def _post(stub: StubEndpoint, body: dict) -> tuple[int, bytes]:
    req = urllib.request.Request(
        stub.base_url + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# --- Policies ------------------------------------------------------------------------


def test_scripted_steps_by_assistant_turns():
    policy = scripted(SCRIPT)
    assert policy(Q1) == SCRIPT["question one"][0]
    later = [*Q1, {"role": "assistant", "content": None}, {"role": "tool", "content": "{}"}]
    assert step(later) == 1
    assert policy(later) == final("done")


def test_scripted_matches_content_parts():
    parts = [{"role": "user", "content": [{"type": "text", "text": "question one"}]}]
    assert scripted(SCRIPT)(parts) == SCRIPT["question one"][0]


def test_scripted_unknown_question_and_overrun_are_errors():
    with pytest.raises(PolicyError, match="no script"):
        scripted(SCRIPT)([{"role": "user", "content": "other"}])
    assert scripted(SCRIPT, default=[final("x")])([{"role": "user", "content": "other"}]) == final("x")
    done = [*Q1, {"role": "assistant"}, {"role": "tool"}, {"role": "assistant"}]
    with pytest.raises(PolicyError, match="2 turns"):
        scripted(SCRIPT)(done)


def test_policies_from_json_uses_star_as_default():
    policies = policies_from_json({"p": {"question one": [final("a")], "*": [final("b")]}})
    assert policies["p"](Q1) == final("a")
    assert policies["p"]([{"role": "user", "content": "zzz"}]) == final("b")


# --- Wire format ---------------------------------------------------------------------


def test_completion_with_parallel_tool_calls():
    turn = calls(tool_call("kg_get_edge", edge_id="E:1"), {"name": "kg_get_edge", "arguments": "{bad"})
    chat = completion({"model": "stub/p", "messages": Q1}, turn)
    message = chat["choices"][0]["message"]
    assert chat["choices"][0]["finish_reason"] == "tool_calls"
    assert [c["id"] for c in message["tool_calls"]] == ["call_0_0", "call_0_1"]
    assert json.loads(message["tool_calls"][0]["function"]["arguments"]) == {"edge_id": "E:1"}
    assert message["tool_calls"][1]["function"]["arguments"] == "{bad"  # malformed, as given


def test_stream_events_parse_with_the_verifiers_chat_dialect():
    """The SSE the stub sends assembles, in verifiers' own stream parser, into the
    same message as the non-streamed completion."""
    turn = calls(tool_call("kg_get_edge", edge_id="E:1"), tool_call("kg_search_entities", text="a"))
    chat = completion({"model": "stub/p", "messages": Q1}, turn)
    parser = ChatDialect().stream_parser()
    for event in stream_events(chat):
        parser.feed(event)
    response = parser.finish()
    assert response.raw["choices"][0]["message"]["tool_calls"] == [
        {**c, "function": dict(c["function"])} for c in chat["choices"][0]["message"]["tool_calls"]
    ]
    assert response.raw["usage"] == chat["usage"]


# --- Server --------------------------------------------------------------------------


def test_server_binds_localhost_and_serves_both_modes():
    with StubEndpoint({"p": scripted(SCRIPT)}) as stub:
        assert stub.base_url.startswith(f"http://{HOST}:")
        status, raw = _post(stub, {"model": "stub/p", "messages": Q1})
        assert status == 200
        assert json.loads(raw)["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "kg_get_edge"
        status, raw = _post(stub, {"model": "p", "messages": Q1, "stream": True})
        assert status == 200 and raw.endswith(b"data: [DONE]\n\n")
        assert len(stub.requests) == 2 and stub.errors == []


def test_server_reports_policy_errors():
    with StubEndpoint({"p": scripted(SCRIPT)}) as stub:
        status, raw = _post(stub, {"model": "stub/nope", "messages": Q1})
        assert status == 400 and b"unknown policy" in raw
        status, raw = _post(stub, {"model": "stub/p", "messages": [{"role": "user", "content": "zz"}]})
        assert status == 400 and b"no script" in raw
        assert len(stub.errors) == 2
