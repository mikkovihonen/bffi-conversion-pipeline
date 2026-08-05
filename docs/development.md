# Development

## Local setup

```bash
git clone --recursive https://github.com/mikkovihonen/bffi-conversion-pipeline.git
cd bffi-conversion-pipeline
uv sync --frozen
```

## Testing

```bash
make test          # run all tests (504 unit tests)
make test-fast     # run only fast tests (no MARC corpus needed)
```

Tests run against on-disk fixtures — no Docker services, no network, no triple store. If a test ever needs an external service, it gets the `integration` marker and its own job.

## Linting

```bash
make lint          # ruff check + ruff format + mypy --strict
```

All three must pass before any commit. The pre-commit hook runs `make lint && make test` on `*.py` changes.

## Coverage

```bash
make coverage      # run tests with coverage, generate badge
```

Coverage badge auto-publishes to `docs/assets/coverage.svg` on `push` to `main`. The `fail_under = 80` threshold in `pyproject.toml` enforces a minimum.

## Generated artifacts

Several documentation files are **generated** from source (XSLT parsing, RDF parsing, fixture corpus). They are committed to the repo and `--check`-guarded:

```bash
bffi-pipeline regenerate-mapping-tables --check
bffi-pipeline regenerate-marc-mapping --check
bffi-pipeline regenerate-marc-to-bibframe-mapping --check
bffi-pipeline regenerate-field-coverage-corpus --check
```

If a generated file drifts from its source, `--check` fails the build. Submodule bumps of `third_party/marc2bibframe2` also trigger doc regeneration.

## Project structure

```
src/bffi_pipeline/
├── stages/              # Conversion stages (marc_to_bibframe, etc.)
│   ├── marc_to_bibframe/     # MARCXML → BIBFRAME via marc2bibframe2 XSLT
│   ├── bibframe_to_bffi/     # BIBFRAME → BFFI (31 routings)
│   ├── bffi_to_marc/         # BFFI → MARCXML (round-trip)
│   ├── melinda/              # OAI-PMH harvesting
│   └── roundtrip_eval/       # Round-trip diff & evaluation
├── diagnostic/          # Field coverage, mapping tables, XSLT coverage
├── observability/       # Structured events → JSONL → Prometheus
├── provenance/          # Conversion decision audit trail
├── validation/          # SHACL shapes, sidecar validation
└── cli.py               # Typer CLI entry point

docs/                    # Documentation (MkDocs)
third_party/             # Vendored XSLT (git submodule)
vocab/                   # BFFI 1.0.0 ontology (RDF/XML)
config/                  # SHACL shapes, validation config
tests/                   # Unit tests against fixtures
```

## Contributing

This is a pro-bono project for the National Library of Finland. Before starting work on a plan, read it through in `docs/plans/README.md`. If you're not working off a plan, check that file first to see whether a plan or proposal already covers the work.

## Operating constraints

- **No paid API services.** Everything runs locally or against free endpoints.
- **Open-source tooling only.**
- **License:** code Apache 2.0, published RDF data CC0.
- **No outbound telemetry** — no Datadog, Sentry, Honeycomb, or similar.
