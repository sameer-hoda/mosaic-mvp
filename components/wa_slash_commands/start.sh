#!/usr/bin/env bash
# Helper script to run the WhatsApp bridge from the correct directory

# Get the directory where this script is located
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

if [ ! -f "bridge/wa-bridge" ]; then
    echo "❌ Error: Bridge binary not found. Please run 'python3 setup.py' first."
    exit 1
fi

if [ ! -d "store" ]; then
    echo "⚠️ Warning: 'store' directory not found. You might need to authenticate."
fi

echo "🚀 Starting WhatsApp Slash Command Bridge..."
./bridge/wa-bridge
