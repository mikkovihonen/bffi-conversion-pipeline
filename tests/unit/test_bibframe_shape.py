"""Unit tests for the Boundary-2 BIBFRAME shape (p-062 Phase B).

`docs/validation-strategy.md` asks for a valid/invalid fixture pair per
shape, and this boundary rejects records, so the invalid half is the
important one: a shape that never fails costs nothing to pass and catches
nothing. Each test removes exactly one thing from a real converted graph and
asserts the shape notices.

The baseline graph is produced by running the vendored LoC sample through the
vendored stylesheet, so the "valid" side is real conversion output rather
than a hand-rolled graph that might agree with the shape by construction.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from rdflib import RDF, BNode, Graph, Literal, URIRef

from bffi_pipeline.validation.bibframe import (
    BF,
    missing_root_resources,
    shape_path,
    validate_graph,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SAMPLE_MARC = _REPO_ROOT / "third_party" / "marc2bibframe2" / "test" / "data" / "marc.xml"
_MARC2BFRAME_XSL = _REPO_ROOT / "third_party" / "marc2bibframe2" / "xsl" / "marc2bibframe2.xsl"


@pytest.fixture(scope="module")
def converted() -> str:
    """RDF/XML from one real conversion, as text so each test re-parses it."""
    result = subprocess.run(
        ["xsltproc", str(_MARC2BFRAME_XSL), str(_SAMPLE_MARC)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _graph(rdf_xml: str) -> Graph:
    g = Graph()
    g.parse(data=rdf_xml, format="xml")
    return g


def _conforms(graph: Graph) -> bool:
    return validate_graph(graph, source_path=Path("in-memory")).conforms


def _main_work(graph: Graph) -> URIRef:
    return next(
        s
        for s in graph.subjects(RDF.type, BF.Work)
        if isinstance(s, URIRef) and str(s).endswith("#Work")
    )


def _an_instance(graph: Graph) -> URIRef:
    return next(s for s in graph.subjects(RDF.type, BF.Instance) if isinstance(s, URIRef))


def test_the_shape_file_is_where_the_module_says_it_is() -> None:
    assert shape_path().is_file()


def test_real_conversion_output_conforms(converted: str) -> None:
    """The valid half of the pair.

    Verified more broadly than this single record when the shape was written:
    515 converted records — 25 curated real fixtures, 319 field-coverage
    probes, 171 mixed fixtures — conform. This test keeps that property from
    regressing on a shape edit or a submodule bump.
    """
    assert _conforms(_graph(converted))


def test_a_work_without_a_title_fails(converted: str) -> None:
    graph = _graph(converted)
    work = _main_work(graph)
    for title in list(graph.objects(work, BF.title)):
        graph.remove((work, BF.title, title))

    assert not _conforms(graph)


def test_an_empty_main_title_fails(converted: str) -> None:
    """Present-but-empty is the sneakier failure: a `bf:title` node with an
    empty `bf:mainTitle` satisfies a plain `minCount` check."""
    graph = _graph(converted)
    work = _main_work(graph)
    for title in graph.objects(work, BF.title):
        for main in list(graph.objects(title, BF.mainTitle)):
            graph.remove((title, BF.mainTitle, main))
            graph.add((title, BF.mainTitle, Literal("")))

    assert not _conforms(graph)


def test_an_instance_without_instance_of_fails(converted: str) -> None:
    graph = _graph(converted)
    instance = _an_instance(graph)
    for work in list(graph.objects(instance, BF.instanceOf)):
        graph.remove((instance, BF.instanceOf, work))

    assert not _conforms(graph)


def test_a_main_work_without_admin_metadata_fails(converted: str) -> None:
    graph = _graph(converted)
    work = _main_work(graph)
    for block in list(graph.objects(work, BF.adminMetadata)):
        graph.remove((work, BF.adminMetadata, block))

    assert not _conforms(graph)


def test_a_related_work_without_admin_metadata_still_conforms(converted: str) -> None:
    """The admin-block shape targets the main Work only.

    Related and contained Works (MARC 700 ind2=2, 740, 776) legitimately
    carry no administrative layer — 467/515 records when every IRI Work is
    targeted. Widening that target would reject a twelfth of the corpus for
    being correct.
    """
    graph = _graph(converted)
    related = URIRef("http://example.invalid/related#Work700-1")
    graph.add((related, RDF.type, BF.Work))
    title = URIRef("http://example.invalid/related#Title")
    graph.add((related, BF.title, title))
    graph.add((title, BF.mainTitle, Literal("Contained work")))

    assert _conforms(graph)


def test_a_blank_node_work_is_not_targeted(converted: str) -> None:
    """marc2bibframe2 uses blank-node Works for internal sub-components and
    never titles them; targeting them would fail records that are fine."""
    graph = _graph(converted)
    graph.add((BNode(), RDF.type, BF.Work))

    assert _conforms(graph)


# --- the absence check SHACL can't express -------------------------------


def test_missing_root_resources_passes_a_real_graph(converted: str) -> None:
    assert missing_root_resources(_graph(converted)) is None


def test_an_empty_graph_is_caught_by_the_absence_check() -> None:
    """SHACL calls an empty graph conforming — no focus nodes, no failures.

    That is exactly what a stylesheet run matching nothing produces, so the
    check has to live outside the shape file.
    """
    empty = Graph()
    assert _conforms(empty)
    assert missing_root_resources(empty) == (
        "Converted graph contains no bf:Work and no bf:Instance."
    )


def test_a_work_without_an_instance_is_caught() -> None:
    graph = Graph()
    work = URIRef("http://example.invalid/1#Work")
    graph.add((work, RDF.type, BF.Work))
    assert missing_root_resources(graph) == "Converted graph contains no bf:Instance."


def test_a_blank_node_work_does_not_satisfy_the_absence_check() -> None:
    """A record whose only Work is a blank node has no addressable resource
    for the BFFI emit to key on, so it counts as absent."""
    graph = Graph()
    graph.add((BNode(), RDF.type, BF.Work))
    graph.add((BNode(), RDF.type, BF.Instance))
    assert missing_root_resources(graph) is not None
