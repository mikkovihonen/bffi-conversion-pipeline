"""Unit tests for per-record conversion provenance (p-060)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from rdflib import Graph, Literal

from bffi_pipeline.provenance import vocab as V
from bffi_pipeline.provenance.activities import (
    PROVENANCE_SUFFIX,
    activity_uri,
    build_conversion_activity,
    sidecar_path_for,
    write_record_provenance,
)

STARTED = datetime(2026, 8, 4, 10, 0, 0, tzinfo=UTC)
ENDED = datetime(2026, 8, 4, 10, 0, 5, tzinfo=UTC)


# --- URI + path helpers --------------------------------------------------


def test_activity_uri_is_deterministic_per_stage_and_record() -> None:
    a = activity_uri(stage="bibframe2bffi", bib_id="b11007849")
    assert str(a) == "http://urn.fi/URN:NBN:fi:bib:activity/bibframe2bffi/b11007849"
    assert a == activity_uri(stage="bibframe2bffi", bib_id="b11007849")
    # Different stage → different Activity for the same record.
    assert a != activity_uri(stage="marc2bibframe", bib_id="b11007849")


def test_sidecar_path_strips_every_output_suffix() -> None:
    """``b1.bffi.ttl`` must yield ``b1.prov.ttl``, not ``b1.bffi.prov.ttl``."""
    assert sidecar_path_for(Path("/out/b1.bffi.ttl")).name == f"b1{PROVENANCE_SUFFIX}"
    assert sidecar_path_for(Path("/out/b1.bibframe.xml")).name == f"b1{PROVENANCE_SUFFIX}"
    assert sidecar_path_for(Path("/out/b1.xml")).name == f"b1{PROVENANCE_SUFFIX}"


def test_sidecar_lands_beside_the_record_output() -> None:
    assert sidecar_path_for(Path("/runs/r/bffi/b1.bffi.ttl")).parent == Path("/runs/r/bffi")


# --- Activity construction ----------------------------------------------


def test_activity_carries_type_stage_bib_id_and_timestamps() -> None:
    g = Graph()
    activity = build_conversion_activity(
        g, stage="marc2bibframe", bib_id="b1", started=STARTED, ended=ENDED
    )
    assert (activity, V.RDF.type, V.PROV.Activity) in g
    assert (activity, V.RDF.type, V.MarcConversion) in g
    assert (activity, V.stage, Literal("marc2bibframe")) in g
    assert (activity, V.localBibId, Literal("b1")) in g
    assert str(next(g.objects(activity, V.PROV.startedAtTime))) == STARTED.isoformat()
    assert str(next(g.objects(activity, V.PROV.endedAtTime))) == ENDED.isoformat()


def test_only_non_zero_decisions_are_recorded() -> None:
    """A triple per zero-count routing would add ~30 triples per record
    stating that nothing happened."""
    g = Graph()
    activity = build_conversion_activity(
        g,
        stage="bibframe2bffi",
        bib_id="b1",
        started=STARTED,
        ended=ENDED,
        decisions={"hub": 2, "title_variant": 0, "work_split": 1},
    )
    decisions = {str(o) for o in g.objects(activity, V.decision)}
    assert decisions == {"hub=2", "work_split=1"}


def test_used_and_generated_are_recorded_as_file_uris(tmp_path: Path) -> None:
    source = tmp_path / "in.xml"
    source.write_text("<x/>", encoding="utf-8")
    out = tmp_path / "out.ttl"
    out.write_text("", encoding="utf-8")
    g = Graph()
    activity = build_conversion_activity(
        g,
        stage="marc2bibframe",
        bib_id="b1",
        started=STARTED,
        ended=ENDED,
        used=source,
        generated=out,
    )
    assert str(next(g.objects(activity, V.PROV.used))).startswith("file://")
    assert str(next(g.objects(activity, V.PROV.generated))).endswith("out.ttl")


def test_converter_version_is_optional() -> None:
    g = Graph()
    activity = build_conversion_activity(
        g,
        stage="marc2bibframe",
        bib_id="b1",
        started=STARTED,
        ended=ENDED,
        converter_version="bffi-pipeline/0.1.0",
    )
    assert (activity, V.converterVersion, Literal("bffi-pipeline/0.1.0")) in g
    bare = Graph()
    build_conversion_activity(
        bare, stage="marc2bibframe", bib_id="b2", started=STARTED, ended=ENDED
    )
    assert not list(bare.objects(None, V.converterVersion))


# --- Sidecar writing -----------------------------------------------------


def test_write_record_provenance_emits_parseable_turtle(tmp_path: Path) -> None:
    out = tmp_path / "b1.bffi.ttl"
    out.write_text("", encoding="utf-8")
    sidecar = write_record_provenance(
        out,
        stage="bibframe2bffi",
        bib_id="b1",
        started=STARTED,
        ended=ENDED,
        decisions={"hub": 1},
    )
    assert sidecar.is_file()
    g = Graph()
    g.parse(sidecar, format="turtle")
    assert any(g.triples((None, V.RDF.type, V.MarcConversion)))
    assert (None, V.decision, Literal("hub=1")) in g


def test_sidecar_uses_the_shared_prefix_bindings(tmp_path: Path) -> None:
    """Written through ProvenanceWriter, so no rdflib ``ns1:`` placeholders."""
    out = tmp_path / "b1.bffi.ttl"
    out.write_text("", encoding="utf-8")
    sidecar = write_record_provenance(
        out, stage="bibframe2bffi", bib_id="b1", started=STARTED, ended=ENDED
    )
    text = sidecar.read_text(encoding="utf-8")
    assert "@prefix bffi-prov:" in text
    assert "ns1:" not in text


def test_rerun_replaces_rather_than_duplicates(tmp_path: Path) -> None:
    """A second conversion of the same record must leave one Activity with
    the newer decision set, not two stacked Activities."""
    out = tmp_path / "b1.bffi.ttl"
    out.write_text("", encoding="utf-8")
    for decisions in ({"hub": 1}, {"work_split": 2}):
        sidecar = write_record_provenance(
            out,
            stage="bibframe2bffi",
            bib_id="b1",
            started=STARTED,
            ended=ENDED,
            decisions=decisions,
        )
    g = Graph()
    g.parse(sidecar, format="turtle")
    assert len(list(g.subjects(V.RDF.type, V.MarcConversion))) == 1
    assert {str(o) for o in g.objects(None, V.decision)} == {"work_split=2"}
