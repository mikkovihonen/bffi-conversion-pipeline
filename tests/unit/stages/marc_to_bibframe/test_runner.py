"""Unit tests for the MARC -> BIBFRAME corpus runner."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from rdflib import Graph, Literal, URIRef

from bffi_pipeline.observability.events import StageEventEmitter, set_active_emitter
from bffi_pipeline.provenance import vocab as V
from bffi_pipeline.stages.marc_to_bibframe.runner import (
    ConversionOptions,
    convert_corpus,
    convert_one,
)
from bffi_pipeline.stages.marc_to_bibframe.xslt import XsltPaths
from bffi_pipeline.validation.sidecar import ERRORS_FILENAME, VALIDATION_FILENAME

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SAMPLE_MARC = _REPO_ROOT / "third_party" / "marc2bibframe2" / "test" / "data" / "marc.xml"


def _options(
    input_dir: Path,
    output_dir: Path,
    *,
    preprocess: bool = True,
    validate: bool = True,
    # Mirrors the production default, so a test that doesn't care about
    # Boundary 2 still exercises what a real run does.
    strict_shapes: bool = True,
) -> ConversionOptions:
    return ConversionOptions(
        input_dir=input_dir,
        output_dir=output_dir,
        xslt_paths=XsltPaths.from_repo_root(_REPO_ROOT),
        baseuri="http://urn.fi/URN:NBN:fi:bib:",
        preprocess=preprocess,
        validate=validate,
        strict_shapes=strict_shapes,
    )


def test_convert_one_writes_rdf_xml_to_output_dir(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    out_dir = tmp_path / "out"
    sample = input_dir / "test.xml"
    shutil.copy(_SAMPLE_MARC, sample)

    output_path = convert_one(sample, options=_options(input_dir, out_dir))

    assert output_path == out_dir / "test.bibframe.xml"
    assert output_path.exists()
    content = output_path.read_text(encoding="utf-8")
    assert "<rdf:RDF" in content
    assert "bf:Work" in content


def test_convert_one_without_preprocess_still_produces_rdf_xml(tmp_path: Path) -> None:
    """The preprocess flag is opt-out; the no-preprocess path still works."""
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    out_dir = tmp_path / "out"
    sample = input_dir / "test.xml"
    shutil.copy(_SAMPLE_MARC, sample)

    output_path = convert_one(sample, options=_options(input_dir, out_dir, preprocess=False))
    assert output_path.exists()
    assert "bf:Work" in output_path.read_text(encoding="utf-8")


def test_convert_corpus_summary_and_sidecar_events(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    out_dir = tmp_path / "out"
    # Three identical input files — enough to verify the loop emits per-record.
    # Names are bare digits because Boundary 1 derives the bib ID from the
    # filename and rejects anything it can't read as one.
    for name in ("1.xml", "2.xml", "3.xml"):
        shutil.copy(_SAMPLE_MARC, input_dir / name)

    sidecar = tmp_path / "stage-events.jsonl"
    emitter = StageEventEmitter(sidecar_path=sidecar, run_uuid="test-run")
    set_active_emitter(emitter)
    try:
        summary = convert_corpus(options=_options(input_dir, out_dir))
    finally:
        set_active_emitter(None)

    assert summary.total == 3
    assert summary.converted == 3
    assert summary.failed == 0
    assert not summary.failures
    assert summary.skipped_invalid == 0

    assert (out_dir / "1.bibframe.xml").exists()
    assert (out_dir / "2.bibframe.xml").exists()
    assert (out_dir / "3.bibframe.xml").exists()

    # Sidecar should carry a start, at least one progress (the final one),
    # and an end event — all labeled `marc2bibframe`.
    lines = sidecar.read_text(encoding="utf-8").strip().splitlines()
    events = [json.loads(line) for line in lines]
    assert any(e["event"] == "start" for e in events)
    assert any(e["event"] == "end" for e in events)
    assert all(e["stage"] == "marc2bibframe" for e in events)
    start = next(e for e in events if e["event"] == "start")
    assert start["counters"]["entities_total"] == 3
    end = next(e for e in events if e["event"] == "end")
    assert end["counters"]["success"] == 3
    assert end["counters"]["failed"] == 0
    assert end["counters"]["skipped_invalid"] == 0
    # The vendored LoC sample carries no 33X and the Boundary-2 shape
    # currently fails every record (p-062 Phase B), so all three are
    # flagged — and flagged records still convert.
    assert end["counters"]["shape_flagged"] == 3


def test_convert_corpus_emits_failed_event_on_bad_input(tmp_path: Path) -> None:
    """The xsltproc-failure path, with the validation gate out of the way.

    ``--no-validate`` is what makes this test still about conversion
    failure: with Boundary 1 on, ``broken.xml`` never reaches xsltproc — it
    is rejected on its filename first. Both behaviours matter, so they get
    separate tests.
    """
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    out_dir = tmp_path / "out"
    # Write a syntactically-invalid XML file to provoke a failure.
    (input_dir / "broken.xml").write_text("not actually xml", encoding="utf-8")
    shutil.copy(_SAMPLE_MARC, input_dir / "good.xml")

    sidecar = tmp_path / "stage-events.jsonl"
    emitter = StageEventEmitter(sidecar_path=sidecar, run_uuid="test-run")
    set_active_emitter(emitter)
    try:
        summary = convert_corpus(options=_options(input_dir, out_dir, validate=False))
    finally:
        set_active_emitter(None)

    assert summary.total == 2
    assert summary.converted == 1
    assert summary.failed == 1
    assert len(summary.failures) == 1
    failed_path, message = summary.failures[0]
    assert failed_path.name == "broken.xml"
    assert message  # non-empty error text

    events = [json.loads(line) for line in sidecar.read_text(encoding="utf-8").splitlines()]
    failed_events = [e for e in events if e["event"] == "failed"]
    assert len(failed_events) == 1
    assert failed_events[0]["extra"]["path"].endswith("broken.xml")


def test_convert_one_writes_provenance_sidecar(tmp_path: Path) -> None:
    """The XSLT hop records its Activity and converter version. No decision
    triples — the stylesheet is one transform with no routing of ours."""
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    shutil.copy(_SAMPLE_MARC, in_dir / "b1.xml")

    convert_one(in_dir / "b1.xml", options=_options(in_dir, out_dir))

    sidecar = out_dir / "b1.prov.ttl"
    assert sidecar.is_file()
    g = Graph()
    g.parse(sidecar, format="turtle")
    activity = URIRef("http://urn.fi/URN:NBN:fi:bib:activity/marc2bibframe/b1")
    assert (activity, V.RDF.type, V.MarcConversion) in g
    assert (activity, V.stage, Literal("marc2bibframe")) in g
    assert str(next(g.objects(activity, V.converterVersion))).startswith("bffi-pipeline/")
    assert not list(g.objects(activity, V.decision))


def test_convert_corpus_counts_bad_encoding_record_instead_of_aborting(
    tmp_path: Path,
) -> None:
    """A Latin-1 record must be counted as one failure, not kill the run.

    Regression: xsltproc echoes the offending bytes into its stderr, which
    ``subprocess.run(text=True)`` used to decode strictly — raising
    ``UnicodeDecodeError`` from inside the wrapper. That escaped the
    ``except XsltprocError`` handler and aborted the whole corpus run on
    the first bad record. ``99999900.xml`` exists for exactly this case.

    Runs with ``validate=False``: Boundary 1 now rejects that record on
    encoding before xsltproc ever sees it, which is the better outcome but
    would stop this test from exercising the wrapper-level protection it
    was written for.
    """
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    bad = _REPO_ROOT / "tests" / "data" / "sample-marcxml" / "99999900.xml"
    shutil.copy(bad, in_dir / "99999900.xml")
    # A valid record after the bad one: it must still be converted, proving
    # the run continued rather than dying on record 1.
    shutil.copy(_SAMPLE_MARC, in_dir / "b0000001.xml")

    summary = convert_corpus(options=_options(in_dir, out_dir, validate=False))

    assert summary.total == 2
    assert summary.failed == 1
    assert summary.converted == 1
    failed_path, message = summary.failures[0]
    assert failed_path.name == "99999900.xml"
    assert message


def test_failed_event_carries_the_exception_class(tmp_path: Path) -> None:
    """``error_type`` is what the dashboard groups errors by; without it
    every failure lands in one unlabelled bucket."""
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    bad = _REPO_ROOT / "tests" / "data" / "sample-marcxml" / "99999900.xml"
    shutil.copy(bad, in_dir / "99999900.xml")

    sidecar = tmp_path / "stage-events.jsonl"
    set_active_emitter(StageEventEmitter(sidecar_path=sidecar, run_uuid="test-run"))
    try:
        # validate=False so the record reaches xsltproc; see the test above.
        convert_corpus(options=_options(in_dir, out_dir, validate=False))
    finally:
        set_active_emitter(None)

    rows = [json.loads(line) for line in sidecar.read_text(encoding="utf-8").splitlines()]
    failed = [r for r in rows if r["event"] == "failed"]
    assert failed
    assert failed[0]["extra"]["error_type"]


# --- validation boundaries (p-062) ---------------------------------------


def _errors_rows(out_dir: Path) -> list[dict[str, object]]:
    path = out_dir / ERRORS_FILENAME
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _validation_rows(out_dir: Path) -> list[dict[str, object]]:
    path = out_dir / VALIDATION_FILENAME
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_boundary1_structural_failure_skips_the_record(tmp_path: Path) -> None:
    """A structurally invalid record is skipped, not failed.

    The distinction is load-bearing: ``failed`` means the converter broke,
    ``skipped_invalid`` means the input was never usable. Conflating them
    puts unusable input into the failure count the CLI exits non-zero on.
    """
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    (in_dir / "1.xml").write_text("not actually xml", encoding="utf-8")
    shutil.copy(_SAMPLE_MARC, in_dir / "2.xml")

    summary = convert_corpus(options=_options(in_dir, out_dir))

    assert summary.skipped_invalid == 1
    assert summary.converted == 1
    assert summary.failed == 0
    assert not (out_dir / "1.bibframe.xml").exists()

    rows = _errors_rows(out_dir)
    assert len(rows) == 1
    assert rows[0]["boundary"] == 1
    assert rows[0]["error_type"] == "marcxml-xml-syntax"
    assert rows[0]["bib_id"] == "1"


def test_boundary1_rejects_an_unreadable_filename(tmp_path: Path) -> None:
    """The bib ID comes from the filename, so an unparseable name is fatal."""
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    shutil.copy(_SAMPLE_MARC, in_dir / "marc.xml")

    summary = convert_corpus(options=_options(in_dir, out_dir))

    assert summary.skipped_invalid == 1
    assert summary.converted == 0
    assert _errors_rows(out_dir)[0]["error_type"] == "marcxml-filename"


def test_boundary1_content_minimum_flags_but_still_converts(tmp_path: Path) -> None:
    """Content-minimum is advisory.

    Measured before wiring: 7 of 53 real fixture records and LoC's own test
    record fail this check for a missing 33X, and all of them convert. A
    gate here would drop them — so it reports instead.
    """
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    shutil.copy(_SAMPLE_MARC, in_dir / "1.xml")

    summary = convert_corpus(options=_options(in_dir, out_dir))

    assert summary.converted == 1
    assert summary.skipped_invalid == 0
    assert (out_dir / "1.bibframe.xml").exists()
    assert not (out_dir / ERRORS_FILENAME).exists()

    kinds = {row["error_type"] for row in _validation_rows(out_dir)}
    assert "marcxml-content-minimum" in kinds


def test_no_validate_writes_no_sidecars(tmp_path: Path) -> None:
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    shutil.copy(_SAMPLE_MARC, in_dir / "marc.xml")

    summary = convert_corpus(options=_options(in_dir, out_dir, validate=False))

    # The filename Boundary 1 rejects converts fine with the gate off.
    assert summary.converted == 1
    assert summary.skipped_invalid == 0
    assert not (out_dir / ERRORS_FILENAME).exists()
    assert not (out_dir / VALIDATION_FILENAME).exists()


#: MARCXML with no 245 at all. Boundary 1 flags it (content-minimum) but lets
#: it through; the conversion then yields an untitled Work and Instance, which
#: is what Boundary 2 is for.
_NO_TITLE_RECORD = """<?xml version='1.0' encoding='UTF-8'?>
<record xmlns="http://www.loc.gov/MARC21/slim">
  <leader>00000nam a2200000 a 4500</leader>
  <controlfield tag="001">1</controlfield>
  <controlfield tag="008">230101s2023    fi ||||| |||| 00| 0 fin d</controlfield>
  <datafield tag="100" ind1="1" ind2=" ">
    <subfield code="a">Tekija, Testi,</subfield>
    <subfield code="e">kirjoittaja.</subfield>
  </datafield>
</record>
"""


def test_boundary2_rejects_a_titleless_conversion_and_removes_its_output(
    tmp_path: Path,
) -> None:
    """A rejection must leave nothing behind for the next stage.

    Output that stayed on disk would be picked up by ``bibframe-to-bffi``'s
    glob, which would make the rejection cosmetic.
    """
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    (in_dir / "1.xml").write_text(_NO_TITLE_RECORD, encoding="utf-8")

    summary = convert_corpus(options=_options(in_dir, out_dir))

    assert summary.skipped_invalid == 1
    assert summary.converted == 0
    assert not (out_dir / "1.bibframe.xml").exists()
    assert not (out_dir / "1.prov.ttl").exists()

    row = next(r for r in _errors_rows(out_dir) if r["boundary"] == 2)
    assert row["error_type"] == "bibframe-shape"
    assert isinstance(row["violations"], int)
    assert row["violations"] > 0


def test_no_strict_shapes_keeps_a_non_conforming_record(tmp_path: Path) -> None:
    """The escape hatch: flag instead of reject, output stays."""
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    (in_dir / "1.xml").write_text(_NO_TITLE_RECORD, encoding="utf-8")

    summary = convert_corpus(options=_options(in_dir, out_dir, strict_shapes=False))

    assert summary.converted == 1
    assert summary.skipped_invalid == 0
    assert summary.shape_flagged == 1
    assert (out_dir / "1.bibframe.xml").exists()
    assert any(r["error_type"] == "bibframe-shape" for r in _validation_rows(out_dir))


def test_a_conforming_record_is_neither_rejected_nor_shape_flagged(tmp_path: Path) -> None:
    """The rescoped shape passes real conversion output (p-062 Phase B).

    Before the rescope this record failed Boundary 2, as did every other —
    the shape required triples a post-processor this repository doesn't have
    was supposed to add. With ``--strict-shapes`` now on by default, that
    would have emptied the corpus.
    """
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    shutil.copy(_SAMPLE_MARC, in_dir / "1.xml")

    summary = convert_corpus(options=_options(in_dir, out_dir))

    assert summary.converted == 1
    assert summary.skipped_invalid == 0
    assert (out_dir / "1.bibframe.xml").exists()
    # The one flag is Boundary 1's content-minimum: the LoC sample has no 33X.
    kinds = {row["error_type"] for row in _validation_rows(out_dir)}
    assert kinds == {"marcxml-content-minimum"}


def test_a_rerun_replaces_rather_than_appends_to_the_sidecar(tmp_path: Path) -> None:
    """Stages overwrite unconditionally, so the sidecars must too.

    Appending would make the second run's error count read as the sum of
    both runs.
    """
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    (in_dir / "1.xml").write_text("not actually xml", encoding="utf-8")

    convert_corpus(options=_options(in_dir, out_dir))
    convert_corpus(options=_options(in_dir, out_dir))

    assert len(_errors_rows(out_dir)) == 1
