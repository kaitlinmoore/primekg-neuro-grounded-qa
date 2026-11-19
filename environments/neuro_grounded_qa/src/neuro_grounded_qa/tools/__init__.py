"""Graph toolset the agent uses to retrieve evidence.

``KGToolset`` serves ``search_entities``, ``get_neighbors`` and ``get_edge`` over an
evidence store as MCP tools (prefix ``kg``), attached through
``Taskset.toolsets()``. Results are ``ToolResultPayload`` JSON, capped by count and
size; each rollout has a call budget tracked in ``KGState``. ``load_store`` is the one
place a store is loaded from disk. Details in ``tools.kg`` and ``tools.store``.
"""

from neuro_grounded_qa.tools.kg import (
    DEFAULT_STORE_PATH,
    TOOL_PREFIX,
    KGState,
    KGToolset,
    KGToolsetConfig,
)
from neuro_grounded_qa.tools.store import load_store

__all__ = [
    "DEFAULT_STORE_PATH",
    "TOOL_PREFIX",
    "KGState",
    "KGToolset",
    "KGToolsetConfig",
    "load_store",
]
