"""PLACEHOLDER taskset, so the Verifiers loader can discover the environment.

This module is a stub. It exists only so that ``neuro-grounded-qa`` resolves to a
``Taskset`` and ``vf-validate`` / ``vf-run --dry-run`` have something to load while
the repo skeleton is set up. It has one placeholder task, no tools and no rewards.

It will be replaced once the shared contracts (``neuro_grounded_qa.contracts``) and
the real wiring land: question items from ``data``, the graph toolset from ``tools``
and the grounding reward from ``reward``. Nothing here is an instrument; don't build
on it.
"""

import verifiers.v1 as vf

PLACEHOLDER_PROMPT = (
    "PLACEHOLDER task. This environment is not built yet; there is nothing to answer."
)


class PlaceholderTask(vf.Task[vf.TaskData]):
    """A task type with no rewards or metrics. Placeholder only."""


class NeuroGroundedQATaskset(vf.Taskset[PlaceholderTask, vf.TasksetConfig]):
    """Stub taskset yielding a single placeholder task. Placeholder only."""

    def load(self) -> list[PlaceholderTask]:
        """Return the one placeholder task. Real items come from the question set."""
        return [
            PlaceholderTask(
                vf.TaskData(id="placeholder-0", prompt=PLACEHOLDER_PROMPT),
                self.config.task,
            )
        ]
