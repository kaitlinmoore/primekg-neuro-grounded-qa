"""A scripted, OpenAI-compatible chat completions endpoint for no-model runs.

The stub stands in for a model so that ``scripts/run.sh`` can run the whole
environment end to end (taskset, ``null`` harness, KG tool server, reward) with no
model call. It is not a model: each reply comes from a *policy*, a plain function
of the conversation so far. The reward-hacking audit's exploit policies are scripted
the same way.

Standard library only, and it binds to ``127.0.0.1`` only.

**Choosing a policy.** The run's model name picks it: ``-m stub/honest`` makes the
stub answer every request with the policy registered as ``honest`` (the ``stub/``
prefix is optional). One stub can serve several policies, one per run.

**Policies.** A policy takes the request's ``messages`` (OpenAI chat format) and
returns one *turn*:

- ``{"content": "..."}``: a final text reply. This ends the rollout under the
  ``null`` harness.
- ``{"tool_calls": [{"name": "kg_get_edge", "arguments": {"edge_id": "E:000001"}},
  ...]}``: one or more tool calls in one assistant message (several make them
  parallel calls in one turn). ``arguments`` may be a dict or a raw JSON string (a
  malformed string is sent as is). ``id`` is optional; the default is
  ``call_<step>_<i>``. A turn may carry ``content`` as well.

:func:`scripted` builds the common kind: a fixed list of turns per question,
chosen by the first user message's text, stepping one turn per assistant message
already in the conversation. Requests are stateless (each carries the whole
conversation), so scripted policies need no per-rollout state and concurrent
rollouts can't mix.

**What it serves.** ``POST /v1/chat/completions``, streaming (SSE, which the
``null`` harness's chat loop requests and verifiers relays) or not, and
``GET /v1/models``. Token usage is a rough character count, not real tokens.

Use from Python (tests)::

    with StubEndpoint({"abstain": scripted({}, default=[{"content": ABSTAIN}])}) as stub:
        subprocess.run(["scripts/run.sh", "-m", "stub/abstain",
                        "--client.base-url", stub.base_url, ...])
        stub.requests  # every request body received, in order

Or from the command line, with policies in a JSON file shaped
``{"<policy>": {"<first user message>": [turn, ...], "*": [turn, ...]}}``
(``"*"`` is the default script)::

    uv run python tests/integration/stub_endpoint.py --scripts policies.json --port 8765
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

HOST = "127.0.0.1"
"""The only address the stub binds to."""

Turn = dict[str, Any]
"""One scripted assistant turn: ``{"content": ...}`` and/or ``{"tool_calls": [...]}``."""

Policy = Callable[[list[dict[str, Any]]], Turn]
"""A function from the request's messages to the next turn."""

MODEL_PREFIX = "stub/"


class PolicyError(Exception):
    """A policy can't produce a turn for this request (returned as HTTP 400)."""


# --- Reading a conversation ----------------------------------------------------------


def message_text(message: Mapping[str, Any]) -> str:
    """A message's text content, whether it is a string or a list of content parts."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, Mapping)
        )
    return ""


def first_user_text(messages: Sequence[Mapping[str, Any]]) -> str:
    """The text of the first user message: the question, under the ``null`` harness."""
    for message in messages:
        if message.get("role") == "user":
            return message_text(message)
    return ""


def step(messages: Sequence[Mapping[str, Any]]) -> int:
    """How many assistant turns the conversation already has: the next turn's index."""
    return sum(1 for m in messages if m.get("role") == "assistant")


def tool_results(messages: Sequence[Mapping[str, Any]]) -> list[str]:
    """The text of every tool message so far, in order (for adaptive policies)."""
    return [message_text(m) for m in messages if m.get("role") == "tool"]


# --- Policies ------------------------------------------------------------------------


def scripted(
    scripts: Mapping[str, Sequence[Turn]], default: Sequence[Turn] | None = None
) -> Policy:
    """A policy that plays a fixed list of turns per question.

    Args:
        scripts: turns keyed by the exact text of the first user message (the
            question's prompt).
        default: turns for any question not in ``scripts``. Without it, an unknown
            question is an error.

    The turn played is ``turns[step(messages)]``. Running past the end of a script is
    an error, so a script that never gives a final reply fails loudly instead of
    looping.
    """

    def policy(messages: list[dict[str, Any]]) -> Turn:
        question = first_user_text(messages)
        turns = scripts.get(question, default)
        if turns is None:
            raise PolicyError(f"no script for question {question[:80]!r}")
        i = step(messages)
        if i >= len(turns):
            raise PolicyError(f"script has {len(turns)} turns; asked for turn {i + 1}")
        return turns[i]

    return policy


def tool_call(name: str, **arguments: Any) -> dict[str, Any]:
    """One tool call for a turn's ``tool_calls`` list."""
    return {"name": name, "arguments": arguments}


def calls(*tool_calls: dict[str, Any]) -> Turn:
    """A turn made of tool calls (several are parallel calls in one message)."""
    return {"tool_calls": list(tool_calls)}


def final(content: str) -> Turn:
    """A final text turn."""
    return {"content": content}


# --- Wire format ---------------------------------------------------------------------


def _assistant_message(turn: Turn, i: int) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": turn.get("content")}
    wire_calls = []
    for j, call in enumerate(turn.get("tool_calls") or []):
        arguments = call.get("arguments", {})
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments)
        wire_calls.append(
            {
                "id": call.get("id") or f"call_{i}_{j}",
                "type": "function",
                "function": {"name": call["name"], "arguments": arguments},
            }
        )
    if wire_calls:
        message["tool_calls"] = wire_calls
    return message


def _usage(messages: Sequence[Mapping[str, Any]], message: Mapping[str, Any]) -> dict[str, int]:
    prompt = len(json.dumps(messages)) // 4
    completion = len(json.dumps(message)) // 4
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}


def completion(body: Mapping[str, Any], turn: Turn) -> dict[str, Any]:
    """The ``chat.completion`` object for one turn."""
    messages = body.get("messages") or []
    message = _assistant_message(turn, step(messages))
    return {
        "id": f"stub-{time.time_ns()}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", ""),
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
            }
        ],
        "usage": _usage(messages, message),
    }


def stream_events(chat: Mapping[str, Any]) -> list[bytes]:
    """The SSE events for a streamed reply: the whole message as one delta (tool calls
    indexed), a usage chunk, then ``[DONE]``. Mirrors verifiers' own
    ``ChatDialect.stream_events``."""
    choice = chat["choices"][0]
    delta = dict(choice["message"])
    if delta.get("tool_calls"):
        delta["tool_calls"] = [{**c, "index": k} for k, c in enumerate(delta["tool_calls"])]
    head = {k: v for k, v in chat.items() if k not in ("choices", "usage")}
    head["object"] = "chat.completion.chunk"
    chunks = [
        {**head, "choices": [{"index": 0, "delta": delta, "finish_reason": choice["finish_reason"]}]},
        {**head, "choices": [], "usage": chat["usage"]},
    ]
    return [f"data: {json.dumps(c)}\n\n".encode() for c in chunks] + [b"data: [DONE]\n\n"]


# --- Server --------------------------------------------------------------------------


class StubEndpoint:
    """The stub server, run on a background thread.

    Args:
        policies: policies by name; ``-m stub/<name>`` selects one.
        port: the port to bind on ``127.0.0.1``; 0 picks a free one.

    Attributes:
        requests: every chat completions request body received, in order.
        errors: messages for requests that failed (unknown policy, script errors).
    """

    def __init__(self, policies: Mapping[str, Policy], port: int = 0) -> None:
        self.policies = dict(policies)
        self.requests: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer((HOST, port), self._handler())
        self._server.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def base_url(self) -> str:
        """The URL to pass as ``--client.base-url``."""
        return f"http://{HOST}:{self.port}/v1"

    def start(self) -> StubEndpoint:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self) -> StubEndpoint:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def reply(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """The completion for one request body. Raises ``PolicyError``."""
        model = str(body.get("model", ""))
        name = model.removeprefix(MODEL_PREFIX)
        policy = self.policies.get(name)
        if policy is None:
            raise PolicyError(f"unknown policy {name!r} (model {model!r})")
        return completion(body, policy(list(body.get("messages") or [])))

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:  # quiet
                return

            def _send_json(self, status: int, obj: Any) -> None:
                data = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                if self.path.rstrip("/").endswith("/models"):
                    data = [{"id": MODEL_PREFIX + n, "object": "model"} for n in stub.policies]
                    self._send_json(200, {"object": "list", "data": data})
                else:
                    self._send_json(404, {"error": {"message": f"no route {self.path}"}})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                if not self.path.rstrip("/").endswith("/chat/completions"):
                    self._send_json(404, {"error": {"message": f"no route {self.path}"}})
                    return
                try:
                    body = json.loads(raw)
                except ValueError:
                    self._send_json(400, {"error": {"message": "body is not JSON"}})
                    return
                with stub._lock:
                    stub.requests.append(body)
                try:
                    chat = stub.reply(body)
                except PolicyError as e:
                    with stub._lock:
                        stub.errors.append(str(e))
                    self._send_json(400, {"error": {"message": str(e), "type": "stub_policy"}})
                    return
                if not body.get("stream"):
                    self._send_json(200, chat)
                    return
                events = b"".join(stream_events(chat))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(events)))
                self.end_headers()
                self.wfile.write(events)

        return Handler


# --- Command line --------------------------------------------------------------------


def policies_from_json(spec: Mapping[str, Mapping[str, Sequence[Turn]]]) -> dict[str, Policy]:
    """Scripted policies from ``{"<policy>": {"<first user message>": [turn, ...],
    "*": [turn, ...]}}``."""
    out: dict[str, Policy] = {}
    for name, scripts in spec.items():
        scripts = dict(scripts)
        default = scripts.pop("*", None)
        out[name] = scripted(scripts, default=default)
    return out


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--scripts", type=Path, required=True, help="policies JSON file")
    parser.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (0: any free)")
    args = parser.parse_args(argv)
    policies = policies_from_json(json.loads(args.scripts.read_text(encoding="utf-8")))
    with StubEndpoint(policies, port=args.port) as stub:
        print(f"stub serving {sorted(policies)} at {stub.base_url}", flush=True)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
