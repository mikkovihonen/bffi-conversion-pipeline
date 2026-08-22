# p-072 — Round-trip recovery status and remaining gaps

**Status: active.** Post-v0.2.3 audit of round-trip losses and changes.

## Problem

After v0.2.3 (ISBD punctuation, alt-script 880 reconstruction, indicator fixes), the round-trip on the curated corpus (25 records) shows:

| Status | Count | % |
|---|---|---|
| **Identical** | 135 | 33% |
| **Changed** | 313 | 77% |
| **Lost** | 148 | 37% |
| **Added** | 27 | 7% |
| **Reordered** | 3 | 1% |

**Total field instances:** 405 unique field occurrences across 14 paired records.

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
| `240` | Uniform title | 5 | marc2bibframe2 does not produce `bf:uniformTitle` |
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
| `246` | Varying form of title | 5 | marcKey missing for ind2=1/3 | Low |
| `040` | Cataloging source | 7 | $a, $d subfields lost | marc2bibframe2 bottleneck |
| `264` | Copyright (ind1=4) | 3 | Copyright date not emitted | Low |
| `600` | Personal name subjects | 7 | Name order swapped, indicators lost | Medium |
| `041` | Language code | 8 | Indicator fix applied, remaining issues | Verified |

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
| `040` | Cataloging source | $a, $d subfields missing | marc2bibframe2 bottleneck |
| `041` | Language code | Indicator fix applied | Verified |
| `264` | Copyright | ind1=4 not preserved | Low |
| `600` | Personal name subjects | Name order swapped | Medium |
| `246` | Varying form | Indicator loss | Low |
| `490` | Series | Indicator loss | Low |

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
