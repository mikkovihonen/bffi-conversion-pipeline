"""Pillar 4 orchestrator: BFFI canonical Turtle -> reconstructed MARCXML.

Step 4 — v0 emit. Reads a BFFI graph and emits a MARCXML record
per Manifestation. Cardinal rule: the reverse converter MUST NOT consult
``bffi-prov:`` (pipeline-internal provenance) for bibliographic content.
Pipeline-internal data is fair for UI / pairing machinery, never for emit
content. The closed-namespace discipline test
(``tests/unit/stages/bffi_to_marc/test_bffi_prov_discipline.py``) parses
this module's source and fails the build if a ``bffi-prov:`` reference
creeps in.

v0 scope: emit the minimum-viable MARCXML that lets the round-trip diff
harness (step 5) compare against the source. Concretely:

  - leader   placeholder (24 chars; positions populated in a later step)
  - 001      Source bib ID, read from a ``bffi:identifiedBy [ a bffi:Local ;
             rdf:value ?bib_id ]`` block. Fallback: parse from the
             Manifestation URI fragment (``http://…/<bib_id>#Instance``,
             marc2bibframe2's emit shape with our ``baseuri`` parameter).
  - 245 $a   main title, walked via ``?m bffi:title / bffi:mainTitle``.

Anything else — contributors, identifier schemes (ISBN / ISSN), subjects,
provision activity, notes, language, content type — is deliberately
deferred. Each MARC family lands in its own follow-on commit so the diff
harness gives a clean per-family verification signal.

Stage label for observability sidecar events: ``bffi2marc``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, TypeVar

from lxml import etree
from rdflib import RDF, BNode, Graph, Literal, URIRef
from rdflib.namespace import RDFS
from rdflib.term import Node

from bffi_pipeline.observability.events import emit_if_active
from bffi_pipeline.provenance.vocab import BFFI
from bffi_pipeline.rdf_utils import local_name

STAGE: Final[str] = "bffi2marc"

#: MARCXML namespace per the LoC MARC21 slim schema.
MARC21_NS: Final[str] = "http://www.loc.gov/MARC21/slim"
_MARC: Final[str] = f"{{{MARC21_NS}}}"

#: How often to emit a ``progress`` event during corpus conversion.
PROGRESS_CADENCE: Final[int] = 100

#: Default values for MARC leader positions that aren't derivable from
#: BFFI signals (or whose derivation lands in a future commit). The
#: full leader is rebuilt per-record by ``_build_leader``.
_LEADER_DEFAULT_STATUS: Final[str] = "n"  # 05: new record
_LEADER_DEFAULT_TYPE: Final[str] = "a"  # 06: language material
_LEADER_DEFAULT_BIBLIOGRAPHIC_LEVEL: Final[str] = "m"  # 07: monograph
_LEADER_DEFAULT_ENCODING_LEVEL: Final[str] = " "  # 17: full level

#: BFFI ``bffi:status`` URI → leader position 05 (record status).
_MSTATUS_TO_LEADER_STATUS: Final[dict[URIRef, str]] = {
    URIRef("http://id.loc.gov/vocabulary/mstatus/a"): "a",
    URIRef("http://id.loc.gov/vocabulary/mstatus/c"): "c",
    URIRef("http://id.loc.gov/vocabulary/mstatus/d"): "d",
    URIRef("http://id.loc.gov/vocabulary/mstatus/n"): "n",
    URIRef("http://id.loc.gov/vocabulary/mstatus/p"): "p",
    URIRef("http://id.loc.gov/vocabulary/mstatus/s"): "s",
    URIRef("http://id.loc.gov/vocabulary/mstatus/x"): "x",
}

#: BFFI content-type URI suffix → leader position 06 (type of record).
#: Maps the last segment of the content-type URI (e.g. ``"txt"``) to
#: the MARC 21 leader byte.
_CONTENT_TYPE_TO_LEADER_TYPE: Final[dict[str, str]] = {
    "txt": "a",  # text → language material
    "tac": "a",
    "ntm": "c",  # notated music
    "ntv": "d",  # notated movement (manuscript-like)
    "prm": "j",  # performed music → musical sound recording
    "spw": "i",  # spoken word → non-musical sound recording
    "snd": "i",
    "sti": "k",  # still image → two-dim nonprojectable graphic
    "tdi": "g",  # two-dim moving image → projected medium
    "tdm": "g",
    "tcm": "g",
    "tci": "g",
    "tdf": "r",  # three-dim form → three-dim artifact
    "crd": "e",  # cartographic dataset
    "cri": "e",
    "crm": "e",
    "crn": "e",
    "crt": "e",
    "crf": "e",
    "cop": "m",  # computer dataset
    "cod": "m",
    "coi": "m",
    "com": "m",
    "con": "m",
    "cos": "m",
    "cot": "m",
    "cox": "m",
    "coz": "m",
}

#: BFFI issuance URI → leader position 07 (bibliographic level).
_ISSUANCE_TO_LEADER_LEVEL: Final[dict[URIRef, str]] = {
    URIRef("http://id.loc.gov/vocabulary/issuance/mono"): "m",
    URIRef("http://id.loc.gov/vocabulary/issuance/serl"): "s",
    URIRef("http://id.loc.gov/vocabulary/issuance/intg"): "i",
    URIRef("http://id.loc.gov/vocabulary/issuance/srcs"): "b",
    URIRef("http://id.loc.gov/vocabulary/issuance/mums"): "c",
    URIRef("http://id.loc.gov/vocabulary/issuance/musa"): "a",
}

#: BFFI ``menclvl`` URI suffix → leader position 17 (encoding level).
#: ``menclvl/f`` ("full level") corresponds to a literal blank in the
#: MARC leader; the digit values map directly to their MARC counterparts.
_MENCLVL_TO_LEADER_ENCODING: Final[dict[str, str]] = {
    "f": " ",  # full level → blank
    "1": "1",
    "2": "2",
    "3": "3",
    "4": "4",
    "5": "5",
    "7": "7",
    "8": "8",
    "u": "u",
    "z": "z",
}

#: LoC relator URI prefix — the namespace for ``$4`` relator-code URIs
#: that marc2bibframe2 emits when source MARC carried ``$4 <code>``.
_LOC_RELATOR_PREFIX: Final[str] = "http://id.loc.gov/vocabulary/relators/"


# --- BFFI → MARC mapping registry (doc-generation metadata) ---------------


@dataclass(frozen=True)
class MarcEmitMeta:
    """Documentation metadata for one MARC field family this module emits.

    Pure metadata — not consulted at conversion time. The diagnostic
    auto-generator (``bffi_pipeline.diagnostic.marc_mapping``) walks
    :data:`MARC_EMIT_REGISTRY` to produce the BFFI → MARC mapping table
    in ``docs/bffi_to_marc_mapping.md``.

    Each entry declares:

      - ``tag``: 3-character MARC field tag (``"245"``) or pseudo-tag
        for record-level constructs (``"leader"``).
      - ``indicators``: tuple of two strings ``("ind1", "ind2")`` —
        ``" "`` represents the MARC blank indicator. Empty tuple for
        control fields and leader.
      - ``subfields``: ordered tuple of ``(code, description)`` pairs.
        Empty for control fields / leader / catch-all emits.
      - ``source``: human-readable description of the BFFI walk that
        drives the emit (e.g. ``"bffi:title / bffi:Title / bffi:mainTitle"``).
      - ``notes``: optional caveat or limitation.
    """

    tag: str
    indicators: tuple[str, ...]
    subfields: tuple[tuple[str, str], ...]
    source: str
    notes: str = ""


#: MARC fields the BFFI → MARC reverse converter currently emits.
#: Populated at import time by :func:`marc_emit`-decorated extract
#: functions (plus the standalone ``leader`` entry below). The
#: registry is the single source of truth — the doc generator
#: (:mod:`bffi_pipeline.diagnostic.marc_mapping`) walks it and
#: produces the BFFI → MARC mapping doc.
MARC_EMIT_REGISTRY: list[MarcEmitMeta] = []


F = TypeVar("F", bound=Callable[..., Any])


def marc_emit(*entries: MarcEmitMeta) -> Callable[[F], F]:
    """Attach :class:`MarcEmitMeta` entries to an extract function and
    register them in :data:`MARC_EMIT_REGISTRY`.

    Multiple entries per decorator support extractors that contribute
    to several MARC tags (e.g. :func:`_extract_identifier_datafields`
    handles both 020 ISBN and 022 ISSN — both are declared on the
    same function).

    Adding a new MARC family now costs one edit: the ``@marc_emit(...)``
    decorator on the extract function. The doc generator picks it up
    at the next regeneration; no parallel registry to keep in sync.
    """

    def decorator(func: F) -> F:
        for entry in entries:
            MARC_EMIT_REGISTRY.append(entry)
        func._marc_emit_meta = entries  # type: ignore[attr-defined]
        return func

    return decorator


@dataclass(frozen=True)
class ConversionOptions:
    """Configuration for one corpus-conversion run."""

    input_dir: Path
    output_dir: Path


@dataclass
class ConversionSummary:
    """Aggregate counts after corpus conversion ends."""

    total: int = 0
    converted: int = 0
    failed: int = 0
    #: Inputs that produced zero ``bffi:Manifestation`` entities. Indicates
    #: either a malformed BFFI Turtle or a BIBFRAME-stage output we haven't
    #: routed yet. v0 treats these as failures.
    no_manifestation: int = 0
    failures: list[tuple[Path, str]] = field(default_factory=list)


class BffiToMarcError(RuntimeError):
    """A single-record conversion failed."""


@marc_emit(
    MarcEmitMeta(
        tag="001",
        indicators=(),
        subfields=(),
        source=(
            "?m bffi:identifiedBy [a bffi:Local ; rdf:value ?bib_id] "
            "(fallback: parse from the Manifestation URI fragment)"
        ),
    )
)
def _extract_bib_id_from_local(graph: Graph, manifestation: URIRef) -> str | None:
    """Walk ``manifestation bffi:identifiedBy [a bffi:Local; rdf:value ?id]``.

    Returns the first Local identifier value found, or ``None`` if no
    Local block is present on this Manifestation.
    """
    for ident in graph.objects(manifestation, BFFI.identifiedBy):
        if (ident, RDF.type, BFFI.Local) in graph:
            value = next(graph.objects(ident, RDF.value), None)
            if isinstance(value, Literal):
                return str(value)
    return None


def _extract_bib_id_from_uri(manifestation: URIRef) -> str | None:
    """Fallback bib-ID extractor for the marc2bibframe2 emit shape.

    With ``baseuri=<…>`` and ``idfield=001``, marc2bibframe2 concatenates
    ``baseuri + bib_id + "#Instance"``. The bib ID is whatever sits
    between the rightmost path/namespace delimiter and the fragment —
    so we take the substring after the rightmost ``/`` *or* ``:`` (URN
    paths like ``http://urn.fi/URN:NBN:fi:bib:<id>`` use ``:`` as the
    final separator before the ID).
    """
    uri_str = str(manifestation)
    fragment_idx = uri_str.find("#")
    base = uri_str[:fragment_idx] if fragment_idx > 0 else uri_str
    cut = max(base.rfind("/"), base.rfind(":"))
    if cut < 0:
        return None
    tail = base[cut + 1 :]
    return tail or None


@marc_emit(
    MarcEmitMeta(
        tag="005",
        indicators=(),
        subfields=(),
        source="?m bffi:adminMetadata [a bffi:AdminMetadata ; bffi:changeDate ?date]",
    )
)
def _extract_change_date(graph: Graph, manifestation: URIRef) -> str | None:
    """Return the ``bffi:changeDate`` literal from the Manifestation's
    AdminMetadata block. Maps directly to MARC 005."""
    for admin in graph.objects(manifestation, BFFI.adminMetadata):
        date = next(graph.objects(admin, BFFI.changeDate), None)
        if isinstance(date, Literal):
            return str(date)
    return None


@dataclass(frozen=True)
class _PublicationEmit:
    """One MARC 260 datafield's content, split into structured subfields.

    Any combination of place / agent / date can be absent (or all three —
    in which case the fallback ``statement`` carries the flat transcribed
    string from ``bffi:publicationStatement``). ISBD trailing punctuation
    is applied at emit time, not stored here."""

    place: str | None
    agent: str | None
    date: str | None
    statement: str | None


@marc_emit(
    MarcEmitMeta(
        tag="260",
        indicators=(" ", " "),
        subfields=(
            ("a", "place of publication / distribution"),
            ("b", "publisher / distributor name"),
            ("c", "date of publication / distribution"),
        ),
        source=(
            "?m bffi:provisionActivity ?pa . ?pa a bffi:Publication ; "
            "bffi:simplePlace ?place ; bffi:simpleAgent ?agent ; "
            "bffi:simpleDate ?date . "
            "Fallback: ?m bffi:publicationStatement ?text — emits in $a "
            "as a single flat string when the structured parts are absent."
        ),
        notes=(
            'ISBD trailing punctuation (" :" before $b, "," before $c) is '
            "added at emit time. If no Publication-typed provisionActivity "
            "carries the structured parts, the flat bffi:publicationStatement "
            "is the fallback — whole transcribed string in $a."
        ),
    )
)
@marc_emit(
    MarcEmitMeta(
        tag="250",
        indicators=(" ", " "),
        subfields=(("a", "edition statement"),),
        source="?m bffi:editionStatement ?text",
    )
)
def _extract_edition_statement(graph: Graph, manifestation: URIRef) -> str | None:
    """Return the first ``bffi:editionStatement`` literal on the
    Manifestation. Maps directly to MARC 250 ``$a``."""
    value = next(graph.objects(manifestation, BFFI.editionStatement), None)
    return str(value) if isinstance(value, Literal) else None


def _extract_publication(graph: Graph, manifestation: URIRef) -> _PublicationEmit | None:
    """Walk the Manifestation's ``bffi:provisionActivity`` blocks for the
    first Publication-typed activity and return its structured place /
    agent / date. Falls back to ``bffi:publicationStatement`` when no
    structured parts are present."""
    place: str | None = None
    agent: str | None = None
    date: str | None = None
    for pa in graph.objects(manifestation, BFFI.provisionActivity):
        if (pa, RDF.type, BFFI.Publication) not in graph:
            continue
        if place is None:
            place = _first_literal(graph, pa, BFFI.simplePlace)
        if agent is None:
            agent = _first_literal(graph, pa, BFFI.simpleAgent)
        if date is None:
            date = _first_literal(graph, pa, BFFI.simpleDate)
        if place and agent and date:
            break
    statement: str | None = None
    if place is None and agent is None and date is None:
        value = next(graph.objects(manifestation, BFFI.publicationStatement), None)
        if isinstance(value, Literal):
            statement = str(value)
    if place is None and agent is None and date is None and statement is None:
        return None
    return _PublicationEmit(place=place, agent=agent, date=date, statement=statement)


def _first_literal(graph: Graph, subject: Node, predicate: URIRef) -> str | None:
    """Return the first literal value of ``predicate`` on ``subject``,
    or ``None`` if absent / not a literal."""
    value = next(graph.objects(subject, predicate), None)
    return str(value) if isinstance(value, Literal) else None


@dataclass(frozen=True)
class _RdaEntry:
    """One MARC 336/337/338 emit: ``$a`` label + ``$b`` code + ``$2``
    scheme + optional ``$3`` materials specified.

    ``label`` comes from the URI's ``rdfs:label`` when present (English
    typically; source-MARC ``$a`` is the cataloguer's display language
    which BFFI doesn't preserve, so labels will not always round-trip
    byte-identical). ``code`` is the URI's last segment (e.g. ``"sti"``).
    ``scheme`` is derived from the URI's namespace (e.g. ``"rdacontent"``
    for ``…/contentTypes/sti``). ``applies_to`` is the
    ``bffi:appliesTo`` bnode's ``rdfs:label`` when present — this is the
    MARC ``$3`` (materials specified)."""

    label: str | None
    code: str
    scheme: str
    applies_to: str | None = None


@dataclass(frozen=True)
class _RdaDescriptors:
    """RDA descriptors for MARC 336 (content), 337 (media), 338 (carrier)."""

    content: tuple[_RdaEntry, ...]
    media: tuple[_RdaEntry, ...]
    carrier: tuple[_RdaEntry, ...]


@marc_emit(
    MarcEmitMeta(
        tag="leader",
        indicators=(),
        subfields=(),
        source=(
            "Position 05 ← bffi:adminMetadata / bffi:status (mstatus URI); "
            "position 06 ← bffi:content URI's last segment (txt → 'a' etc.); "
            "position 07 ← bffi:issuance URI (mono → 'm', serl → 's', …); "
            "position 17 ← bffi:encodingLevel "
            "(menclvl/7 → '7', menclvl/f → ' ')."
        ),
        notes=(
            "Other leader positions hold structural constants (10/11 = '2', "
            "20-23 = '4500') or placeholders (00-04 record length, 12-16 "
            "base address — recomputed by downstream MARC binary writers). "
            "Source-MARC byte-fidelity is not guaranteed because BFFI doesn't "
            "preserve every leader byte (e.g. position 09 character coding)."
        ),
    )
)
def _build_leader(graph: Graph, manifestation: URIRef) -> str:
    """Construct a 24-character MARC leader from BFFI signals."""
    status = _leader_status_byte(graph, manifestation)
    record_type = _leader_record_type_byte(graph, manifestation)
    level = _leader_bibliographic_level_byte(graph, manifestation)
    encoding_level = _leader_encoding_level_byte(graph, manifestation)
    return (
        "00000"  # 00-04: record length placeholder
        + status  # 05: record status
        + record_type  # 06: type of record
        + level  # 07: bibliographic level
        + " "  # 08: type of control (default blank)
        + " "  # 09: character coding (blank = MARC-8; matches HELMET corpus convention)
        + "22"  # 10-11: indicator count + subfield code count
        + "00000"  # 12-16: base address placeholder
        + encoding_level  # 17: encoding level
        + " "  # 18: descriptive cataloging form (default blank)
        + " "  # 19: multipart resource record level (default blank)
        + "4500"  # 20-23: entry map
    )


def _admin_metadata_anchors(graph: Graph, manifestation: URIRef) -> list[URIRef]:
    """Return nodes whose ``bffi:adminMetadata`` triples are in scope
    for this Manifestation — the Manifestation plus its Work.
    marc2bibframe2 attaches AdminMetadata to either; the leader builder
    has to walk both."""
    anchors: list[URIRef] = [manifestation]
    work = _find_work_for_manifestation(graph, manifestation)
    if work is not None:
        anchors.append(work)
    return anchors


def _leader_status_byte(graph: Graph, manifestation: URIRef) -> str:
    """Pick the MARC leader position-05 byte from
    ``bffi:adminMetadata / bffi:status`` URIs on either the
    Manifestation or its Work.

    Prefers ``mstatus/n`` (new) when present — marc2bibframe2 adds a
    separate AdminMetadata block with ``mstatus/c`` (corrected) to
    record its own conversion step, and that block's status is NOT
    the original source-MARC leader byte. The source's own
    AdminMetadata typically carries ``mstatus/n``; preferring it
    keeps the round-trip byte-faithful for the vast majority of
    HELMET libraries test corpus records.

    Falls through to any other recognised status, then to ``"n"``
    (the corpus-wide default for source-MARC leader position 05).
    """
    statuses: list[URIRef] = []
    for anchor in _admin_metadata_anchors(graph, manifestation):
        for am in graph.objects(anchor, BFFI.adminMetadata):
            for status_uri in graph.objects(am, BFFI.status):
                if isinstance(status_uri, URIRef):
                    statuses.append(status_uri)
    if not statuses:
        return _LEADER_DEFAULT_STATUS
    new = URIRef("http://id.loc.gov/vocabulary/mstatus/n")
    if new in statuses:
        return "n"
    for status_uri in statuses:
        mapped = _MSTATUS_TO_LEADER_STATUS.get(status_uri)
        if mapped is not None:
            return mapped
    return _LEADER_DEFAULT_STATUS


def _leader_record_type_byte(graph: Graph, manifestation: URIRef) -> str:
    """Pick the MARC leader position-06 byte (type of record) from
    ``?work bffi:content <…/contentTypes/{code}>``. Returns the
    default ``"a"`` (language material) when no content URI matches
    the dispatch table."""
    work = _find_work_for_manifestation(graph, manifestation)
    if work is None:
        return _LEADER_DEFAULT_TYPE
    for content_uri in graph.objects(work, BFFI.content):
        if not isinstance(content_uri, URIRef):
            continue
        mapped = _CONTENT_TYPE_TO_LEADER_TYPE.get(local_name(content_uri))
        if mapped is not None:
            return mapped
    return _LEADER_DEFAULT_TYPE


def _leader_bibliographic_level_byte(graph: Graph, manifestation: URIRef) -> str:
    """Pick the MARC leader position-07 byte from ``bffi:issuance``
    (``mono`` → ``"m"``, ``serl`` → ``"s"``, etc.). Defaults to
    ``"m"`` (monograph) when no recognised issuance URI is present."""
    for issuance in graph.objects(manifestation, BFFI.issuance):
        if isinstance(issuance, URIRef):
            mapped = _ISSUANCE_TO_LEADER_LEVEL.get(issuance)
            if mapped is not None:
                return mapped
    return _LEADER_DEFAULT_BIBLIOGRAPHIC_LEVEL


def _leader_encoding_level_byte(graph: Graph, manifestation: URIRef) -> str:
    """Pick the MARC leader position-17 byte (encoding level) from
    ``bffi:adminMetadata / bffi:encodingLevel`` on either the
    Manifestation or its Work. ``menclvl/f`` "full level" maps to a
    literal blank; digit URIs map directly. Defaults to blank when
    absent."""
    for anchor in _admin_metadata_anchors(graph, manifestation):
        for am in graph.objects(anchor, BFFI.adminMetadata):
            for ev in graph.objects(am, BFFI.encodingLevel):
                if isinstance(ev, URIRef):
                    mapped = _MENCLVL_TO_LEADER_ENCODING.get(local_name(ev))
                    if mapped is not None:
                        return mapped
    return _LEADER_DEFAULT_ENCODING_LEVEL


_RDA_SUBFIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("a", "term in the cataloguing language (rdfs:label of the URI)"),
    ("b", "RDA 3-letter code (URI last segment)"),
    ("2", "scheme name (rdacontent / rdamedia / rdacarrier)"),
    ("3", "materials specified (from bffi:appliesTo/rdfs:label)"),
)


@marc_emit(
    MarcEmitMeta(
        tag="336",
        indicators=(" ", " "),
        subfields=_RDA_SUBFIELDS,
        source=(
            "?m bffi:workManifested ?work . "
            "?expression bffi:expressionOf ?work (or ?expression "
            "bffi:manifestationOfExpression ?m) . "
            "?expression bffi:content <http://id.loc.gov/vocabulary/contentTypes/{code}> . "
            "?content bffi:appliesTo ?applies . ?applies rdfs:label ?applies_to . "
            "$a = ?content's rdfs:label; $b = {code}; $2 = 'rdacontent'; "
            "$3 = ?applies_to."
        ),
        notes=(
            "Content type is an **Expression** attribute, not a Work one. "
            "Expressions point outward (bffi:expressionOf / "
            "bffi:manifestationOfExpression) with no inverse from the Work, so "
            "the traversal has to be inverted to reach them. Source MARC \\$a is "
            "the cataloguer's display label (often Finnish); BFFI carries the "
            "URI's rdfs:label. Records whose source had no 336 still emit one "
            "when marc2bibframe2 derived a content type from the leader/008."
        ),
    ),
    MarcEmitMeta(
        tag="337",
        indicators=(" ", " "),
        subfields=_RDA_SUBFIELDS,
        source=(
            "?m bffi:media <http://id.loc.gov/vocabulary/mediaTypes/{code}> . "
            "?media bffi:appliesTo ?applies . ?applies rdfs:label ?applies_to . "
            "$a from rdfs:label; $b = {code}; $2 = 'rdamedia'; "
            "$3 = ?applies_to."
        ),
    ),
    MarcEmitMeta(
        tag="338",
        indicators=(" ", " "),
        subfields=_RDA_SUBFIELDS,
        source=(
            "?m bffi:carrier <http://id.loc.gov/vocabulary/carriers/{code}> . "
            "?carrier bffi:appliesTo ?applies . ?applies rdfs:label ?applies_to . "
            "$a from rdfs:label; $b = {code}; $2 = 'rdacarrier'; "
            "$3 = ?applies_to."
        ),
    ),
)
def _extract_rda_descriptors(graph: Graph, manifestation: URIRef) -> _RdaDescriptors:
    """Walk the RDA content / media / carrier predicates and build a
    structured emit per row: the MARC 3-letter code (URI's local name),
    the human-readable label (URI's ``rdfs:label``), and the scheme
    name derived from the URI's namespace path.

    Content lives on the **Expression** (FRBR-axis: content type is an
    Expression attribute); media and carrier live on the Manifestation.
    Multiple values per predicate produce multiple datafields, sorted for
    determinism."""
    work = _find_work_for_manifestation(graph, manifestation)
    content_owners: list[URIRef | BNode] = [work] if work is not None else []
    content_owners.extend(_expressions_for(graph, manifestation, work))
    content = _rda_entries(
        graph,
        (o for owner in content_owners for o in graph.objects(owner, BFFI.content)),
        scheme="rdacontent",
    )
    media = _rda_entries(graph, graph.objects(manifestation, BFFI.media), scheme="rdamedia")
    carrier = _rda_entries(graph, graph.objects(manifestation, BFFI.carrier), scheme="rdacarrier")
    return _RdaDescriptors(content=content, media=media, carrier=carrier)


def _expressions_for(
    graph: Graph, manifestation: URIRef, work: URIRef | None
) -> list[URIRef | BNode]:
    """Return the Expression nodes for one record.

    Expressions point *outward* — ``?expression bffi:expressionOf ?work`` and
    ``?expression bffi:manifestationOfExpression ?m`` — with no inverse from
    the Work or Manifestation. Walking only outgoing predicates from the
    Manifestation therefore never reaches them, which lost every MARC 336:
    the content type is an Expression attribute, so all 301 content-bearing
    records in the reference corpus emitted no 336 at all. Inverting the two
    predicates reaches 296 of those 301.
    """
    found: list[URIRef | BNode] = []
    seen: set[URIRef | BNode] = set()
    candidates: list[Node] = list(graph.subjects(BFFI.manifestationOfExpression, manifestation))
    if work is not None:
        candidates.extend(graph.subjects(BFFI.expressionOf, work))
    for node in candidates:
        if isinstance(node, URIRef | BNode) and node not in seen:
            seen.add(node)
            found.append(node)
    return found


def _rda_entries(graph: Graph, objects: Iterable[Node], *, scheme: str) -> tuple[_RdaEntry, ...]:
    """Build the sorted tuple of ``_RdaEntry`` values for one of the
    three RDA predicates. Skips non-URI objects."""
    entries: list[_RdaEntry] = []
    # Deduplicate on the descriptor URI: a record can carry several
    # Expressions that share a content type (multi-part audio sets repeat
    # ``contentTypes/prm``), and MARC never repeats an identical 336/337/338.
    seen: set[URIRef] = set()
    for obj in objects:
        if not isinstance(obj, URIRef) or obj in seen:
            continue
        seen.add(obj)
        label = next(graph.objects(obj, RDFS.label), None)
        # Read bffi:appliesTo/rdfs:label for $3 (materials specified).
        applies_to: str | None = None
        for applies in graph.objects(obj, BFFI.appliesTo):
            if isinstance(applies, BNode):
                applies_label = next(graph.objects(applies, RDFS.label), None)
                if isinstance(applies_label, Literal):
                    applies_to = str(applies_label)
                    break
        entries.append(
            _RdaEntry(
                label=str(label) if isinstance(label, Literal) else None,
                code=local_name(obj),
                scheme=scheme,
                applies_to=applies_to,
            )
        )
    return tuple(sorted(entries, key=lambda e: (e.code, e.label or "")))


@dataclass(frozen=True)
class _TitleParts:
    """The 245-field-worth of content extracted from one bffi:Title block."""

    main: str
    subtitle: str | None = None
    part_number: str | None = None
    part_name: str | None = None


#: Maps a BFFI agent class to a ``(primary_tag, added_tag)`` MARC pair.
#: Primary contributions (``bffi:PrimaryContribution``-typed) emit as
#: MARC 1XX; all others emit as MARC 7XX of the matching agent type.
#: ``bffi:Jurisdiction`` is corporate-like in MARC.
_AGENT_TYPE_TO_MARC_TAG_PAIR: Final[dict[URIRef, tuple[str, str]]] = {
    BFFI.Person: ("100", "700"),
    BFFI.Organization: ("110", "710"),
    BFFI.Jurisdiction: ("110", "710"),
    BFFI.Meeting: ("111", "711"),
}


@dataclass(frozen=True)
class _ContributorEmit:
    """One MARC contributor datafield (100/110/111/700/710/711).

    ``relator`` is the LoC relator code (the URI's last segment, e.g.
    ``"aut"``) used for ``$4``; ``relator_term`` is the cataloguer's
    free-text term (e.g. Finnish ``"näyttelijä"``) used for ``$e``.
    Either, both, or neither may be present depending on what the
    source MARC carried.

    ``ind1`` / ``ind2`` come from the agent's ``bffi:marcKey`` when
    present (preserves the source-MARC indicators verbatim); otherwise
    they default to blank.

    ``extra_subfields`` carries codes the source MARC had beyond
    ``$a`` / ``$e`` / ``$4`` (e.g. ``$t`` analytical title, ``$c``
    qualification, ``$d`` dates). These are parsed from the agent's
    ``bffi:marcKey`` and emitted in marcKey order between ``$e`` and
    ``$4`` per MARC X00 subfield convention."""

    tag: str
    label: str
    relator: str | None
    relator_term: str | None
    ind1: str
    ind2: str
    extra_subfields: tuple[tuple[str, str], ...]


def _agent_marc_tag(graph: Graph, agent: URIRef, *, is_primary: bool) -> str | None:
    """Pick the MARC tag for a contribution from the agent's class type
    + primary/added flag. Returns ``None`` if no type signal matches."""
    for agent_type, (primary_tag, added_tag) in _AGENT_TYPE_TO_MARC_TAG_PAIR.items():
        if (agent, RDF.type, agent_type) in graph:
            return primary_tag if is_primary else added_tag
    return None


_CONTRIBUTOR_SUBFIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("a", "personal / corporate / meeting name"),
    ("b", "subordinate unit (corporate / meeting; marcKey-driven)"),
    ("c", "qualifier (e.g. fictional-character flag; marcKey-driven)"),
    ("d", "dates of birth / death / activity (marcKey-driven)"),
    ("e", "relator term (cataloguer's free-text role, e.g. 'näyttelijä')"),
    ("g", "miscellaneous information (marcKey-driven)"),
    ("t", "title within work — analytical added entry (marcKey-driven)"),
    ("0", "authority URI (marcKey-driven)"),
    ("4", "LoC relator code (e.g. 'aut')"),
)


@marc_emit(
    MarcEmitMeta(
        tag="100",
        indicators=(" ", " "),
        subfields=_CONTRIBUTOR_SUBFIELDS,
        source=(
            "?m bffi:workManifested ?work . "
            "?work bffi:contribution [a bffi:PrimaryContribution ; "
            "bffi:agent ?agent ; bffi:role ?role] . "
            "?agent a bffi:Person ; rdfs:label ?name . "
            "$4 = local-name of ?role when ?role is a LoC relator URI; "
            "$e = rdfs:label of ?role when ?role is a bnode with a label."
        ),
    ),
    MarcEmitMeta(
        tag="110",
        indicators=(" ", " "),
        subfields=_CONTRIBUTOR_SUBFIELDS,
        source=(
            "Same as 100, but with ?agent a bffi:Organization "
            "(or bffi:Jurisdiction) on a primary contribution"
        ),
    ),
    MarcEmitMeta(
        tag="111",
        indicators=(" ", " "),
        subfields=_CONTRIBUTOR_SUBFIELDS,
        source="Same as 100, but with ?agent a bffi:Meeting on a primary contribution",
    ),
    MarcEmitMeta(
        tag="700",
        indicators=(" ", " "),
        subfields=_CONTRIBUTOR_SUBFIELDS,
        source=(
            "Same chain as 100, but the contribution is NOT typed "
            "bffi:PrimaryContribution — added-entry contributors land in 7XX"
        ),
    ),
    MarcEmitMeta(
        tag="710",
        indicators=(" ", " "),
        subfields=_CONTRIBUTOR_SUBFIELDS,
        source="Same as 700, but with ?agent a bffi:Organization or bffi:Jurisdiction",
    ),
    MarcEmitMeta(
        tag="711",
        indicators=(" ", " "),
        subfields=_CONTRIBUTOR_SUBFIELDS,
        source="Same as 700, but with ?agent a bffi:Meeting",
    ),
)
def _extract_contributors(graph: Graph, manifestation: URIRef) -> list[_ContributorEmit]:
    """Walk ``?work bffi:contribution`` blocks and emit MARC 1XX (for
    ``bffi:PrimaryContribution``) or 7XX (for added entries), picking
    the digit-pair from the agent's class type (Person → X00, Corporate
    / Jurisdiction → X10, Meeting → X11).

    Emits ``$a`` from ``rdfs:label`` on the agent, ``$e`` (relator term)
    from the ``rdfs:label`` of the role bnode, and ``$4`` (LoC relator
    code) from the local-name of ``bffi:role`` when it's a URI. The
    The HELMET libraries test corpus uses free-text ``$e`` heavily (~10x more often than
    ``$4``); both shapes are emitted when their respective signals are
    present in BFFI.
    """
    work = _find_work_for_manifestation(graph, manifestation)
    if work is None:
        return []
    emits: list[_ContributorEmit] = []
    seen_contribs: set[Node] = set()
    for anchor in _contribution_anchors(graph, manifestation, work):
        for contrib in graph.objects(anchor, BFFI.contribution):
            if contrib in seen_contribs:
                continue
            seen_contribs.add(contrib)
            emit = _build_contributor_emit(graph, contrib)
            if emit is not None:
                emits.append(emit)
    return sorted(emits, key=lambda e: (e.tag, e.label, e.relator_term or "", e.relator or ""))


def _contribution_anchors(graph: Graph, manifestation: URIRef, work: URIRef) -> list[URIRef]:
    """Return every node whose ``bffi:contribution`` is in scope for the
    Manifestation: the main Work, plus any Hub Work reachable via
    ``bffi:relation`` from the Work or Manifestation. Analytical added
    entries (source MARC ``700 _ 2`` etc.) attach the contribution to a
    Hub Work rather than the main Work, so the walk has to cover both."""
    anchors: list[URIRef] = [work]
    for source in (work, manifestation):
        for rel in graph.objects(source, BFFI.relation):
            for target in graph.objects(rel, BFFI.associatedResource):
                if isinstance(target, URIRef):
                    anchors.append(target)
    return anchors


def _build_contributor_emit(graph: Graph, contrib: Node) -> _ContributorEmit | None:
    """Build one ``_ContributorEmit`` from a contribution bnode, or
    ``None`` when the agent / label / tag can't be resolved."""
    is_primary = (contrib, RDF.type, BFFI.PrimaryContribution) in graph
    agent = next(graph.objects(contrib, BFFI.agent), None)
    if not isinstance(agent, URIRef):
        return None
    label = next(graph.objects(agent, RDFS.label), None)
    if not isinstance(label, Literal):
        return None
    tag = _agent_marc_tag(graph, agent, is_primary=is_primary)
    if tag is None:
        return None
    relator, relator_term = _extract_role_codes(graph, contrib)
    ind1, ind2, extras = _contributor_marckey_extras(graph, agent)
    return _ContributorEmit(
        tag=tag,
        label=str(label),
        relator=relator,
        relator_term=relator_term,
        ind1=ind1,
        ind2=ind2,
        extra_subfields=extras,
    )


_CONTRIBUTOR_STRUCTURED_CODES: Final[frozenset[str]] = frozenset({"a", "e", "4"})


def _contributor_marckey_extras(
    graph: Graph, agent: URIRef
) -> tuple[str, str, tuple[tuple[str, str], ...]]:
    """Read the agent's ``bffi:marcKey`` (if present) and return
    ``(ind1, ind2, extra_subfields)``.

    Indicators come verbatim from marcKey when available; absent
    marcKey, both default to blank. ``extra_subfields`` is the ordered
    tuple of ``(code, value)`` pairs from marcKey for codes NOT in the
    structured-BFFI set (``$a`` / ``$e`` / ``$4``) — typically ``$t``
    analytical title and ``$c`` qualifier."""
    marc_key = next(graph.objects(agent, BFFI.marcKey), None)
    if not isinstance(marc_key, Literal):
        return " ", " ", ()
    parsed = _parse_marc_key(str(marc_key))
    if parsed is None:
        return " ", " ", ()
    _tag, ind1, ind2, subfields = parsed
    extras = tuple(
        (code, value) for code, value in subfields if code not in _CONTRIBUTOR_STRUCTURED_CODES
    )
    return ind1, ind2, extras


def _extract_role_codes(graph: Graph, contrib: Node) -> tuple[str | None, str | None]:
    """Return ``(relator_code, relator_term)`` for one ``bffi:contribution``.

    A contribution can carry ``bffi:role`` as either a LoC relator URI
    (drives ``$4``) or as a node with ``rdfs:label`` (drives ``$e``).
    The two signals are independent — both can be present on the same
    contribution if marc2bibframe2 emitted both shapes for a source with
    ``$e <term> $4 <code>``.
    """
    relator: str | None = None
    relator_term: str | None = None
    for role in graph.objects(contrib, BFFI.role):
        if (
            isinstance(role, URIRef)
            and str(role).startswith(_LOC_RELATOR_PREFIX)
            and relator is None
        ):
            relator = local_name(role)
        label = next(graph.objects(role, RDFS.label), None)
        if isinstance(label, Literal) and relator_term is None:
            relator_term = str(label)
    return relator, relator_term


@dataclass(frozen=True)
class _AddedTitleEmit:
    """One MARC 730/740 added-title datafield, parsed verbatim from
    ``bffi:marcKey``: tag, indicators, and the ordered subfield list."""

    tag: str
    ind1: str
    ind2: str
    subfields: tuple[tuple[str, str], ...]


def _parse_marc_key(key: str) -> tuple[str, str, str, tuple[tuple[str, str], ...]] | None:
    """Parse a BFLC ``marcKey`` literal.

    Format is ``<tag-3-chars><ind1-1-char><ind2-1-char><subfields>`` where
    ``<subfields>`` is ``$<code><value>$<code><value>...`` — no separator
    between the indicators and the first ``$``. Blank indicators render
    as ASCII space.

    Returns ``(tag, ind1, ind2, subfields)`` or ``None`` if the key is
    too short or malformed (no leading ``$`` at position 5).
    """
    min_len_for_subfields = 6
    sf_start = 5
    if len(key) < min_len_for_subfields or key[sf_start] != "$":
        return None
    tag = key[:3]
    ind1 = key[3]
    ind2 = key[4]
    subfields: list[tuple[str, str]] = []
    # key[5:] starts with "$"; split on "$" yields ["", "<code><val>", ...].
    for part in key[sf_start:].split("$")[1:]:
        if not part:
            continue
        subfields.append((part[0], part[1:]))
    return tag, ind1, ind2, tuple(subfields)


@marc_emit(
    MarcEmitMeta(
        tag="730",
        indicators=("0", " "),
        subfields=(
            ("a", "uniform title heading"),
            ("g", "miscellaneous information"),
            ("i", "relationship information (marcKey-driven)"),
            ("l", "language of a work"),
            ("n", "number of part / section of a work"),
            ("o", "arrangement statement for music"),
            ("p", "name of part / section of a work"),
            ("s", "version (marcKey-driven)"),
        ),
        source=(
            "?m bffi:relation [bffi:associatedResource ?target] . "
            "?target bffi:marcKey ?key (where ?key begins with '730') . "
            "All subfields and indicators are read verbatim from ?key."
        ),
        notes=(
            "Indicators and every subfield are reconstructed from "
            "bffi:marcKey verbatim. The auto-table lists the common "
            "subfields seen in the corpus ($a $g $l $n $o $p); any "
            "additional subfield codes carried in bffi:marcKey are "
            "emitted in the order they appear."
        ),
    ),
    MarcEmitMeta(
        tag="740",
        indicators=("0", " "),
        subfields=(
            ("a", "added analytical title"),
            ("n", "number of part / section"),
            ("p", "name of part / section"),
        ),
        source=("Same chain as 730 but with bffi:marcKey beginning with '740'"),
        notes=(
            "Indicators (including nonfiling-character counts in ind1) "
            "and every subfield are reconstructed from bffi:marcKey "
            "verbatim — same shape as 730."
        ),
    ),
)
def _extract_added_titles(graph: Graph, manifestation: URIRef) -> list[_AddedTitleEmit]:
    """Walk the ``bffi:relation`` chain on both the Manifestation and
    its Work, finding every related resource whose ``bffi:marcKey``
    begins with ``"730"`` or ``"740"``. Parse marcKey verbatim for
    indicators and subfields — the structural BFFI walk on
    ``bffi:title / bffi:mainTitle`` would only recover ``$a`` and lose
    ``$g`` / ``$o`` / ``$l`` / ``$n`` / ``$p``, which BFFI has no
    structured predicate for.

    The BFFI ontology declares ``bffi:relation`` over a union of
    {Work, Expression, Manifestation, Item}; marc2bibframe2 attaches
    the related-work links to the Work in practice, so the walk has
    to traverse both sides."""
    anchors: list[URIRef] = [manifestation]
    work = _find_work_for_manifestation(graph, manifestation)
    if work is not None:
        anchors.append(work)

    emits: list[_AddedTitleEmit] = []
    for anchor in anchors:
        for rel in graph.objects(anchor, BFFI.relation):
            for target in graph.objects(rel, BFFI.associatedResource):
                if not isinstance(target, URIRef):
                    continue
                marc_key = next(graph.objects(target, BFFI.marcKey), None)
                if isinstance(marc_key, Literal):
                    parsed = _parse_marc_key(str(marc_key))
                    if parsed is None:
                        continue
                    tag, ind1, ind2, subfields = parsed
                    if tag not in ("730", "740") or not subfields:
                        continue
                    emits.append(
                        _AddedTitleEmit(tag=tag, ind1=ind1, ind2=ind2, subfields=subfields)
                    )
                    continue
                structural = _uncontrolled_added_title(graph, target)
                if structural is not None:
                    emits.append(structural)
    return sorted(emits, key=lambda e: (e.tag, e.subfields))


def _uncontrolled_added_title(graph: Graph, target: URIRef) -> _AddedTitleEmit | None:
    """Recover a MARC 740 from a marcKey-less uncontrolled related title.

    marc2bibframe2 renders MARC 740 as a ``bffi:Uncontrolled`` Work whose URI
    fragment carries the source tag (``#Work740-42``) and whose title hangs
    off ``bffi:title / bffi:mainTitle``. Unlike 730 it gets **no marcKey**, so
    the marcKey path above skipped it and every 740 was lost.

    Only ``$a`` is recoverable this way: ``$g`` / ``$l`` / ``$n`` / ``$o`` /
    ``$p`` have no structured BFFI predicate, which is exactly why the 730
    path prefers marcKey. ind1 is not recoverable either — the source's
    nonfiling-character count is not carried — so it emits blank.
    """
    match = _SUBJECT_TAG_PATTERN.search(str(target))
    if match is None or match.group(1) != "740":
        return None
    for title in graph.objects(target, BFFI.title):
        main = next(graph.objects(title, BFFI.mainTitle), None)
        if isinstance(main, Literal) and str(main).strip():
            return _AddedTitleEmit(tag="740", ind1=" ", ind2=" ", subfields=(("a", str(main)),))
    return None


#: marc2bibframe2 attaches an ``rdf:type <mnotetype/<tail>>`` discriminator
#: on ``bf:Note`` bnodes when the source MARC came from a 5XX with a
#: specific subtype. Map each known tail to its target MARC tag; notes
#: without a recognised tail fall through to 500 (general note).
_MNOTETYPE_PHYSICAL: Final[URIRef] = URIRef("http://id.loc.gov/vocabulary/mnotetype/physical")
_MNOTETYPE_ACCMAT: Final[URIRef] = URIRef("http://id.loc.gov/vocabulary/mnotetype/accmat")


@dataclass(frozen=True)
class _NoteTarget:
    """MARC destination for a typed ``bffi:Note`` — tag + subfield code
    + (optional) non-blank indicators.

    ``ind1`` overrides the default blank for fields like 587 where the
    mnotetype URI carries the indicator semantic (datasource → ind1=' ',
    datanf → ind1='0'). The vast majority of 5XX notes use blank
    indicators on both slots.
    """

    tag: str
    subfield_code: str = "a"
    ind1: str = " "
    ind2: str = " "


_MNOTETYPE_TO_MARC: Final[dict[URIRef, _NoteTarget]] = {
    URIRef("http://id.loc.gov/vocabulary/mnotetype/biblio"): _NoteTarget("504"),
    URIRef("http://id.loc.gov/vocabulary/mnotetype/participants"): _NoteTarget("511"),
    URIRef("http://id.loc.gov/vocabulary/mnotetype/addphys"): _NoteTarget("530"),
    URIRef("http://id.loc.gov/vocabulary/mnotetype/computer"): _NoteTarget("538"),
    URIRef("http://id.loc.gov/vocabulary/mnotetype/lang"): _NoteTarget("546"),
    URIRef("http://id.loc.gov/vocabulary/mnotetype/award"): _NoteTarget("586"),
    URIRef("http://id.loc.gov/vocabulary/mnotetype/descsource"): _NoteTarget("588"),
    # 534 source-MARC uses $c (Publication, distribution, etc. of original) —
    # not $a — so the destination subfield code overrides the default.
    URIRef("http://id.loc.gov/vocabulary/mnotetype/orig"): _NoteTarget("534", "c"),
    URIRef("http://id.loc.gov/vocabulary/mnotetype/citeas"): _NoteTarget("524"),
    URIRef("http://id.loc.gov/vocabulary/mnotetype/suppl"): _NoteTarget("525"),
    URIRef("http://id.loc.gov/vocabulary/mnotetype/binding"): _NoteTarget("563"),
    URIRef("http://id.loc.gov/vocabulary/mnotetype/relnote"): _NoteTarget("580"),
    URIRef("http://id.loc.gov/vocabulary/mnotetype/action"): _NoteTarget("583"),
    # 587: marc2bibframe2 distinguishes ind1=' ' (datasource) and
    # ind1='0' (datanf, "Source not formalized") via the mnotetype tail.
    URIRef("http://id.loc.gov/vocabulary/mnotetype/datasource"): _NoteTarget("587", ind1=" "),
    URIRef("http://id.loc.gov/vocabulary/mnotetype/datanf"): _NoteTarget("587", ind1="0"),
}

#: Note types consumed by other emit families and therefore skipped by
#: the generic 5XX note walk so they don't double-emit. accmat is the
#: source for MARC 300 ``$e`` (accompanying material); physical is
#: under the Extent bnode and feeds MARC 300 ``$b`` from there.
_NOTE_TYPES_HANDLED_ELSEWHERE: Final[frozenset[URIRef]] = frozenset(
    {_MNOTETYPE_ACCMAT, _MNOTETYPE_PHYSICAL}
)


#: 5XX tags whose forward XSLT emits a bare ``bf:Note`` with no
#: mnotetype tail (the catch-all ``instanceNote5XX`` family). The reverse
#: path recovers the original tag from a ``bffi:marcKey`` literal whose
#: first three characters match — same marcKey-driven recovery pattern
#: as the 800/810/811/830 traced-series families.
_MARCKEY_5XX_DISPATCH_TAGS: Final[frozenset[str]] = frozenset(
    {
        "501",
        "513",
        "515",
        "516",
        "533",
        "536",
        "544",
        "545",
        "547",
        "550",
        "555",
        "556",
        "581",
        "585",
    }
)


@dataclass(frozen=True)
class _NoteEmit:
    """One generic-note datafield (MARC 500 / 511 / 534 / 546).

    ``subfield_code`` is the MARC subfield code the text emits as —
    defaults to ``"a"`` (general-note convention); ``534`` overrides
    to ``"c"`` (Publication, distribution, etc. of original).
    Indicators default to blank; ``587`` overrides ``ind1`` per the
    datasource / datanf mnotetype tail.
    """

    tag: str
    text: str
    subfield_code: str
    ind1: str = " "
    ind2: str = " "
    #: Additional ``(code, value)`` subfields appended after the primary
    #: one, in order. Used by the structured MARC 518 shape, where the
    #: source carries ``$o``/``$d``/``$p``/``$3`` rather than a single
    #: ``$a``. When ``text`` is empty the primary subfield is skipped.
    extra_subfields: tuple[tuple[str, str], ...] = ()


@marc_emit(
    MarcEmitMeta(
        tag="500",
        indicators=(" ", " "),
        subfields=(("a", "general note text"),),
        source=(
            "?m or ?work bffi:note [a bffi:Note ; rdfs:label ?text] — note bnode "
            "with NO mnotetype rdf:type (the catch-all 5XX)."
        ),
        notes=(
            "Notes typed with a specific mnotetype dispatch to their own "
            "MARC tag (e.g. mnotetype/lang → 546). Others fall through to "
            "500. Per-tail expansion (504 bibliography / 511 participants "
            "/ 520 summary / etc.) is a follow-on."
        ),
    ),
    MarcEmitMeta(
        tag="504",
        indicators=(" ", " "),
        subfields=(("a", "bibliography note text"),),
        source=(
            "?m or ?work bffi:note [a bffi:Note, <…/mnotetype/biblio> ; "
            "rdfs:label ?text] — typed with the bibliography tail."
        ),
    ),
    MarcEmitMeta(
        tag="511",
        indicators=(" ", " "),
        subfields=(("a", "participants / performers note text"),),
        source=(
            "?m or ?work bffi:note [a bffi:Note, <…/mnotetype/participants> ; "
            "rdfs:label ?text] — typed with the participants tail. "
            "Participants notes hang off the Work, not the Manifestation."
        ),
    ),
    MarcEmitMeta(
        tag="530",
        indicators=(" ", " "),
        subfields=(("a", "additional physical form available note"),),
        source=("?m or ?work bffi:note [a bffi:Note, <…/mnotetype/addphys> ; rdfs:label ?text]"),
    ),
    MarcEmitMeta(
        tag="538",
        indicators=(" ", " "),
        subfields=(("a", "system details note text"),),
        source=(
            "?m or ?work bffi:note [a bffi:Note, <…/mnotetype/computer> ; "
            "rdfs:label ?text] — typed with the computer tail."
        ),
    ),
    MarcEmitMeta(
        tag="586",
        indicators=(" ", " "),
        subfields=(("a", "awards note text"),),
        source=("?m or ?work bffi:note [a bffi:Note, <…/mnotetype/award> ; rdfs:label ?text]"),
    ),
    MarcEmitMeta(
        tag="588",
        indicators=(" ", " "),
        subfields=(("a", "source of description note"),),
        source=("?m or ?work bffi:note [a bffi:Note, <…/mnotetype/descsource> ; rdfs:label ?text]"),
    ),
    MarcEmitMeta(
        tag="534",
        indicators=(" ", " "),
        subfields=(("c", "publication / distribution of original"),),
        source=(
            "?m or ?work bffi:note [a bffi:Note, <…/mnotetype/orig> ; "
            "rdfs:label ?text] — note typed with the original-version tail."
        ),
        notes=(
            "534 records the original publication of a reproduction or "
            're-release (typical HELMET corpus shape: \\$c "Danjaq : United '
            'Artists, 1974" on a 2001 DVD reissue). The full source row '
            "is collapsed into a single \\$c literal at the BFFI layer; "
            "\\$a main entry, \\$b edition, \\$f series etc. are not "
            "individually preserved."
        ),
    ),
    MarcEmitMeta(
        tag="546",
        indicators=(" ", " "),
        subfields=(("a", "language note text"),),
        source=(
            "?m or ?work bffi:note [a bffi:Note, <…/mnotetype/lang> ; "
            "rdfs:label ?text] — note typed with the language tail."
        ),
    ),
    MarcEmitMeta(
        tag="524",
        indicators=(" ", " "),
        subfields=(("a", "preferred citation of described materials"),),
        source=("?m or ?work bffi:note [a bffi:Note, <…/mnotetype/citeas> ; rdfs:label ?text]"),
    ),
    MarcEmitMeta(
        tag="525",
        indicators=(" ", " "),
        subfields=(("a", "supplement note text"),),
        source=("?m or ?work bffi:note [a bffi:Note, <…/mnotetype/suppl> ; rdfs:label ?text]"),
    ),
    MarcEmitMeta(
        tag="563",
        indicators=(" ", " "),
        subfields=(("a", "binding information note"),),
        source=("?m or ?work bffi:note [a bffi:Note, <…/mnotetype/binding> ; rdfs:label ?text]"),
    ),
    MarcEmitMeta(
        tag="580",
        indicators=(" ", " "),
        subfields=(("a", "linking entry complexity note"),),
        source=("?m or ?work bffi:note [a bffi:Note, <…/mnotetype/relnote> ; rdfs:label ?text]"),
    ),
    MarcEmitMeta(
        tag="583",
        indicators=(" ", " "),
        subfields=(("a", "preservation / action note"),),
        source=("?m or ?work bffi:note [a bffi:Note, <…/mnotetype/action> ; rdfs:label ?text]"),
        notes=(
            "MARC 583 source carries up to ten subfields (\\$3 materials, "
            "\\$a action, \\$c time, \\$h jurisdiction, \\$k agent, \\$l "
            "status, \\$u uri, \\$z public note, etc.) but marc2bibframe2 "
            "collapses the whole row into a single bf:Note with the "
            "concatenated text as rdfs:label. Reverse emit therefore "
            "produces only \\$a; the structured subfields are not "
            "recoverable from a bare bffi:Note."
        ),
    ),
    MarcEmitMeta(
        tag="587",
        indicators=(" ", " "),
        subfields=(("a", "description-based note (source not formalised)"),),
        source=(
            "?m or ?work bffi:note [a bffi:Note, <…/mnotetype/datasource> ; rdfs:label ?text] "
            "— marc2bibframe2 attaches mnotetype/datasource when MARC ind1=' '. "
            "A separate entry exists for mnotetype/datanf which emits with ind1='0'."
        ),
        notes=(
            "marc2bibframe2 splits 587 into two mnotetype tails by ind1 "
            "(datasource for ind1=' ', datanf for ind1='0'). The reverse "
            "converter restores ind1 from the tail. A single tag entry is "
            "listed here; both tails route to MARC 587."
        ),
    ),
    *(
        MarcEmitMeta(
            tag=tag,
            indicators=(" ", " "),
            subfields=(("a", f"general note text (marcKey-driven recovery of {tag})"),),
            source=(
                f"?m or ?work bffi:note [a bffi:Note ; bffi:marcKey ?key ; rdfs:label ?text] "
                f"where the first 3 chars of ?key are '{tag}'. marc2bibframe2 emits "
                f"a bare bf:Note (no mnotetype tail) for {tag}; the original tag "
                f"survives only on the marcKey carrier."
            ),
            notes=(
                f"Without a mnotetype discriminator the bf:Note from {tag} is "
                f"indistinguishable from 500. The reverse path recovers the tag "
                f"from bffi:marcKey; notes lacking the marcKey carrier fall "
                f"through to 500."
            ),
        )
        for tag in sorted(_MARCKEY_5XX_DISPATCH_TAGS)
    ),
)
def _extract_notes(graph: Graph, manifestation: URIRef) -> list[_NoteEmit]:
    """Walk every ``?m bffi:note ?n . ?n rdfs:label ?text`` and dispatch
    to a MARC tag based on the note's mnotetype rdf:type (or 500 by
    default). Notes consumed by other emit families
    (:data:`_NOTE_TYPES_HANDLED_ELSEWHERE`) are skipped here so they
    don't double-emit.
    """
    emits: list[_NoteEmit] = []
    # marc2bibframe2 distributes notes across the FRBR axes: an
    # accompanying-material note lands on the Manifestation, a
    # participants/performers note (MARC 511) on the Work. Walking only the
    # Manifestation lost every Work-side note — 25 511s on a 308-record
    # corpus. ``_extract_specialised_5xx_notes`` already walks both owners.
    work = _find_work_for_manifestation(graph, manifestation)
    owners: tuple[URIRef, ...] = (manifestation, work) if work is not None else (manifestation,)
    seen: set[URIRef | BNode] = set()
    for note in (n for o in owners for n in graph.objects(o, BFFI.note)):
        if not isinstance(note, URIRef | BNode) or note in seen:
            continue
        seen.add(note)
        if any((note, RDF.type, t) in graph for t in _NOTE_TYPES_HANDLED_ELSEWHERE):
            continue
        label = next(graph.objects(note, RDFS.label), None)
        if not isinstance(label, Literal):
            continue
        target = _note_marc_target(graph, note)
        emits.append(
            _NoteEmit(
                tag=target.tag,
                text=str(label),
                subfield_code=target.subfield_code,
                ind1=target.ind1,
                ind2=target.ind2,
            )
        )
    return sorted(emits, key=lambda e: (e.tag, e.ind1, e.ind2, e.subfield_code, e.text))


def _note_marc_target(graph: Graph, note: Node) -> _NoteTarget:
    """Return the MARC ``(tag, subfield_code)`` for a ``bffi:Note`` bnode.

    Dispatch order:

    1. ``rdf:type <…/mnotetype/<tail>>`` — fast static dispatch (504,
       511, 530, 538, 546, 586, 588, 524, 525, 563, 580, 583, 587, …).
    2. ``bffi:marcKey "5XX  ..."`` — recovers the original MARC tag for
       fall-through 5XX notes whose BIBFRAME source produced a bare
       ``bf:Note`` (501, 513, 515, 516, 533, 536, 544, 545, 547, 550,
       555, 556, 581, 585).
    3. Fall through to ``500 $a`` (general note).
    """
    for note_type, target in _MNOTETYPE_TO_MARC.items():
        if (note, RDF.type, note_type) in graph:
            return target
    for marckey in graph.objects(note, BFFI.marcKey):
        if not isinstance(marckey, Literal):
            continue
        prefix = str(marckey)[:3]
        if prefix in _MARCKEY_5XX_DISPATCH_TAGS:
            return _NoteTarget(prefix)
    return _NoteTarget("500")


#: Specialised 5XX note tags whose BIBFRAME shape is a dedicated class
#: (not a bf:Note + mnotetype). Each entry maps a BFFI predicate to the
#: MARC tag the reverse path emits when the predicate's object is a
#: bnode with the matching class and an ``rdfs:label`` literal.
@dataclass(frozen=True)
class _SpecialisedNoteRule:
    """One BFFI shape → MARC tag rule for a non-bf:Note 5XX field."""

    tag: str
    predicate: URIRef
    expected_class: URIRef | None  # ``None`` for predicates that carry a literal directly
    #: Fallback for nodes that carry no ``rdfs:label``: ordered
    #: ``(predicate, subfield_code)`` pairs read off the node itself. MARC
    #: 518 written with ``$o``/``$d``/``$p``/``$3`` becomes a ``bffi:Capture``
    #: with ``bffi:note`` / ``bffi:date`` / ``bffi:place`` / ``bffi:appliesTo``
    #: and no label at all, so the label-only path skipped it entirely.
    structured: tuple[tuple[URIRef, str], ...] = ()


#: marc2bibframe2 emits a *second*, derived ``bffi:Capture`` alongside the
#: transcribed one: its ``bffi:note`` is the generic word below and its
#: ``bffi:date`` values are EDTF-normalised (``"2023-05-XX"``) rather than
#: the cataloguer's string. Emitting both would double every structured
#: MARC 518. On the reference corpus this discriminator is exact: 5 labelled
#: + 20 transcribed structured captures = the 25 source 518s, with 10
#: derived nodes skipped.
_DERIVED_CAPTURE_NOTE: Final[str] = "capture"


_SPECIALISED_NOTE_RULES: Final[tuple[_SpecialisedNoteRule, ...]] = (
    _SpecialisedNoteRule(tag="502", predicate=BFFI.dissertation, expected_class=BFFI.Dissertation),
    _SpecialisedNoteRule(tag="507", predicate=BFFI.scale, expected_class=BFFI.Scale),
    _SpecialisedNoteRule(
        tag="518",
        predicate=BFFI.capture,
        expected_class=BFFI.Capture,
        structured=(
            (BFFI.note, "o"),
            (BFFI.date, "d"),
            (BFFI.place, "p"),
            (BFFI.appliesTo, "3"),
        ),
    ),
    _SpecialisedNoteRule(
        tag="522",
        predicate=BFFI.geographicCoverage,
        expected_class=BFFI.GeographicCoverage,
    ),
    _SpecialisedNoteRule(
        tag="532",
        predicate=BFFI.contentAccessibility,
        expected_class=BFFI.ContentAccessibility,
    ),
    _SpecialisedNoteRule(
        tag="541",
        predicate=BFFI.immediateAcquisition,
        expected_class=BFFI.ImmediateAcquisition,
    ),
    # 561 in BIBFRAME uses bf:custodialHistory as a literal-valued
    # predicate (not a bnode). The BFFI mirror keeps the same shape.
    _SpecialisedNoteRule(tag="561", predicate=BFFI.custodialHistory, expected_class=None),
)


#: Path segment after which a LoC scheme URI's code begins, e.g.
#: ``…/vocabulary/subjectSchemes/yso/fin`` → ``"yso/fin"``. The 6XX subject
#: family uses bare :func:`local_name` instead, which yields ``"fin"`` for
#: that URI — kept as-is there to avoid churning established 6XX output.
_SCHEME_MARKERS: Final[tuple[str, ...]] = ("/subjectSchemes/", "/genreFormSchemes/")


def _scheme_code(uri: URIRef) -> str:
    """Return a LoC scheme URI's ``$2`` code, keeping sub-scheme segments."""
    text = str(uri)
    for marker in _SCHEME_MARKERS:
        if marker in text:
            return text.split(marker, 1)[1]
    return local_name(uri)


@marc_emit(
    MarcEmitMeta(
        tag="370",
        indicators=(" ", " "),
        subfields=(
            ("g", "associated place term (rdfs:label of the place node)"),
            ("2", "source vocabulary code (local part of the place's bffi:source)"),
        ),
        source=(
            "?m bffi:workManifested ?work . ?work bffi:originPlace ?place . "
            "?place a bffi:Place ; rdfs:label ?g . "
            "$2 = the scheme code of ?place's bffi:source when present."
        ),
        notes=(
            "Source \\$0 (the authority URI) is not recoverable: "
            "marc2bibframe2 drops MARC 370 \\$0 entirely, so it never "
            "reaches the BFFI graph. \\$g and \\$2 round-trip."
        ),
    ),
)
def _extract_origin_place_datafields(graph: Graph, manifestation: URIRef) -> list[_NoteEmit]:
    """Walk ``?work bffi:originPlace`` and emit one MARC 370 per place.

    Reuses :class:`_NoteEmit` as the emit record: 370 has the same shape as
    the note families (one datafield, blank indicators, plain subfields), so
    it renders through :func:`_append_note_datafields` unchanged.
    """
    work = _find_work_for_manifestation(graph, manifestation)
    if work is None:
        return []
    emits: list[_NoteEmit] = []
    for place in graph.objects(work, BFFI.originPlace):
        if not isinstance(place, URIRef | BNode):
            continue
        label = next((x for x in graph.objects(place, RDFS.label) if isinstance(x, Literal)), None)
        if label is None:
            continue
        subs: list[tuple[str, str]] = [("g", str(label))]
        source = next(graph.objects(place, BFFI.source), None)
        if isinstance(source, URIRef):
            subs.append(("2", _scheme_code(source)))
        emits.append(_NoteEmit(tag="370", text="", subfield_code="g", extra_subfields=tuple(subs)))
    return sorted(emits, key=lambda e: e.extra_subfields)


@marc_emit(
    MarcEmitMeta(
        tag="257",
        indicators=(" ", " "),
        subfields=(
            ("a", "producing or filming place"),
            ("2", "source code (local part of the place's bffi:source)"),
        ),
        source=(
            "?i bffi:originPlace ?place — bnode on the bf:Instance, "
            "not the bf:Work (which carries 370). ?place a bffi:Place ; "
            "rdfs:label ?a ; [bffi:source [bffi:code ?2]]? ."
        ),
        notes=(
            "Discriminated from MARC 370 by origin: ``bf:Instance`` carries the "
            "MARC 257 (country of producing or filming place), ``bf:Work`` carries "
            "MARC 370 (associated place). Both emit ``bffi:originPlace`` as a "
            "``bffi:Place`` bnode with ``rdfs:label``. The XSLT does not attach "
            "``bffi:marcKey`` to these fields, so axis is the only discriminator."
        ),
    ),
)
def _extract_producing_place_datafields(graph: Graph, manifestation: URIRef) -> list[_NoteEmit]:
    """Walk ``?instance bffi:originPlace`` and emit one MARC 257 datafield per place.

    Discriminated from 370 by axis: 257 lives on the ``bf:Instance``, 370 on
    the ``bf:Work``.
    """
    emits: list[_NoteEmit] = []
    for place in graph.objects(manifestation, BFFI.originPlace):
        if not isinstance(place, BNode):
            continue
        label = next((x for x in graph.objects(place, RDFS.label) if isinstance(x, Literal)), None)
        if label is None:
            continue
        subs: list[tuple[str, str]] = [("a", str(label))]
        source = next(graph.objects(place, BFFI.source), None)
        if isinstance(source, URIRef):
            subs.append(("2", _scheme_code(source)))
        emits.append(_NoteEmit(tag="257", text="", subfield_code="a", extra_subfields=tuple(subs)))
    return sorted(emits, key=lambda e: e.extra_subfields)


@marc_emit(
    MarcEmitMeta(
        tag="043",
        indicators=(" ", " "),
        subfields=(("a", "geographic area code identifier"),),
        source=(
            "?w bffi:geographicCoverage <URI> — URI reference on the same predicate "
            "as 522 bnode discriminates 043 from 522. The URI is "
            "``http://id.loc.gov/vocabulary/geographicAreas/{code}"
            "."
        ),
        notes=(
            "Discriminated from MARC 522 by shape: ``043`` produces a ``bffi:geographicCoverage`` "
            "URI reference (LoC geographic areas vocabulary), ``522`` produces a "
            "``bffi:GeographicCoverage`` bnode with ``rdfs:label``. The XSLT emits "
            "043 ``$a`` as a vocabulary URI and 522 ``$a`` as a bnode value."
        ),
    ),
)
def _extract_geographic_area_codes(graph: Graph, manifestation: URIRef) -> list[str]:
    """Walk ``bffi:geographicCoverage`` URI references and emit MARC 043 ``$a``.

    Discriminated from 522 by object shape: URI reference = 043, bnode = 522.
    """
    GEOGRAPHIC_AREAS_PREFIX = "http://id.loc.gov/vocabulary/geographicAreas/"
    emits: list[str] = []
    for geo in graph.objects(manifestation, BFFI.geographicCoverage):
        if not isinstance(geo, URIRef):
            continue
        uri_str = str(geo)
        if uri_str.startswith(GEOGRAPHIC_AREAS_PREFIX):
            emits.append(uri_str[len(GEOGRAPHIC_AREAS_PREFIX) :])
    work = _find_work_for_manifestation(graph, manifestation)
    work_anchor = work if work is not None else manifestation
    for geo in graph.objects(work_anchor, BFFI.geographicCoverage):
        if not isinstance(geo, URIRef):
            continue
        uri_str = str(geo)
        if uri_str.startswith(GEOGRAPHIC_AREAS_PREFIX):
            emits.append(uri_str[len(GEOGRAPHIC_AREAS_PREFIX) :])
    return sorted(set(emits))


@marc_emit(
    MarcEmitMeta(
        tag="502",
        indicators=(" ", " "),
        subfields=(("a", "dissertation note text"),),
        source=("?m bffi:dissertation [a bffi:Dissertation ; rdfs:label ?text]"),
        notes=(
            "MARC 502 source carries up to six subfields (\\$a, \\$b, \\$c, "
            "\\$d, \\$g, \\$o) but marc2bibframe2 collapses them into a "
            "single rdfs:label on the bf:Dissertation bnode. Reverse emit "
            "produces only \\$a."
        ),
    ),
    MarcEmitMeta(
        tag="507",
        indicators=(" ", " "),
        subfields=(("a", "scale note text"),),
        source=("?w bffi:scale [a bffi:Scale ; rdfs:label ?text]"),
    ),
    MarcEmitMeta(
        tag="518",
        indicators=(" ", " "),
        subfields=(
            ("a", "date / time / place of event note (flattened Capture label)"),
            ("o", "other event information (structured Capture: bffi:note)"),
            ("d", "date of event (structured Capture: bffi:date)"),
            ("p", "place of event (structured Capture: bffi:place)"),
            ("3", "materials specified (structured Capture: bffi:appliesTo)"),
        ),
        source=(
            "?m or ?work bffi:capture ?c . ?c a bffi:Capture . "
            "Two shapes: when ?c carries an rdfs:label the whole field emits as "
            "\\$a; when it carries none, \\$o / \\$d / \\$p / \\$3 are read from "
            "?c's bffi:note / bffi:date / bffi:place / bffi:appliesTo."
        ),
        notes=(
            "A source 518 written with \\$o \\$d \\$p \\$3 becomes a bffi:Capture "
            "with those structured properties and NO rdfs:label, so the "
            "label-only path emitted nothing for it. marc2bibframe2 also emits "
            "a second, derived Capture per field whose bffi:note is the generic "
            'word "capture" and whose dates are EDTF-normalised; that '
            "companion is skipped to avoid double-emitting."
        ),
    ),
    MarcEmitMeta(
        tag="522",
        indicators=(" ", " "),
        subfields=(("a", "geographic coverage note"),),
        source=(
            "?w bffi:geographicCoverage [a bffi:GeographicCoverage ; rdfs:label ?text] "
            "— bnode-with-class object discriminates 522 from 043 (which uses a "
            "literal code on the same predicate)."
        ),
    ),
    MarcEmitMeta(
        tag="532",
        indicators=(" ", " "),
        subfields=(("a", "accessibility note text"),),
        source=("?m bffi:contentAccessibility [a bffi:ContentAccessibility ; rdfs:label ?text]"),
    ),
    MarcEmitMeta(
        tag="541",
        indicators=(" ", " "),
        subfields=(("a", "immediate source of acquisition note"),),
        source=("?m bffi:immediateAcquisition [a bffi:ImmediateAcquisition ; rdfs:label ?text]"),
        notes=(
            "541 is item-level in MARC. This reverse path emits from the "
            "Manifestation when the BFFI graph attaches the predicate "
            "there; Items are not modelled separately in BFFI 1.0.0."
        ),
    ),
    MarcEmitMeta(
        tag="561",
        indicators=(" ", " "),
        subfields=(("a", "ownership / custodial history"),),
        source=(
            "?m bffi:custodialHistory ?text (literal-valued predicate "
            "— no bnode wrapper, mirroring BIBFRAME's bf:custodialHistory)"
        ),
    ),
)
@marc_emit(
    MarcEmitMeta(
        tag="040",
        indicators=(" ", " "),
        subfields=(
            ("b", "language of cataloguing (bffi:descriptionLanguage)"),
            ("e", "description conventions (bffi:descriptionConventions)"),
        ),
        source=(
            "?am a bffi:AdminMetadata ; bffi:descriptionLanguage ?lang . "
            "$b = local name of ?lang ; "
            "$e = local name of each ?am bffi:descriptionConventions."
        ),
        notes=(
            "**\\$a and \\$d are not recoverable.** The vendored "
            "marc2bibframe2 v3.1.0 comments out both the \\$a → bf:assigner "
            "and \\$d → bf:descriptionModifier blocks in its 040 template "
            "(``ConvSpec-010-048.xsl``), so the cataloguing-agency codes never "
            "reach BIBFRAME and cannot be reconstructed from BFFI. Emitting an "
            "agency guessed from some other assigner in the graph produced the "
            "wrong one for 185 of 190 records on the reference corpus — a "
            "false provenance claim — so only \\$b and \\$e are emitted.\n\n"
            "``bffi:descriptionLanguage`` gates the emit: it is the one "
            "property that comes only from 040 \\$b. Keying on "
            "``descriptionConventions`` instead fabricated a 040 for 46 "
            "records that never had one, because marc2bibframe2 also derives "
            "``aacr`` from leader/18.\n\n"
            "\\$e can gain a value the source lacked: ``isbd`` is asserted "
            "alongside the record's own conventions."
        ),
    ),
)
def _extract_cataloging_source(graph: Graph, manifestation: URIRef) -> list[_NoteEmit]:
    """Rebuild the recoverable half of MARC 040 from AdminMetadata.

    A record carries several ``bffi:AdminMetadata`` blocks — one per
    conversion Activity plus one for the source description. Only the
    source-description block has ``bffi:descriptionLanguage``, so that
    property both selects the block and gates the emit; the pipeline's own
    blocks must not produce a cataloguing-source claim.
    """
    del manifestation  # 040 describes the record, not a particular FRBR axis.
    for am in graph.subjects(RDF.type, BFFI.AdminMetadata):
        language = next(graph.objects(am, BFFI.descriptionLanguage), None)
        if not isinstance(language, URIRef):
            continue
        subs: list[tuple[str, str]] = [("b", local_name(language))]
        subs.extend(
            ("e", local_name(conv))
            for conv in sorted(graph.objects(am, BFFI.descriptionConventions), key=str)
            if isinstance(conv, URIRef)
        )
        return [_NoteEmit(tag="040", text="", subfield_code="b", extra_subfields=tuple(subs))]
    return []


def _extract_specialised_5xx_notes(graph: Graph, manifestation: URIRef) -> list[_NoteEmit]:
    """Walk the seven BFFI properties that produce a non-bf:Note 5XX
    field in MARC. Each :class:`_SpecialisedNoteRule` names the
    predicate to walk and (for bnode-valued shapes) the class that
    discriminates the bnode."""
    work = _find_work_for_manifestation(graph, manifestation)
    owners: tuple[URIRef, ...] = (manifestation, work) if work is not None else (manifestation,)

    emits: list[_NoteEmit] = []
    for rule in _SPECIALISED_NOTE_RULES:
        for owner in owners:
            for obj in graph.objects(owner, rule.predicate):
                if rule.expected_class is None:
                    # Predicate-with-literal shape (561 bffi:custodialHistory).
                    if isinstance(obj, Literal):
                        emits.append(_NoteEmit(tag=rule.tag, text=str(obj), subfield_code="a"))
                    continue
                # Bnode-with-class shape. Object must be a node typed
                # with the expected class and carrying an ``rdfs:label``.
                if isinstance(obj, Literal):
                    continue
                if (obj, RDF.type, rule.expected_class) not in graph:
                    continue
                labels = [x for x in graph.objects(obj, RDFS.label) if isinstance(x, Literal)]
                if labels:
                    for label in labels:
                        emits.append(_NoteEmit(tag=rule.tag, text=str(label), subfield_code="a"))
                    continue
                if not isinstance(obj, URIRef | BNode):
                    continue
                extras = _structured_subfields(graph, obj, rule.structured)
                if any(
                    code == "o" and value.strip().casefold() == _DERIVED_CAPTURE_NOTE
                    for code, value in extras
                ):
                    # Derived companion node, not a transcribed field.
                    continue
                if extras:
                    emits.append(
                        _NoteEmit(tag=rule.tag, text="", subfield_code="a", extra_subfields=extras)
                    )
    return sorted(emits, key=lambda e: (e.tag, e.text, e.extra_subfields))


def _structured_subfields(
    graph: Graph, node: URIRef | BNode, spec: tuple[tuple[URIRef, str], ...]
) -> tuple[tuple[str, str], ...]:
    """Read ``spec``'s predicates off ``node`` into ordered MARC subfields.

    Each value is either a literal (used verbatim) or a node whose
    ``rdfs:label`` carries the text. Predicates with no value are skipped,
    so the emitted field carries exactly the subfields the source had.
    """
    out: list[tuple[str, str]] = []
    for predicate, code in spec:
        for value in graph.objects(node, predicate):
            if isinstance(value, Literal):
                out.append((code, str(value)))
                continue
            label = next(
                (x for x in graph.objects(value, RDFS.label) if isinstance(x, Literal)), None
            )
            if label is not None:
                out.append((code, str(label)))
    return tuple(out)


_SERIES_RELATIONSHIP: Final[URIRef] = URIRef("http://id.loc.gov/vocabulary/relationship/series")
_MSTATUS_TRANSCRIBED: Final[URIRef] = URIRef("http://id.loc.gov/vocabulary/mstatus/t")
_MSTATUS_TRANSCRIBED_AND_TRACED: Final[URIRef] = URIRef("http://id.loc.gov/vocabulary/mstatus/tr")


#: 7XX linking-entry families: each tag's forward XSLT emits a
#: ``bf:relation`` carrying a specific ``relationship/<token>`` URI on
#: the ``bf:relationship`` slot. The reverse walks ``bffi:relation`` and
#: dispatches on the URI back to its MARC tag.
#:
#: ``relationship/series`` (760 main-series entry) is intentionally
#: omitted — :func:`_extract_traced_series` already consumes it for the
#: 800/810/811/830 traced-series families when a marcKey is present;
#: distinguishing the bare 760 case from a traced-series Hub needs
#: extra structural cues and is deferred.
_LINKING_RELATIONSHIP_TO_MARC: Final[dict[URIRef, str]] = {
    URIRef("http://id.loc.gov/vocabulary/relationship/subseries"): "762",
    URIRef("http://id.loc.gov/vocabulary/relationship/translationof"): "765",
    URIRef("http://id.loc.gov/vocabulary/relationship/translatedas"): "767",
    URIRef("http://id.loc.gov/vocabulary/relationship/supplement"): "770",
    URIRef("http://id.loc.gov/vocabulary/relationship/supplementto"): "772",
    URIRef("http://id.loc.gov/vocabulary/relationship/partof"): "773",
    URIRef("http://id.loc.gov/vocabulary/relationship/part"): "774",
    URIRef("http://id.loc.gov/vocabulary/relationship/otheredition"): "775",
    URIRef("http://id.loc.gov/vocabulary/relationship/otherphysicalformat"): "776",
    URIRef("http://id.loc.gov/vocabulary/relationship/issuedwith"): "777",
    URIRef("http://id.loc.gov/vocabulary/relationship/datasource"): "786",
    URIRef("http://id.loc.gov/vocabulary/relationship/relatedwork"): "787",
}


#: marcKey tag prefixes already consumed by another emit family. A related
#: resource carrying one of these must not also produce a linking entry:
#: marc2bibframe2 marks every MARC 730/740 analytic as
#: ``relationship/relatedwork``, so emitting on the relationship alone
#: produced a duplicate 787 beside each correct 730 — 223 fabricated fields
#: on the reference corpus, 84 of them on a single box-set record. Same
#: reasoning as the ``relationship/series`` omission below.
_MARCKEY_TAGS_CLAIMED_ELSEWHERE: Final[frozenset[str]] = frozenset(
    {"700", "710", "711", "730", "740", "800", "810", "811", "830"}
)


#: Series-relation tags emitted via marcKey-driven recovery. 800 is
#: personal-name traced series, 810 corporate, 811 meeting, 830 the
#: uniform-title catch-all. Each Hub URI's marcKey starts with the
#: source MARC tag; the discriminator is purely the first 3 chars.
_TRACED_SERIES_TAGS: Final[frozenset[str]] = frozenset({"800", "810", "811", "830"})


@marc_emit(
    MarcEmitMeta(
        tag="800",
        indicators=("1", " "),
        subfields=(
            ("a", "personal-name series statement"),
            ("t", "title (marcKey-driven)"),
            ("v", "volume number (marcKey-driven)"),
        ),
        source=(
            "?source bffi:relation [bffi:relationship <…/relationship/series> "
            "; bffi:associatedResource ?hub] . ?hub bffi:marcKey ?key "
            "(where ?key begins with '800')"
        ),
    ),
    MarcEmitMeta(
        tag="810",
        indicators=("2", " "),
        subfields=(
            ("a", "corporate-name series statement"),
            ("b", "subordinate unit (marcKey-driven)"),
            ("t", "title (marcKey-driven)"),
            ("v", "volume number (marcKey-driven)"),
            ("n", "number of part (marcKey-driven)"),
        ),
        source=(
            "?source bffi:relation [bffi:relationship <…/relationship/series> "
            "; bffi:associatedResource ?hub] . ?hub bffi:marcKey ?key "
            "(where ?key begins with '810')"
        ),
    ),
    MarcEmitMeta(
        tag="811",
        indicators=("2", " "),
        subfields=(
            ("a", "meeting-name series statement"),
            ("t", "title (marcKey-driven)"),
            ("v", "volume number (marcKey-driven)"),
        ),
        source=(
            "?source bffi:relation [bffi:relationship <…/relationship/series> "
            "; bffi:associatedResource ?hub] . ?hub bffi:marcKey ?key "
            "(where ?key begins with '811')"
        ),
    ),
    MarcEmitMeta(
        tag="830",
        indicators=(" ", " "),
        subfields=(
            ("a", "uniform-title series statement"),
            ("n", "number of part / section (marcKey-driven)"),
            ("v", "volume number (marcKey-driven)"),
        ),
        source=(
            "?source bffi:relation [a bffi:Relation ; "
            "bffi:relationship <…/relationship/series> ; "
            "bffi:associatedResource ?hub] . "
            "?hub a bffi:SeriesExpression ; bffi:marcKey ?key "
            "(where ?key begins with '830') — full subfield set parsed "
            "from marcKey verbatim."
        ),
    ),
)
def _extract_traced_series(graph: Graph, manifestation: URIRef) -> list[_AddedTitleEmit]:
    """Walk ``?source bffi:relation [bffi:relationship <series> ;
    bffi:associatedResource ?hub]`` from both the Manifestation and
    its Work, looking for Hub Works whose ``bffi:marcKey`` starts with
    one of the four traced-series tags (800 / 810 / 811 / 830). Each
    emits as a MARC datafield with the full parsed subfield set —
    same marcKey-driven recovery as 730/740/130."""
    work = _find_work_for_manifestation(graph, manifestation)
    anchors: list[URIRef] = [manifestation]
    if work is not None:
        anchors.append(work)
    emits: list[_AddedTitleEmit] = []
    seen_hubs: set[Node] = set()
    for anchor in anchors:
        for rel in graph.objects(anchor, BFFI.relation):
            if (rel, BFFI.relationship, _SERIES_RELATIONSHIP) not in graph:
                continue
            for target in graph.objects(rel, BFFI.associatedResource):
                if not isinstance(target, URIRef) or target in seen_hubs:
                    continue
                seen_hubs.add(target)
                marc_key = next(graph.objects(target, BFFI.marcKey), None)
                if not isinstance(marc_key, Literal):
                    continue
                parsed = _parse_marc_key(str(marc_key))
                if parsed is None:
                    continue
                tag, ind1, ind2, subfields = parsed
                if tag not in _TRACED_SERIES_TAGS or not subfields:
                    continue
                emits.append(_AddedTitleEmit(tag=tag, ind1=ind1, ind2=ind2, subfields=subfields))
    return sorted(emits, key=lambda e: (e.tag, e.subfields))


_LINKING_ENTRY_MARC_TAGS: Final[frozenset[str]] = frozenset(_LINKING_RELATIONSHIP_TO_MARC.values())


def _emit_linking_entry_for_relationship(
    graph: Graph,
    *,
    associated: URIRef,
    tag: str,
) -> _AddedTitleEmit | None:
    """Build one linking-entry emit from an ``bffi:associatedResource`` node.

    Tries marcKey-driven recovery first (preserves the full subfield
    sequence); falls back to ``rdfs:label`` for ``$a`` and the bnode's
    ``bffi:title / bffi:Title / bffi:mainTitle`` for ``$t`` when no
    marcKey is present.

    Returns ``None`` when no usable content can be extracted.
    """
    marc_key = next(graph.objects(associated, BFFI.marcKey), None)
    if isinstance(marc_key, Literal):
        parsed = _parse_marc_key(str(marc_key))
        if parsed is not None:
            mk_tag, ind1, ind2, subfields = parsed
            # marcKey-driven recovery wins when the carrier names this tag
            # (or the tag is otherwise in the linking family); preserve
            # parsed indicators verbatim.
            if subfields and mk_tag in _LINKING_ENTRY_MARC_TAGS:
                return _AddedTitleEmit(tag=mk_tag, ind1=ind1, ind2=ind2, subfields=subfields)

    subfields_list: list[tuple[str, str]] = []
    label = next(graph.objects(associated, RDFS.label), None)
    if isinstance(label, Literal):
        subfields_list.append(("a", str(label)))

    title_text = _extract_first_title_text(graph, associated)
    if title_text is not None:
        subfields_list.append(("t", title_text))

    if not subfields_list:
        return None
    return _AddedTitleEmit(tag=tag, ind1=" ", ind2=" ", subfields=tuple(subfields_list))


def _extract_first_title_text(graph: Graph, resource: URIRef) -> str | None:
    """Return the first ``bffi:title → bffi:Title → bffi:mainTitle`` text
    on ``resource``, or ``None`` if no title is present."""
    for title_block in graph.objects(resource, BFFI.title):
        main = next(graph.objects(title_block, BFFI.mainTitle), None)
        if isinstance(main, Literal):
            return str(main)
    return None


@marc_emit(
    *(
        MarcEmitMeta(
            tag=tag,
            indicators=("0", " ") if tag == "773" else (" ", " "),
            subfields=(
                ("a", "main entry heading of the related resource"),
                ("t", "title of the related resource"),
                ("w", "control number of the related resource (marcKey-driven)"),
            ),
            source=(
                f"?source bffi:relation [a bffi:Relation ; "
                f"bffi:relationship <…/relationship/{token.rsplit('/', 1)[-1]}> ; "
                f"bffi:associatedResource ?related] . Either ?related "
                f"bffi:marcKey ?key (preferred — emits full subfield set "
                f"and indicators) or ?related rdfs:label ?a + bffi:title / "
                f"bffi:Title / bffi:mainTitle ?t (fallback)."
            ),
            notes=(
                "MARC linking entry. Per-ind2 sub-dispatch (which 780/785-style "
                "splits would require) is not modelled here. $w / $i / $x / $z "
                "are only reconstructed via marcKey-driven recovery; the "
                "label/title fallback emits just $a and $t."
            )
            if tag in {"773", "775", "776"}
            else "",
        )
        for token, tag in _LINKING_RELATIONSHIP_TO_MARC.items()
    )
)
def _extract_linking_entries(graph: Graph, manifestation: URIRef) -> list[_AddedTitleEmit]:
    """Walk every ``bffi:relation`` on the Manifestation and its Work
    whose ``bffi:relationship`` URI is in
    :data:`_LINKING_RELATIONSHIP_TO_MARC` and emit one MARC linking-entry
    datafield per associated resource.

    Co-exists with :func:`_extract_traced_series` — the two consume
    disjoint relationship URIs (series → traced series; the rest →
    linking entries).

    Resources whose ``bffi:marcKey`` names a tag another family already
    emits (:data:`_MARCKEY_TAGS_CLAIMED_ELSEWHERE`) are skipped. The
    relationship URI alone is not enough to decide ownership: every MARC
    730/740 analytic is also typed ``relationship/relatedwork``, so keying
    on it emitted a spurious 787 alongside each correct 730.
    """
    work = _find_work_for_manifestation(graph, manifestation)
    anchors: list[URIRef] = [manifestation]
    if work is not None:
        anchors.append(work)
    emits: list[_AddedTitleEmit] = []
    seen: set[tuple[Node, str]] = set()
    for anchor in anchors:
        for relation in graph.objects(anchor, BFFI.relation):
            rel_uri = next(graph.objects(relation, BFFI.relationship), None)
            if not isinstance(rel_uri, URIRef):
                continue
            tag = _LINKING_RELATIONSHIP_TO_MARC.get(rel_uri)
            if tag is None:
                continue
            for associated in graph.objects(relation, BFFI.associatedResource):
                if not isinstance(associated, URIRef):
                    continue
                marckey = next(graph.objects(associated, BFFI.marcKey), None)
                if (
                    isinstance(marckey, Literal)
                    and str(marckey)[:3] in _MARCKEY_TAGS_CLAIMED_ELSEWHERE
                ):
                    # Added-entry / traced-series family owns this resource.
                    continue
                if (associated, RDF.type, _UNCONTROLLED_TYPE) in graph:
                    # bffi:Uncontrolled belongs to the MARC 653 family; an
                    # uncontrolled index term is not a related work.
                    continue
                key = (associated, tag)
                if key in seen:
                    continue
                seen.add(key)
                emit = _emit_linking_entry_for_relationship(graph, associated=associated, tag=tag)
                if emit is not None:
                    emits.append(emit)
    return sorted(emits, key=lambda e: (e.tag, e.subfields))


@dataclass(frozen=True)
class _UntracedSeriesEmit:
    """One MARC 490 datafield: title statement plus optional volume number."""

    title: str
    volume: str | None


@marc_emit(
    MarcEmitMeta(
        tag="490",
        indicators=("0", " "),
        subfields=(
            ("a", "untraced series statement"),
            ("v", "volume number"),
        ),
        source=(
            "?work bffi:relation [a bffi:Relation ; "
            "bffi:relationship <…/relationship/series> ; "
            "bffi:associatedResource ?s ; bffi:seriesEnumeration ?vol] . "
            "?s a bffi:SeriesExpression ; bffi:status <…/mstatus/t> ; "
            "NOT EXISTS { ?s bffi:status <…/mstatus/tr> } . "
            "?s bffi:title / bffi:mainTitle ?text — $v from the "
            "Relation's bffi:seriesEnumeration when present."
        ),
        notes=(
            "490 is the *untraced* series statement (no 8XX partner). The "
            "discriminator is mstatus/t alone — a co-typed mstatus/tr "
            "signals that an 830 controlled-series partner exists and the "
            "transcribed view is suppressed here to avoid double-emit. "
            "ind1=0 (Series not traced) per HELMET corpus convention. ISBD "
            'trailing " ;" is added on $a when $v follows.'
        ),
    )
)
def _extract_untraced_series(graph: Graph, manifestation: URIRef) -> list[_UntracedSeriesEmit]:
    """Walk ``?source bffi:relation [bffi:relationship <series> ;
    bffi:associatedResource ?s ; bffi:seriesEnumeration ?vol]`` from
    both the Manifestation and its Work, and emit MARC 490 for every
    series resource that represents an untraced series statement
    (mstatus/t but not mstatus/tr). $v comes from the Relation's
    bffi:seriesEnumeration when present."""
    work = _find_work_for_manifestation(graph, manifestation)
    anchors: list[URIRef] = [manifestation]
    if work is not None:
        anchors.append(work)
    emits: list[_UntracedSeriesEmit] = []
    for anchor in anchors:
        for rel in graph.objects(anchor, BFFI.relation):
            if (rel, BFFI.relationship, _SERIES_RELATIONSHIP) not in graph:
                continue
            volume_lit = next(graph.objects(rel, BFFI.seriesEnumeration), None)
            volume = str(volume_lit) if isinstance(volume_lit, Literal) else None
            for target in graph.objects(rel, BFFI.associatedResource):
                if not _is_untraced_series(graph, target):
                    continue
                for title_block in graph.objects(target, BFFI.title):
                    main = next(graph.objects(title_block, BFFI.mainTitle), None)
                    if isinstance(main, Literal):
                        emits.append(_UntracedSeriesEmit(title=str(main), volume=volume))
                        break
    return sorted(emits, key=lambda e: (e.title, e.volume or ""))


def _is_untraced_series(graph: Graph, target: Node) -> bool:
    """True when the series resource has mstatus/t (transcribed) and
    NOT mstatus/tr (transcribed + traced — the synthetic companion
    marc2bibframe2 mints alongside a controlled 830)."""
    has_t = (target, BFFI.status, _MSTATUS_TRANSCRIBED) in graph
    has_tr = (target, BFFI.status, _MSTATUS_TRANSCRIBED_AND_TRACED) in graph
    return has_t and not has_tr


@dataclass(frozen=True)
class _PolicyEmits:
    """506 (restrictions on access) + 540 (terms governing use) emits."""

    access: tuple[str, ...]
    use: tuple[str, ...]


@marc_emit(
    MarcEmitMeta(
        tag="506",
        indicators=(" ", " "),
        subfields=(("a", "terms governing access — note text"),),
        source="?m bffi:usageAndAccessPolicy [a bffi:AccessPolicy ; rdfs:label ?text]",
    ),
    MarcEmitMeta(
        tag="540",
        indicators=(" ", " "),
        subfields=(("a", "terms governing use and reproduction — note text"),),
        source="?m bffi:usageAndAccessPolicy [a bffi:UsePolicy ; rdfs:label ?text]",
        notes=(
            "506 vs 540 are disambiguated by the policy's rdf:type: "
            "bffi:AccessPolicy → 506 (restrictions on who can access); "
            "bffi:UsePolicy → 540 (what users may do with the material). "
            "Both predicates funnel through bffi:usageAndAccessPolicy."
        ),
    ),
)
def _extract_policies(graph: Graph, manifestation: URIRef) -> _PolicyEmits:
    """Walk ``bffi:usageAndAccessPolicy`` blocks and split by rdf:type:
    ``bffi:AccessPolicy`` → MARC 506; ``bffi:UsePolicy`` → MARC 540."""
    access: list[str] = []
    use: list[str] = []
    for policy in graph.objects(manifestation, BFFI.usageAndAccessPolicy):
        label = next(graph.objects(policy, RDFS.label), None)
        if not isinstance(label, Literal):
            continue
        if (policy, RDF.type, BFFI.UsePolicy) in graph:
            use.append(str(label))
        else:
            # AccessPolicy is the default — bf:AccessPolicy was the
            # original BIBFRAME type before the split; treat untyped
            # policies as access-restriction notes (the safer fallback).
            access.append(str(label))
    return _PolicyEmits(access=tuple(sorted(access)), use=tuple(sorted(use)))


@marc_emit(
    MarcEmitMeta(
        tag="505",
        indicators=("0", " "),
        subfields=(("a", "formatted contents note"),),
        source=("?m bffi:tableOfContents [a bffi:TableOfContents ; rdfs:label ?text]"),
    )
)
def _extract_table_of_contents(graph: Graph, manifestation: URIRef) -> list[str]:
    """Return every ``bffi:tableOfContents`` block's ``rdfs:label`` —
    each emits as a MARC 505 datafield carrying the formatted contents
    note text in ``$a``.

    ``tableOfContents`` may live on the Manifestation or on the Work; walk
    both anchors so we recover the field whether marc2bibframe2 attached
    it to the Instance- or Work-side predicate.
    """
    texts: list[str] = []
    anchors = [manifestation]
    work = _find_work_for_manifestation(graph, manifestation)
    if work is not None:
        anchors.append(work)
    for anchor in anchors:
        for toc in graph.objects(anchor, BFFI.tableOfContents):
            label = next(graph.objects(toc, RDFS.label), None)
            if isinstance(label, Literal):
                texts.append(str(label))
    return sorted(texts)


def _extract_labelled_block_texts(
    graph: Graph, manifestation: URIRef, predicate: URIRef
) -> list[str]:
    """Walk ``?m <predicate> ?block . ?block rdfs:label ?text`` and
    return the labels sorted for determinism. The common shape for
    several simple-$a-note MARC emits (520 / 310 / 521)."""
    texts: list[str] = []
    for block in graph.objects(manifestation, predicate):
        label = next(graph.objects(block, RDFS.label), None)
        if isinstance(label, Literal):
            texts.append(str(label))
    return sorted(texts)


@marc_emit(
    MarcEmitMeta(
        tag="520",
        indicators=(" ", " "),
        subfields=(("a", "summary note text"),),
        source="?m bffi:summary [a bffi:Summary ; rdfs:label ?text]",
    )
)
def _extract_summaries(graph: Graph, manifestation: URIRef) -> list[str]:
    return _extract_labelled_block_texts(graph, manifestation, BFFI.summary)


#: ``bffi:status`` URIs that mark a ``bffi:Frequency`` bnode as "former"
#: in BIBFRAME (and therefore reverse-emit at MARC 321 rather than 310).
_FREQUENCY_FORMER_STATUSES: Final[frozenset[URIRef]] = frozenset(
    {URIRef("http://id.loc.gov/vocabulary/mstatus/former")}
)


@dataclass(frozen=True)
class _FrequencyEmit:
    """One MARC 310 / 321 frequency datafield: tag + ``$a`` label."""

    tag: str
    text: str


@marc_emit(
    MarcEmitMeta(
        tag="310",
        indicators=(" ", " "),
        subfields=(("a", "current publication frequency"),),
        source=(
            "?m bffi:frequency [a bffi:Frequency ; rdfs:label ?text] — "
            "with NO bffi:status (or status not in the former-status set). "
            "Bnodes typed with bffi:status <…/mstatus/former> emit as 321 "
            "instead."
        ),
    ),
    MarcEmitMeta(
        tag="321",
        indicators=(" ", " "),
        subfields=(("a", "former publication frequency"),),
        source=(
            "?m bffi:frequency [a bffi:Frequency ; bffi:status ?s ; rdfs:label ?text] "
            "where ?s is a member of the former-status URI set "
            "(<…/mstatus/former>)."
        ),
    ),
)
def _extract_frequency(graph: Graph, manifestation: URIRef) -> list[_FrequencyEmit]:
    """Walk every ``bffi:frequency`` block and dispatch to 310 (current)
    or 321 (former) based on the block's ``bffi:status`` URI.

    Frequency blocks with no ``bffi:status``, or with a status URI not
    in :data:`_FREQUENCY_FORMER_STATUSES`, default to 310 — current
    publication frequency is the unmarked case in BIBFRAME source MARC.
    """
    emits: list[_FrequencyEmit] = []
    for block in graph.objects(manifestation, BFFI.frequency):
        label = next(graph.objects(block, RDFS.label), None)
        if not isinstance(label, Literal):
            continue
        tag = "310"
        for status in graph.objects(block, BFFI.status):
            if isinstance(status, URIRef) and status in _FREQUENCY_FORMER_STATUSES:
                tag = "321"
                break
        emits.append(_FrequencyEmit(tag=tag, text=str(label)))
    return sorted(emits, key=lambda e: (e.tag, e.text))


@marc_emit(
    MarcEmitMeta(
        tag="306",
        indicators=(" ", " "),
        subfields=(("a", "playing time (HHMMSS)"),),
        source=(
            "?m bffi:duration ?literal — the duration literal is emitted "
            "verbatim into $a. marc2bibframe2 stores it as a plain string "
            "literal under bf:duration."
        ),
    )
)
def _extract_playing_times(graph: Graph, manifestation: URIRef) -> list[str]:
    """Return every ``bffi:duration`` literal on the Manifestation.

    Each emits as one MARC 306 datafield with the literal verbatim in
    ``$a``. Multiple durations on a single record produce multiple
    datafields per MARC convention."""
    durations: list[str] = []
    for obj in graph.objects(manifestation, BFFI.duration):
        if isinstance(obj, Literal):
            durations.append(str(obj))
    return sorted(durations)


@marc_emit(
    MarcEmitMeta(
        tag="334",
        indicators=(" ", " "),
        subfields=(("a", "mode of issuance term"),),
        source=(
            "?m bffi:issuance [a bffi:Issuance ; rdfs:label ?text] — "
            "marc2bibframe2 emits bf:issuance with a bf:Issuance bnode "
            "carrying the controlled vocabulary label."
        ),
    )
)
def _extract_modes_of_issuance(graph: Graph, manifestation: URIRef) -> list[str]:
    """Walk ``?m bffi:issuance [a bffi:Issuance ; rdfs:label ?text]`` and
    return the labels — each emits as MARC 334 ``$a``."""
    texts: list[str] = []
    for block in graph.objects(manifestation, BFFI.issuance):
        if (block, RDF.type, BFFI.Issuance) not in graph:
            continue
        label = next(graph.objects(block, RDFS.label), None)
        if isinstance(label, Literal):
            texts.append(str(label))
    return sorted(texts)


@marc_emit(
    MarcEmitMeta(
        tag="521",
        indicators=(" ", " "),
        subfields=(("a", "intended audience note"),),
        source="?m bffi:intendedAudience [a bffi:IntendedAudience ; rdfs:label ?text]",
    )
)
def _extract_intended_audiences(graph: Graph, manifestation: URIRef) -> list[str]:
    return _extract_labelled_block_texts(graph, manifestation, BFFI.intendedAudience)


@dataclass(frozen=True)
class _ClassificationEmit:
    """One MARC classification datafield: tag + portion + optional \\$2 scheme."""

    tag: str
    portion: str
    code: str | None


#: BFFI Classification subclass → MARC tag. Plain ``bffi:Classification``
#: (with no subtype) falls through to 084. The six subclass URIs are
#: from `lkd.rdf` and map to the LoC-standard 05X / 06X / 07X / 08X
#: classification fields.
_CLASSIFICATION_TYPE_TO_MARC_TAG: Final[dict[URIRef, str]] = {
    URIRef("http://urn.fi/URN:NBN:fi:schema:bffi:ClassificationLcc"): "050",
    URIRef("http://urn.fi/URN:NBN:fi:schema:bffi:ClassificationNlm"): "060",
    URIRef("http://urn.fi/URN:NBN:fi:schema:bffi:ClassificationNal"): "070",
    URIRef("http://urn.fi/URN:NBN:fi:schema:bffi:ClassificationUdc"): "080",
    URIRef("http://urn.fi/URN:NBN:fi:schema:bffi:ClassificationDdc"): "082",
    # MARC 086 — Government Document Classification. Uses the plain
    # ``bffi:Classification`` type with a ``bffi:source`` whose URI
    # ends in ``/classifications/gpo`` (the LoC source URI for 086).
    # This is NOT the same as the plain-Classification catch-all that
    # dispatches to 084 — the source URI discriminator is checked first
    # in :func:`_classification_marc_tag`.
}


_CLASSIFICATION_SUBFIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("a", "classification number"),
    ("2", "scheme code (e.g. 'ykl'); only when bffi:source present"),
)


@marc_emit(
    MarcEmitMeta(
        tag="050",
        indicators=(" ", " "),
        subfields=_CLASSIFICATION_SUBFIELDS,
        source=(
            "?work bffi:classification [a bffi:ClassificationLcc ; bffi:classificationPortion ?n]"
        ),
    ),
    MarcEmitMeta(
        tag="060",
        indicators=(" ", " "),
        subfields=_CLASSIFICATION_SUBFIELDS,
        source=(
            "?work bffi:classification [a bffi:ClassificationNlm ; bffi:classificationPortion ?n]"
        ),
    ),
    MarcEmitMeta(
        tag="070",
        indicators=(" ", " "),
        subfields=_CLASSIFICATION_SUBFIELDS,
        source=(
            "?work bffi:classification [a bffi:ClassificationNal ; bffi:classificationPortion ?n]"
        ),
    ),
    MarcEmitMeta(
        tag="080",
        indicators=(" ", " "),
        subfields=_CLASSIFICATION_SUBFIELDS,
        source=(
            "?work bffi:classification [a bffi:ClassificationUdc ; bffi:classificationPortion ?n]"
        ),
    ),
    MarcEmitMeta(
        tag="082",
        indicators=(" ", " "),
        subfields=_CLASSIFICATION_SUBFIELDS,
        source=(
            "?work bffi:classification [a bffi:ClassificationDdc ; bffi:classificationPortion ?n]"
        ),
    ),
    MarcEmitMeta(
        tag="084",
        indicators=(" ", " "),
        subfields=_CLASSIFICATION_SUBFIELDS,
        source=(
            "?m bffi:workManifested ?work . "
            "?work bffi:classification [a bffi:Classification ; "
            "bffi:classificationPortion ?number ; "
            "bffi:source [a bffi:Source ; bffi:code ?code]] — "
            "the catch-all for plain bffi:Classification blocks; "
            "subclassed UDC / DDC / LCC / NLM / NAL classifications "
            "dispatch to 080 / 082 / 050 / 060 / 070."
        ),
        notes=(
            "$2 emitted when bffi:source / bffi:code is present (e.g. 'ykl'); "
            "omitted otherwise. **HELMET-local 09X classifications "
            "(091/092/093/094/095/097) are not reconstructable from BFFI** — "
            "marc2bibframe2's ConvSpec-050-088.xsl has a template only for "
            "MARC 084 and the standard 050-088 tags; the 09X tags fall "
            'through to its default "drop unhandled datafield" path and '
            "never reach BIBFRAME XML. Corpus coverage is high (091/097 ~98 %, "
            "095 ~79 %, 092 ~55 %, 094 ~30 %, 093 ~20 %), so the loss is "
            "material. Consumers who need 09X data must read the source "
            "MARCXML directly."
        ),
    ),
    MarcEmitMeta(
        tag="086",
        indicators=(" ", " "),
        subfields=_CLASSIFICATION_SUBFIELDS,
        source=(
            "?m bffi:workManifested ?work . "
            "?work bffi:classification [a bffi:Classification ; "
            "bffi:classificationPortion ?number ; "
            "bffi:source [a bffi:Source ; bffi:code 'gpo']] — "
            "Government Document Classification. Plain bffi:Classification "
            "with bffi:source URI ending in /classifications/gpo dispatches "
            "to 086 instead of the 084 catch-all."
        ),
        notes=(
            "$2 emitted when bffi:source / bffi:code is present. The source "
            "URI discriminator is the LoC ``…/classifications/gpo`` URI, "
            "which marc2bibframe2 emits for MARC 086."
        ),
    ),
)
def _extract_classifications(graph: Graph, manifestation: URIRef) -> list[_ClassificationEmit]:
    """Walk every classification block on the Work and dispatch each
    to a MARC tag based on its most-specific ``rdf:type``. UDC / DDC /
    LCC / NLM / NAL subclasses route to 080 / 082 / 050 / 060 / 070;
    plain ``bffi:Classification`` falls through to 084.
    """
    work = _find_work_for_manifestation(graph, manifestation)
    if work is None:
        return []
    emits: list[_ClassificationEmit] = []
    for cls_block in graph.objects(work, BFFI.classification):
        portion = next(graph.objects(cls_block, BFFI.classificationPortion), None)
        if not isinstance(portion, Literal):
            continue
        tag = _classification_marc_tag(graph, cls_block)
        code: str | None = None
        source_block = next(graph.objects(cls_block, BFFI.source), None)
        if source_block is not None:
            code_lit = next(graph.objects(source_block, BFFI.code), None)
            if isinstance(code_lit, Literal):
                code = str(code_lit)
        emits.append(_ClassificationEmit(tag=tag, portion=str(portion), code=code))
    return sorted(emits, key=lambda e: (e.tag, e.portion, e.code or ""))


def _classification_marc_tag(graph: Graph, cls_block: Node) -> str:
    """Pick the MARC tag for a classification block. Prefers the
    LoC-standard 05X/06X/07X/08X tag when the block is typed with a
    specific Classification subclass; dispatches to 086 when the block
    is plain ``bffi:Classification`` with a ``bffi:source`` URI ending
    in ``/classifications/gpo`` (the LoC Government Publishing Office
    source); falls back to 084 otherwise."""
    for cls_type, tag in _CLASSIFICATION_TYPE_TO_MARC_TAG.items():
        if (cls_block, RDF.type, cls_type) in graph:
            return tag
    # Plain bffi:Classification with bffi:source <…/classifications/gpo>
    # is MARC 086 (Government Document Classification).
    if _is_gpo_classification(graph, cls_block):
        return "086"
    return "084"


def _is_gpo_classification(graph: Graph, cls_block: Node) -> bool:
    """True when ``cls_block`` is a plain ``bffi:Classification`` whose
    ``bffi:source`` URI ends in ``/classifications/gpo``. This is how
    MARC 086 round-trips: marc2bibframe2 emits ``bf:Classification`` +
    ``bf:Source`` pointing at ``…/classifications/gpo``.
    """
    for source in graph.objects(cls_block, BFFI.source):
        if isinstance(source, URIRef) and str(source).endswith("/classifications/gpo"):
            return True
    return False


# --- 037 / 353 — acquisition source & supplementary content --------------


@dataclass(frozen=True)
class _AcquisitionSourceEmit:
    """MARC 037 components: stock number ($a), imprint ($b), place of
    publication ($c), other physical details ($f), dimensions ($g),
    copies held ($n)."""

    stock_number: str | None
    imprint: str | None
    place: str | None
    other_physical: str | None
    dimensions: str | None
    copies: str | None


@dataclass(frozen=True)
class _SupplementaryContentEmit:
    """MARC 353 components: content ($a), level of analysis ($b),
    authority ($0), source ($2)."""

    content: str | None
    level: str | None
    authority_uri: str | None
    source: str | None


@marc_emit(
    MarcEmitMeta(
        tag="037",
        indicators=(" ", " "),
        subfields=(
            ("a", "stock number"),
            ("b", "imprint"),
            ("c", "acquisition terms (place / mode of acquisition)"),
            ("f", "other physical details"),
            ("g", "dimensions"),
            ("n", "copies held"),
        ),
        source=(
            "?m bffi:acquisitionSource [a bffi:AcquisitionSource ; "
            "bffi:identifiedBy [a bffi:Identifier ; "
            "bffi:source <http://id.loc.gov/vocabulary/identifiers/stock-number> ; "
            "rdf:value ?stock] ; "
            "rdfs:label ?imprint ; "
            "bffi:acquisitionTerms ?terms] "
            "— $f / $g / $n come from bffi:note [a bffi:Note ; rdfs:label ?text] "
            "on the same bnode; the first note is $3 (intervening-source / "
            "current-source text from MARC ind1), the rest are $f / $g / $n "
            "in source order."
        ),
        notes=(
            "The $3 subfield is only emitted when a note is present on the "
            "BFFI bnode — marc2bibframe2 only produces it when ind1 is 2 or "
            "3 in the source MARC. $f / $g / $n are emitted in source order "
            "when three or more notes are present; fewer notes produce fewer "
            "subfields."
        ),
    ),
)
@marc_emit(
    MarcEmitMeta(
        tag="353",
        indicators=(" ", " "),
        subfields=(
            ("a", "content of the work (e.g. 'index', 'biographical information')"),
            ("b", "level of analysis (absorbed into $0 when not a URI)"),
            ("0", "authority record control number or URI"),
            ("2", "source of heading or code"),
        ),
        source=(
            "?work bffi:supplementaryContent [a bffi:SupplementaryContent ; "
            "rdfs:label ?content ; "
            "bffi:identifiedBy [rdf:value ?authority] ; "
            "bffi:source [a bffi:Source ; bffi:code ?scheme]] "
            "— $0 is the identifier value (URI or bare string); $2 is the "
            "source scheme code."
        ),
        notes=(
            "marc2bibframe2's 353 template emits $b as a bare identifier "
            "when it's not a URI, so $a / $b / $0 collapse into a single "
            "identifier value. The reverse path emits $0 only when the "
            "identifier is present; $b is absorbed into $0."
        ),
    ),
)
def _extract_supplementary_content(
    graph: Graph, manifestation: URIRef
) -> list[_SupplementaryContentEmit]:
    """Walk ``?work bffi:supplementaryContent`` and emit one MARC 353
    datafield per bnode.

    BFFI shape (per marc2bibframe2's 353 template in
    ``ConvSpec-3XX.xsl``):

    - ``rdfs:label`` on the bnode (→\$a content).
    - ``bffi:identifiedBy`` → ``bffi:Identifier`` with ``rdf:value``
      carrying the $0 authority URI or $b value (→\$0).
    - ``bffi:source`` → ``bffi:Source`` with ``bffi:code`` (→\$2 scheme
      code).
    """
    work = _find_work_for_manifestation(graph, manifestation)
    owners = (manifestation, work) if work is not None else (manifestation,)
    emits: list[_SupplementaryContentEmit] = []
    for owner in owners:
        for sup in graph.objects(owner, BFFI.supplementaryContent):
            if isinstance(sup, Literal):
                continue
            # Content — ``rdfs:label`` on the bnode.
            content: str | None = None
            label = next(graph.objects(sup, RDFS.label), None)
            if isinstance(label, Literal):
                content = str(label)
            # Authority URI — ``bffi:identifiedBy`` → ``bffi:Identifier``
            # → ``rdf:value``. The value is either a URI (for $0) or a
            # bare string (for $b when not a URI).
            authority_uri: str | None = None
            for ident in graph.objects(sup, BFFI.identifier):
                value = next(graph.objects(ident, RDF.value), None)
                if isinstance(value, Literal):
                    authority_uri = str(value)
                    break
            # Source — ``bffi:source`` → ``bffi:Source`` → ``bffi:code``.
            source_code: str | None = None
            for source in graph.objects(sup, BFFI.source):
                if isinstance(source, URIRef):
                    code = next(graph.objects(source, BFFI.code), None)
                    if isinstance(code, Literal):
                        source_code = str(code)
                        break
            if content is None and authority_uri is None and source_code is None:
                continue
            emits.append(
                _SupplementaryContentEmit(
                    content=content,
                    level=None,  # $b is absorbed into $0 when not a URI
                    authority_uri=authority_uri,
                    source=source_code,
                )
            )
    return sorted(
        emits,
        key=lambda e: (e.content or "", e.authority_uri or "", e.source or ""),
    )


def _extract_acquisition_source(
    graph: Graph, manifestation: URIRef
) -> list[_AcquisitionSourceEmit]:
    """Walk ``?m bffi:acquisitionSource`` and emit one MARC 037 datafield
    per bnode.

    BFFI shape (per marc2bibframe2's 037 template in
    ``ConvSpec-010-048.xsl``):

    - ``bffi:identifiedBy`` → ``bffi:Identifier`` + ``bffi:source
      <…/identifiers/stock-number>`` + ``rdf:value`` (→\$a stock number).
    - ``rdfs:label`` on the bnode (→\$b imprint).
    - ``bffi:acquisitionTerms`` literal (→\$c).
    - ``bffi:note`` bnodes carrying ``rdfs:label`` for \$3 / \$f / \$g /
      \$n (the XSLT emits them as generic notes — only \$3 carries the
      "intervening source" / "current source" text from ind1; \$f / \$g /
      \$n are free-text notes).
    """
    emits: list[_AcquisitionSourceEmit] = []
    for src in graph.objects(manifestation, BFFI.acquisitionSource):
        if isinstance(src, Literal):
            continue
        stock = _read_stock_number(graph, src)
        imprint = _read_imprint(graph, src)
        acquisition_terms = _read_acquisition_terms(graph, src)
        note_3, other_physical, dimensions, copies = _read_notes(graph, src)
        if all(
            v is None
            for v in (stock, imprint, acquisition_terms, note_3, other_physical, dimensions, copies)
        ):
            continue
        emits.append(
            _AcquisitionSourceEmit(
                stock_number=stock,
                imprint=imprint,
                place=acquisition_terms,
                other_physical=other_physical,
                dimensions=dimensions,
                copies=copies,
            )
        )
    return sorted(
        emits,
        key=lambda e: (
            e.stock_number or "",
            e.imprint or "",
            e.place or "",
        ),
    )


def _read_stock_number(graph: Graph, src: Node) -> str | None:
    """Read stock number from ``bffi:identifiedBy`` with source
    ``…/identifiers/stock-number``."""
    for ident in graph.objects(src, BFFI.identifier):
        for source in graph.objects(ident, BFFI.source):
            if isinstance(source, URIRef) and str(source).endswith("/identifiers/stock-number"):
                value = next(graph.objects(ident, RDF.value), None)
                if isinstance(value, Literal):
                    return str(value)
    return None


def _read_imprint(graph: Graph, src: Node) -> str | None:
    """Read imprint from ``rdfs:label`` on the acquisition source bnode."""
    label = next(graph.objects(src, RDFS.label), None)
    return str(label) if isinstance(label, Literal) else None


def _read_acquisition_terms(graph: Graph, src: Node) -> str | None:
    """Read acquisition terms from ``bffi:acquisitionTerms`` literal."""
    at = next(graph.objects(src, BFFI.acquisitionTerms), None)
    return str(at) if isinstance(at, Literal) else None


def _read_notes(graph: Graph, src: Node) -> tuple[str | None, str | None, str | None, str | None]:
    """Read notes from ``bffi:note`` bnodes with ``rdfs:label``.

    Returns ``(note_3, other_physical, dimensions, copies)`` — the first
    note is $3 (if any), the next three are $f / $g / $n in source order.
    """
    notes: list[str] = []
    for note in graph.objects(src, BFFI.note):
        if not isinstance(note, URIRef | BNode):
            continue
        note_label = next(graph.objects(note, RDFS.label), None)
        if isinstance(note_label, Literal):
            notes.append(str(note_label))
    note_3 = notes[0] if notes else None
    other_notes = notes[1:]
    _THREE = 3
    _TWO = 2
    _ONE = 1
    if len(other_notes) >= _THREE:
        return note_3, other_notes[0], other_notes[1], other_notes[2]
    if len(other_notes) == _TWO:
        return note_3, other_notes[0], other_notes[1], None
    if len(other_notes) == _ONE:
        return note_3, other_notes[0], None, None
    return note_3, None, None, None


_VARTITLETYPE_PREFIX: Final[str] = "http://id.loc.gov/vocabulary/vartitletype/"

#: Mapping from ``vartitletype/<tail>`` to the MARC tag the title was
#: originally read from. 246-specific tails ({por, dis, cov, atp, cap,
#: run, spi}) discriminate the seven ind2-driven variants of MARC 246;
#: ``tra`` is MARC 242 (translation of title) and ``for`` is MARC 247
#: (former title).
_VARTITLETYPE_TAIL_TO_MARC: Final[dict[str, str]] = {
    # 246 variants — all seven map to MARC 246 in the reverse direction.
    "por": "246",
    "dis": "246",
    "cov": "246",
    "atp": "246",
    "cap": "246",
    "run": "246",
    "spi": "246",
    # Cross-tag variant-title sources.
    "tra": "242",
    "for": "247",
}

#: MARC tags whose forward XSLT collapses a BIBFRAME Title subclass
#: into the shared ``bffi:Title`` bnode. The reverse path recovers the
#: original tag from a ``bffi:marcKey`` literal whose first three
#: characters match. Same recovery pattern as the 800/810/811/830
#: traced-series and 5XX marcKey-driven notes; dormant until the
#: forward direction attaches the marcKey carrier.
_MARCKEY_VARIANT_TITLE_TAGS: Final[frozenset[str]] = frozenset(
    {"210", "222", "242", "243", "246", "247"}
)


def _is_variant_title(graph: Graph, title_block: Node) -> bool:
    """True when the title block has a ``vartitletype/*`` rdf:type OR
    a ``bffi:marcKey`` literal whose first three characters are one of
    the variant-title MARC tags. Either marker indicates the block came
    from a 21X / 22X / 24X / 247 source rather than the main 245."""
    return _variant_title_marc_tag(graph, title_block) is not None


def _variant_title_marc_tag(graph: Graph, title_block: Node) -> str | None:
    """Return the MARC tag this variant-title block emits as, or ``None``
    if the block is the main title (no variant discriminator).

    Dispatch order:

    1. ``rdf:type <…/vartitletype/<tail>>`` — direct mapping via
       :data:`_VARTITLETYPE_TAIL_TO_MARC`.
    2. ``bffi:marcKey "TAG ..."`` — first three characters are the
       source MARC tag. Only honoured when the tag is one of
       :data:`_MARCKEY_VARIANT_TITLE_TAGS`.
    """
    for type_uri in graph.objects(title_block, RDF.type):
        if not isinstance(type_uri, URIRef):
            continue
        type_str = str(type_uri)
        if not type_str.startswith(_VARTITLETYPE_PREFIX):
            continue
        tail = type_str[len(_VARTITLETYPE_PREFIX) :]
        tag = _VARTITLETYPE_TAIL_TO_MARC.get(tail)
        if tag is not None:
            return tag
    for marckey in graph.objects(title_block, BFFI.marcKey):
        if not isinstance(marckey, Literal):
            continue
        prefix = str(marckey)[:3]
        if prefix in _MARCKEY_VARIANT_TITLE_TAGS:
            return prefix
    return None


@marc_emit(
    MarcEmitMeta(
        tag="245",
        indicators=("0", "0"),
        subfields=(
            ("a", "main title"),
            ("b", "subtitle"),
            ("c", "statement of responsibility"),
            ("n", "number of part / section of a work"),
            ("p", "name of part / section of a work"),
        ),
        source=(
            "?m bffi:title / bffi:Title / bffi:mainTitle (mandatory) + "
            "bffi:subtitle / bffi:partNumber / bffi:partName (each optional); "
            "responsibility comes from ?m bffi:responsibilityStatement"
        ),
        notes=(
            "First non-variant bffi:title block wins. Variant-titled "
            "blocks (typed with vartitletype/*) are skipped here and "
            "feed the 246 emit instead."
        ),
    )
)
def _extract_main_title_parts(graph: Graph, manifestation: URIRef) -> _TitleParts | None:
    """Walk ``?m bffi:title / bffi:Title`` blocks and return the first
    non-variant block as the MARC 245 emit. Variant-typed blocks
    (``vartitletype/*``) are skipped here — they feed the 246 emit.

    Returns ``None`` when no non-variant block has a ``bffi:mainTitle``.
    """
    for title_block in graph.objects(manifestation, BFFI.title):
        if _is_variant_title(graph, title_block):
            continue
        main = next(graph.objects(title_block, BFFI.mainTitle), None)
        if not isinstance(main, Literal):
            continue
        subtitle = next(graph.objects(title_block, BFFI.subtitle), None)
        part_number = next(graph.objects(title_block, BFFI.partNumber), None)
        part_name = next(graph.objects(title_block, BFFI.partName), None)
        return _TitleParts(
            main=str(main),
            subtitle=str(subtitle) if isinstance(subtitle, Literal) else None,
            part_number=str(part_number) if isinstance(part_number, Literal) else None,
            part_name=str(part_name) if isinstance(part_name, Literal) else None,
        )
    return None


@dataclass(frozen=True)
class _VariantTitleEmit:
    """One variant-title datafield's worth of content.

    ``tag`` is the source MARC tag (210, 222, 242, 243, 246, 247);
    indicators default to the per-tag convention (210 ind1=' ', the
    rest ind1='1') and ``$a`` carries the title text.
    """

    tag: str
    text: str


_VARIANT_TITLE_INDICATORS: Final[dict[str, tuple[str, str]]] = {
    # MARC 210 (Abbreviated Title): ind1 = added-entry flag (0 = no
    # added entry, 1 = added entry). HELMET corpus records tend to add it.
    "210": ("1", " "),
    # MARC 222 (Key Title): both indicators blank.
    "222": (" ", " "),
    # MARC 242 (Translation of Title): ind1 = added-entry, ind2 =
    # nonfiling chars. Default to ind1=1, ind2=0.
    "242": ("1", "0"),
    # MARC 243 (Collective Uniform Title): ind1 = added-entry, ind2 =
    # nonfiling chars. Default to ind1=1, ind2=0.
    "243": ("1", "0"),
    # MARC 246 (Varying Form of Title): ind1=1 (Note, added entry) per
    # HELMET corpus convention; ind2 blank.
    "246": ("1", " "),
    # MARC 247 (Former Title): ind1=1 (added entry), ind2=0 (display
    # note) per MARC default.
    "247": ("1", "0"),
}


@marc_emit(
    MarcEmitMeta(
        tag="210",
        indicators=("1", " "),
        subfields=(("a", "abbreviated title"),),
        source=(
            "?m bffi:title ?t . ?t a bffi:Title ; bffi:marcKey ?key (begins "
            "with '210') ; bffi:mainTitle ?text — forward direction "
            "collapses bf:AbbreviatedTitle into bffi:Title; the source tag "
            "survives only on bffi:marcKey."
        ),
    ),
    MarcEmitMeta(
        tag="222",
        indicators=(" ", " "),
        subfields=(("a", "key title"),),
        source=(
            "?m bffi:title ?t . ?t a bffi:Title ; bffi:marcKey ?key (begins "
            "with '222') ; bffi:mainTitle ?text — bf:KeyTitle collapses "
            "to bffi:Title via the title-variant routing."
        ),
    ),
    MarcEmitMeta(
        tag="242",
        indicators=("1", "0"),
        subfields=(("a", "translation of title by cataloguing agency"),),
        source=(
            "?m bffi:title ?t . either (?t rdf:type <…/vartitletype/tra>) "
            "or (?t bffi:marcKey ?key beginning with '242') ; bffi:mainTitle ?text"
        ),
    ),
    MarcEmitMeta(
        tag="243",
        indicators=("1", "0"),
        subfields=(("a", "collective uniform title"),),
        source=(
            "?m bffi:title ?t . ?t a bffi:Title ; bffi:marcKey ?key (begins "
            "with '243') ; bffi:mainTitle ?text — bf:CollectiveTitle "
            "collapses to bffi:Title via the title-variant routing."
        ),
    ),
    MarcEmitMeta(
        tag="246",
        indicators=("1", " "),
        subfields=(("a", "variant title"),),
        source=(
            "?m bffi:title ?t . either (?t rdf:type <…/vartitletype/{por,dis,"
            "cov,atp,cap,run,spi}>) or (?t bffi:marcKey ?key beginning with "
            "'246') ; bffi:mainTitle ?text"
        ),
        notes=(
            "ind1=1 (Note, added entry) per HELMET corpus convention; ind2 blank. "
            "The specific vartitletype tail maps to different ind2 values "
            "in MARC source but the per-tail ind2 dispatch is deferred."
        ),
    ),
    MarcEmitMeta(
        tag="247",
        indicators=("1", "0"),
        subfields=(("a", "former title"),),
        source=(
            "?m bffi:title ?t . either (?t rdf:type <…/vartitletype/for>) "
            "or (?t bffi:marcKey ?key beginning with '247') ; bffi:mainTitle ?text"
        ),
    ),
)
def _extract_variant_titles(graph: Graph, manifestation: URIRef) -> list[_VariantTitleEmit]:
    """Walk every ``bffi:title`` block that carries a variant-title
    discriminator (vartitletype/* rdf:type or a bffi:marcKey prefix in
    the 210/222/242/243/246/247 set) and return one
    :class:`_VariantTitleEmit` per block, dispatched to the right MARC
    tag.

    Walks title blocks from both the Manifestation and its Work — 222
    (key title) lives on the Work in BIBFRAME, while 246 sits on the
    Instance / Manifestation.
    """
    emits: list[_VariantTitleEmit] = []
    work = _find_work_for_manifestation(graph, manifestation)
    owners: tuple[URIRef, ...] = (manifestation, work) if work is not None else (manifestation,)
    seen: set[tuple[str, str]] = set()
    for owner in owners:
        for title_block in graph.objects(owner, BFFI.title):
            tag = _variant_title_marc_tag(graph, title_block)
            if tag is None:
                continue
            main = next(graph.objects(title_block, BFFI.mainTitle), None)
            if not isinstance(main, Literal):
                continue
            key = (tag, str(main))
            if key in seen:
                continue
            seen.add(key)
            emits.append(_VariantTitleEmit(tag=tag, text=str(main)))
    return sorted(emits, key=lambda e: (e.tag, e.text))


@marc_emit(
    MarcEmitMeta(
        tag="130",
        indicators=("0", " "),
        subfields=(
            ("a", "uniform title — main entry"),
            ("d", "date of treaty signing (marcKey-driven)"),
            ("g", "miscellaneous information (marcKey-driven)"),
            ("l", "language of a work (marcKey-driven)"),
            ("n", "number of part / section (marcKey-driven)"),
            ("p", "name of part / section (marcKey-driven)"),
        ),
        source=(
            "?m bffi:expressionOf ?hub . ?hub URI fragment matches "
            "'#Hub130'; ?hub bffi:marcKey ?key (begins with '130'). "
            "Indicators and every subfield parsed verbatim — same "
            "marcKey-driven recovery as 730/740."
        ),
    ),
    MarcEmitMeta(
        tag="240",
        indicators=("1", "0"),
        subfields=(
            ("a", "uniform title (Manifestation-anchored variant)"),
            ("g", "miscellaneous information (marcKey-driven)"),
            ("l", "language of a work (marcKey-driven)"),
            ("n", "number of part / section (marcKey-driven)"),
            ("p", "name of part / section (marcKey-driven)"),
            ("s", "version (marcKey-driven)"),
        ),
        source=(
            "?m bffi:expressionOf ?hub (?hub URI fragment matches '#Hub240'). "
            "?hub bffi:contribution / bffi:agent / bffi:marcKey carries the "
            "source 240's subfields as $t/$l/$g/$p/$s/$n/$k extras alongside "
            "the 1XX contributor; the reconstruction parses those and remaps "
            "$t → 240 $a."
        ),
        notes=(
            "MARC 240 appears alongside a 1XX main entry (whereas 130 is "
            "the main entry itself). marc2bibframe2 flattens both into "
            "one Hub240 with the 240 subfields piggy-backing on the 1XX "
            "agent's marcKey. ind1=1 (traced) ind2=0 (0 nonfiling chars) "
            "per the dominant HELMET corpus convention; the actual source value "
            "is recoverable from the agent marcKey but not yet preserved."
        ),
    ),
)
def _extract_uniform_main_entry(graph: Graph, manifestation: URIRef) -> _AddedTitleEmit | None:
    """Walk ``?m bffi:expressionOf ?hub`` (and the Work's same predicate)
    looking for a Hub Expression that maps to MARC 130 or 240.

    For ``Hub130``: the Hub's own ``bffi:marcKey`` starts with ``"130"``
    and is parsed verbatim — same marcKey-driven recovery as 730/740.

    For ``Hub240``: the Hub has no 240-marcKey of its own; the 240's
    source subfields are flattened onto the Hub's contribution-agent
    marcKey as ``$t`` / ``$l`` / ``$g`` / ``$p`` / ``$s`` extras
    (marc2bibframe2 collapses the source 1XX + 240 into one contributor
    chain). The reconstruction parses the agent's marcKey and remaps
    those extras to MARC 240 subfields (``$t`` → ``$a``, others by
    code identity).
    """
    work = _find_work_for_manifestation(graph, manifestation)
    anchors: list[URIRef] = [manifestation]
    if work is not None:
        anchors.append(work)
    for anchor in anchors:
        for hub in graph.objects(anchor, BFFI.expressionOf):
            if not isinstance(hub, URIRef):
                continue
            emit = _hub_uniform_title_emit(graph, hub)
            if emit is not None:
                return emit
    return None


_HUB130_RE: Final[re.Pattern[str]] = re.compile(r"#Hub130\b")
_HUB240_RE: Final[re.Pattern[str]] = re.compile(r"#Hub240\b")


def _hub_uniform_title_emit(graph: Graph, hub: URIRef) -> _AddedTitleEmit | None:
    """Build a 130 or 240 emit from a Hub URI, choosing the path based
    on the URI fragment."""
    hub_uri = str(hub)
    if _HUB130_RE.search(hub_uri):
        return _hub130_emit(graph, hub)
    if _HUB240_RE.search(hub_uri):
        return _hub240_emit(graph, hub)
    return None


def _hub130_emit(graph: Graph, hub: URIRef) -> _AddedTitleEmit | None:
    marc_key = next(graph.objects(hub, BFFI.marcKey), None)
    if not isinstance(marc_key, Literal):
        return None
    parsed = _parse_marc_key(str(marc_key))
    if parsed is None:
        return None
    tag, ind1, ind2, subfields = parsed
    if tag != "130" or not subfields:
        return None
    return _AddedTitleEmit(tag=tag, ind1=ind1, ind2=ind2, subfields=subfields)


_AGENT_TO_240_SUBFIELD_CODE: Final[dict[str, str]] = {
    "t": "a",  # source 240 $a is on the agent's $t
    "l": "l",
    "g": "g",
    "p": "p",
    "s": "s",
    "n": "n",
    "k": "k",
}


def _hub240_emit(graph: Graph, hub: URIRef) -> _AddedTitleEmit | None:
    """Construct a MARC 240 emit by parsing the Hub's contribution-agent
    marcKey for the 240-relevant subfield extras."""
    for contrib in graph.objects(hub, BFFI.contribution):
        for agent in graph.objects(contrib, BFFI.agent):
            marc_key = next(graph.objects(agent, BFFI.marcKey), None)
            if not isinstance(marc_key, Literal):
                continue
            parsed = _parse_marc_key(str(marc_key))
            if parsed is None:
                continue
            _agent_tag, _ind1, _ind2, agent_subfields = parsed
            mapped: list[tuple[str, str]] = []
            for code, value in agent_subfields:
                target = _AGENT_TO_240_SUBFIELD_CODE.get(code)
                if target is not None:
                    mapped.append((target, value))
            if mapped:
                # ind1=1 = traced (HELMET corpus near-universal convention);
                # ind2=0 = 0 nonfiling characters (default).
                return _AddedTitleEmit(tag="240", ind1="1", ind2="0", subfields=tuple(mapped))
    return None


def _extract_responsibility_statement(graph: Graph, manifestation: URIRef) -> str | None:
    """Return the first ``bffi:responsibilityStatement`` literal on the
    Manifestation, or ``None`` if absent. Maps directly to MARC 245 $c."""
    value = next(graph.objects(manifestation, BFFI.responsibilityStatement), None)
    return str(value) if isinstance(value, Literal) else None


#: Identifier-scheme URI → MARC datafield tag. Each ``bffi:identifiedBy``
#: block on a Manifestation carries a ``bffi:source`` URI naming the
#: LoC identifier scheme; this dispatch table converts those URIs into
#: the right MARC tag for the round-trip emit.
@dataclass(frozen=True)
class _IdentifierScheme:
    """MARC datafield shape for one ``bffi:source`` identifier scheme.

    The pair ``(ind1, ind2)`` is fixed per scheme — e.g. EAN is always
    MARC 024 ind1=3 — even though the source-MARC tag (020 / 022 / 024 /
    028) groups several distinct schemes under one numeric tag with the
    indicator picking the kind.
    """

    tag: str
    ind1: str
    ind2: str


_IDENTIFIER_SCHEME_TO_MARC: Final[dict[URIRef, _IdentifierScheme]] = {
    URIRef("http://id.loc.gov/vocabulary/identifiers/isbn"): _IdentifierScheme("020", " ", " "),
    URIRef("http://id.loc.gov/vocabulary/identifiers/issn"): _IdentifierScheme("022", " ", " "),
    URIRef("http://id.loc.gov/vocabulary/identifiers/issn-l"): _IdentifierScheme("023", " ", " "),
    URIRef("http://id.loc.gov/vocabulary/identifiers/fingerprint"): _IdentifierScheme(
        "026", " ", " "
    ),
    # MARC 024 — Other Standard Identifier. ind1 picks the scheme:
    # 1 = UPC, 2 = ISMN, 3 = EAN.
    URIRef("http://id.loc.gov/vocabulary/identifiers/upc"): _IdentifierScheme("024", "1", " "),
    URIRef("http://id.loc.gov/vocabulary/identifiers/ismn"): _IdentifierScheme("024", "2", " "),
    URIRef("http://id.loc.gov/vocabulary/identifiers/ean"): _IdentifierScheme("024", "3", " "),
    # MARC 028 — Publisher / Distributor Number. ind1 picks the kind:
    # 0 = Issue number (audio); 1 = Matrix; 2 = Plate; 3 = Other music;
    # 4 = Videorecording; 5 = Publisher; 6 = Distributor.
    URIRef("http://id.loc.gov/vocabulary/identifiers/audio-issue-number"): _IdentifierScheme(
        "028", "0", "1"
    ),
    # 0XX identifier family — each is a BIBFRAME subclass of bf:Identifier
    # whose forward routing produces ``bffi:Identifier + bffi:source <…/identifiers/<token>>``.
    # See ``docs/bf_to_bffi_mapping.md`` for the per-class routing decisions.
    URIRef("http://id.loc.gov/vocabulary/identifiers/lccn"): _IdentifierScheme("010", " ", " "),
    URIRef("http://id.loc.gov/vocabulary/identifiers/nbn"): _IdentifierScheme("015", " ", " "),
    URIRef("http://id.loc.gov/vocabulary/identifiers/copyright-number"): _IdentifierScheme(
        "017", " ", " "
    ),
    URIRef("http://id.loc.gov/vocabulary/identifiers/lc-overseas-acq"): _IdentifierScheme(
        "025", " ", " "
    ),
    URIRef("http://id.loc.gov/vocabulary/identifiers/strn"): _IdentifierScheme("027", " ", " "),
    URIRef("http://id.loc.gov/vocabulary/identifiers/coden"): _IdentifierScheme("030", " ", " "),
    URIRef("http://id.loc.gov/vocabulary/identifiers/postal-registration"): _IdentifierScheme(
        "032", " ", " "
    ),
    # 035 OCoLC variant — non-OCoLC 035 + 016 share the bare bffi:Local
    # routing (source URI ``…/identifiers/local``) and aren't
    # distinguishable from source alone; that fallback is marcKey-driven
    # via :data:`_MARCKEY_IDENTIFIER_DISPATCH_TAGS`.
    URIRef("http://id.loc.gov/vocabulary/identifiers/oclc-number"): _IdentifierScheme(
        "035", " ", " "
    ),
    URIRef("http://id.loc.gov/vocabulary/identifiers/report-number"): _IdentifierScheme(
        "088", " ", " "
    ),
    URIRef("http://id.loc.gov/vocabulary/identifiers/opus-number"): _IdentifierScheme(
        "383", " ", " "
    ),
    URIRef("http://id.loc.gov/vocabulary/identifiers/serial-number"): _IdentifierScheme(
        "383", " ", " "
    ),
}


#: 0XX identifier tags whose forward routing collapses several MARC
#: tags onto the same ``bffi:source`` URI (or none). The reverse path
#: recovers the original tag from a ``bffi:marcKey`` literal whose first
#: three characters match — same recovery as the 5XX marcKey-driven
#: notes. Dormant until the forward direction attaches a marcKey to the
#: bf:Identifier bnodes.
#:
#: - 016 (national bibliographic agency control number) shares
#:   ``…/identifiers/local`` with non-OCoLC 035.
#: - 035 non-OCoLC variant shares the same source URI as 016.
#: - 074 (GPO item number) emits bare ``bf:Identifier`` (no source URI).
#: - 023 (batch group number) and 026 (fingerprint) share
#:   ``…/identifiers/local`` with 016 / non-OCoLC 035 — marcKey dispatch.
#: - 383 (opus number / serial number) emits bare ``bf:Identifier``
#:   with no source URI.
_MARCKEY_IDENTIFIER_DISPATCH_TAGS: Final[frozenset[str]] = frozenset(
    {"016", "023", "026", "035", "074", "383"}
)

#: BFFI ``bffi:source`` URI → MARC datafield tag for identifiers
#: dispatched by marcKey prefix rather than by clean ``bffi:source``
#: URI. The dispatch value is the tag — the indicators come from
#: ``_IDENTIFIER_SCHEME_TO_MARC`` where they are known, else blank.
_MARCKEY_IDENTIFIER_SCHEME: Final[dict[str, _IdentifierScheme]] = {
    "023": _IdentifierScheme("023", " ", " "),
    "026": _IdentifierScheme("026", " ", " "),
    "383": _IdentifierScheme("383", " ", " "),
}


#: MARC organization codes, keyed by the local name of the LoC
#: ``organizations/`` URI marc2bibframe2 emits for MARC 035's ``(agency)``
#: prefix. The URI drops the hyphen (``FI-BTJ`` → ``fibtj``) and a generic
#: rule can't put it back: ``dlc`` must stay ``DLC``, so inserting a hyphen
#: after a two-letter prefix would wrongly yield ``DL-C``. Unknown codes fall
#: back to a bare uppercase, which round-trips the number but not the
#: hyphenation — visible as ``changed``, never silently wrong.
_ORGANIZATION_MARC_CODES: Final[dict[str, str]] = {
    "dlc": "DLC",
    "fibtj": "FI-BTJ",
}


def _system_control_number(graph: Graph, ident: URIRef | BNode, value: str) -> str | None:
    """Compose MARC 035 ``$a`` (``(agency)number``) for a local identifier.

    Returns ``None`` unless the identifier is a ``bffi:Local`` carrying a
    ``bffi:assigner`` organization URI. That combination is what
    marc2bibframe2 produces for MARC 035: the ``(FI-BTJ)`` prefix becomes the
    assigner and the bare number becomes ``rdf:value``. The record's own
    001-bound bib ID is also ``bffi:Local`` but carries no assigner, so it is
    not mistaken for a system control number.

    **Ambiguity, on the record:** MARC 016 and 074 produce a similar shape.
    They are dispatched first via their ``bffi:marcKey`` prefix; a corpus with
    016s that carry no marcKey would see them emitted here as 035 instead.
    """
    if (ident, RDF.type, BFFI.Local) not in graph:
        return None
    assigner = next(graph.objects(ident, BFFI.assigner), None)
    if not isinstance(assigner, URIRef) or "/organizations/" not in str(assigner):
        return None
    code = local_name(assigner)
    return f"({_ORGANIZATION_MARC_CODES.get(code, code.upper())}){value}"


@dataclass(frozen=True)
class _IdentifierEmit:
    """One MARC identifier datafield's worth of content.

    ``assigner`` carries the issuing body's name (the ``rdfs:label`` of
    a ``bffi:Organization`` referenced by ``bffi:assigner``) and emits
    as MARC ``$b`` on schemes where the source field carries it
    (notably 028). ``qualifier`` is the ``bffi:qualifier`` literal and
    emits as MARC ``$q`` — common on 020 ISBNs (e.g. ``$q (nid.)``).
    """

    tag: str
    ind1: str
    ind2: str
    value: str
    assigner: str | None
    qualifier: str | None


@dataclass(frozen=True)
class _PhysicalDescription:
    """MARC 300 components: extent (\\$a), other physical details (\\$b),
    dimensions (\\$c), and accompanying material (\\$e)."""

    extent: str | None
    other_physical: str | None
    dimensions: str | None
    accompanying_material: str | None


@marc_emit(
    MarcEmitMeta(
        tag="300",
        indicators=(" ", " "),
        subfields=(
            ("a", "extent"),
            ("b", "other physical details (illustrations, colour, etc.)"),
            ("c", "dimensions"),
            ("e", "accompanying material"),
        ),
        source=(
            "?m bffi:extent / bffi:Extent / rdfs:label (for $a); "
            "the Extent's bffi:note typed <…/mnotetype/physical> (for $b); "
            "?m bffi:dimensions literal (for $c); "
            "?m bffi:note typed <…/mnotetype/accmat> (for $e)"
        ),
        notes=(
            "First-extent-wins for multi-extent records (rare). ISBD "
            'trailing punctuation (" :" before $b, " ;" before $c, '
            '" +" before $e) is added at emit time.'
        ),
    )
)
def _extract_physical_description(
    graph: Graph, manifestation: URIRef
) -> _PhysicalDescription | None:
    """Walk the Manifestation's physical-description signals.

    ``$a`` from ``bffi:extent / bffi:Extent / rdfs:label``; ``$b`` from
    that Extent's inner ``bffi:note`` typed
    ``<…/mnotetype/physical>``; ``$c`` from ``bffi:dimensions``; ``$e``
    from the Manifestation's ``bffi:note`` typed
    ``<…/mnotetype/accmat>``.

    Returns ``None`` when no signals are present. First extent /
    physical / accmat / dimensions wins; multi-extent records are a
    follow-on (rare in the corpus).
    """
    extent_label: str | None = None
    other_physical: str | None = None
    for extent_block in graph.objects(manifestation, BFFI.extent):
        label = next(graph.objects(extent_block, RDFS.label), None)
        if isinstance(label, Literal):
            extent_label = str(label)
        other_physical = _note_text_with_type(graph, extent_block, _MNOTETYPE_PHYSICAL)
        if extent_label is not None or other_physical is not None:
            break
    dim_value = next(graph.objects(manifestation, BFFI.dimensions), None)
    dimensions = str(dim_value) if isinstance(dim_value, Literal) else None
    accompanying = _note_text_with_type(graph, manifestation, _MNOTETYPE_ACCMAT)
    if (
        extent_label is None
        and other_physical is None
        and dimensions is None
        and accompanying is None
    ):
        return None
    return _PhysicalDescription(
        extent=extent_label,
        other_physical=other_physical,
        dimensions=dimensions,
        accompanying_material=accompanying,
    )


def _note_text_with_type(graph: Graph, subject: Node, note_type: URIRef) -> str | None:
    """Walk ``?subject bffi:note ?n`` and return the ``rdfs:label`` of
    the first note co-typed ``?note_type``. ``None`` when no matching
    note exists."""
    for note in graph.objects(subject, BFFI.note):
        if (note, RDF.type, note_type) not in graph:
            continue
        label = next(graph.objects(note, RDFS.label), None)
        if isinstance(label, Literal):
            return str(label)
    return None


@marc_emit(
    MarcEmitMeta(
        tag="041",
        indicators=(" ", " "),
        subfields=(
            ("a", "3-letter language code (one per language)"),
            ("h", "language of the original"),
            ("i", "language of intertitles"),
            ("j", "language of subtitles"),
            ("k", "language of intermediate translations"),
            ("m", "language of accompanying material"),
            ("n", "language of the original libretto"),
            ("p", "language of captions"),
            ("q", "language of accessible audio"),
            ("r", "language of accessible visual language"),
        ),
        source=(
            "\\$a: ?m or ?work or ?expression bffi:language "
            "<http://id.loc.gov/vocabulary/languages/{code}> — the last URI "
            "segment is the MARC code. \\$h and the other component codes: "
            "?work bffi:note [a <http://id.loc.gov/vocabulary/resourceComponents/"
            "{component}> ; bffi:language <…/languages/{code}>], where the "
            "component URI selects the subfield (otx → \\$h, sub → \\$j, …)"
        ),
        notes=(
            "**The sub-language codes do survive the forward hop** — an earlier "
            "note here claimed they collapse into flat bffi:language URIs, and "
            "that is wrong. ``ConvSpec-010-048.xsl``'s ``parse041`` wraps every "
            "subfield in the ``hijkmnpqr`` set in a ``bf:Note`` typed with a "
            "``resourceComponents`` URI carrying the language inside, so the "
            "sub-code is recoverable: \\$h (otx) is emitted from "
            "``bffi:note [a <…/resourceComponents/otx> ; bffi:language ?lang]``, "
            "and likewise \\$i \\$j \\$k \\$m \\$n \\$p \\$q \\$r. "
            "26 source \\$h and 10 \\$j occurrences in the fixture corpus; "
            "9 records' 041s became byte-identical when this landed.\n\n"
            "**Not recovered:** the ``bdefgt`` set (\\$b summary, \\$d sung or "
            "spoken text, \\$e librettos, \\$f table of contents, \\$g "
            "accompanying material, \\$t transcripts), which the XSLT emits as "
            "``bf:accompaniedBy`` → ``bf:Work`` instead — a different shape this "
            "path doesn't walk (one \\$d in the corpus). A source \\$3 "
            "(materials specified) makes the XSLT drop the language outright, so "
            "those are unrecoverable at any stage.\n\n"
            "ind1=1 is asserted when \\$h is present (all 26 corpus \\$h "
            "carriers use ind1=1); the XSLT comments out its own ind1 handling, "
            "so ind1 is otherwise absent from BIBFRAME and stays blank rather "
            "than claiming '0' (not a translation) without evidence.\n\n"
            "\\$a order is not preserved — the codes come from an unordered "
            "RDF set and are emitted sorted, so a source whose first \\$a marks "
            "the predominant language loses that distinction. ``mul`` and "
            "``zxx`` are dropped from \\$a when other codes are present: they "
            "are 008/35-37 summary codes that leaked in as an extra \\$a, and "
            "in the corpus they appear in a source 041 only alone.\n\n"
            "Emitted whenever the graph carries a language statement, so a "
            "record whose language came only from 008 (no source 041) gains "
            "one — visible as `added` in the round-trip diff. Suppressing "
            "single-language 041s would remove ~31 such additions on the "
            "reference corpus but lose the 95 source 041s that are "
            "legitimately a single \\$a matching 008/35-37."
        ),
    )
)
def _extract_language_codes(graph: Graph, manifestation: URIRef) -> list[str]:
    """Walk every ``?m bffi:language`` object — typically a LoC language
    vocabulary URI like ``<http://id.loc.gov/vocabulary/languages/eng>``.
    Returns the 3-letter MARC language codes (the URI's local name).

    Deduped, deterministic ordering (sorted). Maps to MARC 041 \\$a (one per
    language).

    Walks the Manifestation, the Work and any Expression. marc2bibframe2 puts
    ``bf:language`` on the **Work** for 041 (this rule's own note has said so
    all along) and language is an Expression attribute in the FRBR sense, so
    a Manifestation-only walk emitted nothing for a record whose only
    language statement came from 041.
    """
    work = _find_work_for_manifestation(graph, manifestation)
    owners: list[URIRef | BNode] = [manifestation]
    if work is not None:
        owners.append(work)
    owners.extend(_expressions_for(graph, manifestation, work))
    codes = {
        local_name(obj)
        for owner in owners
        for obj in graph.objects(owner, BFFI.language)
        if isinstance(obj, URIRef)
    }
    # ``mul`` (multiple languages) and ``zxx`` (no linguistic content) are
    # 008/35-37 summary codes. marc2bibframe2 emits them as ``bf:language``
    # like any other, so a record whose 041 lists its languages individually
    # picked up a spurious extra ``$a`` from its own 008 — ``$azxx`` beside
    # ``$aita $ager``, ``$amul`` beside eight real codes. In the fixture
    # corpus these two codes appear in a source 041 only on their own (3
    # records, all ``zxx`` alone), never alongside real languages, so they
    # are dropped when anything else is present and kept when they aren't.
    if len(codes) > 1:
        codes -= _LANGUAGE_SUMMARY_CODES
    return sorted(codes)


#: MARC language codes that summarise a record rather than name a language:
#: ``mul`` = multiple languages, ``zxx`` = no linguistic content. Both come
#: from 008/35-37 and only make sense as a record's sole language claim.
_LANGUAGE_SUMMARY_CODES: Final[frozenset[str]] = frozenset({"mul", "zxx"})

#: MARC 041 language-component subfields, keyed by the LoC
#: ``resourceComponents`` URI marc2bibframe2 types the note with.
#:
#: The forward XSLT does **not** flatten these into plain ``bf:language``:
#: ``ConvSpec-010-048.xsl``'s ``parse041`` wraps every subfield in the
#: ``hijkmnpqr`` set in a ``bf:Note`` typed with the component URI, carrying
#: the language inside. So the sub-code survives the forward hop and the
#: reverse direction can put it back — 26 ``$h`` occurrences in the fixture
#: corpus, the largest single 041 loss.
#:
#: The ``bdefgt`` set (``$b`` summary, ``$d`` sung text, ``$e`` librettos,
#: ``$f`` table of contents, ``$g`` accompanying material, ``$t``
#: transcripts) takes a different shape — ``bf:accompaniedBy`` → ``bf:Work``
#: — and is not recovered here. One ``$d`` in the corpus.
#:
#: ``$a`` is unaffected: it stays a bare ``bffi:language`` on the Work and
#: feeds the ``$a`` codes.
_RESOURCE_COMPONENT_TO_041_SUBFIELD: Final[dict[URIRef, str]] = {
    URIRef("http://id.loc.gov/vocabulary/resourceComponents/otx"): "h",
    URIRef("http://id.loc.gov/vocabulary/resourceComponents/int"): "i",
    URIRef("http://id.loc.gov/vocabulary/resourceComponents/sub"): "j",
    URIRef("http://id.loc.gov/vocabulary/resourceComponents/itr"): "k",
    URIRef("http://id.loc.gov/vocabulary/resourceComponents/amt"): "m",
    URIRef("http://id.loc.gov/vocabulary/resourceComponents/olb"): "n",
    URIRef("http://id.loc.gov/vocabulary/resourceComponents/cap"): "p",
    URIRef("http://id.loc.gov/vocabulary/resourceComponents/aud"): "q",
    URIRef("http://id.loc.gov/vocabulary/resourceComponents/vis"): "r",
}


def _extract_language_components(graph: Graph, manifestation: URIRef) -> list[tuple[str, str]]:
    """Recover MARC 041's language-component subfields.

    Returns ``(subfield_code, language_code)`` pairs — e.g. ``("h", "rus")``
    for a Finnish translation of a Russian original — sorted so the emit is
    deterministic.

    Walks the same owners as the ``$a`` codes: marc2bibframe2 attaches the
    041 notes to the **Work**, so a Manifestation-only walk finds nothing.

    A source ``$3`` (materials specified) suppresses the language in the
    XSLT entirely, so those are not recoverable at all — one occurrence in
    the fixture corpus.
    """
    work = _find_work_for_manifestation(graph, manifestation)
    owners: list[URIRef | BNode] = [manifestation]
    if work is not None:
        owners.append(work)
    owners.extend(_expressions_for(graph, manifestation, work))

    pairs: set[tuple[str, str]] = set()
    seen: set[URIRef | BNode] = set()
    for owner in owners:
        for note in graph.objects(owner, BFFI.note):
            if not isinstance(note, URIRef | BNode) or note in seen:
                continue
            seen.add(note)
            code = next(
                (
                    subfield
                    for component, subfield in _RESOURCE_COMPONENT_TO_041_SUBFIELD.items()
                    if (note, RDF.type, component) in graph
                ),
                None,
            )
            if code is None:
                continue
            for language in graph.objects(note, BFFI.language):
                if isinstance(language, URIRef):
                    pairs.add((code, local_name(language)))
    return sorted(pairs)


@marc_emit(
    MarcEmitMeta(
        tag="045",
        indicators=(" ", " "),
        subfields=(
            ("a", "time period code"),
            ("b", "additional time period information"),
        ),
        source=(
            "?w bffi:temporalCoverage ?literal (plain EDTF string) — no "
            "discriminator needed, ``bffi:temporalCoverage`` is unique to MARC 045."
        ),
        notes=(
            "Plain literal emit. The XSLT produces ``bf:temporalCoverage`` as "
            "an EDTF-typed literal for both ``$a`` and ``$b``. No other MARC "
            "tag maps to this predicate, so every literal is a 045 subfield. "
            "``$a`` is the primary time period; ``$b`` is additional information "
            "(when ind1=2, two ``$b`` subfields may appear as two separate "
            "literals)."
        ),
    ),
)
def _extract_temporal_coverage(graph: Graph, manifestation: URIRef) -> list[str]:
    """Walk ``bffi:temporalCoverage`` literals and emit MARC 045 ``$a``/``$b``."""
    work = _find_work_for_manifestation(graph, manifestation)
    anchors = [manifestation]
    if work is not None:
        anchors.append(work)
    emits: list[str] = []
    for anchor in anchors:
        for literal in graph.objects(anchor, BFFI.temporalCoverage):
            if isinstance(literal, Literal):
                emits.append(str(literal))
    return sorted(set(emits))


@marc_emit(
    MarcEmitMeta(
        tag="046",
        indicators=(" ", " "),
        subfields=(
            ("k", "origin date"),
            ("l", "valid date"),
            ("m", "death date"),
            ("n", "birth date"),
        ),
        source=(
            "?w bffi:originDate ?k / bffi:validDate ?l (EDTF literals). "
            "Each literal becomes one MARC 046 subfield; ``$m`` and ``$n`` "
            "are not produced by the XSLT (no ``bf:deathDate``/``bf:birthDate`` "
            "templates)."
        ),
        notes=(
            "Two BFFI predicates from one MARC field: ``bffi:originDate`` → ``$k``, "
            "``bffi:validDate`` → ``$l``. Each is a plain EDTF literal. The XSLT "
            "does not emit ``bf:deathDate`` or ``bf:birthDate``, so ``$m`` and ``$n`` "
            "are never produced. No discriminator needed — the predicates are unique "
            "to MARC 046."
        ),
    ),
)
def _extract_special_coded_dates(graph: Graph, manifestation: URIRef) -> list[tuple[str, str]]:
    """Walk ``bffi:originDate`` and ``bffi:validDate`` literals and emit MARC 046.

    Returns ``[(subfield_code, value), ...]`` — each literal becomes one
    subfield: ``originDate`` → ``$k``, ``validDate`` → ``$l``.
    """
    work = _find_work_for_manifestation(graph, manifestation)
    anchors = [manifestation]
    if work is not None:
        anchors.append(work)
    emits: list[tuple[str, str]] = []
    for anchor in anchors:
        for literal in graph.objects(anchor, BFFI.originDate):
            if isinstance(literal, Literal):
                emits.append(("k", str(literal)))
        for literal in graph.objects(anchor, BFFI.validDate):
            if isinstance(literal, Literal):
                emits.append(("l", str(literal)))
    return sorted(set(emits))


@marc_emit(
    MarcEmitMeta(
        tag="384",
        indicators=(" ", " "),
        subfields=(("a", "key"), ("3", "materials specified")),
        source=("?w bffi:musicKey ?literal (plain literal) — single literal value."),
        notes=(
            "Plain literal emit. The XSLT produces ``bf:keyMode`` (a bnode with "
            "``rdfs:label``), the BFFI routing collapses it to ``bffi:musicKey`` "
            "literal. The literal value becomes MARC 384 ``$a``."
        ),
    ),
)
def _extract_music_key(graph: Graph, manifestation: URIRef) -> list[str]:
    """Walk ``bffi:musicKey`` literals and emit MARC 384 ``$a``."""
    work = _find_work_for_manifestation(graph, manifestation)
    anchors = [manifestation]
    if work is not None:
        anchors.append(work)
    emits: list[str] = []
    for anchor in anchors:
        for literal in graph.objects(anchor, BFFI.musicKey):
            if isinstance(literal, Literal):
                emits.append(str(literal))
    return sorted(set(emits))


@marc_emit(
    MarcEmitMeta(
        tag="042",
        indicators=(" ", " "),
        subfields=(("a", "authentication code"),),
        source=(
            "?m bffi:DescriptionAuthentication <http://id.loc.gov/vocabulary/marcauthen/{code}> "
            "— URI reference with marcauthen vocabulary. The URI local name is the MARC code."
        ),
        notes=(
            "Plain URI reference emit. The XSLT produces ``bf:authentication`` "
            "with a marcauthen vocabulary URI; the BFFI routing renames it to "
            "``bffi:DescriptionAuthentication``. The URI's local name is the MARC "
            "authentication code (e.g. ``fdo`` for 'full down to original', "
            "``aacr`` for 'AACR')."
        ),
    ),
)
def _extract_description_authentication(graph: Graph, manifestation: URIRef) -> list[str]:
    """Walk ``bffi:DescriptionAuthentication`` URI references and emit MARC 042 ``$a``.

    Returns the local name of each URI reference (the MARC authentication code).
    """
    MARCAUTHEN_PREFIX = "http://id.loc.gov/vocabulary/marcauthen/"
    emits: list[str] = []
    for auth in graph.objects(manifestation, BFFI.descriptionAuthentication):
        if not isinstance(auth, URIRef):
            continue
        uri_str = str(auth)
        if uri_str.startswith(MARCAUTHEN_PREFIX):
            emits.append(uri_str[len(MARCAUTHEN_PREFIX) :])
    return sorted(set(emits))


@marc_emit(
    MarcEmitMeta(
        tag="351",
        indicators=(" ", " "),
        subfields=(
            ("a", "classification/call number"),
            ("b", "unit ID"),
            ("c", "general group designation"),
            ("3", "materials specified"),
        ),
        source=(
            "?m bffi:collectionArrangement [a bffi:CollectionArrangement ; "
            "rdfs:label ?a] — unique predicate, no discriminator needed."
        ),
        notes=(
            "Bnode emit from ``bffi:CollectionArrangement``. ``$a`` is the "
            "``rdfs:label`` on the bnode (the general group designation); "
            "``$b`` is ``bffi:unitID`` literal when present; ``$c`` is ``bffi:classMark`` "
            "literal when present; ``$3`` is ``bffi:appliesTo`` literal when present. "
            "The XSLT emits 351 ``$a`` as the ``rdfs:label`` on ``bf:CollectionArrangement``."
        ),
    ),
)
def _extract_collection_arrangement(graph: Graph, manifestation: URIRef) -> list[tuple[str, str]]:
    """Walk ``bffi:collectionArrangement`` bnodes and emit MARC 351.

    Returns ``[(subfield_code, value), ...]`` for each subfield.
    """
    emits: list[tuple[str, str]] = []
    for arr in graph.objects(manifestation, BFFI.collectionArrangement):
        if not isinstance(arr, BNode):
            continue
        # Label → $a (general group designation)
        label = next(graph.objects(arr, RDFS.label), None)
        if isinstance(label, Literal):
            emits.append(("a", str(label)))
        # Unit ID → $b
        unit_id = next(graph.objects(arr, BFFI.unitID), None)
        if isinstance(unit_id, Literal):
            emits.append(("b", str(unit_id)))
        # Class mark → $c
        class_mark = next(graph.objects(arr, BFFI.classMark), None)
        if isinstance(class_mark, Literal):
            emits.append(("c", str(class_mark)))
        # Materials specified → $3
        applies_to = next(graph.objects(arr, BFFI.appliesTo), None)
        if isinstance(applies_to, Literal):
            emits.append(("3", str(applies_to)))
    return sorted(set(emits))


@marc_emit(
    MarcEmitMeta(
        tag="352",
        indicators=(" ", " "),
        subfields=(
            ("a", "original use of title (literal)"),
            ("b", "other title information (literal)"),
            ("q", "file format (literal)"),
            ("6", "occurrence identifier (literal)"),
        ),
        source=(
            "?m bffi:digitalCharacteristic [a bffi:CartographicDataType ; rdfs:label ?a] "
            "— unique predicate, no discriminator needed."
        ),
        notes=(
            "Plain literal emit from ``bffi:CartographicDataType`` bnode. The ``$a`` "
            "subfield becomes ``rdfs:label`` on the bnode; ``$b`` is not produced by "
            "the XSLT (no ``bf:title`` on the CartographicDataType). ``$q`` becomes "
            "``bffi:fileFormat`` literal when present. ``$6`` becomes ``bffi:occurrenceId`` "
            "when present."
        ),
    ),
)
def _extract_digital_characteristic(graph: Graph, manifestation: URIRef) -> list[tuple[str, str]]:
    """Walk ``bffi:digitalCharacteristic`` bnodes and emit MARC 352.

    Returns ``[(subfield_code, value), ...]`` for each subfield.
    """
    emits: list[tuple[str, str]] = []
    for char in graph.objects(manifestation, BFFI.digitalCharacteristic):
        if not isinstance(char, BNode):
            continue
        # Label → $a
        label = next(graph.objects(char, RDFS.label), None)
        if isinstance(label, Literal):
            emits.append(("a", str(label)))
        # File format → $q
        file_format = next(graph.objects(char, BFFI.fileFormat), None)
        if isinstance(file_format, Literal):
            emits.append(("q", str(file_format)))
        # Occurrence ID → $6
        occ_id = next(graph.objects(char, BFFI.occurrenceId), None)
        if isinstance(occ_id, Literal):
            emits.append(("6", str(occ_id)))
    return sorted(set(emits))


#: Matches ``#<Type><tag>-<n>`` in subject-node URI fragments emitted
#: by marc2bibframe2 (e.g. ``#Agent600-28`` / ``#Topic650-12`` /
#: ``#Place651-30`` / ``#Temporal648-29``). Capture group 1 is the
#: 3-digit MARC tag the source subject came from.
_SUBJECT_TAG_PATTERN: Final[re.Pattern[str]] = re.compile(r"#[A-Za-z]+(\d{3})-")

#: MARC 6XX subject tags this routing recognises. Other tag values
#: produced by marc2bibframe2 (e.g. 730 uniform titles via #Work730)
#: are not subjects and get dispatched separately.
_SUBJECT_MARC_TAGS: Final[frozenset[str]] = frozenset(
    {"600", "610", "611", "630", "647", "648", "650", "651", "653", "655", "662"}
)

#: Fallback mapping from BFFI subject-node class to MARC tag, used when
#: the subject is an external authority URI (e.g. ``yso/p12148``) and
#: there's no ``#<Type>NNN-N`` URI fragment to extract the tag from.
_SUBJECT_TYPE_TO_MARC_TAG: Final[dict[URIRef, str]] = {
    BFFI.Person: "600",
    BFFI.Organization: "610",
    BFFI.Jurisdiction: "610",
    BFFI.Meeting: "611",
    BFFI.Title: "630",
    BFFI.Event: "647",
    BFFI.Temporal: "648",
    BFFI.Topic: "650",
    BFFI.Place: "651",
    BFFI.GenreForm: "655",
}

#: ``bffi:Uncontrolled`` co-type → MARC 653 (Index Term — Uncontrolled).
#: Takes priority over the Topic/Person/etc. dispatch above because 653
#: spans every term-kind: source ``653 _ N $a "term"`` is uncontrolled
#: regardless of whether the term is topical, personal, geographic, etc.
_UNCONTROLLED_TYPE: Final[URIRef] = URIRef("http://urn.fi/URN:NBN:fi:schema:bffi:Uncontrolled")


@dataclass(frozen=True)
class _SubjectEmit:
    """One MARC 6XX subject datafield's worth of content.

    ``vocab_code`` carries the ``$2`` source-vocabulary code (e.g. ``"yso"``,
    ``"ysa"``, ``"slm"``); ``authority_uri`` carries the ``$0`` authority
    URI when the subject in BFFI is anchored on an external concept (e.g.
    ``http://www.yso.fi/onto/yso/p12148``). Either, both, or neither can be
    present — local bib-mint subjects with no ``bffi:source`` will emit
    just ``$a``.

    ``extra_subfields`` carries marcKey-driven extras the source MARC
    had beyond ``$a`` (e.g. ``$t`` analytical title on a name-title
    subject 600 1 4 \\$a Bond, James \\$t Casino Royale). Parsed
    verbatim from the subject node's ``bffi:marcKey`` when present.
    """

    tag: str
    label: str
    vocab_code: str | None
    authority_uri: str | None
    extra_subfields: tuple[tuple[str, str], ...]


def _find_work_for_manifestation(graph: Graph, manifestation: URIRef) -> URIRef | None:
    """Return the Work URI this Manifestation manifests, or ``None``.

    Walks ``manifestation bffi:workManifested → Work``. The Work URI
    is the anchor for subject / classification / contribution triples
    (which are properties of the abstract Work in BFFI's FRBR-axis
    split, not the Manifestation).
    """
    work = next(graph.objects(manifestation, BFFI.workManifested), None)
    return work if isinstance(work, URIRef) else None


#: BFFI source description prefix shared by every 6XX subject row.
#: Subjects live on the Work (FRBR-axis: subjects describe the
#: abstract Work, not a particular Manifestation), so every row's
#: source begins with the same walk.
_SUBJECT_SOURCE_PREFIX: Final[str] = (
    "?m bffi:workManifested ?work . ?work bffi:subject \\| bffi:genreForm ?subject . "
    "?subject rdfs:label ?label . "
    "$2 = local-name of ?subject's bffi:source URI when present; "
    "$0 = ?subject URI itself when it's not a bib-internal mint"
)

_SUBJECT_SUBFIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("a", "subject heading / term"),
    ("c", "qualifier (marcKey-driven)"),
    ("d", "dates (marcKey-driven)"),
    ("t", "title within name-title subject (marcKey-driven, e.g. 600 ind2=4)"),
    ("0", "authority URI for the subject heading"),
    ("2", "source vocabulary code (e.g. 'yso', 'ysa', 'slm')"),
)


@marc_emit(
    MarcEmitMeta(
        tag="600",
        indicators=(" ", " "),
        subfields=_SUBJECT_SUBFIELDS,
        source=f"{_SUBJECT_SOURCE_PREFIX} — `?subject` is typed `bffi:Person`",
    ),
    MarcEmitMeta(
        tag="610",
        indicators=(" ", " "),
        subfields=_SUBJECT_SUBFIELDS,
        source=f"{_SUBJECT_SOURCE_PREFIX} — `?subject` is typed `bffi:Organization`",
    ),
    MarcEmitMeta(
        tag="611",
        indicators=(" ", " "),
        subfields=_SUBJECT_SUBFIELDS,
        source=f"{_SUBJECT_SOURCE_PREFIX} — `?subject` is typed `bffi:Meeting`",
    ),
    MarcEmitMeta(
        tag="630",
        indicators=(" ", " "),
        subfields=_SUBJECT_SUBFIELDS,
        source=f"{_SUBJECT_SOURCE_PREFIX} — `?subject` is typed `bffi:Title`",
    ),
    MarcEmitMeta(
        tag="647",
        indicators=(" ", " "),
        subfields=_SUBJECT_SUBFIELDS,
        source=f"{_SUBJECT_SOURCE_PREFIX} — `?subject` is typed `bffi:Event`",
    ),
    MarcEmitMeta(
        tag="648",
        indicators=(" ", " "),
        subfields=_SUBJECT_SUBFIELDS,
        source=f"{_SUBJECT_SOURCE_PREFIX} — `?subject` is typed `bffi:Temporal`",
    ),
    MarcEmitMeta(
        tag="650",
        indicators=(" ", " "),
        subfields=_SUBJECT_SUBFIELDS,
        source=f"{_SUBJECT_SOURCE_PREFIX} — `?subject` is typed `bffi:Topic`",
    ),
    MarcEmitMeta(
        tag="651",
        indicators=(" ", " "),
        subfields=_SUBJECT_SUBFIELDS,
        source=f"{_SUBJECT_SOURCE_PREFIX} — `?subject` is typed `bffi:Place`",
    ),
    MarcEmitMeta(
        tag="653",
        indicators=(" ", " "),
        subfields=_SUBJECT_SUBFIELDS,
        source=(
            f"{_SUBJECT_SOURCE_PREFIX} — `?subject` is typed "
            "`bffi:Uncontrolled` (any term-kind, anonymous bnode in BFFI)"
        ),
        notes=(
            "653 is MARC's catch-all for uncontrolled subject terms. The "
            "bffi:Uncontrolled co-type takes priority over the structural "
            "Topic/Person/Place dispatch — source-MARC 653 spans every "
            "term-kind so the structural rdf:type isn't the right "
            "discriminator."
        ),
    ),
    MarcEmitMeta(
        tag="655",
        indicators=(" ", " "),
        subfields=_SUBJECT_SUBFIELDS,
        source=f"{_SUBJECT_SOURCE_PREFIX} — `?subject` is typed `bffi:GenreForm`",
    ),
    MarcEmitMeta(
        tag="662",
        indicators=(" ", " "),
        subfields=_SUBJECT_SUBFIELDS,
        source=(
            f"{_SUBJECT_SOURCE_PREFIX} — `?subject` is typed `bffi:Place` AND its "
            "URI fragment matches `#Place662-N` (the marc2bibframe2 mint pattern "
            "for hierarchical-place-name subjects from MARC 662, distinct from "
            "651's `#Place651-N`)."
        ),
        notes=(
            "662's hierarchical structure ($a country, $b first-order admin, "
            "$c subsequent-order admin, $d city, $f city-subsection, $g other) "
            "is collapsed into a single rdfs:label by marc2bibframe2; the reverse "
            "path emits just $a + the structural metadata ($0 / $2)."
        ),
    ),
)
def _extract_subject_datafields(graph: Graph, manifestation: URIRef) -> list[_SubjectEmit]:
    """Walk ``?work bffi:subject|bffi:genreForm ?subject_node`` and emit one
    MARC 6XX datafield per subject.

    Tag dispatch: when the subject URI is a bib-internal mint (e.g.
    ``#Agent600-28`` / ``#Topic650-12`` / ``#Place651-30``), the URI
    fragment carries the source MARC tag verbatim. When it's an external
    authority URI (e.g. ``http://www.yso.fi/onto/yso/p12148``), the tag
    is derived from the subject's ``rdf:type``: ``bffi:Topic`` → 650,
    ``bffi:Place`` → 651, etc.

    Subfield emit:
      * ``$a`` from ``rdfs:label`` (mandatory)
      * ``$2`` from the local name of ``bffi:source`` (the LoC scheme URI;
        e.g. ``vocabulary/subjectSchemes/yso`` → ``"yso"``)
      * ``$0`` from the subject URI itself when it's an external authority
        URI rather than a bib-internal mint

    Returns a list of emits — one per subject. Only recognised 6XX tags
    (:data:`_SUBJECT_MARC_TAGS`) are emitted; others are skipped (they
    get their own routing).
    """
    work = _find_work_for_manifestation(graph, manifestation)
    if work is None:
        return []
    emits: list[_SubjectEmit] = []
    seen: set[URIRef | BNode] = set()
    # Genre/form terms hang off the Work under ``bffi:genreForm``, not
    # ``bffi:subject`` — marc2bibframe2 renders MARC 655 as ``bf:GenreForm``
    # reached by ``bf:genreForm``, and the clean rename preserves that
    # shape. Walking only ``bffi:subject`` therefore lost every 655 in the
    # corpus even though the class → tag mapping was correct.
    for predicate in (BFFI.subject, BFFI.genreForm):
        for subj_node in graph.objects(work, predicate):
            if not isinstance(subj_node, URIRef | BNode) or subj_node in seen:
                continue
            seen.add(subj_node)
            emit = _build_subject_emit(graph, subj_node)
            if emit is not None:
                emits.append(emit)
    return sorted(emits, key=lambda e: (e.tag, e.label, e.vocab_code or "", e.authority_uri or ""))


_SUBJECT_STRUCTURED_CODES: Final[frozenset[str]] = frozenset({"a", "0", "2"})


def _build_subject_emit(graph: Graph, subj_node: URIRef | BNode) -> _SubjectEmit | None:
    """Return the ``_SubjectEmit`` for one ``bffi:subject`` URI, or
    ``None`` when the node can't be mapped to a 6XX tag or lacks both
    an ``rdfs:label`` and a parseable ``bffi:marcKey`` ``$a``."""
    tag = _subject_marc_tag(graph, subj_node)
    if tag is None:
        return None
    label = _subject_label(graph, subj_node)
    if label is None:
        return None
    vocab_code: str | None = None
    source = next(graph.objects(subj_node, BFFI.source), None)
    if isinstance(source, URIRef):
        vocab_code = local_name(source)
    # $0 is emitted only when the subject is an external authority URI:
    # BNodes never have one (their str() is an rdflib-internal id), and
    # bib-internal mint URIs (#Topic650-12 etc.) shouldn't leak the
    # mint URI as if it were an authority record number.
    if isinstance(subj_node, URIRef) and _SUBJECT_TAG_PATTERN.search(str(subj_node)) is None:
        authority_uri: str | None = str(subj_node)
    else:
        authority_uri = None
    extras = _subject_marckey_extras(graph, subj_node)
    return _SubjectEmit(
        tag=tag,
        label=label,
        vocab_code=vocab_code,
        authority_uri=authority_uri,
        extra_subfields=extras,
    )


def _subject_label(graph: Graph, subj_node: URIRef | BNode) -> str | None:
    """Return the heading text (``$a``) for a subject node. Prefers
    the subject's own ``rdfs:label``; falls back to the marcKey ``$a``
    when the node is a Hub-style wrapper (e.g. ``Hub600-N``) that
    carries the source row's marcKey verbatim but no direct label."""
    label = next(graph.objects(subj_node, RDFS.label), None)
    if isinstance(label, Literal):
        return str(label)
    marc_key = next(graph.objects(subj_node, BFFI.marcKey), None)
    if isinstance(marc_key, Literal):
        parsed = _parse_marc_key(str(marc_key))
        if parsed is not None:
            for code, value in parsed[3]:
                if code == "a":
                    return value
    return None


def _subject_marckey_extras(graph: Graph, subj_node: URIRef | BNode) -> tuple[tuple[str, str], ...]:
    """Parse the subject node's ``bffi:marcKey`` (when present) and
    return marcKey subfields beyond the structured-BFFI set
    (``$a`` / ``$0`` / ``$2``) — typically ``$t`` analytical title on
    name-title subjects (600 ind2=4), occasionally ``$c`` qualifier
    or ``$d`` dates."""
    marc_key = next(graph.objects(subj_node, BFFI.marcKey), None)
    if not isinstance(marc_key, Literal):
        return ()
    parsed = _parse_marc_key(str(marc_key))
    if parsed is None:
        return ()
    _tag, _ind1, _ind2, subfields = parsed
    return tuple(
        (code, value) for code, value in subfields if code not in _SUBJECT_STRUCTURED_CODES
    )


def _subject_marc_tag(graph: Graph, subj_node: Node) -> str | None:
    """Pick the MARC 6XX tag for a subject node (URI or BNode).

    ``bffi:Uncontrolled`` rdf:type takes priority over the structural
    Topic/Person/etc. dispatch — source-MARC 653 (Index Term —
    Uncontrolled) carries terms of any kind, so an Uncontrolled co-type
    forces the 653 destination regardless of structural typing.

    For typed URIs without the Uncontrolled marker, prefers the
    bib-internal URI fragment (``#Topic650-12``) — that's the
    source-MARC tag preserved verbatim by marc2bibframe2. Falls back
    to the subject's ``rdf:type`` (Topic→650, Place→651, etc.) when
    no fragment match exists (the external-authority-URI case AND
    for bnode subjects with structural typing only)."""
    if (subj_node, RDF.type, _UNCONTROLLED_TYPE) in graph:
        return "653"
    match = _SUBJECT_TAG_PATTERN.search(str(subj_node))
    if match is not None:
        tag = match.group(1)
        return tag if tag in _SUBJECT_MARC_TAGS else None
    for type_uri, tag in _SUBJECT_TYPE_TO_MARC_TAG.items():
        if (subj_node, RDF.type, type_uri) in graph:
            return tag
    return None


@marc_emit(
    MarcEmitMeta(
        tag="020",
        indicators=(" ", " "),
        subfields=(
            ("a", "ISBN value"),
            ("q", "qualifier (binding / format, e.g. 'nid.', 'pehmeäkantinen')"),
        ),
        source=(
            "?m bffi:identifiedBy [a bffi:Identifier ; "
            "bffi:source <http://id.loc.gov/vocabulary/identifiers/isbn> ; "
            "rdf:value ?isbn ; bffi:qualifier ?qualifier]"
        ),
    ),
    MarcEmitMeta(
        tag="022",
        indicators=(" ", " "),
        subfields=(("a", "ISSN value"),),
        source=(
            "?m bffi:identifiedBy [a bffi:Identifier ; "
            "bffi:source <http://id.loc.gov/vocabulary/identifiers/issn> ; "
            "rdf:value ?issn]"
        ),
    ),
    MarcEmitMeta(
        tag="024",
        indicators=("0-3", " "),
        subfields=(
            ("a", "EAN / UPC / ISMN value"),
            ("q", "qualifier (binding / format, e.g. 'pelipakkaus')"),
        ),
        source=(
            "?m bffi:identifiedBy [a bffi:Identifier ; "
            "bffi:source <…/identifiers/upc \\| ismn \\| ean> ; "
            "rdf:value ?value ; bffi:qualifier ?qualifier] — "
            "ind1 selects the scheme (1=UPC, 2=ISMN, 3=EAN)."
        ),
    ),
    MarcEmitMeta(
        tag="028",
        indicators=("0-6", "0-3"),
        subfields=(
            ("a", "publisher / distributor number value"),
            ("b", "issuing publisher / distributor name"),
            ("q", "qualifier (binding / format)"),
        ),
        source=(
            "?m bffi:identifiedBy [a bffi:Identifier ; "
            "bffi:source <…/identifiers/audio-issue-number> ; rdf:value ?value ; "
            "bffi:assigner [a bffi:Organization ; rdfs:label ?name] ; "
            "bffi:qualifier ?qualifier] — "
            "ind1=0 for audio issue numbers; ind2=1 = note maker / no added "
            "entry (the HELMET corpus default)."
        ),
    ),
    MarcEmitMeta(
        tag="010",
        indicators=(" ", " "),
        subfields=(("a", "LCCN value"),),
        source=(
            "?m bffi:identifiedBy [a bffi:Identifier ; "
            "bffi:source <http://id.loc.gov/vocabulary/identifiers/lccn> ; rdf:value ?value]"
        ),
    ),
    MarcEmitMeta(
        tag="015",
        indicators=(" ", " "),
        subfields=(("a", "national bibliography number"),),
        source=(
            "?m bffi:identifiedBy [a bffi:Identifier ; "
            "bffi:source <http://id.loc.gov/vocabulary/identifiers/nbn> ; rdf:value ?value]"
        ),
    ),
    MarcEmitMeta(
        tag="017",
        indicators=(" ", " "),
        subfields=(
            ("a", "copyright / legal-deposit number"),
            ("b", "assigning agency"),
        ),
        source=(
            "?m bffi:identifiedBy [a bffi:Identifier ; "
            "bffi:source <http://id.loc.gov/vocabulary/identifiers/copyright-number> ; "
            "rdf:value ?value ; bffi:assigner [a bffi:Organization ; rdfs:label ?name]]"
        ),
        notes=(
            "MARC 017 source carries a date in \\$d that marc2bibframe2 "
            "stores on the bf:Identifier as bf:date. The reverse path does "
            "not yet round-trip this — only \\$a and \\$b are emitted."
        ),
    ),
    MarcEmitMeta(
        tag="025",
        indicators=(" ", " "),
        subfields=(("a", "overseas acquisition number"),),
        source=(
            "?m bffi:identifiedBy [a bffi:Identifier ; "
            "bffi:source <http://id.loc.gov/vocabulary/identifiers/lc-overseas-acq> ; "
            "rdf:value ?value]"
        ),
    ),
    MarcEmitMeta(
        tag="023",
        indicators=(" ", " "),
        subfields=(("a", "batch group number (ISSN-L)"),),
        source=(
            "?m bffi:identifiedBy [a bffi:Identifier ; "
            "bffi:source <http://id.loc.gov/vocabulary/identifiers/issn-l> ; "
            "rdf:value ?value]"
        ),
        notes=(
            "MARC 023 carries the ISSN-L (International Standard Serial Number "
            "Leader) batch group number. marc2bibframe2 emits ``bf:IssnL`` when "
            "ind1=0; the forward routing collapses it to ``bffi:Identifier`` with "
            "``bffi:source`` ``…/identifiers/issn-l``. The reverse path reads "
            "this and emits MARC 023 \$a with the bare value."
        ),
    ),
    MarcEmitMeta(
        tag="026",
        indicators=(" ", " "),
        subfields=(("a", "fingerprint identifier"),),
        source=(
            "?m bffi:identifiedBy [a bffi:Identifier ; "
            "bffi:source <http://id.loc.gov/vocabulary/identifiers/fingerprint> ; "
            "rdf:value ?value]"
        ),
        notes=(
            "MARC 026 carries a fingerprint identifier (a unique character "
            "string derived from the record's control fields and datafields). "
            "marc2bibframe2 emits ``bf:Fingerprint`` → ``bffi:Identifier`` with "
            "``bffi:source`` ``…/identifiers/fingerprint``. The reverse path "
            "reads this and emits MARC 026 \$a with the bare value."
        ),
    ),
    MarcEmitMeta(
        tag="027",
        indicators=(" ", " "),
        subfields=(("a", "standard technical report number"),),
        source=(
            "?m bffi:identifiedBy [a bffi:Identifier ; "
            "bffi:source <http://id.loc.gov/vocabulary/identifiers/strn> ; rdf:value ?value]"
        ),
    ),
    MarcEmitMeta(
        tag="030",
        indicators=(" ", " "),
        subfields=(("a", "CODEN designation"),),
        source=(
            "?m bffi:identifiedBy [a bffi:Identifier ; "
            "bffi:source <http://id.loc.gov/vocabulary/identifiers/coden> ; rdf:value ?value]"
        ),
    ),
    MarcEmitMeta(
        tag="032",
        indicators=(" ", " "),
        subfields=(
            ("a", "postal registration number"),
            ("b", "registering agency"),
        ),
        source=(
            "?m bffi:identifiedBy [a bffi:Identifier ; "
            "bffi:source <http://id.loc.gov/vocabulary/identifiers/postal-registration> ; "
            "rdf:value ?value ; bffi:assigner [a bffi:Organization ; rdfs:label ?name]]"
        ),
    ),
    MarcEmitMeta(
        tag="035",
        indicators=(" ", " "),
        subfields=(("a", "OCLC / system control number"),),
        source=(
            "OCoLC variant: ?m bffi:identifiedBy [a bffi:Identifier ; "
            "bffi:source <http://id.loc.gov/vocabulary/identifiers/oclc-number> ; "
            "rdf:value ?value]. Other agencies: ?m bffi:identifiedBy "
            "[a bffi:Local ; bffi:assigner <…/organizations/{code}> ; "
            "rdf:value ?number] — \\$a is recomposed as (AGENCY)number."
        ),
        notes=(
            "Both variants round-trip. The non-OCoLC shape carries **no** "
            "bffi:source and no marcKey — an earlier note claimed it shared "
            "<…/identifiers/local> with MARC 016 and needed a marcKey to "
            "disambiguate, which the data does not bear out. The assigner "
            "organization URI is the discriminator; the record's own "
            "001-bound bib ID is also bffi:Local but carries no assigner. "
            "The agency code is restored from a small organization-code map "
            "because the LoC URI drops the hyphen (FI-BTJ → fibtj) and no "
            "generic rule can put it back — DLC must stay DLC. Unknown codes "
            "fall back to bare uppercase, which keeps the number and loses "
            "only the hyphenation. MARC 016 / 074 share this shape and are "
            "dispatched first by marcKey; 016s without a marcKey would be "
            "emitted here as 035."
        ),
    ),
    MarcEmitMeta(
        tag="088",
        indicators=(" ", " "),
        subfields=(("a", "report number"),),
        source=(
            "?m bffi:identifiedBy [a bffi:Identifier ; "
            "bffi:source <http://id.loc.gov/vocabulary/identifiers/report-number> ; "
            "rdf:value ?value]"
        ),
    ),
    MarcEmitMeta(
        tag="016",
        indicators=(" ", " "),
        subfields=(("a", "national bibliographic agency control number"),),
        source=(
            "?m bffi:identifiedBy [a bffi:Identifier ; bffi:marcKey ?key ; rdf:value ?value] "
            "where the first 3 chars of ?key are '016' — marcKey-driven recovery, "
            "needed because 016 and non-OCoLC 035 both forward to bffi:source "
            "<…/identifiers/local> and aren't distinguishable from source alone."
        ),
        notes=(
            "Dormant until the BIBFRAME → BFFI direction attaches "
            "bffi:marcKey to bf:Identifier bnodes. The clean-source path is "
            "not viable: 016 forwards to bf:Local, sharing the source URI "
            "with non-OCoLC 035."
        ),
    ),
    MarcEmitMeta(
        tag="074",
        indicators=(" ", " "),
        subfields=(("a", "GPO item number"),),
        source=(
            "?m bffi:identifiedBy [a bffi:Identifier ; bffi:marcKey ?key ; rdf:value ?value] "
            "where the first 3 chars of ?key are '074' — marcKey-driven recovery, "
            "needed because 074 emits a bare bf:Identifier (no source URI)."
        ),
        notes=(
            "Dormant until the BIBFRAME → BFFI direction attaches "
            "bffi:marcKey to bare bf:Identifier bnodes. marc2bibframe2 does "
            "not assign 074 a specific subclass."
        ),
    ),
)
def _extract_identifier_datafields(graph: Graph, manifestation: URIRef) -> list[_IdentifierEmit]:
    """Walk every ``bffi:identifiedBy`` block and convert to a MARC
    datafield emit when its ``bffi:source`` URI is in the dispatch table.

    Local IDs (with no ``bffi:source`` or a source not in the table) are
    skipped — they're either the 001-bound bib ID (handled separately)
    or an identifier scheme we don't yet emit. Each additional scheme
    lands by extending :data:`_IDENTIFIER_SCHEME_TO_MARC`.
    """
    emits: list[_IdentifierEmit] = []
    # marc2bibframe2 attaches an identifier to whichever FRBR axis the field
    # describes: an ISBN lands on the Manifestation, but a serial's ISSN
    # (MARC 022) lands on the **Work**. A Manifestation-only walk lost every
    # Work-side identifier: 022 emitted nothing at all on the reference
    # corpus, despite the scheme URI and the dispatch entry both being
    # correct.
    work = _find_work_for_manifestation(graph, manifestation)
    owners: list[URIRef | BNode] = [manifestation]
    if work is not None:
        owners.append(work)
    seen: set[URIRef | BNode] = set()
    for ident in (i for o in owners for i in graph.objects(o, BFFI.identifiedBy)):
        if not isinstance(ident, URIRef | BNode) or ident in seen:
            continue
        seen.add(ident)
        value = next(graph.objects(ident, RDF.value), None)
        if not isinstance(value, Literal):
            continue
        scheme = _identifier_marc_target(graph, ident)
        if scheme is None:
            # MARC 035 non-OCoLC: a bffi:Local with an assigner organization
            # and no scheme URI at all. The emit rule's note used to claim
            # these shared `…/identifiers/local` with 016 and needed a
            # marcKey to disambiguate; in practice they carry neither, and
            # the assigner is the discriminator.
            composed = _system_control_number(graph, ident, str(value))
            if composed is None:
                continue
            emits.append(
                _IdentifierEmit(
                    tag="035", ind1=" ", ind2=" ", value=composed, assigner=None, qualifier=None
                )
            )
            continue
        qualifier = next(graph.objects(ident, BFFI.qualifier), None)
        emits.append(
            _IdentifierEmit(
                tag=scheme.tag,
                ind1=scheme.ind1,
                ind2=scheme.ind2,
                value=str(value),
                assigner=_extract_assigner_label(graph, ident),
                qualifier=str(qualifier) if isinstance(qualifier, Literal) else None,
            )
        )
    return emits


def _identifier_marc_target(graph: Graph, ident: Node) -> _IdentifierScheme | None:
    """Return the MARC ``(tag, ind1, ind2)`` for a ``bffi:Identifier`` bnode.

    Dispatch order:

    1. ``bffi:source`` URI in :data:`_IDENTIFIER_SCHEME_TO_MARC` — the
       primary path used by the cleanly-discriminated identifier
       families (010 LCCN, 015 NBN, 020 ISBN, 022 ISSN, 024 UPC/ISMN/EAN,
       …, 088 report number).
    2. ``bffi:marcKey`` literal whose first three characters match an
       entry in :data:`_MARCKEY_IDENTIFIER_DISPATCH_TAGS` — fallback for
       tags whose BIBFRAME shape can't be discriminated from
       ``bffi:source`` alone (016, 074).

    Returns ``None`` when neither path matches; the caller skips the
    bnode (either the 001-bound bib ID or an identifier scheme this
    converter doesn't yet emit).
    """
    # Scan every ``bffi:source``, not just the first. An identifier can carry
    # more than one — the scheme URI plus a vocabulary node — and
    # ``next(graph.objects(...))`` returns them in rdflib's arbitrary
    # iteration order, so taking one and giving up silently dropped the
    # field whenever the non-scheme source happened to come first. That made
    # the emit order-dependent, which is a correctness bug even where it
    # currently happens to work.
    for source in graph.objects(ident, BFFI.source):
        if not isinstance(source, URIRef):
            continue
        scheme = _IDENTIFIER_SCHEME_TO_MARC.get(source)
        if scheme is not None:
            return scheme
    for marckey in graph.objects(ident, BFFI.marcKey):
        if not isinstance(marckey, Literal):
            continue
        prefix = str(marckey)[:3]
        if prefix in _MARCKEY_IDENTIFIER_DISPATCH_TAGS:
            # 016 / 035 / 074 carry their tag as the scheme (indicators blank).
            # 023 / 026 / 383 have their indicators in the per-scheme table.
            return _MARCKEY_IDENTIFIER_SCHEME.get(prefix, _IdentifierScheme(prefix, " ", " "))
    return None


@marc_emit(
    MarcEmitMeta(
        tag="856",
        indicators=("4", "0"),
        subfields=(("u", "URI to the electronic resource"),),
        source=(
            "?m bffi:electronicLocator ?url — each URI object emits as one "
            "MARC 856 datafield with $u carrying the URL string. Indicators "
            "default to ind1=4 (HTTP) ind2=0 (Resource) per HELMET corpus convention."
        ),
        notes=(
            "$y (link text) and $z (public note) are not yet round-tripped "
            "— marc2bibframe2 wraps them on a bf:Note bnode attached to the "
            "electronic locator's containing Item; the reverse path emits "
            "only the bare URL today."
        ),
    )
)
def _extract_electronic_locators(graph: Graph, manifestation: URIRef) -> list[str]:
    """Walk ``?m bffi:electronicLocator ?url`` and return one URL string
    per object. Returns URLs sorted for deterministic emission order."""
    urls: list[str] = []
    for url in graph.objects(manifestation, BFFI.electronicLocator):
        if isinstance(url, (URIRef, Literal)):
            urls.append(str(url))
    return sorted(urls)


def _extract_assigner_label(graph: Graph, ident: Node) -> str | None:
    """Return the ``rdfs:label`` of the identifier's ``bffi:assigner``
    organisation (used for MARC 028 ``$b``), or ``None`` when absent."""
    assigner = next(graph.objects(ident, BFFI.assigner), None)
    if assigner is None:
        return None
    label = next(graph.objects(assigner, RDFS.label), None)
    return str(label) if isinstance(label, Literal) else None


def _append_simple_a_datafields(record: etree._Element, tag: str, values: tuple[str, ...]) -> None:
    """Append one MARC datafield per value, each with a single ``$a``
    subfield carrying the value and blank indicators. Used for MARC
    families whose entire emit shape is a list of bare ``$a`` rows
    (336 / 337 / 338 RDA descriptors today; potentially others)."""
    for value in values:
        df = etree.SubElement(record, f"{_MARC}datafield", tag=tag, ind1=" ", ind2=" ")
        sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
        sf_a.text = value


def _append_contributor_datafields(
    record: etree._Element, contributors: Iterable[_ContributorEmit]
) -> None:
    """Append one MARC contributor datafield per emit. Subfield order
    follows the MARC X00 spec: ``$a`` (name) → ``$e`` (relator term,
    free text) → extras from marcKey (``$t`` analytical title, ``$c``
    qualifier, ``$d`` dates, …) → ``$4`` (LoC relator code). Each is
    optional except ``$a``. Indicators come from the agent's marcKey
    when present, else default to blank."""
    for c in contributors:
        df = etree.SubElement(record, f"{_MARC}datafield", tag=c.tag, ind1=c.ind1, ind2=c.ind2)
        sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
        sf_a.text = c.label
        if c.relator_term:
            sf_e = etree.SubElement(df, f"{_MARC}subfield", code="e")
            sf_e.text = c.relator_term
        for code, value in c.extra_subfields:
            sf = etree.SubElement(df, f"{_MARC}subfield", code=code)
            sf.text = value
        if c.relator:
            sf_4 = etree.SubElement(df, f"{_MARC}subfield", code="4")
            sf_4.text = c.relator


def _append_physical_description_datafield(
    record: etree._Element, physical: _PhysicalDescription
) -> None:
    """Append the MARC 300 datafield with ``$a`` / ``$b`` / ``$c`` / ``$e``
    subfields based on which signals are present, with ISBD trailing
    punctuation (\" :\" before $b, \" ;\" before $c, \" +\" before $e)."""
    df = etree.SubElement(record, f"{_MARC}datafield", tag="300", ind1=" ", ind2=" ")
    if physical.extent is not None:
        text = physical.extent
        if physical.other_physical is not None:
            text += " :"
        elif physical.dimensions is not None:
            text += " ;"
        elif physical.accompanying_material is not None:
            text += " +"
        sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
        sf_a.text = text
    if physical.other_physical is not None:
        text = physical.other_physical
        if physical.dimensions is not None:
            text += " ;"
        elif physical.accompanying_material is not None:
            text += " +"
        sf_b = etree.SubElement(df, f"{_MARC}subfield", code="b")
        sf_b.text = text
    if physical.dimensions is not None:
        text = physical.dimensions
        if physical.accompanying_material is not None:
            text += " +"
        sf_c = etree.SubElement(df, f"{_MARC}subfield", code="c")
        sf_c.text = text
    if physical.accompanying_material is not None:
        sf_e = etree.SubElement(df, f"{_MARC}subfield", code="e")
        sf_e.text = physical.accompanying_material


def _append_identifier_datafields(
    record: etree._Element, identifiers: list[_IdentifierEmit]
) -> None:
    """Append one MARC datafield per identifier emit.

    Subfield order follows the MARC spec: ``$a`` value → ``$b``
    assigner (028 issuing-publisher name) → ``$q`` qualifier (020 ISBN
    binding / format). Indicators come from the per-scheme dispatch
    table.
    """
    for ident in identifiers:
        df = etree.SubElement(
            record, f"{_MARC}datafield", tag=ident.tag, ind1=ident.ind1, ind2=ident.ind2
        )
        sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
        sf_a.text = ident.value
        if ident.assigner is not None:
            sf_b = etree.SubElement(df, f"{_MARC}subfield", code="b")
            sf_b.text = ident.assigner
        if ident.qualifier is not None:
            sf_q = etree.SubElement(df, f"{_MARC}subfield", code="q")
            sf_q.text = ident.qualifier


def _append_note_block(
    record: etree._Element,
    *,
    notes: list[_NoteEmit],
    table_of_contents: list[str],
    policies: _PolicyEmits,
    summaries: list[str],
    intended_audiences: list[str],
) -> None:
    """Append the 5XX note block in (approximately) MARC tag-numeric
    order: 500-set general notes → 505 contents → 506 access → 520
    summary → 521 intended audience → 540 use."""
    _append_note_datafields(record, notes)
    _append_table_of_contents_datafields(record, table_of_contents)
    _append_simple_a_datafields(record, "506", policies.access)
    _append_simple_a_datafields(record, "520", tuple(summaries))
    _append_simple_a_datafields(record, "521", tuple(intended_audiences))
    _append_simple_a_datafields(record, "540", policies.use)


def _append_note_datafields(record: etree._Element, notes: list[_NoteEmit]) -> None:
    """Append one MARC 5XX-style datafield per note emit. Indicators
    default to blank; 587 overrides ind1 from the mnotetype tail."""
    for note in notes:
        df = etree.SubElement(
            record, f"{_MARC}datafield", tag=note.tag, ind1=note.ind1, ind2=note.ind2
        )
        if note.text:
            sf = etree.SubElement(df, f"{_MARC}subfield", code=note.subfield_code)
            sf.text = note.text
        for code, value in note.extra_subfields:
            sf = etree.SubElement(df, f"{_MARC}subfield", code=code)
            sf.text = value


def _append_table_of_contents_datafields(
    record: etree._Element, table_of_contents: list[str]
) -> None:
    """Append one MARC 505 datafield per table-of-contents text.

    ind1=0 = "Contents" (the default per the MARC 21 spec).
    """
    for text in table_of_contents:
        df = etree.SubElement(record, f"{_MARC}datafield", tag="505", ind1="0", ind2=" ")
        sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
        sf_a.text = text


def _append_rda_datafields(
    record: etree._Element, tag: str, entries: tuple[_RdaEntry, ...]
) -> None:
    """Append one MARC datafield per RDA descriptor: ``$a`` label (when
    present), ``$b`` 3-letter code, ``$2`` scheme name, ``$3`` materials
    specified (when ``bffi:appliesTo`` is present). Multiple values on one
    BFFI predicate produce multiple datafields per MARC convention."""
    for entry in entries:
        df = etree.SubElement(record, f"{_MARC}datafield", tag=tag, ind1=" ", ind2=" ")
        if entry.label is not None:
            sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
            sf_a.text = entry.label
        sf_b = etree.SubElement(df, f"{_MARC}subfield", code="b")
        sf_b.text = entry.code
        sf_2 = etree.SubElement(df, f"{_MARC}subfield", code="2")
        sf_2.text = entry.scheme
        if entry.applies_to is not None:
            sf_3 = etree.SubElement(df, f"{_MARC}subfield", code="3")
            sf_3.text = entry.applies_to


def _append_title_datafield(
    record: etree._Element,
    title_parts: _TitleParts,
    responsibility: str | None,
) -> None:
    """Append the MARC 245 datafield. Subfield order follows MARC 21:
    ``$a`` main title → ``$n`` part number → ``$p`` part name → ``$b``
    subtitle → ``$c`` statement of responsibility. Each is optional
    except ``$a``."""
    df = etree.SubElement(record, f"{_MARC}datafield", tag="245", ind1="0", ind2="0")
    sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
    sf_a.text = title_parts.main
    if title_parts.part_number is not None:
        sf_n = etree.SubElement(df, f"{_MARC}subfield", code="n")
        sf_n.text = title_parts.part_number
    if title_parts.part_name is not None:
        sf_p = etree.SubElement(df, f"{_MARC}subfield", code="p")
        sf_p.text = title_parts.part_name
    if title_parts.subtitle is not None:
        sf_b = etree.SubElement(df, f"{_MARC}subfield", code="b")
        sf_b.text = title_parts.subtitle
    if responsibility is not None:
        sf_c = etree.SubElement(df, f"{_MARC}subfield", code="c")
        sf_c.text = responsibility


def _append_publication_datafield(record: etree._Element, publication: _PublicationEmit) -> None:
    """Append the MARC 260 datafield with structured ``$a`` / ``$b`` / ``$c``
    when ``bffi:simplePlace`` / ``bffi:simpleAgent`` / ``bffi:simpleDate``
    are present on the Publication-typed provisionActivity. Falls back to
    a single ``$a`` carrying the flat ``bffi:publicationStatement`` literal
    when the structured parts are absent.

    ISBD trailing punctuation is added per the MARC 260 convention:
    ``$a "Place :"`` precedes ``$b``; ``$b "Publisher,"`` precedes ``$c``.
    No trailing punctuation on the last present subfield.
    """
    df = etree.SubElement(record, f"{_MARC}datafield", tag="260", ind1=" ", ind2=" ")
    if publication.place is None and publication.agent is None and publication.date is None:
        sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
        sf_a.text = publication.statement
        return
    if publication.place is not None:
        place_text = publication.place
        if publication.agent is not None:
            place_text += " :"
        elif publication.date is not None:
            place_text += ","
        sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
        sf_a.text = place_text
    if publication.agent is not None:
        agent_text = publication.agent
        if publication.date is not None:
            agent_text += ","
        sf_b = etree.SubElement(df, f"{_MARC}subfield", code="b")
        sf_b.text = agent_text
    if publication.date is not None:
        sf_c = etree.SubElement(df, f"{_MARC}subfield", code="c")
        sf_c.text = publication.date


def _append_subject_datafields(record: etree._Element, subjects: list[_SubjectEmit]) -> None:
    """Append one MARC 6XX datafield per subject emit.

    ``$a`` carries the heading text. ``$0`` (authority URI) and ``$2``
    (source-vocabulary code) emit when their respective signals are
    present in BFFI. When ``$2`` is emitted, ``ind2`` is set to ``"7"``
    per the MARC convention ("source specified in subfield $2");
    otherwise ``ind2`` is blank.
    """
    for subj in subjects:
        ind2 = "7" if subj.vocab_code else " "
        df = etree.SubElement(record, f"{_MARC}datafield", tag=subj.tag, ind1=" ", ind2=ind2)
        sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
        sf_a.text = subj.label
        # marcKey-driven extras ($t, $c, $d, …) come between $a and the
        # structured $0 / $2 — MARC subfield order is alphabetical-ish
        # but $0 / $2 sort after letters per convention.
        for code, value in subj.extra_subfields:
            sf = etree.SubElement(df, f"{_MARC}subfield", code=code)
            sf.text = value
        if subj.authority_uri:
            sf_0 = etree.SubElement(df, f"{_MARC}subfield", code="0")
            sf_0.text = subj.authority_uri
        if subj.vocab_code:
            sf_2 = etree.SubElement(df, f"{_MARC}subfield", code="2")
            sf_2.text = subj.vocab_code


def _append_classification_datafields(
    record: etree._Element, classifications: list[_ClassificationEmit]
) -> None:
    """Append one MARC classification datafield per emit with ``$a``
    portion and optional ``$2`` scheme code. The MARC tag (050 / 060 /
    070 / 080 / 082 / 084) is picked per-emit by the classification's
    BFFI type."""
    for cls in classifications:
        df = etree.SubElement(record, f"{_MARC}datafield", tag=cls.tag, ind1=" ", ind2=" ")
        sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
        sf_a.text = cls.portion
        if cls.code is not None:
            sf_2 = etree.SubElement(df, f"{_MARC}subfield", code="2")
            sf_2.text = cls.code


def _append_acquisition_source_datafields(
    record: etree._Element, sources: list[_AcquisitionSourceEmit]
) -> None:
    """Append one MARC 037 datafield per emit with ``$a`` stock number,
    ``$b`` imprint, ``$c`` acquisition terms, ``$f`` other physical,
    ``$g`` dimensions, ``$n`` copies held."""
    for src in sources:
        df = etree.SubElement(record, f"{_MARC}datafield", tag="037", ind1=" ", ind2=" ")
        if src.stock_number is not None:
            sf = etree.SubElement(df, f"{_MARC}subfield", code="a")
            sf.text = src.stock_number
        if src.imprint is not None:
            sf = etree.SubElement(df, f"{_MARC}subfield", code="b")
            sf.text = src.imprint
        if src.place is not None:
            sf = etree.SubElement(df, f"{_MARC}subfield", code="c")
            sf.text = src.place
        if src.other_physical is not None:
            sf = etree.SubElement(df, f"{_MARC}subfield", code="f")
            sf.text = src.other_physical
        if src.dimensions is not None:
            sf = etree.SubElement(df, f"{_MARC}subfield", code="g")
            sf.text = src.dimensions
        if src.copies is not None:
            sf = etree.SubElement(df, f"{_MARC}subfield", code="n")
            sf.text = src.copies


def _append_supplementary_content_datafields(
    record: etree._Element, contents: list[_SupplementaryContentEmit]
) -> None:
    """Append one MARC 353 datafield per emit with ``$a`` content,
    ``$0`` authority URI, ``$2`` source scheme code."""
    for sup in contents:
        df = etree.SubElement(record, f"{_MARC}datafield", tag="353", ind1=" ", ind2=" ")
        if sup.content is not None:
            sf = etree.SubElement(df, f"{_MARC}subfield", code="a")
            sf.text = sup.content
        if sup.authority_uri is not None:
            sf = etree.SubElement(df, f"{_MARC}subfield", code="0")
            sf.text = sup.authority_uri
        if sup.source is not None:
            sf = etree.SubElement(df, f"{_MARC}subfield", code="2")
            sf.text = sup.source


def _append_added_title_datafields(
    record: etree._Element, added_titles: list[_AddedTitleEmit]
) -> None:
    """Append 730 / 740 added-title datafields after the 7XX contributor block.

    Indicators and every subfield are taken verbatim from the parsed
    ``bffi:marcKey`` — preserves nonfiling-character ind1 plus $a/$g/$l/$n/$o/$p
    and any other code the source carried."""
    for added in added_titles:
        df = etree.SubElement(
            record, f"{_MARC}datafield", tag=added.tag, ind1=added.ind1, ind2=added.ind2
        )
        for code, value in added.subfields:
            sf = etree.SubElement(df, f"{_MARC}subfield", code=code)
            sf.text = value


def _build_marc_record(  # noqa: PLR0912, PLR0915 — structural aggregation;
    # statement and branch counts grow as additional MARC tag families
    # are added; each new family is one append call + an optional
    # presence check.
    *,
    bib_id: str,
    change_date: str | None,
    title_parts: _TitleParts | None,
    variant_titles: list[_VariantTitleEmit],
    uniform_main_entry: _AddedTitleEmit | None,
    responsibility: str | None,
    edition_statement: str | None,
    publication: _PublicationEmit | None,
    identifiers: list[_IdentifierEmit],
    language_codes: list[str],
    language_components: list[tuple[str, str]],
    temporal_coverage: list[str],
    physical: _PhysicalDescription | None,
    rda: _RdaDescriptors,
    classifications: list[_ClassificationEmit],
    contributors: list[_ContributorEmit],
    subjects: list[_SubjectEmit],
    acquisition_sources: list[_AcquisitionSourceEmit],
    supplementary_contents: list[_SupplementaryContentEmit],
    notes: list[_NoteEmit],
    table_of_contents: list[str],
    policies: _PolicyEmits,
    summaries: list[str],
    frequencies: list[_FrequencyEmit],
    playing_times: list[str],
    modes_of_issuance: list[str],
    intended_audiences: list[str],
    untraced_series: list[_UntracedSeriesEmit],
    traced_series: list[_AddedTitleEmit],
    added_titles: list[_AddedTitleEmit],
    linking_entries: list[_AddedTitleEmit],
    electronic_locators: list[str],
    leader_text: str,
) -> etree._Element:
    """Build one MARCXML ``<record>`` element with the v0+ field set."""
    record = etree.Element(f"{_MARC}record")
    leader = etree.SubElement(record, f"{_MARC}leader")
    leader.text = leader_text

    cf001 = etree.SubElement(record, f"{_MARC}controlfield", tag="001")
    cf001.text = bib_id

    if change_date is not None:
        cf005 = etree.SubElement(record, f"{_MARC}controlfield", tag="005")
        cf005.text = change_date

    # 020 / 022 / 024 / 028 identifiers come before 041 / 245 / 300 in
    # MARC tag order. Indicators and the optional $b assigner come from
    # the per-emit fields populated by the scheme dispatcher.
    _append_identifier_datafields(record, identifiers)

    _append_classification_datafields(record, classifications)

    # 037 acquisition source — after 035 identifiers, before 041 language.
    _append_acquisition_source_datafields(record, acquisition_sources)

    if language_codes or language_components:
        # ind1=1 ("item is or includes a translation") whenever a language of
        # the original is present: all 26 source 041s carrying $h in the
        # fixture corpus use ind1=1. Without $h there is no evidence either
        # way, so the indicator stays blank ("no information provided")
        # rather than asserting 0.
        translation = any(code == "h" for code, _ in language_components)
        df041 = etree.SubElement(
            record,
            f"{_MARC}datafield",
            tag="041",
            ind1="1" if translation else " ",
            ind2=" ",
        )
        for code in language_codes:
            sf_a = etree.SubElement(df041, f"{_MARC}subfield", code="a")
            sf_a.text = code
        # $h / $i / $j / … follow every $a, which is also their MARC order.
        for subfield_code, language_code in language_components:
            sf = etree.SubElement(df041, f"{_MARC}subfield", code=subfield_code)
            sf.text = language_code

    # 045 temporal coverage — simple literal emit.
    for value in temporal_coverage:
        df045 = etree.SubElement(record, f"{_MARC}datafield", tag="045", ind1=" ", ind2=" ")
        sf_a = etree.SubElement(df045, f"{_MARC}subfield", code="a")
        sf_a.text = value

    # Primary contributors (MARC 100/110/111) come before 130 in MARC
    # tag order.
    _append_contributor_datafields(record, (c for c in contributors if c.tag.startswith("1")))

    # 130 uniform main entry — between 1XX contributors and 245.
    if uniform_main_entry is not None:
        _append_added_title_datafields(record, [uniform_main_entry])

    if title_parts is not None:
        _append_title_datafield(record, title_parts, responsibility)

    # 2XX variant titles immediately follow 245. Each emits at its own
    # tag (210 / 222 / 242 / 243 / 246 / 247) with per-tag indicators.
    for variant in variant_titles:
        ind1, ind2 = _VARIANT_TITLE_INDICATORS.get(variant.tag, ("1", " "))
        df = etree.SubElement(record, f"{_MARC}datafield", tag=variant.tag, ind1=ind1, ind2=ind2)
        sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
        sf_a.text = variant.text

    if edition_statement is not None:
        df250 = etree.SubElement(record, f"{_MARC}datafield", tag="250", ind1=" ", ind2=" ")
        sf_a = etree.SubElement(df250, f"{_MARC}subfield", code="a")
        sf_a.text = edition_statement

    if publication is not None:
        _append_publication_datafield(record, publication)

    if physical is not None:
        _append_physical_description_datafield(record, physical)

    # 306 playing time precedes the frequency block (MARC tag order).
    _append_simple_a_datafields(record, "306", tuple(playing_times))

    # 310 (current) and 321 (former) frequencies dispatch from the same
    # bffi:frequency walk; each Frequency block carries its target tag.
    for freq in frequencies:
        df = etree.SubElement(record, f"{_MARC}datafield", tag=freq.tag, ind1=" ", ind2=" ")
        sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
        sf_a.text = freq.text

    # 334 mode of issuance — single $a, blank indicators.
    _append_simple_a_datafields(record, "334", tuple(modes_of_issuance))

    # 336/337/338 RDA descriptors. One datafield per code (multiple values
    # on a single predicate produce repeated datafields per MARC convention).
    _append_rda_datafields(record, "336", rda.content)
    _append_rda_datafields(record, "337", rda.media)
    _append_rda_datafields(record, "338", rda.carrier)

    # 353 supplementary content — after RDA descriptors, before notes.
    _append_supplementary_content_datafields(record, supplementary_contents)

    # 490 untraced series statements (after RDA, before notes). ISBD
    # trailing " ;" added on $a when $v volume number follows.
    for series in untraced_series:
        df = etree.SubElement(record, f"{_MARC}datafield", tag="490", ind1="0", ind2=" ")
        sf_a = etree.SubElement(df, f"{_MARC}subfield", code="a")
        sf_a.text = series.title + (" ;" if series.volume is not None else "")
        if series.volume is not None:
            sf_v = etree.SubElement(df, f"{_MARC}subfield", code="v")
            sf_v.text = series.volume

    _append_note_block(
        record,
        notes=notes,
        table_of_contents=table_of_contents,
        policies=policies,
        summaries=summaries,
        intended_audiences=intended_audiences,
    )

    # 6XX subjects come after the bibliographic-description block.
    _append_subject_datafields(record, subjects)

    # Added contributors (MARC 700/710/711) come after 6XX subjects.
    _append_contributor_datafields(record, (c for c in contributors if c.tag.startswith("7")))

    _append_added_title_datafields(record, added_titles)

    # 76X-78X linking entries.
    _append_added_title_datafields(record, linking_entries)

    # 830 traced series.
    _append_added_title_datafields(record, traced_series)

    # 856 electronic locators come last (per common MARC ordering, after
    # all subject / added-entry / series blocks).
    for url in electronic_locators:
        df = etree.SubElement(record, f"{_MARC}datafield", tag="856", ind1="4", ind2="0")
        sf_u = etree.SubElement(df, f"{_MARC}subfield", code="u")
        sf_u.text = url

    return record


def emit_marcxml(graph: Graph, *, manifestation: URIRef) -> bytes:
    """Build a MARCXML document (root: ``<record>``) for one Manifestation.

    Returns the serialised bytes, pretty-printed, with UTF-8 declaration.
    Raises :exc:`BffiToMarcError` if the bib ID can't be determined (no
    Local block + URI fragment fallback also fails).
    """
    bib_id = _extract_bib_id_from_local(graph, manifestation) or _extract_bib_id_from_uri(
        manifestation
    )
    if bib_id is None:
        raise BffiToMarcError(f"no bib ID found for manifestation {manifestation}")
    change_date = _extract_change_date(graph, manifestation)
    title_parts = _extract_main_title_parts(graph, manifestation)
    variant_titles = _extract_variant_titles(graph, manifestation)
    uniform_main_entry = _extract_uniform_main_entry(graph, manifestation)
    responsibility = _extract_responsibility_statement(graph, manifestation)
    edition_statement = _extract_edition_statement(graph, manifestation)
    publication = _extract_publication(graph, manifestation)
    identifiers = _extract_identifier_datafields(graph, manifestation)
    language_codes = _extract_language_codes(graph, manifestation)
    language_components = _extract_language_components(graph, manifestation)
    temporal_coverage = _extract_temporal_coverage(graph, manifestation)
    physical = _extract_physical_description(graph, manifestation)
    rda = _extract_rda_descriptors(graph, manifestation)
    classifications = _extract_classifications(graph, manifestation)
    contributors = _extract_contributors(graph, manifestation)
    subjects = _extract_subject_datafields(graph, manifestation)
    acquisition_sources = _extract_acquisition_source(graph, manifestation)
    supplementary_contents = _extract_supplementary_content(graph, manifestation)
    notes = (
        _extract_notes(graph, manifestation)
        + _extract_specialised_5xx_notes(graph, manifestation)
        + _extract_origin_place_datafields(graph, manifestation)
        + _extract_cataloging_source(graph, manifestation)
    )
    table_of_contents = _extract_table_of_contents(graph, manifestation)
    policies = _extract_policies(graph, manifestation)
    summaries = _extract_summaries(graph, manifestation)
    frequencies = _extract_frequency(graph, manifestation)
    playing_times = _extract_playing_times(graph, manifestation)
    modes_of_issuance = _extract_modes_of_issuance(graph, manifestation)
    intended_audiences = _extract_intended_audiences(graph, manifestation)
    untraced_series = _extract_untraced_series(graph, manifestation)
    traced_series = _extract_traced_series(graph, manifestation)
    leader_text = _build_leader(graph, manifestation)
    added_titles = _extract_added_titles(graph, manifestation)
    linking_entries = _extract_linking_entries(graph, manifestation)
    electronic_locators = _extract_electronic_locators(graph, manifestation)
    record = _build_marc_record(
        bib_id=bib_id,
        change_date=change_date,
        title_parts=title_parts,
        variant_titles=variant_titles,
        uniform_main_entry=uniform_main_entry,
        responsibility=responsibility,
        edition_statement=edition_statement,
        publication=publication,
        identifiers=identifiers,
        language_codes=language_codes,
        language_components=language_components,
        temporal_coverage=temporal_coverage,
        physical=physical,
        rda=rda,
        classifications=classifications,
        contributors=contributors,
        subjects=subjects,
        acquisition_sources=acquisition_sources,
        supplementary_contents=supplementary_contents,
        notes=notes,
        table_of_contents=table_of_contents,
        policies=policies,
        summaries=summaries,
        frequencies=frequencies,
        playing_times=playing_times,
        modes_of_issuance=modes_of_issuance,
        intended_audiences=intended_audiences,
        untraced_series=untraced_series,
        traced_series=traced_series,
        leader_text=leader_text,
        added_titles=added_titles,
        linking_entries=linking_entries,
        electronic_locators=electronic_locators,
    )
    return etree.tostring(
        record,
        pretty_print=True,
        xml_declaration=True,
        encoding="utf-8",
    )


def convert_one(bffi_path: Path, *, options: ConversionOptions) -> Path:
    """Convert one BFFI Turtle to MARCXML.

    Writes ``<output_dir>/<stem>.marcxml`` and returns the path.
    Raises :exc:`BffiToMarcError` on parse failure or when no
    Manifestation is present in the input graph.
    """
    output_path = options.output_dir / f"{bffi_path.stem.removesuffix('.bffi')}.marcxml"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    graph = Graph()
    try:
        graph.parse(bffi_path, format="turtle")
    except Exception as exc:
        raise BffiToMarcError(f"rdflib parse failed for {bffi_path}: {exc}") from exc

    manifestations = [
        m for m in graph.subjects(RDF.type, BFFI.Manifestation) if isinstance(m, URIRef)
    ]
    if not manifestations:
        raise BffiToMarcError(f"no bffi:Manifestation entity in {bffi_path} — nothing to emit")

    # v0: one MARCXML record for the first Manifestation. Multi-Manifestation
    # graphs (marc2bibframe2's preprocess-splitter output) are a follow-on
    # concern.
    marcxml_bytes = emit_marcxml(graph, manifestation=manifestations[0])
    output_path.write_bytes(marcxml_bytes)
    return output_path


def convert_corpus(*, options: ConversionOptions) -> ConversionSummary:
    """Walk ``options.input_dir`` and convert every ``*.bffi.ttl`` to MARCXML.

    Emits observability events through the active emitter (if any):

      - ``start``    once at entry, with ``entities_total``
      - ``progress`` every ``PROGRESS_CADENCE`` records
      - ``failed``   per record that raised :exc:`BffiToMarcError`
      - ``end``      once at exit, with success / failed bucket counts

    Returns the aggregate :class:`ConversionSummary`.
    """
    bffi_files = sorted(options.input_dir.glob("*.bffi.ttl"))
    total = len(bffi_files)

    emit_if_active(
        stage=STAGE,
        event="start",
        counters={"entities_total": total},
    )

    summary = ConversionSummary(total=total)

    for idx, path in enumerate(bffi_files, start=1):
        try:
            convert_one(path, options=options)
            summary.converted += 1
        except BffiToMarcError as exc:
            summary.failed += 1
            message = str(exc)
            if "no bffi:Manifestation" in message:
                summary.no_manifestation += 1
            summary.failures.append((path, message))
            emit_if_active(
                stage=STAGE,
                event="failed",
                extra={
                    "path": str(path),
                    "error": message[:240],
                    "error_type": type(exc).__name__,
                },
            )

        if idx % PROGRESS_CADENCE == 0 or idx == total:
            emit_if_active(
                stage=STAGE,
                event="progress",
                counters={"entities_processed": idx},
            )

    emit_if_active(
        stage=STAGE,
        event="end",
        counters={
            "success": summary.converted,
            "failed": summary.failed,
            "no_manifestation": summary.no_manifestation,
        },
    )

    return summary
