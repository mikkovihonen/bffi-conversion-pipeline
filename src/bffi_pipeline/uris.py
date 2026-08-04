"""Deterministic URI minting for BFFI Works, Expressions, and Manifestations.

All URI construction in this project goes through this module — never
concatenate URI strings elsewhere (see ``CLAUDE.md`` "Conventions"). URIs
are SHA-1 hashes of canonicalised inputs so re-runs are idempotent under
benign surface variations.

Two minting rules coexist — canonical and raw:

* **Canonical**:
  :func:`mint_work_uri` ``(creator_uri, original_title)``,
  :func:`mint_expression_uri` ``(work_uri, language)`` — input
  canonicalisation collapses whitespace/case/Unicode form (diacritics
  preserved). Two records with the same creator and the same original
  title hash to the same Work URI, so translations across language
  editions converge on one Work. No conversion stage calls these today;
  they are library surface exercised by the unit tests.

* **Raw**, minted inside the ``bibframe-to-bffi`` hop:
  :func:`mint_raw_work_uri` / :func:`mint_raw_expression_uri` /
  :func:`mint_raw_manifestation_uri` hash the source ``bf:Work`` /
  ``bf:Instance`` URI string. The same XSLT input always produces the
  same raw BFFI URI on re-run.

Manifestations are 1:1 with source bib records (no merging — each
record describes exactly one published embodiment), so there's no
canonical mint variant for them; the raw URI IS the canonical URI.

:func:`register_sparql_functions` registers an ``arq:sha1`` equivalent
with rdflib. No SPARQL is used by the conversion today (see ``CLAUDE.md``
"Conventions"); the helper exists for parity if a query ever needs it.
"""

from __future__ import annotations

import hashlib
import unicodedata
from typing import Any, cast

from rdflib import Literal, URIRef
from rdflib.plugins.sparql.operators import register_custom_function

from bffi_pipeline.config import get_settings

_FIELD_SEP = "\x00"
_ARQ_SHA1: URIRef = URIRef("http://jena.apache.org/ARQ/function#sha1")


def _normalize_uri(uri: str) -> str:
    return uri.strip()


def _normalize_title(title: str) -> str:
    """NFC + collapse internal whitespace + strip + casefold."""
    nfc = unicodedata.normalize("NFC", title)
    return " ".join(nfc.split()).casefold()


def _normalize_language(lang: str) -> str:
    return lang.strip().casefold()


def _sha1(*parts: str) -> str:
    payload = _FIELD_SEP.join(parts).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


def mint_work_uri(creator_uri: str, original_title: str) -> str:
    """Mint a deterministic canonical Work URI."""
    digest = _sha1(_normalize_uri(creator_uri), _normalize_title(original_title))
    return f"{get_settings().work_namespace}{digest}"


def mint_expression_uri(work_uri: str, language: str) -> str:
    """Mint a deterministic canonical Expression URI."""
    digest = _sha1(_normalize_uri(work_uri), _normalize_language(language))
    return f"{get_settings().expression_namespace}{digest}"


def mint_raw_work_uri(bf_work_uri: str) -> str:
    """Mint a raw BFFI Work URI from a source ``bf:Work`` URI.

    Mints ``"http://urn.fi/URN:NBN:fi:bib:work:" + sha1(<bf:Work URI>)``.
    """
    digest = hashlib.sha1(_normalize_uri(bf_work_uri).encode("utf-8")).hexdigest()
    return f"{get_settings().work_namespace}{digest}"


def mint_raw_expression_uri(bf_work_uri: str) -> str:
    """Mint a raw BFFI Expression URI from a source ``bf:Work`` URI.

    Note: the *raw* expression URI is keyed off the source bf:Work alone.
    Canonical Expression URIs take a Work URI plus a language tag.
    """
    digest = hashlib.sha1(_normalize_uri(bf_work_uri).encode("utf-8")).hexdigest()
    return f"{get_settings().expression_namespace}{digest}"


def mint_raw_manifestation_uri(bf_instance_uri: str) -> str:
    """Mint a raw BFFI Manifestation URI from a source ``bf:Instance`` URI.

    Manifestations are 1:1 with source bib records in our pipeline, so
    each ``bf:Instance`` minted by marc2bibframe2 maps to exactly one
    ``bffi:Manifestation`` — no canonical / raw distinction (no merge
    step, unlike Works). The helper is named ``raw`` for naming
    consistency with :func:`mint_raw_work_uri` even though there's no
    paired ``mint_manifestation_uri`` canonical variant.
    """
    digest = hashlib.sha1(_normalize_uri(bf_instance_uri).encode("utf-8")).hexdigest()
    return f"{get_settings().manifestation_namespace}{digest}"


def _arq_sha1_impl(value: Any) -> Literal:
    """rdflib implementation of the Jena ``arq:sha1`` extension function."""
    return Literal(hashlib.sha1(str(value).encode("utf-8")).hexdigest())


_functions_registered: list[bool] = [False]


def register_sparql_functions() -> None:
    """Register the ``arq:sha1`` extension function with rdflib (idempotent)."""
    if _functions_registered[0]:
        return
    # rdflib's stub types narrow this to a (Expr, FrozenBindings)->Node callable
    # but at runtime non-raw functions receive RDF terms positionally; the
    # `raw=False` (default) overload is the right shape for our use.
    register_custom_function(_ARQ_SHA1, cast("Any", _arq_sha1_impl))
    _functions_registered[0] = True


__all__ = [
    "mint_expression_uri",
    "mint_raw_expression_uri",
    "mint_raw_manifestation_uri",
    "mint_raw_work_uri",
    "mint_work_uri",
    "register_sparql_functions",
]
