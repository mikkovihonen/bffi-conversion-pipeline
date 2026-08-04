"""Conversion-Activity writers for the per-record provenance sidecar.

``CLAUDE.md`` makes provenance mandatory: every conversion decision that
does anything non-trivial writes to the provenance graph before
returning. This module is that write path for the forward direction.

**Per-record sidecar.** Each converter writes ``<stem>.prov.ttl`` beside
the record's own output rather than accumulating one graph for the whole
corpus. At the 800k-record target a single in-memory graph would hold
~5M+ triples; per-record files are O(1) in memory and match the existing
per-record artifact convention. Concatenate the sidecars (or load them
into a store) to get one graph.

Shape of one sidecar::

    <bib:activity/bibframe2bffi/b11007849>
        a prov:Activity, bffi-prov:MarcConversion ;
        bffi-prov:stage      "bibframe2bffi" ;
        bffi-prov:localBibId "b11007849" ;
        prov:startedAtTime   "..."^^xsd:dateTime ;
        prov:endedAtTime     "..."^^xsd:dateTime ;
        prov:used            <file:///...> ;
        prov:generated       <file:///...> ;
        bffi-prov:decision   "hub_routed_work=3" .

Activity URIs are deterministic (``bib:activity/<stage>/<bib_id>``) so
re-runs stay diffable. ``CLAUDE.md`` permits UUIDs for ``prov:Activity``
but does not require them.

The sidecars are *not* byte-deterministic — they carry wall-clock
timestamps. That is inherent to provenance and scoped to these files; the
conversion outputs themselves remain deterministic.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from rdflib import Graph, Literal, URIRef

from bffi_pipeline.provenance import vocab as V
from bffi_pipeline.provenance.writer import ProvenanceWriter

#: Suffix for the per-record provenance sidecar.
PROVENANCE_SUFFIX = ".prov.ttl"


def now() -> datetime:
    """Current UTC instant. Indirected so tests can freeze it."""
    return datetime.now(UTC)


def activity_uri(*, stage: str, bib_id: str) -> URIRef:
    """Deterministic Activity URI for one (stage, record) pair."""
    return V.BIB[f"activity/{stage}/{bib_id}"]


def sidecar_path_for(output_path: Path) -> Path:
    """Return the ``.prov.ttl`` path beside ``output_path``.

    Strips every suffix of the record's own output so
    ``b1.bffi.ttl`` and ``b1.bibframe.xml`` both yield ``b1.prov.ttl``
    rather than ``b1.bffi.prov.ttl``.
    """
    stem = output_path.name.split(".", 1)[0]
    return output_path.parent / f"{stem}{PROVENANCE_SUFFIX}"


def build_conversion_activity(
    graph: Graph,
    *,
    stage: str,
    bib_id: str,
    started: datetime,
    ended: datetime,
    used: Path | None = None,
    generated: Path | None = None,
    decisions: Mapping[str, int] | None = None,
    converter_version: str | None = None,
) -> URIRef:
    """Add one conversion Activity to ``graph`` and return its URI.

    ``decisions`` is the stage's routing-counter mapping. Only non-zero
    entries are recorded — a triple per zero-count routing would add ~30
    triples per record stating that nothing happened.
    """
    activity = activity_uri(stage=stage, bib_id=bib_id)
    graph.add((activity, V.RDF.type, V.PROV.Activity))
    graph.add((activity, V.RDF.type, V.MarcConversion))
    graph.add((activity, V.stage, Literal(stage)))
    graph.add((activity, V.localBibId, Literal(bib_id)))
    graph.add(
        (activity, V.PROV.startedAtTime, Literal(started.isoformat(), datatype=V.XSD.dateTime))
    )
    graph.add((activity, V.PROV.endedAtTime, Literal(ended.isoformat(), datatype=V.XSD.dateTime)))
    if used is not None:
        graph.add((activity, V.PROV.used, URIRef(used.resolve().as_uri())))
    if generated is not None:
        graph.add((activity, V.PROV.generated, URIRef(generated.resolve().as_uri())))
    if converter_version:
        graph.add((activity, V.converterVersion, Literal(converter_version)))
    for name, count in sorted((decisions or {}).items()):
        if count:
            graph.add((activity, V.decision, Literal(f"{name}={count}")))
    return activity


def write_record_provenance(
    output_path: Path,
    *,
    stage: str,
    bib_id: str,
    started: datetime,
    ended: datetime,
    used: Path | None = None,
    decisions: Mapping[str, int] | None = None,
    converter_version: str | None = None,
) -> Path:
    """Write the ``.prov.ttl`` sidecar for one converted record.

    Returns the sidecar path. Uses :class:`ProvenanceWriter` so the write
    is atomic (tmp-then-rename) and the canonical prefixes are bound —
    never a bare ``graph.serialize``.
    """
    sidecar = sidecar_path_for(output_path)
    # A fresh writer per record: it parses any existing file back, so a
    # re-run replaces rather than duplicates the Activity.
    sidecar.unlink(missing_ok=True)
    with ProvenanceWriter(sidecar) as writer:
        build_conversion_activity(
            writer.graph,
            stage=stage,
            bib_id=bib_id,
            started=started,
            ended=ended,
            used=used,
            generated=output_path,
            decisions=decisions,
            converter_version=converter_version,
        )
    return sidecar


__all__ = [
    "PROVENANCE_SUFFIX",
    "activity_uri",
    "build_conversion_activity",
    "now",
    "sidecar_path_for",
    "write_record_provenance",
]
