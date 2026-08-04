# Pipeline observability — local Prometheus + Grafana via Caddy

This repository builds observability in from the ground up. Every stage emits structured events to a JSONL sidecar from its first commit; an exporter tails the sidecars and serves Prometheus metrics; Grafana renders the operator dashboard; Caddy fronts both behind a single `http://localhost:8080` URL.

**Status: the exporter is not implemented yet.** `bffi-pipeline serve-metrics` is a scaffold that raises `NotImplementedError`. The stage side is live — every stage writes `stage-events.jsonl` — but nothing publishes Prometheus metrics, so the Grafana dashboard renders no data until the exporter lands. The metric vocabulary below is the contract it must satisfy, not a description of running code.

**No outbound telemetry.** The whole stack runs in Docker Compose on the operator's machine; no data leaves the box.

## Architecture

```
pipeline stages ─emit→ stage-events.jsonl ─tail→ metrics-exporter ─scrape→ Prometheus ─query→ Grafana
                       (per-run sidecar)         (host :9100)              (container)    (container)
                                                                                │             │
                                                                                └─────────────┴──→ Caddy
                                                                                                   127.0.0.1:8080
                                                                                                   /  → Grafana
                                                                                                   /prometheus/
                                                                                                   /files/ → runs/
```

All local. The exporter runs on the host alongside the conversion pipeline (it shares the data dir directly, no volume mapping); Prometheus, Grafana, and Caddy run as Docker Compose services. Operators reach the stack through a single URL; Caddy routes paths to the right backend and serves the `runs/` directory as static files so per-record diff artifacts are clickable from dashboard links.

## Event sidecar (the contract)

Every stage writes JSON-lines to `runs/<run_uuid>/stage-events.jsonl`. Events are append-only, schema-versioned, and small. Event types:

| Event | When | Required fields | Notes |
|---|---|---|---|
| `start` | stage begin | `stage`, `run_uuid`, `ts`, `entities_total` | `entities_total` is best-effort for stages where the count is knowable at start. |
| `progress` | every N entities (default 100) | `stage`, `run_uuid`, `ts`, `entities_processed` | Drives throughput + ETA derivations. |
| `phase_boundary` | sub-phase transition within a stage | `stage`, `run_uuid`, `ts`, `phase`, `entities_total` | Optional; only stages with internal phases emit these. |
| `end` | stage finish | `stage`, `run_uuid`, `ts`, `outcomes: {bucket: count}` | Outcome buckets are stage-specific (see § Metric vocabulary). |

Adding a new event type is a code change; the metric vocabulary table below is the spec the stage side, exporter, and dashboard must agree on.

## Metric vocabulary

The exporter publishes the canonical metric set below. Every metric maps one-to-one to a sidecar event field.

| Metric | Type | Labels | Source event | What it means |
|---|---|---|---|---|
| `bffi_stage_started_timestamp` | Gauge | `stage`, `run_uuid` | `start` | Unix ts the stage began. |
| `bffi_stage_ended_timestamp` | Gauge | `stage`, `run_uuid` | `end` | Unix ts the stage finished. |
| `bffi_stage_entities_total` | Gauge | `stage`, `phase`, `run_uuid` | `start` / `phase_boundary` | Total entities the stage/phase will process. |
| `bffi_stage_entities_processed_total` | Counter | `stage`, `phase`, `run_uuid` | `progress` | Cumulative entities processed so far. |
| `bffi_stage_outcomes_total` | Counter | `stage`, `outcome`, `run_uuid` | `end` | Per-outcome bucket counts (e.g. for the BFFI conversion stage: `hub_routed_work`, `hub_routed_expression`, `identifier_isbn`, `identifier_issn`, `title_variant`, `series_link`, `music_medium_collapse`, `validation_failed`, …). |
| `bffi_stage_throughput_per_minute` | Gauge | `stage`, `phase`, `run_uuid` | derived | Rolling-window throughput from the last 5 progress events. |
| `bffi_stage_eta_seconds` | Gauge | `stage`, `phase`, `run_uuid` | derived | Linear-extrapolation ETA to phase boundary or stage end. |

### Label cardinality

- `stage` ∈ {`melinda-sync`, `marc2bibframe`, `bibframe2bffi`, `bffi2marc`, `roundtrip_eval`} — this repository's ingestion + bidirectional-conversion stage set. `bffi2marc` is the reverse direction (BFFI graph → reconstructed MARCXML); `roundtrip_eval` is the diff harness that compares reconstructed MARC against the source. Extend as new stages land.
- `phase` ∈ {`_`, `phase1`, …}. `_` is the sentinel for stages without internal phases.
- `outcome` is per-stage but bounded; the conversion stage's outcomes are the discriminator-routing buckets (one per row in the bf → bffi mapping doc's routing callouts) plus `validation_failed`.
- `run_uuid` is one value per pipeline invocation.

Cardinality cap (per `run_uuid`): ~5 stages × ~3 phases + ~15 outcomes ≈ 30 series. Multiplied by accumulated runs in the current exporter session — well within Prometheus's comfort zone for any realistic dev / production cadence.

## Exporter lifecycle

The exporter is a small Python program. Five phases:

| Phase | Trigger | What happens |
|---|---|---|
| **Launch** | CLI command (e.g. `bffi-pipeline serve-metrics`) | Rehydrate every attached sidecar JSONL into the in-memory registry; bind `:9100`; write `<runs-root>/.exporter.pid` + `.exporter.argv`; register the atexit cleanup hook; enter the tail loop. |
| **Steady state** | tail loop, `poll_seconds=1.0` | Per attached sidecar: read new lines via inode + byte offset (no double-counting). Every `glob_rescan_seconds=30s` the `--watch-glob` patterns re-list and auto-attach new matches. |
| **Clean shutdown** | SIGTERM / SIGINT / interpreter exit | atexit hook removes `.exporter.pid` + `.exporter.argv` (best-effort; OSError silently swallowed if the operator already cleaned them). |
| **Operator reset** | explicit reset command | Reads PID file, SIGTERMs the process, waits up to 10 s for clean exit, then relaunches with the recorded argv. |
| **Crash recovery** | Next reset after SIGKILL / OOM / container halt | Process-alive check returns False → unlink the stale PID file, log warning, skip. No manual `rm` needed. |

PID + argv files sit at `<runs-root>/.exporter.pid` and `.exporter.argv` — process-global, not per-run, because the exporter is multi-tenant over its sidecars.

## Operating modes

**Mode A — ambient observer (default):** one long-lived exporter with `--watch-glob 'runs/*/stage-events.jsonl'`. Initial walk attaches every existing sidecar; the 30 s rescan picks up new runs as they spawn. The dashboard's `active_run` dropdown shows new runs within ~30 s — no exporter restart needed.

**Mode B — per-run focused bench:** single `--sidecar runs/<uuid>/stage-events.jsonl`. Useful for an isolated A/B test where you want zero noise from other runs. Operator kills the exporter when done.

Mode A is the durable shape. The exporter never owns the run — it's a passive tail of the run's on-disk JSONL output.

## Counter inheritance across exporter restarts

Counters are cumulative within a single exporter process. Each invocation starts from zero, rehydrates the full sidecar once, then tails for new events. Restarting the exporter resets every counter to "sum of sidecar events on disk at startup" — the displayed numbers can drop visibly on the dashboard at the restart boundary.

This is by design: rehydration replays the JSONL ground truth. PromQL queries spanning an exporter restart should use `increase(...[5m])` or `rate(...[5m])` rather than raw counter values across the boundary. The Grafana dashboard's rate panels do this already; the cumulative-count panels show the by-design discontinuity.

## Per-run metric isolation

Every Counter and Gauge carries an explicit `run_uuid` label. Dashboards filter every panel by `run_uuid="$active_run"`, where `$active_run` is a Grafana templating variable populated by `label_values(bffi_stage_started_timestamp, run_uuid)`. The operator picks the run they want to watch; panels re-render against that single `run_uuid`. Prior runs' data remains queryable in Prometheus under their own values for forensic comparison.

## Bundled Grafana dashboard

Auto-loaded from `config/grafana/dashboards/bffi-pipeline.json` at container start (via `config/grafana/provisioning/`). Read-only in the UI; operators clone-and-edit if they want a custom view.

Initial panel set for the bidirectional-conversion scope:

| Panel | Type | What it shows |
|---|---|---|
| Pipeline overview | Stat × 5 | One tile per stage (Export / marc2bibframe / BIBFRAME→BFFI / BFFI→MARC / Round-trip eval). Coloured green if running, blue if done, grey if idle. Filtered to the active run. |
| Forward-conversion progress | Stat | Processed / total for the BIBFRAME → BFFI stage. |
| Forward-conversion ETA | Stat | Linear-extrapolation ETA. |
| Forward-conversion throughput | Stat | Records per minute over the last 5 progress events. |
| Routing outcome distribution | Bar gauge | Per-outcome counts after BIBFRAME → BFFI ends (hub_routed_work, hub_routed_expression, identifier_isbn, …). |
| Reverse-conversion progress | Stat | Processed / total for the BFFI → MARC stage. |
| Round-trip diff residue | Stat | After `roundtrip_eval`: counts of records by diff status (`identical` / `changed` / `lost` / `tag-changed` / `marckey-bypass`); clickable through to the cataloguer-review HTML via Caddy's `/files/` mount. |
| Per-stage throughput | Time series | All stages — overlay view of who's currently moving. |
| Validation residue | Stat | Count of records with non-zero `_validation.jsonl` rows from the forward conversion. |

## Extending

Adding a new metric is two edits:

1. **Stage side**: extend the relevant event's payload at the `emit_if_active` call.
2. **Exporter side**: declare the metric in the exporter's metric set and route the event's payload to it.
3. **Dashboard** (optional): add a panel to the JSON.

The metric vocabulary table above is the spec. Bump the table first; the code follows.

## Outstanding

- Implement the exporter behind `bffi-pipeline serve-metrics` so the metric
  vocabulary above becomes real. Until then the dashboard is a specification.
