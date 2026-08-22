"""Integration tests for ISBD punctuation with the full pipeline."""

import xml.etree.ElementTree as ET
from pathlib import Path

from bffi_pipeline.stages.bffi_to_marc.runner import ConversionOptions, convert_corpus


def test_isbd_punctuation_enabled_adds_trailing_punctuation() -> None:
    """When --apply-isbd-punctuation is set, fields have ISBD trailing punctuation."""
    run_dir = Path("/tmp/test-isbd-integration")
    options = ConversionOptions(
        input_dir=Path("tests/data/sample-bffi/curated"),
        output_dir=run_dir / "marc",
        apply_isbd_punctuation=True,
    )
    summary = convert_corpus(options=options)
    assert summary.failed == 0

    # Find a record with 260/264 field
    marc_dir = run_dir / "marc"
    if marc_dir.exists():
        for marc_file in marc_dir.glob("*.marcxml"):
            tree = ET.parse(marc_file)
            root = tree.getroot()
            ns = {"ns0": "http://www.loc.gov/MARC21/slim"}
            for f in root.findall(".//ns0:datafield[@tag='264']", ns):
                sfs = [(sf.attrib["code"], sf.text) for sf in f.findall("ns0:subfield", ns)]
                # Check that $a has colon, $b has comma, $c has period
                for code, text in sfs:
                    if code == "a":
                        assert text.endswith(":"), f"264 $a should end with ':' but got: {text}"
                    elif code == "b":
                        assert text.endswith(","), f"264 $b should end with ',' but got: {text}"
                    elif code == "c":
                        assert text.endswith("."), f"264 $c should end with '.' but got: {text}"


def test_isbd_punctuation_disabled_no_trailing_punctuation() -> None:
    """When --apply-isbd-punctuation is not set, fields don't have ISBD trailing punctuation."""
    run_dir = Path("/tmp/test-isbd-integration-disabled")
    options = ConversionOptions(
        input_dir=Path("tests/data/sample-bffi/curated"),
        output_dir=run_dir / "marc",
        apply_isbd_punctuation=False,
    )
    summary = convert_corpus(options=options)
    assert summary.failed == 0

    # Find a record with 260/264 field
    marc_dir = run_dir / "marc"
    if marc_dir.exists():
        for marc_file in marc_dir.glob("*.marcxml"):
            tree = ET.parse(marc_file)
            root = tree.getroot()
            ns = {"ns0": "http://www.loc.gov/MARC21/slim"}
            for f in root.findall(".//ns0:datafield[@tag='264']", ns):
                sfs = [(sf.attrib["code"], sf.text) for sf in f.findall("ns0:subfield", ns)]
                # Check that $a doesn't have colon, $b doesn't have comma,
                # $c doesn't have period
                for code, text in sfs:
                    if code == "a":
                        assert not text.endswith(":"), (
                            f"264 $a should not end with ':' but got: {text}"
                        )
                    elif code == "b":
                        assert not text.endswith(","), (
                            f"264 $b should not end with ',' but got: {text}"
                        )
                    elif code == "c":
                        assert not text.endswith("."), (
                            f"264 $c should not end with '.' but got: {text}"
                        )
