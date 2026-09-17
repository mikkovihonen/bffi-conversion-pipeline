# Implementation Plan - Move `bffi:NotatedMusic` to Expression in `route_work_split`

In record `b1042698x.xml`, MARC leader position 06 indicates notated music (`c`). `marc2bibframe2` outputs `bf:NotatedMusic` on the `bf:Work` entity because BIBFRAME 2.0 does not have an Expression entity. The clean rename pass converts `bf:NotatedMusic` to `bffi:NotatedMusic`. However, in the BFFI ontology (`vocab/lkd.rdf`), `bffi:NotatedMusic` is explicitly declared as:
```xml
<rdfs:subClassOf rdf:resource="http://urn.fi/URN:NBN:fi:schema:bffi:Expression"/>
```
and `bffi:Work` is disjoint with `bffi:Expression`.

Currently, `route_work_split` mints the `bffi:Expression` node and migrates Expression-domain properties, but leaves `rdf:type bffi:NotatedMusic` on the `bffi:Work` node. The Expression node receives only generic `rdf:type bffi:Expression`.

## User Review Required

> [!NOTE]
> All subclasses of `bffi:Expression` declared in `vocab/lkd.rdf` (such as `bffi:NotatedMusic`, `bffi:Text`, `bffi:NotatedMovement`, etc.) attached to `bffi:BibframeWork` will be migrated from the Work node to the Expression node during `route_work_split`. This aligns with the gold reference format in `tests/data/bffi/BFFI-redeemedMonograph.bffi.ttl`.

## Proposed Changes

### BIBFRAME → BFFI Conversion

#### [MODIFY] [routings.py](file:///Users/mikkovihonen/Workspace/bffi-conversion-pipeline/src/bffi_pipeline/stages/bibframe_to_bffi/routings.py)

1. Define `BFFI_EXPRESSION_CLASSES` as a frozen set containing all classes declared as `rdfs:subClassOf bffi:Expression` in `vocab/lkd.rdf`:
   - `bffi:AggregatingExpression`
   - `bffi:Arrangement`
   - `bffi:CartographyExpression`
   - `bffi:CollectionExpression`
   - `bffi:Dataset`
   - `bffi:MixedMaterial`
   - `bffi:MonographExpression`
   - `bffi:MovingImageExpression`
   - `bffi:Multimedia`
   - `bffi:MusicAudioExpression`
   - `bffi:NonMusicAudioExpression`
   - `bffi:NotatedMovement`
   - `bffi:NotatedMusic`
   - `bffi:Object`
   - `bffi:SerialExpression`
   - `bffi:SeriesExpression`
   - `bffi:StillImage`
   - `bffi:Text`
2. Update `_EXPRESSION_AXIS_SIGNALS` to be `{BFFI.Expression} | BFFI_EXPRESSION_CLASSES`.
3. In `route_work_split(graph: Graph)`:
   - For each `o` in `graph.objects(subject, RDF.type)` where `o in BFFI_EXPRESSION_CLASSES`:
     - Remove `(subject, RDF.type, o)`
     - Add `(expr_node, RDF.type, o)`

---

### BFFI → MARC Leader Recovery (Enhancement)

#### [MODIFY] [extractors.py](file:///Users/mikkovihonen/Workspace/bffi-conversion-pipeline/src/bffi_pipeline/stages/bffi_to_marc/extractors.py)

1. In `_leader_record_type_byte(graph, manifestation)`:
   - Also check `_expressions_for(graph, manifestation, work)` for `BFFI.content` if `work` does not carry it, ensuring leader pos 06 ('c' for notated music) is correctly reconstructed when `content` resides on Expression.

---

### Tests

#### [MODIFY] [test_routings.py](file:///Users/mikkovihonen/Workspace/bffi-conversion-pipeline/tests/unit/stages/bibframe_to_bffi/test_routings.py)

1. Add unit test `test_route_work_split_migrates_expression_classes`:
   - Setup graph with `(work, RDF.type, BFFI.BibframeWork)` and `(work, RDF.type, BFFI.NotatedMusic)`.
   - Run `route_work_split(g)`.
   - Assert `(work, RDF.type, BFFI.Work)` in graph.
   - Assert `(work, RDF.type, BFFI.NotatedMusic)` NOT in graph.
   - Assert `(expr, RDF.type, BFFI.Expression)` in graph.
   - Assert `(expr, RDF.type, BFFI.NotatedMusic)` in graph.
2. Add integration test converting `tests/data/sample-golden/b1042698x.xml`:
   - Verify `bffi:NotatedMusic` is on Expression and not on Work.

## Verification Plan

### Automated Tests
- Run `uv run pytest tests/unit/stages/bibframe_to_bffi/test_routings.py`
- Run `uv run pytest tests/` (full test suite across all 645+ tests)
- Run `uv run ruff check src tests` and `uv run mypy --strict src`

### Manual Verification
- Run conversion on `tests/data/sample-golden/b1042698x.xml` and verify the emitted Turtle output for both Work and Expression entities.
