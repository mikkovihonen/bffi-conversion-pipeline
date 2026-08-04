"""OAI-PMH protocol client for Melinda.

Implements ListRecords request with resumption token tracking.
Uses httpx for non-blocking I/O and lxml for XML parsing.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from xml.etree import ElementTree as ET

import httpx

logger = logging.getLogger(__name__)

#: OAI-PMH base URL for Melinda bibliographic records
MELINDA_OAI_PMH_BASE = "https://oai-pmh.api.melinda.kansalliskirjasto.fi/bib"

#: Metadata prefix for MARCXML format (OAI-PMH standard)
METADATA_PREFIX = "marc21"

#: OAI-PMH request timeout in seconds
TIMEOUT = 30.0

#: Namespace mappings for OAI-PMH XML parsing
NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "marc": "http://www.loc.gov/MARC21/slim",
}


@dataclass(frozen=True)
class OaiRecord:
    """A single OAI-PMH record with its identifier and metadata."""

    identifier: str
    #: The MARCXML content as a string
    metadata_xml: str
    #: The record's datestamp in OAI-PMH format (ISO 8601 UTC)
    datestamp: str
    #: Whether the record is marked as deleted
    deleted: bool = False


@dataclass(frozen=True)
class ListRecordsResponse:
    """Response from a ListRecords request."""

    records: list[OaiRecord]
    #: Resumption token for the next request, or None if no more records
    resumption_token: str | None
    #: The expiration date of the resumption token (if present)
    expiration: str | None
    #: Cursor position in the complete list
    cursor: int | None
    #: Total number of records matching the request
    complete_list_size: int | None


class OaiPmhError(Exception):
    """Base exception for OAI-PMH errors."""

    pass


class OaiPmhResponseError(OaiPmhError):
    """OAI-PMH error response from the server."""

    pass


def _parse_record(record_elem: ET.Element) -> OaiRecord:
    """Parse a single <record> element from OAI-PMH response."""
    # Get the OAI identifier
    identifier_elem = record_elem.find("oai:header/oai:identifier", NS)
    if identifier_elem is None or identifier_elem.text is None:
        raise OaiPmhError("Record missing <identifier>")
    identifier = identifier_elem.text

    # Get the datestamp
    datestamp_elem = record_elem.find("oai:header/oai:datestamp", NS)
    if datestamp_elem is None or datestamp_elem.text is None:
        raise OaiPmhError(f"Record {identifier} missing <datestamp>")
    datestamp = datestamp_elem.text

    # Check if the record is deleted
    header = record_elem.find("oai:header", NS)
    deleted = header is not None and header.get("status") == "deleted"

    if deleted:
        # Deleted records have no metadata
        return OaiRecord(
            identifier=identifier,
            metadata_xml="",
            datestamp=datestamp,
            deleted=True,
        )

    # Get the metadata (MARCXML)
    metadata_elem = record_elem.find("oai:metadata", NS)
    if metadata_elem is None:
        raise OaiPmhError(f"Record {identifier} missing <metadata>")

    # The MARCXML is typically wrapped in a MARC record element
    marc_elem = metadata_elem.find("marc:record", NS)
    if marc_elem is None:
        raise OaiPmhError(f"Record {identifier} metadata missing MARC record")

    # Serialize the MARC record back to XML string
    metadata_xml = ET.tostring(marc_elem, encoding="unicode")

    return OaiRecord(
        identifier=identifier,
        metadata_xml=metadata_xml,
        datestamp=datestamp,
        deleted=False,
    )


def _parse_resumption_token(
    elem: ET.Element | None,
) -> tuple[str | None, str | None, int | None, int | None]:
    """Extract resumption token info from a <resumptionToken> element."""
    if elem is None:
        return None, None, None, None

    token = elem.text
    expiration = elem.get("expirationDate")
    cursor_str = elem.get("cursor")
    complete_list_size_str = elem.get("completeListSize")

    cursor = int(cursor_str) if cursor_str else None
    complete_list_size = int(complete_list_size_str) if complete_list_size_str else None

    return token, expiration, cursor, complete_list_size


def _parse_response(response_text: str) -> ListRecordsResponse:
    """Parse OAI-PMH ListRecords response XML."""
    try:
        root = ET.fromstring(response_text)
    except ET.ParseError as e:
        raise OaiPmhError(f"Invalid XML response: {e}") from e

    # Check for OAI error elements
    error_elem = root.find("oai:error", NS)
    if error_elem is not None:
        code = error_elem.get("code", "unknown")
        message = error_elem.text or "(no message)"
        raise OaiPmhResponseError(f"OAI-PMH error {code}: {message}")

    # Parse records
    records = []
    for record_elem in root.findall("oai:ListRecords/oai:record", NS):
        try:
            record = _parse_record(record_elem)
            records.append(record)
        except OaiPmhError as e:
            logger.warning(f"Failed to parse record: {e}")
            # Continue with next record on parse errors

    # Parse resumption token info
    resumption_elem = root.find("oai:ListRecords/oai:resumptionToken", NS)
    token, expiration, cursor, complete_list_size = _parse_resumption_token(resumption_elem)

    return ListRecordsResponse(
        records=records,
        resumption_token=token,
        expiration=expiration,
        cursor=cursor,
        complete_list_size=complete_list_size,
    )


def list_records(
    from_date: str | None = None,
    until_date: str | None = None,
    resumption_token: str | None = None,
    base_url: str = MELINDA_OAI_PMH_BASE,
    timeout: float = TIMEOUT,
) -> ListRecordsResponse:
    """Fetch a batch of records via OAI-PMH ListRecords request.

    Arguments:
        from_date: ISO 8601 UTC date (YYYY-MM-DD) to start from (inclusive).
        until_date: ISO 8601 UTC date (YYYY-MM-DD) to end at (inclusive).
        resumption_token: Token from a previous response to continue fetching.
        base_url: OAI-PMH endpoint URL.
        timeout: Request timeout in seconds.

    Returns:
        ListRecordsResponse with records and next resumption token.

    Raises:
        OaiPmhResponseError: If the server returns an error.
        OaiPmhError: On parsing or network errors.
    """
    params: dict[str, str] = {
        "verb": "ListRecords",
    }

    if resumption_token:
        params["resumptionToken"] = resumption_token
    else:
        params["metadataPrefix"] = METADATA_PREFIX
        if from_date:
            params["from"] = from_date
        if until_date:
            params["until"] = until_date

    try:
        response = httpx.get(base_url, params=params, timeout=timeout, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise OaiPmhError(f"HTTP request failed: {e}") from e

    return _parse_response(response.text)


def iter_all_records(
    from_date: str | None = None,
    until_date: str | None = None,
    base_url: str = MELINDA_OAI_PMH_BASE,
    timeout: float = TIMEOUT,
) -> Iterator[OaiRecord]:
    """Iterate over all available records via OAI-PMH, handling resumption automatically.

    Yields records one by one, making additional ListRecords requests as needed
    to fetch all records matching the date range.

    Arguments:
        from_date: ISO 8601 UTC date (YYYY-MM-DD) to start from (inclusive).
        until_date: ISO 8601 UTC date (YYYY-MM-DD) to end at (inclusive).
        base_url: OAI-PMH endpoint URL.
        timeout: Request timeout in seconds.

    Raises:
        OaiPmhResponseError: If the server returns an error.
        OaiPmhError: On parsing or network errors.
    """
    resumption_token = None

    while True:
        response = list_records(
            from_date=from_date,
            until_date=until_date,
            resumption_token=resumption_token,
            base_url=base_url,
            timeout=timeout,
        )

        yield from response.records

        if response.resumption_token:
            resumption_token = response.resumption_token
        else:
            break
