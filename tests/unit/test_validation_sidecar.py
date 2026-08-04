"""Unit tests for the two validation sidecars (p-062)."""

from __future__ import annotations

import json
from pathlib import Path

from bffi_pipeline.validation.sidecar import (
    ERRORS_FILENAME,
    MESSAGE_MAX_LEN,
    VALIDATION_FILENAME,
    ValidationRow,
    ValidationSidecars,
)


def _row(**kwargs: object) -> ValidationRow:
    defaults: dict[str, object] = {
        "boundary": 1,
        "error_type": "marcxml-filename",
        "bib_id": "b1",
        "path": Path("/tmp/b1.xml"),
        "message": "nope",
    }
    defaults.update(kwargs)
    return ValidationRow(**defaults)  # type: ignore[arg-type]


def test_rejections_and_advisories_go_to_separate_files(tmp_path: Path) -> None:
    """The split is the point: "what did the pipeline refuse?" has to be
    answerable without parsing severity out of every row."""
    sidecars = ValidationSidecars(tmp_path)
    sidecars.reject(_row())
    sidecars.flag(_row(error_type="marcxml-content-minimum"))

    errors = [json.loads(x) for x in (tmp_path / ERRORS_FILENAME).read_text().splitlines()]
    flagged = [json.loads(x) for x in (tmp_path / VALIDATION_FILENAME).read_text().splitlines()]
    assert [r["error_type"] for r in errors] == ["marcxml-filename"]
    assert [r["error_type"] for r in flagged] == ["marcxml-content-minimum"]


def test_no_file_is_created_without_a_row(tmp_path: Path) -> None:
    ValidationSidecars(tmp_path)
    assert not (tmp_path / ERRORS_FILENAME).exists()
    assert not (tmp_path / VALIDATION_FILENAME).exists()


def test_first_row_truncates_and_later_rows_append(tmp_path: Path) -> None:
    """A re-run into the same output directory replaces the previous run's
    findings; appending would make the count read as the sum of both runs."""
    (tmp_path / ERRORS_FILENAME).write_text("stale\n", encoding="utf-8")

    sidecars = ValidationSidecars(tmp_path)
    sidecars.reject(_row(bib_id="b1"))
    sidecars.reject(_row(bib_id="b2"))

    rows = [json.loads(x) for x in (tmp_path / ERRORS_FILENAME).read_text().splitlines()]
    assert [r["bib_id"] for r in rows] == ["b1", "b2"]


def test_violations_is_omitted_unless_set(tmp_path: Path) -> None:
    assert "violations" not in json.loads(_row().as_json())
    assert json.loads(_row(violations=4).as_json())["violations"] == 4


def test_a_long_report_is_truncated(tmp_path: Path) -> None:
    """SHACL reports and XSD error logs run to thousands of characters. The
    sidecar is an index into the problem, not a transcript of it."""
    payload = json.loads(_row(message="x" * (MESSAGE_MAX_LEN + 500)).as_json())
    assert len(payload["message"]) == MESSAGE_MAX_LEN + 1  # + the ellipsis
    assert payload["message"].endswith("…")


def test_rows_carry_no_timestamp(tmp_path: Path) -> None:
    """Unlike the provenance sidecars, these stay byte-deterministic for a
    given input — so a diff between two runs is a real difference."""
    assert _row().as_json() == _row().as_json()
    assert "ts" not in json.loads(_row().as_json())
