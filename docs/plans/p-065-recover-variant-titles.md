# p-065 — Recover MARC 246 variant-title fields the XSLT leaves discriminator-less

**Status: proposed.** Three families of variant-title blocks arrive in the
BFFI graph without any discriminator the reverse extractor can fire on:
`bf:ParallelTitle` (ind2=1), `bf:VariantTitle` with no vartitletype (ind2=3
or blank). All three vanish at hop 3 (BFFI conversion). The fix lives in the
BFFI routing layer, which is where the discriminator should have been
attached.

## Problem

The marc2bibframe2 XSLT (`ConvSpec-200-247not240-Titles.xsl`) emits
`bf:VariantTitle` / `bf:ParallelTitle` for every MARC 246, carrying only
`bf:mainTitle`. Its `<xsl:choose>` attaches a vartitletype
`<rdf:type>` for ind2 ∈ {0, 2, 4, 5, 6, 7, 8} but produces **no vartitletype
and no `bflc:marcKey`** for ind2 ∈ {1, 3, blank}. The `t246Props` template
that generates title bnode properties never emits marcKey — unlike the
`240` and `1XX/7XX/8XX` templates, which do.

The reverse extractor (`_variant_title_marc_tag` in `runner.py`) has two
discrimination paths:

| Path | Matches |
|---|---|
| `rdf:type <…/vartitletype/<tail>>` | ind2 ∈ {0, 2, 4, 5, 6, 7, 8} — seven tails, all map to `"246"` |
| `bffi:marcKey "246 …"` | never fires — XSLT never emits marcKey for 246 |

For ind2 ∈ {1, 3, blank} both paths return `None`, the title block is
skipped, and the data is silently lost.

Verified on the curated corpus (26 records): 6 records carry 8 total 246
fields. Four survive (all ind2=0, via vartitletype/por); four are lost.

### What the BFFI graph actually carries

Every `bf:VariantTitle` / `bf:ParallelTitle` bnode in the full BIBFRAME
output was inspected — zero marcKeys, on the bnode or on its parent
`bf:title`. The BFFI routing doc (`bf_to_bffi_mapping.md`) describes
marcKey as the canonical discriminator with an example
`"24631$aOsallisuus …"`; this never materialises in the XSLT output. The
routing `route_title_variants` then strips the `bf:ParallelTitle` /
`bf:VariantTitle` rdf:type and replaces it with `bffi:Title`, so by the
time the reverse converter runs the original class is unrecoverable too.

| Source ind2 | XSLT element | vartitletype | marcKey | Survives? |
|---|---|---|---|---|
| 0 | `bf:VariantTitle` | **YES** (`por`) | NO | YES — vartitletype |
| 1 | `bf:ParallelTitle` | NO | NO | **NO** |
| 2 | `bf:VariantTitle` | **YES** (`dis`) | NO | YES — vartitletype |
| 3 | `bf:VariantTitle` | NO | NO | **NO** |
| 4–8 | `bf:VariantTitle` | **YES** | NO | YES — vartitletype |
| blank | `bf:VariantTitle` | NO | NO | **NO** |

The ind2 dispatch back to MARC is already deferred per the emit-rule
`notes=`: every recovered 246 currently emits `ind2=" "` regardless of
source. Recovering the *value* is the priority; recovering the original
ind2 is a Phase C enhancement.

## What measurement showed before writing any code

- marc2bibframe2's `bflc:marcKey` carrier exists on names (1XX/7XX/8XX),
  Uniform Titles (240), and Agents — never on 246 title bnodes. Verified
  across all 25 BIBFRAME files in the curated run.
- `route_title_variants` removes the original rdf:type unconditionally
  (`graph.remove((subject, RDF.type, bf_class))`). Any fix must attach the
  discriminator *before* that removal.
- The reverse path already has a marcKey-parsing pipeline: `_parse_marc_key`
  exists, traced-series and 730/740 use it, `_VariantTitleEmit` is the
  current dataclass and would need `ind1`/`ind2` fields for Phase C.
- `_VARIANT_TITLE_INDICATORS` currently hardcodes `("1", " ")` for 246;
  the render loop reads from this table. Per-instance indicators require
  threading them through `_VariantTitleEmit`.

## Design

### Phase A — attach marcKey in `route_title_variants`

Extend `route_title_variants` (in `routings.py`) to read each bnode's
pre-routing rdf:type, then attach a `bffi:marcKey` literal before
overwriting the type with `bffi:Title`:

```
bf:ParallelTitle       → bffi:marcKey "2461 $a<text>"    (ind2=1)
bf:VariantTitle + vartitletype/*  → skip (already recoverable)
bf:VariantTitle no vartitletype  → bffi:marcKey "2463 $a<text>"  (ind2=3)
```

The `"2463 "` assignment for the ambiguous VariantTitle-without-type case
is a best-effort mapping. The XSLT cannot distinguish ind2=3 from ind2=blank
at the RDF level — both produce `bf:VariantTitle` with no inner discriminator.
The corpus shows ind2=3 for every affected record; `ind2=3` is the
conservative default. This is flagged as an open limitation in the mapping
doc.

The marcKey value encodes tag + ind1 + ind2 + the `bf:mainTitle` text in
standard BFLC marcKey form (`<tag><ind1><ind2> $a<text>`), so
`_parse_marc_key` on the reverse side recovers the full field verbatim.

**Correctness checks before applying:**

- Don't overwrite an existing marcKey. Some title bnodes (rare — the
  `rename_graph` pass preserves them from upstream templates) may already
  carry one; guard with `if not graph.objects(subject, BFFI.marcKey)`.
- Don't emit marcKey on bnodes the routing doesn't touch. The routing
  iterates `graph.subjects(RDF.type, bf_class)` for the four TITLE_VARIANT
  classes — only those bnodes are in scope.
- The `bf:ParallelTitle` case must NOT also carry a vartitletype — the XSLT
  never emits one for ind2=1, but the guard is defensive.

### Phase B — extend the reverse dispatcher

Two small changes in `runner.py`:

1. **`_variant_title_marc_tag`** — already matches `"246"` from marcKey
   prefix (it is in `_MARCKEY_VARIANT_TITLE_TAGS`). No code change needed.
2. **`_extract_variant_titles`** — currently returns
   `_VariantTitleEmit(tag=tag, text=str(main))` with no indicators. Extend
   to parse marcKey when present and thread `ind1`/`ind2` through:

```python
@dataclass(frozen=True)
class _VariantTitleEmit:
    tag: str
    text: str
    ind1: str = "1"      # added to the dataclass
    ind2: str = " "      # added to the dataclass
```

When marcKey is present on the title block, `_parse_marc_key` provides the
verbatim indicators. When absent (vartitletype path), fall back to
`_VARIANT_TITLE_INDICATORS[tag]` — the current hardcoded `("1", " ")`.

The render loop at line 4240 already reads per-tag indicators; with the
dataclass extended it uses `variant.ind1` / `variant.ind2` when present
instead of the table default.

### Phase C — ind2 reconstruction (optional, deferred)

Currently every recovered 246 emits `ind2=" "` regardless of source. With
marcKey now carrying ind2, the reverse path can reconstruct it verbatim.
This is a Phase C enhancement only — the Phase A/B fix recovers the
**value** even if ind2 is still collapsed.

Open question: when marcKey is absent (should never happen after Phase A,
but be defensive), the fall-back ind2 from `_VARIANT_TITLE_INDICATORS`
keeps working.

## Measurement

### Before / after on the curated corpus

| Metric | Before | After (predicted) |
|---|---|---|
| Records with 246 | 6 | 6 |
| 246 fields recovered | 4 | 8 |
| 246 fields lost | 4 | 0 |
| Fabricated 246 | 0 | 0 |
| Other tags affected | — | 0 (marcKey is scoped to title bnodes) |

### Regression guard

- Every tag that currently round-trips must still round-trip. The
  vartitletype path is untouched by Phase A — marcKey is only added to
  bnodes that lacked it.
- The field-coverage corpus has probes `246.xml` (minimal, ind2=0) and
  `1246.xml` (maximal, all ind2 values). Run both before and after.
- The curated records 1109760 (ind2=3 ×2), 2394080 (ind2=1), and 2616222
  (ind2=1 + blank) are the specific regressions to watch.

### Fabrication check

The new marcKey values are deterministic SHA-1-free strings of the form
`"246X $a<text>"`. No other family emits marcKey starting with `"246"`, so
there is no ownership collision. The dispatch table
`_MARCKEY_VARIANT_TITLE_TAGS` already includes `"246"`, so the new keys
fall into an existing slot rather than inventing a new one.

## Trade-offs on the record

- **Why marcKey and not a fresh predicate?** The BFFI namespace is closed
  to what `lkd.rdf` declares. Adding a new `bffi:` predicate for
  "variant-title ind2" would require an NLF proposal. marcKey already
  exists (`owl:equivalentProperty bflc:marcKey`), already carries tag +
  indicators + subfields, and the reverse path has a parser for it.
- **Why not fix the XSLT?** `CLAUDE.md` rules out modifying
  `third_party/marc2bibframe2/` ("wrap, don't fork"). Attaching the
  discriminator in the routing layer is the only in-scope route.
- **Why ind2=3 for the ambiguous case?** The XSLT's `title246` template
  has no `<xsl:when>` for ind2=3 or ind2=blank — both fall through to the
  same `xsl:otherwise`. From the BFFI graph alone they are indistinguishable.
  The curated corpus has ind2=3 for every affected record. ind2=blank is
  technically possible but would require a different default. Documented
  as an open limitation.
- **Why not recover ind2 now?** The current emit hardcodes `ind2=" "` and
  the emit-rule `notes=` already discloses the deferral. Phase C is a
  thin layer on top of Phase A and can ship in the same PR.
- **Why not use `bf:ParallelTitle` as a structural discriminator?**
  `route_title_variants` strips it. Keeping it around as a side-channel
  would mean two discriminators (type + marcKey) for one field; marcKey
  alone is simpler and is the pattern the mapping doc already prescribes.

## Tests

Every phase lands with its own test. The repo's convention is tests
against fixtures, not network — and the round-trip is verified by content,
not count (see `docs/roundtrip-debugging.md`).

### Phase A tests — `tests/unit/stages/bibframe_to_bffi/test_routings.py`

One new test, covering the three discriminable cases:

```python
def test_route_title_variants_attaches_marcKey_for_untyped_blocks():
    """bf:ParallelTitle and bf:VariantTitle without vartitletype must
    receive a bffi:marcKey before the routing strips their original
    rdf:type. Already-typed blocks (with vartitletype) are left alone –
    the vartitletype path is the existing discriminator."""
    g = Graph()

    # Case 1: bf:ParallelTitle (ind2=1) — no vartitletype.
    pt = URIRef("http://example.org/pt")
    g.add((pt, RDF.type, BF.ParallelTitle))
    g.add((pt, BF.mainTitle, Literal("Parallel form")))

    # Case 2: bf:VariantTitle + vartitletype/por (ind2=0) — already has
    # a discriminator; marcKey must NOT be attached.
    vt_por = URIRef("http://example.org/vtpor")
    g.add((vt_por, RDF.type, BF.VariantTitle))
    g.add((vt_por, RDF.type, URIRef(VARTITLETYPE_PREFIX + "por")))
    g.add((vt_por, BF.mainTitle, Literal("por form")))

    # Case 3: bf:VariantTitle, no vartitletype (ind2=3).
    vt_raw = URIRef("http://example.org/vtraw")
    g.add((vt_raw, RDF.type, BF.VariantTitle))
    g.add((vt_raw, BF.mainTitle, Literal("raw form")))

    rewritten = route_title_variants(g)
    assert rewritten == 4  # all four TITLE_VARIANT_CLASSES collapsed

    # All three become bffi:Title.
    assert (pt, RDF.type, BFFI.Title) in g
    assert (pt, RDF.type, BF.ParallelTitle) not in g
    assert (vt_por, RDF.type, BFFI.Title) in g
    assert (vt_raw, RDF.type, BFFI.Title) in g

    # marcKey is attached only for the untyped blocks.
    assert g.objects(pt, BFFI.marcKey)  # ParallelTitle got one
    assert list(g.objects(pt, BFFI.marcKey))[0] == Literal("2461 $aParallel form")
    assert list(g.objects(vt_por, BFFI.marcKey)) == []  # untouched
    assert list(g.objects(vt_raw, BFFI.marcKey))[0] == Literal("2463 $araw form")
```

Regression guard: the existing
`test_route_title_variants_collapses_subclasses_to_bffi_title` must still
pass — it only checks rdf:type rewriting, not marcKey.

### Phase B tests — `tests/unit/stages/bffi_to_marc/test_runner.py`

Three new tests, covering the reverse path:

```python
def test_variant_title_marc_tag_recovers_tag_from_marcKey():
    """marcKey prefix '246' must dispatch to '246' — same as the
    vartitletype path, but via the second discrimination branch."""
    g = Graph()
    title = BNode()
    g.add((title, RDF.type, BFFI.Title))
    g.add((title, BFFI.marcKey, Literal("2461 $aParallel form")))
    g.add((title, BFFI.mainTitle, Literal("Parallel form")))
    assert _variant_title_marc_tag(g, title) == "246"


def test_extract_variant_titles_reads_indicators_from_marcKey():
    """When marcKey is present the emit must carry its verbatim ind1/ind2;
    when absent (vartitletype path) the emit falls back to the
    _VARIANT_TITLE_INDICATORS table."""
    g = Graph()
    man = URIRef("http://example.org/man")

    # marcKey-driven — ind2=1.
    mk_title = BNode()
    g.add((man, BFFI.title, mk_title))
    g.add((mk_title, RDF.type, BFFI.Title))
    g.add((mk_title, BFFI.marcKey, Literal("2461 $aFrom marcKey")))
    g.add((mk_title, BFFI.mainTitle, Literal("From marcKey")))

    # vartitletype-driven — ind2=0.
    vt_title = BNode()
    g.add((man, BFFI.title, vt_title))
    g.add((vt_title, RDF.type, BFFI.Title))
    g.add((vt_title, RDF.type, URIRef(VARTITLETYPE_PREFIX + "por")))
    g.add((vt_title, BFFI.mainTitle, Literal("From vartitletype")))

    emits = _extract_variant_titles(g, man)
    by_text = {e.text: e for e in emits}
    assert by_text["From marcKey"].tag == "246"
    assert by_text["From marcKey"].ind1 == "1"
    assert by_text["From marcKey"].ind2 == "1"
    assert by_text["From vartitletype"].tag == "246"
    assert by_text["From vartitletype"].ind1 == "1"
    assert by_text["From vartitletype"].ind2 == "0"  # from vartitletype/por
```

The `_VariantTitleEmit` dataclass change (`ind1` / `ind2` fields with
defaults) must not break the existing render: every current call site
that constructs `_VariantTitleEmit(tag=tag, text=text)` still works
because the new fields have defaults.

### Field-coverage probes — new fixtures

The generator (`field_coverage.py`) picks one indicator pair per tag from
the XSLT's literal indicator tests; for 246 those are translate()-expressions
that don't resolve to clean literals, so the generator falls back to the
emit registry's `("1", " ")`. The current probes (`246.xml`, `1246.xml`)
therefore exercise only ind2=blank — the worst case, since blank has no
vartitletype and no marcKey.

The generator's `regenerate_field_coverage_corpus` **deletes** any file
in the corpus directory that is not in its `wanted` set. Hand-crafted
probes cannot live there — they would be wiped on the next regeneration.

**Approach: extend `FieldCase` with an `indicator_variant` slot.** Add a
new optional field to the dataclass and two new cases to `build_cases()`:

```python
@dataclass(frozen=True)
class FieldCase:
    tag: str
    variant: str                       # "minimal" | "maximal"
    indicators: tuple[str, str]        # already present
    indicator_variant: str | None = None  # new: "0", "1", "3", etc.
    subfields: list[tuple[str, str]]
    is_controlfield: bool
```

When `indicator_variant` is set, `record_id()` returns a variant-aware
id (`"246-ind2-1"` / `"1246-ind2-1"`) and the rendered record uses the
override indicators. `build_cases()` produces two extra cases per tag
where the XSLT's ind2-driven dispatch matters:

| File | 001 | ind1 | ind2 | Purpose |
|---|---|---|---|---|
| `246-ind2-1.xml` | `246-ind2-1` | 1 | 1 | ParallelTitle — XSLT emits `bf:ParallelTitle` |
| `246-ind2-3.xml` | `246-ind2-3` | 1 | 3 | VariantTitle without vartitletype |

These survive regeneration because they are produced by the generator
itself. `roundtrip-eval` pairs by 001 content so the non-bare-digits
naming is fine.

These probes are the regression guard: after the fix they must round-trip
to a 246 datafield with ind2=1 / ind2=3 respectively. Before the fix they
fail (no discriminator in the XSLT output).

### Integration test — curated record round-trips

Add a single integration test that runs all four stages against the three
known-loss curated records (1109760, 2394080, 2616222) and asserts the
reconstructed MARC contains the expected 246 values with the expected
ind2:

```python
def test_curated_246_round_trips_value_and_ind2():
    """Records whose source 246 had ind2 in {1, 3, blank} were lost
    before p-065. After the fix they must reconstruct with the
    original ind2 (except ind2=blank which is the default)."""
    records = {
        "1109760": [("TM : Tekniikan maailma", "3"),
                    ("TM Vantaa", "3")],
        "2394080": [("Kungsleden map and guide", "1")],
        "2616222": [("Masterpieces for piano", "1"),
                    ("Grundlegendes Klavierrepertoire…", " ")],
    }
    for bib_id, expected in records.items():
        run = new_run()
        run_pipeline(bib_id, run)
        for text, expected_ind2 in expected:
            recon = load_marc(run["marc"] / f"{bib_id}.marcxml")
            fields = [df for df in recon.iterfind(f".//{M}datafield[@tag='246']")]
            # …assert at least one field matches (text, ind2).
```

This pins the specific regressions instead of leaving them to the
field-coverage probe. The field-coverage probe exercises shapes; this
exercise pins values.

## Documentation updates

### Required (ship with the fix)

1. **Emit-rule `notes=` for 246.** Change from:

   ```
   "ind1=1 (Note, added entry) per HELMET corpus convention; ind2 blank. "
   "The specific vartitletype tail maps to different ind2 values "
   "in MARC source but the per-tail ind2 dispatch is deferred."
   ```

   to something like:

   ```
   "ind1=1 (Note, added entry) per HELMET corpus convention. ind2 is "
   "recovered from bffi:marcKey when present (Phase A attaches it for "
   "ParallelTitle and for VariantTitle without vartitletype); falls "
   "back to blank when the vartitletype path supplies the tag."
   ```

   Then run `bffi-pipeline regenerate-marc-mapping` — this auto-generates
   `docs/bffi_to_marc_mapping.md` from the emit-rule metadata. The 246
   row in that doc will change to reflect the new source pattern and the
   updated notes.

2. **`docs/bffi_to_marc_mapping.md` — Known limitations for 246.** Add a
   row to the limitations table:

   | Field | Limitation |
   |---|---|
   | `246` ind2 ∈ {3, blank} | ind2=3 and ind2=blank produce identical RDF in the XSLT (both `bf:VariantTitle` without vartitletype); the routing defaults to ind2=3. The original ind2 is unrecoverable from the BFFI graph alone. |

3. **`docs/bf_to_bffi_mapping.md` — title-variant class section.** The
   existing example `"24631$aOsallisuus …"` is now real for ind2=1
   records too. No correction needed, but add a note:

   > marcKey is now attached by `route_title_variants` for `bf:ParallelTitle`
   > (ind2=1) and `bf:VariantTitle` without vartitletype (ind2=3). Already-
   > typed blocks (ind2 ∈ {0, 2, 4–8}) keep their vartitletype discriminator
   > and do not receive a marcKey.

4. **`docs/roundtrip-debugging.md` — failure-pattern catalogue.** Add a
   new entry to the catalogue of failure patterns:

   > **8. Title bnode without discriminator.** marc2bibframe2 emits
   > `bf:ParallelTitle` / `bf:VariantTitle` without vartitletype and without
   > marcKey for some ind2 values. The BFFI routing (p-065) now attaches
   > marcKey for these; if a future XSLT change drops the attachment, the
   > symptom is identical to pattern 1 (wrong FRBR axis): data hangs on the
   > Work but the extractor can't see it.

### Generated artifacts (regenerated, not committed by hand)

These are regenerated as part of the CI gate
(`bffi-pipeline regenerate-… --check`) and committed automatically:

- `docs/bffi_to_marc_mapping.md` — 246 row, new notes
- `tests/data/sample-marcxml/field-coverage/README.md` — two new probes
- `tests/data/sample-marcxml/field-coverage/246-ind2-1.xml` — generator-produced
- `tests/data/sample-marcxml/field-coverage/246-ind2-3.xml` — generator-produced

### Pre-commit gate

The pre-commit hook runs `regenerate-marc-mapping --check` and
`regenerate-field-coverage-corpus --check`. The commit is blocked until
both pass. The emit-rule change + new probes must land in the same
commit as the code change — otherwise `--check` fails on the next commit.

## Out of scope

- **ind2=blank vs ind2=3 disambiguation.** Requires information the XSLT
  drops. Out of scope for this plan.
- **MARC 247 (former title) ind2 dispatch.** 247 uses the same XSLT
  template (`t247Props`) with the same marcKey gap. Diagnose and fix in a
  follow-on plan if the data shows affected records.
- **MARC 210 / 222 / 242 / 243.** These tags' XSLT templates DO emit
  marcKey (verified: they use the `marcKey` mode in `utils.xsl`). Not
  affected.
- **The BFFI routing doc example.** `bf_to_bffi_mapping.md` shows
  `"24631$aOsallisuus …"` as the canonical example. After Phase A this
  becomes real for ind2=1 records too — no correction needed, the example
  was aspirational not wrong.
