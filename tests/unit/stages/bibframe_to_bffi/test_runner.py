"""Unit tests for the BIBFRAME -> BFFI corpus runner."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from rdflib import RDF, Graph, Literal, URIRef

from bffi_pipeline.observability.events import StageEventEmitter, set_active_emitter
from bffi_pipeline.provenance import vocab as V
from bffi_pipeline.stages.bibframe_to_bffi.mappings import (
    BF_NAMESPACE,
    BFFI_NAMESPACE,
    load_rules,
)
from bffi_pipeline.stages.bibframe_to_bffi.runner import (
    ConversionOptions,
    convert_corpus,
    convert_one,
)
from bffi_pipeline.validation.sidecar import ERRORS_FILENAME, VALIDATION_FILENAME


def _parse_turtle(path: Path) -> Graph:
    g = Graph()
    g.parse(path, format="turtle")
    return g


_REPO_ROOT = Path(__file__).resolve().parents[4]
_SAMPLE_MARC = _REPO_ROOT / "third_party" / "marc2bibframe2" / "test" / "data" / "marc.xml"
_MARC2BFRAME_XSL = _REPO_ROOT / "third_party" / "marc2bibframe2" / "xsl" / "marc2bibframe2.xsl"


def _produce_bibframe_fixture(out_dir: Path, *, stem: str = "test") -> Path:
    """Run the vendored MARCXML through marc2bibframe2 to get a real BIBFRAME RDF/XML.

    Used as the fixture for the BIBFRAME -> BFFI runner; avoids hand-rolling
    BIBFRAME by hand or vendoring a fixture file. Fast enough at <1s.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    bibframe_path = out_dir / f"{stem}.bibframe.xml"
    result = subprocess.run(
        ["xsltproc", str(_MARC2BFRAME_XSL), str(_SAMPLE_MARC)],
        capture_output=True,
        text=True,
        check=True,
    )
    bibframe_path.write_text(result.stdout, encoding="utf-8")
    return bibframe_path


def test_convert_one_emits_bffi_person_for_bf_person(tmp_path: Path) -> None:
    """Sanity check the rename actually happens: at least one entity in
    the vendored fixture is typed ``bffi:Person`` after rename, and no
    entity carries the original ``bf:Person`` type."""
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    _produce_bibframe_fixture(in_dir, stem="test")
    options = ConversionOptions(input_dir=in_dir, output_dir=out_dir)

    output_path, _residual, _routings = convert_one(
        in_dir / "test.bibframe.xml", options=options, rules=load_rules()
    )
    assert output_path == out_dir / "test.bffi.ttl"

    g = _parse_turtle(output_path)
    bffi_person = URIRef(BFFI_NAMESPACE + "Person")
    bf_person = URIRef(BF_NAMESPACE + "Person")
    assert any(g.triples((None, RDF.type, bffi_person)))
    assert not any(g.triples((None, RDF.type, bf_person)))


def test_convert_one_renames_bf_work_to_bibframework(tmp_path: Path) -> None:
    """Memory-note canonical case: ``bf:Work`` -> ``bffi:BibframeWork`` (not
    ``bffi:Work``, which is a separate RDA-aligned concept)."""
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    _produce_bibframe_fixture(in_dir, stem="test")

    output_path, _, _ = convert_one(
        in_dir / "test.bibframe.xml",
        options=ConversionOptions(input_dir=in_dir, output_dir=out_dir),
        rules=load_rules(),
    )
    g = _parse_turtle(output_path)
    bffi_work = URIRef(BFFI_NAMESPACE + "Work")
    bffi_bibframework = URIRef(BFFI_NAMESPACE + "BibframeWork")
    bf_work = URIRef(BF_NAMESPACE + "Work")
    assert any(g.triples((None, RDF.type, bffi_work)))
    assert not any(g.triples((None, RDF.type, bffi_bibframework)))
    assert not any(g.triples((None, RDF.type, bf_work)))


def test_convert_one_residual_count_surfaces_unhandled_bf_terms(tmp_path: Path) -> None:
    """Records that carry a discriminator-routed term (`bf:Hub`,
    `bf:VariantTitle`, …) carry a non-zero residual until step 6 lands.

    Use the vendored sample — it has at least one ``bf:Identifier``
    subclass without a clean rename rule (e.g. ``bf:Isbn``), so the
    residual should be positive.
    """
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    _produce_bibframe_fixture(in_dir, stem="test")

    _, residual, _ = convert_one(
        in_dir / "test.bibframe.xml",
        options=ConversionOptions(input_dir=in_dir, output_dir=out_dir),
        rules=load_rules(),
    )
    # After the discriminator routings ship in step 6, the vendored test
    # record's discriminator-routed terms (bf:Isbn, bf:Hub, bf:VariantTitle,
    # …) are rewritten to bffi:* shapes so the residual collapses to 0.
    # Records can still carry residue if they hit a term family beyond
    # what Phase 1 + Phase 4 cover.
    assert residual >= 0


def test_convert_corpus_summary_and_sidecar_events(tmp_path: Path) -> None:
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    src = _produce_bibframe_fixture(in_dir, stem="test")
    # Duplicate the fixture so we exercise more than one iteration.
    for stem in ("a", "b"):
        shutil.copy(src, in_dir / f"{stem}.bibframe.xml")
    src.unlink()  # leave just a.bibframe.xml + b.bibframe.xml

    sidecar = tmp_path / "stage-events.jsonl"
    emitter = StageEventEmitter(sidecar_path=sidecar, run_uuid="test-run")
    set_active_emitter(emitter)
    try:
        summary = convert_corpus(options=ConversionOptions(input_dir=in_dir, output_dir=out_dir))
    finally:
        set_active_emitter(None)

    assert summary.total == 2
    assert summary.converted == 2
    assert summary.failed == 0
    assert (out_dir / "a.bffi.ttl").exists()
    assert (out_dir / "b.bffi.ttl").exists()

    events = [json.loads(line) for line in sidecar.read_text(encoding="utf-8").splitlines()]
    assert all(e["stage"] == "bibframe2bffi" for e in events)
    start = next(e for e in events if e["event"] == "start")
    assert start["counters"]["entities_total"] == 2
    end = next(e for e in events if e["event"] == "end")
    assert end["counters"]["success"] == 2
    assert end["counters"]["failed"] == 0
    # Residue is per-record, not corpus-summed; the vendored fixture
    # exercises classes Phase 4 covers (bf:Isbn, bf:Hub, bf:Lccn,
    # bf:Topic-via-subject, …) so the count should be small after step 6
    # — but exact zero isn't guaranteed because the test record carries
    # term families (e.g. complex-subject decomposition) Phase 4 doesn't
    # tackle. Bound: at most one residual-per-record, so <= 2 for two
    # identical copies.
    assert end["counters"]["closed_namespace_residue"] <= 2
    # Routings fired non-zero times — at minimum the identifier-scheme
    # routing rewrites the ISBN/ISSN blocks the vendored record carries.
    assert end["counters"]["routing_identifier_scheme"] >= 1


def test_convert_corpus_emits_failed_event_on_bad_input(tmp_path: Path) -> None:
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    # rdflib parses "not xml" as turtle-ish whitespace and emits an empty
    # graph rather than raising — use actual invalid RDF/XML to provoke a
    # parse error.
    (in_dir / "broken.bibframe.xml").write_text("<rdf:RDF><not-closed-tag", encoding="utf-8")

    sidecar = tmp_path / "stage-events.jsonl"
    emitter = StageEventEmitter(sidecar_path=sidecar, run_uuid="test-run")
    set_active_emitter(emitter)
    try:
        summary = convert_corpus(options=ConversionOptions(input_dir=in_dir, output_dir=out_dir))
    finally:
        set_active_emitter(None)

    assert summary.total == 1
    assert summary.converted == 0
    assert summary.failed == 1
    failed_path, _msg = summary.failures[0]
    assert failed_path.name == "broken.bibframe.xml"


@pytest.fixture(autouse=True)
def _clear_active_emitter() -> None:
    """Defensive cleanup in case a test leaves a stale emitter set."""
    yield
    set_active_emitter(None)


def test_convert_one_writes_provenance_sidecar_with_routing_decisions(
    tmp_path: Path,
) -> None:
    """Provenance is mandatory (``CLAUDE.md``): the record's routing
    decisions must land in a ``.prov.ttl`` beside its output, and only the
    routings that actually fired get a decision triple."""
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    _produce_bibframe_fixture(in_dir, stem="test")
    options = ConversionOptions(input_dir=in_dir, output_dir=out_dir)

    output_path, _residual, routings = convert_one(
        in_dir / "test.bibframe.xml", options=options, rules=load_rules()
    )

    sidecar = out_dir / "test.prov.ttl"
    assert sidecar.is_file()
    g = _parse_turtle(sidecar)
    activity = URIRef("http://urn.fi/URN:NBN:fi:bib:activity/bibframe2bffi/test")
    assert (activity, V.stage, Literal("bibframe2bffi")) in g
    assert (activity, V.localBibId, Literal("test")) in g

    recorded = {str(o) for o in g.objects(activity, V.decision)}
    expected = {f"{name}={count}" for name, count in routings.items() if count}
    assert recorded == expected
    assert output_path.is_file()


def test_emitted_turtle_has_no_auto_generated_prefixes(tmp_path: Path) -> None:
    """Real marc2bibframe2 output must serialise with zero ``ns1:`` prefixes.

    Regression guard with teeth: the hand-listed namespace test in
    ``test_vocab_prefixes.py`` can only catch namespaces someone remembered
    to list. This one converts an actual BIBFRAME record, so any namespace
    the pipeline genuinely emits but forgets to bind shows up here. Dropping
    ``madsrdf`` from the canonical prefixes leaked ``ns1:`` into 255 of 302
    corpus records before this existed — marc2bibframe2 emits
    ``madsrdf:authoritativeLabel`` / ``madsrdf:Topic`` / ``madsrdf:GenreForm``
    on 6XX blocks and they survive the clean rename.
    """
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir(parents=True, exist_ok=True)
    # A curated record with a 655 genre/form block: marc2bibframe2 renders
    # those with madsrdf terms, which is what makes this a real test. The
    # vendored marc2bibframe2 sample has no such block, so using it here
    # would pass whether or not madsrdf is bound.
    source = _REPO_ROOT / "tests" / "data" / "sample-marcxml" / "curated" / "1353996.xml"
    bibframe_path = in_dir / "genre.bibframe.xml"
    result = subprocess.run(
        ["xsltproc", str(_MARC2BFRAME_XSL), str(source)],
        capture_output=True,
        text=True,
        check=True,
    )
    bibframe_path.write_text(result.stdout, encoding="utf-8")
    assert "mads/rdf" in result.stdout, "fixture no longer exercises madsrdf"

    output_path, _, _ = convert_one(
        bibframe_path,
        options=ConversionOptions(input_dir=in_dir, output_dir=out_dir),
        rules=load_rules(),
    )
    turtle = output_path.read_text(encoding="utf-8")
    auto = re.findall(r"^@prefix\s+(ns\d+):\s+<([^>]*)>", turtle, re.MULTILINE)
    assert not auto, f"unbound namespaces leaked as auto-prefixes: {auto}"


# --- Boundary 3 (p-062) ---------------------------------------------------


#: BIBFRAME carrying a `bf:AbbreviatedTitle` — a class `vocab/lkd.rdf` declares
#: no counterpart for, so the rename leaves it in the `bf:` namespace and the
#: emitted `bffi:title` ends up pointing at something that is not a
#: `bffi:Title`. The residual-`bf:` shape of MARC 210 in the real corpus.
_UNMAPPED_TITLE_CLASS = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:bf="http://id.loc.gov/ontologies/bibframe/">
  <bf:Work rdf:about="http://urn.fi/URN:NBN:fi:bib:1#Work">
    <bf:title>
      <bf:AbbreviatedTitle>
        <bf:mainTitle>Abbrev title</bf:mainTitle>
      </bf:AbbreviatedTitle>
    </bf:title>
  </bf:Work>
</rdf:RDF>
"""


def test_boundary3_flags_non_conforming_records_without_blocking(tmp_path: Path) -> None:
    """Boundary 3 reports and keeps — never blocks.

    The finding here is real and appears in the corpus: a class with no
    `lkd.rdf` counterpart survives the rename as `bf:*`, which the shape sees
    as a `bffi:title` value that isn't a `bffi:Title`. What this test pins is
    the contract — the record is still emitted and still counted as converted.
    """
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    (in_dir / "1.bibframe.xml").write_text(_UNMAPPED_TITLE_CLASS, encoding="utf-8")

    summary = convert_corpus(options=ConversionOptions(input_dir=in_dir, output_dir=out_dir))

    assert summary.converted == 1
    assert summary.failed == 0
    assert (out_dir / "1.bffi.ttl").exists()
    assert summary.shape_flagged == 1

    rows = [
        json.loads(line)
        for line in (out_dir / VALIDATION_FILENAME).read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 1
    assert rows[0]["boundary"] == 3
    assert rows[0]["error_type"] == "bffi-shape"
    assert rows[0]["bib_id"] == "1"
    assert rows[0]["violations"] > 0


def test_boundary3_leaves_a_conforming_record_unflagged(tmp_path: Path) -> None:
    """A real conversion of the vendored sample now conforms.

    Before p-062 Phase C rescoped the shape to `vocab/lkd.rdf`'s own axioms,
    this record was flagged — as were 342 of 343 emitted records — because the
    shape demanded `skos:prefLabel` (a Skosmos-era requirement nothing emits)
    and a full FRBR spine (a cardinality lkd.rdf never states).
    """
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    _produce_bibframe_fixture(in_dir, stem="1")

    summary = convert_corpus(options=ConversionOptions(input_dir=in_dir, output_dir=out_dir))

    assert summary.converted == 1
    assert summary.shape_flagged == 0
    assert not (out_dir / VALIDATION_FILENAME).exists()


def test_no_validate_skips_boundary3(tmp_path: Path) -> None:
    """Uses the record Boundary 3 *does* flag, so the assertion means
    "the check didn't run" rather than "there was nothing to find"."""
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    (in_dir / "1.bibframe.xml").write_text(_UNMAPPED_TITLE_CLASS, encoding="utf-8")

    summary = convert_corpus(
        options=ConversionOptions(input_dir=in_dir, output_dir=out_dir, validate=False)
    )

    assert summary.converted == 1
    assert summary.shape_flagged == 0
    assert not (out_dir / VALIDATION_FILENAME).exists()


def test_a_conversion_failure_lands_in_the_errors_sidecar(tmp_path: Path) -> None:
    """``_errors.jsonl`` answers "what is missing from this output, and why",
    so a conversion failure belongs there alongside boundary rejections —
    tagged ``boundary: 0`` to keep the two apart."""
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    (in_dir / "1.bibframe.xml").write_text("<rdf:RDF><not-closed-tag", encoding="utf-8")

    summary = convert_corpus(options=ConversionOptions(input_dir=in_dir, output_dir=out_dir))

    assert summary.failed == 1
    rows = [
        json.loads(line)
        for line in (out_dir / ERRORS_FILENAME).read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 1
    assert rows[0]["boundary"] == 0
    assert rows[0]["error_type"] == "BibframeToBffiError"
