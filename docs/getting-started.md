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
RUN=$(bffi-pipeline new-run)
echo "$RUN"
# → runs/20260805-1630-a1b2c3/
```

The run directory is the atomic output boundary for one pipeline invocation.

### 2. Stage MARCXML input

```bash
mkdir -p "$RUN/marc"
cp /path/to/records.xml "$RUN/marc/"
```

### 3. Convert MARCXML → BIBFRAME

```bash
bffi-pipeline marc-to-bibframe \
  --input-dir "$RUN/marc" \
  --output-dir "$RUN/bibframe"
```

### 4. Convert BIBFRAME → BFFI

```bash
bffi-pipeline bibframe-to-bffi \
  --input-dir "$RUN/bibframe" \
  --output-dir "$RUN/bffi"
```

### 5. Convert BFFI → MARCXML (round-trip)

```bash
bffi-pipeline bffi-to-marc \
  --input-dir "$RUN/bffi" \
  --output-dir "$RUN/marc-reconstructed"
```

### 6. Evaluate round-trip

```bash
bffi-pipeline roundtrip-eval \
  --source-dir "$RUN/marc" \
  --reconstructed-dir "$RUN/marc-reconstructed" \
  --html "$RUN/eval/review.html"
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

