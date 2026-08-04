"""Round-trip cross-check between the forward XSLT and the reverse converter.

Compares :class:`~.model.ParseReport` (what marc2bibframe2 reads from
MARC) against the BFFI → MARC reverse converter's
``MARC_EMIT_REGISTRY`` (what the reverse path emits as MARC). Per-tag,
classifies the relationship as round-trippable, forward-only,
reverse-only, or asymmetric, and reports the subfield / indicator
deltas that drive the verdict.

This is **input-side** (MARC tag + subfield + indicators), not
output-side: it does not compare the BIBFRAME terms emitted by the
forward path against the BFFI terms read by the reverse path — that's
the BIBFRAME ↔ BFFI mapping doc's job.
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
    ParseReport,
    Verdict,
)
from bffi_pipeline.diagnostic.xslt_coverage.renderer import merge_templates_to_rows
from bffi_pipeline.stages.bffi_to_marc.runner import MARC_EMIT_REGISTRY, MarcEmitMeta

_CONTROLFIELD_TAGS: Final[frozenset[str]] = frozenset(
    {"001", "003", "005", "006", "007", "008", "009"}
)
_INDICATOR_SLOTS: Final[int] = 2


def cross_check(
    report: ParseReport,
    registry: Iterable[MarcEmitMeta] | None = None,
) -> CrossCheckReport:
    """Compare a :class:`ParseReport` against a reverse-converter registry."""
    if registry is None:
        registry = MARC_EMIT_REGISTRY

    forward_rows: dict[str, CoverageRow] = {row.tag: row for row in merge_templates_to_rows(report)}
    reverse_by_tag = _group_registry(registry)

    rows: list[CrossCheckRow] = []
    for tag in sorted(set(forward_rows) | set(reverse_by_tag), key=_sort_key):
        fwd = forward_rows.get(tag)
        rev_entries = reverse_by_tag.get(tag, ())
        rows.append(_compute_row(tag, fwd, rev_entries))
    return CrossCheckReport(rows=tuple(rows))


# --- helpers ----------------------------------------------------------------


def _group_registry(
    registry: Iterable[MarcEmitMeta],
) -> dict[str, tuple[MarcEmitMeta, ...]]:
    grouped: dict[str, list[MarcEmitMeta]] = defaultdict(list)
    for entry in registry:
        grouped[entry.tag].append(entry)
    return {tag: tuple(entries) for tag, entries in grouped.items()}


def _compute_row(
    tag: str,
    forward: CoverageRow | None,
    reverse: tuple[MarcEmitMeta, ...],
) -> CrossCheckRow:
    handled_forward = forward is not None
    emitted_reverse = bool(reverse)

    field_kind = _classify_field_kind(tag, forward)

    fwd_subfields: frozenset[str] = forward.subfield_codes if forward else frozenset()
    rev_subfields: frozenset[str] = frozenset(
        code for entry in reverse for code, _desc in entry.subfields
    )

    fwd_indicators = forward.indicator_tests if forward else frozenset()
    rev_indicators = _reverse_indicator_set(reverse)

    forward_only_inds = _indicator_delta_forward(fwd_indicators, rev_indicators)
    reverse_only_inds = _indicator_delta_reverse(fwd_indicators, rev_indicators)

    forward_only_subs = fwd_subfields - rev_subfields
    reverse_only_subs = rev_subfields - fwd_subfields
    both_subs = fwd_subfields & rev_subfields

    verdict = _verdict(
        handled_forward=handled_forward,
        emitted_reverse=emitted_reverse,
        forward_only_subs=forward_only_subs,
        reverse_only_subs=reverse_only_subs,
        forward_only_inds=forward_only_inds,
        reverse_only_inds=reverse_only_inds,
    )

    partial_forward = bool(forward) and any(
        "dynamic" in note for note in (forward.notes if forward else ())
    )

    return CrossCheckRow(
        tag=tag,
        field_kind=field_kind,
        handled_by_marc2bibframe2=handled_forward,
        forward_modes=forward.modes if forward else (),
        emitted_by_reverse=emitted_reverse,
        reverse_sources=tuple(entry.source for entry in reverse),
        subfields_forward_only=forward_only_subs,
        subfields_reverse_only=reverse_only_subs,
        subfields_both=both_subs,
        indicators_forward_only=forward_only_inds,
        indicators_reverse_only=reverse_only_inds,
        verdict=verdict,
        partial_forward=partial_forward,
    )


def _reverse_indicator_set(
    reverse: tuple[MarcEmitMeta, ...],
) -> frozenset[tuple[IndicatorSlot, str]]:
    """Treat each ``MarcEmitMeta.indicators`` as a two-slot pair.

    The reverse converter stores indicators as ``(ind1_value, ind2_value)``
    — a single canonical pair per emit site. We project each non-empty
    pair into ``(slot, value)`` tuples so it lines up with the forward
    side's branch-test set. Empty tuples (control fields / leader) yield
    no entries.
    """
    out: set[tuple[IndicatorSlot, str]] = set()
    for entry in reverse:
        if not entry.indicators or len(entry.indicators) != _INDICATOR_SLOTS:
            continue
        ind1, ind2 = entry.indicators
        out.add(("ind1", ind1))
        out.add(("ind2", ind2))
    return frozenset(out)


def _indicator_delta_forward(
    forward: frozenset[tuple[IndicatorSlot, str]],
    reverse: frozenset[tuple[IndicatorSlot, str]],
) -> frozenset[tuple[IndicatorSlot, str]]:
    """Indicator values the forward XSLT branches on but reverse never emits."""
    if not forward or not reverse:
        return frozenset()
    return forward - reverse


def _indicator_delta_reverse(
    forward: frozenset[tuple[IndicatorSlot, str]],
    reverse: frozenset[tuple[IndicatorSlot, str]],
) -> frozenset[tuple[IndicatorSlot, str]]:
    """Indicator values the reverse converter emits that the forward XSLT
    has no literal branch for. Empty when the forward template doesn't
    branch indicators at all (it accepts any value)."""
    if not reverse:
        return frozenset()
    if not forward:
        return frozenset()
    return reverse - forward


def _verdict(
    *,
    handled_forward: bool,
    emitted_reverse: bool,
    forward_only_subs: frozenset[str],
    reverse_only_subs: frozenset[str],
    forward_only_inds: frozenset[tuple[IndicatorSlot, str]],
    reverse_only_inds: frozenset[tuple[IndicatorSlot, str]],
) -> Verdict:
    if handled_forward and not emitted_reverse:
        return "forward_only"
    if emitted_reverse and not handled_forward:
        return "reverse_only"
    if forward_only_subs or reverse_only_subs or forward_only_inds or reverse_only_inds:
        return "asymmetric"
    return "round_trippable"


def _classify_field_kind(tag: str, forward: CoverageRow | None) -> FieldKind:
    if forward is not None:
        return forward.field_kind
    if tag == "leader":
        return "leader"
    if tag in _CONTROLFIELD_TAGS:
        return "controlfield"
    return "datafield"


def _sort_key(tag: str) -> tuple[int, str]:
    if tag in _CONTROLFIELD_TAGS:
        return (0, tag)
    if tag == "leader":
        return (1, "")
    return (2, tag)
