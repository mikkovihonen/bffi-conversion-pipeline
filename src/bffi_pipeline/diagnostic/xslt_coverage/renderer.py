"""Markdown rendering for the marc2bibframe2 coverage doc.

Four auto-blocks: the per-tag coverage table, the dynamic-constructor
appendix, the round-trip cross-check table, and the generator metadata
footer. Pure functions — no filesystem writes happen here.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Final

from bffi_pipeline.diagnostic.xslt_coverage.model import (
    CoverageRow,
    CrossCheckReport,
    CrossCheckRow,
    FieldKind,
    IndicatorSlot,
    OutputTerm,
    ParseReport,
    TemplateFact,
    Verdict,
)

_CONTROLFIELD_TAGS: Final[frozenset[str]] = frozenset(
    {"001", "003", "005", "006", "007", "008", "009"}
)

_VERDICT_ORDER: Final[tuple[Verdict, ...]] = (
    "asymmetric",
    "forward_only",
    "reverse_only",
    "round_trippable",
)

_VERDICT_LABEL: Final[dict[Verdict, str]] = {
    "round_trippable": "✓ round-trippable",
    "forward_only": "→ forward only",
    "reverse_only": "← reverse only",
    "asymmetric": "≠ asymmetric",
}


# --- template merge ---------------------------------------------------------


def merge_templates_to_rows(report: ParseReport) -> list[CoverageRow]:
    """Collapse a flat list of :class:`TemplateFact`s into per-tag rows.

    880 alias facts (``is_880_alias_for != None``) are folded into the
    linked tag's row as a note rather than producing their own row;
    bare 880 facts (with no dispatch linkage) produce a stand-alone row.
    """
    primary: dict[str, list[TemplateFact]] = defaultdict(list)
    alias_links: dict[str, set[str]] = defaultdict(set)  # linked_tag -> {sources}
    for fact in report.templates:
        if fact.is_880_alias_for is None:
            primary[fact.tag].append(fact)
        else:
            alias_links[fact.is_880_alias_for].add(fact.source_file)

    rows: list[CoverageRow] = []
    for tag, facts in primary.items():
        row = _merge_facts(tag, facts, alias_sources=alias_links.get(tag, set()))
        rows.append(row)
    rows.sort(key=_row_sort_key)
    return rows


def _merge_facts(
    tag: str,
    facts: Iterable[TemplateFact],
    *,
    alias_sources: set[str],
) -> CoverageRow:
    facts_list = list(facts)
    field_kind = _classify_field_kind(tag)

    modes = sorted({fact.mode for fact in facts_list if fact.mode is not None})
    indicator_tests: set[tuple[IndicatorSlot, str]] = set()
    indicator_projected = False
    subfield_codes: set[str] = set()
    position_reads: set[tuple[int, int]] = set()
    output_terms: set[OutputTerm] = set()
    source_modules: set[str] = set()
    dynamic_vars: set[str] = set()

    for fact in facts_list:
        for slot, value in fact.indicator_tests:
            indicator_tests.add((slot, value))
        indicator_projected = indicator_projected or fact.indicator_projected
        subfield_codes |= fact.subfield_codes
        if field_kind == "leader":
            position_reads |= fact.leader_position_reads
        elif field_kind == "controlfield":
            position_reads |= fact.controlfield_position_reads
        output_terms |= fact.output_terms
        source_modules.add(fact.source_file)
        dynamic_vars |= fact.dynamic_element_constructors

    notes: list[str] = []
    if alias_sources:
        notes.append("also handled via 880 `$6` dispatch")
    if dynamic_vars:
        var_list = ", ".join(f"`{v}`" for v in sorted(dynamic_vars))
        notes.append(f"contains dynamic `xsl:element` constructors ({var_list}) — partial coverage")
    if field_kind == "datafield" and not indicator_tests and not indicator_projected:
        notes.append("no indicator literals tested — fires for any indicator value")
    if indicator_projected:
        notes.append("indicator values projected without literal comparison")

    return CoverageRow(
        tag=tag,
        field_kind=field_kind,
        modes=tuple(modes),
        indicator_tests=frozenset(indicator_tests),
        indicator_projected=indicator_projected,
        subfield_codes=frozenset(subfield_codes),
        position_reads=frozenset(position_reads),
        output_terms=tuple(sorted(output_terms, key=lambda t: t.qname)),
        source_modules=tuple(sorted(source_modules)),
        notes=tuple(notes),
    )


# --- coverage table ---------------------------------------------------------


def render_coverage_table(rows: Iterable[CoverageRow]) -> str:
    header = (
        "| MARC | Kind | Modes | Indicators tested | Subfields read "
        "| Position reads | BIBFRAME emitted | Source module(s) | Notes |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
    )
    body_lines = []
    for row in rows:
        body_lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row.tag}`",
                    row.field_kind,
                    _format_modes(row.modes),
                    _format_indicators(row.indicator_tests, row.indicator_projected),
                    _format_subfields(row.subfield_codes),
                    _format_positions(row.tag, row.field_kind, row.position_reads),
                    _format_output_terms(row.output_terms),
                    _format_modules(row.source_modules),
                    _format_notes(row.notes),
                ]
            )
            + " |\n"
        )
    return header + "".join(body_lines)


# --- dynamic appendix -------------------------------------------------------


def render_dynamic_appendix(report: ParseReport) -> str:
    rows: list[tuple[str, int, str, str, str]] = []
    for fact in report.templates:
        if not fact.dynamic_element_constructors:
            continue
        if fact.is_880_alias_for is not None:
            continue  # the linked tag's fact covers the same template body
        for var in sorted(fact.dynamic_element_constructors):
            rows.append(
                (
                    fact.source_file,
                    fact.start_line,
                    fact.tag,
                    var,
                    fact.mode or "—",
                )
            )
    rows.sort()
    header = (
        "| Source module | Line | MARC tag | Variable / expression | Template mode |\n"
        "|---|---|---|---|---|\n"
    )
    if not rows:
        return header + "| _(none)_ | | | | |\n"
    body = "".join(
        f"| `{src}` | {line} | `{tag}` | `{var}` | {mode} |\n" for src, line, tag, var, mode in rows
    )
    return header + body


# --- round-trip cross-check -------------------------------------------------


def render_roundtrip_table(cross_check_report: CrossCheckReport) -> str:
    header = (
        "| MARC | Verdict | Forward modes | Reverse emits "
        "| Subfields forward-only | Subfields reverse-only | Subfields shared "
        "| Indicator delta | Notes |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
    )
    body_lines: list[str] = []
    grouped: dict[Verdict, list[CrossCheckRow]] = defaultdict(list)
    for row in cross_check_report.rows:
        grouped[row.verdict].append(row)

    for verdict in _VERDICT_ORDER:
        bucket = grouped.get(verdict, [])
        if not bucket:
            continue
        bucket.sort(key=_crosscheck_sort_key)
        for row in bucket:
            body_lines.append(_render_crosscheck_row(row))

    if not body_lines:
        return header + "| _(no tags handled by either direction)_ | | | | | | | | |\n"
    return header + "".join(body_lines)


def _render_crosscheck_row(row: CrossCheckRow) -> str:
    notes: list[str] = []
    if row.partial_forward:
        notes.append("forward template uses dynamic `xsl:element` — comparison partial")

    return (
        "| "
        + " | ".join(
            [
                f"`{row.tag}`",
                _VERDICT_LABEL[row.verdict],
                _format_modes(row.forward_modes),
                "✓" if row.emitted_by_reverse else "—",
                _format_subfields(row.subfields_forward_only),
                _format_subfields(row.subfields_reverse_only),
                _format_subfields(row.subfields_both),
                _format_indicator_delta(row.indicators_forward_only, row.indicators_reverse_only),
                _format_notes(tuple(notes)),
            ]
        )
        + " |\n"
    )


# --- metadata footer --------------------------------------------------------


def render_metadata_block(
    report: ParseReport,
    cross_check_report: CrossCheckReport,
) -> str:
    sha = report.xslt_commit_sha or "_(unknown — git unavailable)_"
    dynamic_sites = sum(
        len(fact.dynamic_element_constructors)
        for fact in report.templates
        if fact.is_880_alias_for is None
    )
    rows = list(merge_templates_to_rows(report))
    tally = cross_check_report.tally
    tally_str = (
        f"{tally['round_trippable']} round-trippable, "
        f"{tally['asymmetric']} asymmetric, "
        f"{tally['forward_only']} forward-only, "
        f"{tally['reverse_only']} reverse-only"
    )
    return (
        f"_Generated from `third_party/marc2bibframe2/xsl/marc2bibframe2.xsl` "
        f"at commit `{sha}`. Parsed {len(report.parsed_modules)} modules, "
        f"{len(report.templates)} templates, {len(rows)} unique MARC tags. "
        f"{dynamic_sites} dynamic `xsl:element` constructor sites. "
        f"Round-trip tallies: {tally_str}._\n"
    )


# --- formatting helpers ----------------------------------------------------


def _classify_field_kind(tag: str) -> FieldKind:
    if tag == "leader":
        return "leader"
    if tag in _CONTROLFIELD_TAGS:
        return "controlfield"
    return "datafield"


def _format_modes(modes: tuple[str, ...]) -> str:
    if not modes:
        return "—"
    return ", ".join(f"`{m}`" for m in modes)


def _format_indicators(
    indicator_tests: frozenset[tuple[IndicatorSlot, str]],
    projected: bool,
) -> str:
    if not indicator_tests:
        if projected:
            return "_(projected, no literal tests)_"
        return "—"
    by_slot: dict[str, list[str]] = defaultdict(list)
    for slot, value in indicator_tests:
        rendered = "#" if value == " " else value
        by_slot[slot].append(rendered)
    parts: list[str] = []
    for slot in ("ind1", "ind2"):
        if slot in by_slot:
            values = sorted(by_slot[slot])
            parts.append(f"{slot}=`{'`/`'.join(values)}`")
    suffix = " (+ projected)" if projected else ""
    return ", ".join(parts) + suffix


def _format_subfields(codes: frozenset[str]) -> str:
    if not codes:
        return "—"
    return " ".join(f"`${c}`" for c in sorted(codes))


def _format_positions(
    tag: str,
    field_kind: FieldKind,
    positions: frozenset[tuple[int, int]],
) -> str:
    """Render XSLT-1-based (start, length) as MARC-0-based ``tag/start-end``."""
    if not positions:
        return "—"
    if field_kind == "leader":
        prefix = "LDR"
    elif field_kind == "controlfield":
        prefix = tag
    else:
        prefix = tag
    rendered: list[str] = []
    for start, length in sorted(positions):
        marc_start = start - 1
        marc_end = start + length - 2
        if length == 1:
            rendered.append(f"`{prefix}/{marc_start:02d}`")
        else:
            rendered.append(f"`{prefix}/{marc_start:02d}-{marc_end:02d}`")
    return " ".join(rendered)


def _format_output_terms(terms: tuple[OutputTerm, ...]) -> str:
    if not terms:
        return "—"
    rendered: list[str] = []
    for term in terms:
        if term.kind == "unknown":
            rendered.append(f"`{term.qname}`")
        else:
            rendered.append(f"`{term.qname}`")
    return "<br>".join(rendered)


def _format_modules(modules: tuple[str, ...]) -> str:
    if not modules:
        return "—"
    return "<br>".join(f"`{m}`" for m in modules)


def _format_notes(notes: tuple[str, ...]) -> str:
    if not notes:
        return "—"
    return "<br>".join(notes)


def _format_reverse_sources(sources: tuple[str, ...]) -> str:
    if not sources:
        return "—"
    return "<br>".join(f"`{s}`" for s in sources)


def _format_indicator_delta(
    forward_only: frozenset[tuple[IndicatorSlot, str]],
    reverse_only: frozenset[tuple[IndicatorSlot, str]],
) -> str:
    if not forward_only and not reverse_only:
        return "—"
    parts: list[str] = []
    if forward_only:
        rendered = ", ".join(
            f"{slot}=`{'#' if value == ' ' else value}`" for slot, value in sorted(forward_only)
        )
        parts.append(f"forward only: {rendered}")
    if reverse_only:
        rendered = ", ".join(
            f"{slot}=`{'#' if value == ' ' else value}`" for slot, value in sorted(reverse_only)
        )
        parts.append(f"reverse only: {rendered}")
    return "<br>".join(parts)


def _row_sort_key(row: CoverageRow) -> tuple[int, str]:
    if row.field_kind == "controlfield":
        return (0, row.tag)
    if row.field_kind == "leader":
        return (1, "")
    return (2, row.tag)


def _crosscheck_sort_key(row: CrossCheckRow) -> tuple[int, str]:
    if row.field_kind == "controlfield":
        return (0, row.tag)
    if row.field_kind == "leader":
        return (1, "")
    return (2, row.tag)
