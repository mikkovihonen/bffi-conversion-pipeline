"""Shared helper for marker-driven block replacement in generated docs.

Every auto-generated section of a doc in ``docs/`` lives between a pair
of HTML comment markers (``<!-- BEGIN AUTO: <name> -->`` ... ``<!-- END
AUTO: <name> -->``) so the surrounding hand-written framing survives a
regeneration. Each generator was carrying its own copy of the same
``_replace_block`` helper; this module is the single source of truth.
"""

from __future__ import annotations


def replace_block(
    doc_text: str,
    begin_marker: str,
    end_marker: str,
    new_block: str,
) -> str:
    """Replace content between markers, preserving the markers themselves."""
    begin_idx = doc_text.find(begin_marker)
    end_idx = doc_text.find(end_marker)
    if begin_idx == -1 or end_idx == -1 or end_idx < begin_idx:
        msg = f"could not locate markers in doc: begin={begin_marker!r}, end={end_marker!r}"
        raise ValueError(msg)
    before = doc_text[: begin_idx + len(begin_marker)]
    after = doc_text[end_idx:]
    return f"{before}\n\n{new_block}\n{after}"
