# Getting Started

## Prerequisites

- **Python 3.14+** (managed via [`uv`](https://docs.astral.sh/uv/))
- **`xsltproc`** — required for MARCXML → BIBFRAME conversion via marc2bibframe2
- **Git** with submodule support (the marc2bibframe2 XSLT is a git submodule)

### Install system dependencies

```bash
# Debian/Ubuntu
sudo apt-get update && sudo apt-get install -y xsltproc

# macOS (Homebrew)
brew install libxslt
```

## Clone & setup

```bash
git clone --recursive https://github.com/mikkovihonen/bffi-conversion-pipeline.git
cd bffi-conversion-pipeline
uv sync --frozen
```

The `--recursive` flag initializes the `third_party/marc2bibframe2` submodule. If you cloned without it:

```bash
git submodule update --init --recursive
```

## Run the pipeline

### 1. Create a run directory

```bash
bffi-pipeline new-run
# → runs/20260805-1630-a1b2c3/
```

The run directory is the atomic output boundary for one pipeline invocation.

### 2. Convert MARCXML → BIBFRAME

```bash
bffi-pipeline marc-to-bibframe \
  --input /path/to/records.xml \
  --output-dir runs/20260805-1630-a1b2c3
```

### 3. Convert BIBFRAME → BFFI

```bash
bffi-pipeline bibframe-to-bffi \
  --input runs/.../bibframe.ntriples \
  --output-dir runs/...
```

### 4. Convert BFFI → MARCXML (round-trip)

```bash
bffi-pipeline bffi-to-marc \
  --input runs/.../bffi.ntriples \
  --output-dir runs/...
```

### 5. Evaluate round-trip

```bash
bffi-pipeline roundtrip-eval \
  --original /path/to/records.xml \
  --reconstructed runs/.../marc-reconstructed.xml \
  --html runs/.../roundtrip.html
```

## CLI reference

All subcommands accept `--help` for full option listings:

```bash
bffi-pipeline --help
bffi-pipeline marc-to-bibframe --help
bffi-pipeline bibframe-to-bffi --help
bffi-pipeline bffi-to-marc --help
bffi-pipeline roundtrip-eval --help
bffi-pipeline melinda-sync --help
bffi-pipeline new-run --help
```

## Configuration

The pipeline reads from environment variables and optional config files:

| Variable | Purpose | Default |
|----------|---------|---------|
| `BFFI_INPUT_DIR` | MARCXML input directory | `./input` |
| `BFFI_OUTPUT_DIR` | Conversion output directory | `./runs/default` |
| `BFFI_LOG_LEVEL` | Logging verbosity | `INFO` |