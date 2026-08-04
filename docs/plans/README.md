# Plans

Index of plans of record for the BFFI conversion pipeline.

## Convention

- One plan per file: `docs/plans/p-NNN-<slug>.md` (three-digit zero-padded; flat structure — no sub-folders).
- Status is tracked **in this index**, not by sub-folder location. Update the status column when a plan moves between states.
- Filenames stay stable across status transitions (no `git mv`); the history of a single plan is the file's own `git log`.

## Status values

| Status | Meaning |
|---|---|
| **proposed** | Idea drafted; trade-offs on the record; no implementation yet. |
| **active** | At least one phase has shipped; work in progress. |
| **completed** | All phases done; remains in the index for reference. |
| **abandoned** | Dropped before completion; the file documents why. |

## Index

| Plan | Title | Status |
|---|---|---|
| [p-059](p-059-prometheus-exporter.md) | Implement the Prometheus exporter behind `serve-metrics` | **active** — Phase A shipped: exporter, tailer, CLI wiring. |
| [p-060](p-060-conversion-provenance.md) | Emit conversion provenance per record | **active** — Phase A shipped: per-record `.prov.ttl` for both forward stages. |
| [p-061](p-061-field-coverage-corpus.md) | Synthetic field-coverage corpus | **active** — Phase A shipped: generator, 319 probes, `--check` guard in CI. |
