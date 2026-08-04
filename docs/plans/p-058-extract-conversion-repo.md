# P-58 — Extract the conversion pipeline into its own repository

**Status**: active (2026-08-04). The extraction itself is done — this repository *is* the result. The follow-on cleanups in "Phases" below are what remains.

**Supersedes the open question at the end of P-57.** That plan ended with a choice to make once the rewrite reached conversion + eval parity: "merge `rewrite` → `main` and archive the legacy pipeline, or keep both lines." This plan is the third answer: **neither** — lift the conversion work out into a repository of its own, and leave the legacy line untouched where it stands.

## Why a separate repository rather than a merge

The `rewrite` branch had already diverged into a different artifact than `main`, not an improved version of it. `main` is a full-stack authority pipeline whose output is a browsable Skosmos vocabulary; `rewrite` is a bidirectional format converter whose output is a round-trip diff. They share an ontology and a corpus, and almost nothing else — different dependencies, different operating envelope, different reviewers.

Three things the split buys:

1. **An honest dependency surface.** The merged repo required SQLAlchemy + asyncpg (ILS access), mlx-lm (a local LLM), FAISS + an embedding model, a PHP application and a triple store — none of which the conversion path touches. A reviewer at NLF evaluating the MARC ↔ BFFI mapping had to install a research stack to run the tests. Here, `uv sync` plus `xsltproc` is the whole story.
2. **A single quality signal.** The conversion layer's correctness question is answerable — does MARC → BFFI → MARC reconstruct the source, and where it doesn't, is the shortfall enumerated? Sharing a repo with the clustering and judge stages meant sharing a CI signal and a review surface with work whose quality question is statistical and much softer.
3. **Contribution readiness.** This is the piece intended for upstream contribution to NLF alongside the existing tooling. A repository that *is* the contribution is a materially easier thing to hand over than a branch of a larger project with instructions about which directories to ignore.

The cost is real and worth stating: the ontology reference (`vocab/lkd.rdf`), the mapping docs, and the MARCXML fixtures now exist in two places. Divergence between the two copies of `lkd.rdf` is the failure mode to watch. See "Shared artifacts" below.

## What moved, what didn't

**Moved here** — the three conversion pillars, the eval harness, the diagnostics that generate the mapping docs, the observability stack, the validation boundaries, the vendored ontology, and the MARCXML test fixtures.

**Deliberately left behind:**

| Left in the legacy repo | Why |
|---|---|
| Clustering, embeddings, LLM judge, reconciliation | Downstream of conversion; different quality question. Already out of scope on `rewrite`. |
| Skosmos + Fuseki (`third_party/Skosmos`, the compose services, `config/skosmos-*`) | Vocabulary publishing. Nothing in the MARCXML ↔ BFFI chain reads a SPARQL endpoint. The compose services were already dead on `rewrite` — they mounted `config/` files the branch didn't carry. |
| `src/marcxml_export_pipeline/sierra/` + the `export` CLI stub | Corpus acquisition against the ILS, upstream of this repo's input boundary. Its natural home is `helmet-sierra-data-tools`, which already produces the corpus. The `export` command here was a `NotImplementedError` scaffold. |
| `config/shapes/post-load-smoke.rq` | Boundary-5 smoke checks for the Skosmos load — an out-of-scope stage. Unreferenced by any code on `rewrite`. |

**Kept, with a note:** `melinda-sync` (OAI-PMH MARCXML harvest) is ingestion, not conversion, so by the reasoning above it arguably belongs upstream too. It stays because it is self-contained, has no ILS or database dependency, and is the only way to get non-Helmet MARCXML in front of the converter — which matters for testing that the mapping isn't quietly Helmet-specific. Revisit if it grows a dependency.

## History

Fresh `git init`, single initial commit. The 82-commit lineage of the `rewrite` branch stays in the legacy repository, which remains readable; it is not reachable from this repo's history.

This is a deliberate trade against the `docs/plans/` convention that "the file's own `git log` is the lineage" — for plans authored on `rewrite`, that lineage now lives in the other repo. Recovering the provenance of a decision made before 2026-08-04 means reading `helmet-marcxml-bffi-skos-pipeline` at branch `rewrite`. Worth knowing before concluding a plan file has no history.

## Shared artifacts

`vocab/lkd.rdf` (the vendored BFFI ontology) now exists in both repositories and will drift. This repo's copy is authoritative for conversion decisions, because the closed-namespace discipline test asserts against it. On a BFFI release, bump it here first, regenerate the mapping docs, and treat the legacy repo's copy as stale until someone needs it.

The same applies to the MARCXML fixtures under `tests/data/sample-marcxml/`. Their `README.md` files still carry rationale prose written when Skosmos display was the downstream consumer — accurate about *why the record is interesting*, out of date about *what reads it*.

## Phases

### Phase A — restore stage idempotency (not started)

The extraction surfaced that the `CLAUDE.md` idempotency convention is not implemented. `marc-to-bibframe`, `bibframe-to-bffi`, and `bffi-to-marc` all overwrite unconditionally: no atomic `.tmp` → rename, no skip-when-newer, no `--force`. Only `melinda-sync` does it properly.

This was latent in the legacy repo and is worth fixing here, where a corpus-scale run over ~800k records makes "re-run the stage that died two hours in" a routine operation rather than a hypothetical. Until it lands, `CLAUDE.md` and `README.md` both say so explicitly rather than restating the convention as fact.

### Phase B — prune vestigial configuration (not started)

Small dead surfaces carried over from the legacy line, none load-bearing:

- `Settings.sparql_dir` — points at a `sparql/` directory that doesn't exist. There are no `.rq` files; the BIBFRAME → BFFI conversion is 31 Python routings.
- `Settings.data_dir` — superseded by the canonical run-directory convention.
- `probe_mlx_lm` and `probe_finto` in `observability/probes.py` — probes for services this repo has no dependency on. Exported but never called. `probe_fuseki` is likewise uncalled, though a staging store is at least conceivable.
- `events.py` docstring references `settings.observability_sidecar` / `settings.run_uuid`, neither of which is a field on `Settings`.

Deferred rather than done during the extraction: each is a code change with its own test surface, and bundling them into the initial commit would have mixed "moved the repo" with "changed the code."

### Phase C — decide the Melinda boundary (not started)

Either keep `melinda-sync` here permanently and document it as a first-class ingestion path for non-Helmet MARCXML, or move it to `helmet-sierra-data-tools` alongside the Sierra exporter so that *all* corpus acquisition lives in one place. Don't leave it ambiguous — the current state is a judgement call recorded above, not a decision.

## Legacy repository

Left untouched by this extraction: no branch deleted, no pointer commit, `rewrite` still present. If it is later archived, add a README note there pointing here first, so the lineage is discoverable from the side that has the history.
