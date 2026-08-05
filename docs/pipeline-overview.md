# BFFI Conversion Pipeline — Architecture Overview

This document gives a high-level view of the pipeline's data flows, stage
boundaries, and the surrounding diagnostic and observability tooling. It is
intended for operators who need to understand *what happens when you run the
pipeline*, not the per-field routing decisions — those live in the mapping
references.

## The three pillars

The pipeline is built around three conversion stages, each a self-contained
Python package that neither imports from nor is imported by the others.

```mermaid
flowchart LR
    MARC[MARCXML] -->|marc-to-bibframe| BF[BIBFRAME RDF]
    BF -->|bibframe-to-bffi| BFFI[BFFI canonical Turtle]
    BFFI -->|bffi-to-marc| MARC2["MARCXML (reconstructed)"]
```

| Stage | Input | Output | Implementation |
|---|---|---|---|
| `marc-to-bibframe` | MARCXML records | BIBFRAME RDF graph | LoC marc2bibframe2 XSLT (vendored, see `third_party/marc2bibframe2/`) |
| `bibframe-to-bffi` | BIBFRAME RDF graph | BFFI-only canonical Turtle | 31 discriminator routings in `routings.py`; mandatory provenance writes |
| `bffi-to-marc` | BFFI canonical Turtle | Reconstructed MARCXML | Reverse-routing table; see `docs/bffi_to_marc_mapping.md` for known limitations |

## Full pipeline data flow

```mermaid
flowchart TD
    subgraph Upstream["Upstream (out of scope)"]
        ILS[Melinda ILS / national bibliographic database]
        OAI[OAI-PMH endpoint]
    end

    subgraph Ingest["Ingestion"]
        MEL[melinda-sync<br/>OAI-PMH → MARCXML]
    end

    subgraph Conversion["Conversion (three pillars)"]
        M2B[marc-to-bibframe<br/>XSLT transform]
        B2BF[bibframe-to-bffi<br/>31 routings]
        BF2M[bffi-to-marc<br/>reverse reconstruction]
    end

    subgraph Eval["Evaluation"]
        RT[roundtrip-eval<br/>diff + review HTML]
    end

    ILS --> OAI
    OAI --> MEL
    MEL --> M2B
    M2B --> B2BF
    B2BF --> BF2M
    BF2M --> RT
```

## Stage detail

### 1. `melinda-sync` — OAI-PMH → MARCXML

Harvests bibliographic records from the Melinda OAI-PMH endpoint and writes
MARCXML files to the run directory. This is the **only** stage in the
repository that touches the network; the rest are purely local.

```mermaid
sequenceDiagram
    participant OAI as OAI-PMH server
    participant MS as melinda-sync
    participant FS as Run directory

    MS->>OAI: ListRecords (resumptionToken)
    OAI-->>MS: XML chunk (MARCXML)
    MS->>FS: Write MARCXML (.tmp → rename)
    MS->>FS: Update resumption token

    Note over MS,FS: Idempotent: .tmp → rename,<br/>resumption-token state survives restarts
```

**Key properties:** atomic writes (`.tmp` → rename), resumable via token state,
the only stage with retry logic for transient network errors.

### 2. `marc-to-bibframe` — MARCXML → BIBFRAME

Runs the LoC marc2bibframe2 XSLT against each MARCXML record. The XSLT is
vendored under `third_party/marc2bibframe2/` and must not be modified —
wrap, don't fork.

```mermaid
flowchart TD
    MARC[Source MARCXML] --> XSLT[marc2bibframe2 XSLT]
    XSLT --> BF[BIBFRAME RDF graph]
    XSLT -->|stderr| ERR[Xsltproc diagnostics]
```

**Key properties:** deterministic (same MARCXML → same BIBFRAME), failures
raise (no silent fallbacks), coverage documented in
`docs/marc_to_bibframe_mapping.md`.

### 3. `bibframe-to-bffi` — BIBFRAME → BFFI canonical Turtle

The heart of the forward direction. 31 discriminator routings in
`routings.py` walk the BIBFRAME graph, classify each entity, and emit
**only** `bffi:` URIs. The `bffi:` namespace is closed — zero `bf:*` URIs
may appear in the output graph.

```mermaid
flowchart TD
    subgraph Input["BIBFRAME input"]
        BF[BIBFRAME RDF graph<br/>with bf:* predicates]
    end

    subgraph Routings["31 routings"]
        R1[Discriminator routing<br/>e.g. bf:Hub → Work vs Expression]
        R2[Identifier routing<br/>e.g. ISBN → bffi:Identifier]
        R3[FRBR axis routing]
        Rn[... + 28 more]
    end

    subgraph Prov["Mandatory provenance"]
        P[Provenance graph<br/>bffi-prov:Activity per decision]
    end

    subgraph Output["BFFI canonical"]
        BFFI[BFFI Turtle<br/>bffi:* only]
    end

    BF --> R1
    BF --> R2
    BF --> R3
    BF --> Rn
    R1 --> P
    R2 --> P
    R3 --> P
    Rn --> P
    R1 --> BFFI
    R2 --> BFFI
    R3 --> BFFI
    Rn --> BFFI
```

**Key properties:** every non-trivial decision writes to the provenance graph
before returning; `bffi:` namespace discipline is enforced at emit time; the
mapping reference is generated from `vocab/lkd.rdf` and lives in
`docs/bf_to_bffi_mapping.md`.

### 4. `bffi-to-marc` — BFFI → MARCXML (reverse direction)

Reconstructs MARCXML from the canonical BFFI graph for round-trip
verification and downstream MARC consumers. This is the inverse of hop 2 + 3
combined.

```mermaid
flowchart TD
    BFFI[BFFI canonical Turtle] --> REV[Reverse-routing table]
    REV --> MARC[MARCXML]
    REV -->|not round-trippable| NOTES[Known limitations<br/>placeholder leader, 300 first-extent-wins,<br/>HELMET-local 09X loss, …]
```

**Key properties:** uses **only** the `bffi:` namespace — never reads
`bffi-prov:` for content decisions; see `docs/bffi_to_marc_mapping.md`
"Known limitations" for the full list of cases where reconstructed MARC
differs from source.

### 5. `roundtrip-eval` — Diff + Cataloguer review

Compares source MARCXML against the reconstructed MARCXML and produces:

- A structured diff report (per-record status: `identical` / `reordered` /
  `changed` / `lost` / `added`).
- A cataloguer-review HTML page for manual inspection of differences.

```mermaid
flowchart LR
    SRC[Source MARCXML] --> DIFF[diff comparator]
    RECON[Reconstructed MARCXML] --> DIFF
    DIFF --> STATUS[Per-record status]
    DIFF --> HTML[Cataloguer-review HTML]
```

## Run directory layout

Every pipeline invocation writes into a canonical run directory minted with
`bffi-pipeline new-run`:

```
runs/
└── yyyymmdd-hhmm-<6hex>/
    ├── marc/                  # Input MARCXML (from melinda-sync or manual copy)
    ├── bibframe/              # marc-to-bibframe output
    ├── bffi/                  # bibframe-to-bffi output (canonical Turtle)
    ├── marc-reconstructed/    # bffi-to-marc output
    ├── eval/                  # roundtrip-eval output (diff + HTML)
    ├── stage-events.jsonl     # Observability sidecar (every stage writes here)
    └── .exporter.pid          # (only when serve-metrics is attached)
```

The CLI validates every stage's `--output-dir` against this convention —
non-canonical paths exit with `error: --output-dir: …` before the stage
starts.

## Observability stack

```mermaid
flowchart TD
    STAGES[Pipeline stages] -->|JSONL events| SIDECAR[stage-events.jsonl<br/>per-run sidecar]
    SIDECAR -->|tail| EXPORTER[metrics-exporter<br/>host :9100]
    EXPORTER --> PROM[Prometheus<br/>Docker container]
    PROM --> GRAF[Grafana<br/>Docker container]
    PROM --> CADDY[Caddy<br/>Docker container]
    GRAF --> CADDY
    CADDY -->|:8080| OP[Operator browser]
    CADDY -->|/files/| SIDECAR
```

All local. The exporter tails sidecar JSONL files and serves Prometheus
metrics on `:9100`; Prometheus, Grafana, and Caddy run as Docker Compose
services behind `http://localhost:8080`. No outbound telemetry — no data
leaves the operator's machine.

See `docs/observability.md` for the full event schema, metric vocabulary,
and dashboard panel descriptions.

## Diagnostics & maintenance

```mermaid
flowchart LR
    subgraph MappingDocs["Mapping documentation"]
        M2B_DOC[marc_to_bibframe_mapping.md]
        B2BF_DOC[bf_to_biffi_mapping.md]
        BF2M_DOC[bffi_to_marc_mapping.md]
    end

    subgraph Tools["Diagnostics tools"]
        RMT[regenerate-mapping-tables]
        RMTB[regenerate-marc-to-bibframe-mapping]
        RMBF[regenerate-bffi-to-marc-mapping]
        DM[diagnose-mappings]
        DMC[diagnose-marc-coverage]
    end

    subgraph Sources["Sources"]
        XSLT[third_party/marc2bibframe2/<br/>XSLT stylesheets]
        LKD[vocab/lkd.rdf<br/>BFFI ontology]
        CORPUS[Corpus MARCXML]
    end

    XSLT --> RMTB
    RMTB --> M2B_DOC
    LKD --> RMT
    RMT --> B2BF_DOC
    LKD --> RMBF
    RMBF --> BF2M_DOC
    CORPUS --> DMC
    LKD --> DM
```

These tools regenerate the mapping reference docs when source material
changes (XSLT updates, ontology additions, submodule bumps). The `--check`
flag catches drift without writing.

## FRBR model

The pipeline operates on the FRBR entity model — works, expressions, and
manifestations — which drives the discriminator routing in hop 3.

The `bffi-to-marc` reverse stage walks these relationships in the opposite
direction to reconstruct MARC fields. See `docs/roundtrip-debugging.md` for
the catalogue of failure patterns when a field goes missing, wrong, or
retagged in the reconstructed output.

```mermaid
classDiagram
    class Work {
        +URI work:
        +bffi:mainTitle
        +bffi:hasExpression
    }
    class Expression {
        +URI expression:
        +bffi:manifestationOfExpression
        +bffi:format
    }
    class Manifestation {
        +URI manifestation:
        +bffi:carrier
        +bffi:extent
    }

    Work --> Expression
    Expression --> Manifestation
```
