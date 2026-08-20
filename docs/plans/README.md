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
| [p-059](p-059-prometheus-exporter.md) | Implement the Prometheus exporter behind `serve-metrics` | **completed** — exporter, tailer, CLI wiring; 13 gauges matching the metric vocabulary. |
| [p-060](p-060-conversion-provenance.md) | Emit conversion provenance per record | **completed** — per-record `.prov.ttl` for both forward stages. |
| [p-061](p-061-field-coverage-corpus.md) | Synthetic field-coverage corpus | **completed** — generator, 319 probes, `--check` guard in CI. |
| [p-063](p-063-diff-pairing.md) | Pair repeated fields by content, not position | **active** — Phase A shipped: content-based pairing, fuzzy fallback, `reordered` status. |
| [p-062](p-062-wire-validation.md) | Wire the three validation boundaries into the stages | **completed** — all three boundaries run: Boundary 1 gates by severity, Boundary 2 rescoped and gating, Boundary 3 rescoped to lkd.rdf axioms and reporting. |
| [p-064](p-064-boundary3-residue.md) | Clear the Boundary-3 residue p-062 left behind | **active** — Phase A shipped: Hub retype + Manifestation→Work lift, 10 flagged records → 5. Title shapes and the `expressionOf` domain family open. |
| [p-065](p-065-recover-variant-titles.md) | Recover MARC 246 variant-title fields the XSLT leaves discriminator-less | **proposed** — marc2bibframe2 emits no marcKey for 246 ind2 ∈ {1, 3, blank}; the BFFI routing attaches marcKey before stripping the original rdf:type. |
| [p-066](p-066-devops-ci-release.md) | DevOps: CI coverage, GitHub Pages, releases | **completed** — coverage badge, MkDocs Material site with mermaid diagrams, release workflow, CHANGELOG, release skill.
| [p-067](p-067-recover-forward-only-marc-fields.md) | Recover MARC fields the XSLT reads but the reverse converter does not emit | **implemented** — 17 of 23 forward-only tags recovered (023/026/037/042/043/045/046/086/257/351/352/353/384). 6 skipped (no discriminator: 048/254/256/340/341/352/382/656/720/752/753/758). 9 lost at XSLT, 12 already-documented, 3 `bflc:`-namespace out of scope. |
| [p-068](p-068-recover-remaining-forward-only-fields-and-subfields.md) | Recover remaining forward-only MARC fields and subfields | **active** — Phase 1 shipped: 024 `$q` recovered. Phase 2 complete: 051/055/072 all skipped. Phase 3 complete: docs verified. |
