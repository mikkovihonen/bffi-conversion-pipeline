"""Unit tests for the Boundary-3 BFFI shape (p-062 Phase C).

Every constraint in `config/shapes/bffi.shape.ttl` restates an axiom from
`vocab/lkd.rdf`, so these tests come in two kinds:

1. **The axiom is really in lkd.rdf.** A constraint the ontology doesn't
   declare is a local invention wearing a shape's clothes — which is what
   the previous version of this shape was full of.
2. **The constraint fires on a violation and not otherwise.** Including the
   two cases that made the old shape useless: a `bffi:Local` identifier is a
   valid `bffi:Identifier` value (needs lkd.rdf in scope), and an untyped
   external URI does not violate an `rdfs:range`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rdflib import RDF, RDFS, BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, SKOS

from bffi_pipeline.validation.bffi import lkd_path, shape_path, validate_graph

BFFI = Namespace("http://urn.fi/URN:NBN:fi:schema:bffi:")
BF = Namespace("http://id.loc.gov/ontologies/bibframe/")
BIB = Namespace("http://urn.fi/URN:NBN:fi:bib:")


@pytest.fixture(scope="module")
def ontology() -> Graph:
    g = Graph()
    g.parse(lkd_path(), format="xml")
    return g


def _conforms(graph: Graph) -> bool:
    return validate_graph(graph).conforms


def _messages(graph: Graph) -> str:
    return validate_graph(graph).text


def _record() -> Graph:
    """A minimal conforming record: Manifestation, Work, and a titled Title."""
    g = Graph()
    work = BIB["1#Work"]
    manifestation = BIB["1#Instance"]
    title = BNode()
    g.add((work, RDF.type, BFFI.Work))
    g.add((manifestation, RDF.type, BFFI.Manifestation))
    g.add((manifestation, BFFI.title, title))
    g.add((title, RDF.type, BFFI.Title))
    g.add((title, BFFI.mainTitle, Literal("A title")))
    return g


# --- the constraints are lkd.rdf's, not ours -----------------------------


def test_the_shape_and_the_ontology_are_both_on_disk() -> None:
    assert shape_path().is_file()
    assert lkd_path().is_file()


def test_work_and_expression_disjointness_is_declared_in_lkd_rdf(ontology: Graph) -> None:
    assert (BFFI.Work, OWL.disjointWith, BFFI.Expression) in ontology


@pytest.mark.parametrize(
    ("predicate", "domain"),
    [
        ("content", "Expression"),
        ("summary", "Expression"),
        ("subject", "Work"),
        ("classification", "Work"),
        ("originDate", "Work"),
        ("genreForm", "Work"),
        ("hasExpression", "Work"),
        ("expressionOf", "Expression"),
        ("expressionManifested", "Manifestation"),
        ("mainTitle", "Title"),
    ],
)
def test_each_asserted_domain_comes_from_lkd_rdf(
    ontology: Graph, predicate: str, domain: str
) -> None:
    assert (BFFI[predicate], RDFS.domain, BFFI[domain]) in ontology


@pytest.mark.parametrize(
    ("predicate", "range_"),
    [
        ("title", "Title"),
        ("identifiedBy", "Identifier"),
        ("source", "Source"),
        ("note", "Note"),
        ("adminMetadata", "AdminMetadata"),
        ("hasExpression", "Expression"),
        ("expressionOf", "Work"),
        ("expressionManifested", "Expression"),
    ],
)
def test_each_asserted_range_comes_from_lkd_rdf(
    ontology: Graph, predicate: str, range_: str
) -> None:
    assert (BFFI[predicate], RDFS.range, BFFI[range_]) in ontology


@pytest.mark.parametrize("predicate", ["language", "note", "identifiedBy"])
def test_the_predicates_we_stopped_restricting_are_open_in_lkd_rdf(
    ontology: Graph, predicate: str
) -> None:
    """The old shape confined these to one FRBR axis. lkd.rdf doesn't.

    This is the test that would have caught the invention: `rdfs:domain
    rdfs:Resource` means "anything", and a shape claiming otherwise is
    asserting a rule the ontology never made.
    """
    assert (BFFI[predicate], RDFS.domain, RDFS.Resource) in ontology
    assert (BFFI[predicate], RDFS.domain, BFFI.Expression) not in ontology
    assert (BFFI[predicate], RDFS.domain, BFFI.Manifestation) not in ontology


def test_skos_preflabel_is_not_a_bffi_term(ontology: Graph) -> None:
    """The removed Skosmos-era constraint. Nothing in lkd.rdf asks for it and
    no stage emits it."""
    assert (BFFI.prefLabel, None, None) not in ontology
    assert not any(ontology.triples((None, RDFS.subPropertyOf, SKOS.prefLabel)))


# --- the constraints fire, and only when they should ---------------------


def test_a_minimal_record_conforms() -> None:
    assert _conforms(_record())


def test_a_node_typed_both_work_and_expression_fails() -> None:
    g = _record()
    g.add((BIB["1#Work"], RDF.type, BFFI.Expression))
    assert not _conforms(g)


def test_a_work_domain_predicate_on_a_manifestation_fails() -> None:
    """The wrong-FRBR-axis family from `docs/roundtrip-debugging.md`."""
    g = _record()
    g.add((BIB["1#Instance"], BFFI.genreForm, URIRef("http://example.invalid/genre")))
    assert not _conforms(g)
    assert "genreForm" in _messages(g) or "Work-domain" in _messages(g)


def test_the_same_predicate_on_a_work_conforms() -> None:
    g = _record()
    g.add((BIB["1#Work"], BFFI.genreForm, URIRef("http://example.invalid/genre")))
    assert _conforms(g)


def test_a_subclass_identifier_value_conforms() -> None:
    """`bffi:Local` is a `bffi:Identifier` because lkd.rdf says so.

    This is the case that produced 348 phantom violations across 343 records
    before `validate_graph` passed lkd.rdf to pyshacl as the ontology graph:
    `sh:class` walks `rdfs:subClassOf` in the graph it can see, and the
    subclass axioms live in the ontology, not in a converted record.
    """
    g = _record()
    identifier = BNode()
    g.add((BIB["1#Instance"], BFFI.identifiedBy, identifier))
    g.add((identifier, RDF.type, BFFI.Local))
    g.add((identifier, RDF.value, Literal("b11007849")))
    assert _conforms(g)


def test_an_untyped_range_value_conforms() -> None:
    """`rdfs:range` infers a type, it doesn't forbid the absence of one.

    90 of the emit's `bffi:source` values are bare authority URIs with no
    `rdf:type`. Flagging those would report standard practice as a defect.
    """
    g = _record()
    identifier = BNode()
    g.add((BIB["1#Instance"], BFFI.identifiedBy, identifier))
    g.add((identifier, RDF.type, BFFI.Identifier))
    g.add((identifier, BFFI.source, URIRef("http://id.loc.gov/vocabulary/organizations/fi-nl")))
    assert _conforms(g)


def test_a_contradictorily_typed_range_value_fails() -> None:
    """Typed as something else *does* contradict the range."""
    g = _record()
    identifier = BNode()
    g.add((BIB["1#Instance"], BFFI.identifiedBy, identifier))
    g.add((identifier, RDF.type, BFFI.Identifier))
    source = BNode()
    g.add((identifier, BFFI.source, source))
    g.add((source, RDF.type, BFFI.Title))
    assert not _conforms(g)


def test_a_literal_where_a_title_node_belongs_fails() -> None:
    g = _record()
    g.add((BIB["1#Instance"], BFFI.title, Literal("A bare literal title")))
    assert not _conforms(g)


def test_expression_of_pointing_at_an_expression_fails() -> None:
    """A real finding from the corpus: 13 `bffi:expressionOf` values across
    343 emitted records point at a node typed `bffi:Expression` rather than a
    Work — an Expression claiming another Expression as its Work."""
    g = _record()
    expression = BIB["1#Expression"]
    other = BIB["1#Hub240-15"]
    g.add((expression, RDF.type, BFFI.Expression))
    g.add((other, RDF.type, BFFI.Expression))
    g.add((expression, BFFI.expressionOf, other))
    assert not _conforms(g)


def test_expression_of_pointing_at_a_work_conforms() -> None:
    g = _record()
    expression = BIB["1#Expression"]
    g.add((expression, RDF.type, BFFI.Expression))
    g.add((expression, BFFI.expressionOf, BIB["1#Work"]))
    assert _conforms(g)


def test_a_missing_frbr_spine_is_not_a_violation() -> None:
    """The removed cardinality mandate.

    A Work with no `bffi:hasExpression` conforms: lkd.rdf declares the
    predicate's domain and range, not that every Work must have one. 187 of
    225 Works in the curated emit are MARC 700/730/76X-derived hubs with no
    expression of their own, and failing them said nothing about quality.
    """
    g = _record()
    assert not list(g.objects(BIB["1#Work"], BFFI.hasExpression))
    assert _conforms(g)


def test_a_residual_bf_type_is_reported_via_the_range_check() -> None:
    """The hard-cut namespace boundary, caught from the other direction.

    `bf:AbbreviatedTitle` survives into 2 of 343 emitted records (MARC 210
    probes) because lkd.rdf declares no counterpart. The closed-namespace
    residue counter already counts those records; this shape names the
    predicate, because a `bf:`-typed node is not a `bffi:Title`.
    """
    g = _record()
    abbreviated = BNode()
    g.add((BIB["1#Work"], BFFI.title, abbreviated))
    g.add((abbreviated, RDF.type, BF.AbbreviatedTitle))
    g.add((abbreviated, BFFI.mainTitle, Literal("Z210a")))
    assert not _conforms(g)


def test_the_shape_reports_a_violation_count_the_stage_can_use() -> None:
    """`bibframe-to-bffi` puts this number in `_validation.jsonl`."""
    g = _record()
    g.add((BIB["1#Instance"], BFFI.genreForm, URIRef("http://example.invalid/genre")))
    assert _messages(g).count("Constraint Violation") >= 1


def test_shape_path_points_inside_the_config_dir() -> None:
    assert shape_path().parent.name == "shapes"
    assert Path(shape_path()).name == "bffi.shape.ttl"
