"""Pillar 4 emit layer: structured dataclasses -> MARCXML ``<record>``.

This module takes the dataclasses produced by
:mod:`bffi_pipeline.stages.bffi_to_marc.extractors` and constructs
a MARC21 ``<record>`` element using ``lxml.etree``. It is decoupled
from the graph-walking logic -- it only consumes typed Python data
and emits XML.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from lxml import etree

from bffi_pipeline.stages.bffi_to_marc.alt_script import AltScriptInfo
from bffi_pipeline.stages.bffi_to_marc.constants import MARC21_NS, ConversionOptions
from bffi_pipeline.stages.bffi_to_marc.extractors import (
    MarcEmitMeta,
    _AcquisitionSourceEmit,
    _AddedTitleEmit,
    _ClassificationEmit,
    _ContributorEmit,
    _FrequencyEmit,
    _IdentifierEmit,
    _NoteEmit,
    _PhysicalDescription,
    _PolicyEmits,
    _PublicationEmit,
    _RdaDescriptors,
    _RdaEntry,
    _SubjectEmit,
    _SupplementaryContentEmit,
    _TitleParts,
    _UntracedSeriesEmit,
    _VariantTitleEmit,
    marc_emit_dynamic,
)
from bffi_pipeline.stages.bffi_to_marc.isbd import get_isbd_punctuation

_MARC: Final[str] = f"{{{MARC21_NS}}}"


def _deduplicate_datafields(record: etree._Element) -> None:
    """Remove exact duplicate datafields from a MARC record.

    A "duplicate" is a datafield with the same tag, ind1, ind2, and
    subfield content (same order — MARC order is structural but
    duplicates from marc2bibframe2 have identical subfield order). The
    first occurrence is kept; subsequent duplicates are removed.

    This is a post-processing step that runs after all datafields have
    been appended to the record. It does not modify the order of
    remaining fields.
    """
    datafields = [child for child in record if child.tag.endswith("datafield")]
    seen: set[tuple[str, str, str, tuple[tuple[str, str], ...]]] = set()
    for df in datafields:
        tag = df.get("tag")
        if tag is None:
            continue
        ind1 = df.get("ind1", " ") or " "
        ind2 = df.get("ind2", " ") or " "
        subfields: tuple[tuple[str, str], ...] = tuple(
            (sf.get("code") or "", sf.text or "") for sf in df if sf.tag.endswith("subfield")
        )
        key: tuple[str, str, str, tuple[tuple[str, str], ...]] = (tag, ind1, ind2, subfields)
        if key in seen:
            record.remove(df)
        else:
            seen.add(key)


def _append_simple_a_datafields(
    record: etree._Element,
    tag: str,
    values: tuple[str, ...],
    *,
    options: ConversionOptions | None = None,
) -> None:
    """Append one MARC datafield per value, each with a single ``$a``
    subfield carrying the value and blank indicators. Used for MARC
    families whose entire emit shape is a list of bare ``$a`` rows
    (336 / 337 / 338 RDA descriptors today; potentially others).

    ISBD trailing punctuation is added when ``options.apply_isbd_punctuation``
    is True."""
    _isbd_enabled = options.apply_isbd_punctuation if options else False
    for value in values:
        df = etree.SubElement(record, f"{_MARC}datafield", tag=tag, ind1=" ", ind2=" ")
        sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
        if _isbd_enabled:
            sf_a.text = value + get_isbd_punctuation(
                tag=tag,
                subfield_code="a",
                next_subfield_code=None,
                enabled=True,
            )
        else:
            sf_a.text = value


@marc_emit_dynamic(
    MarcEmitMeta(
        tag="880",
        indicators=(" ", " "),
        subfields=(
            ("6", "linkage to main field: {main_tag}-{occurrence}/{script_indicator}"),
            ("a", "alt-script value (language-tagged duplicate on main field predicates)"),
        ),
        source=(
            "Derived from language-tagged duplicates detected on the main field's "
            "BFFI predicates (e.g. agent rdfs:label, title bffi:mainTitle, etc.). "
            "One 880 per alt-script value, with occurrence-numbered $6 linkage."
        ),
        notes=(
            "**880 is a reconstruction artifact**, not a primary MARC field. "
            "It mirrors alt-script content detected on the main field during "
            "BFFI → MARC conversion. The $6 field links it to the main field as "
            "'{main_tag}-{occurrence:02d}{script_indicator}'. Extra subfields "
            "($e, $b, $c) are copied from the alt-script dataclass when present."
        ),
    ),
)
def _reserve_occurrence(alt_script_counter: dict[str, int], _main_tag: str) -> int:
    """Pre-increment the global occurrence counter and return the new value.

    Call this BEFORE emitting the main field so that the main field
    and the 880 field share the same occurrence number in their ``$6``
    linkage. The counter is GLOBAL across all tags (not per-tag),
    matching MARC's sequential 880 occurrence numbering.

    ``_main_tag`` is accepted for API compatibility but not used —
    the counter is shared across all tags.
    """
    global_key = "__global__"
    if global_key not in alt_script_counter:
        alt_script_counter[global_key] = 0
    alt_script_counter[global_key] += 1
    return alt_script_counter[global_key]


def _replace_marckey_six(
    extra_subfields: tuple[tuple[str, str], ...], occurrence: int
) -> tuple[tuple[str, str], ...]:
    """Replace any ``$6`` in ``extra_subfields`` with the reconstructed
    occurrence linkage.

    The marcKey may carry a ``$6`` from the source MARC, but we must
    overwrite it with the reconstructed value to ensure consistency
    with the 880 field's ``$6`` (which uses the same global occurrence
    counter).
    """
    return tuple(
        ("6", f"880-{occurrence:02d}") if code == "6" else (code, value)
        for code, value in extra_subfields
    )


def _append_alt_script_datafields(
    record: etree._Element,
    main_tag: str,
    main_ind1: str,
    main_ind2: str,
    main_label: str,
    alt_scripts: tuple[AltScriptInfo, ...],
    alt_script_counter: dict[str, int],
    main_occurrence: int | None = None,
    *,
    options: ConversionOptions | None = None,
) -> None:
    """Append MARC 880 fields for alt-script duplicates.

    Emits one 880 field per alt-script value, with occurrence-numbered
    ``$6`` qualifiers. The main field's ``$6`` must have been set
    beforehand via :func:`_reserve_occurrence` +
    :func:`_append_main_field_with_occurrence`.

    ``alt_script_counter`` is updated in-place to track occurrence
    numbers per tag.
    """
    if main_occurrence is None:
        if main_tag not in alt_script_counter:
            alt_script_counter[main_tag] = 0
        alt_script_counter[main_tag] += 1
        main_occurrence = alt_script_counter[main_tag]

    _isbd_enabled = options.apply_isbd_punctuation if options else False
    for alt in alt_scripts:
        df = etree.SubElement(
            record, f"{_MARC}datafield", tag="880", ind1=main_ind1, ind2=main_ind2
        )
        # $6 linkage: main_tag-occurrence/script_indicator
        sf_6 = etree.SubElement(df, f"{_MARC}subfield", code="6")
        sf_6.text = f"{main_tag}-{main_occurrence:02d}{alt.script_indicator}"
        # $a alt-script value with ISBD punctuation
        sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
        if _isbd_enabled and alt.extra_subfields:
            _next_a = alt.extra_subfields[0][0]
            sf_a.text = alt.value + get_isbd_punctuation(
                tag=main_tag,
                subfield_code="a",
                next_subfield_code=_next_a,
                enabled=True,
            )
        elif _isbd_enabled:
            sf_a.text = alt.value + get_isbd_punctuation(
                tag=main_tag,
                subfield_code="a",
                next_subfield_code=None,
                enabled=True,
            )
        else:
            sf_a.text = alt.value
        # Extra subfields (e.g., $e relator for contributors)
        for i, (code, value) in enumerate(alt.extra_subfields):
            sf = etree.SubElement(df, f"{_MARC}subfield", code=code)
            if _isbd_enabled:
                _next_code = (
                    alt.extra_subfields[i + 1][0] if i + 1 < len(alt.extra_subfields) else None
                )
                sf.text = value + get_isbd_punctuation(
                    tag=main_tag,
                    subfield_code=code,
                    next_subfield_code=_next_code,
                    enabled=True,
                )
            else:
                sf.text = value


def _append_contributor_datafields(
    record: etree._Element,
    contributors: Iterable[_ContributorEmit],
    alt_script_counter: dict[str, int],
    *,
    options: ConversionOptions | None = None,
) -> None:
    """Append one MARC contributor datafield per emit. Subfield order
    follows the MARC X00 spec: ``$a`` (name) → ``$e`` (relator term,
    free text) → extras from marcKey (``$t`` analytical title, ``$c``
    qualifier, ``$d`` dates, …) → ``$4`` (LoC relator code). Each is
    optional except ``$a``. Indicators come from the agent's marcKey
    when present, else default to blank.

    If the contributor has alt-script duplicates (non-Latin script
    versions detected in BFFI), emits MARC 880 fields after the main
    field with occurrence-numbered ``$6`` qualifiers."""
    for c in contributors:
        # Pre-reserve occurrence for alt-script linkage
        main_occurrence: int | None = None
        if c.alt_scripts:
            main_occurrence = _reserve_occurrence(alt_script_counter, c.tag)

        df = etree.SubElement(record, f"{_MARC}datafield", tag=c.tag, ind1=c.ind1, ind2=c.ind2)
        _isbd_enabled = options.apply_isbd_punctuation if options else False
        # Determine which subfields are present for ISBD punctuation
        _has_e = c.relator_term is not None
        _has_4 = c.relator is not None
        sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
        if _isbd_enabled:
            _next_a = "e" if _has_e else ("4" if _has_4 else None)
            _punct = get_isbd_punctuation(
                tag=c.tag, subfield_code="a", next_subfield_code=_next_a, enabled=True
            )
            # Avoid double punctuation if value already ends with the punctuation char
            if _punct and c.label.endswith(_punct[-1]):
                sf_a.text = c.label
            else:
                sf_a.text = c.label + _punct
        else:
            sf_a.text = c.label
        if c.relator_term:
            sf_e = etree.SubElement(df, f"{_MARC}subfield", code="e")
            if _isbd_enabled:
                _next_e = "4" if _has_4 else None
                _punct = get_isbd_punctuation(
                    tag=c.tag, subfield_code="e", next_subfield_code=_next_e, enabled=True
                )
                # Avoid double punctuation if value already ends with the punctuation char
                if _punct and c.relator_term.endswith(_punct[-1]):
                    sf_e.text = c.relator_term
                else:
                    sf_e.text = c.relator_term + _punct
            else:
                sf_e.text = c.relator_term
        # Overwrite marcKey's $6 with reconstructed occurrence linkage
        extra = c.extra_subfields
        if c.alt_scripts and alt_script_counter is not None and main_occurrence is not None:
            extra = _replace_marckey_six(extra, main_occurrence)
        for code, value in extra:
            sf = etree.SubElement(df, f"{_MARC}subfield", code=code)
            sf.text = value
        if c.relator:
            sf_4 = etree.SubElement(df, f"{_MARC}subfield", code="4")
            if _isbd_enabled:
                _punct = get_isbd_punctuation(
                    tag=c.tag, subfield_code="4", next_subfield_code=None, enabled=True
                )
                if _punct and c.relator.endswith(_punct[-1]):
                    sf_4.text = c.relator
                else:
                    sf_4.text = c.relator + _punct
            else:
                sf_4.text = c.relator

        # Emit 880 fields for alt-script duplicates
        if c.alt_scripts:
            _append_alt_script_datafields(
                record=record,
                main_tag=c.tag,
                main_ind1=c.ind1,
                main_ind2=c.ind2,
                main_label=c.label,
                alt_scripts=c.alt_scripts,
                alt_script_counter=alt_script_counter,
                main_occurrence=main_occurrence,
                options=options,
            )


def _append_physical_description_datafield(
    record: etree._Element,
    physical: _PhysicalDescription,
    *,
    options: ConversionOptions | None = None,
) -> None:
    """Append the MARC 300 datafield with ``$a`` / ``$b`` / ``$c`` / ``$e``
    subfields based on which signals are present. ISBD trailing punctuation
    (\" :\" before $b, \" ;\" before $c, \" +\" before $e) is added when
    ``options.apply_isbd_punctuation`` is True."""
    df = etree.SubElement(record, f"{_MARC}datafield", tag="300", ind1=" ", ind2=" ")
    _isbd_enabled = options.apply_isbd_punctuation if options else False
    # Determine which subfields are present for ISBD punctuation
    _has_b = physical.other_physical is not None
    _has_c = physical.dimensions is not None
    _has_e = physical.accompanying_material is not None
    if physical.extent is not None:
        text = physical.extent
        _next_a = "b" if _has_b else ("c" if _has_c else ("e" if _has_e else None))
        text += get_isbd_punctuation(
            tag="300",
            subfield_code="a",
            next_subfield_code=_next_a,
            enabled=_isbd_enabled,
        )
        sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
        sf_a.text = text
    if physical.other_physical is not None:
        text = physical.other_physical
        _next_b = "c" if _has_c else ("e" if _has_e else None)
        _punct_b = get_isbd_punctuation(
            tag="300",
            subfield_code="b",
            next_subfield_code=_next_b,
            enabled=_isbd_enabled,
        )
        # Avoid double punctuation if value already ends with the punctuation char
        sf_b_text = text if (_punct_b and text.endswith(_punct_b[-1])) else text + _punct_b
        sf_b = etree.SubElement(df, f"{_MARC}subfield", code="b")
        sf_b.text = sf_b_text
    if physical.dimensions is not None:
        text = physical.dimensions
        _next_c = "e" if _has_e else None
        text += get_isbd_punctuation(
            tag="300",
            subfield_code="c",
            next_subfield_code=_next_c,
            enabled=_isbd_enabled,
        )
        sf_c = etree.SubElement(df, f"{_MARC}subfield", code="c")
        sf_c.text = text
    if physical.accompanying_material is not None:
        sf_e = etree.SubElement(df, f"{_MARC}subfield", code="e")
        sf_e.text = physical.accompanying_material + get_isbd_punctuation(
            tag="300",
            subfield_code="e",
            next_subfield_code=None,
            enabled=_isbd_enabled,
        )


def _append_identifier_datafields(
    record: etree._Element, identifiers: list[_IdentifierEmit]
) -> None:
    """Append one MARC datafield per identifier emit.

    Subfield order follows the MARC spec: ``$a`` value → ``$b``
    assigner (028 issuing-publisher name) → ``$q`` qualifier (020 ISBN
    binding / format). Indicators come from the per-scheme dispatch
    table.
    """
    for ident in identifiers:
        df = etree.SubElement(
            record, f"{_MARC}datafield", tag=ident.tag, ind1=ident.ind1, ind2=ident.ind2
        )
        sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
        sf_a.text = ident.value
        if ident.assigner is not None:
            sf_b = etree.SubElement(df, f"{_MARC}subfield", code="b")
            sf_b.text = ident.assigner
        if ident.qualifier is not None:
            sf_q = etree.SubElement(df, f"{_MARC}subfield", code="q")
            sf_q.text = ident.qualifier


def _append_note_block(
    record: etree._Element,
    *,
    notes: list[_NoteEmit],
    table_of_contents: list[str],
    policies: _PolicyEmits,
    summaries: list[str],
    intended_audiences: list[str],
    alt_script_counter: dict[str, int] | None = None,
    options: ConversionOptions | None = None,
) -> None:
    """Append the 5XX note block in (approximately) MARC tag-numeric
    order: 500-set general notes → 505 contents → 506 access → 520
    summary → 521 intended audience → 540 use."""
    _append_note_datafields(record, notes, alt_script_counter, options=options)
    _append_table_of_contents_datafields(record, table_of_contents)
    _append_simple_a_datafields(record, "506", policies.access, options=options)
    _append_simple_a_datafields(record, "520", tuple(summaries), options=options)
    _append_simple_a_datafields(record, "521", tuple(intended_audiences), options=options)
    _append_simple_a_datafields(record, "540", policies.use, options=options)


def _append_note_datafields(
    record: etree._Element,
    notes: list[_NoteEmit],
    alt_script_counter: dict[str, int] | None = None,
    *,
    options: ConversionOptions | None = None,
) -> None:
    """Append one MARC 5XX-style datafield per note emit. Indicators
    default to blank; 587 overrides ind1 from the mnotetype tail.

    If ``alt_script_counter`` is provided and a note has
    alt-script duplicates, emits MARC 880 fields after the note
    field."""
    for note in notes:
        # Pre-reserve occurrence for alt-script linkage
        main_occurrence: int | None = None
        if note.alt_scripts and alt_script_counter is not None:
            main_occurrence = _reserve_occurrence(alt_script_counter, note.tag)

        df = etree.SubElement(
            record, f"{_MARC}datafield", tag=note.tag, ind1=note.ind1, ind2=note.ind2
        )
        if note.text:
            _isbd_enabled = options.apply_isbd_punctuation if options else False
            sf = etree.SubElement(df, f"{_MARC}subfield", code=note.subfield_code)
            if _isbd_enabled:
                sf.text = note.text + get_isbd_punctuation(
                    tag=note.tag,
                    subfield_code=note.subfield_code,
                    next_subfield_code=None,
                    enabled=True,
                )
            else:
                sf.text = note.text
            # Add $6 linkage when alt-script is present
            if note.alt_scripts and alt_script_counter is not None and main_occurrence is not None:
                sf_6 = etree.SubElement(df, f"{_MARC}subfield", code="6")
                sf_6.text = f"880-{main_occurrence:02d}"
        for code, value in note.extra_subfields:
            # Overwrite marcKey's $6 with reconstructed occurrence linkage
            has_alt = (
                note.alt_scripts and alt_script_counter is not None and main_occurrence is not None
            )
            if code == "6" and has_alt:
                sf_6 = etree.SubElement(df, f"{_MARC}subfield", code="6")
                sf_6.text = f"880-{main_occurrence:02d}"
            else:
                sf = etree.SubElement(df, f"{_MARC}subfield", code=code)
                sf.text = value

        # Emit 880 fields for alt-script duplicates
        if note.alt_scripts and alt_script_counter is not None:
            _append_alt_script_datafields(
                record=record,
                main_tag=note.tag,
                main_ind1=note.ind1,
                main_ind2=note.ind2,
                main_label=note.text,
                alt_scripts=note.alt_scripts,
                alt_script_counter=alt_script_counter,
                main_occurrence=main_occurrence,
                options=options,
            )


def _append_table_of_contents_datafields(
    record: etree._Element, table_of_contents: list[str]
) -> None:
    """Append one MARC 505 datafield per table-of-contents text.

    ind1=0 = "Contents" (the default per the MARC 21 spec).
    """
    for text in table_of_contents:
        df = etree.SubElement(record, f"{_MARC}datafield", tag="505", ind1="0", ind2=" ")
        sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
        sf_a.text = text


def _append_rda_datafields(
    record: etree._Element,
    tag: str,
    entries: tuple[_RdaEntry, ...],
    *,
    options: ConversionOptions | None = None,
) -> None:
    """Append one MARC datafield per RDA descriptor: ``$a`` label (when
    present), ``$b`` 3-letter code, ``$2`` scheme name, ``$3`` materials
    specified (when ``bffi:appliesTo`` is present). Multiple values on one
    BFFI predicate produce multiple datafields per MARC convention.

    ISBD trailing punctuation is added when ``options.apply_isbd_punctuation``
    is True."""
    _isbd_enabled = options.apply_isbd_punctuation if options else False
    for entry in entries:
        df = etree.SubElement(record, f"{_MARC}datafield", tag=tag, ind1=" ", ind2=" ")
        _has_b = entry.code is not None
        _has_2 = entry.scheme is not None
        _has_3 = entry.applies_to is not None
        if entry.label is not None:
            sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
            if _isbd_enabled:
                _next_a = "b" if _has_b else ("2" if _has_2 else ("3" if _has_3 else None))
                sf_a.text = entry.label + get_isbd_punctuation(
                    tag=tag,
                    subfield_code="a",
                    next_subfield_code=_next_a,
                    enabled=True,
                )
            else:
                sf_a.text = entry.label
        sf_b = etree.SubElement(df, f"{_MARC}subfield", code="b")
        if _isbd_enabled:
            _next_b = "2" if _has_2 else ("3" if _has_3 else None)
            sf_b.text = entry.code + get_isbd_punctuation(
                tag=tag,
                subfield_code="b",
                next_subfield_code=_next_b,
                enabled=True,
            )
        else:
            sf_b.text = entry.code
        sf_2 = etree.SubElement(df, f"{_MARC}subfield", code="2")
        if _isbd_enabled:
            _next_2 = "3" if _has_3 else None
            sf_2.text = entry.scheme + get_isbd_punctuation(
                tag=tag,
                subfield_code="2",
                next_subfield_code=_next_2,
                enabled=True,
            )
        else:
            sf_2.text = entry.scheme
        if entry.applies_to is not None:
            sf_3 = etree.SubElement(df, f"{_MARC}subfield", code="3")
            if _isbd_enabled:
                sf_3.text = entry.applies_to + get_isbd_punctuation(
                    tag=tag,
                    subfield_code="3",
                    next_subfield_code=None,
                    enabled=True,
                )
            else:
                sf_3.text = entry.applies_to


def _append_title_datafield(
    record: etree._Element,
    title_parts: _TitleParts,
    responsibility: str | None,
    alt_script_counter: dict[str, int] | None = None,
    *,
    options: ConversionOptions | None = None,
) -> None:
    """Append the MARC 245 datafield. Subfield order follows MARC 21:
    ``$a`` main title → ``$n`` part number → ``$p`` part name → ``$b``
    subtitle → ``$c`` statement of responsibility. Each is optional
    except ``$a``.

    If ``alt_script_counter`` is provided and the title has
    alt-script duplicates, emits MARC 880 fields after the 245."""
    # Pre-reserve occurrence for alt-script linkage
    main_occurrence: int | None = None
    if title_parts.alt_scripts and alt_script_counter is not None:
        main_occurrence = _reserve_occurrence(alt_script_counter, "245")

    df = etree.SubElement(record, f"{_MARC}datafield", tag="245", ind1="0", ind2="0")
    _isbd_enabled = options.apply_isbd_punctuation if options else False
    # Determine which subfields are present for ISBD punctuation ($n, $p are ISBD-neutral)
    _has_b = title_parts.subtitle is not None
    _has_c = responsibility is not None
    sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
    if _isbd_enabled:
        _next_a = "b" if _has_b else ("c" if _has_c else None)
        sf_a.text = title_parts.main + get_isbd_punctuation(
            tag="245", subfield_code="a", next_subfield_code=_next_a, enabled=True
        )
    else:
        sf_a.text = title_parts.main
    # Add $6 linkage when alt-script is present
    if title_parts.alt_scripts and alt_script_counter is not None and main_occurrence is not None:
        sf_6 = etree.SubElement(df, f"{_MARC}subfield", code="6")
        sf_6.text = f"880-{main_occurrence:02d}"
    if title_parts.part_number is not None:
        sf_n = etree.SubElement(df, f"{_MARC}subfield", code="n")
        sf_n.text = title_parts.part_number
    if title_parts.part_name is not None:
        sf_p = etree.SubElement(df, f"{_MARC}subfield", code="p")
        sf_p.text = title_parts.part_name
    if title_parts.subtitle is not None:
        sf_b = etree.SubElement(df, f"{_MARC}subfield", code="b")
        if _isbd_enabled:
            _next_b = "c" if _has_c else None
            sf_b.text = title_parts.subtitle + get_isbd_punctuation(
                tag="245", subfield_code="b", next_subfield_code=_next_b, enabled=True
            )
        else:
            sf_b.text = title_parts.subtitle
    if responsibility is not None:
        sf_c = etree.SubElement(df, f"{_MARC}subfield", code="c")
        if _isbd_enabled:
            sf_c.text = responsibility + get_isbd_punctuation(
                tag="245", subfield_code="c", next_subfield_code=None, enabled=True
            )
        else:
            sf_c.text = responsibility

    # Emit 880 fields for alt-script duplicates
    if title_parts.alt_scripts and alt_script_counter is not None:
        _append_alt_script_datafields(
            record=record,
            main_tag="245",
            main_ind1="0",
            main_ind2="0",
            main_label=title_parts.main,
            alt_scripts=title_parts.alt_scripts,
            alt_script_counter=alt_script_counter,
            main_occurrence=main_occurrence,
            options=options,
        )


def _append_publication_datafield(
    record: etree._Element,
    publication: _PublicationEmit,
    alt_script_counter: dict[str, int] | None = None,
    *,
    options: ConversionOptions | None = None,
) -> None:
    """Append the MARC 260/264 datafield with structured ``$a`` / ``$b`` / ``$c``
    when ``bffi:simplePlace`` / ``bffi:simpleAgent`` / ``bffi:simpleDate``
    are present on the Publication-typed provisionActivity. Falls back to
    a single ``$a`` carrying the flat ``bffi:publicationStatement`` literal
    when the structured parts are absent.

    ``publication.ind1`` determines the MARC source tag: ``"1"`` → 260,
    ``"4"`` → 264 (copyright). ISBD trailing punctuation is added per the
    MARC convention: ``$a "Place :"`` precedes ``$b``; ``$b "Publisher,"``
    precedes ``$c``. No trailing punctuation on the last present subfield.

    If ``alt_script_counter`` is provided and the publication has
    alt-script duplicates, emits MARC 880 fields after the publication
    field."""
    # Use 264 for structured place/agent/date, 260 for flat publicationStatement
    if publication.place is None and publication.agent is None and publication.date is None:
        tag = "260"
    else:
        tag = "264" if publication.ind1 in (" ", "1", "4") else "260"

    # Pre-reserve occurrence for alt-script linkage
    main_occurrence: int | None = None
    if publication.alt_scripts and alt_script_counter is not None:
        main_occurrence = _reserve_occurrence(alt_script_counter, tag)

    df = etree.SubElement(record, f"{_MARC}datafield", tag=tag, ind1=publication.ind1, ind2=" ")
    if publication.place is None and publication.agent is None and publication.date is None:
        sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
        sf_a.text = publication.statement
        pub_has_alt = (
            publication.alt_scripts
            and alt_script_counter is not None
            and main_occurrence is not None
        )
        if pub_has_alt:
            sf_6 = etree.SubElement(df, f"{_MARC}subfield", code="6")
            sf_6.text = f"880-{main_occurrence:02d}"
        return
    # Determine which subfields are present for ISBD punctuation
    _has_b = publication.agent is not None
    _has_c = publication.date is not None
    _isbd_enabled = options.apply_isbd_punctuation if options else False

    if publication.place is not None:
        place_text = publication.place
        _next_a = "b" if _has_b else ("c" if _has_c else None)
        place_text += get_isbd_punctuation(
            tag=tag,
            subfield_code="a",
            next_subfield_code=_next_a,
            enabled=_isbd_enabled,
        )
        sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
        sf_a.text = place_text
    if publication.agent is not None:
        agent_text = publication.agent
        _next_b = "c" if _has_c else None
        agent_text += get_isbd_punctuation(
            tag=tag,
            subfield_code="b",
            next_subfield_code=_next_b,
            enabled=_isbd_enabled,
        )
        sf_b = etree.SubElement(df, f"{_MARC}subfield", code="b")
        sf_b.text = agent_text
    if publication.date is not None:
        date_text = publication.date
        date_text += get_isbd_punctuation(
            tag=tag,
            subfield_code="c",
            next_subfield_code=None,
            enabled=_isbd_enabled,
        )
        sf_c = etree.SubElement(df, f"{_MARC}subfield", code="c")
        sf_c.text = date_text
    # Add $6 linkage when alt-script is present
    if publication.alt_scripts and alt_script_counter is not None and main_occurrence is not None:
        sf_6 = etree.SubElement(df, f"{_MARC}subfield", code="6")
        sf_6.text = f"880-{main_occurrence:02d}"

    # Emit 880 fields for alt-script duplicates
    if publication.alt_scripts and alt_script_counter is not None:
        _append_alt_script_datafields(
            record=record,
            main_tag=tag,
            main_ind1=publication.ind1,
            main_ind2="0",
            main_label=publication.place or publication.statement or "",
            alt_scripts=publication.alt_scripts,
            alt_script_counter=alt_script_counter,
            main_occurrence=main_occurrence,
            options=options,
        )


def _append_subject_datafields(
    record: etree._Element,
    subjects: list[_SubjectEmit],
    alt_script_counter: dict[str, int] | None = None,
    *,
    options: ConversionOptions | None = None,
) -> None:
    """Append one MARC 6XX datafield per subject emit.

    ``$a`` carries the heading text. ``$0`` (authority URI) and ``$2``
    (source-vocabulary code) emit when their respective signals are
    present in BFFI. When ``$2`` is emitted, ``ind2`` is set to ``"7"``
    per the MARC convention ("source specified in subfield $2");
    otherwise ``ind2`` is blank.

    If ``alt_script_counter`` is provided and a subject has
    alt-script duplicates, emits MARC 880 fields after the subject
    field."""
    for subj in subjects:
        # Use marcKey indicators when present; otherwise default to blank.
        # When $2 is emitted (vocab_code present), ind2 becomes "7" per the
        # MARC convention ("source specified in subfield $2").
        ind1 = subj.ind1
        ind2 = "7" if subj.vocab_code else subj.ind2
        # Pre-reserve occurrence for alt-script linkage
        main_occurrence: int | None = None
        if subj.alt_scripts and alt_script_counter is not None:
            main_occurrence = _reserve_occurrence(alt_script_counter, subj.tag)

        df = etree.SubElement(record, f"{_MARC}datafield", tag=subj.tag, ind1=ind1, ind2=ind2)
        _isbd_enabled = options.apply_isbd_punctuation if options else False
        sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
        if _isbd_enabled:
            sf_a.text = subj.label + get_isbd_punctuation(
                tag=subj.tag,
                subfield_code="a",
                next_subfield_code=None,
                enabled=True,
            )
        else:
            sf_a.text = subj.label
        # marcKey-driven extras ($t, $c, $d, …) come between $a and the
        # structured $0 / $2 — MARC subfield order is alphabetical-ish
        # but $0 / $2 sort after letters per convention.
        # Overwrite marcKey's $6 with reconstructed occurrence linkage
        extra = subj.extra_subfields
        if subj.alt_scripts and alt_script_counter is not None and main_occurrence is not None:
            extra = _replace_marckey_six(extra, main_occurrence)
        for code, value in extra:
            sf = etree.SubElement(df, f"{_MARC}subfield", code=code)
            sf.text = value
        if subj.authority_uri:
            sf_0 = etree.SubElement(df, f"{_MARC}subfield", code="0")
            sf_0.text = subj.authority_uri
        if subj.vocab_code:
            sf_2 = etree.SubElement(df, f"{_MARC}subfield", code="2")
            sf_2.text = subj.vocab_code
        # Add $6 linkage when alt-script is present (if no extra_subfields)
        subj_has_alt = (
            subj.alt_scripts
            and alt_script_counter is not None
            and main_occurrence is not None
            and not subj.extra_subfields
        )
        if subj_has_alt:
            sf_6 = etree.SubElement(df, f"{_MARC}subfield", code="6")
            sf_6.text = f"880-{main_occurrence:02d}"

        # Emit 880 fields for alt-script duplicates
        if subj.alt_scripts and alt_script_counter is not None:
            _append_alt_script_datafields(
                record=record,
                main_tag=subj.tag,
                main_ind1="0",
                main_ind2=ind2,
                main_label=subj.label,
                alt_scripts=subj.alt_scripts,
                alt_script_counter=alt_script_counter,
                main_occurrence=main_occurrence,
                options=options,
            )


def _append_classification_datafields(
    record: etree._Element, classifications: list[_ClassificationEmit]
) -> None:
    """Append one MARC classification datafield per emit with ``$a``
    portion and optional ``$2`` scheme code. The MARC tag (050 / 060 /
    070 / 080 / 082 / 084) is picked per-emit by the classification's
    BFFI type."""
    for cls in classifications:
        df = etree.SubElement(record, f"{_MARC}datafield", tag=cls.tag, ind1=" ", ind2=" ")
        sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
        sf_a.text = cls.portion
        if cls.code is not None:
            sf_2 = etree.SubElement(df, f"{_MARC}subfield", code="2")
            sf_2.text = cls.code


def _append_acquisition_source_datafields(
    record: etree._Element, sources: list[_AcquisitionSourceEmit]
) -> None:
    """Append one MARC 037 datafield per emit with ``$a`` stock number,
    ``$b`` imprint, ``$c`` acquisition terms, ``$f`` other physical,
    ``$g`` dimensions, ``$n`` copies held."""
    for src in sources:
        df = etree.SubElement(record, f"{_MARC}datafield", tag="037", ind1=" ", ind2=" ")
        if src.stock_number is not None:
            sf = etree.SubElement(df, f"{_MARC}subfield", code="a")
            sf.text = src.stock_number
        if src.imprint is not None:
            sf = etree.SubElement(df, f"{_MARC}subfield", code="b")
            sf.text = src.imprint
        if src.place is not None:
            sf = etree.SubElement(df, f"{_MARC}subfield", code="c")
            sf.text = src.place
        if src.other_physical is not None:
            sf = etree.SubElement(df, f"{_MARC}subfield", code="f")
            sf.text = src.other_physical
        if src.dimensions is not None:
            sf = etree.SubElement(df, f"{_MARC}subfield", code="g")
            sf.text = src.dimensions
        if src.copies is not None:
            sf = etree.SubElement(df, f"{_MARC}subfield", code="n")
            sf.text = src.copies


def _append_supplementary_content_datafields(
    record: etree._Element,
    contents: list[_SupplementaryContentEmit],
    *,
    options: ConversionOptions | None = None,
) -> None:
    """Append one MARC 353 datafield per emit with ``$a`` content,
    ``$0`` authority URI, ``$2`` source scheme code.

    ISBD trailing punctuation is added when ``options.apply_isbd_punctuation``
    is True."""
    _isbd_enabled = options.apply_isbd_punctuation if options else False
    for sup in contents:
        df = etree.SubElement(record, f"{_MARC}datafield", tag="353", ind1=" ", ind2=" ")
        _has_0 = sup.authority_uri is not None
        _has_2 = sup.source is not None
        if sup.content is not None:
            sf = etree.SubElement(df, f"{_MARC}subfield", code="a")
            if _isbd_enabled:
                _next_a = "0" if _has_0 else ("2" if _has_2 else None)
                sf.text = sup.content + get_isbd_punctuation(
                    tag="353",
                    subfield_code="a",
                    next_subfield_code=_next_a,
                    enabled=True,
                )
            else:
                sf.text = sup.content
        if sup.authority_uri is not None:
            sf = etree.SubElement(df, f"{_MARC}subfield", code="0")
            if _isbd_enabled:
                _next_0 = "2" if _has_2 else None
                sf.text = sup.authority_uri + get_isbd_punctuation(
                    tag="353",
                    subfield_code="0",
                    next_subfield_code=_next_0,
                    enabled=True,
                )
            else:
                sf.text = sup.authority_uri
        if sup.source is not None:
            sf = etree.SubElement(df, f"{_MARC}subfield", code="2")
            if _isbd_enabled:
                sf.text = sup.source + get_isbd_punctuation(
                    tag="353",
                    subfield_code="2",
                    next_subfield_code=None,
                    enabled=True,
                )
            else:
                sf.text = sup.source


def _append_added_title_datafields(
    record: etree._Element, added_titles: list[_AddedTitleEmit]
) -> None:
    """Append 730 / 740 added-title datafields after the 7XX contributor block.

    Indicators and every subfield are taken verbatim from the parsed
    ``bffi:marcKey`` — preserves nonfiling-character ind1 plus $a/$g/$l/$n/$o/$p
    and any other code the source carried."""
    for added in added_titles:
        df = etree.SubElement(
            record, f"{_MARC}datafield", tag=added.tag, ind1=added.ind1, ind2=added.ind2
        )
        for code, value in added.subfields:
            sf = etree.SubElement(df, f"{_MARC}subfield", code=code)
            sf.text = value


def _build_marc_record(  # noqa: PLR0915 — structural aggregation;
    # statement and branch counts grow as additional MARC tag families
    # are added; each new family is one append call + an optional
    # presence check.
    *,
    bib_id: str,
    change_date: str | None,
    title_parts: _TitleParts | None,
    variant_titles: list[_VariantTitleEmit],
    uniform_main_entry: _AddedTitleEmit | None,
    responsibility: str | None,
    edition_statement: str | None,
    publications: list[_PublicationEmit],
    identifiers: list[_IdentifierEmit],
    language_codes: list[str],
    language_components: list[tuple[str, str]],
    temporal_coverage: list[str],
    physical: _PhysicalDescription | None,
    rda: _RdaDescriptors,
    classifications: list[_ClassificationEmit],
    contributors: list[_ContributorEmit],
    subjects: list[_SubjectEmit],
    acquisition_sources: list[_AcquisitionSourceEmit],
    supplementary_contents: list[_SupplementaryContentEmit],
    notes: list[_NoteEmit],
    table_of_contents: list[str],
    policies: _PolicyEmits,
    summaries: list[str],
    frequencies: list[_FrequencyEmit],
    playing_times: list[str],
    modes_of_issuance: list[str],
    intended_audiences: list[str],
    untraced_series: list[_UntracedSeriesEmit],
    traced_series: list[_AddedTitleEmit],
    added_titles: list[_AddedTitleEmit],
    linking_entries: list[_AddedTitleEmit],
    electronic_locators: list[str],
    leader_text: str,
    options: ConversionOptions | None = None,
) -> etree._Element:
    """Build one MARCXML ``<record>`` element with the v0+ field set."""
    record = etree.Element(f"{_MARC}record")
    leader = etree.SubElement(record, f"{_MARC}leader")
    leader.text = leader_text

    # Track occurrence numbers for alt-script 880 fields per tag
    alt_script_counter: dict[str, int] = {}

    cf001 = etree.SubElement(record, f"{_MARC}controlfield", tag="001")
    cf001.text = bib_id

    if change_date is not None:
        cf005 = etree.SubElement(record, f"{_MARC}controlfield", tag="005")
        cf005.text = change_date

    # 020 / 022 / 024 / 028 identifiers come before 041 / 245 / 300 in
    # MARC tag order. Indicators and the optional $b assigner come from
    # the per-emit fields populated by the scheme dispatcher.
    _append_identifier_datafields(record, identifiers)

    _append_classification_datafields(record, classifications)

    # 037 acquisition source — after 035 identifiers, before 041 language.
    _append_acquisition_source_datafields(record, acquisition_sources)

    if language_codes or language_components:
        # ind1=1 ("item is or includes a translation") whenever a language of
        # the original ($h) is present: all 26 source 041s carrying $h in the
        # fixture corpus use ind1=1. ind1=0 ("item is not a translation") when
        # language codes exist but no $h is present — the absence of $h is
        # evidence of non-translation for round-trip fidelity. Blank ind1 is
        # reserved for records with no language statement at all (emitted only
        # from 008, which the converter doesn't touch).
        translation = any(code == "h" for code, _ in language_components)
        df041 = etree.SubElement(
            record,
            f"{_MARC}datafield",
            tag="041",
            ind1="1" if translation else "0",
            ind2=" ",
        )
        for code in language_codes:
            sf_a = etree.SubElement(df041, f"{_MARC}subfield", code="a")
            sf_a.text = code
        # $h / $i / $j / … follow every $a, which is also their MARC order.
        for subfield_code, language_code in language_components:
            sf = etree.SubElement(df041, f"{_MARC}subfield", code=subfield_code)
            sf.text = language_code

    # 045 temporal coverage — simple literal emit.
    for value in temporal_coverage:
        df045 = etree.SubElement(record, f"{_MARC}datafield", tag="045", ind1=" ", ind2=" ")
        sf_a = etree.SubElement(df045, f"{_MARC}subfield", code="a")
        sf_a.text = value

    # Primary contributors (MARC 100/110/111) come before 130 in MARC
    # tag order.
    _append_contributor_datafields(
        record,
        (c for c in contributors if c.tag.startswith("1")),
        alt_script_counter,
        options=options,
    )

    # 130 uniform main entry — between 1XX contributors and 245.
    if uniform_main_entry is not None:
        _append_added_title_datafields(record, [uniform_main_entry])

    if title_parts is not None:
        _append_title_datafield(
            record, title_parts, responsibility, alt_script_counter, options=options
        )

    # 2XX variant titles immediately follow 245. Each emits at its own
    # tag (210 / 222 / 242 / 243 / 246 / 247) with indicators from
    # bffi:marcKey (Phase C of p-065) or the per-tag convention.
    for variant in variant_titles:
        # Pre-reserve occurrence for alt-script linkage
        main_occurrence: int | None = None
        if variant.alt_scripts and alt_script_counter is not None:
            main_occurrence = _reserve_occurrence(alt_script_counter, variant.tag)

        df = etree.SubElement(
            record,
            f"{_MARC}datafield",
            tag=variant.tag,
            ind1=variant.ind1,
            ind2=variant.ind2,
        )
        sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
        sf_a.text = variant.text
        # Add $6 linkage when alt-script is present
        if variant.alt_scripts and alt_script_counter is not None and main_occurrence is not None:
            sf_6 = etree.SubElement(df, f"{_MARC}subfield", code="6")
            sf_6.text = f"880-{main_occurrence:02d}"

        # Emit 880 fields for alt-script duplicates
        if variant.alt_scripts and alt_script_counter is not None:
            _append_alt_script_datafields(
                record=record,
                main_tag=variant.tag,
                main_ind1=variant.ind1,
                main_ind2=variant.ind2,
                main_label=variant.text,
                alt_scripts=variant.alt_scripts,
                alt_script_counter=alt_script_counter,
                main_occurrence=main_occurrence,
                options=options,
            )

    if edition_statement is not None:
        df250 = etree.SubElement(record, f"{_MARC}datafield", tag="250", ind1=" ", ind2=" ")
        sf_a = etree.SubElement(df250, f"{_MARC}subfield", code="a")
        sf_a.text = edition_statement

    for pub in publications:
        _append_publication_datafield(record, pub, alt_script_counter, options=options)

    if physical is not None:
        _append_physical_description_datafield(record, physical, options=options)

    # 306 playing time precedes the frequency block (MARC tag order).
    _append_simple_a_datafields(record, "306", tuple(playing_times), options=options)

    # 310 (current) and 321 (former) frequencies dispatch from the same
    # bffi:frequency walk; each Frequency block carries its target tag.
    for freq in frequencies:
        df = etree.SubElement(record, f"{_MARC}datafield", tag=freq.tag, ind1=" ", ind2=" ")
        sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
        sf_a.text = freq.text

    # 334 mode of issuance — single $a, blank indicators.
    _append_simple_a_datafields(record, "334", tuple(modes_of_issuance), options=options)

    # 336/337/338 RDA descriptors. One datafield per code (multiple values
    # on a single predicate produce repeated datafields per MARC convention).
    _append_rda_datafields(record, "336", rda.content, options=options)
    _append_rda_datafields(record, "337", rda.media, options=options)
    _append_rda_datafields(record, "338", rda.carrier, options=options)

    # 353 supplementary content — after RDA descriptors, before notes.
    _append_supplementary_content_datafields(record, supplementary_contents, options=options)

    # 490 untraced series statements (after RDA, before notes). ISBD
    # trailing " ;" added on $a when $v volume number follows.
    for series in untraced_series:
        # Pre-reserve occurrence for alt-script linkage
        series_occurrence: int | None = None
        if series.alt_scripts and alt_script_counter is not None:
            series_occurrence = _reserve_occurrence(alt_script_counter, "490")

        df = etree.SubElement(record, f"{_MARC}datafield", tag="490", ind1="0", ind2=" ")
        _isbd_enabled = options.apply_isbd_punctuation if options else False
        _has_v = series.volume is not None
        sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
        sf_a.text = series.title + get_isbd_punctuation(
            tag="490",
            subfield_code="a",
            next_subfield_code=("v" if _has_v else None),
            enabled=_isbd_enabled,
        )
        if series.volume is not None:
            sf_v = etree.SubElement(df, f"{_MARC}subfield", code="v")
            sf_v.text = series.volume
        # Add $6 linkage when alt-script is present
        if series.alt_scripts and alt_script_counter is not None and series_occurrence is not None:
            sf_6 = etree.SubElement(df, f"{_MARC}subfield", code="6")
            sf_6.text = f"880-{series_occurrence:02d}"

        # Emit 880 fields for alt-script duplicates
        if series.alt_scripts and alt_script_counter is not None:
            _append_alt_script_datafields(
                record=record,
                main_tag="490",
                main_ind1="0",
                main_ind2=" ",
                main_label=series.title,
                alt_scripts=series.alt_scripts,
                alt_script_counter=alt_script_counter,
                main_occurrence=series_occurrence,
                options=options,
            )

    _append_note_block(
        record,
        notes=notes,
        table_of_contents=table_of_contents,
        policies=policies,
        summaries=summaries,
        intended_audiences=intended_audiences,
        alt_script_counter=alt_script_counter,
        options=options,
    )

    # 6XX subjects come after the bibliographic-description block.
    _append_subject_datafields(record, subjects, alt_script_counter, options=options)

    # Added contributors (MARC 700/710/711) come after 6XX subjects.
    _append_contributor_datafields(
        record,
        (c for c in contributors if c.tag.startswith("7")),
        alt_script_counter,
        options=options,
    )

    _append_added_title_datafields(record, added_titles)

    # 76X-78X linking entries.
    _append_added_title_datafields(record, linking_entries)

    # 830 traced series.
    _append_added_title_datafields(record, traced_series)

    # 856 electronic locators come last (per common MARC ordering, after
    # all subject / added-entry / series blocks).
    for url in electronic_locators:
        df = etree.SubElement(record, f"{_MARC}datafield", tag="856", ind1="4", ind2="0")
        sf_u = etree.SubElement(df, f"{_MARC}subfield", code="u")
        sf_u.text = url

    # Deduplicate exact duplicate datafields (same tag, ind1, ind2,
    # subfield content). marc2bibframe2 can produce duplicate
    # bf:relation / bf:adminMetadata blocks that become byte-identical
    # MARC datafields (e.g. two 490 "Kungsleden" from one source 490).
    _deduplicate_datafields(record)

    return record
