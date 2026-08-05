# BFFI conversion pipeline

A bidirectional MARCXML ↔ BFFI conversion pipeline, by way of BIBFRAME. Built for the [National Library of Finland](https://www.kansalliskirjasto.fi/en/), producing [BFFI](https://finto.fi/bffi/en/) canonical Turtle from MARCXML records harvested via OAI-PMH, and reconstructing MARCXML from the BFFI graph for round-trip verification.

<div align="center" style="text-align:center;" markdown = "1">
  <img src="docs/assets/logo-2x.png" alt="BFFI conversion pipeline" width="400">
</div>

<div align="center" style="text-align:center;" markdown = "1">

[![CI](https://github.com/mikkovihonen/bffi-conversion-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/mikkovihonen/bffi-conversion-pipeline/actions/workflows/ci.yml)
[![Coverage](docs/assets/coverage.svg)](docs/development.md#coverage)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python: 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](https://www.python.org/)
[![uv](https://img.shields.io/badge/desc/uv-managed-brightgreen.svg)](https://docs.astral.sh/uv/)

</div>

## Highlights

- **Three-way conversion** — MARCXML → BIBFRAME (via LoC [marc2bibframe2](https://github.com/loccolorado/marc2bibframe2) XSLT) → BFFI canonical Turtle (31 routings in `stages/bibframe_to_bffi/routings.py`), and the reverse direction reconstructing MARCXML from the BFFI graph.
- **Hard namespace boundary** — `bf:*` URIs stay inside the conversion input; the BFFI output emits **only** `bffi:*` terms declared in `vocab/lkd.rdf`. BIBFRAME is recoverable by OWL inference through the re-anchor pattern.
- **Provenance** — every non-trivial conversion decision writes to the provenance graph before returning. No "optional logging" flag.
- **Round-trip evaluation** — an evaluation harness wraps the three conversion hops: round-trip diff, cataloguer-review HTML, and mapping-discipline tests against a fixture corpus.
- **Observable** — every stage emits structured events to a JSONL sidecar, tail-exported to a local Prometheus + Grafana stack at `http://localhost:8080`.

## Quick start

```bash
git clone --recursive https://github.com/mikkovihonen/bffi-conversion-pipeline.git
cd bffi-conversion-pipeline
uv sync --frozen
make test && make lint
bffi-pipeline new-run                    # create a run directory
bffi-pipeline marc-to-bibframe --input /path/to/marc.xml --output-dir runs/...
bffi-pipeline bibframe-to-bffi --input .../bibframe.ntriples --output-dir runs/...
bffi-pipeline bffi-to-marc --input .../bffi.ntriples --output-dir runs/...
bffi-pipeline roundtrip-eval --original .../marc.xml --reconstructed .../marc-reconstructed.xml
```

See **[Getting Started](docs/getting-started.md)** for prerequisites, dependencies, and the full CLI reference.

## Documentation

| Page | What's inside |
|------|---------------|
| [Getting Started](docs/getting-started.md) | Prerequisites, dependencies, build, CLI reference |
| [Development](docs/development.md) | Local dev setup, tests, lint, coverage, pre-commit |
| [Mapping references](docs/bf_to_bffi_mapping.md) | Every `bf:*` class/predicate → `bffi:*` routing decision |
| [BFFI → MARC mapping](docs/bffi_to_marc_mapping.md) | Every MARC field the reverse converter emits, with known limitations |
| [MARC → BIBFRAME mapping](docs/marc_to_bibframe_mapping.md) | What the LoC XSLT does with each MARC field |
| [Round-trip debugging](docs/roundtrip-debugging.md) | Diagnose missing, wrong, or fabricated fields in reconstructed MARC |
| [Validation strategy](docs/validation-strategy.md) | SHACL shapes, sidecar validation, three validation boundaries |
| [Observability](docs/observability.md) | Prometheus + Grafana local stack, JSONL sidecar, stage events |
| [Vocabulary](docs/vocabulary.md) | BFFI 1.0.0 ontology (RDF/XML) reference |
| [Plans](docs/plans/README.md) | Plans of record with status tracking |

## Implementation

Built with [Pi Coding Agent](https://github.com/earendil-works/pi-coding-agent) via [pi-container](https://mikkovihonen.github.io/pi-container/) for agentic coding.

## License

Code: [MIT](LICENSE) · Published RDF data: [CC0](https://creativecommons.org/publicdomain/zero/1.0/)
