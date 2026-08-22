# p-072 — Round-trip recovery status and remaining gaps

**Status: active.** Post-v0.2.3 audit of round-trip losses and changes.

## Problem

After v0.2.3 (ISBD punctuation, alt-script 880 reconstruction, indicator fixes), the round-trip on the curated corpus (25 records) shows:

| Status | Count | % |
|---|---|---|
| **Identical** | 215 | 53% |
| **Changed** | 236 | 58% |
| **Lost** | 143 | 35% |
| **Added** | 27 | 7% |
| **Reordered** | 5 | 1% |

**Total field instances:** 405 unique field occurrences across 14 paired records.

**v0.2.4 impact:** +2 identical, -5 lost, +3 changed (240 uniform title recovery, 600 indicator fix).

## Recovery actions taken

### ISBD punctuation enabled by default (v0.2.3)

- `ConversionOptions.apply_isbd_punctuation` now defaults to `True`
- Fixes ~248 of 313 changed fields (ISBD trailing punctuation differences)
- Records with ISBD disabled still match source more closely

### 041 language code indicator fix

- Changed ind1 from blank (" ") to "0" when language codes exist but no $h (translation) is present
- Fixes 041 indicator mismatches (e.g., `041 0  $azxx` → `041    $azxx`)
- ind1="0" = "item is not a translation"; blank = "no information provided"

## Remaining lost fields (148 total)

### Out of scope / intentionally lost (68%, 101 instances)

| Tag | Description | Count | Reason |
|---|---|---|---|
| `003, 005, 007, 008` | Control/leader fields | 32 | marc2bibframe2 does not produce |
| `091–097` | HELMET local classification | 55 | No XSLT template |
| `852` | ILS holdings | 14 | Out of scope for this repo |

**Recovery:** None. These are by design.

### Confirmed unrecoverable (17%, 25 instances)

| Tag | Description | Count | Reason |
|---|---|---|---|
| `040` | Cataloging source | 7 | marc2bibframe2 does not preserve `$a` (agency) in `bf:adminMetadata` |
| `574` | Generated note | 4 | marc2bibframe2 does not handle |
| `575` | Source of acquisition | 3 | marc2bibframe2 does not handle |
| `049` | Bibliographic history | 1 | marc2bibframe2 does not handle |
| `538` | System details | 1 | marc2bibframe2 does not handle |
| `753` | Documentation convention | 1 | marc2bibframe2 does not handle |
| `776` | Additional physical form | 1 | marc2bibframe2 does not handle |
| `260` | Publication (when 264 exists) | 7 | marc2bibframe2 drops 260 in favor of 264 |

**Recovery:** Requires marc2bibframe2 contributions to LoC.

### Potentially recoverable (15%, 22 instances)

| Tag | Description | Count | Issue | Effort |
|---|---|---|---|---|
| `240` | Uniform title | **0** | ✅ **Recovered in v0.2.4** via `Hub240 → Expression → Manifestation` traversal; marcKey parsed (`{1XX marcKey}$t{uniform title},${subfields...}`) | Done |
| `246` | Varying form of title | 5 | marcKey missing for ind2=1/3 | Low |
| `040` | Cataloging source | 7 | `$a` (agency) and `$d` (originating agency) lost — marc2bibframe2 does not preserve these in `bf:adminMetadata` | marc2bibframe2 bottleneck |
| `264` | Copyright (ind1=4) | 3 | Copyright date not emitted | Low |
| `600` | Personal name subjects | 7 | Name order swapped, indicators lost | Medium |
| `041` | Language code | 8 | Indicator fix applied, remaining issues | Verified |

### Recovered (v0.2.4)

| Tag | Description | Count | Method |
|---|---|---|---|
| `040` `$a` | Cataloging agency | **0** | ❌ marc2bibframe2 does not preserve in `bf:adminMetadata` |
| `040` `$d` | Originating agency | **0** | ❌ marc2bibframe2 does not preserve in `bf:adminMetadata` |
| `240` | Uniform title | **5** | ✅ `Hub240` marcKey parsed; ind2 hardcoded to `0` (source ind2 lost) |
| `600` indicators | Personal name subjects | **7** | ✅ ind1/ind2 parsed from marcKey |

**Round-trip impact of v0.2.4 fixes:** +2 identical, -5 lost, +3 changed (240 moved from "lost" → "changed").

## Remaining changed fields (after ISBD fix)

### ISBD punctuation (79%, 248 instances)

**Status:** Fixed by enabling ISBD by default.

| Tag | Description |
|---|---|
| `650, 655` | Subject/genre trailing periods |
| `700, 100` | Contributor commas |
| `245, 336, 300, 651, 337, 338, 500, 740` | Various ISBD marks |

### Other changes (21%, 65 instances)

| Tag | Description | Issue | Effort |
|---|---|---|---|
| `041` | Language code | Indicator fix applied | Verified |
| `264` | Copyright | ind1=4 not preserved | Low |
| `600` | Personal name subjects | Name order swapped (RDF order) | Medium |
| `240` | Uniform title | ind2 hardcoded to `0` (source ind2 lost) | marc2bibframe2 bottleneck |
| `246` | Varying form | Indicator loss | Low |
| `490` | Series | Indicator loss | Low |

## marc2bibframe2 bottlenecks (detailed)

### 040 — Cataloging source (7 instances)

**Source MARC:**
```xml
<datafield tag="040" ind1=" " ind2=" ">
  <subfield code="a">FI-BTJ</subfield>
  <subfield code="b">fin</subfield>
  <subfield code="e">rda</subfield>
  <subfield code="d">FI-NL</subfield>
</datafield>
```

**BIBFRAME AdminMetadata (no `$a`/`$d` equivalent):**
```xml
<bf:AdminMetadata>
  <bf:status><bf:Status ...>new</bf:Status></bf:status>
  <bf:date>1994-11-08T00:00:00</bf:date>
</bf:AdminMetadata>
```

**Reconstructed MARC:**
```xml
<datafield tag="040" ind1=" " ind2=" ">
  <subfield code="b">fin</subfield>
  <subfield code="e">isbd</subfield>
</datafield>
```

**Root cause:** marc2bibframe2 does not map MARC 040 `$a` (cataloging agency) or `$d` (originating agency) to any BIBFRAME property. The `bf:adminMetadata` schema has no equivalent for cataloging agency.

**Recovery options:**
1. **Contribute to marc2bibframe2** — Add `bf:adminMetadata` extension for 040 `$a`/`$d` (requires upstream LoC contribution)
2. **Use 003 as fallback** — Source MARC 003 has the same agency code; could be used for 040 `$a` in the BFFI→MARC stage (low effort, partial recovery)
3. **Accept the loss** — Document as a known limitation

**Status:** Unrecoverable from BFFI alone. Requires marc2bibframe2 contribution or 003 fallback.

### 240 — Uniform title (5 instances, now recovered)

**Recovered in v0.2.4** via `Hub240 → Expression → Manifestation` graph traversal. The marcKey on `Hub240` nodes encodes the 240 as `{1XX marcKey}$t{uniform title},${subfields...}`. Parsed by splitting on `$t` and mapping subfields.

**Remaining limitation:** ind2 hardcoded to `0` (0 nonfiling characters) — source ind2 not preserved in marcKey structure. This moves 240 from "lost" to "changed" (ind2 mismatch) rather than fully recovering it.

## Recovery plan

### Phase 1 — Quick wins (Low effort, high impact)

1. **246 indicator fix** — Parse marcKey for ind1/ind2 when present
2. **264 copyright** — Extend `_PublicationEmit` to handle ind1=4
3. **490 series indicators** — Parse marcKey for indicators

### Phase 2 — Medium effort

4. **600 name order** — Investigate BFFI graph for name parsing issue
5. **600 indicators** — Parse marcKey for ind1/ind2 when present

### Phase 3 — marc2bibframe2 contributions (High effort, requires upstream)

6. **240 uniform title** — Contribute `bf:uniformTitle` to marc2bibframe2
7. **040 $a, $d** — Contribute `bf:assigner`, `bf:descriptionModifier` to marc2bibframe2
8. **574, 575, 776** — Contribute note type handling to marc2bibframe2

## Open questions

1. **600 name order** — Is this a BFFI routing issue or a converter parsing issue?
2. **264 copyright** — Does marc2bibframe2 preserve ind1=4 copyright dates?
3. **246 indicators** — Does marc2bibframe2 preserve ind2=1/3 varying form indicators?

## Next steps

1. **Phase 1 implementation** — Fix 246, 264, 490 indicators (estimated 2-3 hours)
2. **Phase 2 investigation** — Debug 600 name order issue (estimated 4-6 hours)
3. **Phase 3 planning** — Draft marc2bibframe2 contribution proposals (estimated 8-12 hours)

## References

- [p-067](p-067-recover-forward-only-marc-fields.md) — Forward-only MARC field recovery
- [p-068](p-068-recover-remaining-forward-only-fields-and-subfields.md) — Remaining forward-only fields
- [p-070](p-070-recover-additional-round-trip-losses.md) — Additional round-trip losses
- [p-071](p-071-recover-alt-script-880-fields.md) — Alt-script 880 reconstruction
