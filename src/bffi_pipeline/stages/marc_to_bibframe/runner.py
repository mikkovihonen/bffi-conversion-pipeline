"""Pillar 2 orchestrator: corpus-wide MARCXML -> BIBFRAME RDF/XML conversion.

Walks an input directory of MARCXML records, runs each through the
marc2bibframe2 XSLT pipeline (optional preprocess split + main convert),
writes a ``<bib-id>.bibframe.xml`` per input under the output directory,
and emits observability events (start / progress / failed / end) along
the way.

Stage label for observability sidecar events: ``marc2bibframe``.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from rdflib import Graph

from bffi_pipeline import __version__
from bffi_pipeline.observability.events import emit_if_active
from bffi_pipeline.provenance.activities import (
    now,
    sidecar_path_for,
    write_record_provenance,
)
from bffi_pipeline.stages.marc_to_bibframe.xslt import (
    XsltPaths,
    XsltprocError,
    run_xsltproc,
)
from bffi_pipeline.validation.bibframe import (
    missing_root_resources,
)
from bffi_pipeline.validation.bibframe import (
    validate_graph as validate_bibframe_graph,
)
from bffi_pipeline.validation.marcxml import inspect as inspect_marcxml
from bffi_pipeline.validation.sidecar import ValidationRow, ValidationSidecars

STAGE: Final[str] = "marc2bibframe"

#: How often to emit a ``progress`` event during corpus conversion.
#: Tuned to balance sidecar density against dashboard tail-responsiveness.
PROGRESS_CADENCE: Final[int] = 100


@dataclass(frozen=True)
class ConversionOptions:
    """Configuration for one corpus-conversion run."""

    input_dir: Path
    output_dir: Path
    xslt_paths: XsltPaths
    #: Stem of the BIBFRAME URI namespace; matches our committed identifiers.
    baseuri: str = "http://urn.fi/URN:NBN:fi:bib:"
    #: Which MARC field carries the record ID. Source bib IDs live in 001.
    idfield: str = "001"
    #: Optional source URI for the Local identifier minted from ``idfield``.
    idsource: str | None = None
    #: Run the LoC preprocessing splitter before the main conversion.
    #: Default True per the LoC README's "strongly encouraged" guidance.
    preprocess: bool = True
    #: Per-record timeout in seconds. xsltproc has been seen to hang on
    #: malformed input; the timeout is the fallback when that happens.
    timeout_per_record: float = 60.0
    #: Run Boundary 1 (MARCXML input) and Boundary 2 (post-XSLT SHACL).
    #: On by default — the two checks cost ~18 ms/record against
    #: ~200 ms+/record for the two xsltproc spawns. See p-062.
    validate: bool = True
    #: Treat a Boundary-2 shape failure as a rejection, removing the
    #: record's output so the next stage never reads it. On by default since
    #: p-062 Phase B rescoped the shape to what marc2bibframe2 actually
    #: emits: every constraint in it was verified against 515 converted
    #: records, and a record that fails one has lost its title, its
    #: Work↔Instance link, or its whole administrative layer.
    strict_shapes: bool = True


@dataclass
class ConversionSummary:
    """Aggregate counts after corpus conversion ends."""

    total: int = 0
    converted: int = 0
    failed: int = 0
    #: Records rejected at a validation boundary and never converted.
    #: Distinct from ``failed``: nothing broke, the input was unusable.
    skipped_invalid: int = 0
    #: Records converted but flagged by a non-blocking boundary
    #: (content-minimum, or a Boundary-2 shape report).
    shape_flagged: int = 0
    failures: list[tuple[Path, str]] = field(default_factory=list)
    #: ``(path, reason)`` per rejected record, for the CLI's summary line.
    skips: list[tuple[Path, str]] = field(default_factory=list)


def _xslt_params(options: ConversionOptions) -> dict[str, str]:
    params: dict[str, str] = {
        "baseuri": options.baseuri,
        "idfield": options.idfield,
    }
    if options.idsource:
        params["idsource"] = options.idsource
    return params


def convert_one(marcxml_path: Path, *, options: ConversionOptions) -> Path:
    """Convert one MARCXML file to BIBFRAME RDF/XML.

    Writes ``<output_dir>/<input_stem>.bibframe.xml`` and returns the path.
    Raises :exc:`XsltprocError` on any conversion failure (preprocess or
    convert pass).
    """
    started = now()
    output_path = options.output_dir / f"{marcxml_path.stem}.bibframe.xml"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    params = _xslt_params(options)

    if options.preprocess:
        # Two-pass via a temp file: preprocess output is the convert input.
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".preprocessed.xml",
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
        try:
            pre = run_xsltproc(
                stylesheet=options.xslt_paths.preprocess,
                input_path=marcxml_path,
                timeout=options.timeout_per_record,
            )
            if not pre.ok:
                raise XsltprocError(
                    f"preprocess failed for {marcxml_path}: {pre.stderr.strip()[:240]}"
                )
            tmp_path.write_text(pre.stdout, encoding="utf-8")
            result = run_xsltproc(
                stylesheet=options.xslt_paths.convert,
                input_path=tmp_path,
                params=params,
                timeout=options.timeout_per_record,
            )
        finally:
            tmp_path.unlink(missing_ok=True)
    else:
        result = run_xsltproc(
            stylesheet=options.xslt_paths.convert,
            input_path=marcxml_path,
            params=params,
            timeout=options.timeout_per_record,
        )

    if not result.ok:
        raise XsltprocError(f"convert failed for {marcxml_path}: {result.stderr.strip()[:240]}")

    output_path.write_text(result.stdout, encoding="utf-8")

    # Provenance is mandatory (see ``CLAUDE.md``). The XSLT hop is one
    # transform with no discriminator decisions of our own, so the sidecar
    # records the Activity and the converter version only.
    write_record_provenance(
        output_path,
        stage=STAGE,
        bib_id=marcxml_path.name.split(".", 1)[0],
        started=started,
        ended=now(),
        used=marcxml_path,
        converter_version=f"bffi-pipeline/{__version__}",
    )
    return output_path


def _boundary2_row(output_path: Path, bib_id: str) -> ValidationRow | None:
    """Run Boundary 2 over the RDF/XML **on disk**; return a row if it fails.

    Validating the written file rather than an in-memory graph is
    deliberate: that file is what ``bibframe-to-bffi`` will read, so a
    serialisation-level defect is in scope. Costs one extra parse
    (~10 ms/record).

    A file rdflib cannot parse at all yields a ``bibframe-parse`` row
    rather than an exception — the next stage would count it as a failure
    anyway, and this makes it visible one hop earlier.
    """
    graph = Graph()
    try:
        graph.parse(output_path, format="xml")
    except Exception as exc:
        return ValidationRow(
            boundary=2,
            error_type="bibframe-parse",
            bib_id=bib_id,
            path=output_path,
            message=f"rdflib could not parse the converted RDF/XML: {exc}",
        )

    # Absence first: a Work-less graph has no focus node for any shape to
    # fail on, so SHACL calls it conforming. See
    # ``validation.bibframe.missing_root_resources``.
    absent = missing_root_resources(graph)
    if absent is not None:
        return ValidationRow(
            boundary=2,
            error_type="bibframe-empty",
            bib_id=bib_id,
            path=output_path,
            message=absent,
        )

    report = validate_bibframe_graph(graph, source_path=output_path)
    if report.conforms:
        return None
    return ValidationRow(
        boundary=2,
        error_type="bibframe-shape",
        bib_id=bib_id,
        path=output_path,
        message=report.text,
        violations=report.text.count("Constraint Violation"),
    )


def convert_corpus(*, options: ConversionOptions) -> ConversionSummary:
    """Walk ``options.input_dir`` and convert every ``*.xml`` to BIBFRAME RDF/XML.

    With ``options.validate`` on (the default) each record passes two
    validation boundaries — see `docs/validation-strategy.md` and p-062:

      - **Boundary 1**, before conversion. A structural failure (filename,
        encoding, XML syntax, XSD) rejects the record: it is skipped, a row
        lands in ``_errors.jsonl``, and conversion is never attempted. A
        content-minimum failure is advisory — the record converts and a row
        lands in ``_validation.jsonl``.
      - **Boundary 2**, after conversion, over the written RDF/XML. Advisory
        by default; ``options.strict_shapes`` promotes it to a rejection,
        which also removes the output and its provenance sidecar so the
        next stage cannot read a non-conforming record.

    Emits observability events through the active emitter (if any):

      - ``start``    once at entry, with ``entities_total``
      - ``progress`` every ``PROGRESS_CADENCE`` records
      - ``failed``   per record that raised :exc:`XsltprocError`
      - ``end``      once at exit, with success / failed / skipped /
                     flagged bucket counts

    Rejections deliberately do *not* emit a per-record event — that would
    put the record path in a metric label. The sidecars are the
    record-level sink.

    Returns the aggregate :class:`ConversionSummary`.
    """
    marcxml_files = sorted(options.input_dir.glob("*.xml"))
    total = len(marcxml_files)

    emit_if_active(
        stage=STAGE,
        event="start",
        counters={"entities_total": total},
    )

    summary = ConversionSummary(total=total)
    sidecars = ValidationSidecars(options.output_dir)

    for idx, path in enumerate(marcxml_files, start=1):
        flagged = False
        bib_id = path.name.split(".", 1)[0]

        if options.validate:
            outcome = inspect_marcxml(path)
            bib_id = outcome.bib_id
            if outcome.rejection is not None:
                rejection = outcome.rejection
                summary.skipped_invalid += 1
                summary.skips.append((path, str(rejection)))
                sidecars.reject(
                    ValidationRow(
                        boundary=1,
                        error_type=rejection.error_type,
                        bib_id=bib_id,
                        path=path,
                        message=rejection.message,
                    )
                )
                if idx % PROGRESS_CADENCE == 0 or idx == total:
                    emit_if_active(
                        stage=STAGE,
                        event="progress",
                        counters={"entities_processed": idx},
                    )
                continue
            if outcome.advisory is not None:
                flagged = True
                sidecars.flag(
                    ValidationRow(
                        boundary=1,
                        error_type=outcome.advisory.error_type,
                        bib_id=bib_id,
                        path=path,
                        message=outcome.advisory.message,
                    )
                )

        try:
            output_path = convert_one(path, options=options)
            if options.validate:
                row = _boundary2_row(output_path, bib_id)
                if row is not None:
                    if options.strict_shapes:
                        # Rejected after the fact: drop the output and its
                        # provenance sidecar so bibframe-to-bffi never sees
                        # a record this boundary refused.
                        output_path.unlink(missing_ok=True)
                        sidecar_path_for(output_path).unlink(missing_ok=True)
                        summary.skipped_invalid += 1
                        summary.skips.append((path, f"[{row.error_type}] {path.name}"))
                        sidecars.reject(row)
                        continue
                    flagged = True
                    sidecars.flag(row)
            summary.converted += 1
            if flagged:
                summary.shape_flagged += 1
        # One malformed record must never abort the corpus run. XsltprocError
        # is the expected shape, but the XSLT hop shells out, so an
        # unexpected type (a decode error on a non-UTF-8 record, a transient
        # OSError) would otherwise escape and kill a multi-hour run.
        # Never swallowed: counted, emitted as a ``failed`` event, and
        # re-surfaced in ``summary.failures`` for the CLI's exit code.
        except Exception as exc:
            summary.failed += 1
            message = str(exc)
            summary.failures.append((path, message))
            # boundary=0: not a validation finding but a conversion failure.
            # Same sink, because "which records are missing from the output
            # and why" is one question for the operator.
            sidecars.reject(
                ValidationRow(
                    boundary=0,
                    error_type=type(exc).__name__,
                    bib_id=bib_id,
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
            "skipped_invalid": summary.skipped_invalid,
            "shape_flagged": summary.shape_flagged,
        },
    )

    return summary
