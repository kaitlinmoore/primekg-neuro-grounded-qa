"""Graph toolset the agent uses to retrieve evidence.

Will provide ``search_entities``, ``get_neighbors`` and ``get_edge`` over the
evidence store as a ``vf.Toolset``, attached through ``Taskset.toolsets()``, with
a call budget and result caps. Tool results are structured JSON carrying evidence
IDs.

Owner: harness lane. Empty for now.
"""
