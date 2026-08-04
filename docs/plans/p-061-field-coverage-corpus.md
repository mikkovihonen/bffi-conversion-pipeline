# p-061 — Synthetic field-coverage corpus

**Status: completed.** The generator, the 319 probes, the generated README
with its predicted verdict per tag, and the `--check` guard in CI all
shipped.

The corpus is the instrument, not the reading. Committing a *measured*
verdict beside the predicted one — the first full run scored 2470 source
fields down to 1891 reconstructed with 0 fabricated — would be a separate
generated artifact and needs its own plan; see "Out of scope" below for
why the measurement deliberately isn't pinned in the test suite.

## Problem

Round-trip assumptions have been checked one field at a time, reactively:
655 was 100% lost, then 336, 370, 511 and 518. Each was found by noticing
a gap in a real-record run and tracing it by hand. There is no systematic
instrument that answers "for every MARC field the pipeline claims to
handle, what actually survives?"

The curated fixtures are real records: excellent for fidelity questions,
useless for coverage — they exercise whatever fields those particular
Helmet records happen to carry, which is a small and uneven slice.

## Design

**Two synthetic records per MARC tag.** `<tag>.xml` carries only the tag's
**primary** subfield; `1<tag>.xml` carries the **maximal** union of subfields
both directions know about. The pair is the diagnostic, and it earned its
keep immediately: a single maximal-only design reported MARC 020 as lost,
when in fact `020 $a` alone round-trips perfectly and it is the spurious
`$z`/`$q`/`$0` company that breaks it. Without the minimal variant, eight
identifier fields (010, 017, 020, 025, 027, 030, 032, 088) would have been
filed as pipeline gaps rather than subfield-combination breaks.

**Isolated content.** A record carries the
leader, `001`, `008`, a `245` (so Boundary-1 minimum content passes) and
*only the tag under test*. If a tag is lost, exactly one record shows it —
no cross-field interference, no invalid field combinations.

Filename and `001` always match (`655.xml` carries `001 = 655`; the maximal
`1655.xml` carries `001 = 1655`), which keeps both inside the bare-digits
filename pattern Boundary 1 accepts and makes `roundtrip-eval`'s 001 pairing
self-documenting.

Driven from the two existing coverage sources, so the corpus tracks the
code rather than rotting beside it:

| Source | Contributes |
|---|---|
| `parse_xslt_corpus()` | tags the XSLT reads, their subfield codes, indicator tests |
| `MARC_EMIT_REGISTRY` | tags the reverse converter emits, their subfields |

Subfield values are greppable sentinels (`Z655a`) except where a value
must be syntactically valid — vocabulary codes, authority URIs, relator
codes, dates — which come from a curated table.

The generated `README.md` is the point of the exercise: per tag it records
the subfields emitted and the **predicted** verdict from `cross_check`
(round-trippable / forward-only / reverse-only / handled by neither). A run
of the corpus then shows where reality departs from the prediction.

## Trade-offs on the record

- **Isolation over realism.** One tag per record cannot catch interaction
  bugs (a 245 that changes how 246 converts). It is the wrong instrument
  for that and the right one for coverage. Interaction cases stay with the
  curated fixtures.
- **`$6` and `$8` are excluded.** `$6` is the 880 alternate-script linkage
  and is meaningless without a paired 880; emitting it bare would produce
  a dangling link. The `non-latin-translations` fixtures already cover 880.
- **Generated, with a `--check` guard**, matching the three mapping docs.
  A submodule bump that changes the XSLT's field surface fails CI until
  the corpus is regenerated.
- **Not a validity claim.** These records are synthetic and minimal; they
  are not cataloguing examples and should never be mistaken for reference
  data. The README says so at the top.

## Measuring it honestly

Two axes, not one. "Tag absent from the reconstruction" conflates three
different things, and conflating them overstates the damage:

| Outcome | Meaning |
|---|---|
| values kept, same tag | round-trips |
| values kept, **retagged** | data survives under a different MARC tag — a 655 with subdivisions returns as 650, because marc2bibframe2 renders it as a complex subject typed `bffi:Topic` |
| values **lost** | genuinely gone |

The first pass at this analysis reported "59 asymmetric tags fully lost" by
checking tag presence alone. That number was wrong: 30 of those tags keep
their values under a different tag.

## Out of scope

- Asserting on the corpus in unit tests. It is a diagnostic instrument the
  operator runs, not a fixture the suite pins — pinning it would freeze the
  very drift it exists to surface.
