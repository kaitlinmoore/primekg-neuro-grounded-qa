"""Question generation for neuro-grounded-qa: the shared, importable module.

The template catalog stays in ``scripts/data/qgen_templates.py``; everything that
turns templates into checked items lives here, and both the generator
(``scripts/data/question_set.py``) and the feasibility report
(``scripts/data/qgen_feasibility.py``) import it:

- :mod:`qgen.checks`: the reusable checks (names, routes, full-graph absence,
  flags, split hash, item IDs, leaks);
- :mod:`qgen.candidates`: the shape generators, the domain filters and the check
  pipeline (:func:`qgen.candidates.build_pool`);
- :mod:`qgen.selection`: the targets and the seeded selection, held-out first;
- :mod:`qgen.files`: the item files, counts and manifest;
- :mod:`qgen.verify`: the re-check of written files (counts only);
- :mod:`qgen.view`: the train-only viewer and the spot-check sample;
- :mod:`qgen.safety`: the no-text-out guard every command runs under;
- :mod:`qgen.cli`: the commands.

It sits in ``scripts/data/`` rather than the environment package because it is
build-time research tooling: it needs duckdb and the full graph, and nothing in it
runs during an evaluation. Import it with ``scripts/data`` on ``sys.path`` (the
scripts there, and ``tests/data/conftest.py``, arrange that).

See "Question generation design" in ``docs/question_set_design.md``.
"""

GENERATOR_VERSION = "1.1.0"
"""Recorded in every item's notes and in the manifest. Bump it when a change to this
module or the template catalog changes which items are generated or what they say."""
