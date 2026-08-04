# Plans

Index of plans of record for the BFFI conversion pipeline.

## Convention

- One plan per file: `docs/plans/p-NNN-<slug>.md` (three-digit zero-padded; flat structure — no sub-folders).
- Status is tracked **in this index**, not by sub-folder location. Update the status column when a plan moves between states.
- Filenames stay stable across status transitions (no `git mv`); the history of a single plan is the file's own `git log`.
- Numbering continues the sequence from the legacy repository — it does not restart at 001. p-001 through p-056 live there.

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
| [p-057](p-057-rewrite-conversion-first-branch.md) | Conversion-first pipeline rewrite | **active** — the three conversion pillars and the eval harness have shipped. Superseded on the branch-policy question by p-058. |
| [p-058](p-058-extract-conversion-repo.md) | Extract the conversion pipeline into its own repository | **active** — extraction done (this repo is the result). Phase A (restore stage idempotency), Phase B (prune vestigial config), Phase C (decide the Melinda boundary) outstanding. |

## Plan history predates this repository

This repository starts from a single initial commit (see p-058). Plans authored before 2026-08-04 have their lineage in the legacy repository, not in this one's `git log`:

```sh
# Read a plan's history where it actually lives
git -C ../helmet-marcxml-bffi-skos-pipeline log --follow rewrite -- docs/plans/p-057-rewrite-conversion-first-branch.md

# Or fetch a document that was never carried across
git -C ../helmet-marcxml-bffi-skos-pipeline show rewrite:docs/plans/README.md
```

Plans p-001 – p-056 were never carried across. Most concern the downstream stages that stayed behind; the two that shaped this codebase are worth knowing by name:

- **p-49** (`main:docs/plans/proposed/p-49-bffi-structured-fields-vs-marckey.md`) — the marcKey-bypass audit: the finding that the round-trip was smuggling cataloguer-typed MARC strings through the graph to mask structural mapping shortfalls.
- **p-56** (`main:docs/plans/proposed/p-56-purge-bf-from-bffi-emit.md`) — the hard cut to zero `bf:*` URIs in the BFFI emit, which this repo bakes in from its first commit rather than treating as a migration.

Retrieve either with `git -C ../helmet-marcxml-bffi-skos-pipeline show <path-above>`. If a legacy plan becomes load-bearing here, copy it in under this repo's naming and add it to the index above.
