"""Unit tests for the MARCXML diff classifier."""

from __future__ import annotations

from pathlib import Path

import pytest

from bffi_pipeline.stages.roundtrip_eval.diff import (
    FieldRow,
    MarcxmlParseError,
    diff_fields,
    diff_records,
    parse_record,
)

_MIN_RECORD = b"""<?xml version='1.0' encoding='utf-8'?>
<record xmlns="http://www.loc.gov/MARC21/slim">
  <leader>00000nam a2200000 a 4500</leader>
  <controlfield tag="001">b1</controlfield>
  <datafield tag="245" ind1="0" ind2="0">
    <subfield code="a">Same Title</subfield>
  </datafield>
</record>
"""


def _write(path: Path, payload: bytes) -> Path:
    path.write_bytes(payload)
    return path


# --- parse_record -------------------------------------------------------


def test_parse_record_extracts_bib_id_and_fields(tmp_path: Path) -> None:
    path = _write(tmp_path / "rec.xml", _MIN_RECORD)
    bib_id, fields = parse_record(path)
    assert bib_id == "b1"
    tags = sorted(f.tag for f in fields)
    assert tags == ["001", "245"]


def test_parse_record_handles_collection_wrapper(tmp_path: Path) -> None:
    """The MARC21 slim schema allows ``<collection><record>...</record></collection>``."""
    payload = (
        b"<?xml version='1.0' encoding='utf-8'?>"
        b"<collection xmlns='http://www.loc.gov/MARC21/slim'>"
        + _MIN_RECORD.split(b"?>\n", 1)[1]
        + b"</collection>"
    )
    path = _write(tmp_path / "rec.xml", payload)
    bib_id, _ = parse_record(path)
    assert bib_id == "b1"


def test_parse_record_raises_on_missing_001(tmp_path: Path) -> None:
    payload = b"""<?xml version='1.0' encoding='utf-8'?>
<record xmlns="http://www.loc.gov/MARC21/slim">
  <leader>00000nam a2200000 a 4500</leader>
</record>"""
    path = _write(tmp_path / "rec.xml", payload)
    with pytest.raises(MarcxmlParseError, match="no controlfield 001"):
        parse_record(path)


# --- diff_fields --------------------------------------------------------


def _df(tag: str, code: str, value: str) -> FieldRow:
    return FieldRow(tag=tag, ind1="0", ind2="0", subfields=((code, value),), text=None)


def _cf(tag: str, value: str) -> FieldRow:
    return FieldRow(tag=tag, ind1=None, ind2=None, subfields=(), text=value)


def test_diff_fields_identical_when_source_matches_reconstructed_exactly() -> None:
    source = (_cf("001", "b1"), _df("245", "a", "Title"))
    diffs = diff_fields(source=source, reconstructed=source)
    statuses = [d.status for d in diffs]
    assert statuses == ["identical", "identical"]


def test_diff_fields_classifies_changed_when_same_tag_different_content() -> None:
    source = (_df("245", "a", "Source Title"),)
    recon = (_df("245", "a", "Different Title"),)
    diffs = diff_fields(source=source, reconstructed=recon)
    assert len(diffs) == 1
    assert diffs[0].status == "changed"
    assert diffs[0].source.subfields[0][1] == "Source Title"
    assert diffs[0].reconstructed.subfields[0][1] == "Different Title"


def test_diff_fields_classifies_lost_when_only_in_source() -> None:
    source = (_df("700", "a", "Andersen, H. C."),)
    recon: tuple[FieldRow, ...] = ()
    diffs = diff_fields(source=source, reconstructed=recon)
    assert [d.status for d in diffs] == ["lost"]
    assert diffs[0].source.subfields[0][1] == "Andersen, H. C."
    assert diffs[0].reconstructed is None


def test_diff_fields_classifies_added_when_only_in_reconstructed() -> None:
    source: tuple[FieldRow, ...] = ()
    recon = (_df("999", "a", "Synthesised"),)
    diffs = diff_fields(source=source, reconstructed=recon)
    assert [d.status for d in diffs] == ["added"]
    assert diffs[0].source is None
    assert diffs[0].reconstructed.subfields[0][1] == "Synthesised"


def test_diff_fields_leaves_unrelated_repeated_instances_unpaired() -> None:
    """Two repeated fields with nothing in common are lost + added.

    They used to be zipped positionally into a `changed` row, which claimed
    "A became X" about two fields that have no relationship (p-063).
    """
    source = (
        _df("700", "a", "A"),
        _df("700", "a", "B"),  # matches recon[0]
        _df("700", "a", "C"),
    )
    recon = (
        _df("700", "a", "B"),  # identical to source[1]
        _df("700", "a", "X"),  # shares nothing with A or C
    )
    diffs = diff_fields(source=source, reconstructed=recon)
    assert sorted(d.status for d in diffs) == ["added", "identical", "lost", "lost"]


# --- p-063: pairing by content, and the `reordered` status ----------------


def _sub(tag: str, *subfields: tuple[str, str], ind1: str = "0", ind2: str = "0") -> FieldRow:
    return FieldRow(tag=tag, ind1=ind1, ind2=ind2, subfields=subfields, text=None)


def test_subfield_order_alone_is_reordered_not_changed() -> None:
    """The reverse converter emits `$0` before `$2`; cataloguers write `$2`
    first. Same content, so not `changed` — but MARC prescribes subfield
    order, so not `identical` either."""
    source = (_sub("650", ("a", "henget"), ("2", "kauno"), ("0", "http://x/p1")),)
    recon = (_sub("650", ("a", "henget"), ("0", "http://x/p1"), ("2", "kauno")),)
    diffs = diff_fields(source=source, reconstructed=recon)
    assert [d.status for d in diffs] == ["reordered"]


def test_differing_indicators_are_changed_not_reordered() -> None:
    source = (_sub("084", ("a", "Historia"), ("2", "ykl"), ind1="9"),)
    recon = (_sub("084", ("a", "Historia"), ("2", "ykl"), ind1=" "),)
    diffs = diff_fields(source=source, reconstructed=recon)
    assert [d.status for d in diffs] == ["changed"]


def test_repeated_fields_pair_by_content_not_position() -> None:
    """The 650 case from the curated corpus.

    The reconstruction emits subjects in alphabetical order while the source
    is in cataloguer order. Positional zipping paired `perhesalaisuudet` with
    `aikatasot` and called it a changed value; each subject must instead find
    the instance that shares its authority URI.
    """
    source = (
        _sub("650", ("a", "perhesalaisuudet"), ("2", "kauno/fin"), ("0", "http://x/p959")),
        _sub("650", ("a", "mysteerit"), ("2", "kauno/fin"), ("0", "http://x/p6221")),
        _sub("650", ("a", "henget"), ("2", "kauno/fin"), ("0", "http://x/p5316")),
    )
    recon = (
        _sub("650", ("a", "aikatasot"), ("0", "http://x/p5820"), ("2", "kauno")),
        _sub("650", ("a", "henget"), ("0", "http://x/p5316"), ("2", "kauno")),
        _sub("650", ("a", "mysteerit"), ("0", "http://x/p6221"), ("2", "kauno")),
    )
    diffs = diff_fields(source=source, reconstructed=recon)

    paired = {
        d.source.subfields[0][1]: d.reconstructed.subfields[0][1]
        for d in diffs
        if d.status == "changed"
    }
    assert paired == {"mysteerit": "mysteerit", "henget": "henget"}
    lost = [d.source.subfields[0][1] for d in diffs if d.status == "lost"]
    added = [d.reconstructed.subfields[0][1] for d in diffs if d.status == "added"]
    assert lost == ["perhesalaisuudet"]
    assert added == ["aikatasot"]


def test_a_normalised_value_still_pairs_as_changed() -> None:
    """Fields sharing no subfield value exactly can still be the same field.

    A stripped nonfiling article or dropped trailing punctuation is the whole
    point of the `changed` bucket, so primary-value similarity pairs them
    even with no exact overlap to go on.
    """
    source = (_sub("700", ("a", "Puškin, Aleksandr,")), _sub("700", ("a", "Tolstoi, Leo,")))
    recon = (_sub("700", ("a", "Tolstoi, Leo")), _sub("700", ("a", "Puškin, Aleksandr")))
    diffs = diff_fields(source=source, reconstructed=recon)

    assert [d.status for d in diffs] == ["changed", "changed"]
    paired = {d.source.subfields[0][1]: d.reconstructed.subfields[0][1] for d in diffs}
    assert paired == {
        "Puškin, Aleksandr,": "Puškin, Aleksandr",
        "Tolstoi, Leo,": "Tolstoi, Leo",
    }


def test_a_single_instance_pairs_even_with_nothing_in_common() -> None:
    """One field in, one field out: `changed` beats a lost/added pair the
    reader has to re-associate by eye."""
    source = (_sub("040", ("a", "FI-BTJ")),)
    recon = (_sub("040", ("b", "fin")),)
    diffs = diff_fields(source=source, reconstructed=recon)
    assert [d.status for d in diffs] == ["changed"]


def test_pairing_is_deterministic_across_equally_good_candidates() -> None:
    """Two equally-similar candidates must resolve the same way every run —
    the review HTML is a conversion output like any other."""
    source = (_sub("650", ("a", "x"), ("0", "http://x/p1")),)
    recon = (
        _sub("650", ("a", "y"), ("0", "http://x/p1")),
        _sub("650", ("a", "z"), ("0", "http://x/p1")),
    )
    first = diff_fields(source=source, reconstructed=recon)
    second = diff_fields(source=source, reconstructed=recon)
    assert first == second
    changed = next(d for d in first if d.status == "changed")
    # Earliest reconstructed index wins the tie.
    assert changed.reconstructed.subfields[0][1] == "y"


# --- diff_records (integration) -----------------------------------------


def test_diff_records_round_trip_on_same_input(tmp_path: Path) -> None:
    """A record diffed against itself yields all-identical."""
    path = _write(tmp_path / "rec.xml", _MIN_RECORD)
    result = diff_records(source_path=path, reconstructed_path=path)
    assert result.bib_id == "b1"
    assert all(d.status == "identical" for d in result.fields)
    counts = result.status_counts
    assert counts["identical"] == len(result.fields)
