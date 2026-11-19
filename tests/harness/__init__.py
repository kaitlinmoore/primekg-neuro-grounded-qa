"""Tests for the harness: the KG toolset, the taskset wiring, the system prompt and
``scripts/run.sh``.

A package (with this file) so its modules import as ``harness.<name>`` and don't
collide with other suites' test module names. Fixtures are synthetic: the contracts'
store and items, plus ``tests/fixtures/harness/``.
"""
