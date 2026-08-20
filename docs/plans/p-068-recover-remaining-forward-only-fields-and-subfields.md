# p-068 — Recover remaining forward-only MARC fields and subfields

**Status: completed.** Phase 1 complete: 020/024/028 `$q` recovered, 336/337/338 `$3` recovered. Phase 2 complete: 051/055/072 all skipped (no discriminators). Phase 3 complete: all docs up to date. Remaining subfields (257 $0, 080 $x, 240 $k, 505 $t, 490 $x, 490 $6) out of scope — no BFFI predicates exist.

This plan picks up where p-067 left off: the easy wins first (subfields
with clear BFFI predicates), then the three Phase A tags once their
discriminators are confirmed.

## Context

p-067 recovered 17 of 23 forward-only MARC fields whose data survives to
the BFFI graph. The remaining six are:

| MARC | Reason p-067 skipped it | Current status |
|---|---|---|
| `051` | Item-level classification needs `bffi:Item` axis verification | Needs discriminator investigation |
| `055` | Same as 051 — shares `bffi:classification` with no discriminator | Same |
| `072` | marcKey-driven dispatch assumed; XSLT does not set marcKey on 072 | Needs discriminator investigation |
| `048` | PMO data collapsed to opaque `readMarc382` literal | Out of scope (structural loss) |
| `382` | Same as 048 | Out of scope (structural loss) |
| `377` | Shares `bffi:language` with 041 on Work axis; no discriminator | Out of scope (shared predicate) |
| `656` | No marcKey on `bffi:subject` — could be 650/651/653/655/656/7XX | Out of scope (no discriminator) |
| `720` | No marcKey on contributor nodes | Out of scope (no discriminator) |
| `752` | No marcKey on hierarchical place nodes | Out of scope (no discriminator) |
| `753` | Shares `bffi:descriptionConventions` with 040 `$e` | Out of scope (no discriminator) |
| `758` | No marcKey on identifier nodes | Out of scope (no discriminator) |
| `254` | Shares `bffi:editionStatement` with 250 `$a` | Out of scope (no discriminator) |
| `256` | Generic `bffi:Note` could be any of 30+ note tags | Out of scope (no discriminator) |
| `341` | Shares `bffi:contentAccessibility` with 532 | Out of scope (no discriminator) |
| `034` | No discriminator from 255 — same predicates, same shapes | Out of scope (no discriminator) |
| `255` | No discriminator from 034 — same predicates, same shapes | Out of scope (no discriminator) |
| `340` | XSLT produces `bf:illustrativeContent` not in `lkd.rdf` | Out of scope (XSLT drop) |

The three tags that **might** be recoverable are 051, 055, and 072. They
were deferred in p-067 pending investigation of:

- **051/055**: Whether `bffi:Item` is reachable from Manifestation in the
  current BFFI graph (the 051 classification attaches to the Item in
  BIBFRAME, not the Work).
- **072**: Whether marcKey is set on 072's BFFI nodes (p-067 assumed it
  would be, but investigation showed the XSLT does not set marcKey on
  most subject/contributor tags).

Plus a set of subfields across existing tags that are missing from the
reconstruction but whose BFFI carriers may already exist.

## Phase 1 — Subfield recovery (quick wins)

Recover subfields that are missing from the reconstruction but whose BFFI
carriers already exist in the graph. Each subfield is a one-line change
to an existing emit rule's `subfields=` declaration and a one-line
addition to the extractor function.

### Subfield candidates

From `diagnose-marc-coverage` output against the curated corpus:

| Tag | Missing subfield | BFFI carrier | Implementation effort |
|---|---|---|---|
| `024` | `$q` (qualifier) | `bffi:qualifier` (literal) | Emit rule declares only `$a`; add `$q`. Code already emits it. |
| `257` | `$0` (authority) | ? | Check if `bffi:authority` or `bffi:authorityURI` exists |
| `080` | `$x` (nonfiling) | ? | Check if `bffi:nonfiling` or similar exists |
| `240` | `$k` (intermediate) | ? | Check if `bffi:abstract` or similar exists |
| `505` | `$t` (enumerated title) | ? | Check if `bffi:mainTitle` on nested structures exists |
| `020` | `$q` (qualifier) | `bffi:qualifier` (literal) | Emit rule declares only `$a`; add `$q`. Code already emits it. |
| `028` | `$q` (qualifier) | `bffi:qualifier` (literal) | Emit rule declares `$a`, `$b`; add `$q`. Code already emits it. |
| `490` | `$x` (ISSN/LCCN) | ? | Check if `bffi:issn` or similar exists |
| `490` | `$6` (occurrence id) | ? | Check if `bffi:occurrenceId` exists on series |

**Immediate fixes (no investigation needed):**

- `020 $q`: The `_IdentifierEmit` dataclass has a `qualifier` field that
  emits as `$q`. The emit rule's `subfields=` only declares `$a`. Update
  to `(("a", "ISBN value"), ("q", "physical description"))`.
- `024 $q`: Same pattern. Update emit rule's `subfields=` to include
  `("q", "physical description")`.
- `028 $q`: Same pattern. Update emit rule's `subfields=` to include
  `("q", "physical description")`.

These three are one-line changes that recover subfields already being
emitted but not declared in the registry.

**Investigation needed:**

For each remaining subfield, check:

1. Does `vocab/lkd.rdf` declare a matching BFFI predicate?
2. Does the XSLT produce that predicate in the BIBFRAME output?
3. Does the bibframe→bffi routing preserve it (not drop or rename)?
4. Does the current emit rule read it (or can it be added with one line)?

If all four answers are yes, implement. If any is no, skip with reason.

### Investigation procedure

For each candidate subfield:

```python
# 1. Check BFFI vocabulary
grep -i '<predicate>' vocab/lkd.rdf

# 2. Check XSLT output
grep -i '<predicate>' runs/*/bf/*.bibframe.xml

# 3. Check BFFI output
grep -i '<predicate>' runs/*/bffi/*.bffi.ttl

# 4. Check current emit rule
grep -A30 'tag="<tag>"' src/bffi_pipeline/stages/bffi_to_marc/runner.py
```

If the predicate exists in all three places, extend the emit rule.

## Phase 2 — Phase A remainder (051, 055, 072) — **all skipped**

Investigation complete. All three tags are unfixable due to lack of
discriminators in the BFFI form.

### 051 / 055 — Item-level classification — **skipped**

**Finding:** `bffi:Item` IS modeled in BFFI and IS reachable from
Manifestation via `bffi:hasItem`. Item-level classification survives the
forward conversion (confirmed by running the XSLT + bibframe→bffi on a
MARC 051 probe).

**But:** 051 and 055 produce **identical** `bffi:classification` structure
on the Item. Both emit `bf:ClassificationLcc` with `bf:classificationPortion`
($a) and `bf:itemPortion` ($b). The BFFI graph has no way to distinguish
which MARC tag produced it.

**Outcome: Skip with reason.** "051 and 055 produce identical
`bffi:classification` structure on the Item; no discriminator to
distinguish them. Implementing one without the other would emit fabricated
data."

### 072 — Subject/genre from non-vocabulary sources — **skipped**

**Finding:** The XSLT does NOT set marcKey on 072's BFFI nodes. 072
produces `bffi:subject` on the Work with `bffi:Topic` class — identical
structure to 650, 651, 653, 655, 656, and 7XX subject tags.

**Outcome: Skip with reason.** "No discriminator in BFFI form — 072
produces the same `bffi:subject` structure as 650/651/653/655/656/7XX.
Implementing 072 without being able to discriminate would emit fabricated
MARC fields."

| MARC | Reason for skipping |
|---|---|
| `051` | Identical BFFI structure to 055 on Item; no discriminator |
| `055` | Identical BFFI structure to 051 on Item; no discriminator |
| `072` | Identical `bffi:subject` structure to 650/651/653/655/656/7XX; no discriminator |

## Phase 3 — Verification and documentation

After Phases 1 and 2:

1. **Regenerate mapping documents:**
   ```sh
   uv run bffi-pipeline regenerate-marc-mapping
   uv run bffi-pipeline regenerate-marc-to-bibframe-mapping
   uv run bffi-pipeline regenerate-field-coverage-corpus
   ```

2. **Run diagnostics against curated corpus:**
   ```sh
   uv run bffi-pipeline diagnose-marc-coverage \
       --input-dir tests/data/sample-marcxml/curated
   ```

3. **Update p-067 plan status** to reflect recovered fields/subfields.

4. **Add integration tests** for each recovered subfield using curated
   records that previously lost data.

## Tests

### Per-subfield unit tests

Each recovered subfield gets a unit test in
`tests/unit/stages/bffi_to_marc/test_runner.py`:

```python
def test_024_emits_qualifier_when_present():
    """MARC 024 $q comes from bffi:qualifier literal."""
    # Build a graph with a bffi:Identifier carrying bffi:qualifier.
    # Run _extract_identifier_datafields.
    # Assert the emit includes ("q", expected_value).
```

### Field-coverage probes

Extend the field-coverage generator with probes that include the recovered
subfields:

| Tag | New probe | Subfields to verify |
|---|---|---|
| `020` | `020_with_q.xml` | `$a`, `$q` |
| `024` | `024_with_q.xml` | `$a`, `$q` |
| `028` | `028_with_q.xml` | `$a`, `$b`, `$q` |
| `257` | `257_with_0.xml` | `$a`, `$0` (if recovered) |
| `080` | `080_with_x.xml` | `$a`, `$x` (if recovered) |

### Integration test

```python
def test_subfield_recovery_curated_records():
    """Records that lost subfields before p-068 must reconstruct
    with those subfields after the fix."""
    expected = {
        # (bib_id, [(tag, [(code, value), ...])])
        "2088800": [("024", [("a", "655132003018"), ("q", "pelipakkaus")])],
        # ... more records as subfields are recovered
    }
    for bib_id, fields in expected.items():
        run = new_run()
        run_pipeline(bib_id, run)
        for tag, subs in fields:
            recon = load_marc(run["marc"] / f"{bib_id}.marcxml")
            # assert each subfield present with expected value
```

## Documentation updates

### Required (ship with each subfield recovery)

1. **Emit-rule `source=` / `notes=` for each new subfield.** These generate
   `docs/bffi_to_marc_mapping.md` automatically.
2. **`docs/bffi_to_marc_mapping.md` — Known limitations.** Add a row per
   new subfield if there is a caveat.
3. **`docs/marc_to_bibframe_mapping.md` — Round-trip cross-check.** The
   verdict for each newly-emitted subfield flips from `→ forward only`
   to `≠ asymmetric` (or `✓ round-trippable`).
4. **`docs/plans/p-067-recover-forward-only-marc-fields.md`** — update
   status to reflect recovered subfields.

### Generated artifacts (regenerated, committed as part of the phase commit)

- `docs/bffi_to_marc_mapping.md` — new rows + notes
- `docs/marc_to_bibframe_mapping.md` — verdict flips
- `tests/data/sample-marcxml/field-coverage/README.md` — new probes listed
- New probe `.xml` files

## Trade-offs on the record

- **Why recover subfields before new tags?** Subfield recovery is lower
  risk (one-line changes to existing emitters) and higher immediate
  impact (reduces round-trip diff count). New tags require discriminator
  investigation and may turn out to be unfixable.

- **Why not recover all subfields at once?** Each subfield recovery
  requires independent verification (BFFI predicate exists, XSLT produces
  it, routing preserves it, emit rule reads it). Grouping by tag keeps
  each PR focused.

- **Why investigate 051/055/072 before implementing?** p-067 deferred
  these pending discriminator investigation. Implementing without
  confirmed discriminators would emit fabricated data. Better to confirm
  unfixability now than to ship a buggy emitter.

- **Why not recover 048/382?** The PMO ensemble data is collapsed to an
  opaque literal by `route_music_medium`. No structured properties
  survive. Reconstructing MARC from the summary string is not
  round-trip-safe. Documented as out of scope.

- **Why not recover 254/256/341?** Same discriminator problem as 051/055/
  072 — shared predicates without marcKey. Documented as out of scope in
  p-067.

- **Why not recover 656/720/752/753/758?** No marcKey on BFFI nodes for
  subjects/contributors/hierarchical places. Documented as out of scope
  in p-067.

## Test plan summary

| Phase | Unit tests | Field probes | Integration |
|---|---|---|---|
| 1 (subfields) | 3–9 emitters (depending on investigation results) | 3–9 probes | Curated records for each recovered subfield |
| 2 (051/055/072) | 0–3 emitters (depending on investigation results) | 0–6 probes | Curated records if tags are recoverable |
| **Total** | **3–12** | **3–15** | **4–10 curated records** |

## Open questions

- **051/055 item axis.** Need to verify whether `bffi:Item` is reachable
  from Manifestation in the current BFFI graph. If Items are not modeled
  separately, these tags are effectively unrecoverable.

- **072 discriminator.** Need to verify whether marcKey is set on 072's
  BFFI nodes, or whether another discriminator exists. If not, 072 is
  unfixable without upstream XSLT changes.

- **Subfield BFFI carriers.** Need to verify whether `bffi:authority`,
  `bffi:nonfiling`, `bffi:abstract`, `bffi:mainTitle` (on nested
  structures), `bffi:issn`, `bffi:occurrenceId` exist in `lkd.rdf` and
  are preserved by the XSLT + routing.

## References

- p-067: `docs/plans/p-067-recover-forward-only-marc-fields.md`
- Round-trip debugging: `docs/roundtrip-debugging.md`
- BFFI vocabulary: `vocab/lkd.rdf`
- Diagnostic tool: `uv run bffi-pipeline diagnose-marc-coverage --help`
