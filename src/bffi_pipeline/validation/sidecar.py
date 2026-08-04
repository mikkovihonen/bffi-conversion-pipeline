"""The two per-run validation sidecars.

`docs/validation-strategy.md` names two sinks, one per severity, and this
module is the only writer for both. They live in the stage's **output**
directory beside the records themselves::

    runs/<run>/bibframe/
      _errors.jsonl        # rejected — this record did not convert
      _validation.jsonl    # kept, but flagged

The split is the point. ``_errors.jsonl`` answers "what did the pipeline
refuse?", `_validation.jsonl` answers "what went through with a caveat?".
Merging them would make the first question unanswerable without parsing
severity out of every row.

Rows carry no timestamp, so the sidecars stay byte-deterministic for a
given input — unlike the provenance sidecars, which carry wall-clock
`prov:startedAtTime` by necessity. Each file is truncated when this run
writes its first row to it, so re-running a stage into an existing
directory replaces the previous run's findings instead of appending to
them.

Record-level detail belongs here and *not* in the stage-event stream: a
per-record event would put the record path into a Prometheus label, which
is the cardinality bug already fixed once in `46a55c2`. Aggregate counts
reach the dashboard through the stage's ``end`` counters.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

#: Rejections: the stage skipped the record.
ERRORS_FILENAME: Final[str] = "_errors.jsonl"

#: Advisories: the stage kept the record and flagged it.
VALIDATION_FILENAME: Final[str] = "_validation.jsonl"

#: Cap on the ``message`` field. An XSD error log or a SHACL conformance
#: report runs to thousands of characters; the sidecar is an index into
#: the problem, not a transcript of it. Re-run the boundary by hand for
#: the full report.
MESSAGE_MAX_LEN: Final[int] = 1000


@dataclass(frozen=True)
class ValidationRow:
    """One finding, in either sidecar."""

    #: 1, 2 or 3 — which boundary produced this. ``0`` means the row is a
    #: conversion failure rather than a validation finding: it shares
    #: ``_errors.jsonl`` because "which records are missing from the output,
    #: and why" is one question for the operator.
    boundary: int
    #: Typed family, e.g. ``marcxml-xsd-validation`` or ``bibframe-shape``.
    error_type: str
    bib_id: str
    path: Path
    message: str
    #: SHACL violation count, for Boundary 2 / 3 rows.
    violations: int | None = None

    def as_json(self) -> str:
        """Serialise to one compact JSON object (no trailing newline)."""
        message = self.message.strip()
        if len(message) > MESSAGE_MAX_LEN:
            message = message[:MESSAGE_MAX_LEN] + "…"
        payload: dict[str, object] = {
            "boundary": self.boundary,
            "error_type": self.error_type,
            "bib_id": self.bib_id,
            "path": str(self.path),
            "message": message,
        }
        if self.violations is not None:
            payload["violations"] = self.violations
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


@dataclass
class ValidationSidecars:
    """Writer for one stage's pair of sidecars.

    Constructed once per corpus run. Neither file is created until it has
    a row to hold, so a clean run leaves no empty sidecars behind.
    """

    output_dir: Path
    _opened: set[str] = field(default_factory=set)

    def reject(self, row: ValidationRow) -> None:
        """Record a rejection in ``_errors.jsonl``."""
        self._append(ERRORS_FILENAME, row)

    def flag(self, row: ValidationRow) -> None:
        """Record a non-blocking finding in ``_validation.jsonl``."""
        self._append(VALIDATION_FILENAME, row)

    def errors_path(self) -> Path:
        """Path of the rejection sidecar (may not exist)."""
        return self.output_dir / ERRORS_FILENAME

    def validation_path(self) -> Path:
        """Path of the advisory sidecar (may not exist)."""
        return self.output_dir / VALIDATION_FILENAME

    def _append(self, filename: str, row: ValidationRow) -> None:
        path = self.output_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        # First row of this run truncates; every later row appends.
        mode = "a" if filename in self._opened else "w"
        self._opened.add(filename)
        with path.open(mode, encoding="utf-8") as fh:
            fh.write(row.as_json() + "\n")


__all__ = [
    "ERRORS_FILENAME",
    "MESSAGE_MAX_LEN",
    "VALIDATION_FILENAME",
    "ValidationRow",
    "ValidationSidecars",
]
