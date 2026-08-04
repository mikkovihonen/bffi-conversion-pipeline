"""Orchestrates the four auto-blocks in ``docs/marc_to_bibframe_mapping.md``.

Reads the doc, parses the XSLT, runs the cross-check, renders all four
blocks, and either writes back or — under ``check=True`` — reports
drift.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from bffi_pipeline.diagnostic._doc_blocks import replace_block
from bffi_pipeline.diagnostic.xslt_coverage.cross_check import cross_check
from bffi_pipeline.diagnostic.xslt_coverage.parser import parse_xslt_corpus
from bffi_pipeline.diagnostic.xslt_coverage.renderer import (
    merge_templates_to_rows,
    render_coverage_table,
    render_dynamic_appendix,
    render_metadata_block,
    render_roundtrip_table,
)
from bffi_pipeline.stages.bffi_to_marc.runner import MARC_EMIT_REGISTRY, MarcEmitMeta
from bffi_pipeline.stages.marc_to_bibframe.xslt import XsltPaths

#: Repo root resolved from this module's location. Six ``parents`` hops:
#: ``regenerator.py`` → ``xslt_coverage`` → ``diagnostic`` → ``bffi_pipeline``
#: → ``src`` → repo root.
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[4]

DEFAULT_XSLT_ENTRY_POINT: Final[Path] = XsltPaths.from_repo_root(_REPO_ROOT).convert
DEFAULT_DOC_PATH: Final[Path] = _REPO_ROOT / "docs" / "marc_to_bibframe_mapping.md"

COVERAGE_BEGIN_MARKER: Final[str] = "<!-- BEGIN AUTO: coverage -->"
COVERAGE_END_MARKER: Final[str] = "<!-- END AUTO: coverage -->"

DYNAMIC_BEGIN_MARKER: Final[str] = "<!-- BEGIN AUTO: dynamic -->"
DYNAMIC_END_MARKER: Final[str] = "<!-- END AUTO: dynamic -->"

ROUNDTRIP_BEGIN_MARKER: Final[str] = "<!-- BEGIN AUTO: roundtrip -->"
ROUNDTRIP_END_MARKER: Final[str] = "<!-- END AUTO: roundtrip -->"

METADATA_BEGIN_MARKER: Final[str] = "<!-- BEGIN AUTO: metadata -->"
METADATA_END_MARKER: Final[str] = "<!-- END AUTO: metadata -->"


@dataclass(frozen=True)
class GeneratedBlocks:
    coverage: str
    dynamic: str
    roundtrip: str
    metadata: str


def build_blocks(
    *,
    xslt_entry_point: Path | None = None,
    registry: Iterable[MarcEmitMeta] | None = None,
) -> GeneratedBlocks:
    """Compute the four markdown blocks. Pure: no filesystem writes."""
    entry = xslt_entry_point or DEFAULT_XSLT_ENTRY_POINT
    report = parse_xslt_corpus(entry)
    rows = merge_templates_to_rows(report)
    crosscheck = cross_check(
        report,
        registry=registry if registry is not None else MARC_EMIT_REGISTRY,
    )
    return GeneratedBlocks(
        coverage=render_coverage_table(rows),
        dynamic=render_dynamic_appendix(report),
        roundtrip=render_roundtrip_table(crosscheck),
        metadata=render_metadata_block(report, crosscheck),
    )


def regenerate_marc_to_bibframe_mapping(
    *,
    doc_path: Path | None = None,
    xslt_entry_point: Path | None = None,
    registry: Iterable[MarcEmitMeta] | None = None,
    check: bool = False,
) -> tuple[str, bool]:
    """Regenerate the auto-blocks in ``docs/marc_to_bibframe_mapping.md``.

    Returns ``(new_doc_text, changed)``. When ``check=True`` the file is
    not written.
    """
    target = doc_path or DEFAULT_DOC_PATH
    original = target.read_text(encoding="utf-8")
    blocks = build_blocks(xslt_entry_point=xslt_entry_point, registry=registry)

    new_text = replace_block(original, COVERAGE_BEGIN_MARKER, COVERAGE_END_MARKER, blocks.coverage)
    new_text = replace_block(new_text, DYNAMIC_BEGIN_MARKER, DYNAMIC_END_MARKER, blocks.dynamic)
    new_text = replace_block(
        new_text, ROUNDTRIP_BEGIN_MARKER, ROUNDTRIP_END_MARKER, blocks.roundtrip
    )
    new_text = replace_block(new_text, METADATA_BEGIN_MARKER, METADATA_END_MARKER, blocks.metadata)

    changed = new_text != original
    if changed and not check:
        target.write_text(new_text, encoding="utf-8")
    return new_text, changed
