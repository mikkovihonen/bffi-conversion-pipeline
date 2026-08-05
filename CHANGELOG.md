# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
