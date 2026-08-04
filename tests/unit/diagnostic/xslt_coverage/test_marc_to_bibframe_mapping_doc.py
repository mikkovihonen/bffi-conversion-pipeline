"""Drift guard for ``docs/marc_to_bibframe_mapping.md``.

The only test in this package that touches the live marc2bibframe2
submodule and the live ``MARC_EMIT_REGISTRY``. Failure means the doc on
disk is out of date; running ``bffi-pipeline regenerate-marc-to-bibframe-mapping``
should resolve it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from bffi_pipeline.diagnostic.xslt_coverage.regenerator import (
    COVERAGE_BEGIN_MARKER,
    COVERAGE_END_MARKER,
    DEFAULT_DOC_PATH,
    DYNAMIC_BEGIN_MARKER,
    DYNAMIC_END_MARKER,
    METADATA_BEGIN_MARKER,
    METADATA_END_MARKER,
    ROUNDTRIP_BEGIN_MARKER,
    ROUNDTRIP_END_MARKER,
    regenerate_marc_to_bibframe_mapping,
)


def test_doc_on_disk_matches_generator_output() -> None:
    _, changed = regenerate_marc_to_bibframe_mapping(check=True)
    assert not changed, (
        "docs/marc_to_bibframe_mapping.md is out of sync with the generator — "
        "run `bffi-pipeline regenerate-marc-to-bibframe-mapping` to refresh."
    )

    on_disk = DEFAULT_DOC_PATH.read_text(encoding="utf-8")
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
        assert marker in on_disk, f"missing marker in on-disk doc: {marker}"


def test_generator_is_idempotent_on_live_doc(tmp_path: Path) -> None:
    tmp_doc = tmp_path / "mapping.md"
    shutil.copy(DEFAULT_DOC_PATH, tmp_doc)

    text_after_first, _ = regenerate_marc_to_bibframe_mapping(doc_path=tmp_doc)
    text_after_second, changed_second = regenerate_marc_to_bibframe_mapping(doc_path=tmp_doc)
    assert text_after_first == text_after_second
    assert changed_second is False
