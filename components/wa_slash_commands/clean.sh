#!/usr/bin/env bash
# Clean up script to return the project to a fresh, newly cloned state.

echo "🧹 Cleaning up wa-slash-commands sandbox..."

# Remove databases
if [ -d "store" ]; then
    rm -rf store
    echo "  Deleted 'store/' directory (databases)"
fi

# Remove environment file
if [ -f ".env" ]; then
    rm .env
    echo "  Deleted '.env' file"
fi

# Remove log file
if [ -f "slash_cmd.log" ]; then
    rm slash_cmd.log
    echo "  Deleted 'slash_cmd.log' file"
fi

# Remove python cache
find . -type d -name "__pycache__" -exec rm -rf {} +
echo "  Deleted '__pycache__/' directories"

# Remove compiled bridge binary
if [ -f "bridge/wa-bridge" ]; then
    rm bridge/wa-bridge
    echo "  Deleted compiled bridge binary 'bridge/wa-bridge'"
fi

# Kill any running bridge processes to ensure port 8080 is free
pkill -f "wa-bridge" || true

echo "✨ Project is now in a fresh state."
echo "   Run 'python3 setup.py' to begin the setup wizard."
