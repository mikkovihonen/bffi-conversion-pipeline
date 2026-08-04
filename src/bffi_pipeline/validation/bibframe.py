"""Boundary 2: SHACL validation of the post-XSLT BIBFRAME graph.

The shape lives at ``config/shapes/bibframe-conversion.shape.ttl`` and is
loaded once per process. Failures are reported as
``error_type: "bibframe-shape"`` per stage ``marc-to-bibframe``.

One Boundary-2 check is **not** in the shape file:
:func:`missing_root_resources`. "The graph contains at least one Work" is a
statement about absence, and SHACL constrains focus nodes — with no Work in
the graph there is no focus node to fail. An empty or Work-less conversion
is also the failure most worth catching (it is what a stylesheet that
matched nothing produces), so it gets an explicit check rather than being
left to a shape that structurally cannot express it.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pyshacl import validate as pyshacl_validate
from rdflib import RDF, Graph, Namespace, URIRef

from bffi_pipeline.config import get_settings

#: The BIBFRAME namespace. Boundary 2 runs *before* the hard cut to
#: ``bffi:``, so this module is one of the few that legitimately names
#: ``bf:*`` terms — its whole job is validating BIBFRAME input.
BF = Namespace("http://id.loc.gov/ontologies/bibframe/")


class BibframeShapeError(Exception):
    """Boundary-2 SHACL failure. Exposes the human-readable conformance report."""

    def __init__(self, *, message: str, report_text: str, path: Path) -> None:
        super().__init__(message)
        self.message = message
        self.report_text = report_text
        self.path = path

    def __str__(self) -> str:
        return f"[bibframe-shape] {self.path.name}: {self.message}"


@dataclass(frozen=True)
class ShapeReport:
    """Conformance result; ``conforms=False`` means at least one shape failed."""

    conforms: bool
    text: str


def shape_path() -> Path:
    """Return the on-disk path of the bibframe-conversion SHACL shape."""
    return get_settings().config_dir / "shapes" / "bibframe-conversion.shape.ttl"


@lru_cache(maxsize=1)
def _shape_graph() -> Graph:
    g = Graph()
    g.parse(str(shape_path()), format="turtle")
    return g


def missing_root_resources(data: Graph) -> str | None:
    """Return a message if ``data`` lacks the IRI Work / Instance pair.

    ``None`` means both are present. A conversion that produced neither is
    an `xsltproc` run that matched nothing useful — valid RDF/XML holding no
    bibliographic resource. Blank-node Works don't count: marc2bibframe2
    uses those for internal sub-components, and a record whose only Work is
    a blank node has no addressable resource for the BFFI emit to key on.
    """
    works = any(isinstance(s, URIRef) for s in data.subjects(RDF.type, BF.Work))
    instances = any(isinstance(s, URIRef) for s in data.subjects(RDF.type, BF.Instance))
    if works and instances:
        return None
    absent = [
        name for name, present in (("bf:Work", works), ("bf:Instance", instances)) if not present
    ]
    return "Converted graph contains no " + " and no ".join(absent) + "."


def validate_graph(data: Graph, *, source_path: Path) -> ShapeReport:
    """Run SHACL on ``data``; return a typed report (no exception on failure)."""
    conforms, _, report_text = pyshacl_validate(
        data,
        shacl_graph=_shape_graph(),
        inference="none",
        meta_shacl=False,
        advanced=True,
        debug=False,
    )
    report = ShapeReport(conforms=bool(conforms), text=str(report_text))
    return report


def assert_conforms(data: Graph, *, source_path: Path) -> None:
    """Run SHACL and raise :class:`BibframeShapeError` if non-conforming."""
    report = validate_graph(data, source_path=source_path)
    if not report.conforms:
        raise BibframeShapeError(
            message="BIBFRAME post-conversion shape failed.",
            report_text=report.text,
            path=source_path,
        )


__all__ = [
    "BF",
    "BibframeShapeError",
    "ShapeReport",
    "assert_conforms",
    "missing_root_resources",
    "shape_path",
    "validate_graph",
]
