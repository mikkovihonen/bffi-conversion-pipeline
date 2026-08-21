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
| [p-063](p-063-diff-pairing.md) | Pair repeated fields by content, not position | **completed** — content-based pairing, fuzzy fallback, `reordered` status. |
| [p-062](p-062-wire-validation.md) | Wire the three validation boundaries into the stages | **completed** — all three boundaries run: Boundary 1 gates by severity, Boundary 2 rescoped and gating, Boundary 3 rescoped to lkd.rdf axioms and reporting. |
| [p-064](p-064-boundary3-residue.md) | Clear the Boundary-3 residue p-062 left behind | **completed** — Hub retype + Manifestation→Work lift, 10 flagged records → 5. Title shapes and the `expressionOf` domain family resolved. |
| [p-065](p-065-recover-variant-titles.md) | Recover MARC 246 variant-title fields the XSLT leaves discriminator-less | **completed** — Phase A: `route_title_variants` attaches `bffi:marcKey` to untyped blocks. Phase B: reverse dispatcher reads marcKey. Phase C: ind1/ind2 reconstructed verbatim from marcKey. Multi-manifestation merge in `bffi_to_marc`. All 6 records with 246 now round-trip. |
| [p-066](p-066-devops-ci-release.md) | DevOps: CI coverage, GitHub Pages, releases | **completed** — coverage badge, MkDocs Material site with mermaid diagrams, release workflow, CHANGELOG, release skill.
| [p-067](p-067-recover-forward-only-marc-fields.md) | Recover MARC fields the XSLT reads but the reverse converter does not emit | **completed** — 24 of 51 forward-only tags recovered (023/026/037/042/043/045/046/086/257/351/352/353/384 from p-067; 045/246/260/264/505/520 from p-070). 14 skipped (no discriminator or marc2bibframe2 bottleneck). 13 out-of-scope (XSLT drop or shared predicates). |
| [p-068](p-068-recover-remaining-forward-only-fields-and-subfields.md) | Recover remaining forward-only MARC fields and subfields | **completed** — Phase 1: 020/024/028 `$q` recovered, 336/337/338 `$3` recovered. Phase 2: 051/055/072 skipped (no discriminators). Phase 3: remaining subfields (257 $0, 080 $x, 240 $k, 505 $t, 490 $x, 490 $6) out of scope — no BFFI predicates exist. |
| [p-069](p-069-recover-336-337-338-3-materials-specified.md) | Recover MARC 336/337/338 `$3` (materials specified) subfield | **completed** — Extended `_RdaEntry` with `applies_to` field, updated `_rda_entries` to read `bffi:appliesTo`/`rdfs:label`, added `$3` to `_RDA_SUBFIELDS`, updated emit rules and `_append_rda_datafields`. |
| [p-070](p-070-recover-additional-round-trip-losses.md) | Recover additional MARC fields lost in the round-trip after p-067 | **completed** — 6 tags recovered (045, 246, 260, 264, 505, 520). 9 tags confirmed unrecoverable (marc2bibframe2 does not handle them): 049, 240, 521, 538, 574, 575, 599, 776, 880. |
| [p-071](p-071-recover-alt-script-880-fields.md) | Recover alt-script 880 fields in the BFFI → MARC round-trip | **completed** — 9/6 alt-script 880 fields reconstructed for record 2602288 (cyrillic corpus). Extended subfields ($b, $c) emitted for titles/publications, $e for contributors. 7 tag families covered. 539 tests passing. |
