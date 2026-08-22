# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **6XX subject indicator loss** — 600/610/611/630/648/650/651/653/655/662 fields now preserve ind1/ind2 from `bffi:marcKey` (e.g. `600 14` instead of `600   `). ind2 set to `"7"` when `$2` emitted (per MARC convention). Added `ind1`/`ind2` fields to `_SubjectEmit` dataclass.
- **240 uniform title loss** — 240 fields are now recovered from `bffi:Hub240` nodes via `Manifestation → Expression → Hub` traversal. Parsed from Hub's `marcKey` (`{1XX marcKey}$t{uniform title},${subfields...}`) with uniform title mapped to `$a` and remaining subfields via `_AGENT_TO_240_SUBFIELD_CODE`. Round-trip: 5 fewer lost fields, 2 more identical.
- **490 duplicate series** — exact duplicate 490 datafields (same tag, ind1, ind2, subfield content) are now deduplicated via `_FieldDeduplicator`. marc2bibframe2 produces duplicate `bf:relation` blocks for the first 490 field; the deduplicator removes the byte-identical duplicate before emit. Round-trip: -2 added fields.

### Changed

- **041 indicator** — ind1 now `"0"` (not blank) when language codes exist but no `$h` (translation) is present. Fixes `041    $azxx` → `041 0  $azxx` mismatches.
- **ISBD punctuation default** — `apply_isbd_punctuation` now defaults to `True` (was `False`) for round-trip fidelity. Toggle off with `--no-apply-isbd-punctuation`.

## [0.2.3] - 2026-08-22

### Added

- **ISBD punctuation (toggleable)** — `--apply-isbd-punctuation` flag adds ISBD trailing punctuation to reconstructed MARC fields. Rules defined per tag family:
  - Publications (260/264): `$a :` `$b ,` `$c .`
  - Titles (245): `$a :` `$b /` `$c .` (also `$n`, `$p`, `$f`, `$g`)
  - Contributors (100/700): `$a ,` `$e .` (also `$b`, `$f`, `$t`, `$c`, `$d`, `$4`)
  - Corporate bodies (110/710): `$a ,` `$e .` (also `$f`, `$t`, `$c`, `$d`, `$4`)
  - Meetings (111/711): `$a ,` `$e .` (also `$f`, `$t`, `$c`, `$d`, `$4`)
  - Physical description (300): `$a :` `$b ;` `$c +`
  - Notes (500/504/511/534/546): `$a .`
  - Series (490): `$a .` `$v .` `$x .`
  - Subjects (650/651): `$a .` `$t .` `$c .` `$d .` `$v .` `$x .` `$y .` `$0 .` `$2 .`
  - Edition (250): `$a .`
  - Duration (306), Extent (334), Content (336/337), Carrier (338), Supplementary content (353), Uniform title (730), Added entry (740): `$a .`
- **Double punctuation prevention** — values already ending with the ISBD punctuation character don't get it added again (e.g., source `"Hiller, Sean,"` stays as-is, not `"Hiller, Sean,,"`).
- **Proper ISBD spacing** — leading space included where MARC convention requires (e.g., ` :` not `:` for 260/245 `$a` before `$b`).
- **`ConversionOptions.apply_isbd_punctuation`** — new boolean field (default `False`) to enable ISBD punctuation programmatically.
- **`get_isbd_punctuation()` helper** — returns ISBD trailing punctuation based on tag, subfield code, and next subfield. Fast path when disabled (returns empty string immediately).
- **ISBD test coverage** — 635 unit tests covering all tag/subfield/next combinations (~95% rule coverage), plus 8 integration tests validating end-to-end punctuation behavior and double punctuation prevention.

## [0.2.2] - 2026-08-22

### Changed

- **Alt-script 880 reconstruction** — extended to include `$c` subfield for titles (245) from Work contributions (author + translator), formatted as `"Name ; role_term"` per contributor, joined with `" ; "`, ending with period. Contributors sorted by MARC tag (100 before 700) to match source order.
- **Publication tag selection** — now emits 264 (not 260) for structured place/agent/date when ind1 is not `"4"`, matching source MARC. Flat `bffi:publicationStatement` fallback still uses 260.
- **Doc generator TODOs** — added comments in `marc_mapping.py` noting that `dynamic` and `emits_from` fields from `MarcEmitMeta` should be handled in future iterations.

### Fixed

- **False-positive alt-script 880 emission** — `detect_alt_scripts()` now skips literals whose text is identical to the primary value (e.g., date `[2025]` tagged `@ru` when romanized version is also `[2025]`). Reduces spurious 880 fields.
- **Alt-script subtitle for titles (245)** — `$b` in reconstructed 880 now uses the alt-script subtitle value (matched by language) instead of always using the romanized primary. Fixed merge logic to treat subtitle as `$b` extra_subfield, not a separate `$a` alt-script.
- **Alt-script agent/date for publications (260/264)** — `$b` and `$c` in reconstructed 880 now use alt-script agent/date values (matched by language) instead of always using romanized primary values.

## [0.2.1] - 2026-08-21

### Added

- **Alt-script 880 field reconstruction (p-071)** — detects language-tagged duplicate literals in the BFFI graph and emits MARC 880 fields after the main field with dynamic occurrence numbering and `$6` linkage. Covers 7 tag families:
  - Contributors (100, 110, 111, 700, 710, 711) with `$a` name + `$e` relator
  - Titles (245) with `$a` mainTitle + `$b` subtitle
  - Variant titles (210, 222, 242, 243, 246, 247) with `$a` mainTitle
  - Publications (260, 264) with `$a` place + `$b` agent + `$c` date
  - Notes (500, 504, 511, 534, 546, etc.) with `$a` label
  - Subjects (600, 610, 611, 630, 650, 651, 653, 655, 656) with `$a` label
  - Series (490) with `$a` mainTitle
- **Alt-script detection utility** (`src/bffi_pipeline/stages/bffi_to_marc/alt_script.py`) — Unicode script detection for 30+ scripts (Cyrillic=`/(N`, Greek=`/(G`, Hebrew=`/(I`, Arabic=`/(R`, etc.) per MARC Code Lists for Script Codes.
- **Unit tests** for alt-script detection (14 tests) and **integration tests** for round-trip reconstruction on real curated samples (6 tests).

## [0.2.0] - 2026-08-20

### Added

- **Round-trip recovery: 24 forward-only MARC tags** — recovered fields lost in the MARC → BIBFRAME conversion:
  - Phase A (p-067): 023 (ISSN-l), 026 (Fingerprint), 037 (Acquisition Source), 086 (GPO Classification), 353 (Supplementary Content)
  - Phase B (p-067): 043 (Geographic Code), 045 (Temporal Coverage), 046 (Date Code), 257 (Country of Origin)
  - Phase C (p-067): 384 (Number of Units)
  - Phase D (p-067): 352 (Digital Graphical Representation)
  - Phase E (p-067): 042 (Description Authentication), 351 (Collection Arrangement)
  - Phase F skipped: 656/720/752/753/758 (no marcKey on subjects/names/hierarchical places)
- **Variant title recovery (246, p-065)** — attaches `bffi:marcKey` discriminators to untyped `bf:VariantTitle` / `bf:ParallelTitle` blocks before overwriting type with `bffi:Title`. The reverse extractor dispatches on marcKey to emit the correct MARC 246 datafield. 5 of 6 records recovered.
- **Multi-manifestation variant title merge (bffi_to_marc)** — `convert_one` in the BFFI → MARC stage iterates over all `bffi:Manifestation` nodes and merges variant titles (deduplicated by tag+text) before emitting MARCXML. This recovers 246 datafields that live on non-first manifestations (e.g. record 1109760). The merge is a post-processing step in the reverse converter; the BFFI graph itself remains unchanged with separate manifestations intact.
- **024 $q qualifier subfield** — emits the qualifier subfield for ISBNs (e.g., "hardcover").
- **336/337/338 $3 (materials specified)** — recovers the `appliesTo` subfield for content/media/carrier type designations.
- **Additional round-trip recoveries (p-070)**: 045 (temporal coverage), 246 (variant titles — see above), 260/264 (publication/distribution with ind1 discrimination), 505 (table of contents from Work anchor), 520 (summary from Expression anchor).
- **Plan documentation**: p-065, p-067, p-068, p-069, p-070 — all implemented and marked complete.

### Removed

- (None)

### Changed

- (None — all additions)

### Fixed

- **Multi-manifestation 246 loss** — records with multiple `bffi:Manifestation` nodes (marc2bibframe2's preprocess-splitter output) now have variant titles merged across all manifestations in the BFFI → MARC stage (`convert_one`), not just the first. The BFFI graph remains unchanged; the merge is a post-processing step during MARCXML emission.

### Deprecated

- (None)

### Security

- (None)

### Known Limitations

- 14 MARC tags remain unrecoverable: no marcKey on BFFI nodes (656/720/752/753/758/072/051/055), marc2bibframe2 bottleneck (049/240/521/538/574/575/599/776/880), or shared predicates without discriminator (377/048/382/034/255/340/254/256/341).

## [0.1.0] - 2026-08-05

### Added

- DevOps: CI coverage badge auto-publish, GitHub Pages (MkDocs Material),
  release workflow, CHANGELOG, release skill for pi, dependabot, `.gitattributes`.
  ([p-066](docs/plans/p-066-devops-ci-release.md))
- **MARCXML → BIBFRAME conversion** via LoC marc2bibframe2 XSLT (vendored as git submodule)
- **BIBFRAME → BFFI canonical Turtle** with 31 discriminator routings; hard `bffi:` namespace boundary (zero `bf:*` URIs in output)
- **BFFI → MARCXML reverse reconstruction** for round-trip verification
- **Round-trip evaluation harness**: diff comparison, cataloguer-review HTML, mapping-discipline tests
- **OAI-PMH ingestion**: `melinda-sync` harvests Melinda bibliographic records over OAI-PMH
- **Per-record conversion provenance**: every non-trivial routing decision writes to a `bffi-prov:` graph
- **Three validation boundaries**: MARCXML input gating, post-XSLT SHACL shapes, BFFI emit-time namespace discipline
- **Synthetic field-coverage corpus**: generator with 319 probes, `--check`-guarded in CI
- **Mapping reference docs**: auto-generated from XSLT parsing and RDF parsing, committed to repo
- **Structured observability**: per-stage JSONL sidecars, Prometheus exporter (`serve-metrics`), local Grafana dashboard
- **Typer CLI** with 13 subcommands covering ingestion, conversion, evaluation, diagnostics, and observability
- **FRBR entity model**: Work/Expression/Manifestation with `bffi:hasExpression` and `bffi:manifestationOfExpression` relationships
- **Run directory convention**: canonical `runs/<yyyymmdd-hhmm-<6hex>>/` layout minted via `bffi-pipeline new-run`
- **CI**: lint (ruff + mypy --strict), format, tests, generated-artifact drift checks, coverage badge
- **GitHub Pages**: MkDocs Material site with mermaid architecture diagrams
- **Release workflow**: `softprops/action-gh-release` on `v*` tags with auto-notes
- **Dependabot**: Python + GitHub Actions ecosystem updates
- **Pre-commit hook**: runs `make lint && make test` on `*.py` changes
