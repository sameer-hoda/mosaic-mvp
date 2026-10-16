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
    GOTOOLCHAIN=go1.26.0 go build -o wa-bridge .
    cd "$PROJECT_DIR"
    echo "[setup] Bridge built."
fi

# Ensure store directory exists (local dev uses hermes_bot/store)
mkdir -p "$HERMES_DIR/store"
export STORE_DIR="${STORE_DIR:-$HERMES_DIR/store}"
# mosaic.db lives here locally (container default /data untouched).
export MOSAIC_DATA_DIR="${MOSAIC_DATA_DIR:-$HERMES_DIR/store}"
# Bridge send API fails closed without a token: mint an ephemeral one per
# boot that both the bridge (child process) and the sender inherit.
if [ -z "$MOSAIC_BRIDGE_TOKEN" ]; then
    MOSAIC_BRIDGE_TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(16))")
    export MOSAIC_BRIDGE_TOKEN
fi
# Scan the live bridge store (read-only; refusal of other paths kept).
# Still dry-run without MOSAIC_LIVE=1.
export MOSAIC_SNAPSHOT_PATH="${MOSAIC_SNAPSHOT_PATH:-$STORE_DIR/messages.db}"
export MOSAIC_CYCLE="${MOSAIC_CYCLE:-1}"
export MOSAIC_CYCLE_INTERVAL_S="${MOSAIC_CYCLE_INTERVAL_S:-300}"

# Brain event hook: macOS owns :7000 (AirPlay), so pick a free loopback
# port and point the bridge at it (Railway path untouched).
if [ -z "$BRAIN_PORT" ]; then
    BRAIN_PORT=$(python3 -c "import socket; s=socket.socket(); s.bind(('127.0.0.1',0)); print(s.getsockname()[1]); s.close()")
    export BRAIN_PORT
fi
export BRAIN_EVENT_URL="${BRAIN_EVENT_URL:-http://127.0.0.1:$BRAIN_PORT/event}"

# Launch Mosaic
cd "$PROJECT_DIR"
exec python3 -m hermes_bot.main