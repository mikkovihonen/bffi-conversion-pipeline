# p-064 — Clear the Boundary-3 residue p-062 left behind

**Status: active.** Phase A (the two FRBR-axis families) has shipped. Phase B
(title shapes) and Phase C (the `expressionOf` domain family) are open.

## Problem

p-062 rescoped `config/shapes/bffi.shape.ttl` to what `lkd.rdf` actually
declares and turned Boundary 3 on for every record. It did not clear the
violations that remained. Over the 319 field-coverage probes, 10 records came
out flagged:

| Shape | Records | Cause |
|---|---|---|
| `bffi-prov:AxisLinkRangeShape` | 130, 1130, 1240 | A `bf:Hub` routed to `bffi:Expression` by its marcKey while something points at it with `bffi:expressionOf`, whose `rdfs:range` is `bffi:Work`. |
| `bffi-prov:ExpressionOfDomainShape` | 1100, 1110, 1111 | A `bf:Instance` *asserting* `bf:expressionOf`; the predicate's `rdfs:domain` is `bffi:Expression`. |
| `bffi-prov:WorkDomainShape` | 051, 1051 | `bffi:classification` on a `bffi:Item` (MARC 051) — `rdfs:domain` is `bffi:Work`. |
| `bffi-prov:MainTitleShape` + `bffi-prov:TitleRangeShape` | 210, 1210 | `bffi:mainTitle` asserted by a node that is not a `bffi:Title`. |

Every one of these records converts and round-trips; the shapes are reporting
genuine `lkd.rdf` mismatches in the emit, not broken records. Boundary 3 is
non-blocking, so the cost of leaving them is noise in `_validation.jsonl` that
hides a real regression when one lands.

## What measurement showed before writing any code

- `bffi:workManifested` is the Manifestation → Work link the emit actually
  produces: 346 of 346 Manifestations, always exactly one object.
  `bffi:manifestationOfWork` is never emitted, because `route_work_split`
  migrates `bf:hasInstance` to the BNode Expression first.
- Owners of the four Work-domain predicates: everything sits on a Work except
  `bffi:classification` on 2 `bf:Item` nodes (field-coverage probes 051 / 1051,
  MARC 051) and `bffi:genreForm` on 1 `bf:Instance` (real fixture record
  2394080). Nothing else is off-axis.
- The reverse converter reads a Hub by URI fragment plus `bffi:marcKey`, never
  by `rdf:type` — so retyping a Hub cannot change the reconstructed MARC. All 3
  affected records reconstruct byte-identical MARCXML after the retype.
- The reverse converter reads Work-domain predicates through
  `?m bffi:workManifested ?work`, so an off-axis term is not just a shape
  violation: it is silently dropped from the reconstructed MARC.

## Phase A — the two FRBR-axis families (shipped)

**Hub retype.** `route_hubs` forces `bffi:Work` on any Hub that is the target of
`bffi:expressionOf`, overriding the marcKey discriminator. Clears all 3
`AxisLinkRangeShape` violations; round-trip neutral (measured). The Expression
signal stays on the Hub's `bffi:marcKey`.

**Manifestation → Work lift.** `route_manifestation_work_domain_props` moves a
Work-domain predicate off a `bffi:Manifestation` onto the Work it manifests,
resolving through `bffi:workManifested` → `bffi:manifestationOfWork` → the
Expression detour, first non-empty shape winning, and lifting only when exactly
one Work resolves. Recovers the one real off-axis genre term (record 2394080)
into the reconstructed MARC.

**Item classification.** Not lifted. MARC 051 is the LC class number of one
specific copy; asserting it as the Work's classification would make the reverse
direction emit a MARC 050 the source never had. `lkd.rdf` declares
`bffi:classification` with `rdfs:domain bffi:Work` and offers no item-level
alternative, so `bffi:classification` moves to its own
`bffi-prov:ClassificationDomainShape`, which accepts `bffi:Work` or `bffi:Item`.
**This is an ontology gap to raise with NLF** — see the gap note in
`docs/bf_to_bffi_mapping.md`.

Result: 10 flagged records → 5.

## Phase B — the title shapes (open)

Records 210 / 1210 assert `bffi:mainTitle` on a node that is not a
`bffi:Title`. Not yet diagnosed: establish which marc2bibframe2 shape produces
it (MARC 210 abbreviated title is the likely source) before deciding between a
routing and a shape rescope.

## Phase C — `bf:Instance` asserting `bf:expressionOf` (open)

Records 1100 / 1110 / 1111. The mirror image of the Hub case: here the
*subject* is wrong for the predicate's domain, and the fix is a triple swap or
an axis-default pick rather than a retype. Needs the same
round-trip-neutrality measurement the Hub retype got before it ships.

## Out of scope

The emit is not byte-deterministic — two runs of identical code differ for 280
of 319 records, because `route_work_split` mints BNodes whose labels vary per
run and the Turtle serializer orders anonymous blocks by label. That
contradicts the idempotency rule in `CLAUDE.md` and is worth its own plan; it
is not a Boundary-3 problem.
