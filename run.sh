#!/usr/bin/env bash
# Demo run: sets up observability and runs the full BFFI conversion pipeline
# on MARCXML data from a directory given as the first argument.
#
# Usage:
#   ./run.sh /path/to/marcxml/
#
# Prerequisites:
#   - Python 3.14+ with uv installed
#   - xsltproc (for MARCXML → BIBFRAME conversion)
#   - Docker or Podman (for the observability stack)
#   - The marc2bibframe2 submodule initialised (git submodule update --init --recursive)
#   - Dependencies installed: uv sync --frozen
#
# What it does:
#   1. Validates inputs and waits for xsltproc / Docker availability.
#   2. Starts the observability stack (Prometheus + Grafana + Caddy + metrics exporter).
#   3. Mints a new run directory, copies the MARCXML input there.
#   4. Runs the full forward + reverse pipeline:
#        marc-to-bibframe → bibframe-to-bffi → bffi-to-marc → roundtrip-eval
#   5. Prints a summary and the dashboard URL.
#
# The observability stack is torn down on Ctrl-C but left running afterwards
# so you can browse the dashboard at http://localhost:8080/.
# Stop it manually with: make observability-down

set -euo pipefail

# Activate the project venv so `bffi-pipeline` is on PATH.
[ -f .venv/bin/activate ] && source .venv/bin/activate || { echo -e "${RED}error: .venv not found. Run 'uv sync --frozen' first.${RESET}"; exit 1; }

# ── colours / icons ────────────────────────────────────────────────────────
RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[0;33m'
CYAN=$'\033[0;36m'
BOLD=$'\033[1m'
RESET=$'\033[0m'

# ── argument handling ──────────────────────────────────────────────────────
if [[ $# -lt 1 ]]; then
    echo -e "${RED}error: missing argument${RESET}"
    echo "Usage: $0 /path/to/marcxml/"
    exit 1
fi

INPUT_DIR="$1"

if [[ ! -d "$INPUT_DIR" ]]; then
    echo -e "${RED}error: input directory does not exist: $INPUT_DIR${RESET}"
    exit 1
fi

# Count MARCXML files
XML_COUNT=$(find "$INPUT_DIR" -maxdepth 1 -name '*.xml' -type f | wc -l)
if [[ "$XML_COUNT" -eq 0 ]]; then
    echo -e "${RED}error: no .xml files found in $INPUT_DIR${RESET}"
    exit 1
fi

echo -e "${CYAN}══════════════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}  BFFI Conversion Pipeline — Demo Run${RESET}"
echo -e "${CYAN}══════════════════════════════════════════════════════════${RESET}"
echo ""
echo -e "  Input:     ${BOLD}$INPUT_DIR${RESET} ($XML_COUNT MARCXML file(s))"
echo -e "  Pipeline:  MARCXML → BIBFRAME → BFFI → MARCXML → Round-trip eval"
echo -e "  Output:    ./runs/<timestamp>-<hex>/"
echo ""

# ── prerequisite checks ────────────────────────────────────────────────────
echo -e "${CYAN}── Checking prerequisites ────────────────────────────────────────────${RESET}"

# xsltproc
if ! command -v xsltproc &>/dev/null; then
    echo -e "${RED}error: xsltproc not found. Install it:${RESET}"
    echo "  sudo apt-get update && sudo apt-get install -y xsltproc"
    exit 1
fi
echo -e "  ${GREEN}✓${RESET} xsltproc"

# uv
if ! command -v uv &>/dev/null; then
    echo -e "${RED}error: uv not found. Install from https://docs.astral.sh/uv/${RESET}"
    exit 1
fi
echo -e "  ${GREEN}✓${RESET} uv"

# Docker or Podman
DOCKER_CMD=""
if command -v docker &>/dev/null; then
    DOCKER_CMD=docker
elif command -v podman &>/dev/null; then
    DOCKER_CMD=podman
else
    echo -e "${RED}error: neither docker nor podman found in PATH${RESET}"
    exit 1
fi
echo -e "  ${GREEN}✓${RESET} container runtime ($DOCKER_CMD)"

# Submodule
if [[ ! -d "third_party/marc2bibframe2" ]]; then
    echo -e "${RED}error: marc2bibframe2 submodule not initialised. Run:${RESET}"
    echo "  git submodule update --init --recursive"
    exit 1
fi
echo -e "  ${GREEN}✓${RESET} marc2bibframe2 submodule"

# Dependencies installed
if [[ ! -f "uv.lock" ]]; then
    echo -e "${RED}error: uv.lock not found. Run: uv sync --frozen${RESET}"
    exit 1
fi
echo -e "  ${GREEN}✓${RESET} dependencies installed"
echo ""

# ── start observability stack ──────────────────────────────────────────────
echo -e "${CYAN}── Starting observability stack ──────────────────────────────────────${RESET}"

# Start Docker Compose services (Prometheus + Grafana + Caddy)
$DOCKER_CMD compose --profile observability up -d prometheus grafana caddy

# Start the host-side metrics exporter in the background
mkdir -p runs
nohup env BFFI_OBSERVABILITY_SIDECAR=none \
    uv run bffi-pipeline serve-metrics \
        --port 9100 \
        --watch-glob 'runs/*/stage-events.jsonl' \
    > /tmp/bffi-exporter.log 2>&1 &
EXPORTER_PID=$!

# Give services time to start
echo -e "  Waiting for services to become ready…"
for i in $(seq 1 30); do
    if curl -fsS http://localhost:8080/prometheus/-/healthy &>/dev/null; then
        break
    fi
    sleep 1
done

echo -e "  ${GREEN}✓${RESET} Prometheus + Grafana + Caddy at http://localhost:8080"
echo -e "  ${GREEN}✓${RESET} Metrics exporter (PID $EXPORTER_PID)"
echo ""

# ── mint run directory ─────────────────────────────────────────────────────
echo -e "${CYAN}── Setting up run directory ──────────────────────────────────────────${RESET}"

RUN_DIR=$(bffi-pipeline new-run)
echo "  Run: $RUN_DIR"

# Copy MARCXML into the run directory
mkdir -p "$RUN_DIR/marc"
cp "$INPUT_DIR"/*.xml "$RUN_DIR/marc/"
XML_COPIED=$(find "$RUN_DIR/marc" -maxdepth 1 -name '*.xml' | wc -l)
echo -e "  ${GREEN}✓${RESET} $XML_COPIED MARCXML file(s) copied to $RUN_DIR/marc"
echo ""

# ── stage 1: MARCXML → BIBFRAME ────────────────────────────────────────────
echo -e "${CYAN}── Stage 1/4: MARCXML → BIBFRAME ─────────────────────────────────────${RESET}"
bffi-pipeline marc-to-bibframe \
    --input-dir "$RUN_DIR/marc" \
    --output-dir "$RUN_DIR/bibframe" 2>&1 | tee "$RUN_DIR/marc-to-bibframe.log"
echo ""

# ── stage 2: BIBFRAME → BFFI ───────────────────────────────────────────────
echo -e "${CYAN}── Stage 2/4: BIBFRAME → BFFI ────────────────────────────────────────${RESET}"
bffi-pipeline bibframe-to-bffi \
    --input-dir "$RUN_DIR/bibframe" \
    --output-dir "$RUN_DIR/bffi" 2>&1 | tee "$RUN_DIR/bibframe-to-bffi.log"
echo ""

# ── stage 3: BFFI → MARCXML (reverse) ─────────────────────────────────────
echo -e "${CYAN}── Stage 3/4: BFFI → MARCXML (reverse) ───────────────────────────────${RESET}"
bffi-pipeline bffi-to-marc \
    --input-dir "$RUN_DIR/bffi" \
    --output-dir "$RUN_DIR/marc-reconstructed" 2>&1 | tee "$RUN_DIR/bffi-to-marc.log"
echo ""

# ── stage 4: round-trip evaluation ─────────────────────────────────────────
echo -e "${CYAN}── Stage 4/4: Round-trip evaluation ───────────────────────────────────${RESET}"
bffi-pipeline roundtrip-eval \
    --source-dir "$RUN_DIR/marc" \
    --reconstructed-dir "$RUN_DIR/marc-reconstructed" \
    --html "$RUN_DIR/eval/review.html" 2>&1 | tee "$RUN_DIR/roundtrip-eval.log"
echo ""

# ── summary ─────────────────────────────────────────────────────────────────
echo -e "${CYAN}══════════════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}  Demo run complete${RESET}"
echo -e "${CYAN}══════════════════════════════════════════════════════════${RESET}"
echo ""
echo -e "  Dashboard: ${BOLD}http://localhost:8080${RESET}"
echo -e "  Run dir:   ${BOLD}$RUN_DIR${RESET}"
echo ""
echo -e "  Per-record review HTML: ${BOLD}$RUN_DIR/eval/review.html${RESET}"
echo ""
echo -e "  Stage logs:"
for log in marc-to-bibframe.log bibframe-to-bffi.log bffi-to-marc.log roundtrip-eval.log; do
    if [[ -f "$RUN_DIR/$log" ]]; then
        echo -e "    $RUN_DIR/$log"
    fi
done
echo ""
echo -e "  ${YELLOW}Press Ctrl-C to stop the pipeline stages. The observability stack${RESET}"
echo -e "  ${YELLOW}(Prometheus + Grafana + exporter) will keep running so you can${RESET}"
echo -e "  ${YELLOW}browse http://localhost:8080 until you run:${RESET}"
echo ""
echo -e "    ${BOLD}make observability-down${RESET}"
echo ""

# ── trap: stop pipeline stages on Ctrl-C, leave observability running ──────
cleanup() {
    echo -e "\n${YELLOW}Ctrl-C received. Pipeline stages stopped.${RESET}"
    echo -e "${YELLOW}Observability stack still running at http://localhost:8080${RESET}"
    echo -e "${YELLOW}Stop it with: make observability-down${RESET}"
}
trap cleanup INT TERM

# Wait for all background jobs (the pipeline stages are already done,
# this just keeps the script alive until the user interrupts)
wait
