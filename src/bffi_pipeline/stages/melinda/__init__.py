"""OAI-PMH sync for Melinda bibliographic records → MARCXML.

Fetches bibliographic records from the Melinda OAI-PMH endpoint
(https://oai-pmh.api.melinda.kansalliskirjasto.fi/bib), writes
per-record MARCXML files to ``marcxml/melinda/``, and tracks
resumption tokens for incremental sync.

Stage label for observability sidecar events: ``melinda-sync``.
"""
