# Validation strategy

Data crosses three distinct boundaries on the conversion side, and each gets its own validation. Don't conflate them — rdflib's permissive parsing is **not** validation, and silent acceptance at one boundary becomes corruption at the next.

## The three boundaries

| # | Boundary | When | Tool | Failure mode |
|---|---|---|---|---|
| 1 | MARCXML input | conversion entry | XSD + minimum-content checks | **Structural** failure (filename, encoding, XML syntax, XSD): skip record, log to `_errors.jsonl`. **Content-minimum**: keep the record, log to `_validation.jsonl` |
| 2 | BIBFRAME post-conversion | marc2bibframe exit | SHACL (`bibframe-conversion.shape.ttl`) + absence check | Skip record, log to `_errors.jsonl`; `--no-strict-shapes` downgrades to a `_validation.jsonl` flag |
| 3 | BFFI post-CONSTRUCT | BIBFRAME→BFFI exit | SHACL (`bffi.shape.ttl`) | Report to `_validation.jsonl`, warn on CLI, don't block |

Boundary 1's severity split is a deviation from this doc as originally
written — a measured one, not drift. Content-minimum fires on 7 of 53 real
fixture records (missing 33X) that convert perfectly well, and on LoC's own
test record, so it reports instead of rejecting. The numbers are in
[`plans/p-062-wire-validation.md`](plans/p-062-wire-validation.md).

Note what that demotion costs and where it is recovered: a record with no
245 passes Boundary 1 with only a flag, and is then **rejected at Boundary
2**, because the conversion of it demonstrably has no title rather than
merely looking thin. The fixture `99999902.xml` ("broken — missing 245") is
exactly this path.

## Layout

```
config/shapes/
├── bibframe-conversion.shape.ttl    # Boundary 2
└── bffi.shape.ttl                   # Boundary 3 — the big one

src/<package>/
├── schemas/
│   └── MARC21slim.xsd               # vendored from LoC, version pinned in a comment
└── validation/
    ├── marcxml.py                   # Boundary 1
    ├── bibframe.py                  # Boundary 2 (pyshacl wrapper)
    └── bffi.py                      # Boundary 3 (pyshacl wrapper)
```

## What each boundary checks

### Boundary 1 — MARCXML input

Two stages.

**Stage 1: XSD validation** against the LoC MARC21 slim schema using cached `lxml.etree.XMLSchema`.

**Stage 2: minimum-content check.** At least one 1XX/7XX (creator), one 245 (title), one 008, one 336/337/338 (RDA content/media/carrier).

The XSD catches malformed XML; the content check catches records too thin to produce useful BFFI. Failures are typed (`marcxml-xml-syntax`, `marcxml-xsd-validation`, `marcxml-content-minimum`) so you can grep the error log by category.

### Boundary 2 — BIBFRAME post-conversion

A small SHACL shape verifying what the conversion assumes from marc2bibframe2. Intentionally minimal — this validates "BIBFRAME the BFFI conversion can handle," not "correct BIBFRAME" (the latter is unbounded). Four constraints, each verified to hold on 515 converted records before it was written down:

- Every IRI `bf:Work` has a non-empty `bf:title` / `bf:mainTitle`.
- Every IRI `bf:Instance` has a non-empty `bf:title` / `bf:mainTitle`.
- Every IRI `bf:Instance` has an IRI-valued `bf:instanceOf` — the Work↔Instance link the BFFI routings walk.
- The main Work (`<base>#Work`) carries `bf:adminMetadata`.

Because this boundary **rejects**, the bar for adding a constraint is that it holds on every record, not most: at 800 000 records a rule that fails 1% discards 8 000 good records. Three things were measured and deliberately left out for exactly that reason — `bf:contribution` (24/25 curated, 10/319 probes), anything inside the AdminMetadata node, and `bf:identifiedBy` anywhere. `config/shapes/bibframe-conversion.shape.ttl` records each exclusion with its numbers.

**One check is not in the shape file.** "The graph contains at least one Work" is a statement about absence, and SHACL constrains focus nodes — an empty graph has none, so SHACL calls it conforming. That is precisely what a stylesheet run matching nothing produces, so it gets an explicit check: `validation.bibframe.missing_root_resources`, which runs alongside the shape and reports as `bibframe-empty`.

The shape previously required a local `bf:identifiedBy` and a `bffi:adminMetadata` block on every main Work and Instance, both stamped by a post-processor that does not exist in this repository, and so failed 100% of records for a reason unrelated to conversion quality. Rescoping it is [p-062](plans/p-062-wire-validation.md) Phase B; the shape file's header keeps the full account.

### Boundary 3 — BFFI post-CONSTRUCT

Every constraint derives from `vocab/lkd.rdf` — and only from `lkd.rdf`. The closed-namespace rule governs what a shape may *assert* as much as what the converter may emit: a shape constraining a term the ontology doesn't declare, or mandating a cardinality it never states, is a local invention wearing a shape's clothes. So the file restates three kinds of axiom and nothing else:

| Kind | Constraints |
|---|---|
| `owl:disjointWith` | `bffi:Work` / `bffi:Expression` — the only disjointness lkd.rdf declares between the axes |
| `rdfs:domain` | `bffi:content`, `bffi:summary` (Expression); `bffi:subject`, `bffi:classification`, `bffi:originDate`, `bffi:genreForm` (Work); the three axis links; `bffi:mainTitle` (Title) |
| `rdfs:range` | `bffi:title`, `bffi:identifiedBy`, `bffi:source`, `bffi:note`, `bffi:adminMetadata`, the three axis links |

Two subtleties make the difference between a useful boundary and noise:

- **`vocab/lkd.rdf` is passed to pyshacl as the ontology graph.** `sh:class` tests membership by walking `rdf:type/rdfs:subClassOf*` in the graph it can see, and the subclass axioms aren't in a converted record. Without lkd.rdf in scope, a correct `bffi:Local` identifier fails a `bffi:Identifier` range check — 348 phantom violations across 343 records.
- **`rdfs:range` infers a type; it doesn't forbid the absence of one.** An untyped external authority URI (90 of the emit's `bffi:source` values) satisfies the range check; a value typed as something *else* does not. Literals are excluded from that leniency — `bffi:title "Some title"` instead of a `bffi:Title` node is precisely the defect these checks exist for.

Result: 21 of 343 emitted records flagged, five distinct finding types, 15 ms/record. That is a boundary worth reading. The previous version of this shape flagged 342 of 343.

Removed in [p-062](plans/p-062-wire-validation.md) Phase C, with the reasons kept in the shape file's header: the `skos:prefLabel` requirement (a Skosmos-publishing concern from the original implementation — this repository publishes nothing to Skosmos and emits no `skos:prefLabel` at all), the FRBR-spine cardinality mandates (lkd.rdf declares the axis predicates, not that every Work must have one), the single-axis restrictions on `bffi:language` / `bffi:note` / `bffi:identifiedBy` (lkd.rdf gives all three `rdfs:domain rdfs:Resource`), and a `bf:source` path that could never match a hard-cut BFFI graph.

**Closed-namespace discipline** — no `bf:*` URI as a class or predicate on an emitted triple — stays where it was: counted per record by the stage as `closed_namespace_residue`, and pinned by `tests/unit/test_bffi_namespace_discipline.py`. The range checks now surface it from the other side too: a `bf:AbbreviatedTitle`-typed node is not a `bffi:Title`, which is how MARC 210's residue shows up by name.

`pyshacl` runs this on every conversion batch. Failures don't block the pipeline (you still get the data) but they surface in `_validation.jsonl` and emit a CLI warning.

## Wiring — what actually runs

Both forward stages validate by default. `--no-validate` turns it off for a
stage; `marc-to-bibframe` also takes `--no-strict-shapes` to downgrade
Boundary 2 from a rejection to a flag.

```sh
uv run bffi-pipeline marc-to-bibframe --input-dir … --output-dir $RUN/bibframe
#   → Boundary 1 before the XSLT, Boundary 2 over the written RDF/XML
uv run bffi-pipeline bibframe-to-bffi  --input-dir … --output-dir $RUN/bffi
#   → Boundary 3 over the written Turtle
```

Both shape boundaries run against the artifact **on disk** — the file the
next stage will read — not an in-memory intermediate. Cost is 18 ms/record
for Boundary 2 and 10 ms/record for Boundary 3, against ~200 ms+/record
for the two `xsltproc` spawns, which is why validation is on by default
rather than opt-in.

### The two sidecars

Written by `validation/sidecar.py` into the stage's output directory:

| File | Holds |
|---|---|
| `_errors.jsonl` | records the stage **rejected** — they are not in the output |
| `_validation.jsonl` | records the stage **kept and flagged** |

One JSON object per row: `boundary`, `error_type`, `bib_id`, `path`,
`message`, plus `violations` on shape rows. `boundary: 0` marks a
conversion failure rather than a validation finding — same sink, because
"what is missing from this output, and why" is one question. Rows carry no
timestamp, so the sidecars stay byte-deterministic; each file is truncated
on a stage's first row, so a re-run replaces rather than accumulates.

Record-level detail stays in these files and out of the stage-event
stream: a per-record event would put the record path into a Prometheus
label. Aggregate counts (`skipped_invalid`, `shape_flagged`) ride the
stage's `end` counters instead and land in
`bffi_stage_outcomes_total{outcome=…}`.

## What this isn't

Deliberate non-goals:

- **No validating intermediate rdflib graphs in unit tests.** The shapes are the contract; once they pass, unit tests check specific behaviors, not graph well-formedness.
- **No blocking on Boundary-3 validation in production runs.** At 800 k records, even a 0.1 % failure rate is 800 records — surface them in the report and triage. The CI gate is where strict blocking happens.
- **No tests that re-implement validation.** The shapes file *is* the test. Unit tests verify the shapes work by hand-crafting one valid graph and one invalid graph per shape and checking each is judged correctly.

## A note on the BFFI shape file

`bffi.shape.ttl` is going to be the most-edited artifact in the repo over time. Every BFFI version bump (1.0.0 → 1.1.0 will land BIBFRAME 3.0 PMO equivalents), every new Work or Expression subclass, every new property in the model — they all flow through this file. Treat it as living documentation: every change gets a comment explaining what real-world failure mode it catches, and every new shape gets a corresponding pair of unit-test fixtures (one valid, one invalid). It's the one file where stale shapes silently let bad data through and nobody notices for months.

It went stale in a more interesting way than that. Written for an implementation that published to Skosmos and minted a full FRBR spine, it survived as a description of a pipeline this repository no longer is — invisible because nothing called it. Two rules now guard against a repeat:

- **Every constraint cites its lkd.rdf axiom.** `tests/unit/test_bffi_shape.py` asserts the axiom is really there, so a constraint the ontology doesn't declare fails the suite rather than the data.
- **`tests/unit/test_bffi_namespace_discipline.py` scans `config/shapes/`.** It used to cover only `vocab.py` and `sparql/`, which is how three locally-minted `bffi:…Shape` node names lived here undisturbed. Shape-node names belong in `bffi-prov:`.
