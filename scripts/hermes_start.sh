#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
HERMES_DIR="$PROJECT_DIR/hermes_bot"
BRIDGE_DIR="$PROJECT_DIR/components/wa_bridge"

echo "=== Mosaic — WhatsApp Personal Assistant ==="
echo ""

# Build Go bridge if needed
if [ ! -f "$BRIDGE_DIR/wa-bridge" ]; then
    echo "[setup] Building Go bridge..."
    cd "$BRIDGE_DIR"
    GOTOOLCHAIN=go1.25.0 go build -o wa-bridge .
    cd "$PROJECT_DIR"
    echo "[setup] Bridge built."
fi

# Ensure store directory exists (local dev uses hermes_bot/store)
mkdir -p "$HERMES_DIR/store"
export STORE_DIR="${STORE_DIR:-$HERMES_DIR/store}"

# Launch Mosaic
cd "$PROJECT_DIR"
exec python3 -m hermes_bot.main