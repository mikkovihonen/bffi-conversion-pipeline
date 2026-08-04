"""Prometheus exporter that tails ``stage-events.jsonl`` sidecars.

The stage side of observability appends one JSON object per event to
``runs/<run_uuid>/stage-events.jsonl`` (see
:mod:`bffi_pipeline.observability.events`). This module is the other half
of that contract: it tails those files and publishes the metric
vocabulary ``docs/observability.md`` specifies, on a local ``/metrics``
endpoint the operator's own Prometheus container scrapes.

Three separable pieces:

* :class:`MetricStore` — pure translation. One sidecar row in, gauge
  updates out. No I/O, so it is directly unit-testable.
* :class:`SidecarTailer` — byte-offset tracking per file, keyed on
  ``(st_dev, st_ino)`` so rotation and truncation are detected. Yields
  complete JSON rows only; a partially-written trailing line is carried
  over to the next poll.
* :class:`Exporter` — glob rescan, poll loop, PID/argv bookkeeping.

**Every metric is a Gauge**, including the ``_total``-suffixed ones. The
sidecar carries absolute cumulative counter values, so replaying it into
a ``Counter`` (which can only ``inc``) would double-count whenever the
exporter restarts and re-reads a sidecar from offset 0. A Gauge holding
the last observed absolute value is the honest primitive; the ``_total``
names are kept because the dashboard queries them.

No outbound telemetry: nothing here connects out, it only listens.
"""

from __future__ import annotations

import atexit
import json
import os
import signal
import sys
import time
from collections import deque
from collections.abc import Iterator, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Any, Final

from prometheus_client import CollectorRegistry, Gauge, generate_latest, start_http_server

#: Label value for stages without internal phases. Matches the sentinel
#: ``docs/observability.md`` documents for the ``phase`` label.
PHASE_SENTINEL: Final[str] = "_"

#: Minimum progress events before a throughput / ETA rate is derivable.
_MIN_WINDOW: Final[int] = 2

#: Progress events retained per ``(run_uuid, stage, phase)`` for the
#: throughput / ETA derivations. Five is enough to smooth a single slow
#: record without lagging a genuine slowdown by minutes.
PROGRESS_WINDOW: Final[int] = 5

#: Sidecar ``ts`` format, written by ``StageEventEmitter.emit``.
TS_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%SZ"

#: Default glob for sidecars under the runs root.
DEFAULT_WATCH_GLOB: Final[str] = "runs/*/stage-events.jsonl"

#: Operator bookkeeping files, written beside the runs root on launch.
PID_FILENAME: Final[str] = ".exporter.pid"
ARGV_FILENAME: Final[str] = ".exporter.argv"


def _as_dict(value: object) -> dict[str, Any]:
    """Coerce a sidecar sub-object to a string-keyed dict (``{}`` if absent)."""
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    return {}


def parse_ts(raw: str) -> float:
    """Parse a sidecar ``ts`` into a Unix timestamp.

    Raises :exc:`ValueError` on a malformed value — callers treat that as
    a skipped row rather than crashing the poll loop.
    """
    return datetime.strptime(raw, TS_FORMAT).replace(tzinfo=UTC).timestamp()


@dataclass(frozen=True)
class _Series:
    """Key for the derived-metric sliding window."""

    run_uuid: str
    stage: str
    phase: str


class MetricStore:
    """Translates sidecar rows into Prometheus gauges.

    Holds its own :class:`CollectorRegistry` rather than the process-wide
    default so tests can build a throwaway store and so two stores never
    collide on duplicate metric registration.
    """

    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        r = self.registry
        run = ["run_uuid"]
        by_stage = ["stage", *run]
        by_phase = ["stage", "phase", *run]

        self.started = Gauge(
            "bffi_stage_started_timestamp", "Unix ts the stage began.", by_stage, registry=r
        )
        self.ended = Gauge(
            "bffi_stage_ended_timestamp", "Unix ts the stage finished.", by_stage, registry=r
        )
        self.entities_total = Gauge(
            "bffi_stage_entities_total",
            "Total entities the stage/phase will process.",
            by_phase,
            registry=r,
        )
        self.entities_processed = Gauge(
            "bffi_stage_entities_processed_total",
            "Entities processed so far (absolute, not a counter delta).",
            by_phase,
            registry=r,
        )
        self.outcomes = Gauge(
            "bffi_stage_outcomes_total",
            "Per-outcome bucket counts from the stage's end event.",
            ["stage", "outcome", *run],
            registry=r,
        )
        self.errors = Gauge(
            "bffi_stage_errors_total",
            "Failed-event count per exception class.",
            ["stage", "error_type", *run],
            registry=r,
        )
        self.failed = Gauge(
            "bffi_stage_failed",
            "1 when the stage (or phase) failed terminally.",
            ["stage", "phase", "error_type", "message", *run],
            registry=r,
        )
        self.skipped = Gauge(
            "bffi_stage_skipped",
            "1 when the runner explicitly skipped the stage.",
            ["stage", "reason", *run],
            registry=r,
        )
        self.planned = Gauge(
            "bffi_stage_planned",
            "1 for every stage the run intends to execute.",
            by_stage,
            registry=r,
        )
        self.phase_planned = Gauge(
            "bffi_stage_phase_planned",
            "1 for every planned (stage, phase) pair.",
            by_phase,
            registry=r,
        )
        self.run_description = Gauge(
            "bffi_run_description",
            "1, carrying the run's free-text label.",
            ["description", *run],
            registry=r,
        )
        self.throughput = Gauge(
            "bffi_stage_throughput_per_minute",
            "Rolling-window throughput from the last progress events.",
            by_phase,
            registry=r,
        )
        self.eta = Gauge(
            "bffi_stage_eta_seconds",
            "Linear-extrapolation ETA to phase boundary or stage end.",
            by_phase,
            registry=r,
        )

        #: Sliding window of ``(ts, processed)`` per series, for the
        #: throughput / ETA derivations.
        self._progress: dict[_Series, deque[tuple[float, float]]] = {}
        #: Last known ``entities_total`` per series, so ETA can be derived
        #: from a progress event alone.
        self._totals: dict[_Series, float] = {}

    # --- ingestion --------------------------------------------------------

    def ingest(self, row: dict[str, Any]) -> bool:
        """Apply one sidecar row. Returns False if the row was unusable.

        Malformed rows are skipped rather than raised: the exporter must
        survive a half-written line or a hand-edited sidecar without
        taking the whole poll loop down.
        """
        stage = row.get("stage")
        event = row.get("event")
        run_uuid = row.get("run_uuid")
        if not isinstance(stage, str) or not isinstance(event, str):
            return False
        if not isinstance(run_uuid, str):
            return False
        try:
            ts = parse_ts(str(row.get("ts", "")))
        except ValueError:
            return False

        phase_raw = row.get("phase")
        phase = phase_raw if isinstance(phase_raw, str) and phase_raw else PHASE_SENTINEL
        counters = _as_dict(row.get("counters"))
        extra = _as_dict(row.get("extra"))
        series = _Series(run_uuid=run_uuid, stage=stage, phase=phase)

        if event == "start":
            self.started.labels(stage=stage, run_uuid=run_uuid).set(ts)
            self._set_total(series, counters)
        elif event == "phase_boundary":
            self._set_total(series, counters)
        elif event == "progress":
            self._on_progress(series, ts, counters)
        elif event == "end":
            self.ended.labels(stage=stage, run_uuid=run_uuid).set(ts)
            self._on_end(stage, run_uuid, counters)
        elif event == "failed":
            self._on_failed(stage, phase, run_uuid, extra)
        elif event == "skipped":
            reason = str(extra.get("reason", ""))
            self.skipped.labels(stage=stage, reason=reason, run_uuid=run_uuid).set(1)
        elif event == "plan":
            self._on_plan(run_uuid, extra)
        else:
            return False
        return True

    def _set_total(self, series: _Series, counters: dict[str, Any]) -> None:
        raw = counters.get("entities_total")
        if not isinstance(raw, int | float):
            return
        total = float(raw)
        self._totals[series] = total
        self.entities_total.labels(
            stage=series.stage, phase=series.phase, run_uuid=series.run_uuid
        ).set(total)

    def _on_progress(self, series: _Series, ts: float, counters: dict[str, Any]) -> None:
        raw = counters.get("entities_processed")
        if not isinstance(raw, int | float):
            return
        processed = float(raw)
        self.entities_processed.labels(
            stage=series.stage, phase=series.phase, run_uuid=series.run_uuid
        ).set(processed)

        window = self._progress.setdefault(series, deque(maxlen=PROGRESS_WINDOW))
        window.append((ts, processed))
        if len(window) < _MIN_WINDOW:
            return
        (t0, p0), (t1, p1) = window[0], window[-1]
        elapsed = t1 - t0
        done = p1 - p0
        if elapsed <= 0 or done <= 0:
            return
        per_second = done / elapsed
        self.throughput.labels(
            stage=series.stage, phase=series.phase, run_uuid=series.run_uuid
        ).set(per_second * 60.0)

        total = self._totals.get(series)
        if total is not None and total > p1:
            self.eta.labels(stage=series.stage, phase=series.phase, run_uuid=series.run_uuid).set(
                (total - p1) / per_second
            )

    def _on_end(self, stage: str, run_uuid: str, counters: dict[str, Any]) -> None:
        for outcome, value in counters.items():
            if isinstance(value, int | float):
                self.outcomes.labels(stage=stage, outcome=outcome, run_uuid=run_uuid).set(
                    float(value)
                )

    def _on_failed(self, stage: str, phase: str, run_uuid: str, extra: dict[str, Any]) -> None:
        error_type = str(extra.get("error_type", ""))
        message = str(extra.get("message", extra.get("error", "")))
        self.failed.labels(
            stage=stage,
            phase=phase,
            error_type=error_type,
            message=message,
            run_uuid=run_uuid,
        ).set(1)
        # One sidecar row per failed record, so accumulate rather than set.
        child = self.errors.labels(stage=stage, error_type=error_type, run_uuid=run_uuid)
        child.inc()

    def _on_plan(self, run_uuid: str, extra: dict[str, Any]) -> None:
        stages = extra.get("stages")
        if isinstance(stages, list):
            for stage in stages:
                if isinstance(stage, str):
                    self.planned.labels(stage=stage, run_uuid=run_uuid).set(1)
        phases = extra.get("stage_phases")
        if isinstance(phases, dict):
            for stage, phase_list in phases.items():
                if not isinstance(stage, str) or not isinstance(phase_list, list):
                    continue
                for phase in phase_list:
                    if isinstance(phase, str):
                        self.phase_planned.labels(stage=stage, phase=phase, run_uuid=run_uuid).set(
                            1
                        )
        description = extra.get("description")
        if isinstance(description, str) and description:
            self.run_description.labels(description=description, run_uuid=run_uuid).set(1)

    def render(self) -> str:
        """Return the ``/metrics`` exposition text (used by tests)."""
        return generate_latest(self.registry).decode("utf-8")


@dataclass
class _Attached:
    """Tail state for one sidecar file."""

    path: Path
    key: tuple[int, int]
    offset: int = 0
    carry: str = ""


class SidecarTailer:
    """Tracks byte offsets across polls for a set of sidecar files."""

    def __init__(self) -> None:
        self._files: dict[Path, _Attached] = {}

    @property
    def attached(self) -> list[Path]:
        return sorted(self._files)

    def attach(self, path: Path) -> bool:
        """Start tailing ``path`` from offset 0. Returns False if already attached."""
        if path in self._files:
            return False
        try:
            st = path.stat()
        except OSError:
            return False
        self._files[path] = _Attached(path=path, key=(st.st_dev, st.st_ino))
        return True

    def poll(self) -> Iterator[dict[str, Any]]:
        """Yield every complete JSON row appended since the last poll.

        A trailing partial line is carried over — the emitter appends
        under a lock but the reader can still catch a line mid-write.
        """
        for state in list(self._files.values()):
            yield from self._poll_one(state)

    def _poll_one(self, state: _Attached) -> Iterator[dict[str, Any]]:
        try:
            st = state.path.stat()
        except OSError:
            # File vanished (run directory pruned). Drop it silently.
            self._files.pop(state.path, None)
            return
        key = (st.st_dev, st.st_ino)
        if key != state.key or st.st_size < state.offset:
            # Rotated or truncated — restart from the beginning.
            state.key = key
            state.offset = 0
            state.carry = ""
        if st.st_size == state.offset:
            return
        try:
            with state.path.open("rb") as fh:
                fh.seek(state.offset)
                chunk = fh.read()
        except OSError:
            return
        state.offset += len(chunk)
        text = state.carry + chunk.decode("utf-8", errors="replace")
        parts = text.split("\n")
        state.carry = parts.pop()
        for raw_line in parts:
            line = raw_line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                yield parsed


@dataclass
class Exporter:
    """Glob rescan + poll loop around a :class:`MetricStore`."""

    globs: Sequence[str]
    root: Path
    store: MetricStore = field(default_factory=MetricStore)
    tailer: SidecarTailer = field(default_factory=SidecarTailer)

    def rescan(self) -> list[Path]:
        """Attach any glob match not already tailed. Returns the new paths."""
        fresh: list[Path] = []
        for pattern in self.globs:
            for match in sorted(self.root.glob(pattern)):
                if match.is_file() and self.tailer.attach(match):
                    fresh.append(match)
        return fresh

    def tick(self) -> int:
        """Ingest every row available right now. Returns rows applied."""
        return sum(1 for row in self.tailer.poll() if self.store.ingest(row))

    # --- operator bookkeeping --------------------------------------------

    @property
    def pid_path(self) -> Path:
        return self.root / PID_FILENAME

    @property
    def argv_path(self) -> Path:
        return self.root / ARGV_FILENAME

    def write_bookkeeping(self, argv: Sequence[str]) -> None:
        """Record PID + argv so an operator reset can relaunch identically."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
        self.argv_path.write_text(" ".join(argv) + "\n", encoding="utf-8")

    def clear_bookkeeping(self) -> None:
        """Remove the PID/argv files. Best-effort — the operator may have already."""
        for path in (self.pid_path, self.argv_path):
            with suppress(OSError):
                path.unlink()

    def serve_forever(
        self,
        *,
        port: int,
        poll_seconds: float = 1.0,
        rescan_seconds: float = 30.0,
    ) -> None:  # pragma: no cover - blocking loop, exercised by hand
        """Bind ``port`` and poll until SIGTERM / SIGINT / KeyboardInterrupt."""
        start_http_server(port, registry=self.store.registry)
        self.write_bookkeeping(sys.argv)
        atexit.register(self.clear_bookkeeping)

        stopping = False

        def _stop(signum: int, frame: FrameType | None) -> None:
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        self.rescan()
        self.tick()
        last_rescan = time.monotonic()
        try:
            while not stopping:
                time.sleep(poll_seconds)
                now = time.monotonic()
                if now - last_rescan >= rescan_seconds:
                    self.rescan()
                    last_rescan = now
                self.tick()
        except KeyboardInterrupt:
            pass
        finally:
            self.clear_bookkeeping()


__all__ = [
    "ARGV_FILENAME",
    "DEFAULT_WATCH_GLOB",
    "PHASE_SENTINEL",
    "PID_FILENAME",
    "PROGRESS_WINDOW",
    "TS_FORMAT",
    "Exporter",
    "MetricStore",
    "SidecarTailer",
    "parse_ts",
]
