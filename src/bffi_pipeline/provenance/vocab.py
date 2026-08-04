"""Namespaces and term URIs for the PROV-O + BFFI provenance graph.

The provenance graph is layered with the BFFI-native AdminMetadata view.
The vocabulary covers this repository's stages only — ``melinda-sync``,
``marc-to-bibframe``, ``bibframe-to-bffi``, ``bffi-to-marc`` and
``roundtrip-eval``: the :data:`MarcConversion` and :data:`Synthesis`
Activity classes, the source-MARC field token, subject reification, the
stage / decision audit pair, and the AdminMetadata description terms.
Terms for clustering, LLM judging, human review, authority reconciliation
and the canonical Work merge are deliberately absent.

This module is intentionally pure constants — no I/O, no graph mutation —
so it stays cheap to import from any stage.
"""

from __future__ import annotations

from typing import Any, cast

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, OWL, RDF, RDFS, XSD

# --- Namespaces -----------------------------------------------------------

PROV = Namespace("http://www.w3.org/ns/prov#")
BFFI = Namespace("http://urn.fi/URN:NBN:fi:schema:bffi:")
BFFI_PROV = Namespace("http://urn.fi/URN:NBN:fi:schema:bffi-prov#")
BIB = Namespace("http://urn.fi/URN:NBN:fi:bib:")
BF = Namespace("http://id.loc.gov/ontologies/bibframe/")
BFLC = Namespace("http://id.loc.gov/ontologies/bflc/")
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")
#: MADS/RDF. Neither a BFFI nor a BIBFRAME namespace, and absent from
#: ``lkd.rdf`` — but marc2bibframe2 renders 6XX subject blocks with
#: ``madsrdf:authoritativeLabel`` / ``madsrdf:Topic`` / ``madsrdf:GenreForm``,
#: and those survive the clean rename into the BFFI graph. It must stay
#: bound or rdflib invents ``ns1:`` for them — the exact failure the
#: prefix-discipline rule in ``CLAUDE.md`` exists to prevent.
MADSRDF = Namespace("http://www.loc.gov/mads/rdf/v1#")


#: Canonical short prefix → namespace mapping for every vocabulary the
#: pipeline emits in serialised output. The union covers BIBFRAME
#: (``bf``, ``bflc``), BFFI (``bffi``, our private ``bffi-prov``,
#: ``bib`` for record-scoped URIs), W3C standards (``rdf``, ``rdfs``,
#: ``skos``, ``owl``, ``xsd``, ``prov``), the Dublin Core terms set
#: (``dct``), and MADS/RDF (``madsrdf``, emitted by marc2bibframe2 on 6XX
#: subject blocks).
#:
#: Two reasons to keep this list one place:
#:
#: 1. **Stable serialiser output.** Without an explicit ``Graph.bind``,
#:    rdflib's Turtle serialiser invents ``ns1`` / ``ns2`` placeholders
#:    in graph-iteration order — non-deterministic across processes.
#:    Two records emitted by the same stage can land different
#:    ``@prefix ns1: …`` declarations; concatenating their Turtle then
#:    silently reinterprets the local-name half of any ``ns1:…`` triple
#:    in whichever record's prefix block lost the redeclaration race.
#:    The 2026-06-07 corpus-concat bug
#:    (commit ``2103de3`` and its companion fix) was exactly this
#:    failure mode.
#:
#: 2. **Per-file consistency.** A stage that binds 7 of the 13
#:    namespaces it emits leaves the other 6 prone to auto-prefixing.
#:    Historical per-stage bind lists drifted — each one missed a
#:    different subset of the namespaces its stage emitted, so every
#:    fresh predicate addition reopened the
#:    same audit. One central list collapses the audit to "did the
#:    new predicate's namespace make it into this dict?"
#:
#: Order is informational — the serialiser writes ``@prefix`` lines in
#: the order ``Graph.bind`` was called, so this dict's iteration order
#: shows up in output Turtle. Group W3C standards first, then BIBFRAME
#: family, then BFFI / pipeline-specific.
CANONICAL_TURTLE_PREFIXES: dict[str, object] = {
    "rdf": RDF,
    "rdfs": RDFS,
    "skos": SKOS,
    "owl": OWL,
    "xsd": XSD,
    "dct": DCTERMS,
    "prov": PROV,
    "bf": BF,
    "bflc": BFLC,
    "madsrdf": MADSRDF,
    "bffi": BFFI,
    "bffi-prov": BFFI_PROV,
    "bib": BIB,
}


def bind_canonical_prefixes(graph: Graph) -> Graph:
    """Bind every namespace in :data:`CANONICAL_TURTLE_PREFIXES` on
    ``graph``. Returns the same graph for fluent use.

    Call this immediately before any ``graph.serialize(format="turtle")``
    in the pipeline so the output has stable, human-readable
    ``@prefix`` declarations and zero auto-generated ``ns1``/``ns2``
    placeholders. Idempotent — calling multiple times is harmless;
    binding a prefix that's already bound is a no-op.

    Binding namespaces the graph doesn't actually use is harmless: the
    Turtle serialiser only emits ``@prefix`` lines for namespaces that
    appear in at least one triple. The extra ``Graph.bind`` calls are
    O(1) each, so the cost is negligible.
    """
    for short, ns in CANONICAL_TURTLE_PREFIXES.items():
        # ``ns`` is a heterogeneous mix of ``Namespace`` instances and
        # rdflib's ``DefinedNamespace`` metaclasses (e.g. ``RDF``,
        # ``DCTERMS``); both work with ``Graph.bind`` at runtime, but
        # the type stubs only narrow at the call site.
        graph.bind(short, cast("Any", ns))
    return graph


# --- Activity classes -----------------------------------------------------

MarcConversion: URIRef = BFFI_PROV.MarcConversion
#: Sibling of :data:`MarcConversion`. Emitted when the converter's
#: salvage layer synthesises a field to make a record meet
#: :func:`bffi_pipeline.validation.marcxml.validate_minimum_content`.
#: Carries one ``prov:used`` link to the source MarcConversion Activity
#: for the same record, so the audit chain is traversable from a
#: synthesised triple back to its MARC input. Predicate set documented
#: in :mod:`bffi_pipeline.provenance.vocab` below
#: (``synthetic*`` predicates).
Synthesis: URIRef = BFFI_PROV.Synthesis

# --- bffi-prov predicates emitted by marc-to-bibframe ---------------------

localBibId: URIRef = BFFI_PROV.localBibId
converterVersion: URIRef = BFFI_PROV.converterVersion

#: Source-MARC-field provenance token. Attached to
#: every BFFI / BIBFRAME entity derived from a specific source MARC
#: datafield or controlfield instance. Computed post-conversion from source
#: MARCXML and carried through every downstream transformation
#: unchanged by later processing.
#:
#: Value format: ``"<bib_id>:<tag>:<within-tag-ordinal>"``. The ordinal
#: is 1-indexed position of this field instance within the same-tag
#: bucket in source MARCXML document order. Controlfields use ordinal 1.
#:
#: Cardinality: 0..n per entity. Zero on pipeline-synthesised entities
#: (Activity URIs, canonical mint anchors). One on the common case
#: (one source field → one entity). Many on canonical entities merged
#: across editions (one token per contributing record).
fromMarcField: URIRef = BFFI_PROV.fromMarcField

# --- Subject reification via standard rdf:Statement ---------------------
#
# Originally proposed as a triplet of locally-minted ``bffi:SubjectLink`` /
# ``bffi:hasSubjectLink`` / ``bffi:subjectTarget`` terms. Switched to
# W3C-standard ``rdf:Statement`` reification at commit time so the BFFI
# namespace doesn't grow a private extension for a problem RDF already
# solves.
#
# Shape:
#
#     <stmt> rdf:type rdf:Statement ;
#            rdf:subject   <work-uri> ;
#            rdf:predicate bffi:subject ;
#            rdf:object    <target-uri> ;
#            bffi-prov:fromMarcField "<bib>:<tag>:<ord>" .
#
# The reified statement URI is per-record per-occurrence
# (``http://urn.fi/URN:NBN:fi:bib:subject-statement:<bib>:<tag>:<ord>``)
# so each cataloguer-typed 6XX gets its own provenance anchor even
# when the target URI is shared (YSO / finaf authority URIs).
#
# The flat ``<work> bffi:subject <target>`` triple is retained as the
# derived shortcut Skosmos and most query consumers walk.

#: Property whose triples are the targets of subject reification.
#: Stored here for parity with the older ``hasSubjectLink`` symbol —
#: used as the value of ``rdf:predicate``.
reifiedSubjectPredicate: URIRef = BFFI.subject

# --- bffi-prov decision-audit predicates ----------------------------------
#
# ``stage`` tags which pipeline stage wrote an Activity; ``decision``
# records what a non-trivial routing call chose.

stage: URIRef = BFFI_PROV.stage
decision: URIRef = BFFI_PROV.decision

# --- bffi-prov predicates emitted by conversion salvage (Synthesis) ------
# Every Synthesis Activity carries the four below.

#: The BFFI field synthesised, e.g. ``"bf:contribution/bf:agent"`` for
#: a creator salvaged during conversion. Free-text; the value is the
#: graph path the consumer should look at, not a URI.
syntheticField: URIRef = BFFI_PROV.syntheticField
#: Human-readable method tag, e.g. ``"creator-from-245c (regex)"`` or
#: ``"anonymous-by-convention"``. Used in the per-run TSV's ``method``
#: column verbatim.
syntheticMethod: URIRef = BFFI_PROV.syntheticMethod
#: Phase B tier ID — ``"B1"`` (245$c parse), ``"B2"`` (publisher-as-
#: corporate-creator), ``"B3"`` (anonymous sentinel). Future plans
#: extending the salvage taxonomy reuse the same predicate with a
#: distinct tier string.
syntheticTier: URIRef = BFFI_PROV.syntheticTier
#: Confidence in the synthesised value, 0.0 to 1.0. Tier-specific
#: bands documented in ``docs/bibliographic-minimum.md``. B1 regex
#: emits 0.5-0.8; B1 LLM cascade caps at 0.7; B2 emits 0.3; B3 emits 0.1.
syntheticConfidence: URIRef = BFFI_PROV.syntheticConfidence
#: The literal or URI now in the synthesised resource — for B1/B2 the
#: agent name, for B3 the sentinel URI. Persisted on the Synthesis
#: Activity so the Phase C.3 retrospective CLI can rebuild the TSV's
#: ``synthesised_value`` column byte-identically without traversing
#: the BIBFRAME graph.
syntheticValue: URIRef = BFFI_PROV.syntheticValue
#: The MARC source field(s) the salvage tier read — ``"245$c"`` for
#: B1, ``"260$b/264$b"`` for B2, ``"(none)"`` for B3. Persisted on
#: the Synthesis Activity so the retrospective CLI doesn't have to
#: infer this from the method tag.
syntheticMarcSource: URIRef = BFFI_PROV.syntheticMarcSource

# --- bffi-prov predicates added by conversion salvage -------------------

#: Boolean flag marking synthetic-sentinel resources
#: (Agents, Works) that downstream stages must NOT key on. The B3
#: sentinel agent at :data:`SENTINEL_AGENT_UNKNOWN` carries this
#: triple. Downstream consumers honour it via their exclude rules.
#:
#: Lives in the ``bffi-prov:`` namespace (not ``bffi:``) because the
#: flag is pipeline-internal metadata — it identifies a synthetic
#: stand-in produced by our salvage logic, not a bibliographic
#: property of the agent itself.
syntheticSentinel: URIRef = BFFI_PROV.syntheticSentinel

# --- Stable sentinel URIs (committed identifiers) -----------------------

#: Single shared sentinel agent URI for B3
#: (anonymous-by-convention) salvages. Multiple records sharing this
#: URI is correct — they share the property "no known author", not
#: the claim of being by the same person. Carries the
#: :data:`syntheticSentinel` flag. Committed identifier per
#: ``CLAUDE.md`` § "Committed identifiers"; do not change without
#: surfacing.
SENTINEL_AGENT_UNKNOWN: URIRef = URIRef("http://urn.fi/URN:NBN:fi:bib:agent:unknown")


def is_synthetic_sentinel(graph: Graph, resource: URIRef) -> bool:
    """Return True if ``resource`` carries
    ``bffi-prov:syntheticSentinel "true"``.

    Downstream stages call this to decide whether to skip a resource:

    - Downstream clustering already skips via the ``bffi:PrimaryContribution``
      filter (the B3 salvage emits MARC 710 → non-primary), so the
      sentinel is naturally excluded from union-find keying.
    - **The reconciliation walker** for non-primary contributions
      is the explicit consumer — it walks every non-primary
      contribution and would otherwise submit ``Tekijä tuntematon``
      to KANTO. The walker calls this helper to short-circuit.

    Returns False on any non-True value (including missing predicate,
    explicit "false", non-boolean literal).
    """
    for o in graph.objects(resource, syntheticSentinel):
        if isinstance(o, Literal) and str(o).lower() == "true":
            return True
    return False


# --- Stable agent / process URIs ------------------------------------------

AGENT_MARC2BIBFRAME2: URIRef = BIB["agent/marc2bibframe2"]
GEN_PROCESS_PIPELINE_V0_1_0: URIRef = BIB["gen-process/bffi-pipeline/v0.1.0"]
DESC_CONV_BFFI_1_0_0: URIRef = BIB["desc-conv/bffi-1.0.0"]
DESC_LEVEL_MINIMUM: URIRef = BIB["desc-level/minimum"]
ENC_LEVEL_AUTO: URIRef = BIB["enc-level/auto"]
RECORDING_SOURCE_LOCAL: URIRef = BIB["recording-source/local"]
METADATA_LICENSOR_CC0: URIRef = BIB["metadata-licensor/cc0"]
SOURCE_URI: URIRef = URIRef("http://urn.fi/URN:NBN:fi:bib:source:local")

# --- AdminMetadata predicates --------------------------------------------

adminMetadata: URIRef = BFFI.adminMetadata
adminMetadataFor: URIRef = BFFI.adminMetadataFor
#: AdminMetadata creation/change dates rebound to the canonical
#: ``lkd.rdf`` terms. ``descriptionCreationDate`` and ``dateGenerated``
#: both meant "when the converter generated this admin block" — collapsed onto
#: ``bffi:generationDate`` (lkd.rdf, domain AdminMetadata). The
#: Python attribute names are retained so call sites don't churn,
#: per the ``sourceMetadata`` precedent.
descriptionCreationDate: URIRef = BFFI.generationDate
dateGenerated: URIRef = BFFI.generationDate

#: "When our pipeline last updated this AdminMetadata block." Distinct
#: from ``bffi:changeDate`` which now carries the SOURCE description's
#: change date (MARC 005). ``dct:modified`` is the standard term for
#: "the resource was changed at this time" without binding it to a
#: specific actor — fits the downstream update semantic exactly.
descriptionChangeDate: URIRef = DCTERMS.modified
descriptionModifier: URIRef = BFFI.descriptionModifier
descriptionConventions: URIRef = BFFI.descriptionConventions
descriptionLevel: URIRef = BFFI.descriptionLevel
encodingLevel: URIRef = BFFI.encodingLevel
descriptionAuthentication: URIRef = BFFI.descriptionAuthentication
generationProcess: URIRef = BFFI.generationProcess
metadataLicensor: URIRef = BFFI.metadataLicensor
recordingSource: URIRef = BFFI.recordingSource
#: Source-record pointer on an AdminMetadata block. Was previously
#: minted as ``bffi:sourceMetadata`` (a local extension absent from
#: ``vocab/lkd.rdf``); migrated to standard PROV-O
#: ``prov:hadPrimarySource`` — semantically exact and avoids a private
#: ``bffi:`` term. Python attribute name retained so existing call
#: sites (``V.sourceMetadata``) keep working without a renaming pass.
sourceMetadata: URIRef = PROV.hadPrimarySource

AdminMetadata: URIRef = BFFI.AdminMetadata

__all__ = [
    "AGENT_MARC2BIBFRAME2",
    "BF",
    "BFFI",
    "BFFI_PROV",
    "BFLC",
    "BIB",
    "CANONICAL_TURTLE_PREFIXES",
    "DESC_CONV_BFFI_1_0_0",
    "DESC_LEVEL_MINIMUM",
    "ENC_LEVEL_AUTO",
    "GEN_PROCESS_PIPELINE_V0_1_0",
    "MADSRDF",
    "METADATA_LICENSOR_CC0",
    "PROV",
    "RDF",
    "RDFS",
    "RECORDING_SOURCE_LOCAL",
    "SENTINEL_AGENT_UNKNOWN",
    "SKOS",
    "SOURCE_URI",
    "XSD",
    "AdminMetadata",
    "MarcConversion",
    "Synthesis",
    "adminMetadata",
    "adminMetadataFor",
    "bind_canonical_prefixes",
    "converterVersion",
    "dateGenerated",
    "decision",
    "descriptionAuthentication",
    "descriptionChangeDate",
    "descriptionConventions",
    "descriptionCreationDate",
    "descriptionLevel",
    "descriptionModifier",
    "encodingLevel",
    "fromMarcField",
    "generationProcess",
    "is_synthetic_sentinel",
    "localBibId",
    "metadataLicensor",
    "recordingSource",
    "reifiedSubjectPredicate",
    "sourceMetadata",
    "stage",
    "syntheticConfidence",
    "syntheticField",
    "syntheticMarcSource",
    "syntheticMethod",
    "syntheticSentinel",
    "syntheticTier",
    "syntheticValue",
]
