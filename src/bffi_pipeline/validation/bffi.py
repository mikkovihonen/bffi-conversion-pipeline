"""Boundary 3: SHACL validation of the emitted BFFI graph.

The shape lives at ``config/shapes/bffi.shape.ttl``. Per stage
``bibframe-to-bffi``, failures are non-blocking: the stage continues,
records are flagged in ``_validation.jsonl`` and a summary count is
surfaced on the CLI.

**``vocab/lkd.rdf`` is passed to pyshacl as the ontology graph.** Every
constraint in the shape restates an axiom from lkd.rdf, so lkd.rdf has to
be in scope when they run. SHACL's ``sh:class`` tests membership by walking
``rdf:type/rdfs:subClassOf*`` **in the graph it can see** — and the
subclass axioms live in the ontology, not in a converted record. Without
lkd.rdf mixed in, a perfectly correct ``bffi:Local`` identifier fails a
``sh:class bffi:Identifier`` check even though lkd.rdf declares
``bffi:Local rdfs:subClassOf bffi:Identifier``: 348 phantom violations
across 343 records, all of them the validator's fault rather than the
converter's.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pyshacl import validate as pyshacl_validate
from rdflib import Graph

from bffi_pipeline.config import get_settings


def shape_path() -> Path:
    """Return the on-disk path of the bffi SHACL shape."""
    return get_settings().config_dir / "shapes" / "bffi.shape.ttl"


@lru_cache(maxsize=1)
def _shape_graph() -> Graph:
    g = Graph()
    g.parse(str(shape_path()), format="turtle")
    return g


def lkd_path() -> Path:
    """Return the on-disk path of the vendored BFFI ontology."""
    return get_settings().vocab_dir / "lkd.rdf"


@lru_cache(maxsize=1)
def _ontology_graph() -> Graph:
    """The vendored BFFI ontology, parsed once per process.

    Supplies the ``rdfs:subClassOf`` axioms ``sh:class`` needs; see the
    module docstring for what happens without them.
    """
    g = Graph()
    g.parse(str(lkd_path()), format="xml")
    return g


@dataclass(frozen=True)
class ShapeReport:
    """Conformance result and the human-readable conformance text."""

    conforms: bool
    text: str


def validate_graph(data: Graph) -> ShapeReport:
    """Run SHACL on ``data`` and return a conformance report (no raise)."""
    conforms, _, report_text = pyshacl_validate(
        data,
        shacl_graph=_shape_graph(),
        ont_graph=_ontology_graph(),
        inference="none",
        meta_shacl=False,
        advanced=True,
        debug=False,
    )
    return ShapeReport(conforms=bool(conforms), text=str(report_text))


__all__ = ["ShapeReport", "shape_path", "validate_graph"]
