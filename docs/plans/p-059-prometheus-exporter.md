# p-059 — Implement the Prometheus exporter behind `serve-metrics`

**Status: active.** Phase A (this plan) ships the exporter.

## Problem

`bffi-pipeline serve-metrics` was a scaffold that raised
`NotImplementedError`. The stage side of observability was live — every
stage appends to `runs/<run>/stage-events.jsonl` — but nothing translated
those events into Prometheus metrics, so the Grafana dashboard rendered
no data and `make observability-up` started a process that died
immediately.

`docs/observability.md` already specifies the metric vocabulary, the
label cardinality, and the exporter lifecycle. This plan implements that
specification rather than inventing a new one.

## Design

New module `src/bffi_pipeline/observability/exporter.py`, three pieces
that are separately testable:

| Piece | Responsibility |
|---|---|
| `MetricStore` | Pure translation: one sidecar row in → gauge updates on a private `CollectorRegistry`. No I/O. |
| `SidecarTailer` | Per-file `(st_dev, st_ino)` + byte-offset tracking; yields complete JSON rows only. Survives truncation and rotation. |
| `Exporter` | Glob rescan, poll loop, PID/argv files, atexit cleanup. |

**Every metric is a Gauge**, including the `_total`-suffixed ones. The
sidecar carries *absolute cumulative* counter values, so replaying it
into a `Counter` (which can only `inc`) would double-count on exporter
restart. A Gauge holding the last observed absolute value is the honest
primitive. The `_total` names are kept because the dashboard and
`docs/observability.md` already specify them.

Derived metrics (`bffi_stage_throughput_per_minute`,
`bffi_stage_eta_seconds`) come from a 5-event sliding window of
`progress` rows per `(run_uuid, stage, phase)`.

Phase label defaults to the `_` sentinel for stages without internal
phases, matching the existing convention.

## Trade-offs on the record

- **Gauge-not-Counter** as above. Deviation from the `Counter` column in
  `docs/observability.md`; the doc is updated to match.
- **No socket binding in tests.** `MetricStore` / `SidecarTailer` /
  `Exporter.tick` are driven directly against `tmp_path` fixtures. The
  HTTP bind is a two-line call into `prometheus_client`; exercising it
  would mean listening on a port from the unit suite, which the
  "tests never hit the network" rule rules out.
- **Rehydration is just a cold tail.** On launch the exporter attaches
  every glob match at offset 0, so restarting rebuilds full state from
  the sidecars. No separate rehydrate path.

## Out of scope

- Pushing metrics anywhere. The exporter serves `/metrics` on localhost
  for the operator's own Prometheus to scrape; no outbound telemetry.
- A `status` CLI. The sidecar plus `tail -F` covers that need today.
