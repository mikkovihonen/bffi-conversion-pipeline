"""Pillar 4 orchestrator: BFFI canonical Turtle -> reconstructed MARCXML.

Step 4 — v0 emit. Reads a BFFI graph and emits a MARCXML record
per Manifestation. Cardinal rule: the reverse converter MUST NOT consult
``bffi-prov:`` (pipeline-internal provenance) for bibliographic content.
Pipeline-internal data is fair for UI / pairing machinery, never for emit
content. The closed-namespace discipline test
(``tests/unit/stages/bffi_to_marc/test_bffi_prov_discipline.py``) parses
this module's source and fails the build if a ``bffi-prov:`` reference
creeps in.

v0 scope: emit the minimum-viable MARCXML that lets the round-trip diff
harness (step 5) compare against the source. Concretely:

  - leader   placeholder (24 chars; positions populated in a later step)
  - 001      Source bib ID, read from a ``bffi:identifiedBy [ a bffi:Local ;
             rdf:value ?bib_id ]`` block. Fallback: parse from the
             Manifestation URI fragment (``http://…/<bib_id>#Instance``,
             marc2bibframe2's emit shape with our ``baseuri`` parameter).
  - 245 $a   main title, walked via ``?m bffi:title / bffi:mainTitle``.

Anything else — contributors, identifier schemes (ISBN / ISSN), subjects,
provision activity, notes, language, content type — is deliberately
deferred. Each MARC family lands in its own follow-on commit so the diff
harness gives a clean per-family verification signal.

Stage label for observability sidecar events: ``bffi2marc``.
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree
from rdflib import RDF, Graph, URIRef

from bffi_pipeline.observability.events import emit_if_active
from bffi_pipeline.provenance.vocab import BFFI
from bffi_pipeline.stages.bffi_to_marc.constants import (
    MARC21_NS,
    PROGRESS_CADENCE,
    STAGE,
    ConversionOptions,
    ConversionSummary,
)
from bffi_pipeline.stages.bffi_to_marc.emit import _build_marc_record

# Re-exports for backward compatibility — diagnostic tools and tests
# import these from runner; the actual definitions live in extractors.
from bffi_pipeline.stages.bffi_to_marc.extractors import (
    MARC_EMIT_REGISTRY,
    MarcEmitMeta,
    _build_leader,
    _extract_acquisition_source,
    _extract_added_titles,
    _extract_bib_id_from_local,
    _extract_bib_id_from_uri,
    _extract_cataloging_source,
    _extract_change_date,
    _extract_classifications,
    _extract_contributors,
    _extract_edition_statement,
    _extract_electronic_locators,
    _extract_frequency,
    _extract_identifier_datafields,
    _extract_intended_audiences,
    _extract_language_codes,
    _extract_language_components,
    _extract_linking_entries,
    _extract_main_title_parts,
    _extract_modes_of_issuance,
    _extract_notes,
    _extract_origin_place_datafields,
    _extract_physical_description,
    _extract_playing_times,
    _extract_policies,
    _extract_publications,
    _extract_rda_descriptors,
    _extract_responsibility_statement,
    _extract_specialised_5xx_notes,
    _extract_subject_datafields,
    _extract_summaries,
    _extract_supplementary_content,
    _extract_table_of_contents,
    _extract_temporal_coverage,
    _extract_traced_series,
    _extract_uniform_main_entry,
    _extract_untraced_series,
    _extract_variant_titles,
    _VariantTitleEmit,
)

__all__ = [
    "MARC21_NS",
    "MARC_EMIT_REGISTRY",
    "BffiToMarcError",
    "ConversionOptions",
    "ConversionSummary",
    "MarcEmitMeta",
    "convert_corpus",
    "convert_one",
    "emit_marcxml",
]


class BffiToMarcError(RuntimeError):
    """A single-record conversion failed."""


def emit_marcxml(
    graph: Graph,
    *,
    manifestation: URIRef,
    variant_titles: list[_VariantTitleEmit] | None = None,
    options: ConversionOptions | None = None,
) -> bytes:
    """Build a MARCXML document (root: ``<record>``) for one Manifestation.

    ``variant_titles`` is optional — when provided, it overrides the
    titles extracted from ``manifestation``. This is used by
    :func:`convert_one` to merge variant titles from all manifestations
    in a multi-manifestation graph.

    Returns the serialised bytes, pretty-printed, with UTF-8 declaration.
    Raises :exc:`BffiToMarcError` if the bib ID can't be determined (no
    Local block + URI fragment fallback also fails).
    """
    bib_id = _extract_bib_id_from_local(graph, manifestation) or _extract_bib_id_from_uri(
        manifestation
    )
    if bib_id is None:
        raise BffiToMarcError(f"no bib ID found for manifestation {manifestation}")
    change_date = _extract_change_date(graph, manifestation)
    title_parts = _extract_main_title_parts(graph, manifestation)
    variant_titles = (
        variant_titles
        if variant_titles is not None
        else _extract_variant_titles(graph, manifestation)
    )
    uniform_main_entry = _extract_uniform_main_entry(graph, manifestation)
    responsibility = _extract_responsibility_statement(graph, manifestation)
    edition_statement = _extract_edition_statement(graph, manifestation)
    publications = _extract_publications(graph, manifestation)
    identifiers = _extract_identifier_datafields(graph, manifestation)
    language_codes = _extract_language_codes(graph, manifestation)
    language_components = _extract_language_components(graph, manifestation)
    temporal_coverage = _extract_temporal_coverage(graph, manifestation)
    physical = _extract_physical_description(graph, manifestation)
    rda = _extract_rda_descriptors(graph, manifestation)
    classifications = _extract_classifications(graph, manifestation)
    contributors = _extract_contributors(graph, manifestation)
    subjects = _extract_subject_datafields(graph, manifestation)
    acquisition_sources = _extract_acquisition_source(graph, manifestation)
    supplementary_contents = _extract_supplementary_content(graph, manifestation)
    notes = (
        _extract_notes(graph, manifestation)
        + _extract_specialised_5xx_notes(graph, manifestation)
        + _extract_origin_place_datafields(graph, manifestation)
        + _extract_cataloging_source(graph, manifestation)
    )
    table_of_contents = _extract_table_of_contents(graph, manifestation)
    policies = _extract_policies(graph, manifestation)
    summaries = _extract_summaries(graph, manifestation)
    frequencies = _extract_frequency(graph, manifestation)
    playing_times = _extract_playing_times(graph, manifestation)
    modes_of_issuance = _extract_modes_of_issuance(graph, manifestation)
    intended_audiences = _extract_intended_audiences(graph, manifestation)
    untraced_series = _extract_untraced_series(graph, manifestation)
    traced_series = _extract_traced_series(graph, manifestation)
    leader_text = _build_leader(graph, manifestation)
    added_titles = _extract_added_titles(graph, manifestation)
    linking_entries = _extract_linking_entries(graph, manifestation)
    electronic_locators = _extract_electronic_locators(graph, manifestation)
    record = _build_marc_record(
        bib_id=bib_id,
        change_date=change_date,
        title_parts=title_parts,
        variant_titles=variant_titles,
        uniform_main_entry=uniform_main_entry,
        responsibility=responsibility,
        edition_statement=edition_statement,
        publications=publications,
        identifiers=identifiers,
        language_codes=language_codes,
        language_components=language_components,
        temporal_coverage=temporal_coverage,
        physical=physical,
        rda=rda,
        classifications=classifications,
        contributors=contributors,
        subjects=subjects,
        acquisition_sources=acquisition_sources,
        supplementary_contents=supplementary_contents,
        notes=notes,
        table_of_contents=table_of_contents,
        policies=policies,
        summaries=summaries,
        frequencies=frequencies,
        playing_times=playing_times,
        modes_of_issuance=modes_of_issuance,
        intended_audiences=intended_audiences,
        untraced_series=untraced_series,
        traced_series=traced_series,
        leader_text=leader_text,
        added_titles=added_titles,
        linking_entries=linking_entries,
        electronic_locators=electronic_locators,
        options=options,
    )
    return etree.tostring(
        record,
        pretty_print=True,
        xml_declaration=True,
        encoding="utf-8",
    )


def convert_one(bffi_path: Path, *, options: ConversionOptions) -> Path:
    """Convert one BFFI Turtle to MARCXML.

    Writes ``<output_dir>/<stem>.marcxml`` and returns the path.
    Raises :exc:`BffiToMarcError` on parse failure or when no
    Manifestation is present in the input graph.
    """
    output_path = options.output_dir / f"{bffi_path.stem.removesuffix('.bffi')}.marcxml"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    graph = Graph()
    try:
        graph.parse(bffi_path, format="turtle")
    except Exception as exc:
        raise BffiToMarcError(f"rdflib parse failed for {bffi_path}: {exc}") from exc

    manifestations = [
        m for m in graph.subjects(RDF.type, BFFI.Manifestation) if isinstance(m, URIRef)
    ]
    if not manifestations:
        raise BffiToMarcError(f"no bffi:Manifestation entity in {bffi_path} — nothing to emit")

    # Merge variant titles from all manifestations — a single MARC source
    # record may produce multiple bf:Manifestation nodes (marc2bibframe2's
    # preprocess-splitter), and variant titles (246) can live on the Work
    # of any one of them. Collecting them all ensures we recover the data
    # regardless of which manifestation happens to be "first".
    merged_variant_titles: list[_VariantTitleEmit] = []
    seen_titles: set[tuple[str, str]] = set()
    for manifest in manifestations:
        for vt in _extract_variant_titles(graph, manifest):
            key = (vt.tag, vt.text)
            if key not in seen_titles:
                seen_titles.add(key)
                merged_variant_titles.append(vt)
    merged_variant_titles.sort(key=lambda e: (e.tag, e.text))

    # v0: one MARCXML record for the first Manifestation. Multi-Manifestation
    # graphs (marc2bibframe2's preprocess-splitter output) are a follow-on
    # concern.
    marcxml_bytes = emit_marcxml(
        graph,
        manifestation=manifestations[0],
        variant_titles=merged_variant_titles,
        options=options,
    )
    output_path.write_bytes(marcxml_bytes)
    return output_path


def convert_corpus(*, options: ConversionOptions) -> ConversionSummary:
    """Walk ``options.input_dir`` and convert every ``*.bffi.ttl`` to MARCXML.

    Emits observability events through the active emitter (if any):

      - ``start``    once at entry, with ``entities_total``
      - ``progress`` every ``PROGRESS_CADENCE`` records
      - ``failed``   per record that raised :exc:`BffiToMarcError`
      - ``end``      once at exit, with success / failed bucket counts

    Returns the aggregate :class:`ConversionSummary`.
    """
    bffi_files = sorted(options.input_dir.glob("*.bffi.ttl"))
    total = len(bffi_files)

    emit_if_active(
        stage=STAGE,
        event="start",
        counters={"entities_total": total},
    )

    summary = ConversionSummary(total=total)

    for idx, path in enumerate(bffi_files, start=1):
        try:
            convert_one(path, options=options)
            summary.converted += 1
        except BffiToMarcError as exc:
            summary.failed += 1
            message = str(exc)
            if "no bffi:Manifestation" in message:
                summary.no_manifestation += 1
            summary.failures.append((path, message))
            emit_if_active(
                stage=STAGE,
                event="failed",
                extra={
                    "path": str(path),
                    "error": message[:240],
                    "error_type": type(exc).__name__,
                },
            )

        if idx % PROGRESS_CADENCE == 0 or idx == total:
            emit_if_active(
                stage=STAGE,
                event="progress",
                counters={"entities_processed": idx},
            )

    emit_if_active(
        stage=STAGE,
        event="end",
        counters={
            "success": summary.converted,
            "failed": summary.failed,
            "no_manifestation": summary.no_manifestation,
        },
    )

    return summary
