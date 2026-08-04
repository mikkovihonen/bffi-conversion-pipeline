"""Tests for the pure-function markdown renderer."""

from __future__ import annotations

from pathlib import Path

from bffi_pipeline.diagnostic.xslt_coverage.model import (
    OutputTerm,
    ParseReport,
    TemplateFact,
)
from bffi_pipeline.diagnostic.xslt_coverage.parser import parse_xslt_corpus
from bffi_pipeline.diagnostic.xslt_coverage.renderer import (
    merge_templates_to_rows,
    render_coverage_table,
    render_dynamic_appendix,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _make_template(
    tag: str,
    *,
    mode: str | None = "work",
    is_880_alias_for: str | None = None,
    indicator_tests: frozenset[tuple[str, str]] = frozenset(),
    subfields: frozenset[str] = frozenset(),
    controlfield_positions: frozenset[tuple[int, int]] = frozenset(),
    leader_positions: frozenset[tuple[int, int]] = frozenset(),
    dynamic_constructors: frozenset[str] = frozenset(),
    output_terms: frozenset[OutputTerm] = frozenset(),
) -> TemplateFact:
    return TemplateFact(
        source_file="synthetic.xsl",
        start_line=0,
        tag=tag,
        mode=mode,
        is_880_alias_for=is_880_alias_for,
        indicator_tests=indicator_tests,  # type: ignore[arg-type]
        indicator_projected=False,
        subfield_codes=subfields,
        controlfield_position_reads=controlfield_positions,
        leader_position_reads=leader_positions,
        output_terms=output_terms,
        dynamic_element_constructors=dynamic_constructors,
    )


def test_blank_indicator_renders_as_hash() -> None:
    report = ParseReport(
        templates=(
            _make_template(
                "020",
                indicator_tests=frozenset({("ind2", " ")}),  # type: ignore[arg-type]
                subfields=frozenset({"a"}),
            ),
        ),
        parsed_modules=("synthetic.xsl",),
        xslt_commit_sha=None,
    )
    table = render_coverage_table(merge_templates_to_rows(report))
    assert "ind2=`#`" in table


def test_sort_order_controlfields_then_leader_then_datafields() -> None:
    report = ParseReport(
        templates=(
            _make_template("245"),
            _make_template("008"),
            _make_template("leader"),
        ),
        parsed_modules=("synthetic.xsl",),
        xslt_commit_sha=None,
    )
    rows = merge_templates_to_rows(report)
    assert [r.tag for r in rows] == ["008", "leader", "245"]


def test_dash_for_no_indicators_or_subfields() -> None:
    report = ParseReport(
        templates=(_make_template("245"),),
        parsed_modules=("synthetic.xsl",),
        xslt_commit_sha=None,
    )
    table = render_coverage_table(merge_templates_to_rows(report))
    # 245 has no indicator tests + no subfields + no positions + no outputs,
    # so multiple cells should render `—`.
    assert table.count("—") >= 4


def test_position_read_translates_xslt_1based_to_marc_0based() -> None:
    report = ParseReport(
        templates=(
            _make_template(
                "008",
                mode="work",
                controlfield_positions=frozenset({(36, 3), (16, 1)}),
            ),
        ),
        parsed_modules=("synthetic.xsl",),
        xslt_commit_sha=None,
    )
    table = render_coverage_table(merge_templates_to_rows(report))
    assert "`008/15`" in table  # 1-based start 16, length 1 -> MARC 15
    assert "`008/35-37`" in table  # 1-based start 36, length 3 -> MARC 35-37


def test_output_term_class_vs_predicate_classification() -> None:
    report = ParseReport(
        templates=(
            _make_template(
                "245",
                output_terms=frozenset(
                    {
                        OutputTerm(qname="bf:Title", kind="class", origin="literal"),
                        OutputTerm(qname="bf:title", kind="predicate", origin="literal"),
                    }
                ),
            ),
        ),
        parsed_modules=("synthetic.xsl",),
        xslt_commit_sha=None,
    )
    rows = merge_templates_to_rows(report)
    table = render_coverage_table(rows)
    assert "`bf:Title`" in table
    assert "`bf:title`" in table


def test_880_alias_folds_into_linked_tag_row_with_note() -> None:
    report = parse_xslt_corpus(FIXTURES / "eight_eighty_dispatch.xsl")
    rows = merge_templates_to_rows(report)
    by_tag = {r.tag: r for r in rows}
    assert "245" in by_tag
    assert "880" not in by_tag, "880 alias should not get its own row"
    assert any("880" in note for note in by_tag["245"].notes)


def test_dynamic_appendix_lists_constructors_outside_alias_rows() -> None:
    report = parse_xslt_corpus(FIXTURES / "dynamic_element.xsl")
    appendix = render_dynamic_appendix(report)
    assert "$vClass" in appendix
    assert "`340`" in appendix
