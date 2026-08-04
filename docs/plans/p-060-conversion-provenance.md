# p-060 — Emit conversion provenance per record

**Status: active.** Phase A (this plan) wires the forward direction.

## Problem

`CLAUDE.md` states "**Provenance is mandatory**: every conversion
decision that does anything non-trivial (e.g. discriminator-routing a
`bf:Hub` to `bffi:Work` vs `bffi:Expression`) writes to the provenance
graph before returning." Nothing did. `routings.py` returned counter
dicts and never touched a graph, and the only provenance writers in the
tree were for downstream stages that don't exist here (removed in
`fb21b33`).

## Design

**Per-record sidecar.** Each converter writes `<stem>.prov.ttl` beside
the record's output, holding one Activity plus its decision triples:

```
runs/<run>/bibframe-to-bffi/
  b11007849.bffi.ttl
  b11007849.prov.ttl
```

New module `src/bffi_pipeline/provenance/activities.py` with
`build_conversion_activity(graph, *, stage, bib_id, …, decisions)` and
`write_record_provenance(path, …)`. Both converters call it:

| Stage | Activity | Decision triples |
|---|---|---|
| `marc-to-bibframe` | `bffi-prov:MarcConversion` | — (the XSLT is one transform) |
| `bibframe-to-bffi` | `bffi-prov:MarcConversion` | one `bffi-prov:decision` per routing that fired |

## Trade-offs on the record

- **Per-record, not one graph per run.** A single `ProvenanceWriter` held
  open across a corpus run keeps every Activity in memory — ~5M+ triples
  at the 800k-record target, with real OOM risk. Per-record files are
  O(1) in memory and match the existing per-record artifact convention.
  `ProvenanceWriter` is still the mechanism; it's constructed per record.
  Concatenate the sidecars or load them into a store to get one graph.
- **Deterministic Activity URIs**, `bib:activity/<stage>/<bib_id>`, not
  UUIDs. `CLAUDE.md` permits UUIDs for `prov:Activity`; it does not
  require them, and a deterministic URI keeps re-runs diffable.
- **Provenance sidecars are not byte-deterministic.** They carry
  wall-clock `prov:startedAtTime` / `endedAtTime`, so the same input
  yields different bytes across runs. This is inherent to provenance and
  is a scoped exception to the idempotency rule — the *conversion*
  outputs remain deterministic.
- **Only non-zero routings are recorded.** A decision triple per
  zero-count routing would add ~30 triples per record saying nothing
  happened.

## Out of scope

- Provenance for `bffi-to-marc` and `roundtrip-eval`. The reverse
  direction must not read `bffi-prov:` (see `CLAUDE.md`); whether it
  should *write* its own Activity is a separate question.
- Loading provenance into a triple store. No SPARQL endpoint here.
