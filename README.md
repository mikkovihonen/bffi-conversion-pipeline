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

- **Three-way conversion**
  - MARCXML to BIBFRAME (via LoC [marc2bibframe2](https://github.com/loccolorado/marc2bibframe2) XSLT)
  - BIBFRAME to BFFI canonical Turtle (31 routings in `stages/bibframe_to_bffi/routings.py`)
  - The reverse direction reconstructing MARCXML from the BFFI graph.
- **Hard namespace boundary**
   - `bf:*` URIs stay inside the conversion input
   - BFFI output emits **only** `bffi:*` terms declared in `vocab/lkd.rdf`
- **Provenance**
   - Every non-trivial conversion decision writes to the provenance graph before returning.
   - No "optional logging" flag.
- **Round-trip evaluation**
   - An evaluation harness wraps the three conversion hops: round-trip diff, cataloguer-review HTML, and mapping-discipline tests against a fixture corpus.
- **Observable**
  - Every stage emits structured events to a JSONL sidecar, tail-exported to a local Prometheus + Grafana stack at `http://localhost:8080`.

## Quick start

```bash
# Clone with submodules (marc2bibframe2 XSLT is a git submodule)
git clone --recursive https://github.com/mikkovihonen/bffi-conversion-pipeline.git
cd bffi-conversion-pipeline
uv sync --frozen
make test && make lint

# Mint a run directory — capture its path for the stage commands below
RUN=$(bffi-pipeline new-run)

# Forward: MARCXML → BIBFRAME → BFFI
cp /path/to/marc.xml "$RUN/marc/"
bffi-pipeline marc-to-bibframe --input-dir "$RUN/marc" --output-dir "$RUN/bibframe"
bffi-pipeline bibframe-to-bffi --input-dir "$RUN/bibframe" --output-dir "$RUN/bffi"

# Reverse: BFFI → reconstructed MARCXML
bffi-pipeline bffi-to-marc --input-dir "$RUN/bffi" --output-dir "$RUN/marc-reconstructed"

# Evaluate: diff source vs reconstructed
bffi-pipeline roundtrip-eval \
  --source-dir "$RUN/marc" \
  --reconstructed-dir "$RUN/marc-reconstructed" \
  --html "$RUN/eval/review.html"
```

See **[Getting Started](docs/getting-started.md)** for prerequisites, dependencies, and the full CLI reference.

## Documentation

| Page | What's inside |
|------|---------------|
| [Getting Started](docs/getting-started.md) | Prerequisites, dependencies, build, CLI reference |
| [Development](docs/development.md) | Local dev setup, tests, lint, coverage, pre-commit |
| [Debugging](docs/roundtrip-debugging.md) | Diagnose missing, wrong, or fabricated fields in reconstructed MARC |
| [Observability](docs/observability.md) | Prometheus + Grafana local stack, JSONL sidecar, stage events |

## License

- Code: [MIT](LICENSE)
- Published RDF data: [CC0](https://creativecommons.org/publicdomain/zero/1.0/)

## Agentic coding disclosure
Built using agentic coding tools.
- [Pi Coding Agent](https://github.com/earendil-works/pi-coding-agent) via [pi-container](https://mikkovihonen.github.io/pi-container/) for agentic coding.
- [Claude Code](https://claude.com/product/claude-code)
