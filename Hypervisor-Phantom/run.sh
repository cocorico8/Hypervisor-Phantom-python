#!/usr/bin/env bash

# Exit immediately if any command fails
set -e

# --- Configuration ---
VENV_DIR=".venv"
REQUIREMENTS_FILE="requirements.txt"
MAIN_SCRIPT="auto_hypervisor.py"

# --- Prerequisite Check ---
if ! command -v python3 &> /dev/null; then
    echo "❌ ERROR: python3 is not installed or not in your PATH. Please install Python 3."
    exit 1
fi

# --- First-Time Setup Logic ---
if [ ! -d "$VENV_DIR" ]; then
    echo "--- First-time setup detected. Preparing environment... ---"

    if ! command -v uv &> /dev/null; then
        echo "ℹ️  'uv' not found. Installing it globally via pip..."
        python3 -m pip install uv
        echo "✅ 'uv' installed. You may need to open a new terminal for the 'uv' command to be available everywhere."
    fi

    echo "ℹ️  Creating Python virtual environment in './$VENV_DIR'..."
    uv venv
    echo "✅ Virtual environment created."

    echo "ℹ️  Installing required Python libraries from '$REQUIREMENTS_FILE'..."

    # Call 'uv' directly. It will automatically find and use the .venv in this directory.
    uv pip install -r "$REQUIREMENTS_FILE"
    echo "✅ Libraries installed."
    echo "--- Setup complete! ---"
    echo ""
fi

# --- Application Launch ---
echo "🚀 Launching Hypervisor Phantom..."
echo ""

# Execute the main Python script using the Python interpreter from our virtual environment.
./$VENV_DIR/bin/python "$MAIN_SCRIPT" "$@"