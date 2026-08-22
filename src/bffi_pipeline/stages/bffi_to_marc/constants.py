"""Shared MARC constants for the BFFI→MARC conversion stage.

Stage label, MARC21 namespace URI, and progress cadence —
consumed by both the extract and emit layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

STAGE: Final[str] = "bffi2marc"

MARC21_NS: Final[str] = "http://www.loc.gov/MARC21/slim"

_MARC: Final[str] = f"{{{MARC21_NS}}}"

PROGRESS_CADENCE: Final[int] = 100


@dataclass(frozen=True)
class ConversionOptions:
    """Configuration for one corpus-conversion run."""

    input_dir: Path
    output_dir: Path
    apply_isbd_punctuation: bool = True


@dataclass
class ConversionSummary:
    """Aggregate counts after corpus conversion ends."""

    total: int = 0
    converted: int = 0
    failed: int = 0
    #: Inputs that produced zero ``bffi:Manifestation`` entities. Indicates
    #: either a malformed BFFI Turtle or a BIBFRAME-stage output we haven't
    #: routed yet. v0 treats these as failures.
    no_manifestation: int = 0
    failures: list[tuple[Path, str]] = field(default_factory=list)
