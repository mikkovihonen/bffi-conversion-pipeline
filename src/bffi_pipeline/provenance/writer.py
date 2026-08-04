"""On-disk Turtle writer for the provenance named graph.

The pipeline persists the provenance graph as a Turtle file under
``BFFI_DATA_DIR``: ``provenance.ttl``, holding the
``<http://urn.fi/URN:NBN:fi:bib:graph:provenance>`` named graph. It
carries the Activities this repository's stages emit —
``bffi-prov:MarcConversion`` and ``bffi-prov:Synthesis``.

The writer is intentionally minimal: it keeps an in-memory
``rdflib.Graph`` and serializes it to disk on :meth:`flush` (or on
context-manager exit) using tmp-then-rename for crash safety.
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import Final

from rdflib import Graph

from bffi_pipeline.config import get_settings
from bffi_pipeline.provenance import vocab as V

#: Named-graph URI for the provenance graph.
PROVENANCE_GRAPH_URI: Final[str] = "http://urn.fi/URN:NBN:fi:bib:graph:provenance"

#: Default Turtle filename under ``BFFI_DATA_DIR``.
PROVENANCE_FILENAME: Final[str] = "provenance.ttl"


def default_provenance_path() -> Path:
    """Return ``<BFFI_DATA_DIR>/provenance.ttl`` from the live Settings."""
    return get_settings().data_dir / PROVENANCE_FILENAME


def _atomic_serialize(graph: Graph, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    V.bind_canonical_prefixes(graph)
    tmp = path.with_suffix(path.suffix + ".tmp")
    graph.serialize(destination=str(tmp), format="turtle")
    tmp.replace(path)


# --- Main provenance writer ----------------------------------------------


class ProvenanceWriter:
    """In-memory accumulator that flushes the provenance graph to Turtle.

    Use as a context manager so the final ``flush`` is guaranteed even on
    exception::

        with ProvenanceWriter() as writer:
            writer.graph.add(...)
        # Turtle file written on scope exit.

    On construction the existing ``provenance.ttl`` is parsed back so
    re-runs append rather than overwrite.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_provenance_path()
        self.graph = Graph()
        self._bind_prefixes()
        if self.path.is_file():
            self.graph.parse(str(self.path), format="turtle")

    def _bind_prefixes(self) -> None:
        V.bind_canonical_prefixes(self.graph)

    # --- Persistence -----------------------------------------------------

    def flush(self) -> None:
        """Serialise the in-memory graph to ``self.path`` atomically."""
        _atomic_serialize(self.graph, self.path)

    def __enter__(self) -> ProvenanceWriter:
        return self

    def __exit__(self, *args: object) -> None:
        with suppress(Exception):
            self.flush()


__all__ = [
    "PROVENANCE_FILENAME",
    "PROVENANCE_GRAPH_URI",
    "ProvenanceWriter",
    "default_provenance_path",
]
