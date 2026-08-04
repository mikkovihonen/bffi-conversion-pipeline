"""Cross-check verdict tests using synthetic ``ParseReport`` +
``MarcEmitMeta`` inputs. No XSLT parsing, no live registry."""

from __future__ import annotations

from bffi_pipeline.diagnostic.xslt_coverage.cross_check import cross_check
from bffi_pipeline.diagnostic.xslt_coverage.model import (
    OutputTerm,
    ParseReport,
    TemplateFact,
)
from bffi_pipeline.stages.bffi_to_marc.runner import MarcEmitMeta


def _fact(tag: str, subfields: set[str], **overrides) -> TemplateFact:
    return TemplateFact(
        source_file="synthetic.xsl",
        start_line=0,
        tag=tag,
        mode=overrides.get("mode", "work"),
        is_880_alias_for=None,
        indicator_tests=overrides.get("indicator_tests", frozenset()),
        indicator_projected=False,
        subfield_codes=frozenset(subfields),
        controlfield_position_reads=frozenset(),
        leader_position_reads=frozenset(),
        output_terms=frozenset({OutputTerm(qname="bf:Foo", kind="class", origin="literal")}),
        dynamic_element_constructors=frozenset(),
    )


def _emit(tag: str, subfield_codes: list[str], **overrides) -> MarcEmitMeta:
    return MarcEmitMeta(
        tag=tag,
        indicators=overrides.get("indicators", ()),
        subfields=tuple((code, "") for code in subfield_codes),
        source=overrides.get("source", "synthetic"),
    )


def _report(facts: list[TemplateFact]) -> ParseReport:
    return ParseReport(
        templates=tuple(facts),
        parsed_modules=("synthetic.xsl",),
        xslt_commit_sha=None,
    )


def test_round_trippable_when_subfields_match() -> None:
    report = _report([_fact("245", subfields={"a", "b"})])
    registry = [_emit("245", ["a", "b"])]
    result = cross_check(report, registry=registry)
    by_tag = {row.tag: row for row in result.rows}
    assert by_tag["245"].verdict == "round_trippable"
    assert by_tag["245"].subfields_both == frozenset({"a", "b"})
    assert by_tag["245"].subfields_forward_only == frozenset()
    assert by_tag["245"].subfields_reverse_only == frozenset()


def test_forward_only_when_reverse_does_not_emit_tag() -> None:
    report = _report([_fact("037", subfields={"a", "b"})])
    result = cross_check(report, registry=[])
    by_tag = {row.tag: row for row in result.rows}
    assert by_tag["037"].verdict == "forward_only"
    assert by_tag["037"].emitted_by_reverse is False
    assert by_tag["037"].subfields_forward_only == frozenset({"a", "b"})


def test_reverse_only_when_forward_does_not_handle_tag() -> None:
    report = _report([])
    registry = [_emit("490", ["a", "v"])]
    result = cross_check(report, registry=registry)
    by_tag = {row.tag: row for row in result.rows}
    assert by_tag["490"].verdict == "reverse_only"
    assert by_tag["490"].handled_by_marc2bibframe2 is False
    assert by_tag["490"].subfields_reverse_only == frozenset({"a", "v"})


def test_asymmetric_when_subfield_sets_differ() -> None:
    report = _report([_fact("245", subfields={"a", "b", "n"})])
    registry = [_emit("245", ["a", "b", "c"])]
    result = cross_check(report, registry=registry)
    by_tag = {row.tag: row for row in result.rows}
    row = by_tag["245"]
    assert row.verdict == "asymmetric"
    assert row.subfields_forward_only == frozenset({"n"})
    assert row.subfields_reverse_only == frozenset({"c"})
    assert row.subfields_both == frozenset({"a", "b"})


def test_field_kind_inferred_when_only_reverse_side_present() -> None:
    """``leader`` and 00X tags are still classified correctly when the
    forward side has no coverage row to look up."""
    report = _report([])
    registry = [
        _emit("leader", []),
        _emit("005", []),
        _emit("490", ["a"]),
    ]
    result = cross_check(report, registry=registry)
    by_tag = {row.tag: row for row in result.rows}
    assert by_tag["leader"].field_kind == "leader"
    assert by_tag["005"].field_kind == "controlfield"
    assert by_tag["490"].field_kind == "datafield"


def test_tally_aggregates_all_verdicts() -> None:
    report = _report(
        [
            _fact("245", subfields={"a"}),
            _fact("100", subfields={"a"}),
        ]
    )
    registry = [
        _emit("245", ["a"]),  # round_trippable
        _emit("490", ["a"]),  # reverse_only
        # 100 in forward only — no reverse entry
    ]
    result = cross_check(report, registry=registry)
    assert result.tally["round_trippable"] == 1
    assert result.tally["reverse_only"] == 1
    assert result.tally["forward_only"] == 1
    assert result.tally["asymmetric"] == 0
