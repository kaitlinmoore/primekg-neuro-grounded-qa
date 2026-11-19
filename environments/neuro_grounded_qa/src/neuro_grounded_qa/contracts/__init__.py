"""Shared contracts: the interfaces the other subpackages build against.

Will hold, as committed code with contract tests in ``tests/contracts/``:
- the kind-tagged evidence unit and the evidence store interface (``evidence.py``)
- the question item schema, a ``TaskData`` subclass (``items.py``)
- the model output schema (claims with cited evidence IDs, or abstain) and the
  ``parse_output`` signature (``output.py``)
- the tool result format and ``retrieved_ids(trace)`` with strict provenance
  (``trace.py``)

Owner: orchestrator lane. Changes only through a deliberate contract change.
Empty for now.
"""
