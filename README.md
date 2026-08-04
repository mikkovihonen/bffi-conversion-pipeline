# BFFI conversion pipeline

Bidirectional conversion between MARCXML and [BFFI](https://schema.finto.fi/bffi/)
(the National Library of Finland's
BIBFRAME application profile), by way of BIBFRAME:

```
MARCXML  →  BIBFRAME  →  BFFI canonical Turtle  →  MARCXML
```

Both directions are first-class. The forward path converts Helmet
bibliographic records into BFFI; the reverse path reconstructs MARCXML
from the BFFI graph alone, which is what makes the round-trip a
falsifiable test of the mapping rather than a one-way assertion about it.

Target corpus: ~800 000 Helmet bibliographic records.

This is **pro bono** work for the [National Library of Finland](https://www.kansalliskirjasto.fi/),
intended for upstream contribution alongside the existing NLF tooling.
Code is **Apache 2.0** (matching NLF tools); published RDF data is **CC0**
(matching Finto vocabularies).

## Why the round-trip

A forward-only converter can't tell you what it silently dropped. Running
MARC → BFFI → MARC and diffing against the source turns every mapping
shortfall into a visible artifact: a `lost` field, an `added` field, or a
`changed` value. The round-trip diff is therefore the primary quality
signal in this repository, not a nice-to-have.

Two disciplines keep the diff honest:

- **The `bffi:` namespace is closed.** Only terms declared in
  `vocab/lkd.rdf` may be emitted. When the ontology has no term for
  something, the converter drops it *visibly* rather than inventing a
  local term to make the diff look better.
- **The reverse converter reads `bffi:` only.** It never consults the
  BIBFRAME intermediate, and never reads `bffi-prov:` pipeline-internal
  provenance to decide what to emit. If a field survives the round-trip,
  it survived through the canonical graph.

Where the round-trip provably cannot be byte-identical, the reasons are
enumerated rather than hidden — see the "Known limitations" section of
[`docs/bffi_to_marc_mapping.md`](docs/bffi_to_marc_mapping.md).

## Architecture

```mermaid
flowchart TD
    SRC[Helmet MARCXML<br/>~800k records]
    MEL[Melinda OAI-PMH]

    subgraph Forward
        S1[marc-to-bibframe<br/>LoC marc2bibframe2 XSLT]
        S2[bibframe-to-bffi<br/>routings, BFFI-only emit]
    end

    subgraph Reverse
        S3[bffi-to-marc<br/>MARCXML reconstruction]
    end

    subgraph Evaluate
        S4[roundtrip-eval<br/>diff + cataloguer-review HTML]
    end

    SRC --> S1
    MEL --> S1
    S1 --> S2 --> S3 --> S4
    SRC -.->|source of truth| S4

    S2 -. "bffi-prov: decisions" .-> PROV[(provenance graph)]
    S3 -. "reads bffi: only" .-> PROV
```

Stage code lives in [`src/bffi_pipeline/stages/`](src/bffi_pipeline/stages/);
orchestration in [`src/bffi_pipeline/cli.py`](src/bffi_pipeline/cli.py).
Stages don't import each other.

The MARCXML at the head of the diagram is produced upstream, by
`helmet-sierra-data-tools`, which streams Helmet's Sierra Postgres
replica and writes per-bib MARCXML files. This repository takes MARCXML
as given and never touches the ILS.

## Prerequisites

- **Python 3.14** via [uv](https://github.com/astral-sh/uv) — everything is pinned in `uv.lock`.
- **`xsltproc`** on `PATH` — the marc2bibframe2 stylesheets are XSLT 1.0
  and are invoked as a subprocess, unchanged (see
  [`stages/marc_to_bibframe/xslt.py`](src/bffi_pipeline/stages/marc_to_bibframe/xslt.py)).
  Ships with macOS; `apt install xsltproc` on Debian/Ubuntu.
- **git with submodule support** — `marc2bibframe2` is vendored under
  `third_party/` as a submodule. Clone with `--recurse-submodules`, or run
  `git submodule update --init --recursive` after cloning.
- **Docker or Podman** — only for the optional local observability stack.
  No container is needed to run a conversion.

```sh
git clone --recurse-submodules <this-repo>
cd bffi-conversion-pipeline
uv sync
make test
```

## Running a conversion

Every invocation writes into a canonical run directory
(`runs/<yyyymmdd-hhmm-6hex>/`). Mint one with `new-run` and pass sub-paths
to each stage — the CLI rejects non-canonical output paths before the
stage starts.

```sh
RUN=$(uv run bffi-pipeline new-run)

uv run bffi-pipeline marc-to-bibframe \
    --input-dir tests/data/sample-marcxml/curated --output-dir $RUN/bibframe
uv run bffi-pipeline bibframe-to-bffi \
    --input-dir $RUN/bibframe --output-dir $RUN/bffi
uv run bffi-pipeline bffi-to-marc \
    --input-dir $RUN/bffi --output-dir $RUN/marc-out
uv run bffi-pipeline roundtrip-eval \
    --source-dir tests/data/sample-marcxml/curated \
    --reconstructed-dir $RUN/marc-out \
    --html $RUN/review.html
```

Conversions are deterministic: the same input produces the same output,
so a re-run into a fresh run directory is safe. Note that the three
conversion stages currently **overwrite unconditionally** — they do not
yet implement the atomic-write and skip-when-newer behaviour that
`CLAUDE.md` sets as the convention, and there is no `--force` flag to
override. `melinda-sync` is the one stage that does (atomic `.tmp` →
rename, resumption-token idempotency, `--force-restart`). Closing that
gap is tracked in
[`docs/plans/p-058-extract-conversion-repo.md`](docs/plans/p-058-extract-conversion-repo.md).

`bffi-pipeline --help` lists every command, including the diagnostics
(`diagnose-mappings`, `diagnose-marc-coverage`) and the mapping-doc
regenerators.

## Observability

Every stage emits structured events to a `stage-events.jsonl` sidecar in
its run directory, from each stage's first commit — this is built in from
the ground up, not bolted on. A local Prometheus + Grafana stack behind
Caddy turns those events into live panels:

```sh
make observability-up     # → http://localhost:8080
make observability-down
```

Everything runs on the operator's machine. There is **no outbound
telemetry or error reporting** — no Datadog, Sentry, or similar. See
[`docs/observability.md`](docs/observability.md).

## Documentation

| Document | What it is |
|---|---|
| [`docs/marc_to_bibframe_mapping.md`](docs/marc_to_bibframe_mapping.md) | Generated: what the LoC XSLT does with each MARC field. |
| [`docs/bf_to_bffi_mapping.md`](docs/bf_to_bffi_mapping.md) | Generated: every `bf:*` class/predicate → its `bffi:*` counterpart. Source of truth for forward-direction decisions; "Gap clusters" carry the ontology-shortfall caveats. |
| [`docs/bffi_to_marc_mapping.md`](docs/bffi_to_marc_mapping.md) | Generated: every MARC field the reverse converter emits, plus "Known limitations". |
| [`docs/validation-strategy.md`](docs/validation-strategy.md) | The three validation boundaries: MARCXML in, BIBFRAME post-conversion, BFFI post-emit. |
| [`docs/observability.md`](docs/observability.md) | The local metrics stack, end to end. |
| [`docs/plans/`](docs/plans/) | Plans of record, one per file, indexed by [`docs/plans/README.md`](docs/plans/README.md). |
| [`vocab/lkd.rdf`](vocab/lkd.rdf) | The full BFFI 1.0.0 ontology, vendored (the canonical schema URL returns 403 outside the Finto network). The closed set of terms we may emit. |

The three mapping docs are generated from the code, not hand-maintained.
Regenerate them when the emit changes:

```sh
uv run bffi-pipeline regenerate-mapping-tables            # bf_to_bffi
uv run bffi-pipeline regenerate-marc-mapping              # bffi_to_marc
uv run bffi-pipeline regenerate-marc-to-bibframe-mapping  # marc_to_bibframe
```

## Testing

```sh
make lint   # ruff check + ruff format --check + mypy --strict
make test   # pytest
```

Both must pass before any commit; a pre-commit hook installs itself on
first `make` run and enforces it for commits touching `*.py`. The suite
is entirely offline — unit tests run against fixtures under
`tests/data/`, never against external services. CI runs the same two
commands on GitHub-hosted Ubuntu runners.

Beyond ordinary coverage, the suite pins the two namespace disciplines
directly: `tests/unit/test_bffi_namespace_discipline.py` fails on any
emitted `bffi:` term absent from `vocab/lkd.rdf`, and
`tests/unit/stages/bffi_to_marc/test_bffi_prov_discipline.py` fails if the
reverse converter reads pipeline-internal provenance.

## Operating constraints

- **No paid API services.** Open-source tooling only.
- **No outbound telemetry.** The observability stack is local-only.
- **`mypy --strict`** across `src/`. Pydantic v2 at module boundaries;
  frozen dataclasses for internal value objects.
- **Errors over silent fallbacks.** Conversion failures raise; the only
  retries are for transient external errors.
- **Provenance is mandatory.** Every non-trivial conversion decision
  writes to the provenance graph before returning — there is no
  "optional logging" flag.

## Committed identifiers (do not change without surfacing)

| Namespace | URI |
|---|---|
| Work | `http://urn.fi/URN:NBN:fi:bib:work:` |
| Expression | `http://urn.fi/URN:NBN:fi:bib:expression:` |
| Manifestation | `http://urn.fi/URN:NBN:fi:bib:manifestation:` |
| Helmet source | `http://urn.fi/URN:NBN:fi:bib:source:helmet` |
| `bffi-prov` | `http://urn.fi/URN:NBN:fi:schema:bffi-prov#` |

## Relationship to the legacy repository

This repository was extracted from `helmet-marcxml-bffi-skos-pipeline`,
which carries the earlier full-stack pipeline: clustering, embedding
candidate generation, a local-LLM judge cascade, authority reconciliation
against KANTO / VIAF / YSO / KAUNO / MUSO, and Skosmos publication. Those
stages remain there as the legacy reference and are deliberately **not**
part of this repository — the conversion layer is the foundation they all
build on, and it is worth getting right on its own terms. See
[`docs/plans/p-058-extract-conversion-repo.md`](docs/plans/p-058-extract-conversion-repo.md).

## License

Code: [Apache License 2.0](LICENSE). Copyright (c) 2026 University of
Helsinki (The National Library of Finland).

Published RDF data: [CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/),
matching the Finto vocabulary licensing.
