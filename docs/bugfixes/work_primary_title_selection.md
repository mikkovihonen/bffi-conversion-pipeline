# Walkthrough - Work Primary Title Selection (`mts:m1628`) & Finto MTS Vocabulary

## Overview

In Finnish cataloguing (RDA / BFFI profile), every conceptual Work carries a preferred primary title (`bffi:title` typed `bffi:Title` and `mts:m1628`, *Teoksen ensisijainen nimeke*).

1. When a uniform title resulting from MARC 130 or 240 exists (present on an associated Hub or the Work), it is chosen as the primary title of the Work, typed `bffi:Title, mts:m1628`, and the transcribed 245 title is removed from the Work.
2. When only the BIBFRAME equivalent of MARC 245 exists (on the Manifestation), the equivalents of subfields `$a` (`bffi:mainTitle`), `$p` (`bffi:partName`), and `$n` (`bffi:partNumber`) are used — excluding subtitle `$b` (`bffi:subtitle`) — and typed `bffi:Title, mts:m1628`.
3. Variant titles on the Work (e.g. from MARC 246) are preserved.
4. The Metatietosanasto (MTS) vocabulary from Finto has been vendored to `vocab/mts.ttl`, and `MTS` (`http://urn.fi/URN:NBN:fi:au:mts:`) is bound to the pipeline's canonical Turtle prefixes.
5. All code decorators (`@routing` and `@marc_emit`) have been updated so that mapping documentation generation remains in sync.

---

## Changes

### 1. Vocabulary & Provenance

#### [vocab/mts.ttl](file:///Users/mikkovihonen/Workspace/bffi-conversion-pipeline/vocab/mts.ttl)
- Vendored export of Metatietosanasto (MTS) from Finto API (`https://api.finto.fi/rest/v1/mts/data?format=text/turtle`).

#### [vocab/README.md](file:///Users/mikkovihonen/Workspace/bffi-conversion-pipeline/vocab/README.md)
- Documented `vocab/mts.ttl` and the `mts:m1628` term in the vocabulary registry README.

#### [src/bffi_pipeline/provenance/vocab.py](file:///Users/mikkovihonen/Workspace/bffi-conversion-pipeline/src/bffi_pipeline/provenance/vocab.py)
- Declared `MTS: Final[Namespace] = Namespace("http://urn.fi/URN:NBN:fi:au:mts:")`.
- Bound `"mts": MTS` in `CANONICAL_TURTLE_PREFIXES`.
- Exported `MTS` in `__all__`.

---

### 2. BIBFRAME → BFFI Conversion & Routings

#### [src/bffi_pipeline/stages/bibframe_to_bffi/routings.py](file:///Users/mikkovihonen/Workspace/bffi-conversion-pipeline/src/bffi_pipeline/stages/bibframe_to_bffi/routings.py)
- Implemented `route_work_primary_title(graph: Graph) -> int` decorated with `@routing`:
  - Discovers uniform titles from MARC 130 / 240 Hubs reachable via `bffi:expressionOf` or direct links.
  - When uniform title exists: copies `mainTitle`, `partName`, `partNumber`, `nonSortNum`, `qualifier` onto a fresh `bffi:Title, mts:m1628` bnode on the Work, and removes the old transcribed 245 title from the Work.
  - When only MARC 245 exists: resolves Manifestation's 245 title, copies `$a`, `$p`, `$n` (excluding subtitle `$b`) onto a fresh `bffi:Title, mts:m1628` bnode on the Work, and removes old transcribed 245 title from the Work.
  - Preserves any variant titles (e.g. MARC 246) on the Work.
- Registered `"work_primary_title": route_work_primary_title(graph)` in `apply_all_routings(graph)`.

---

### 3. BFFI → MARC Reverse Extractor & Documentation

#### [src/bffi_pipeline/stages/bffi_to_marc/extractors.py](file:///Users/mikkovihonen/Workspace/bffi-conversion-pipeline/src/bffi_pipeline/stages/bffi_to_marc/extractors.py)
- Updated `@marc_emit` metadata for MARC tags `130`, `240`, and `245` to document that preferred title is typed `bffi:Title + mts:m1628` on the Work.

#### [docs/bffi_to_marc_mapping.md](file:///Users/mikkovihonen/Workspace/bffi-conversion-pipeline/docs/bffi_to_marc_mapping.md)
- Re-generated via `bffi-pipeline regenerate-marc-mapping`.

---

### 4. Tests

#### [tests/unit/test_vocab_prefixes.py](file:///Users/mikkovihonen/Workspace/bffi-conversion-pipeline/tests/unit/test_vocab_prefixes.py)
- Added verification of `V.MTS.m1628` and `mts:` prefix in `CANONICAL_TURTLE_PREFIXES`.

#### [tests/unit/stages/bibframe_to_bffi/test_routings.py](file:///Users/mikkovihonen/Workspace/bffi-conversion-pipeline/tests/unit/stages/bibframe_to_bffi/test_routings.py)
- Added unit tests for `route_work_primary_title`:
  - `test_route_work_primary_title_uniform_title_from_hub130`
  - `test_route_work_primary_title_uniform_title_from_hub240`
  - `test_route_work_primary_title_245_fallback_strips_subtitle`
  - `test_route_work_primary_title_preserves_variant_titles`
  - `test_route_work_primary_title_idempotent`
- Updated `test_routing_registry_attaches_metadata_to_decorated_functions` and `test_apply_all_routings_returns_per_routing_counts`.

#### [tests/unit/stages/bibframe_to_bffi/test_runner.py](file:///Users/mikkovihonen/Workspace/bffi-conversion-pipeline/tests/unit/stages/bibframe_to_bffi/test_runner.py)
- Added integration tests on golden fixture records:
  - `test_convert_one_work_primary_title_245_fallback` on `b1042698x.xml` (Work title: "Jazz fake book", no subtitle).
  - `test_convert_one_work_primary_title_marc_130` on `b18797684.xml` (Work title from 130, 246 variant preserved).
  - `test_convert_one_work_primary_title_marc_240` on `b25999163.xml` (Work title from 240).

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
  - `uv run pytest tests/`: **660 passed in 12.22s**
