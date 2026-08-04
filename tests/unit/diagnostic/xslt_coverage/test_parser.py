"""Unit tests for the XSLT parser, against synthetic fixtures."""

from __future__ import annotations

from pathlib import Path

from bffi_pipeline.diagnostic.xslt_coverage.parser import parse_xslt_corpus

FIXTURES = Path(__file__).parent / "fixtures"


def _by_tag_and_mode(facts):
    return {(f.tag, f.mode): f for f in facts}


def test_single_tag_with_indicators_collects_branches_and_outputs() -> None:
    report = parse_xslt_corpus(FIXTURES / "single_tag_with_indicators.xsl")
    facts = _by_tag_and_mode(report.templates)
    fact = facts[("100", "work")]
    assert fact.tag == "100"
    assert fact.mode == "work"
    assert fact.is_880_alias_for is None
    assert fact.indicator_tests == frozenset({("ind1", "0"), ("ind1", "1")})
    assert fact.indicator_projected is True
    assert "a" in fact.subfield_codes
    output_qnames = {term.qname for term in fact.output_terms}
    assert "bf:Person" in output_qnames
    assert "bf:Family" in output_qnames
    assert "bf:contribution" in output_qnames
    assert "bflc:Contribution" in output_qnames
    # Classify by case: capitalized -> class, lowercase -> predicate.
    kinds = {term.qname: term.kind for term in fact.output_terms}
    assert kinds["bf:Person"] == "class"
    assert kinds["bf:contribution"] == "predicate"


def test_multi_mode_dispatch_emits_one_fact_per_mode() -> None:
    report = parse_xslt_corpus(FIXTURES / "multi_mode_dispatch.xsl")
    pairs = {(f.tag, f.mode) for f in report.templates}
    assert pairs == {("245", "instance")}


def test_controlfield_position_reads_use_self_axis() -> None:
    report = parse_xslt_corpus(FIXTURES / "controlfield_position_reads.xsl")
    fact = next(iter(report.templates))
    assert fact.tag == "008"
    assert fact.controlfield_position_reads == frozenset({(36, 3), (16, 1)})
    assert fact.leader_position_reads == frozenset()
    # Controlfields don't have indicators or subfields — both must be empty.
    assert fact.indicator_tests == frozenset()
    assert fact.subfield_codes == frozenset()


def test_leader_template_collects_position_reads_not_subfields() -> None:
    report = parse_xslt_corpus(FIXTURES / "leader_template.xsl")
    fact = next(iter(report.templates))
    assert fact.tag == "leader"
    assert fact.leader_position_reads == frozenset({(7, 1), (8, 1)})
    assert fact.subfield_codes == frozenset()


def test_eight_eighty_dispatch_emits_paired_facts_with_alias_link() -> None:
    report = parse_xslt_corpus(FIXTURES / "eight_eighty_dispatch.xsl")
    facts = {(f.tag, f.is_880_alias_for) for f in report.templates}
    assert facts == {("245", None), ("880", "245")}
    # Both facts share the same body — subfields harvested from the
    # OR-chained predicate should appear in both.
    for fact in report.templates:
        assert {"a", "b", "n", "p", "6"} <= fact.subfield_codes


def test_dynamic_element_constructor_is_flagged_and_listed() -> None:
    report = parse_xslt_corpus(FIXTURES / "dynamic_element.xsl")
    fact = next(iter(report.templates))
    assert "$vClass" in fact.dynamic_element_constructors
    output_qnames = {term.qname for term in fact.output_terms}
    assert any(q.startswith("<dynamic:") for q in output_qnames)
    # Literal element from the same template should still be recorded.
    assert "bf:literalThing" in output_qnames


def test_include_graph_walks_relative_hrefs() -> None:
    report = parse_xslt_corpus(FIXTURES / "include_root.xsl")
    assert "include_root.xsl" in report.parsed_modules
    assert "include_child.xsl" in report.parsed_modules
    pairs = {(f.tag, f.source_file) for f in report.templates}
    assert ("100", "include_root.xsl") in pairs
    assert ("245", "include_child.xsl") in pairs
