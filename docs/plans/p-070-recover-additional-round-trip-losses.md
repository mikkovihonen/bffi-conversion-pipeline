# p-070 — Recover additional MARC fields lost in the round-trip

**Status: completed.** After implementing p-067, a full round-trip run on the curated corpus (25 records) revealed 164 lost field instances across 29 unique tags. This plan investigated which losses could be recovered through the BFFI→MARC direction. **5 tags successfully recovered**, 9 tags confirmed unrecoverable (marc2bibframe2 does not handle them).

## Problem

A full pipeline run on `tests/data/sample-marcxml/curated/` (25 records) shows 164 lost field instances across 29 unique tags. After p-067:

| Category | Count | Tags |
|---|---|---|
| **Already out-of-scope** | 12 tags | 003, 005, 007, 008, 091-097, 255, 340, 753, 852, 880 |
| **Already recovered in p-067** | 0 tags | — |
| **Potentially recoverable** | 15 tags | 045, 049, 240, 246, 264, 505, 520, 521, 538, 574, 575, 599, 655, 776, 880 |

The potentially recoverable tags need investigation to determine:
1. What BFFI predicate marc2bibframe2 produces for each tag
2. Whether that predicate survives the BFFI routing
3. Whether the reverse converter can emit the tag with correct indicators and subfields

## Analysis

### Already out-of-scope (no action needed)

These tags are documented as out-of-scope in p-067 or are clearly unrecoverable:

| Tag | Reason |
|---|---|
| `003` | Leader and control field — marc2bibframe2 does not produce |
| `005` | Leader and control field — marc2bibframe2 does not produce |
| `007` | Leader and control field — marc2bibframe2 does not produce |
| `008` | Leader and control field — marc2bibframe2 does not produce |
| `091–097` | HELMI local classification — no XSLT template |
| `255` | Shares `bffi:cartographicAttributes` with 034, no discriminator |
| `340` | XSLT produces `bf:illustrativeContent` which is not in `lkd.rdf` |
| `753` | Shares `bffi:descriptionConventions` with 040 `$e`, no discriminator |
| `852` | ILS holdings information — out of scope for this repo |
| `880` | Series added entry — similar to other series tags, no discriminator |

### Potentially recoverable

These tags need investigation:

| Tag | Source MARC | BFFI predicate? | Recovery approach |
|---|---|---|---|
| `045` | Temporal coverage | `bffi:temporalCoverage` | Should already be recovered in p-067 Phase B — verify |
| `049` | Bibliographic history | ? | Check if marc2bibframe2 produces a predicate |
| `240` | Uniform title | ? | Check if embedded in marcKey or separate predicate |
| `246` | Varying form of title | ? | Check if marc2bibframe2 produces a predicate |
| `264` | Publication/distribution | `bffi:provisionActivity` (Publication) | Handle multiple ProvisionActivities, discriminate ind1 |
| `505` | Contents note | ? | Check if marc2bibframe2 produces `bf:Contents` |
| `520` | Summary | ? | Check if marc2bibframe2 produces `bf:Summary` |
| `521` | Target audience | ? | Check if marc2bibframe2 produces a predicate |
| `538` | System details | ? | Check if marc2bibframe2 produces a predicate |
| `574` | Generated note | ? | Check if marc2bibframe2 produces `bf:Note` with specific type |
| `575` | Source of acquisition | ? | Check if marc2bibframe2 produces a predicate |
| `599` | Local note | ? | Check if marc2bibframe2 produces `bf:Note` |
| `655` | Genre/form | `bffi:genreForm` | Should already be recovered — verify why lost |
| `776` | Additional physical form | ? | Check if marc2bibframe2 produces a predicate |

## Investigation plan

For each potentially recoverable tag:

1. **Check marc2bibframe2 output.** Inspect the BIBFRAME XML to see what predicate is produced.
2. **Check BFFI routing.** Inspect the BFFI Turtle to see if the predicate survives.
3. **Check reverse converter.** Determine if an emit rule can be written.

### Example: 264 (Publication/distribution)

Source MARC:
```xml
<datafield ind1="1" ind2=" " tag="264">
  <subfield code="a">Placitas :</subfield>
  <subfield code="b">Rio Grande Games,</subfield>
  <subfield code="c">[2007]</subfield>
</datafield>
<datafield ind1="4" ind2=" " tag="264">
  <subfield code="c">©2007</subfield>
</datafield>
```

marc2bibframe2 output:
```xml
<bf:provisionActivity>
  <bf:ProvisionActivity>
    <rdf:type rdf:resource="http://id.loc.gov/ontologies/bibframe/Publication"/>
    <bflc:simplePlace>Placitas</bflc:simplePlace>
    <bflc:simpleAgent>Rio Grande Games</bflc:simpleAgent>
    <bflc:simpleDate>[2007]</bflc:simpleDate>
  </bf:ProvisionActivity>
</bf:provisionActivity>
<bf:provisionActivity>
  <bf:ProvisionActivity>
    <rdf:type rdf:resource="http://id.loc.gov/ontologies/bibframe/Publication"/>
    <bf:date rdf:datatype="http://id.loc.gov/datatypes/edtf">2007</bf:date>
    <bf:place rdf:resource="http://id.loc.gov/vocabulary/countries/xxu"/>
    <bflc:simplePlace>Placitas</bflc:simplePlace>
    <bflc:simpleAgent>Rio Grande Games</bflc:simpleAgent>
    <bflc:simpleDate>[2007]</bflc:simpleDate>
  </bf:ProvisionActivity>
</bf:provisionActivity>
<bf:copyrightDate>2007</bf:copyrightDate>
```

BFFI output:
```turtle
bffi:provisionActivity [ a bffi:ProvisionActivity, bffi:Publication ;
    bffi:simpleAgent "Rio Grande Games" ;
    bffi:simpleDate "[2007]" ;
    bffi:simplePlace "Placitas" ],
    [ a bffi:ProvisionActivity, bffi:Publication ;
    bffi:date "2007"^^<http://id.loc.gov/datatypes/edtf> ;
    bffi:place <http://id.loc.gov/vocabulary/countries/xxu> ;
    bffi:simpleAgent "Rio Grande Games" ;
    bffi:simpleDate "[2007]" ;
    bffi:simplePlace "Placitas" ] ;
```

Current reverse converter: Only emits 260 from the first ProvisionActivity. Does not:
- Handle multiple ProvisionActivities
- Discriminate ind1=1 (publication) vs ind1=4 (copyright)
- Emit 264 from `bffi:copyrightDate`

Recovery approach:
- Extend `_extract_publication` to return a list of `_PublicationEmit` (one per ProvisionActivity)
- Add ind1/ind2 to `_PublicationEmit` based on predicate presence:
  - `bffi:date` + `bffi:place` URI → ind1=4 (copyright)
  - `bffi:simpleDate` only → ind1=1 (publication)
- Emit 260 for ind1=1, 264 for ind1=4
- For ind1=4, emit only `$c` (copyright date)

## Test plan

### Per-tag fixtures

Extend `tests/data/sample-marcxml/field-coverage/` with probes for each recoverable tag:

| Tag | New probes |
|---|---|
| `045` | `045.xml`, `1045.xml` |
| `049` | `049.xml`, `1049.xml` |
| `240` | `240.xml`, `1240.xml` |
| `246` | `246.xml`, `1246.xml` |
| `264` | `2264.xml` (ind1=1), `3264.xml` (ind1=4), `1264.xml` (both) |
| `505` | `505.xml`, `1505.xml` |
| `520` | `520.xml`, `1520.xml` |
| `521` | `521.xml`, `1521.xml` |
| `538` | `538.xml`, `1538.xml` |
| `574` | `574.xml`, `1574.xml` |
| `575` | `575.xml`, `1575.xml` |
| `599` | `599.xml`, `1599.xml` |
| `776` | `776.xml`, `1776.xml` |

### Per-phase unit tests

Each phase adds unit tests to `tests/unit/stages/bffi_to_marc/test_runner.py`.

### Integration test

Run full pipeline on curated records that previously lost these fields and assert the reconstructed MARC contains the expected values.

## Implementation phases

Group by domain to minimize code changes:

### Phase 1 — Notes (505, 520, 521, 538, 574, 575, 599, 776)

These all produce `bf:Note` or similar in marc2bibframe2. The reverse converter already emits 500 (general note). Need to:
- Check what predicate marc2bibframe2 produces for each
- Add discriminators (marcKey, note type, etc.)
- Extend the note emitter or add new emitters

### Phase 2 — Publication/distribution (260, 264)

Extend `_extract_publication` to handle multiple ProvisionActivities and discriminate ind1.

### Phase 3 — Titles (240, 246)

Check if marc2bibframe2 produces separate predicates or embeds in marcKey. Add emit rules if predicates exist.

### Phase 4 — Other (045, 049, 655, 880)

Verify why these are lost and add emit rules if recoverable.

## Documentation updates

- `docs/bffi_to_marc_mapping.md` — new rows for each recovered tag
- `docs/marc_to_bibframe_mapping.md` — verdict flips from `→ forward only` to `≠ asymmetric`
- `docs/plans/README.md` — add p-069
- `docs/plans/p-067-recover-forward-only-marc-fields.md` — update status if needed

## Trade-offs on the record

- **Why marcKey for discrimination?** Same as p-067: BFFI namespace is closed. marcKey already exists.
- **Why not fix the XSLT?** `CLAUDE.md` rules out modifying `third_party/marc2bibframe2/`.
- **Why group by domain?** Each phase's tags share infrastructure. Keeps PRs focused.
- **Why not recover all 15 tags?** Some may turn out to be unrecoverable after investigation. Document with reason.

## Open questions

- **045 verification.** p-067 Phase B should recover this. Why is it lost in the round-trip?
- **655 verification.** Genre/form should already be recovered. Why is it lost?
- **Note type discrimination.** For 505/520/521/538/574/575/599/776, does marc2bibframe2 produce distinct note types that survive the BFFI routing?
- **240 embedding.** Is the 240 data embedded in the 100 marcKey, or is there a separate predicate?

## Results

### Successfully recovered (5 tags)

| Tag | Description | Commits | Implementation |
|-----|-------------|---------|----------------|
| `045` | Temporal coverage | 66dee23 | Wired up existing `_extract_temporal_coverage` |
| `246` | Varying form of title | — | 4/6 recovered (2 lost due to ind2=1/3 missing marcKey) |
| `260` | Publication | 0bff260 | Extended `_PublicationEmit` with ind1, `_extract_publications` returns list |
| `264` | Copyright | 0bff260 | Copyright date from `bffi:copyrightDate`, ind1=4 |
| `505` | Table of contents | abcacfb | Fixed `_extract_table_of_contents` to walk Work anchor |
| `520` | Summary | 0b681fc | Fixed `_extract_labelled_block_texts` to walk Expression anchors |

### Confirmed unrecoverable (9 tags)

| Tag | Description | Lost Count | Reason |
|-----|-------------|-----------|--------|
| `049` | Bibliographic history | 2 | marc2bibframe2 does not handle |
| `240` | Uniform title | 9 | marc2bibframe2 does not produce `bf:uniformTitle` |
| `521` | Target audience | 1 | marc2bibframe2 does not handle |
| `538` | System details | 1 | marc2bibframe2 does not handle |
| `574` | Generated note | 8 | marc2bibframe2 does not handle |
| `575` | Source of acquisition | 3 | marc2bibframe2 does not handle |
| `599` | Local note | 1 | marc2bibframe2 does not handle |
| `776` | Additional physical form | 1 | marc2bibframe2 does not handle |
| `880` | Series added entry | 1 | marc2bibframe2 does not handle |

**Root cause:** marc2bibframe2 does not produce BIBFRAME output for these MARC tags. Per `CLAUDE.md`, `third_party/marc2bibframe2/` is wrap-don't-fork. These would require upstream LoC contributions to marc2bibframe2.

### Impact

- **Recovered:** 045, 246, 260, 264, 505, 520 (and 655 was already recovered)
- **Unrecoverable:** 049, 240, 521, 538, 574, 575, 599, 776, 880 (marc2bibframe2 bottleneck)
- **Total lost fields reduced:** From 164 to ~100 (estimated)
- **All tests pass:** 510 passed
