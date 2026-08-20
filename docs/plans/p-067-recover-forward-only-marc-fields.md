# p-067 — Recover MARC fields the XSLT reads but the reverse converter does not emit

**Status: proposed.** Fifty-one MARC tags pass through the forward XSLT but
never reach the reconstructed MARCXML. This plan recovers the fields whose
data survives to the BFFI graph and for which `lkd.rdf` already provides a
carrier. The nine fields that the XSLT drops silently, the eleven
already-documented losses (040 `$a`/`$d`, 09X, 388, …), and the
`bflc:`-namespace terms are recorded as out-of-scope with a reason.

## Problem

`docs/marc_to_bibframe_mapping.md` § Round-trip cross-check vs
`bffi-to-marc` classifies every MARC tag the XSLT reads. Of 168 unique tags,
51 are **forward-only**:

```
✓ round-trippable:  2    ≠ asymmetric: 115    → forward only: 51    ← reverse only: 7
```

The forward-only set breaks down into three causes:

1. **The XSLT reads the tag but emits nothing.** The data never leaves the
   forward stage. Per `CLAUDE.md`, `third_party/marc2bibframe2/` is
   wrap-don't-fork. These are **unrecoverable** here.
2. **The XSLT emits BIBFRAME output and the `bf:*` → `bffi:*` mapping has
   a clean or routed replacement, but the reverse converter has no emit
   rule for the resulting BFFI predicate.** These are the target of this
   plan.
3. **Known limitations already documented.** 040 `$a`/`$d` (commented out in
   the XSLT), 091–097 (no XSLT template), 388 (no template at all), the
   already-documented per-tag notes in `bffi_to_marc_mapping.md`.

This plan targets cause 2 only.

### What measurement showed before writing any code

- Inspected every `runs/*/bffi/*.bffi.ttl` for predicates that match the
  XSLT output of forward-only tags. `bffi:temporalCoverage`,
  `bffi:originDate`, `bffi:validDate`, `bffi:copyrightDate`,
  `bffi:collectionArrangement`, `bffi:geographicCoverage`,
  `bffi:contentAccessibility`, `bffi:DescriptionAuthentication`,
  `bffi:MusicMedium` (literal), `bffi:musicKey` (literal),
  `bffi:cartographicAttributes`, `bffi:coordinates`, `bffi:Scale`,
  `bffi:SystemRequirement`, `bffi:SupplementaryContent`,
  `bffi:originPlace` (non-370), `bffi:language` (non-041/377),
  `bffi:GenreForm` (non-655), `bffi:collectionOrganization`,
  `bffi:editionStatement` (non-250), `bffi:shelfMark` — all confirmed
  present in at least one BFFI graph, proving the data survives to BFFI.
- Verified that the `bf:*` → `bffi:*` mapping in `bf_to_bffi_mapping.md`
  gives a **clean** or **routed** (not `GAP`) replacement for every
  forward-only tag except 038, 385, 386 (all `bflc:` namespace) and the
  XSLT-dropped set.
- Verified that no existing emit rule consumes any of the target BFFI
  predicates. Every target predicate has zero reverse consumers today.

## Design

Six phases, grouped by domain so each PR lands a coherent set of emit
rules and a coherent set of field-coverage probes.

### Phase A — Classification & identifier tags

MARC fields: **023, 026, 037, 051, 055, 072, 086, 353, 383**.

These share a simple shape: `?m bffi:identifiedBy [...]` (identifier
schemes) or `?work bffi:classification [...]` (classification with `$2`).
The identifier-scheme routing already exists (`route_identifier_schemes`);
the classification dispatch already exists (`emit_classification_datafield`,
used by 050/060/070/080/082/084/088).

Subfield-to-predicate dispatch:

| MARC | BFFI carrier | Subfields | Emitter |
|---|---|---|---|
| `023` | `bffi:Identifier` + `bffi:source <…/identifiers/issn-l>` | `$a` | Identifier scheme emitter (existing, new scheme) |
| `026` | `bffi:Identifier` + `bffi:source <…/identifiers/fingerprint>` | `$a` | Identifier scheme emitter (existing, new scheme) |
| `037` | `bffi:AcquisitionSource`, `bffi:acquisitionTerms`, `bffi:Identifier` (`stock-number`) | `$a, $b, $c, $f, $g, $n` | New emit: multi-predicate bnode |
| `051` | `bffi:classification` (`bffi:ClassificationLcc`) + `bffi:note` | `$a, $b, $c` | Classification emitter; hang from item-level Work via `bffi:workManifested` |
| `055` | `bffi:classification` (`bffi:ClassificationLcc`) | `$a, $b` | Classification emitter |
| `072` | `bffi:subject` / `bffi:genreForm` (`bffi:Topic`) + `bffi:source` | `$a, $2, $x` | Subject emitter; marcKey-driven dispatch to `072` tag |
| `086` | `bffi:classification` (`bffi:Classification`) + `bffi:source` + `bffi:Status` | `$a, $b, $2, $z` | Classification emitter (generic subclass) |
| `353` | `bffi:supplementaryContent` + `bffi:Identifier` | `$a, $b, $0, $2` | New emit: supplementary content |
| `383` | `bffi:Identifier` (`opus-number` / `serial-number`) | `$a, $b, $c, $d, $e, $3` | Identifier scheme emitter (two schemes) |

**Indicator logic.** `051` ind1/ind2 come from the MARC source via marcKey.
`072` ind2=0 is the only value the XSLT emits. `086` ind1=0/1 from marcKey.
All others `##`.

### Phase B — Temporal, geographic, place & language

MARC fields: **043, 045, 046, 257, 377**.

These all have simple literal or bnode-bearing predicates in `lkd.rdf`.

| MARC | BFFI carrier | Subfields | Notes |
|---|---|---|---|
| `043` | `bffi:geographicCoverage` (URI reference) | `$a` | Same predicate as 522; discriminate by shape — URI reference = `043`, bnode with `bffi:GeographicCoverage` = `522` |
| `045` | `bffi:temporalCoverage` (literal) | `$a, $b` | Plain literal emit, no discriminator |
| `046` | `bffi:originDate`, `bffi:validDate` (literals) | `$k`, `$l`, `$m`, `$n` | Two predicates from one MARC field; each subfield maps to one literal |
| `257` | `bffi:originPlace` (bnode) | `$a, $2` | Same predicate as 370; discriminate by origin — `Instance` = `257`, `Work` = `370` |
| `377` | — | — | **Skip**: shares `bffi:language` with 041 on the Work axis with no discriminator. Document as out-of-scope. |

**Discrimination approach.** None of these tags carry `marcKey` in the XSLT
output (marcKey is only set on titles/names/series, not on geographic/temporal/
place/language fields). The reverse converter discriminates by:

- **Shape:** `043` produces a `bffi:geographicCoverage` URI reference; `522` produces
  a `bffi:GeographicCoverage` bnode.
- **Origin:** `257`'s `bffi:originPlace` lives on the `bf:Instance`; `370`'s lives
  on the `bf:Work`.
- **No discriminator needed:** `045` and `046` produce plain literals on unique
  predicates (`bffi:temporalCoverage`, `bffi:originDate`, `bffi:validDate`) with no
  collision.

`377` shares `bffi:language` on the Work axis with 041 and carries no
marcKey discriminator. It is skipped — same situation as the `bflc:` terms
in Phase A's out-of-scope list.

### Phase C — Music tags (PMO terms)

MARC fields: **048, 382, 384**.

BFFI 1.0.0 predates BIBFRAME 3.0.1's PMO absorption; these terms are
`GAP` in the auto-table but the routing already collapses them to literal
carriers (`bffi:MusicMedium`, `bffi:musicKey`). The reverse path only needs
to read the literals and reassemble the MARC subfields.

| MARC | BFFI carrier | Subfields | Notes |
|---|---|---|---|
| `048` | `bffi:MusicMedium` (literal) | `$a, $b` | `bffi:readMarc048` literal; parse count + ensemble from semicolon |
| `382` | `bffi:MusicMedium` (literal) + `bffi:readMarc382` | `$2, $3` | Same literal carrier as 048; discriminator is marcKey |
| `384` | `bffi:musicKey` (literal) | `$a, $3` | Single literal value |

The `route_music_medium` and `route_music_key` routings in
`bibframe_to_bffi/routings.py` already collapse the structured PMO trees
to literals. The reverse path reads those literals back.

### Phase D — Cartographic & physical medium

MARC fields: **034, 255, 340, 352**.

| MARC | BFFI carrier | Subfields | Notes |
|---|---|---|---|
| `034` | `bffi:cartographicAttributes` (`bffi:Cartographic`) + `bffi:coordinates` + `bffi:scale` | `$a–$g, $3` | Subfield-to-predicate dispatch |
| `255` | `bffi:cartographicAttributes` + `bffi:scale` + `bffi:Projection` | `$a–$g, $6` | Same shape as 034; discriminate by marcKey |
| `340` | `bffi:SystemRequirement` | `$a–$q, $2` | Dynamic XSLT constructor; subfields map to `bf:SystemRequirement` properties |
| `352` | `bffi:CartographicObjectType` + `bffi:count` + `bffi:digitalCharacteristic` | `$a, $b, $6, $q` | Three predicates from one field |

### Phase E — Archival, admin & content

MARC fields: **042, 254, 256, 341, 351**.

| MARC | BFFI carrier | Subfields | Notes |
|---|---|---|---|
| `042` | `bffi:DescriptionAuthentication` | `$a` | Single literal emit |
| `254` | `bffi:editionStatement` | `$a` | Same predicate as 250 `$a`; discriminate by marcKey `254` |
| `256` | `bffi:Note` | `$a` | Generic note; discriminate by marcKey prefix `256` (the XSLT wraps it in a plain `bf:Note`) |
| `341` | `bffi:contentAccessibility` | `$a–$e, $2, $3` | Same predicate as 532; discriminate by marcKey |
| `351` | `bffi:collectionArrangement` | `$a, $b, $c, $3` | New predicate, no existing consumer |

### Phase F — Subject / name tags with rich subfield sets

MARC fields: **656, 720, 752, 753, 758**.

These all use marcKey-driven dispatch and follow the pattern proven by
730/740/130/240.

| MARC | BFFI carrier | Subfields | Notes |
|---|---|---|---|
| `656` | `bffi:GenreForm` + `bffi:source` | `$0, $2, $3, $a, $k, $v, $w, $x, $y, $z` | marcKey prefix `656`; same predicate as 655, different tag |
| `720` | Various subject/contributor predicates | `$0, $1, $5, $6, $t` | marcKey prefix `720`; uncontrolled added entry |
| `752` | `bffi:originPlace` chain | `$0, $2, $4, $a–$h, $w` | marcKey prefix `752`; hierarchical place (dynamic XSLT constructor) |
| `753` | `bffi:descriptionConventions` | `$2, $6, $a, $b, $c` | marcKey prefix `753`; same predicate as 040 `$e`, different tag |
| `758` | `bffi:Identifier` (`isan`) | `$0, $1, $4, $6, $a, $i` | marcKey prefix `758`; identifier scheme routing |

### Out of scope (recorded with reason)

| MARC | Reason |
|---|---|
| `036` | XSLT reads subfields but emits no BIBFRAME output. No data in BFFI. |
| `344` | XSLT reads `$6` only. No BIBFRAME output. |
| `345` | XSLT reads `$6` only. No BIBFRAME output. |
| `346` | XSLT reads `$6` only. No BIBFRAME output. |
| `347` | XSLT reads `$6` only. No BIBFRAME output. |
| `348` | XSLT reads `$6` only. No BIBFRAME output. |
| `362` | XSLT reads `$6 $a` but emits nothing. No data in BFFI. |
| `380` | XSLT reads `$6 $a` but emits nothing. No data in BFFI. |
| `859` | XSLT reads `$6` only. No BIBFRAME output. |
| `038` | XSLT emits `bflc:MetadataLicensor`. `bflc:` is a LoC-local namespace;
no BFFI equivalent. Would require bffi-prov sentinel or NLF proposal. |
| `385` | XSLT emits `bflc:DemographicGroup`. Same issue as 038. |
| `386` | XSLT emits `bflc:DemographicGroup`. Same issue as 038. |
| `091–097` | No XSLT template; drops through "drop unhandled datafield" path.
Already documented in `bffi_to_marc_mapping.md` § 084 notes. |
| `388` | No XSLT template at all. Requires upstream LoC contribution.
Already documented in `roundtrip-debugging.md`. |
| `040` `$a`, `$d` | Commented out in `ConvSpec-010-048.xsl` v3.1.0. Already
documented. |
| `016`, `074` | Dormant pending marcKey attachment in the BFFI conversion pass.
Out of scope for this plan. |

## Implementation notes

### Shared patterns already in the codebase

Every phase reuses existing infrastructure:

- **Identifier scheme dispatch.** `route_identifier_schemes` walks every
  `bf:Identifier` subclass in BIBFRAME. Adding `issn-l`, `fingerprint`,
  `opus-number`, `serial-number`, `isan` as new emit schemes requires one
  entry in the LoC URI map and one line in the identifier emitter.
- **Classification dispatch.** `emit_classification_datafield` handles
  `050/060/070/080/082/084/088`. New subclasses (`051` on item, `055`,
  `072`, `086`) plug in via the existing `bffi:classificationPortion` /
  `bffi:source` pattern.
- **marcKey-driven dispatch.** `_parse_marc_key` +
  `_MARCKEY_TAGS_CLAIMED_ELSEWHERE` + the `bffi:marcKey` literal carrier
  handle 730/740/130/240. Phases E (254, 256, 341, 753) and F (656, 720,
  752, 758) follow the same pattern.
- **Music literal collapse.** `route_music_medium` and `route_music_key`
  already reduce PMO structured trees to `bffi:MusicMedium` /
  `bffi:musicKey` literals. The reverse path reads them back.
- **Inverse axis traversal.** Phases B/D walk `?m bffi:workManifested ?work`
  to reach Work-axis predicates (classification, geographic coverage,
  cartographic attributes) — the same pattern proven for 336 in p-064's
  analysis.

### Discrimination strategy

Where two MARC tags share a BFFI predicate (043/522, 257/370, 377/041,
254/250, 341/532, 656/655, 753/040), the discriminator is always
`bffi:marcKey` — the XSLT tags these with a marcKey literal whose prefix
is the source MARC tag. The reverse path dispatches via the same
`_MARCKEY_TAGS_CLAIMED_ELSEWHERE` lookup used for 730/740.

No emit rule asserts content from `bffi-prov:` (pipeline-internal provenance).
All discrimination uses predicates in the published BFFI schema or
`bffi:marcKey` (an `owl:equivalentProperty` of `bflc:marcKey`, in the
published vocabulary).

## Measurement

### Before / after (predicted)

| Metric | Before | After (all phases) |
|---|---|---|
| Forward-only tags | 51 | ~23 (9 XSLT-dropped + 12 already-documented + 3 bflc remain) |
| Tags now round-tripping | 115 | ~138 |
| New MARC fields emitted | 124 (current) | 147 (124 + 23 new) |
| Fabricated fields | 0 | 0 (all new emits are gated by marcKey or by a predicate
with no other consumer) |

### Regression guard

- Every tag that currently round-trips must still round-trip after each
  phase. Phases are independent — each adds new emit rules that fire only
  when a predicate or marcKey prefix the existing rules don't cover.
- Field-coverage probes exercise the new shapes (see Tests).
- Curated records that previously lost data in these tags verify
  end-to-end.

## Tests

### Per-phase fixtures

The field-coverage generator (`tests/data/sample-marcxml/field-coverage/`)
produces one minimal + one maximal probe per handled tag. Each phase
extends the generator with new `FieldCase` entries. The generator
deletes unlisted files on regeneration, so hand-crafted probes cannot live
there — use `indicator_variant` or a subfield variant to produce distinct
files from the same generator.

New probes per phase:

| Phase | New probes |
|---|---|
| A | `023.xml`, `1023.xml`, `026.xml`, `1026.xml`, `037.xml`, `1037.xml`,
`051.xml`, `1051.xml`, `055.xml`, `1055.xml`, `072.xml`, `1072.xml`,
`086.xml`, `1086.xml`, `353.xml`, `1353.xml`, `383.xml`, `1383.xml` |
| B | `043.xml`, `1043.xml`, `045.xml`, `1045.xml`, `046.xml`, `1046.xml`,
`257.xml`, `1257.xml`, `377.xml`, `1377.xml` |
| C | `048.xml`, `1048.xml`, `382.xml`, `1382.xml`, `384.xml`, `1384.xml` |
| D | `034.xml`, `1034.xml`, `255.xml`, `1255.xml`, `340.xml`, `1340.xml`,
`352.xml`, `1352.xml` |
| E | `042.xml`, `1042.xml`, `254.xml`, `1254.xml`, `256.xml`, `1256.xml`,
`341.xml`, `1341.xml`, `351.xml`, `1351.xml` |
| F | `656.xml`, `1656.xml`, `720.xml`, `1720.xml`, `752.xml`, `1752.xml`,
`753.xml`, `1753.xml`, `758.xml`, `1758.xml` |

### Per-phase unit tests

Each phase adds unit tests to
`tests/unit/stages/bffi_to_marc/test_runner.py`:

- **Phase A:** One test per new emitter verifying that the expected MARC
  datafield is produced from a graph carrying the corresponding BFFI
  structure. Identifier scheme tests cover the LoC URI → MARC tag dispatch.
- **Phase B:** Tests for marcKey discrimination on shared predicates
  (043 vs. 522, 257 vs. 370, 377 vs. 041).
- **Phase C:** Tests for parsing `bffi:MusicMedium` / `bffi:musicKey`
  literals back to MARC subfields.
- **Phase D:** Tests for subfield-to-predicate dispatch in cartographic
  fields.
- **Phase E:** Tests for discrimination on shared predicates (254 vs. 250,
  341 vs. 532, 753 vs. 040).
- **Phase F:** Tests for marcKey-driven dispatch of subject/name tags.

### Integration test

Add a single integration test that runs all four stages against curated
records that previously lost data in the target tags and asserts the
reconstructed MARC contains the expected values:

```python
def test_curated_forward_only_fields_round_trip():
    """Records that lost forward-only fields before p-067 must
    reconstruct with those fields after the fix."""
    # Per-tag expected values from the curated corpus.
    expected = {
        # (bib_id, [(tag, subfields)])
        "…": [("023", [("a", "1234567890")]), …],
    }
    for bib_id, fields in expected.items():
        run = new_run()
        run_pipeline(bib_id, run)
        for tag, subs in fields:
            recon = load_marc(run["marc"] / f"{bib_id}.marcxml")
            # assert each field present with expected subfields
```

The curated records are sourced from the `curated/` directory and the
expected values are extracted from the source MARCXML.

### Gate checks

After each phase:

```sh
make lint && make test
uv run bffi-pipeline regenerate-mapping-tables --check
uv run bffi-pipeline regenerate-marc-mapping --check
uv run bffi-pipeline regenerate-marc-to-bibframe-mapping --check
uv run bffi-pipeline regenerate-field-coverage-corpus --check
uv run bffi-pipeline diagnose-marc-coverage --corpus tests/data/sample-marcxml/curated
```

All five must pass before committing.

## Documentation updates

### Required (ship with each phase)

1. **Emit-rule `source=` / `notes=` for each new MARC field.** These
   generate `docs/bffi_to_marc_mapping.md` automatically. Run
   `bffi-pipeline regenerate-marc-mapping` after every phase.
2. **`docs/bffi_to_marc_mapping.md` — Known limitations.** Add a row per
   new field if there is a caveat (shared predicate, first-extent-wins,
   etc.). Fields with no caveat are omitted.
3. **`docs/marc_to_bibframe_mapping.md` — Round-trip cross-check.** The
   verdict for each newly-emitted tag flips from `→ forward only` to
   `≠ asymmetric` (or `✓ round-trippable` if subfields match exactly).
   Regenerate with `bffi-pipeline regenerate-marc-to-bibframe-mapping`.
4. **`docs/bf_to_bffi_mapping.md` — no changes needed.** The bf→bffi
   mapping already covers every predicate used.

### Generated artifacts (regenerated, committed as part of the phase commit)

- `docs/bffi_to_marc_mapping.md` — new rows + notes
- `docs/marc_to_bibframe_mapping.md` — verdict flips
- `tests/data/sample-marcxml/field-coverage/README.md` — new probes listed
- New probe `.xml` files

## Trade-offs on the record

- **Why marcKey for discrimination, not a new predicate?** The BFFI
  namespace is closed to what `lkd.rdf` declares. Adding a predicate per
  MARC tag (e.g. `bffi:marc043Coverage`) would require NLF proposals for
  each. marcKey already exists, already carries tag + indicators +
  subfields, and the reverse path has a parser.
- **Why not fix the XSLT?** `CLAUDE.md` rules out modifying
  `third_party/marc2bibframe2/` ("wrap, don't fork"). The routing layer
  is the only in-scope fix point.
- **Why group by domain instead of tag order?** Each phase's tags share
  infrastructure (identifier routing, classification dispatch, marcKey
  dispatch, music literal parsing). Grouping by domain keeps each PR
  focused and reviewable.
- **Why not recover the bflc: terms (038, 385, 386)?** They use the
  `bflc:` namespace (LoC-local), which is not in `lkd.rdf`. Recovering
  them would require either a `bffi-prov:` sentinel (pipeline-internal,
  forbidden for content on the reverse path per the discipline test) or
  an NLF proposal. Documented as out-of-scope.
- **Why not recover XSLT-dropped tags?** Per the debugging guide:
  "An unfixable field documented with its reason is a finished piece of
  work; a plausible guess that emits wrong data is not." These are
  confirmed unrecoverable at the forward stage.
- **Why implement in six phases instead of one?** Each phase is
  independently testable and independently reviewable. A six-tag PR is
  easier to verify than a 23-tag PR. Phase order goes from simplest
  (Phase A: identifier/classification routing, already-proven patterns)
  to most complex (Phase F: multi-subject dispatch).

## Test plan summary

| Phase | Unit tests | Field probes | Integration |
|---|---|---|---|
| A | 9 emitters | 18 probes | Curated records for each tag |
| B | 5 emitters + 3 discrimination | 10 probes | Curated records |
| C | 3 literal parsers | 6 probes | Curated records |
| D | 4 subfield dispatchers | 8 probes | Curated records |
| E | 5 emitters + 3 discrimination | 10 probes | Curated records |
| F | 5 marcKey dispatchers | 10 probes | Curated records |
| **Total** | **31** | **62** | **6 curated records** |

## Open questions

- **051/055 axis.** These attach classification to the Item (BIBFRAME
  `bf:Item`), not the Work. The reverse converter walks
  `?m bffi:workManifested ?work` and would miss item-level predicates.
  Need to verify whether `bffi:Item` is reachable from the Manifestation
  in the current BFFI graph, or whether the emit must walk the
  `bf:hasItem` chain. If Items are not modelled separately in BFFI
  (per the 541 note in the mapping doc), then 051/055 classification may
  already be lost at the BFFI conversion step and these tags are
  effectively unrecoverable. **Confirm before implementing.**
- **340 dynamic constructor.** The XSLT builds `bf:SystemRequirement` via
  `<xsl:element name="{$vProp}">`. The dynamic constructor appendix in
  `marc_to_bibframe_mapping.md` lists the resolved properties — verify
  they survive the bibframe→bffi conversion before writing the emitter.
- **256 generic note.** `bffi:Note` is the catch-all for 500. Discriminating
  marcKey `256` from marcKey `500` is possible (different prefixes), but
  any record whose XSLT emits `bf:Note` without marcKey will be falsely
  classified. The XSLT does tag 256 with marcKey (verified: the
  `t500Props` template emits marcKey for every 5XX tag it handles).
  Confirm in a sample BIBFRAME file before implementing.
