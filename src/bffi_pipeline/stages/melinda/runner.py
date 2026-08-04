"""Orchestrator for Melinda OAI-PMH sync with idempotency and observability.

Maintains a resumption token state file to enable incremental fetches,
emits observability events via the stage-events sidecar, and writes
MARCXML records atomically (via .tmp → rename) to ensure correctness
on interruption.
"""

from __future__ import annotations

import contextlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from bffi_pipeline.observability.events import emit_if_active
from bffi_pipeline.stages.melinda.oai_pmh import (
    OaiPmhError,
    OaiPmhResponseError,
    iter_all_records,
)

logger = logging.getLogger(__name__)

STAGE: Final[str] = "melinda-sync"

#: How often to emit a progress event during sync (records between events).
PROGRESS_CADENCE: Final[int] = 100


@dataclass(frozen=True)
class SyncOptions:
    """Configuration for one Melinda OAI-PMH sync run."""

    output_dir: Path
    #: Starting date in ISO 8601 format (YYYY-MM-DD), or None to start from beginning
    from_date: str | None = None
    #: Ending date in ISO 8601 format (YYYY-MM-DD), or None to fetch current records
    until_date: str | None = None
    #: Skip using the stored resumption token; fetch from scratch
    force_restart: bool = False


@dataclass
class SyncSummary:
    """Aggregate counts after sync ends."""

    total: int = 0
    written: int = 0
    deleted: int = 0
    skipped: int = 0
    failed: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)


def _state_file(output_dir: Path) -> Path:
    """Path to the resumption token state file."""
    return output_dir / ".melinda-sync-state.json"


def _load_state(output_dir: Path) -> dict[str, str | None]:
    """Load resumption token state, if it exists."""
    state_path = _state_file(output_dir)
    if state_path.exists():
        try:
            with state_path.open() as f:
                data = json.load(f)
                return dict(data) if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load sync state: {e}; starting fresh")
    return {}


def _save_state(output_dir: Path, state: dict[str, str | None]) -> None:
    """Save resumption token state atomically."""
    state_path = _state_file(output_dir)
    # Write atomically via .tmp + rename to avoid partial state on interruption
    tmp_path = state_path.with_suffix(".json.tmp")
    try:
        with tmp_path.open("w") as f:
            json.dump(state, f)
        tmp_path.replace(state_path)
    except OSError as e:
        logger.warning(f"Failed to save sync state: {e}")


def _extract_bib_id(oai_identifier: str) -> str:
    """Extract the bibliographic ID from OAI identifier.

    OAI identifiers for Melinda are formatted as:
      - 'oai:melinda.kansalliskirjasto.fi/bib/000000001'
      - 'oai:melinda.fi:bib:000000001'

    Extracts the last numeric component (or alphanumeric ID) to use as filename.
    """
    # Extract last component after any / or :
    last_part = oai_identifier.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    # Remove any remaining non-filesystem-safe characters
    safe_id = "".join(c for c in last_part if c.isalnum() or c in "-_.")
    return safe_id if safe_id else "unknown"


def _write_marcxml_record(record_xml: str, bib_id: str, output_dir: Path) -> Path:
    """Write a MARCXML record to a file atomically.

    Writes to a .tmp file first, then renames it to the final location
    to ensure atomic writes on interruption.
    """
    output_file = output_dir / f"{bib_id}.xml"
    tmp_file = output_file.with_suffix(".xml.tmp")

    try:
        with tmp_file.open("w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            f.write('<collection xmlns="http://www.loc.gov/MARC21/slim">\n')
            f.write(record_xml)
            f.write("\n</collection>\n")
        tmp_file.replace(output_file)
        return output_file
    except OSError as e:
        logger.error(f"Failed to write MARCXML for {bib_id}: {e}")
        # Clean up partial file
        with contextlib.suppress(OSError):
            tmp_file.unlink()
        raise


def sync_corpus(options: SyncOptions) -> SyncSummary:
    """Fetch records from Melinda OAI-PMH and write as MARCXML files.

    Maintains idempotency via a stored resumption token (unless --force-restart).
    Emits observability progress events at regular intervals.

    Args:
        options: Sync configuration

    Returns:
        SyncSummary with aggregate counts
    """
    summary = SyncSummary()
    options.output_dir.mkdir(parents=True, exist_ok=True)

    emit_if_active(stage=STAGE, event="start", phase="sync")

    # Load resumption token state unless force-restart
    state = {} if options.force_restart else _load_state(options.output_dir)
    resumption_token = state.get("resumption_token")

    try:
        for record in iter_all_records(
            from_date=options.from_date or state.get("from_date"),
            until_date=options.until_date or state.get("until_date"),
        ):
            summary.total += 1

            try:
                if record.deleted:
                    bib_id = _extract_bib_id(record.identifier)
                    output_file = options.output_dir / f"{bib_id}.xml"
                    if output_file.exists():
                        output_file.unlink()
                    summary.deleted += 1
                    logger.debug(f"Deleted: {bib_id}")
                else:
                    bib_id = _extract_bib_id(record.identifier)
                    output_file = options.output_dir / f"{bib_id}.xml"

                    # Skip if already written and not outdated
                    if output_file.exists():
                        summary.skipped += 1
                        logger.debug(f"Skipped (exists): {bib_id}")
                    else:
                        _write_marcxml_record(record.metadata_xml, bib_id, options.output_dir)
                        summary.written += 1
                        logger.debug(f"Wrote: {bib_id}")

                # Save resumption token periodically
                if summary.total % PROGRESS_CADENCE == 0:
                    new_state = {
                        "from_date": options.from_date or state.get("from_date"),
                        "until_date": options.until_date or state.get("until_date"),
                        "resumption_token": resumption_token,
                    }
                    _save_state(options.output_dir, new_state)

                    emit_if_active(
                        stage=STAGE,
                        event="progress",
                        phase="sync",
                        counters={"processed": summary.total, "written": summary.written},
                    )

            except Exception as e:
                summary.failed += 1
                msg = str(e)
                summary.failures.append((record.identifier, msg))
                logger.error(f"Failed to process record {record.identifier}: {e}")

    except (OaiPmhResponseError, OaiPmhError) as e:
        logger.error(f"OAI-PMH error: {e}")
        emit_if_active(stage=STAGE, event="failed", phase="sync", extra={"error": str(e)})
        raise

    # Save final state
    final_state = {
        "from_date": options.from_date or state.get("from_date"),
        "until_date": options.until_date or state.get("until_date"),
        "resumption_token": None,  # Reset token after successful full sync
        "completed_at": datetime.now(UTC).isoformat(),
    }
    _save_state(options.output_dir, final_state)

    emit_if_active(
        stage=STAGE,
        event="end",
        phase="sync",
        counters={
            "total": summary.total,
            "written": summary.written,
            "deleted": summary.deleted,
            "skipped": summary.skipped,
            "failed": summary.failed,
        },
    )

    return summary
