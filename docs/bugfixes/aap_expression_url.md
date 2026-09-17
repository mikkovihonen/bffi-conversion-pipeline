# Walkthrough — BFFI Pipeline Enhancements

## Overview

This session implemented several BFFI pipeline features covering Work primary title selection, authorized access point (AAP) generation, Expression URI minting, and vocabulary updates.

---

## Changes

### 1. Vocabulary & Provenance

#### [lkd.rdf](file:///Users/mikkovihonen/Workspace/bffi-conversion-pipeline/vocab/lkd.rdf)
- Declared `bffi:authorizedAccessPoint` as `owl:DatatypeProperty` with `owl:equivalentProperty` to `bffi:aap` and `bflc:aap`, domain union (Work, Expression, Manifestation, Item), range `rdfs:Literal`.
- Added `owl:equivalentProperty` from `bffi:aap` → `bffi:authorizedAccessPoint`.

#### [mts.ttl](file:///Users/mikkovihonen/Workspace/bffi-conversion-pipeline/vocab/mts.ttl)
- Vendored Metatietosanasto (MTS) from Finto API.

#### [vocab.py](file:///Users/mikkovihonen/Workspace/bffi-conversion-pipeline/src/bffi_pipeline/provenance/vocab.py)
- Exported `authorizedAccessPoint: URIRef = BFFI.authorizedAccessPoint` and `aap: URIRef = BFFI.aap`.
- Declared `MTS: Final[Namespace]` and bound `mts:` prefix.
- Added all to `__all__` (sorted per RUF022).

---

### 2. Expression URI Minting (`route_work_split`)

#### [routings.py](file:///Users/mikkovihonen/Workspace/bffi-conversion-pipeline/src/bffi_pipeline/stages/bibframe_to_bffi/routings.py)
- Expressions now get canonical URIs instead of blank nodes:
  - `<base>#Work` → `<base>#Expression`
  - `<base>#Work776-46` → `<base>#Expression776-46` (via `#Work` → `#Expression` replacement)
  - Other fragment patterns → `<base>#Expression_<frag>`
  - BNode subjects → still `BNode()`
- Added bidirectional link: `(subject, BFFI.hasExpression, expr_node)`.

---

### 3. Work Primary Title Selection (`mts:m1628`)

#### [routings.py](file:///Users/mikkovihonen/Workspace/bffi-conversion-pipeline/src/bffi_pipeline/stages/bibframe_to_bffi/routings.py)
- `route_work_primary_title(graph)` decorated with `@routing`:
  - Discovers uniform titles from MARC 130 / 240 Hubs via `bffi:expressionOf` or direct links.
  - When uniform title exists: copies `mainTitle`, `partName`, `partNumber`, `nonSortNum`, `qualifier` onto a fresh `bffi:Title, mts:m1628` bnode; removes old transcribed 245 title from Work.
  - When only MARC 245 exists: resolves Manifestation's 245 title, copies `$a`, `$p`, `$n` (excluding `$b` subtitle); types `bffi:Title, mts:m1628`.
  - Preserves variant titles (e.g. MARC 246).
- Fixed `_find_uniform_title`: removed `"$t" in mk_str` check to restrict to 130/240 only (fixed b24698015 picking 700 $t Hub incorrectly).

---

### 4. Authorized Access Point (`bffi:authorizedAccessPoint`)

#### [routings.py](file:///Users/mikkovihonen/Workspace/bffi-conversion-pipeline/src/bffi_pipeline/stages/bibframe_to_bffi/routings.py)
- Added `BFLC` namespace definition.
- Added `_LANGUAGE_NAMES_FI` dictionary (~40 Finnish language name mappings).
- Added helpers: `_clean_aap_text`, `_language_node_to_fi`, `_resolve_expression_languages`, `_compute_work_aap`, `_set_aap_triples`.
- `route_authorized_access_points(graph)` decorated with `@routing`:
  - **Work AAP**: `"{creator}. {primary_title}"` (with creator) or `"{primary_title}"` (without).
  - **Expression AAP**: `"{work_aap}. {language}"` appending Finnish language names.
  - Non-linguistic (`zxx`) expressions skip AAP.
- Registered after `route_work_primary_title` in `apply_all_routings`.

---

### 5. BFFI → MARC Documentation

#### [extractors.py](file:///Users/mikkovihonen/Workspace/bffi-conversion-pipeline/src/bffi_pipeline/stages/bffi_to_marc/extractors.py)
- Updated `@marc_emit` metadata for MARC 130, 240, 245 to document `bffi:Title + mts:m1628`.

#### [bffi_to_marc_mapping.md](file:///Users/mikkovihonen/Workspace/bffi-conversion-pipeline/docs/bffi_to_marc_mapping.md)
- Re-generated via `bffi-pipeline regenerate-marc-mapping`.

---

### 6. Tests

#### [test_routings.py](file:///Users/mikkovihonen/Workspace/bffi-conversion-pipeline/tests/unit/stages/bibframe_to_bffi/test_routings.py)
- Updated registry and counter tests for new routings.
- Updated `test_route_work_split_splits_bibframework_into_work_and_expression` to assert Expression URI and bidirectional link.
- Added 5 primary title tests: `hub130`, `hub240`, `245_fallback`, `preserves_variant`, `idempotent`.
- Added 5 AAP tests: `with_primary_creator`, `without_primary_creator`, `hub240_language`, `non_linguistic_zxx`, `idempotent`.

#### [test_runner.py](file:///Users/mikkovihonen/Workspace/bffi-conversion-pipeline/tests/unit/stages/bibframe_to_bffi/test_runner.py)
- Added integration tests on golden fixtures:
  - `test_convert_one_work_primary_title_245_fallback` (b1042698x)
  - `test_convert_one_work_primary_title_marc_130` (b18797684)
  - `test_convert_one_work_primary_title_marc_240` (b25999163)
  - `test_convert_one_expression_has_uri_and_aap_b1042698x`
  - `test_convert_one_work_and_expression_aap_with_primary_creator` (b24698015)

---

## Verification Results

### Automated Quality Checks
- `bffi-pipeline regenerate-mapping-tables --check`: **docs up to date**
- `bffi-pipeline regenerate-marc-mapping --check`: **docs up to date**
- `make lint`:
  - `uv run ruff check src tests`: **All checks passed!**
  - `uv run ruff format --check src tests`: **107 files already formatted**
  - `uv run mypy --strict src`: **Success: no issues found in 56 source files**
- `make test`:
  - `uv run pytest tests/`: **667 passed in 12.48s**
