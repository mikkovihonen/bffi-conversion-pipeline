"""Unit tests for the Prometheus exporter (p-059).

Three surfaces, tested independently:

* :class:`MetricStore` — pure row-in, gauge-out translation.
* :class:`SidecarTailer` — byte offsets, partial lines, truncation.
* :class:`Exporter` — glob rescan, tick, PID/argv bookkeeping.

No socket is bound: the HTTP serve path is two lines of
``prometheus_client`` and exercising it would mean listening on a port
from the unit suite.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bffi_pipeline.observability.exporter import (
    ARGV_FILENAME,
    PHASE_SENTINEL,
    PID_FILENAME,
    Exporter,
    MetricStore,
    SidecarTailer,
    parse_ts,
)

RUN = "20260804-1055-abcdef"


def _row(event: str, **kwargs: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "ts": "2026-08-04T10:00:00Z",
        "run_uuid": RUN,
        "stage": "bibframe2bffi",
        "event": event,
    }
    row.update(kwargs)
    return row


def _write(path: Path, rows: list[dict[str, Any]], *, mode: str = "a") -> None:
    with path.open(mode, encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")


# --- parse_ts ------------------------------------------------------------


def test_parse_ts_reads_the_emitter_format() -> None:
    assert parse_ts("2026-08-04T10:00:00Z") == 1785837600.0


# --- MetricStore ---------------------------------------------------------


def test_start_sets_started_timestamp_and_entities_total() -> None:
    store = MetricStore()
    assert store.ingest(_row("start", counters={"entities_total": 12666}))
    text = store.render()
    assert (
        f'bffi_stage_started_timestamp{{run_uuid="{RUN}",stage="bibframe2bffi"}} 1.7858376e+09'
        in text
    )
    assert (
        f'bffi_stage_entities_total{{phase="_",run_uuid="{RUN}",stage="bibframe2bffi"}} 12666.0'
        in text
    )


def test_phase_defaults_to_the_sentinel_and_is_honoured_when_set() -> None:
    store = MetricStore()
    store.ingest(_row("start", counters={"entities_total": 10}))
    store.ingest(_row("phase_boundary", phase="route", counters={"entities_total": 4}))
    text = store.render()
    assert f'phase="{PHASE_SENTINEL}"' in text
    assert 'phase="route"' in text


def test_end_expands_every_counter_into_an_outcome_series() -> None:
    store = MetricStore()
    store.ingest(_row("end", counters={"success": 7, "failed": 2, "routing_hub": 3}))
    text = store.render()
    for outcome, value in (("success", "7.0"), ("failed", "2.0"), ("routing_hub", "3.0")):
        assert f'outcome="{outcome}"' in text
        assert value in text
    assert "bffi_stage_ended_timestamp" in text


def test_failed_sets_the_failed_gauge_and_accumulates_errors() -> None:
    store = MetricStore()
    store.ingest(_row("failed", extra={"error_type": "XsltprocError", "message": "boom"}))
    store.ingest(_row("failed", extra={"error_type": "XsltprocError", "message": "boom"}))
    text = store.render()
    assert 'error_type="XsltprocError"' in text
    # One sidecar row per failed record, so the error count accumulates.
    expected = (
        f'bffi_stage_errors_total{{error_type="XsltprocError",'
        f'run_uuid="{RUN}",stage="bibframe2bffi"}} 2.0'
    )
    assert expected in text


def test_failed_falls_back_to_the_error_key_the_stages_actually_emit() -> None:
    """The runners emit ``extra={"path": ..., "error": ...}`` rather than
    ``message``; the store must surface that as the message label."""
    store = MetricStore()
    store.ingest(_row("failed", extra={"error": "xsltproc not found"}))
    assert 'message="xsltproc not found"' in store.render()


def test_plan_marks_planned_stages_phases_and_description() -> None:
    store = MetricStore()
    store.ingest(
        _row(
            "plan",
            stage="pipeline",
            extra={
                "stages": ["marc2bibframe", "bibframe2bffi"],
                "stage_phases": {"bibframe2bffi": ["rename", "route"]},
                "description": "smoke run",
            },
        )
    )
    text = store.render()
    assert f'bffi_stage_planned{{run_uuid="{RUN}",stage="marc2bibframe"}} 1.0' in text
    assert 'bffi_stage_phase_planned{phase="route"' in text
    assert 'description="smoke run"' in text


def test_skipped_records_the_reason() -> None:
    store = MetricStore()
    store.ingest(_row("skipped", extra={"reason": "output fresh"}))
    assert 'reason="output fresh"' in store.render()


def test_progress_derives_throughput_and_eta() -> None:
    store = MetricStore()
    store.ingest(_row("start", counters={"entities_total": 100}))
    store.ingest(_row("progress", ts="2026-08-04T10:00:00Z", counters={"entities_processed": 0}))
    store.ingest(_row("progress", ts="2026-08-04T10:01:00Z", counters={"entities_processed": 20}))
    text = store.render()
    # 20 records in 60 s → 20/min, and 80 left at 1/3 per second → 240 s.
    assert (
        f'bffi_stage_throughput_per_minute{{phase="_",run_uuid="{RUN}",stage="bibframe2bffi"}} 20.0'
        in text
    )
    assert (
        f'bffi_stage_eta_seconds{{phase="_",run_uuid="{RUN}",stage="bibframe2bffi"}} 240.0' in text
    )


def test_single_progress_event_yields_no_rate() -> None:
    """One sample can't establish a rate — no throughput/ETA series yet."""
    store = MetricStore()
    store.ingest(_row("start", counters={"entities_total": 100}))
    store.ingest(_row("progress", counters={"entities_processed": 5}))
    text = store.render()
    assert "bffi_stage_throughput_per_minute{" not in text
    assert "bffi_stage_eta_seconds{" not in text


def test_malformed_rows_are_skipped_not_raised() -> None:
    store = MetricStore()
    assert not store.ingest({"event": "start"})  # no stage / run_uuid / ts
    assert not store.ingest(_row("start", ts="not-a-timestamp"))
    assert not store.ingest(_row("unknown-event-kind"))
    assert not store.ingest({"stage": "s", "event": "start", "run_uuid": 7, "ts": "x"})


def test_replaying_the_same_row_is_idempotent() -> None:
    """Gauges hold absolute values, so a restart that re-reads a sidecar
    from offset 0 must not double-count."""
    store = MetricStore()
    row = _row("end", counters={"success": 7})
    store.ingest(row)
    store.ingest(row)
    assert f'outcome="success",run_uuid="{RUN}",stage="bibframe2bffi"}} 7.0' in store.render()


# --- SidecarTailer -------------------------------------------------------


def test_tailer_yields_only_new_rows_between_polls(tmp_path: Path) -> None:
    sidecar = tmp_path / "stage-events.jsonl"
    _write(sidecar, [_row("start", counters={"entities_total": 1})], mode="w")
    tailer = SidecarTailer()
    assert tailer.attach(sidecar)
    assert len(list(tailer.poll())) == 1
    # Nothing appended → nothing yielded.
    assert list(tailer.poll()) == []
    _write(sidecar, [_row("end", counters={"success": 1})])
    rows = list(tailer.poll())
    assert len(rows) == 1
    assert rows[0]["event"] == "end"


def test_tailer_attach_is_idempotent(tmp_path: Path) -> None:
    sidecar = tmp_path / "stage-events.jsonl"
    _write(sidecar, [_row("start")], mode="w")
    tailer = SidecarTailer()
    assert tailer.attach(sidecar)
    assert not tailer.attach(sidecar)
    assert tailer.attached == [sidecar]


def test_tailer_attach_returns_false_for_missing_file(tmp_path: Path) -> None:
    assert not SidecarTailer().attach(tmp_path / "absent.jsonl")


def test_tailer_carries_a_partial_trailing_line(tmp_path: Path) -> None:
    """A line caught mid-write must not be parsed until it's complete."""
    sidecar = tmp_path / "stage-events.jsonl"
    complete = json.dumps(_row("start", counters={"entities_total": 1}))
    sidecar.write_text(complete + "\n" + '{"ts":"2026-08-04T10:00:00Z","ru', encoding="utf-8")
    tailer = SidecarTailer()
    tailer.attach(sidecar)
    assert len(list(tailer.poll())) == 1
    # Finish the truncated line; now it parses.
    with sidecar.open("a", encoding="utf-8") as fh:
        fh.write('n_uuid":"' + RUN + '","stage":"bibframe2bffi","event":"end"}\n')
    rows = list(tailer.poll())
    assert len(rows) == 1
    assert rows[0]["event"] == "end"


def test_tailer_restarts_after_truncation(tmp_path: Path) -> None:
    sidecar = tmp_path / "stage-events.jsonl"
    _write(sidecar, [_row("start"), _row("progress", counters={"entities_processed": 1})], mode="w")
    tailer = SidecarTailer()
    tailer.attach(sidecar)
    assert len(list(tailer.poll())) == 2
    # Truncate to a single shorter row — size < offset triggers a re-read.
    _write(sidecar, [_row("end")], mode="w")
    rows = list(tailer.poll())
    assert [r["event"] for r in rows] == ["end"]


def test_tailer_drops_a_vanished_file(tmp_path: Path) -> None:
    sidecar = tmp_path / "stage-events.jsonl"
    _write(sidecar, [_row("start")], mode="w")
    tailer = SidecarTailer()
    tailer.attach(sidecar)
    list(tailer.poll())
    sidecar.unlink()
    assert list(tailer.poll()) == []
    assert tailer.attached == []


def test_tailer_skips_unparseable_lines(tmp_path: Path) -> None:
    sidecar = tmp_path / "stage-events.jsonl"
    sidecar.write_text("not json\n" + json.dumps(_row("start")) + "\n", encoding="utf-8")
    tailer = SidecarTailer()
    tailer.attach(sidecar)
    assert len(list(tailer.poll())) == 1


# --- Exporter ------------------------------------------------------------


def test_rescan_attaches_new_sidecars_only_once(tmp_path: Path) -> None:
    (tmp_path / "runs" / "run-a").mkdir(parents=True)
    first = tmp_path / "runs" / "run-a" / "stage-events.jsonl"
    _write(first, [_row("start", counters={"entities_total": 3})], mode="w")

    exporter = Exporter(globs=["runs/*/stage-events.jsonl"], root=tmp_path)
    assert exporter.rescan() == [first]
    assert exporter.rescan() == []

    # A run that starts after the exporter is picked up on the next rescan.
    (tmp_path / "runs" / "run-b").mkdir(parents=True)
    second = tmp_path / "runs" / "run-b" / "stage-events.jsonl"
    _write(second, [_row("start", counters={"entities_total": 5})], mode="w")
    assert exporter.rescan() == [second]


def test_tick_ingests_rows_and_counts_them(tmp_path: Path) -> None:
    (tmp_path / "runs" / "run-a").mkdir(parents=True)
    sidecar = tmp_path / "runs" / "run-a" / "stage-events.jsonl"
    _write(sidecar, [_row("start", counters={"entities_total": 3}), _row("end")], mode="w")
    exporter = Exporter(globs=["runs/*/stage-events.jsonl"], root=tmp_path)
    exporter.rescan()
    assert exporter.tick() == 2
    assert exporter.tick() == 0
    assert "bffi_stage_started_timestamp" in exporter.store.render()


def test_bookkeeping_files_are_written_and_cleared(tmp_path: Path) -> None:
    exporter = Exporter(globs=[], root=tmp_path)
    exporter.write_bookkeeping(["bffi-pipeline", "serve-metrics", "--port", "9100"])
    assert (tmp_path / PID_FILENAME).is_file()
    assert "serve-metrics" in (tmp_path / ARGV_FILENAME).read_text(encoding="utf-8")
    exporter.clear_bookkeeping()
    assert not (tmp_path / PID_FILENAME).exists()
    assert not (tmp_path / ARGV_FILENAME).exists()


def test_clear_bookkeeping_is_safe_when_files_are_already_gone(tmp_path: Path) -> None:
    """Crash recovery path: the operator may have removed them by hand."""
    Exporter(globs=[], root=tmp_path).clear_bookkeeping()
