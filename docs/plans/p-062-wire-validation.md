# p-062 — Wire the three validation boundaries into the stages

**Status: completed.** Phase A wires Boundary 1 and the two sidecars;
Phase B rescopes the BIBFRAME shape and turns Boundary 2 into a gate;
Phase C rescopes the BFFI shape to what `vocab/lkd.rdf` actually declares.
All three validation boundaries now run on every conversion.

## Problem

`docs/validation-strategy.md` describes three validation boundaries as
though they run. They don't. All three modules under
`src/bffi_pipeline/validation/` are implemented and `mypy --strict`-clean,
and **no stage calls any of them** — the only importer in the tree is
`tests/unit/test_marcxml_validation.py`. Nothing writes the
`_errors.jsonl` the doc names as the rejection sink, and nothing writes
`_validation.jsonl` either. A malformed record's first contact with the
pipeline is `xsltproc`.

## What measurement showed before writing any code

Running the validators by hand over the fixture corpus and over an
existing run's outputs, which is what a plan for this should have started
with:

| Check | Result |
|---|---|
| Boundary 1 over 53 real fixture records | 46 pass; **7 fail `marcxml-content-minimum`**, all for a missing 336/337/338 |
| Boundary 1 over the vendored LoC sample (`third_party/.../test/data/marc.xml`) | fails `marcxml-content-minimum` — no 33X |
| Boundary 1 over the 319 field-coverage probes | 317 fail `marcxml-content-minimum`; **2 fail `marcxml-xsd-validation`** |
| Boundary 2 over 5 converted records | **0 conform** (5–11 violations each) |
| Boundary 3 over 5 emitted records | **0 conform** (6–28 violations each) |
| Boundary 2 cost | 18 ms/record (parse + pyshacl) |
| Boundary 3 cost | 10 ms/record |

Three conclusions follow, and they shape the design:

**1. Content-minimum cannot reject.** It fires on 7 real
cataloguer-picked records and on LoC's own test record — all of which
convert fine. A gate that drops those is dropping data the pipeline
handles. So Boundary 1 splits by severity rather than acting as one gate.

**2. Both SHACL shapes describe a pipeline this repository doesn't have.**
`bibframe-conversion.shape.ttl` requires a local `bf:identifiedBy` and a
`bffi:adminMetadata` block on every main Work and Instance, and its own
comments reference `stages.marc_to_bf._find_root_resources` and a
post-processor that stamps them. No such module or stage exists here —
`marc-to-bibframe` is a thin `xsltproc` wrapper. Likewise
`bffi.shape.ttl` asserts a fully FRBRised graph (`bffi:hasExpression`,
`bffi:expressionOf`, `bffi:expressionManifested`, Manifestation-only
`bffi:identifiedBy`), while `bibframe-to-bffi` is a rename + routings
pass that keeps marc2bibframe2's `#Work` / `#Instance` node identities and
mints no FRBR axes. Both shapes fail 100% of records for that reason, not
because the conversion is broken. Wiring either as a gate today would
skip the entire corpus.

**3. Cost is not the constraint.** 28 ms/record for both shape checks
against ~200 ms+/record for the two `xsltproc` spawns. Roughly 6 hours
added to an 800k run that is already tens of hours. Validation is
therefore **on by default**, not opt-in.

## Design

**Boundary 1 splits by severity.** `validation.marcxml.inspect()` returns
a `Boundary1Outcome` carrying at most one rejection and at most one
advisory:

| Family | Checks | Action |
|---|---|---|
| structural | filename, encoding, XML syntax, XSD | **reject** — record is skipped, row in `_errors.jsonl` |
| advisory | content-minimum | **keep** — record converts, row in `_validation.jsonl` |

The split is not arbitrary: a structural failure means `xsltproc` cannot
read the file at all, so rejecting costs nothing that wasn't already lost.
A content-thin record converts.

**Boundaries 2 and 3 run against the artifact on disk** — the file the next
stage will actually read — not an in-memory intermediate. Boundary 2 rejects
by default since Phase B (output and its `.prov.ttl` are removed so the next
stage never sees a non-conforming record); `--no-strict-shapes` downgrades
it to a flag. Boundary 3 is specified non-blocking and stays that way:
non-conformance writes one `_validation.jsonl` row carrying the violation
count.

**Two sidecars, two severities**, written into the stage's output
directory by `validation.sidecar`:

```
runs/<run>/bibframe/
  _errors.jsonl        # rejected: this record did not convert
  _validation.jsonl    # kept, but flagged
```

Rows are JSON objects with `boundary`, `error_type`, `bib_id`, `path`,
`message`, and `violations` for shape reports. No timestamps — the rows
stay byte-deterministic, unlike the provenance sidecars. Each file is
truncated on its first row of a run, so a re-run into the same directory
replaces rather than accumulates.

**Counts reach the dashboard through the existing `end` counters** —
`skipped_invalid` and `shape_flagged` become
`bffi_stage_outcomes_total{outcome=…}` with no exporter change. Per-record
detail deliberately does *not* go to the event stream: a `failed` event
per rejected record would put the record path in a metric label, which is
the cardinality bug already fixed once in `46a55c2`. The sidecar is the
record-level sink.

**`--no-validate`** on both forward stages turns the whole thing off, for
diagnostic runs over deliberately-thin input (the field-coverage corpus is
exactly that) and for reproducing a pre-validation baseline.

## Trade-offs on the record

- **Content-minimum demoted from the doc's stated behaviour.**
  `docs/validation-strategy.md` said "skip record" for all of Boundary 1.
  The measurement above says that would drop convertible real records, so
  the doc is amended rather than the check being enforced as written.
- **Filename is structural, so it rejects.** `bib_id` is derived from the
  filename, so an unparseable name means an unidentifiable record. The
  cost is that input from a source with other naming (`marc.xml`) is now
  rejected instead of silently converted under a junk bib ID.
- **Shape checks validate the file, not the graph in hand.** One extra
  parse per record (~10 ms). Bought deliberately: it tests the artifact
  the pipeline hands on, which is where a serialisation-level defect
  would actually bite.
- **Boundary-3 non-conformance is reported, not suppressed.** Once both
  shapes were rescoped (Phases B and C), the flagged counts became signal:
  0 of 343 records fail Boundary 2, and 21 fail Boundary 3 with findings
  that each name a specific defect. Before that, both boundaries flagged
  essentially every record, which is the same as flagging none.

## Phase B — rescope `bibframe-conversion.shape.ttl` — **shipped**

The local-identifier and AdminMetadata-block expectations that belonged to
the absent post-processor are gone. What replaced them was chosen by
measuring 515 converted records (25 curated real fixtures, 319
field-coverage probes, 171 mixed fixtures) and keeping only invariants that
held on **all** of them:

| Constraint | Held |
|---|---|
| every IRI `bf:Work` has a non-empty `bf:title` / `bf:mainTitle` | 515/515 |
| every IRI `bf:Instance` has a non-empty `bf:title` / `bf:mainTitle` | 515/515 |
| every IRI `bf:Instance` has an IRI-valued `bf:instanceOf` | 515/515 |
| the main `<base>#Work` carries `bf:adminMetadata` | 515/515 |

Measured and **rejected** as constraints, with the numbers that disqualified
them:

| Candidate | Held | Why not |
|---|---|---|
| `bf:contribution` on the Work | 24/25 curated, 10/319 probes | a record with no 1XX/7XX correctly has none |
| `bf:adminMetadata` on *every* IRI Work | 467/515 | related / contained Works legitimately carry no admin block — target narrowed to the main Work instead |
| `bf:generationProcess` inside AdminMetadata | 0/515 | records carry several admin blocks; only some carry it |
| `bf:identifiedBy` anywhere | 22/25 curated Instances, 31/319 probes | the post-processor's triple, not the stylesheet's |

The bar is "holds on every record", not "holds on most", because this
boundary rejects: at 800k records a constraint that fails 1% of the time
discards 8 000 good records.

**The absence check moved into code.** "The graph contains at least one
Work" has no focus node when it fails, so SHACL reports an empty graph as
conforming — the one failure mode most worth catching. It is now
`validation.bibframe.missing_root_resources`, reported as `bibframe-empty`.

**`--strict-shapes` became the default**, so the flag is now
`--no-strict-shapes`. Verified after the flip: 25/25 curated and 319/319
probes convert with zero rejections, and across all 383 fixture records the
only three rejections are the three fixtures that exist to be broken —
`99999900` (Latin-1), `99999901` (XSD-invalid) and `99999902` (missing 245).

That last one is the part worth noting. Demoting content-minimum in Phase A
let a title-less record through Boundary 1 with a flag; Boundary 2 now stops
it, on the evidence that its *conversion* has no title rather than on a
heuristic about its input. The gate came back one hop later and better
grounded.

## Phase C — rescope `bffi.shape.ttl` to lkd.rdf — **shipped**

The framing in the Phase-A analysis was wrong, and the operator corrected
it: this shape isn't a target-state description worth preserving, it is
**outdated**. It was written for the original implementation, which
published to Skosmos and minted a full FRBR spine. Neither is what this
repository does. So the rule for Phase C is the same one that governs the
emit: **the shape may only assert what `vocab/lkd.rdf` declares.**

What that leaves, all of it restating an lkd.rdf axiom:

| Kind | Terms |
|---|---|
| `owl:disjointWith` | `bffi:Work` / `bffi:Expression` — the only axis disjointness lkd.rdf declares |
| `rdfs:domain` | `content`, `summary` → Expression; `subject`, `classification`, `originDate`, `genreForm` → Work; the three axis links; `mainTitle` → Title |
| `rdfs:range` | `title`, `identifiedBy`, `source`, `note`, `adminMetadata`, the three axis links |

What came out, and what made each one indefensible:

| Removed | Why |
|---|---|
| `skos:prefLabel` in fi/sv/en on every Work | Skosmos-era. **0** `skos:prefLabel` triples exist across 343 emitted records; the constraint fired 450 times on 24 records |
| `hasExpression` ≥ 1 per Work, `expressionManifested` = 1 per Manifestation | lkd.rdf declares the predicates' domain and range, not their obligatory presence. Fired on 225/225 Works, 187 of which are MARC 700/730/76X hub Works with no expression of their own |
| `language` / `note` / `identifiedBy` confined to one axis | lkd.rdf gives all three `rdfs:domain rdfs:Resource`. The pipeline's own emit disagreed (25 Works carry `bffi:language`), as does the reverse converter, which reads language off all three axes for MARC 041 |
| `bf:source` inside the identifier check | A `bf:*` predicate cannot appear in a hard-cut BFFI graph, so it could never match |

Two implementation details did most of the work:

- **lkd.rdf is now passed to pyshacl as the ontology graph.** `sh:class`
  walks `rdf:type/rdfs:subClassOf*` in the graph it can see, and subclass
  axioms live in the ontology, not in a record. Without it, every correct
  `bffi:Local` identifier failed a `bffi:Identifier` range check — 348
  phantom violations, the validator's fault rather than the converter's.
- **`rdfs:range` infers a type, it does not forbid the absence of one.**
  Each range check also accepts a value with no `rdf:type` — 90 of the
  emit's `bffi:source` values are bare authority URIs — but only for IRIs
  and blank nodes. A literal is untyped too, and `bffi:title "Some title"`
  where a `bffi:Title` node belongs is exactly what these checks are for.

**Result: 21 of 343 records flagged, 5 finding types, 15 ms/record**, against
342 of 343 before. Every finding looks real:

| Count | Finding |
|---|---|
| 13 | `bffi:expressionOf` points at a node typed `bffi:Expression`, not a Work — an Expression claiming another Expression as its Work |
| 3 | a Work-domain predicate on a node that isn't a Work |
| 3 | `bffi:expressionOf` asserted by something that isn't an Expression |
| 2 | `bffi:mainTitle` on a `bf:AbbreviatedTitle`-typed node (MARC 210 probes) |
| 2 | `bffi:title` pointing at that same non-`bffi:Title` node |

The last two are the closed-namespace residue the stage already counts —
`bf:AbbreviatedTitle` survives because lkd.rdf declares no counterpart —
now named per record and per predicate instead of only counted. Fixing the
underlying routings is separate work, deliberately not folded in here.

**Shape-node names moved to `bffi-prov:`.** `bffi:WorkShape` and friends
were locally-minted terms in a closed namespace, in both shape files (four
of them mine, added in Phase B). The namespace-discipline test scanned
`vocab.py` and `sparql/` but never `config/shapes/`, which is how they went
unnoticed; it now scans the shapes too.

## Out of scope

- Validation for `bffi-to-marc` and `roundtrip-eval`. The round-trip diff
  is that direction's validation.
- **Fixing what Boundary 3 now reports.** The 21 flagged records point at
  real routing defects (`bffi:expressionOf` pointing at an Expression, the
  `bf:AbbreviatedTitle` residue). Surfacing them was this plan's job;
  repairing them is emit work and wants its own plan.
- **Whether the emit should mint a full Work → Expression → Manifestation
  spine.** The committed URI namespaces in `CLAUDE.md` imply it should, and
  nothing does today. That is a mapping question for NLF, and it is now
  clearly separated from "is the shape correct?" instead of being tangled
  up in it.
- The `_errors.jsonl` schema as a published contract. It is an operator
  diagnostic for now.
