"""Pillar 3 orchestrator: BIBFRAME RDF/XML -> BFFI canonical Turtle.

Step 3 — v0 emit. Implements the clean-rename phase only:
every ``owl:equivalentClass`` / ``owl:equivalentProperty`` row from the
mapping doc is materialised as a URI substitution; any remaining
``bf:*`` URI in the input falls through to the output and triggers the
closed-namespace test failure. Discriminator routings (Hub /
Identifier-scheme / Title-variant / Series-link / Audio / Music) land in
step 6.

Stage label for observability sidecar events: ``bibframe2bffi``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from rdflib import BNode, Graph, Literal, URIRef

from bffi_pipeline.observability.events import emit_if_active
from bffi_pipeline.provenance.activities import now, write_record_provenance
from bffi_pipeline.provenance.vocab import bind_canonical_prefixes
from bffi_pipeline.stages.bibframe_to_bffi.mappings import (
    BF_NAMESPACE,
    CleanRenameRules,
    load_rules,
)
from bffi_pipeline.stages.bibframe_to_bffi.routings import apply_all_routings
from bffi_pipeline.validation.bffi import validate_graph as validate_bffi_graph
from bffi_pipeline.validation.sidecar import ValidationRow, ValidationSidecars

STAGE: Final[str] = "bibframe2bffi"

#: How often to emit a ``progress`` event during corpus conversion.
PROGRESS_CADENCE: Final[int] = 100


@dataclass(frozen=True)
class ConversionOptions:
    """Configuration for one corpus-conversion run."""

    input_dir: Path
    output_dir: Path
    #: Path to ``vocab/lkd.rdf``. None means look up via ``get_settings``.
    lkd_rdf_path: Path | None = None
    #: Run Boundary 3 (SHACL over the emitted Turtle). Non-blocking by
    #: specification: findings are reported in ``_validation.jsonl`` and
    #: counted, the record is kept either way. See p-062.
    validate: bool = True


@dataclass
class ConversionSummary:
    """Aggregate counts after corpus conversion ends."""

    total: int = 0
    converted: int = 0
    failed: int = 0
    #: Records that contained at least one ``bf:*`` URI after rename +
    #: Phase 4 routings. A non-zero value indicates a closed-namespace
    #: discipline gap on a term family not yet covered.
    closed_namespace_residue: int = 0
    #: Records the Boundary-3 BFFI shape reported on. Non-blocking: these
    #: are also counted in ``converted``.
    shape_flagged: int = 0
    #: Corpus-wide totals of how many times each Phase 4 routing fired.
    #: Surfaces in the observability ``end`` event so the operator can
    #: see, e.g. "this run rewrote 9 525 bf:Isbn instances".
    routing_counters: dict[str, int] = field(default_factory=dict)
    failures: list[tuple[Path, str]] = field(default_factory=list)


class BibframeToBffiError(RuntimeError):
    """A single-record conversion failed."""


#: Whitespace runs inside a URI, which the Turtle serializer refuses.
_URI_WHITESPACE: Final[re.Pattern[str]] = re.compile(r"\s+")


def _sanitise_uri(uri: URIRef) -> URIRef:
    """Return ``uri`` in a form the Turtle serializer accepts.

    rdflib's RDF/XML parser accepts URIs containing whitespace — the
    MARCXML source has a few such cases, catalogue free text typed into
    a ``$0`` authority field, which marc2bibframe2 then concatenates
    onto a LoC URI base — but the Turtle serializer raises on them
    ("does not look like a valid URI"), which would abort an otherwise
    convertible record.

    Leading / trailing whitespace is dropped; interior whitespace is
    percent-encoded rather than removed, because the run is part of the
    cataloguer's text and ``%20`` keeps the original string recoverable
    by URL-decoding the local name. Stripping alone is not enough: the
    typed-free-text case is words separated by spaces, so the interior
    runs are the ones that actually break serialisation.
    """
    return URIRef(_URI_WHITESPACE.sub("%20", str(uri).strip()))


def _rename_node(node: object, rules: CleanRenameRules) -> object:
    """Return the BFFI counterpart of ``node`` if a rename applies.

    Literals and blank nodes pass through unchanged; only URIs are
    rewritten via the rules table, after :func:`_sanitise_uri` has made
    them serialisable.
    """
    if isinstance(node, URIRef):
        return rules.rename(_sanitise_uri(node))
    return node


def _residual_bf_uris(graph: Graph) -> set[URIRef]:
    """Return every ``bf:*`` URI still present in ``graph`` after rename.

    Phase 1 expects this set to shrink to zero only for records that
    don't trigger any discriminator-routed term. The summary surfaces
    the per-record count so the operator can target step 6 work.
    """
    out: set[URIRef] = set()
    for s, p, o in graph:
        for node in (s, p, o):
            if isinstance(node, URIRef) and str(node).startswith(BF_NAMESPACE):
                out.add(node)
    return out


def rename_graph(input_graph: Graph, rules: CleanRenameRules) -> tuple[Graph, int]:
    """Apply every clean rename rule to ``input_graph``.

    The transformation is term-by-term: every URI in subject / predicate /
    object position is looked up in the rules table; matches get
    substituted, non-matches pass through. Blank nodes and literals
    pass through unchanged.

    The output graph has the canonical Turtle prefix bindings applied
    so serialisation is deterministic across records.

    Returns ``(graph, uri_whitespace_cleaned)`` — the second element
    counts distinct URIs :func:`_sanitise_uri` had to rewrite, so a
    silent repair shows up in the stage's counters and in the record's
    provenance rather than only in the output bytes.
    """
    output = Graph()
    bind_canonical_prefixes(output)
    cleaned: set[URIRef] = set()
    for s, p, o in input_graph:
        for node in (s, p, o):
            if isinstance(node, URIRef) and _sanitise_uri(node) != node:
                cleaned.add(node)
        renamed_s = _rename_node(s, rules)
        renamed_p = _rename_node(p, rules)
        renamed_o = _rename_node(o, rules)
        # Type guards keep mypy --strict happy; rdflib's add() accepts the
        # general triple shape but we narrow to the types we actually emit.
        assert isinstance(renamed_s, URIRef | BNode)
        assert isinstance(renamed_p, URIRef)
        assert isinstance(renamed_o, URIRef | BNode | Literal)
        output.add((renamed_s, renamed_p, renamed_o))
    return output, len(cleaned)


def convert_one(
    bibframe_path: Path,
    *,
    options: ConversionOptions,
    rules: CleanRenameRules,
) -> tuple[Path, int, dict[str, int]]:
    """Convert one BIBFRAME RDF/XML file to BFFI Turtle.

    Returns ``(output_path, residual_bf_count, routing_counters)``.
    Pipeline order:

      1. ``rename_graph`` applies the clean renames (including
         BFLC aliases — ``bflc:marcKey`` / ``bflc:simplePlace`` / etc.
         all rename to their ``bffi:*`` counterparts here).
      2. ``apply_all_routings`` applies the discriminator
         routings (Identifier-scheme, Title-variant, Audio, Series-link,
         Hub).
      3. Residual ``bf:*`` URIs are counted — non-zero means a term
         family beyond what Phase 1 + Phase 4 cover.

    The routing counters are per-record; the caller aggregates them
    into the corpus summary.
    """
    started = now()
    output_path = options.output_dir / f"{bibframe_path.stem.removesuffix('.bibframe')}.bffi.ttl"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    input_graph = Graph()
    try:
        input_graph.parse(bibframe_path, format="xml")
    except Exception as exc:
        raise BibframeToBffiError(f"rdflib parse failed for {bibframe_path}: {exc}") from exc

    output_graph, uri_whitespace_cleaned = rename_graph(input_graph, rules)
    routing_counters = apply_all_routings(output_graph)
    routing_counters["uri_whitespace_cleaned"] = uri_whitespace_cleaned
    residual = len(_residual_bf_uris(output_graph))

    # ``_sanitise_uri`` handles the whitespace case (see its docstring),
    # but rdflib's RDF/XML parser is permissive about more than
    # whitespace — control characters in a cataloguer-typed URI reach
    # here too. Catch the serialize-side failure per-record so one bad
    # URI in record N doesn't abort the corpus run; the closed-namespace
    # test still catches any bffi:* drift.
    try:
        turtle = output_graph.serialize(format="turtle")
    except Exception as exc:
        raise BibframeToBffiError(
            f"rdflib turtle serialize failed for {bibframe_path}: {exc}"
        ) from exc

    output_path.write_text(turtle, encoding="utf-8")

    # Provenance is mandatory (see ``CLAUDE.md``): record the Activity and
    # one decision triple per routing that actually fired, beside the
    # record's own output.
    write_record_provenance(
        output_path,
        stage=STAGE,
        bib_id=bibframe_path.name.split(".", 1)[0],
        started=started,
        ended=now(),
        used=bibframe_path,
        decisions=routing_counters,
    )
    return output_path, residual, routing_counters


def _boundary3_row(output_path: Path) -> ValidationRow | None:
    """Run Boundary 3 over the emitted Turtle on disk; row if it fails.

    Re-reading the file rather than reusing the in-memory graph is
    deliberate: the file is the artifact, and a Turtle round-trip is the
    only thing that proves what we wrote can be read back. Costs one parse
    plus ~10 ms of pyshacl per record.

    A file rdflib cannot parse back yields a ``bffi-parse`` row. That is a
    stronger signal than a shape violation — it means the emit produced
    Turtle we can't read — but Boundary 3 is non-blocking by
    specification, so it is still only reported.
    """
    graph = Graph()
    try:
        graph.parse(output_path, format="turtle")
    except Exception as exc:
        return ValidationRow(
            boundary=3,
            error_type="bffi-parse",
            bib_id=output_path.name.split(".", 1)[0],
            path=output_path,
            message=f"rdflib could not parse the emitted Turtle back: {exc}",
        )

    report = validate_bffi_graph(graph)
    if report.conforms:
        return None
    return ValidationRow(
        boundary=3,
        error_type="bffi-shape",
        bib_id=output_path.name.split(".", 1)[0],
        path=output_path,
        message=report.text,
        violations=report.text.count("Constraint Violation"),
    )


def convert_corpus(*, options: ConversionOptions) -> ConversionSummary:
    """Walk ``options.input_dir`` and convert every ``*.bibframe.xml`` to BFFI Turtle.

    Emits observability events through the active emitter (if any):

      - ``start``    once at entry, with ``entities_total``
      - ``progress`` every ``PROGRESS_CADENCE`` records
      - ``failed``   per record that raised :exc:`BibframeToBffiError`
      - ``end``      once at exit, with success / failed bucket counts, the
                     corpus-wide closed-namespace residue total, and the
                     Boundary-3 flagged count

    With ``options.validate`` on (the default) each emitted record is run
    through **Boundary 3** — the BFFI SHACL shape — over the Turtle on
    disk. The boundary is non-blocking by specification: a non-conforming
    record is still kept and still counted as converted; the finding goes
    to ``_validation.jsonl`` with its violation count. See
    `docs/validation-strategy.md` and p-062.

    Returns the aggregate :class:`ConversionSummary`.
    """
    bibframe_files = sorted(options.input_dir.glob("*.bibframe.xml"))
    total = len(bibframe_files)
    rules = load_rules(options.lkd_rdf_path)

    emit_if_active(
        stage=STAGE,
        event="start",
        counters={"entities_total": total},
    )

    summary = ConversionSummary(total=total)
    sidecars = ValidationSidecars(options.output_dir)

    for idx, path in enumerate(bibframe_files, start=1):
        try:
            output_path, residual, routings = convert_one(path, options=options, rules=rules)
            summary.converted += 1
            if residual > 0:
                summary.closed_namespace_residue += 1
            for name, count in routings.items():
                summary.routing_counters[name] = summary.routing_counters.get(name, 0) + count
            if options.validate:
                row = _boundary3_row(output_path)
                if row is not None:
                    summary.shape_flagged += 1
                    sidecars.flag(row)
        except BibframeToBffiError as exc:
            summary.failed += 1
            message = str(exc)
            summary.failures.append((path, message))
            # boundary=0: a conversion failure, not a validation finding.
            # Shares the rejection sidecar so one file answers "what is
            # missing from this stage's output, and why".
            sidecars.reject(
                ValidationRow(
                    boundary=0,
                    error_type=type(exc).__name__,
                    bib_id=path.name.split(".", 1)[0],
                    path=path,
                    message=message,
                )
            )
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
            "closed_namespace_residue": summary.closed_namespace_residue,
            "shape_flagged": summary.shape_flagged,
            **{f"routing_{name}": count for name, count in summary.routing_counters.items()},
        },
    )

    return summary
