# p-069 — Recover MARC 336/337/338 `$3` (materials specified) subfield

**Status: implemented.** 336/337/338 `$3` (materials specified) recovered
by extending `_RdaEntry` with optional `applies_to` field, updating
`_rda_entries` to read `bffi:appliesTo`/`rdfs:label`, adding `$3` to
`_RDA_SUBFIELDS`, updating emit rule `source=` declarations, and updating
`_append_rda_datafields` to include `$3` in the output.

This plan adds `$3` to the 336/337/338 emit rules by extending the
`_RdaEntry` dataclass and `_rda_entries` helper to read
`bffi:appliesTo`/`rdfs:label` and pass it through to the emit.

## Problem

MARC 336/337/338 have four subfields:

| Code | Name | BFFI carrier | Currently emitted? |
|---|---|---|---|
| `$a` | Term in cataloguing language | `rdfs:label` of URI | ✅ Yes |
| `$b` | RDA 3-letter code | URI last segment | ✅ Yes |
| `$2` | Scheme | `bffi:source`/`bffi:source` | ✅ Yes |
| `$3` | Materials specified | `bffi:appliesTo`/`rdfs:label` | ❌ No |

The `_RDA_SUBFIELDS` constant declares only `$a`, `$b`, `$2`. The
`_rda_entries` helper only reads the URI and its `rdfs:label`. The
`bffi:appliesTo` literal on the URI node is never read.

## Design

### 1. Extend `_RdaEntry` dataclass

Add optional `applies_to` field:

```python
@dataclass(frozen=True)
class _RdaEntry:
    """One MARC 336/337/338 emit: ``$a`` label + ``$b`` code + ``$2``
    scheme + optional ``$3`` materials specified."""

    label: str | None
    code: str
    scheme: str
    applies_to: str | None = None  # NEW: from bffi:appliesTo/rdfs:label
```

### 2. Update `_rda_entries` helper

Read `bffi:appliesTo` from the URI node and extract its `rdfs:label`:

```python
def _rda_entries(graph: Graph, objects: Iterable[Node], *, scheme: str) -> tuple[_RdaEntry, ...]:
    """Build the sorted tuple of ``_RdaEntry`` values for one of the
    three RDA predicates. Skips non-URI objects."""
    entries: list[_RdaEntry] = []
    seen: set[URIRef] = set()
    for obj in objects:
        if not isinstance(obj, URIRef) or obj in seen:
            continue
        seen.add(obj)
        label = next(graph.objects(obj, RDFS.label), None)
        # NEW: Read bffi:appliesTo/rdfs:label for $3
        applies_to = None
        for applies in graph.objects(obj, BFFI.appliesTo):
            if isinstance(applies, BNode):
                applies_label = next(graph.objects(applies, RDFS.label), None)
                if isinstance(applies_label, Literal):
                    applies_to = str(applies_label)
                    break
        entries.append(
            _RdaEntry(
                label=str(label) if isinstance(label, Literal) else None,
                code=local_name(obj),
                scheme=scheme,
                applies_to=applies_to,
            )
        )
    return tuple(sorted(entries, key=lambda e: (e.code, e.label or "")))
```

### 3. Update `_RDA_SUBFIELDS`

Add `$3` to the subfields list:

```python
_RDA_SUBFIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("a", "term in the cataloguing language (rdfs:label of the URI)"),
    ("b", "RDA 3-letter code (URI last segment)"),
    ("2", "scheme name (rdacontent / rdamedia / rdacarrier)"),
    ("3", "materials specified (from bffi:appliesTo/rdfs:label)"),
)
```

### 4. Update emit rule `source=` declarations

Update the `source=` in the `@marc_emit` decorators for 336/337/338 to
include `$3`:

```python
source=(
    "?m bffi:workManifested ?work . "
    "?expression bffi:expressionOf ?work (or ?expression "
    "bffi:manifestationOfExpression ?m) . "
    "?expression bffi:content <http://id.loc.gov/vocabulary/contentTypes/{code}> . "
    "?content bffi:appliesTo ?applies . ?applies rdfs:label ?applies_to . "
    "$a = ?content's rdfs:label; $b = {code}; $2 = 'rdacontent'; "
    "$3 = ?applies_to."
),
```

### 5. Update emit function to pass `applies_to` through

The `_rda_descriptors` emit function needs to include `applies_to` in
the emitted subfields. Since the current implementation uses
`_RDA_SUBFIELDS` which is a static tuple, the emit function needs to
be updated to read the `applies_to` field from `_RdaEntry` and include
it in the output.

## Implementation notes

### Shared infrastructure reuse

Every step reuses existing patterns:

- **`_RdaEntry` dataclass**: Already has `label`, `code`, `scheme`. Adding
  `applies_to` is a one-line change with a default value of `None` for
  backward compatibility.

- **`_rda_entries` helper**: Already walks URI objects and reads
  `rdfs:label`. Adding `bffi:appliesTo`/`rdfs:label` reading is a few
  lines.

- **`_RDA_SUBFIELDS` constant**: Already declared. Adding `("3", ...)` is
  a one-line change.

- **Emit rule `source=`**: Already declares the SPARQL pattern. Adding
  `?content bffi:appliesTo ?applies . ?applies rdfs:label ?applies_to`
  extends the pattern.

- **`bffi:appliesTo`**: Already in `lkd.rdf` with `owl:equivalentProperty`
  of `bflc:appliesTo`. The BFFI routing already converts it.

### No new BFFI terms required

The `bffi:appliesTo` predicate is already in the published BFFI
vocabulary (`lkd.rdf`). No term proposal to NLF needed.

### No discriminator needed

`$3` is always on 336/337/338. There's no collision with other tags.

## Measurement

### Before / after (predicted)

| Metric | Before | After |
|---|---|---|
| 336 `$3` coverage | 0% | ~90% (records with materials specified) |
| 337 `$3` coverage | 0% | ~90% |
| 338 `$3` coverage | 0% | ~90% |
| Round-trip diff rows | - | Fewer `added` rows for records with `$3` |

### Regression guard

- Every tag that currently round-trips must still round-trip after this
  change. The change is additive — it only adds `$3` when present.
- Field-coverage probes already have `$3` in the maximal probe (1336.xml,
  1337.xml, 1338.xml). The generator will pick it up automatically.
- Curated records with `$3` in source 336/337/338 must reconstruct with
  `$3` after the fix.

## Tests

### Per-tag unit tests

Each of 336/337/338 gets a unit test verifying `$3` is emitted when
`bffi:appliesTo` is present:

```python
def test_336_emits_3_when_applies_to_present():
    """MARC 336 $3 (materials specified) comes from bffi:appliesTo
    bnode's rdfs:label."""
    g = _build_minimal_bffi_graph(
        manifestation_uri="http://example.org/b1#Instance",
        bib_id="b1",
        title="A book",
    )
    manifestation, work = _work_with_manifestation(g)
    # Build an Expression with content type + appliesTo
    expr = BNode()
    g.add((expr, RDF.type, BFFI.Expression))
    g.add((expr, BFFI.expressionOf, work))
    g.add((expr, BFFI.manifestationOfExpression, manifestation))
    content_uri = URIRef("http://id.loc.gov/vocabulary/contentTypes/txt")
    g.add((expr, BFFI.content, content_uri))
    applies_node = BNode()
    g.add((content_uri, BFFI.appliesTo, applies_node))
    g.add((applies_node, RDFS.label, Literal("Z3363")))
    # Run the emit
    marcxml = emit_marcxml(g, manifestation=manifestation)
    root = etree.fromstring(marcxml)
    df336 = root.find(f"{{{MARC21_NS}}}datafield[@tag='336']")
    assert df336 is not None
    sf_3 = df336.find(f"{{{MARC21_NS}}}subfield[@code='3']")
    assert sf_3 is not None
    assert sf_3.text == "Z3363"
```

### Field-coverage probes

The field-coverage generator already has `$3` in the maximal probes
(1336.xml, 1337.xml, 1338.xml). No changes needed to the generator.

### Integration test

```python
def test_336_337_338_round_trip_with_materials_specified():
    """Records with $3 in 336/337/338 must reconstruct with $3."""
    expected = {
        # (bib_id, [(tag, [(code, value), ...])])
        "1336": [("336", [("a", "..."), ("b", "txt"), ("2", "rdacontent"), ("3", "Z3363")])],
        "1337": [("337", [("a", "..."), ("b", "n"), ("2", "rdamedia"), ("3", "...")])],
        "1338": [("338", [("a", "..."), ("b", "nc"), ("2", "rdacarrier"), ("3", "...")])],
    }
    for bib_id, fields in expected.items():
        run = new_run()
        run_pipeline(bib_id, run)
        for tag, subs in fields:
            recon = load_marc(run["marc"] / f"{bib_id}.marcxml")
            # assert each subfield present with expected value
```

## Documentation updates

### Required (ship with the change)

1. **Emit-rule `source=` / `notes=` for 336/337/338.** Add `$3` to the
   `source=` declaration. These generate `docs/bffi_to_marc_mapping.md`
   automatically.

2. **`docs/bffi_to_marc_mapping.md` — Known limitations.** Add a row per
   tag if there is a caveat (e.g., `$3` only emitted when present in
   source).

3. **`docs/marc_to_bibframe_mapping.md` — Round-trip cross-check.** The
   verdict for `$3` flips from `→ forward only` to `≠ asymmetric` (or
   `✓ round-trippable` if subfields match exactly).

4. **`docs/plans/p-067-recover-forward-only-marc-fields.md`** — update
   the "Measurement" section to reflect that 336/337/338 `$3` is now
   recoverable.

### Generated artifacts (regenerated, committed as part of the change)

- `docs/bffi_to_marc_mapping.md` — new rows + notes
- `docs/marc_to_bibframe_mapping.md` — verdict flips
- `tests/data/sample-marcxml/field-coverage/README.md` — already lists
  `$3` for 336/337/338; no changes needed

## Trade-offs on the record

- **Why fix `$3` before other subfields?** It's the lowest-hanging fruit:
  the BFFI carrier (`bffi:appliesTo`) is already in `lkd.rdf`, the XSLT
  already produces it, the routing already preserves it. The only
  missing piece is reading it in the reverse converter. One dataclass
  field, one helper function extension, one constant update.

- **Why not fix `$3` for other tags?** Other tags with `$3` (e.g., 300,
  336-338 are the only ones with `$3` on RDA descriptors) would require
  separate investigation. Focus on the proven case first.

- **Why not fix the label round-trip issue?** The emit rule's note
  already documents that source `$a` is the cataloguer's display label
  (often Finnish) while BFFI carries the URI's `rdfs:label` (English).
  This is a known limitation, not a gap. `$3` is a separate, concrete
  gap.

- **Why not propose a new BFFI term?** `bffi:appliesTo` is already in
  `lkd.rdf`. No term proposal needed.

## Test plan summary

| Test type | Count |
|---|---|
| Unit tests (336/337/338 `$3`) | 3 |
| Field-coverage probes | 3 (already present) |
| Integration tests | 1 |
| **Total** | **7** |

## References

- p-067: `docs/plans/p-067-recover-forward-only-marc-fields.md`
- p-068: `docs/plans/p-068-recover-remaining-forward-only-fields-and-subfields.md`
- BFFI vocabulary: `vocab/lkd.rdf` (search for `appliesTo`)
- Round-trip debugging: `docs/roundtrip-debugging.md`
