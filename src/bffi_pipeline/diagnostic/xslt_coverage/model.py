"""Value types for the marc2bibframe2 coverage analyzer.

All ``dataclass(frozen=True)``. Per-template facts (:class:`TemplateFact`)
are the raw parser output; per-tag rows (:class:`CoverageRow`,
:class:`CrossCheckRow`) are what the renderer consumes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: ``ind1`` or ``ind2`` — restricted alphabet for the indicator slot.
IndicatorSlot = Literal["ind1", "ind2"]

#: Categorisation of an emitted BIBFRAME term. ``class`` / ``predicate``
#: by first-letter case of the local name (BIBFRAME convention);
#: ``unknown`` for ``xsl:element name="{$var}"`` constructors whose
#: target QName can't be resolved statically.
OutputKind = Literal["class", "predicate", "unknown"]

#: How the output term was constructed in XSLT. Literal element
#: construction (``<bf:title>``) vs. dynamic construction via
#: ``<xsl:element name="..."/>``.
OutputOrigin = Literal["literal", "xsl:element"]

#: Whether the MARC field is a controlfield (00X), the leader, or a
#: variable-length datafield.
FieldKind = Literal["controlfield", "leader", "datafield"]

#: The cross-check verdict for one MARC tag.
Verdict = Literal["round_trippable", "forward_only", "reverse_only", "asymmetric"]


@dataclass(frozen=True)
class OutputTerm:
    """One BIBFRAME class or predicate emitted by an XSLT template."""

    qname: str  # e.g. ``"bf:Work"``, ``"bf:title"``, ``"<dynamic:$vTitleClass>"``
    kind: OutputKind
    origin: OutputOrigin


@dataclass(frozen=True)
class TemplateFact:
    """One parsed ``xsl:template`` matching a MARC field.

    Multiple ``TemplateFact``s can share a ``tag`` — different ``mode``s
    are the rule rather than the exception in marc2bibframe2 (same field
    drives the Work, Instance, and Item modes from one or more templates).
    """

    source_file: str  # basename, not full path
    start_line: int
    tag: str  # ``"245"``, ``"008"``, ``"leader"``
    mode: str | None
    is_880_alias_for: str | None  # the linked tag if this template is the 880 half
    indicator_tests: frozenset[tuple[IndicatorSlot, str]]
    indicator_projected: bool  # ``<xsl:value-of select="@ind1"/>`` etc.
    subfield_codes: frozenset[str]
    controlfield_position_reads: frozenset[tuple[int, int]]  # (start, length), XSLT 1-based
    leader_position_reads: frozenset[tuple[int, int]]  # (start, length), XSLT 1-based
    output_terms: frozenset[OutputTerm]
    dynamic_element_constructors: frozenset[str]  # variable expressions, e.g. ``"$vTitleClass"``


@dataclass(frozen=True)
class CoverageRow:
    """Per-tag merged row rendered into the coverage table."""

    tag: str
    field_kind: FieldKind
    modes: tuple[str, ...]
    indicator_tests: frozenset[tuple[IndicatorSlot, str]]
    indicator_projected: bool
    subfield_codes: frozenset[str]
    position_reads: frozenset[tuple[int, int]]
    output_terms: tuple[OutputTerm, ...]
    source_modules: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class ParseReport:
    """The complete static-parse result for one XSLT entry point."""

    templates: tuple[TemplateFact, ...]
    parsed_modules: tuple[str, ...]
    xslt_commit_sha: str | None


@dataclass(frozen=True)
class CrossCheckRow:
    """One row in the round-trip cross-check table.

    Compares the forward direction (this analyzer's ``CoverageRow``)
    against the reverse direction (a ``MarcEmitMeta`` from
    ``MARC_EMIT_REGISTRY``).
    """

    tag: str
    field_kind: FieldKind
    handled_by_marc2bibframe2: bool
    forward_modes: tuple[str, ...]
    emitted_by_reverse: bool
    reverse_sources: tuple[str, ...]
    subfields_forward_only: frozenset[str]
    subfields_reverse_only: frozenset[str]
    subfields_both: frozenset[str]
    indicators_forward_only: frozenset[tuple[IndicatorSlot, str]]
    indicators_reverse_only: frozenset[tuple[IndicatorSlot, str]]
    verdict: Verdict
    partial_forward: bool  # forward template uses dynamic xsl:element — comparison may be off


@dataclass(frozen=True)
class CrossCheckReport:
    """Aggregate output of the cross-check."""

    rows: tuple[CrossCheckRow, ...]

    @property
    def tally(self) -> dict[Verdict, int]:
        out: dict[Verdict, int] = {
            "round_trippable": 0,
            "forward_only": 0,
            "reverse_only": 0,
            "asymmetric": 0,
        }
        for row in self.rows:
            out[row.verdict] += 1
        return out
