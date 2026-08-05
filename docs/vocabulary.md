# BFFI Vocabulary

The closed set of `bffi:` classes and properties is declared in
[`vocab/lkd.rdf`](../vocab/lkd.rdf) — the full BFFI 1.0.0 ontology, vendored
because the canonical schema URL (`https://schema.finto.fi/bffi/`) returns
HTTP 403 outside the Finto network.

This is the only source of truth for what terms may be emitted under the
`bffi:` namespace. When the ontology has no term for something, the converter
drops it visibly rather than inventing a local term.

The mapping docs (BIBFRAME → BFFI, BFFI → MARC) document every routing decision
against this vocabulary. See the "Gap clusters" subsections in
[`bf_to_bffi_mapping.md`](bf_to_bffi_mapping.md) for the ontology-shortfall
caveats.
