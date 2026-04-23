#!/usr/bin/env bash
set -euo pipefail

echo "MisakaAssetGene setup (M0 scaffold)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "1. Create virtual environment"
if [ ! -f ".venv/bin/python" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

echo "2. Install Python package"
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .

echo "3. Install frontend dependencies"
npm install

echo "4. Verify manifests"
.venv/bin/python scripts/doctor.py

echo "5. Ensure Rust desktop toolchain is installed"
.venv/bin/python scripts/ensure_desktop_toolchain.py

echo "6. Ensure local Ollama is installed"
.venv/bin/python scripts/ensure_local_llm.py

echo "7. Setup complete"
