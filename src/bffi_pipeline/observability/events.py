"""Structured stage-event emission.

One canonical event stream that every stage writes to during its run.
Operators tail the sidecar (by hand with ``tail -F``) to answer "is the
pipeline making forward progress?" without composing ``ps`` / ``curl`` /
``docker logs`` / log-grep against three different files.

The shape is a stderr line with a ``STAGE_EVENT `` prefix the existing
log-tail tooling can pick up on, plus an append to a JSONL sidecar at
``<BFFI_DATA_DIR>/stage-events.jsonl`` for post-run analysis. The
canonical payload shape:

::

    {
      "ts": "2026-05-13T05:13:36Z",
      "run_uuid": "01HXXX...",
      "stage": "bibframe2bffi",
      "event": "progress",
      "phase": "phase1",
      "counters": {"processed": 9876, "total": 12666},
      "extra": {"tier0_local": 7421, "no_candidate": 1893}
    }

Module-level active-emitter singleton: the CLI subcommand at entry
calls :func:`set_active_emitter` with a configured
:class:`StageEventEmitter`; stages call :func:`get_active_emitter` and,
if the result is non-None, emit. Stages don't need to thread the
emitter through their function signatures, which would have rippled
through every ``run()`` and every test fixture.

Thread safety: ``StageEventEmitter.emit`` is guarded by an internal
``threading.Lock`` so a concurrent picker pool and Phase 1
pool can call it concurrently without interleaving stderr lines or
JSONL appends. ``set_active_emitter`` is *not* thread-safe and is
expected to be called once at CLI entry before any worker dispatch.
"""

from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal

from bffi_pipeline.run_manifest import append_stage_completed, append_stage_observed

StageEvent = Literal[
    "start",
    "progress",
    "phase_boundary",
    "end",
    "plan",
    "skipped",
    "failed",
]

#: stderr prefix for every stage event.
STAGE_EVENT_STDERR_PREFIX: Final[str] = "STAGE_EVENT "

#: Max length for the truncated ``message`` field on ``failed`` events.
#: Picked to keep stage-events.jsonl rows under ~1 KB even with a long
#: error message and the surrounding payload. Operator can dig into the
#: original exception via the captured run log when the truncation
#: matters.
_FAILED_MESSAGE_MAX_LEN: Final[int] = 240


@dataclass
class StageEventEmitter:
    """One emitter per pipeline invocation.

    Constructed by the CLI subcommand at entry. ``sidecar_path`` is the
    canonical ``<BFFI_DATA_DIR>/stage-events.jsonl`` location for
    production; tests pass ``None`` and assert against the stderr
    capture only.

    ``run_uuid`` anchors every event from one CLI invocation; the
    Grafana dashboard (Phase D) uses it to filter views to the current
    run, and the status CLI (Phase B) uses it to scope ``--since now``.
    """

    sidecar_path: Path | None
    run_uuid: str
    #: Path to the run's ``bffi-run.json`` manifest. When
    #: set, each ``start`` / ``end`` event also appends the stage to the
    #: manifest's ``stages_observed`` / ``stages_completed`` list. None
    #: in tests that construct emitters directly + don't want manifest
    #: side-effects.
    manifest_path: Path | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def emit(
        self,
        *,
        stage: str,
        event: StageEvent,
        phase: str | None = None,
        counters: dict[str, int] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Emit one event to stderr and (if configured) to the JSONL sidecar.

        Concurrent calls are serialised by an internal lock — a
        c=4 picker pool + phase1=8 Phase 1 pool can call this from
        multiple threads without interleaving lines.

        ``ts`` is always set to ``datetime.now(UTC)`` formatted as
        ISO-8601 with second precision; callers can't override it
        (avoids the temptation to back-date events for "alignment"
        which then breaks the throughput-derivation math in Phase B's
        ETA calculation).
        """
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "run_uuid": self.run_uuid,
            "stage": stage,
            "event": event,
        }
        if phase is not None:
            payload["phase"] = phase
        if counters is not None:
            payload["counters"] = counters
        if extra is not None:
            payload["extra"] = extra
        line = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

        with self._lock:
            print(f"{STAGE_EVENT_STDERR_PREFIX}{line}", file=sys.stderr, flush=True)
            if self.sidecar_path is not None:
                self.sidecar_path.parent.mkdir(parents=True, exist_ok=True)
                with self.sidecar_path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")

        # Update the run's manifest with the stage's
        # lifecycle markers. Idempotent on retries — same stage
        # emitting ``start`` twice writes one entry. Done outside the
        # emitter lock because the manifest helpers carry their own
        # module-level lock (and the JSONL append finished by the
        # time we get here).
        if self.manifest_path is not None:
            if event == "start":
                append_stage_observed(self.manifest_path, stage)
            elif event == "end":
                append_stage_completed(self.manifest_path, stage)


#: Module-level singleton slot. ``None`` when no pipeline invocation is
#: active — stages that look it up via :func:`get_active_emitter` and
#: find ``None`` skip emission silently (no crash, no opt-in required).
_active_emitter: StageEventEmitter | None = None


def set_active_emitter(emitter: StageEventEmitter | None) -> None:
    """Set the process-wide active emitter.

    Called by CLI subcommands at entry once they've constructed an
    emitter from ``settings.observability_sidecar`` + ``settings.run_uuid``.
    Passing ``None`` explicitly clears the slot — useful when a test
    needs to verify the "no emitter" path between assertions.

    Not thread-safe; expected to be called once at CLI entry before
    any worker dispatch.
    """
    global _active_emitter  # noqa: PLW0603 — module-level singleton by design; the alternative (thread-local or per-call plumbing) would force every stage's signature to thread the emitter through.
    _active_emitter = emitter


def get_active_emitter() -> StageEventEmitter | None:
    """Return the active emitter (or ``None`` if not set).

    Stages call this and, if non-None, call ``emitter.emit(...)``.
    Pattern at the call site:

    .. code-block:: python

        emitter = get_active_emitter()
        if emitter is not None:
            emitter.emit(stage="bibframe2bffi", event="progress", ...)
    """
    return _active_emitter


def emit_if_active(
    *,
    stage: str,
    event: StageEvent,
    phase: str | None = None,
    counters: dict[str, int] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Convenience helper: emit via the active emitter if one is set.

    Reduces call-site boilerplate from "fetch emitter → null-check →
    call emit" to one line. Stages can use either pattern; this helper
    is the ergonomic default.
    """
    emitter = _active_emitter
    if emitter is not None:
        emitter.emit(
            stage=stage,
            event=event,
            phase=phase,
            counters=counters,
            extra=extra,
        )


def emit_plan(
    stages: list[str],
    description: str = "",
    stage_phases: dict[str, list[str]] | None = None,
) -> None:
    """Emit a single ``plan`` event listing every stage the active run
    intends to execute.

    Runner scripts call this once at start (typically via the
    ``bffi-pipeline plan`` CLI shim) so the dashboard's state tiles
    can distinguish "pending" (stage planned, not yet started) from
    "skipped" (stage not in plan at all). Direct ``bffi-pipeline
    <subcmd>`` invocations don't emit a plan; their non-active
    stages correctly stay "skipped" in the dashboard view.

    Optional ``description`` is a free-text label (e.g. "Full pipeline
    on /data/marcxml-2026-Q2") that the dashboard surfaces in the
    header tile. Stored as a label on the ``bffi_run_description``
    gauge so Grafana's templating layer can interpolate it.

    Optional ``stage_phases`` maps each planned stage to its phase
    sequence (e.g. ``{"bibframe2bffi": ["rename", "route"]}``). The
    metrics exporter pre-creates ``bffi_stage_phase_planned{stage,
    phase}=1`` gauges from this so the dashboard can render 0%-valued
    pending bars for not-yet-started phases instead of "—" no-data
    tiles. Stages with only an implicit phase pass ``["_"]``.

    No-op when no emitter is active (test fixtures that bypass the
    CLI bootstrap).
    """
    emitter = _active_emitter
    if emitter is not None:
        extra: dict[str, Any] = {"stages": list(stages)}
        if description:
            extra["description"] = description
        if stage_phases:
            extra["stage_phases"] = {stage: list(phases) for stage, phases in stage_phases.items()}
        emitter.emit(
            stage="pipeline",
            event="plan",
            extra=extra,
        )


def emit_failed(
    stage: str,
    *,
    phase: str | None = None,
    error_type: str = "",
    message: str = "",
) -> None:
    """Emit a ``failed`` event marking the stage (or a specific phase
    within it) as terminally failed for this run.

    The runner calls this when a dispatched stage raises an exception
    before re-raising; stages themselves can call it from a
    try/finally if they want phase-level failure granularity.
    ``error_type`` is the exception class
    name; ``message`` is the truncated str(exc) — the dashboard reads
    both as labels on ``bffi_stage_failed`` so the operator can
    tell apart a ``TimeoutError`` from a ``RuntimeError`` without
    leaving the dashboard.

    ``phase`` is optional. The runner sets it to ``None`` because it
    doesn't track per-phase state — the exporter routes that case to
    ``phase="_"`` to align with the existing phase-less metric label
    convention.

    No-op when no emitter is active (parity with :func:`emit_plan`).
    """
    emitter = _active_emitter
    if emitter is None:
        return
    extra: dict[str, Any] = {}
    if error_type:
        extra["error_type"] = error_type
    if message:
        truncated = message[:_FAILED_MESSAGE_MAX_LEN]
        if len(message) > _FAILED_MESSAGE_MAX_LEN:
            truncated += "…"
        extra["message"] = truncated
    emitter.emit(
        stage=stage,
        event="failed",
        phase=phase,
        extra=extra or None,
    )


def emit_skipped(stage: str, reason: str = "") -> None:
    """Emit a ``skipped`` event for a stage the runner decided not to run.

    Layered on top of :func:`emit_plan`: the plan declares which stages
    *intended* to run; ``skipped`` records that the runner explicitly
    chose not to dispatch one of them (operator passed ``--skip``,
    ``--from-stage`` cut it off the chain, output-fresh idempotency
    elided it, etc.). The reason string is the load-bearing audit
    detail — the dashboard's tile tooltip surfaces it so the operator
    can tell "intentionally skipped" apart from "failed before this
    stage could start".

    No-op when no emitter is active (parity with :func:`emit_plan`).
    """
    emitter = _active_emitter
    if emitter is None:
        return
    extra: dict[str, Any] | None = {"reason": reason} if reason else None
    emitter.emit(stage=stage, event="skipped", extra=extra)


__all__ = [
    "STAGE_EVENT_STDERR_PREFIX",
    "StageEvent",
    "StageEventEmitter",
    "emit_failed",
    "emit_if_active",
    "emit_plan",
    "emit_skipped",
    "get_active_emitter",
    "set_active_emitter",
]
