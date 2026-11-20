"""Held-out hygiene for every command that touches items: no item text gets out.

Generation holds every candidate, held-out ones included, in memory; the item check
reads ``heldout.jsonl``. Neither may print, log or raise anything that carries item
content: question text, entity names, entity IDs or evidence IDs. This module is how
the commands keep to that:

- :func:`scrubbed_error` turns any exception into a one-line message that names
  only the stage and the exception's type. For a pydantic ``ValidationError`` it
  adds each error's location and type code (from ``errors(include_input=False)``),
  never its message: our own validators put evidence IDs and relation labels in
  their messages, and pydantic's default ``str(e)`` echoes the input values.
- :func:`no_text_out` is the context every command runs in. It disables logging
  (a library's log line could quote a value), records warnings instead of printing
  them (pydantic's serializer warnings quote values), and on an exception prints
  the scrubbed message and exits with status 2. The traceback, which would show the
  exception's message and its chained causes, is never printed.

What the commands *do* print is aggregate counts, hashes and file paths. The tests
in ``tests/data/test_question_set.py`` check that every command's stdout, stderr and
non-item files hold no entity name, entity ID or edge ID from a fixture held-out set.

The guard covers these commands' own output. It can't stop someone opening the
file; see "Split plan and held-out protection" in ``docs/question_set_design.md``.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import warnings
from collections.abc import Iterator

from pydantic import ValidationError


class ItemsError(Exception):
    """A refusal or failed precondition whose message is written to be safe to print:
    it names paths, build IDs, counts or check names only, never item content."""


def scrubbed_error(stage: str, e: BaseException) -> str:
    """A printable one-line description of ``e`` that carries no item content.

    ``ItemsError`` messages are written to be safe and are kept. Anything else is
    reduced to its type name; a ``ValidationError`` also lists ``loc:type`` for
    each error (field paths and pydantic's error codes, no values, no messages).
    """
    if isinstance(e, ItemsError):
        return f"{stage}: {e}"
    if isinstance(e, ValidationError):
        parts = [
            ".".join(str(x) for x in err.get("loc", ())) + ":" + str(err.get("type", "?"))
            for err in e.errors(include_input=False, include_url=False, include_context=False)
        ]
        return f"{stage}: ValidationError ({len(parts)}): " + "; ".join(parts)
    return f"{stage}: {type(e).__name__} (message withheld: it may quote item content)"


@contextlib.contextmanager
def no_text_out(stage: str) -> Iterator[list[warnings.WarningMessage]]:
    """Run a command with logging off, warnings recorded, and errors scrubbed.

    Yields the list of recorded warnings; the caller reports their count (and
    categories) only. On any exception, prints :func:`scrubbed_error` to stderr and
    raises ``SystemExit(2)`` with no traceback.
    """
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                yield caught
            except SystemExit:
                raise
            except BaseException as e:  # noqa: BLE001 - every error is scrubbed
                print(scrubbed_error(stage, e), file=sys.stderr)
                raise SystemExit(2) from None
    finally:
        logging.disable(previous)


def warning_summary(caught: list[warnings.WarningMessage]) -> str:
    """``"N warnings (Category: n, ...)"``: categories and counts, no messages."""
    if not caught:
        return "0 warnings"
    by_cat: dict[str, int] = {}
    for w in caught:
        by_cat[w.category.__name__] = by_cat.get(w.category.__name__, 0) + 1
    return f"{len(caught)} warnings (" + ", ".join(f"{k}: {v}" for k, v in sorted(by_cat.items())) + ")"
