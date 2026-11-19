"""The taskset: question items in, tasks with the KG toolset attached out.

``NeuroGroundedQATaskset`` loads ``QuestionItem``s from a JSONL file, keeps one split,
gives every task the system prompt below, and attaches the KG toolset
(``tools.KGToolset``) through ``toolsets()``. With its defaults it reads the
contracts' synthetic fixtures (``tests/fixtures/contracts/``), so the environment
runs end to end with no graph data. The real items and store are plugged in through
``items_path`` and ``tools.store_path``.

**Held-out guard.** Loading ``split="heldout"`` raises unless the config sets
``allow_heldout=true`` explicitly. Held-out items stay unexamined until the freeze, so
reading them has to be a visible, deliberate choice. Items of other splits are
skipped *before* they are validated, so a validation error can never print a held-out
item.

**Reward: not attached yet.** The task class has no ``@vf.reward`` methods; the
reward adapter from ``neuro_grounded_qa.reward`` is wired in a later integration step
(see the TODO on :class:`NeuroGroundedQATask`).

**Paths.** Relative ``items_path`` and ``tools.store_path`` resolve against the
current working directory, which is the repo root for ``scripts/run.sh``,
``vf-run`` and ``vf-validate`` as the README runs them. ``toolsets()`` makes the store
path absolute before handing it to the tool server, which runs in its own working
directory.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import verifiers.v1 as vf
from pydantic import model_validator

from neuro_grounded_qa.contracts import QuestionItem, Split
from neuro_grounded_qa.tools import TOOL_PREFIX, KGState, KGToolset, KGToolsetConfig

DEFAULT_ITEMS_PATH = "tests/fixtures/contracts/items.jsonl"
"""The contracts' synthetic fixture items, relative to the repo root."""

# The system prompt is an instrument: changes to it go in docs/changelog.md.
# It must describe exactly the format `contracts.output.parse_output` accepts;
# tests/harness/test_system_prompt.py parses its examples with that parser.
# `{prefix}`, `{max_calls}` and `{max_results}` are filled from the config.
SYSTEM_PROMPT_TEMPLATE = """\
You answer biomedical questions using only evidence from a knowledge graph. You reach the graph through three tools:

- {prefix}_search_entities(text): find entities by name. Returns entities with their graph IDs. Entities are not evidence.
- {prefix}_get_neighbors(entity_id, relation): list the edges that touch an entity, optionally only those with one relation label.
- {prefix}_get_edge(edge_id): fetch one edge by its evidence ID.

Tool results are JSON. Each edge in a result's "evidence" list has an "id" (its evidence ID, such as E:000001), a "subject", a "relation" and an "object". You can make at most {max_calls} tool calls, and each returns at most {max_results} items.

Only evidence returned by your own tool calls in this conversation counts. Background knowledge, and evidence IDs that did not come from your tool calls, count for nothing.

When you are done, reply with exactly one JSON object and nothing else (it may be inside one ```json code block). Either an answer:

{{"claims": [{{"subject_id": "...", "relation": "...", "object_id": "...", "cites": ["..."]}}], "answer_text": "...", "unsupported_parts": ["..."]}}

- Each claim states one fact. "subject_id" and "object_id" are entity IDs and "relation" is a relation label, each copied exactly from a tool result. "cites" lists the evidence IDs of the edges that state the fact; it must not be empty. A claim may also have "subject_name" and "object_name" for readability.
- "answer_text" answers the question in words.
- "unsupported_parts" lists parts of the question the evidence does not support. It may be an empty list or left out, but an answer needs at least one claim or one unsupported part.

or an abstention, when the evidence cannot support an answer:

{{"abstain": true, "reason": "..."}}

Use no other keys.
"""


def render_system_prompt(tools: KGToolsetConfig) -> str:
    """The system prompt with the tool prefix and the configured limits filled in."""
    return SYSTEM_PROMPT_TEMPLATE.format(
        prefix=TOOL_PREFIX, max_calls=tools.max_calls, max_results=tools.max_results
    )


def check_split_allowed(split: str, allow_heldout: bool) -> None:
    """Raise unless loading ``split`` is allowed.

    Raises:
        ValueError: for ``split="heldout"`` without ``allow_heldout=True``.
    """
    if split == "heldout" and allow_heldout is not True:
        raise ValueError(
            'refusing to load split="heldout": the held-out split stays unexamined '
            "until the freeze. Set allow_heldout=true explicitly to load it."
        )


class NeuroGroundedQAConfig(vf.TasksetConfig):
    """Taskset settings, under ``--env.taskset.*`` (``--taskset.*`` for vf-validate)."""

    items_path: Path = Path(DEFAULT_ITEMS_PATH)
    """JSONL file of ``QuestionItem``s, one per line."""
    split: Split = "train"
    """Which split to load."""
    allow_heldout: bool = False
    """Must be set to true, explicitly, to load ``split="heldout"``."""
    tools: KGToolsetConfig = KGToolsetConfig()
    """The KG tool server's settings, under ``--env.taskset.tools.*``."""

    @model_validator(mode="after")
    def _heldout_guard(self) -> NeuroGroundedQAConfig:
        # Checked when the config is built, so even `vf-run --dry-run` refuses.
        check_split_allowed(self.split, self.allow_heldout)
        return self


def load_items(path: str | Path, split: str, allow_heldout: bool = False) -> list[QuestionItem]:
    """Read the items of one split from a JSONL file.

    Lines whose ``split`` differs are skipped without validation. Blank lines are
    ignored.

    Raises:
        ValueError: held-out split not allowed; a line that isn't a JSON object or
            has no ``split``; an item that fails validation; or no items in ``split``.
        FileNotFoundError: if ``path`` doesn't exist.
    """
    check_split_allowed(split, allow_heldout)
    path = Path(path)
    items: list[QuestionItem] = []
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            if not line.strip():
                continue
            record: Any = json.loads(line)
            if not isinstance(record, dict) or "split" not in record:
                raise ValueError(f"{path}:{lineno}: expected an item object with a split")
            if record["split"] != split:
                continue
            try:
                items.append(QuestionItem.model_validate(record))
            except ValueError as e:
                raise ValueError(f"{path}:{lineno}: invalid item: {e}") from e
    if not items:
        raise ValueError(f"{path}: no items in split {split!r}")
    return items


class NeuroGroundedQATask(vf.Task[QuestionItem, KGState]):
    """One question. Its data is the ``QuestionItem``; its state carries the KG call
    counter, which the tool server updates (``tools.kg``).

    The state type must stay ``KGState`` (or a subclass): verifiers creates
    ``trace.state`` from the task's state type and validates every tool-server push
    against it.

    TODO(reward wiring): attach the reward adapter from ``neuro_grounded_qa.reward``
    here (its ``@vf.reward`` / ``@vf.metric`` methods, reading
    ``contracts.parse_output(trace.last_reply)``, ``contracts.retrieved_evidence(trace)``
    and gold from ``trace.task.data``). Until then the task has no reward.
    """


class NeuroGroundedQATaskset(vf.Taskset[NeuroGroundedQATask, NeuroGroundedQAConfig]):
    """Question items from ``items_path`` (one split), with the KG toolset."""

    @classmethod
    def toolsets(cls, config: NeuroGroundedQAConfig) -> list[vf.Toolset]:
        """The KG tool server, shared by every rollout of a run.

        Resolves ``store_path`` to an absolute path here, in the host process, because
        the server process runs in a different working directory.
        """
        tools = config.tools.model_copy(
            update={"store_path": config.tools.store_path.resolve()}
        )
        return [KGToolset(tools)]

    def load(self) -> list[NeuroGroundedQATask]:
        """Build one task per item of the configured split, with the system prompt."""
        cfg = self.config
        system_prompt = render_system_prompt(cfg.tools)
        return [
            NeuroGroundedQATask(item.model_copy(update={"system_prompt": system_prompt}), cfg.task)
            for item in load_items(cfg.items_path, cfg.split, cfg.allow_heldout)
        ]
