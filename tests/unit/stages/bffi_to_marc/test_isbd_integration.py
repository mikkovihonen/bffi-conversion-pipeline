"""Integration tests for ISBD punctuation with the full pipeline."""

import xml.etree.ElementTree as ET
from pathlib import Path

from bffi_pipeline.stages.bffi_to_marc.runner import ConversionOptions, convert_corpus


def _parse_marc(run_dir: Path) -> list[ET.Element]:
    """Parse all MARCXML files in a directory and return datafields."""
    marc_dir = run_dir / "marc"
    all_fields = []
    if marc_dir.exists():
        for marc_file in marc_dir.glob("*.marcxml"):
            tree = ET.parse(marc_file)
            root = tree.getroot()
            ns = {"ns0": "http://www.loc.gov/MARC21/slim"}
            for tag in ["100", "245", "260", "264", "300", "490", "500", "650", "700"]:
                for f in root.findall(f".//ns0:datafield[@tag='{tag}']", ns):
                    sfs = [(sf.attrib["code"], sf.text) for sf in f.findall("ns0:subfield", ns)]
                    all_fields.append(sfs)
    return all_fields


def test_isbd_260_a_ends_with_colon() -> None:
    """260 $a should end with ':' when $b follows."""
    run_dir = Path("/tmp/test-isbd-260-colon")
    options = ConversionOptions(
        input_dir=Path("tests/data/sample-bffi/curated"),
        output_dir=run_dir / "marc",
        apply_isbd_punctuation=True,
    )
    summary = convert_corpus(options=options)
    assert summary.failed == 0

    fields = _parse_marc(run_dir)
    for sfs in fields:
        for code, text in sfs:
            if code == "a":
                # $a should end with ':' if $b follows, or have no trailing punct if alone
                has_b = any(c == "b" for c, _ in sfs)
                if has_b:
                    assert text.endswith(":"), f"260 $a should end with ':' but got: {text}"


def test_isbd_260_c_ends_with_period() -> None:
    """260 $c should end with '.' as the last subfield."""
    run_dir = Path("/tmp/test-isbd-260-period")
    options = ConversionOptions(
        input_dir=Path("tests/data/sample-bffi/curated"),
        output_dir=run_dir / "marc",
        apply_isbd_punctuation=True,
    )
    summary = convert_corpus(options=options)
    assert summary.failed == 0

    fields = _parse_marc(run_dir)
    for sfs in fields:
        for code, text in sfs:
            if code == "c":
                # $c is the last subfield in 260, so should end with '.'
                assert text.endswith("."), f"260 $c should end with '.' but got: {text}"


def test_isbd_300_a_b_c_punctuation() -> None:
    """300 $a has ':', $b has ';', $c has '.'."""
    run_dir = Path("/tmp/test-isbd-300")
    options = ConversionOptions(
        input_dir=Path("tests/data/sample-bffi/curated"),
        output_dir=run_dir / "marc",
        apply_isbd_punctuation=True,
    )
    summary = convert_corpus(options=options)
    assert summary.failed == 0

    fields = _parse_marc(run_dir)
    for sfs in fields:
        codes = [c for c, _ in sfs]
        for code, text in sfs:
            if code == "a":
                if "b" in codes:
                    assert text.endswith(":"), f"300 $a should end with ':' but got: {text}"
                elif "c" in codes:
                    assert text.endswith(" ;"), f"300 $a should end with ' ;' but got: {text}"
            elif code == "b":
                if "c" in codes:
                    assert text.endswith(" ;"), f"300 $b should end with ' ;' but got: {text}"
                else:
                    assert text.endswith("."), f"300 $b should end with '.' but got: {text}"


def test_isbd_245_a_b_c_punctuation() -> None:
    """245 $a has ':', $b has '/', $c has '.'."""
    run_dir = Path("/tmp/test-isbd-245")
    options = ConversionOptions(
        input_dir=Path("tests/data/sample-bffi/curated"),
        output_dir=run_dir / "marc",
        apply_isbd_punctuation=True,
    )
    summary = convert_corpus(options=options)
    assert summary.failed == 0

    fields = _parse_marc(run_dir)
    for sfs in fields:
        codes = [c for c, _ in sfs]
        for code, text in sfs:
            if code == "a":
                if "b" in codes or "n" in codes or "p" in codes:
                    assert text.endswith(":"), f"245 $a should end with ':' but got: {text}"
            elif code == "b":
                if "c" in codes or "f" in codes or "g" in codes:
                    assert text.endswith("/"), f"245 $b should end with '/' but got: {text}"
                else:
                    assert text.endswith("."), f"245 $b should end with '.' but got: {text}"


def test_isbd_100_a_e_punctuation() -> None:
    """100 $a has ',', $e has '.'."""
    run_dir = Path("/tmp/test-isbd-100")
    options = ConversionOptions(
        input_dir=Path("tests/data/sample-bffi/curated"),
        output_dir=run_dir / "marc",
        apply_isbd_punctuation=True,
    )
    summary = convert_corpus(options=options)
    assert summary.failed == 0

    fields = _parse_marc(run_dir)
    for sfs in fields:
        codes = [c for c, _ in sfs]
        for code, text in sfs:
            if code == "a":
                if "e" in codes or "4" in codes:
                    assert text.endswith(","), f"100 $a should end with ',' but got: {text}"
            elif code == "e":
                if "4" in codes:
                    assert text.endswith(","), f"100 $e should end with ',' but got: {text}"
                else:
                    assert text.endswith("."), f"100 $e should end with '.' but got: {text}"


def test_isbd_650_a_v_punctuation() -> None:
    """650 $a has '.', $v has '.'."""
    run_dir = Path("/tmp/test-isbd-650")
    options = ConversionOptions(
        input_dir=Path("tests/data/sample-bffi/curated"),
        output_dir=run_dir / "marc",
        apply_isbd_punctuation=True,
    )
    summary = convert_corpus(options=options)
    assert summary.failed == 0

    fields = _parse_marc(run_dir)
    for sfs in fields:
        for code, text in sfs:
            if code in ("a", "t", "c", "d", "v", "x", "y", "0", "2"):
                assert text.endswith("."), f"650 {code} should end with '.' but got: {text}"


def test_isbd_disabled_no_punctuation() -> None:
    """When ISBD is disabled, fields don't have trailing punctuation."""
    run_dir = Path("/tmp/test-isbd-disabled")
    options = ConversionOptions(
        input_dir=Path("tests/data/sample-bffi/curated"),
        output_dir=run_dir / "marc",
        apply_isbd_punctuation=False,
    )
    summary = convert_corpus(options=options)
    assert summary.failed == 0

    fields = _parse_marc(run_dir)
    for sfs in fields:
        for code, text in sfs:
            # Values should not end with ISBD punctuation marks
            assert not text.endswith((":", ",", ".", "/")), (
                f"{code} should not end with ISBD punct but got: {text}"
            )


def test_isbd_double_punctuation_prevention() -> None:
    """Values ending with punctuation don't get double punctuation."""
    run_dir = Path("/tmp/test-isbd-double-punct")
    options = ConversionOptions(
        input_dir=Path("tests/data/sample-bffi/curated"),
        output_dir=run_dir / "marc",
        apply_isbd_punctuation=True,
    )
    summary = convert_corpus(options=options)
    assert summary.failed == 0

    fields = _parse_marc(run_dir)
    for sfs in fields:
        for code, text in sfs:
            # Check for double punctuation (e.g., "foo,," or "foo:.")
            if len(text) >= 2:
                last_two = text[-2:]
                assert not (last_two[0] in ":,./" and last_two[1] in ":,./"), (
                    f"Double punctuation detected in {code}: {text}"
                )
