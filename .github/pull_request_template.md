## Summary
<!-- 1-3 bullets: what changes, why -->

## Mapping impact
<!--
Required if this PR changes what the converter emits in either
direction — routings, mappings, the reverse-converter field builders,
or the vendored `vocab/lkd.rdf`.

State which MARC fields / BFFI terms change, and regenerate the
affected mapping doc so the reference stays in step with the code:

  bffi-pipeline regenerate-mapping-tables           # docs/bf_to_bffi_mapping.md
  bffi-pipeline regenerate-marc-mapping             # docs/bffi_to_marc_mapping.md
  bffi-pipeline regenerate-marc-to-bibframe-mapping # docs/marc_to_bibframe_mapping.md

If this PR doesn't change the emit, write "N/A — no mapping change".
-->

N/A

## Round-trip effect
<!--
Required if this PR touches the conversion path. Run the round-trip on
a sample and report the direction of travel — did reconstruction
fidelity improve, hold, or regress?

  RUN=$(bffi-pipeline new-run)
  bffi-pipeline marc-to-bibframe --input-dir tests/data/sample-marcxml/curated --output-dir $RUN/bibframe
  bffi-pipeline bibframe-to-bffi --input-dir $RUN/bibframe --output-dir $RUN/bffi
  bffi-pipeline bffi-to-marc     --input-dir $RUN/bffi     --output-dir $RUN/marc-out
  bffi-pipeline roundtrip-eval   --source-dir tests/data/sample-marcxml/curated \
                                 --reconstructed-dir $RUN/marc-out --html $RUN/review.html

A regression is acceptable when it trades a silently-wrong emit for an
honestly-absent one — say so explicitly if that's the trade.

If this PR doesn't touch the conversion path, write "N/A".
-->

N/A

## Checklist
- [ ] `make lint && make test` pass locally
- [ ] No new `bf:*` URI in the BFFI emit (the namespace boundary is hard-cut)
- [ ] No new `bffi:` term absent from `vocab/lkd.rdf` (the namespace is closed —
      see the BFFI namespace discipline rule in `CLAUDE.md`)
- [ ] The BFFI → MARC direction reads no `bffi-prov:` predicate for any
      content decision
- [ ] Turtle-emitting paths bind prefixes through the shared helper, not a
      private `graph.bind()` list
- [ ] Mapping docs regenerated if the emit changed (see above)
- [ ] Documentation updated (README / docstrings) if the operator-facing
      surface changed
- [ ] If this ships a plan phase, the plan file and `docs/plans/README.md`
      status column are updated
