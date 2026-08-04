"""Unit tests for the provenance vocabulary and Turtle writer.

The vocabulary covers this repository's stages only — the
``MarcConversion`` and ``Synthesis`` Activity classes, the stage /
decision audit pair, and the AdminMetadata description terms. These
tests pin the terms that must survive a Turtle round-trip and the
writer's append-on-reopen contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rdflib import Graph, Literal, URIRef

from bffi_pipeline.provenance import vocab as V
from bffi_pipeline.provenance import writer as W

# --- Synthesis vocabulary ------------------------------------------------


def test_synthesis_activity_terms_round_trip_via_turtle(tmp_path: Path) -> None:
    """The four ``synthetic*`` predicates + the Synthesis Activity class +
    the ``bffi-prov:syntheticSentinel`` flag + the
    ``SENTINEL_AGENT_UNKNOWN`` URI must survive a Turtle write/read cycle
    without rdflib mangling."""
    g = Graph()
    activity = URIRef("http://urn.fi/URN:NBN:fi:bib:activity/synthesis-test-1")
    source_marc = URIRef("http://urn.fi/URN:NBN:fi:bib:activity/marc-conv-test-1")
    contribution = URIRef("http://urn.fi/URN:NBN:fi:bib:contribution/test-1")
    g.add((activity, V.RDF.type, V.PROV.Activity))
    g.add((activity, V.RDF.type, V.Synthesis))
    g.add((activity, V.PROV.used, source_marc))
    g.add((activity, V.PROV.generated, contribution))
    g.add((activity, V.syntheticField, Literal("bf:contribution/bf:agent")))
    g.add((activity, V.syntheticMethod, Literal("creator-from-245c (regex)")))
    g.add((activity, V.syntheticTier, Literal("B1")))
    g.add((activity, V.syntheticConfidence, Literal("0.65", datatype=V.XSD.decimal)))
    g.add((V.SENTINEL_AGENT_UNKNOWN, V.syntheticSentinel, Literal("true", datatype=V.XSD.boolean)))

    out = tmp_path / "synthesis.ttl"
    out.write_text(g.serialize(format="turtle"), encoding="utf-8")
    reloaded = Graph()
    reloaded.parse(str(out), format="turtle")

    types = {str(o) for _, _, o in reloaded.triples((activity, V.RDF.type, None))}
    assert str(V.PROV.Activity) in types
    assert str(V.Synthesis) in types
    assert (activity, V.PROV.used, source_marc) in reloaded
    assert (activity, V.PROV.generated, contribution) in reloaded
    assert any(reloaded.triples((activity, V.syntheticField, None)))
    assert any(reloaded.triples((activity, V.syntheticMethod, None)))
    tier_value = next(reloaded.objects(activity, V.syntheticTier))
    assert str(tier_value) == "B1"
    confidence_value = next(reloaded.objects(activity, V.syntheticConfidence))
    assert float(str(confidence_value)) == pytest.approx(0.65)
    sentinel_value = next(reloaded.objects(V.SENTINEL_AGENT_UNKNOWN, V.syntheticSentinel))
    assert str(sentinel_value).lower() == "true"


def test_sentinel_agent_unknown_uri_matches_committed_identifier() -> None:
    """The sentinel agent URI is a committed identifier
    (see ``CLAUDE.md`` § "Committed identifiers"). Pin its value here
    so a refactor that touches the namespace surfacing won't silently
    rename it without surfacing the change."""
    assert str(V.SENTINEL_AGENT_UNKNOWN) == "http://urn.fi/URN:NBN:fi:bib:agent:unknown"


def test_marc_conversion_terms_round_trip_via_turtle(tmp_path: Path) -> None:
    """The conversion-side Activity class and its audit predicates
    (``stage``, ``decision``, ``localBibId``, ``fromMarcField``) survive a
    Turtle round-trip."""
    g = Graph()
    activity = URIRef("http://urn.fi/URN:NBN:fi:bib:activity/marc-conv-test-1")
    g.add((activity, V.RDF.type, V.PROV.Activity))
    g.add((activity, V.RDF.type, V.MarcConversion))
    g.add((activity, V.stage, Literal("marc2bibframe")))
    g.add((activity, V.decision, Literal("hub_routed_work")))
    g.add((activity, V.localBibId, Literal("b11007849")))
    g.add((activity, V.fromMarcField, Literal("b11007849:245:1")))

    out = tmp_path / "conversion.ttl"
    out.write_text(g.serialize(format="turtle"), encoding="utf-8")
    reloaded = Graph()
    reloaded.parse(str(out), format="turtle")

    types = {str(o) for _, _, o in reloaded.triples((activity, V.RDF.type, None))}
    assert str(V.MarcConversion) in types
    assert str(next(reloaded.objects(activity, V.stage))) == "marc2bibframe"
    assert str(next(reloaded.objects(activity, V.decision))) == "hub_routed_work"
    assert str(next(reloaded.objects(activity, V.localBibId))) == "b11007849"
    assert str(next(reloaded.objects(activity, V.fromMarcField))) == "b11007849:245:1"


def test_downstream_only_terms_are_absent() -> None:
    """The vocabulary covers this pipeline's stages only. Terms for
    clustering, LLM judging, human review, authority reconciliation and
    the canonical Work merge must not reappear — re-adding one means a
    downstream stage crept back into this repository."""
    for name in (
        "WorkMergeDecision",
        "HumanReview",
        "Reconciliation",
        "mintAnchor",
        "rawResponse",
        "modelId",
        "embeddingSimilarity",
        "chosenAuthorityUri",
        "reviewNote",
    ):
        assert not hasattr(V, name), f"downstream term {name} is back in provenance.vocab"


# --- ProvenanceWriter -----------------------------------------------------


def test_provenance_writer_round_trips_via_turtle(tmp_path: Path) -> None:
    out = tmp_path / "provenance.ttl"
    activity = URIRef("http://urn.fi/URN:NBN:fi:bib:activity/marc-conv-1")
    with W.ProvenanceWriter(out) as writer:
        writer.graph.add((activity, V.RDF.type, V.PROV.Activity))
        writer.graph.add((activity, V.RDF.type, V.MarcConversion))
        writer.graph.add((activity, V.stage, Literal("marc2bibframe")))
    assert out.is_file()
    g = Graph()
    g.parse(str(out), format="turtle")
    assert any(g.triples((None, V.RDF.type, V.MarcConversion)))


def test_provenance_writer_appends_to_existing_file(tmp_path: Path) -> None:
    out = tmp_path / "provenance.ttl"
    for n in (1, 2):
        activity = URIRef(f"http://urn.fi/URN:NBN:fi:bib:activity/marc-conv-{n}")
        with W.ProvenanceWriter(out) as writer:
            writer.graph.add((activity, V.RDF.type, V.MarcConversion))
            writer.graph.add((activity, V.stage, Literal("marc2bibframe")))
    g = Graph()
    g.parse(str(out), format="turtle")
    assert len(list(g.subjects(V.RDF.type, V.MarcConversion))) == 2


def test_provenance_writer_binds_canonical_prefixes(tmp_path: Path) -> None:
    """Serialised output must use the shared prefix bindings, never
    rdflib's auto-generated ``ns1:`` placeholders."""
    out = tmp_path / "provenance.ttl"
    activity = URIRef("http://urn.fi/URN:NBN:fi:bib:activity/marc-conv-1")
    with W.ProvenanceWriter(out) as writer:
        writer.graph.add((activity, V.RDF.type, V.MarcConversion))
    text = out.read_text(encoding="utf-8")
    assert "@prefix bffi-prov:" in text
    assert "ns1:" not in text
