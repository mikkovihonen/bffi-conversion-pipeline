"""Static coverage analysis for the vendored marc2bibframe2 XSLT.

The public surface is the regenerator: it parses the XSLT, cross-checks
the result against ``MARC_EMIT_REGISTRY``, and rewrites the auto-blocks
in ``docs/marc_to_bibframe_mapping.md``.
"""

from __future__ import annotations

from bffi_pipeline.diagnostic.xslt_coverage.cross_check import cross_check
from bffi_pipeline.diagnostic.xslt_coverage.model import (
    CoverageRow,
    CrossCheckReport,
    CrossCheckRow,
    OutputTerm,
    ParseReport,
    TemplateFact,
)
from bffi_pipeline.diagnostic.xslt_coverage.parser import parse_xslt_corpus
from bffi_pipeline.diagnostic.xslt_coverage.regenerator import (
    COVERAGE_BEGIN_MARKER,
    COVERAGE_END_MARKER,
    DEFAULT_DOC_PATH,
    DEFAULT_XSLT_ENTRY_POINT,
    DYNAMIC_BEGIN_MARKER,
    DYNAMIC_END_MARKER,
    METADATA_BEGIN_MARKER,
    METADATA_END_MARKER,
    ROUNDTRIP_BEGIN_MARKER,
    ROUNDTRIP_END_MARKER,
    build_blocks,
    regenerate_marc_to_bibframe_mapping,
)
from bffi_pipeline.diagnostic.xslt_coverage.renderer import (
    merge_templates_to_rows,
    render_coverage_table,
    render_dynamic_appendix,
    render_metadata_block,
    render_roundtrip_table,
)

__all__ = [
    "COVERAGE_BEGIN_MARKER",
    "COVERAGE_END_MARKER",
    "DEFAULT_DOC_PATH",
    "DEFAULT_XSLT_ENTRY_POINT",
    "DYNAMIC_BEGIN_MARKER",
    "DYNAMIC_END_MARKER",
    "METADATA_BEGIN_MARKER",
    "METADATA_END_MARKER",
    "ROUNDTRIP_BEGIN_MARKER",
    "ROUNDTRIP_END_MARKER",
    "CoverageRow",
    "CrossCheckReport",
    "CrossCheckRow",
    "OutputTerm",
    "ParseReport",
    "TemplateFact",
    "build_blocks",
    "cross_check",
    "merge_templates_to_rows",
    "parse_xslt_corpus",
    "regenerate_marc_to_bibframe_mapping",
    "render_coverage_table",
    "render_dynamic_appendix",
    "render_metadata_block",
    "render_roundtrip_table",
]
