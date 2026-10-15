#!/bin/bash

# wa-slash-commands | One-Line Installer
# Target: macOS (Homebrew) and Linux (apt)

set -e

# ANSI Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

echo -e "${BOLD}${CYAN}💬 Welcome to the wa-slash-commands Installer${NC}"
echo -e "──────────────────────────────────────────"

# 1. Detect OS
OS_TYPE=$(uname -s)
PACKAGER=""

if [ "$OS_TYPE" == "Darwin" ]; then
    echo -e "→ Detected macOS"
    if ! command -v brew &> /dev/null; then
        echo -e "→ Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    fi
    PACKAGER="brew"
elif [ "$OS_TYPE" == "Linux" ]; then
    echo -e "→ Detected Linux"
    if command -v apt-get &> /dev/null; then
        PACKAGER="apt"
    else
        echo -e "${RED}✗ Error: This script currently only supports 'apt' based Linux distributions.${NC}"
        exit 1
    fi
else
    echo -e "${RED}✗ Error: Unsupported OS: $OS_TYPE${NC}"
    exit 1
fi

# 2. Install Dependencies
echo -e "→ Checking dependencies..."

install_dep() {
    local name=$1
    local cmd=$2
    if ! command -v "$cmd" &> /dev/null; then
        echo -e "  → Installing $name..."
        if [ "$PACKAGER" == "brew" ]; then
            brew install "$name"
        else
            sudo apt-get update && sudo apt-get install -y "$name"
        fi
    fi
}

install_dep "git" "git"
install_dep "go" "go"
install_dep "python3" "python3"

# 3. Clone Repository
INSTALL_DIR="$HOME/wa-slash-commands"
if [ -d "$INSTALL_DIR" ]; then
    echo -e "→ Repository already exists at $INSTALL_DIR. Updating..."
    cd "$INSTALL_DIR"
    git pull origin master
else
    echo -e "→ Cloning repository to $INSTALL_DIR..."
    git clone https://github.com/your-username/wa-slash-commands.git "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# 4. Install Python Requirements
echo -e "→ Installing Python requirements..."
pip3 install -q -r requirements.txt

echo -e "──────────────────────────────────────────"
echo -e "${GREEN}✅ Environment Ready!${NC}"
echo -e "Starting the setup wizard..."
echo ""

# 5. Run Setup
python3 setup.py
