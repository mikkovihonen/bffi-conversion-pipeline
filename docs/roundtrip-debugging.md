# Debugging the round-trip

A field is missing, wrong, or invented in the reconstructed MARCXML. This is
how to find out why, in the order that actually converges.

Written from the fixes in `git log` — MARC 022, 035, 040, 041, 336, 370, 511,
518, 655, 740, 787 — so every pattern and pitfall below has bitten this
repository at least once. Aimed at whoever picks this up next, human or agent.

## The one rule

**Compare values, not counts.**

Counting fields is not verifying them. Two real examples from this repo:

- A MARC 040 attempt emitted **185** fields against **190** in source with
  zero fabrication. It looked finished. Comparing `$a` values showed **5 of
  190** correct — it was attributing records to the wrong cataloguing agency.
- A 655 analysis reported "59 tags fully lost" by checking tag presence.
  **30 of those** kept their values under a *different* tag.

Every measurement below compares content. When you are tempted to report a
count, compare the values instead.

## Step 0 — reproduce

Everything writes into a canonical run directory. Keep one per attempt so you
can diff hops.

```sh
CORPUS=tests/data/sample-marcxml/curated          # or your own input dir
RUN=$(uv run bffi-pipeline new-run)

uv run bffi-pipeline marc-to-bibframe --input-dir "$CORPUS"      --output-dir "$RUN/bibframe"
uv run bffi-pipeline bibframe-to-bffi --input-dir "$RUN/bibframe" --output-dir "$RUN/bffi"
uv run bffi-pipeline bffi-to-marc     --input-dir "$RUN/bffi"    --output-dir "$RUN/marc"
uv run bffi-pipeline roundtrip-eval   --source-dir "$CORPUS" \
    --reconstructed-dir "$RUN/marc" --html "$RUN/review.html"
```

**Never send stderr to `/dev/null`.** A whole chain once "succeeded" against
empty directories because the first stage aborted and the diagnosis went with
the discarded stream. If you must quieten a run, redirect to a file.

Likewise avoid `cmd-a >/dev/null && cmd-b` chains while debugging: a silent
non-zero exit on the first command skips the rest and you end up reading a
stale artifact, concluding the wrong thing.

## Step 1 — get the field-level picture

Source counts against reconstructed counts, per tag, both directions:

```sh
uv run python - <<'PY'
from lxml import etree
import pathlib, collections, sys
M = "{http://www.loc.gov/MARC21/slim}"
SRC, REC = sys.argv[1] if len(sys.argv) > 1 else "tests/data/sample-marcxml/curated", "REPLACE/marc"

def counts(d, pat):
    c = collections.Counter()
    for p in pathlib.Path(d).glob(pat):
        try: root = etree.parse(str(p)).getroot()
        except Exception: continue          # unreadable record; count separately
        for el in list(root.iter(M+"datafield")) + list(root.iter(M+"controlfield")):
            c[el.get("tag")] += 1
    return c

s, r = counts(SRC, "*.xml"), counts(REC, "*.marcxml")
for tag in sorted(set(s) | set(r)):
    a, b = s.get(tag, 0), r.get(tag, 0)
    if a != b:
        print(f"  {tag}: source {a:5d} -> recon {b:5d}")
print("fabricated:", sum(v for t, v in r.items() if not s.get(t)))
PY
```

**Include tags with zero source count.** Ranking by `source - recon > 0`
cannot show fabrication, because an invented tag has no source occurrences.
That is how 232 invented fields (MARC 787 ×223, 774 ×9) went unnoticed through
several rounds of triage.

## Step 2 — classify the symptom

The classification decides which hop to look at and how urgent it is.

| Symptom | Meaning | Where to look |
|---|---|---|
| **identical** | round-trips | — |
| **changed** | tag right, value differs | usually upstream normalisation (nonfiling articles, punctuation, EDTF dates) |
| **retagged** | values present under another tag | reverse dispatch picked a different tag; often correct-by-model |
| **lost** | values absent entirely | traversal, gating, or upstream drop |
| **fabricated** | emitted with no source | ownership collision — **fix first** |

Fabrication outranks loss. A missing field is visible; an invented one reads
as authoritative and quietly corrupts the diff it is supposed to falsify.

To tell **lost** from **retagged**, search the reconstruction for the source
value rather than the tag:

```sh
grep -c "Kalevala" "$RUN/marc/1354066.marcxml"      # value survived?
grep -o 'tag="740"' "$RUN/marc/1354066.marcxml"     # under the right tag?
```

## Step 3 — localise the hop

Follow one distinctive value through all three artifacts. Pick something
greppable from the source field.

```sh
V="Äänitys konsertissa"      # a value from the field under test
STEM=b0000025

grep -c "$V" "$CORPUS/$STEM.xml"                  # 1. source
grep -c "$V" "$RUN/bibframe/$STEM.bibframe.xml"   # 2. after the LoC XSLT
grep -c "$V" "$RUN/bffi/$STEM.bffi.ttl"           # 3. after BFFI conversion
grep -c "$V" "$RUN/marc/$STEM.marcxml"            # 4. after reconstruction
```

Where the count first drops to zero tells you the responsible hop:

| Drops at | Cause | Can we fix it? |
|---|---|---|
| 2 (BIBFRAME) | the vendored XSLT never reads the field | **No.** `third_party/marc2bibframe2` is wrap-don't-fork |
| 3 (BFFI) | a routing dropped or renamed it | yes — `stages/bibframe_to_bffi/routings.py` |
| 4 (MARC) | reverse converter didn't emit | yes — `stages/bffi_to_marc/runner.py` |

**Confirm hop-2 losses in the stylesheet before concluding anything.** Two
findings came only from reading it:

- MARC 388: no stylesheet under `third_party/marc2bibframe2/xsl/` mentions the
  tag at all — permanently unrecoverable here.
- MARC 040 `$a`: the `$a` → `bf:assigner` block is **commented out** in
  `ConvSpec-010-048.xsl` (v3.1.0), as is `$d`. Weeks of reverse-side work
  could not have recovered them.

```sh
grep -rn "'388'" third_party/marc2bibframe2/xsl/ || echo "no template: unrecoverable"
```

## Step 4 — inspect the graph

When the loss is at hop 4, the question is always *where does the data hang,
and does the extractor walk there?*

```sh
uv run python - <<'PY'
from rdflib import Graph, RDF, RDFS
from bffi_pipeline.provenance.vocab import BFFI
g = Graph(); g.parse("RUN/bffi/STEM.bffi.ttl", format="turtle")

man  = next(iter(g.subjects(RDF.type, BFFI.Manifestation)), None)
work = next(iter(g.objects(man, BFFI.workManifested)), None)

for s, _, o in g.triples((None, BFFI.language, None)):        # the predicate under test
    who = ("MANIFESTATION" if s == man else "WORK" if s == work
           else [str(t).rsplit(":", 1)[-1] for t in g.objects(s, RDF.type)])
    print("holder:", who, "->", o)
    print("  incoming:", [str(p).rsplit(":", 1)[-1] for _, p in g.subject_predicates(s)])
PY
```

`incoming` matters as much as the holder: a node with no incoming edge is not
necessarily unreachable — see the inverse-traversal pattern below.

## Known failure patterns

Ordered by how often they have been the answer.

### 1. Wrong FRBR axis

marc2bibframe2 attaches data to whichever axis the field *describes*, not to
the Manifestation. An extractor that walks only `?m` loses everything else.

| Field | Data hangs on | Was lost |
|---|---|---|
| 041 language | Work | all 265 |
| 511 participants note | Work | all 25 |
| 022 ISSN (serial) | Work | all |
| 336 content type | Expression | all 301 |

Fix shape — walk the axes, dedupe by node:

```python
work = _find_work_for_manifestation(graph, manifestation)
owners: list[URIRef | BNode] = [manifestation]
if work is not None:
    owners.append(work)
seen: set[URIRef | BNode] = set()
for node in (n for o in owners for n in graph.objects(o, PREDICATE)):
    if node in seen:
        continue
    seen.add(node)
```

**Read the emit rule's own `notes=` first.** MARC 041's note already said
"every source 041 sub-code becomes `bf:language` on the Work" while the code
read the Manifestation. The contradiction sat in the same rule.

### 2. Inverse traversal

Expressions point *outward* — `?expression bffi:expressionOf ?work` — with no
inverse from the Work. Walking outgoing predicates from the Manifestation never
reaches them.

Measuring *incoming* edges made these look orphaned (66% "unreachable"), which
wrongly suggested a forward-converter fix. Inverting the predicate reached 296
of 301. Use `_expressions_for(graph, manifestation, work)`.

### 3. Arbitrary single-value selection

`next(graph.objects(x, PREDICATE), None)` takes **one** value in rdflib's
arbitrary order. When several exist, emit becomes order-dependent — the same
graph can produce different MARC between runs.

```python
# wrong: silently drops the field when the non-scheme source sorts first
source = next(graph.objects(ident, BFFI.source), None)

# right: scan until something dispatches
for source in graph.objects(ident, BFFI.source):
    scheme = _IDENTIFIER_SCHEME_TO_MARC.get(source)
    if scheme is not None:
        return scheme
```

This is a correctness bug even where it currently happens to work. Grep for
`next(graph.objects(` when a field is intermittently absent.

### 4. Ownership collisions → fabrication

Several families can match the same resource. marc2bibframe2 marks *every*
MARC 730/740 analytic as `relationship/relatedwork`, so keying MARC 787 on the
relationship alone emitted a duplicate beside each correct 730: **223 invented
fields**, 84 on one box-set record.

Before emitting on a relationship URI or a type alone, ask **which other family
already owns this node**. Check `bffi:marcKey`'s tag prefix
(`_MARCKEY_TAGS_CLAIMED_ELSEWHERE`) and co-types such as `bffi:Uncontrolled`
(the MARC 653 family).

### 5. marcKey present for one tag, absent for its sibling

730 carries a `bffi:marcKey`; **740 does not**. A family keyed only on marcKey
loses the sibling silently. 740's tag lives in the URI fragment
(`#Work740-42`) and its title in `bffi:title / bffi:mainTitle`.

Prefer marcKey when present (it preserves subfields BFFI has no predicate
for), and add a structural fallback for nodes without one.

**Do not reconstruct a field by replaying a verbatim marcKey string when a
structural path exists.** MARC 040 has a `"040  $aFI-BTJ$bfin$erda"` note in
the graph; replaying it yields a perfect diff and hides the structural gap the
round-trip exists to expose. This is the marcKey bypass the repo audits
against.

### 6. Derived companions

The XSLT emits derived nodes alongside transcribed ones. Emitting both doubles
the field.

| Field | Transcribed | Derived companion |
|---|---|---|
| 518 | Capture with cataloguer text | Capture with `bffi:note` = `"capture"`, EDTF dates (`2023-05-XX`) |
| 336 | from source 336 | content type derived from leader/008 |
| 040 | `$e` from source | `isbd` / `aacr` derived from leader/18 |

Discriminate, then verify the discriminator's exact counts before relying on
it. For 518: 5 labelled + 20 structured = the 25 source fields, 10 derived
skipped.

### 7. Gate on the property that only the source can produce

When deciding *whether* to emit, pick the property that exists only when the
source field did.

| Gate | Result |
|---|---|
| `bffi:descriptionConventions` | 190 true, **46 fabricated** — also derived from leader/18 |
| `bffi:descriptionLanguage` | 185 true, **0 fabricated** — comes only from `040 $b` |

Prefer zero fabrication over completeness on any field asserting provenance.
Losing 5 of 190 beats inventing 46 cataloguing-source claims.

## Fix, verify, regenerate

1. **Measure the same numbers before and after**, on real records, not just
   the probe corpus. Report source → reconstructed, and check fabrication.
2. **Add a regression test** naming the pattern and the observed damage, so
   the next reader knows what it cost. Assert the *value*, not just presence.
3. **Run the gate.** `make lint && make test`, then all four artifact guards:

```sh
uv run bffi-pipeline regenerate-mapping-tables --check
uv run bffi-pipeline regenerate-marc-mapping --check
uv run bffi-pipeline regenerate-marc-to-bibframe-mapping --check
uv run bffi-pipeline regenerate-field-coverage-corpus --check
```

4. **Update the emit rule's `source=` / `notes=`.** They generate
   `docs/bffi_to_marc_mapping.md`, which goes to NLF. A stale note is worse
   than none: 518's claimed "only `$a` is reconstructed in reverse" after that
   stopped being true. Escape `|` as `\|` — a raw pipe breaks the table.
5. **Regenerate and commit the artifacts.** Adding an emit rule changes the
   field-coverage corpus and the registry's expected tag set; the pre-commit
   hook blocks until both are refreshed.

## The instruments

| Tool | Use it for |
|---|---|
| `tests/data/sample-marcxml/field-coverage/` | one minimal + one maximal probe per handled tag, generated. `<tag>.xml` is minimal, `1<tag>.xml` maximal |
| `roundtrip-eval` + review HTML | per-record diff classification on real records |
| `docs/marc_to_bibframe_mapping.md` | what the XSLT reads per tag, plus the round-trip cross-check verdict |
| `docs/bffi_to_marc_mapping.md` | every tag the reverse converter emits, and its known limitations |
| `diagnose-marc-coverage` | how much of a corpus the reverse converter covers |

**Compare the minimal and maximal probes.** A tag that round-trips minimally
but not maximally is broken by a subfield *combination*, not by missing
support: `020 $a` alone round-trips, but add a spurious `$z`/`$q`/`$0` and the
ISBN vanishes. That distinction rescued eight identifier fields from being
filed as pipeline gaps.

Probes are synthetic and deliberately maximal, so they exercise shapes real
cataloguing may never produce. **Confirm every finding on real records before
acting on it**, and never conclude from a single record — MARC 040 looked
correct on `b0000028` and was wrong on 185 of 190.

## Pitfalls, collected

Each of these has cost real time here.

- Counting fields instead of comparing values.
- Checking tag presence, which conflates *lost* with *retagged*.
- Ranking by `source - recon`, which cannot surface fabrication.
- Concluding from one record that happened to work.
- `2>/dev/null`, which hid an aborted stage behind four "successful" ones.
- `&&` chains with suppressed output, which skipped a regeneration step and
  left a stale doc being read as evidence.
- Measuring incoming edges and calling nodes orphaned.
- Trusting a rule's `notes=` as description rather than checking the code —
  and equally, not reading them, since 041's note held the answer all along.
- Assuming a probe failure is a pipeline failure. Some are artifacts of
  subfield combinations that cannot occur in real data.

## When the answer is "cannot be fixed here"

Say so explicitly, with evidence, and record it in the emit rule's `notes=`
so it reaches the generated doc. Three real cases:

- **MARC 388** — no stylesheet template. Needs an upstream contribution to
  LoC, or a post-XSLT enrichment layer that does not exist.
- **MARC 040 `$a` / `$d`** — commented out in the vendored stylesheet.
- **HELMET-local 09X** (091–097, 223 fields) — no template for the local
  classification block.

Modifying `third_party/marc2bibframe2` is ruled out by `CLAUDE.md`: wrap,
don't fork. An unfixable field documented with its reason is a finished piece
of work; a plausible guess that emits wrong data is not.
