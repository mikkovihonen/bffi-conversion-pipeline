# p-063 — Pair repeated fields by content, not position

**Status: active.** Phase A (this plan) fixes the pairing and adds the
`reordered` status.

## Problem

The review HTML reports fields as `changed` when the only difference is
order. Two distinct causes, found by measuring the curated corpus (13
paired records, 209 `changed` rows):

**1. Repeated fields are paired positionally.** `diff_fields` claims exact
matches first — order-insensitively, which is right — then **zips the
remainder by position**. When source and reconstruction hold the same
repeated tag in different orders, that pairs unrelated instances. In **20
of 30** repeated-tag groups a better pairing existed. The worst case is
`9789511335474`, whose eleven 650s the reconstruction emits in alphabetical
order while the source is in cataloguer order:

```
src: 650  7 $aperhesalaisuudet $2kauno/fin $0…/kauno/p959
rec: 650  7 $aaikatasot        $0…/kauno/p5820 $2kauno     ← paired with the above
```

The row reads as "this subject changed from perhesalaisuudet to
aikatasot". Nothing of the kind happened: one subject is absent and a
different one was added, while `henget`, `kummittelu` and `mysteerit`
appear on both sides and should have paired with each other.

**2. Subfield order alone counts as a content change.** `FieldRow.subfields`
is an ordered tuple and equality is exact, so `$a $2 $0` versus `$a $0 $2`
falls through the exact-match pass and lands in `changed` beside genuine
value differences.

## Design

Four passes per tag, replacing pass-1-then-zip:

| Pass | Match | Status |
|---|---|---|
| 1 | exact equality, any order | `identical` |
| 2 | equal ignoring subfield *order* | `reordered` |
| 3a | greatest **exact** subfield-value overlap | `changed` |
| 3b | primary values similar enough | `changed` |
| 4 | overhang | `lost` / `added` |

**Pass 3a pairs on exact overlap** — Jaccard over the field's `(code, value)`
pairs. This is what makes `henget` find `henget`: matching subjects share
their `$0` authority URI verbatim even when `$2` and subfield order differ.
Greedy in source order, highest overlap wins, earliest reconstructed index
breaks ties, so the output stays deterministic (the idempotency rule covers
the review HTML too).

**Pass 3b is the fallback for normalised values.** A field whose only
difference is stripped ISBD punctuation or a removed nonfiling article
shares *no* subfield value exactly, so 3a can't see it. 3b compares primary
values — the first `$a`, or a controlfield's text — with
`difflib.SequenceMatcher` and pairs above 0.75.

That threshold is calibrated, not guessed. Unrelated subjects in the corpus
score 0.17–0.63 (`hopeatyöt` vs `hopeasepät` is the closest false pair at
0.63); the differences the round-trip actually introduces score 0.75
(`The Hobbit` → `Hobbit`) to 0.97 (`Puškin, Aleksandr,` → `Puškin,
Aleksandr`).

**Only the primary value is compared, deliberately.** Running the character
similarity over `$0` authority URIs would score two unrelated subjects at
~0.9, because `…/kauno/p959` and `…/kauno/p5820` differ in a few digits —
which is exactly how a fuzzy matcher ends up confidently pairing
`perhesalaisuudet` with `aikatasot` again, by a different route.

**Rows with nothing in common stay unpaired** — unless the tag was never
repeated. Two 650s sharing no value are one lost subject and one added one,
not a subject that changed; but for a single-instance field, `changed` beats
making the reader re-associate a lost row with an added one by eye. The
not-repeated test uses the counts *before* any pass consumes rows: "one left
over after 3a" is not the same claim as "one to begin with".

**`reordered` is its own status, not folded into `identical`.** MARC
prescribes subfield order, so a reordering is a real fidelity difference and
the reverse converter's `$0`-before-`$2` habit is worth seeing. It is not a
*content* difference, which is what `changed` should mean.

## Measured effect

Curated corpus, 13 paired records:

| Status | Before | After |
|---|---|---|
| identical | 199 | 199 |
| reordered | — | 1 |
| changed | 209 | 208 |
| lost | 153 | 153 |
| added | 21 | 21 |

**The distribution barely moves, and that is the point.** The numbers were
never the problem — the pairings were: **103 of the 209 `changed` rows were
paired against the wrong counterpart.** Half the rows a cataloguer reads in
the review HTML were a side-by-side comparison of two unrelated fields, and
no summary count could reveal that. After the fix each `changed` row shows a
field beside its actual counterpart.

Of the rows that reach the fuzzy tier — no exact subfield value in common —
all 23 in this corpus are genuine normalisations, and they are the reason
the tier exists:

```
src: 700 1  $aKunnas, Tarja.          rec: 700 1  $aKunnas, Tarja $4ctb
src: 740 4  $aThe Russian charka      rec: 740    $aRussian charka
src: 100 1  $aMorton, Kate, $ekirjoittaja.  rec: 100 1  $aMorton, Kate $ekirjoittaja
```

ISBD punctuation, a stripped nonfiling article, an added relator. Without
the tier these would each read as one lost field plus one added field.

## Trade-offs on the record

- **Instance order is still not compared.** The reconstruction emits
  repeated fields in a deterministic sorted order, not source order. That
  is a real difference from the source, and this plan does not report it:
  it would flag nearly every record carrying repeated fields with something
  no cataloguer can act on field by field. It belongs in the "Known
  limitations" section of `docs/bffi_to_marc_mapping.md` as a systematic
  property of the reverse converter, which is where it now is.
- **Greedy, not optimal, assignment.** A Hungarian-style optimal matching
  would score marginally better on pathological groups. Greedy is
  deterministic, dependency-free, O(n²) on groups that are almost always
  under a dozen rows, and the first brute-force attempt at optimal matching
  on an 11-row group did not finish inside two minutes.
- **Jaccard over `(code, value)` pairs ignores subfield order** by
  construction, so pass 3 cannot re-introduce the pass-2 confusion.

## Out of scope

- `tag-changed` and `marckey-bypass`, still deferred (see `diff.py`).
- Reporting *which* subfields differ inside a `changed` row. The HTML shows
  both sides in full and the cataloguer reads across; a per-subfield diff
  is a presentation change with its own design questions.
