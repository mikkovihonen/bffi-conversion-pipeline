"""Regenerator orchestration tests against a synthetic doc + XSLT."""

from __future__ import annotations

from pathlib import Path

from bffi_pipeline.diagnostic.xslt_coverage.regenerator import (
    COVERAGE_BEGIN_MARKER,
    COVERAGE_END_MARKER,
    DYNAMIC_BEGIN_MARKER,
    DYNAMIC_END_MARKER,
    METADATA_BEGIN_MARKER,
    METADATA_END_MARKER,
    ROUNDTRIP_BEGIN_MARKER,
    ROUNDTRIP_END_MARKER,
    regenerate_marc_to_bibframe_mapping,
)

FIXTURES = Path(__file__).parent / "fixtures"

_HANDWRITTEN_FRAMING = """# Synthetic doc

This is hand-written framing — it must survive a regeneration.

## Coverage

<!-- BEGIN AUTO: coverage -->
<!-- END AUTO: coverage -->

## Dynamic

<!-- BEGIN AUTO: dynamic -->
<!-- END AUTO: dynamic -->

## Roundtrip

<!-- BEGIN AUTO: roundtrip -->
<!-- END AUTO: roundtrip -->

## Metadata

<!-- BEGIN AUTO: metadata -->
<!-- END AUTO: metadata -->

End of hand-written framing.
"""


def test_regenerator_replaces_all_four_blocks_and_preserves_framing(tmp_path: Path) -> None:
    doc = tmp_path / "doc.md"
    doc.write_text(_HANDWRITTEN_FRAMING, encoding="utf-8")

    new_text, changed = regenerate_marc_to_bibframe_mapping(
        doc_path=doc,
        xslt_entry_point=FIXTURES / "single_tag_with_indicators.xsl",
        registry=[],
    )
    assert changed is True
    # Framing intact.
    assert "# Synthetic doc" in new_text
    assert "End of hand-written framing." in new_text
    # Markers intact.
    for marker in (
        COVERAGE_BEGIN_MARKER,
        COVERAGE_END_MARKER,
        DYNAMIC_BEGIN_MARKER,
        DYNAMIC_END_MARKER,
        ROUNDTRIP_BEGIN_MARKER,
        ROUNDTRIP_END_MARKER,
        METADATA_BEGIN_MARKER,
        METADATA_END_MARKER,
    ):
        assert marker in new_text
    # Tag from the fixture.
    assert "`100`" in new_text


def test_regenerator_is_idempotent(tmp_path: Path) -> None:
    doc = tmp_path / "doc.md"
    doc.write_text(_HANDWRITTEN_FRAMING, encoding="utf-8")

    text_after_first, first_changed = regenerate_marc_to_bibframe_mapping(
        doc_path=doc,
        xslt_entry_point=FIXTURES / "single_tag_with_indicators.xsl",
        registry=[],
    )
    text_after_second, second_changed = regenerate_marc_to_bibframe_mapping(
        doc_path=doc,
        xslt_entry_point=FIXTURES / "single_tag_with_indicators.xsl",
        registry=[],
    )
    assert first_changed is True
    assert second_changed is False
    assert text_after_first == text_after_second


def test_check_mode_does_not_write(tmp_path: Path) -> None:
    doc = tmp_path / "doc.md"
    doc.write_text(_HANDWRITTEN_FRAMING, encoding="utf-8")
    on_disk_before = doc.read_text(encoding="utf-8")

    _, changed = regenerate_marc_to_bibframe_mapping(
        doc_path=doc,
        xslt_entry_point=FIXTURES / "single_tag_with_indicators.xsl",
        registry=[],
        check=True,
    )
    assert changed is True
    assert doc.read_text(encoding="utf-8") == on_disk_before
