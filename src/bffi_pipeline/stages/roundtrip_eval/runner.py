"""Pillar 5 orchestrator: round-trip diff harness.

Walks a directory of source MARCXML records + a directory of reconstructed
MARCXML records (from step 4), pairs them by ``controlfield 001`` content,
diffs each pair, and emits:

  - per-record diff classification rows,
  - a single cataloguer-review HTML report,
  - observability events (start / progress / failed / end + corpus
    distribution counters).

Stage label for observability sidecar events: ``roundtrip_eval``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from bffi_pipeline.observability.events import emit_if_active
from bffi_pipeline.stages.roundtrip_eval.diff import (
    DiffStatus,
    MarcxmlParseError,
    RecordDiff,
    diff_records,
    parse_record,
)
from bffi_pipeline.stages.roundtrip_eval.html import render

STAGE: Final[str] = "roundtrip_eval"

PROGRESS_CADENCE: Final[int] = 100


@dataclass(frozen=True)
class EvalOptions:
    """Configuration for one round-trip eval run."""

    source_dir: Path
    reconstructed_dir: Path
    #: HTML report destination. ``None`` skips the HTML render (the JSONL
    #: sidecar + summary counters still land).
    html_path: Path | None = None


@dataclass
class EvalSummary:
    """Aggregate outcome of one round-trip eval run."""

    total_pairs: int = 0
    diffed: int = 0
    failed: int = 0
    source_only: int = 0
    reconstructed_only: int = 0
    distribution: Counter[DiffStatus] = field(default_factory=Counter)
    failures: list[tuple[Path, str]] = field(default_factory=list)


class RoundtripEvalError(RuntimeError):
    """Eval-side failure (typically a malformed input MARCXML)."""


def _index_by_bib_id(
    directory: Path, pattern: str
) -> tuple[dict[str, Path], list[tuple[Path, str]]]:
    """Walk ``directory/<pattern>``, parse each, index by 001.

    Returns ``(index, unreadable)``. A record that can't be parsed has no
    bib ID, so it can never be paired and can never surface later in the
    diff loop — the caller must report it here or it vanishes. Returning it
    keeps the "errors over silent fallbacks" rule intact.
    """
    out: dict[str, Path] = {}
    unreadable: list[tuple[Path, str]] = []
    for path in sorted(directory.glob(pattern)):
        try:
            bib_id, _ = parse_record(path)
        except MarcxmlParseError as exc:
            unreadable.append((path, str(exc)))
            continue
        out[bib_id] = path
    return out, unreadable


def run_eval(*, options: EvalOptions) -> EvalSummary:
    """Pair source vs reconstructed records by 001 and diff each pair.

    Emits sidecar events:

      - ``start``    with ``entities_total`` = number of pairs to diff
      - ``progress`` every ``PROGRESS_CADENCE`` pairs + on the final pair
      - ``failed``   per unreadable record and per pair that raises
      - ``end``      with the corpus diff distribution + orphan counts
    """
    source_index, source_unreadable = _index_by_bib_id(options.source_dir, "*.xml")
    reconstructed_index, reconstructed_unreadable = _index_by_bib_id(
        options.reconstructed_dir, "*.marcxml"
    )

    paired_ids = sorted(set(source_index) & set(reconstructed_index))
    summary = EvalSummary(
        total_pairs=len(paired_ids),
        source_only=len(set(source_index) - set(reconstructed_index)),
        reconstructed_only=len(set(reconstructed_index) - set(source_index)),
    )

    emit_if_active(
        stage=STAGE,
        event="start",
        counters={"entities_total": summary.total_pairs},
    )

    # Records that couldn't be parsed at all never make it into either
    # index, so report them before the diff loop — otherwise an unreadable
    # record disappears from the eval entirely.
    for path, message in (*source_unreadable, *reconstructed_unreadable):
        summary.failed += 1
        summary.failures.append((path, message))
        emit_if_active(
            stage=STAGE,
            event="failed",
            extra={
                "path": str(path),
                "error": message[:240],
                "error_type": MarcxmlParseError.__name__,
            },
        )

    diffs: list[RecordDiff] = []
    for idx, bib_id in enumerate(paired_ids, start=1):
        source_path = source_index[bib_id]
        reconstructed_path = reconstructed_index[bib_id]
        try:
            record_diff = diff_records(
                source_path=source_path,
                reconstructed_path=reconstructed_path,
            )
        # One unreadable record must never abort the eval; counted, emitted
        # and re-surfaced in ``summary.failures`` like the conversion stages.
        except Exception as exc:
            summary.failed += 1
            message = str(exc)
            summary.failures.append((source_path, message))
            emit_if_active(
                stage=STAGE,
                event="failed",
                extra={
                    "bib_id": bib_id,
                    "error": message[:240],
                    "error_type": type(exc).__name__,
                },
            )
        else:
            summary.diffed += 1
            for status, count in record_diff.status_counts.items():
                summary.distribution[status] += count
            diffs.append(record_diff)

        if idx % PROGRESS_CADENCE == 0 or idx == summary.total_pairs:
            emit_if_active(
                stage=STAGE,
                event="progress",
                counters={"entities_processed": idx},
            )

    if options.html_path is not None and diffs:
        options.html_path.parent.mkdir(parents=True, exist_ok=True)
        options.html_path.write_text(render(diffs), encoding="utf-8")

    emit_if_active(
        stage=STAGE,
        event="end",
        counters={
            "diffed": summary.diffed,
            "failed": summary.failed,
            "source_only": summary.source_only,
            "reconstructed_only": summary.reconstructed_only,
            **{status: count for status, count in summary.distribution.items()},
        },
    )

    return summary
