$ErrorActionPreference = "Stop"

Write-Host "MisakaAssetGene setup (M0 scaffold)" -ForegroundColor Cyan

$pythonCommand = if (Get-Command py -ErrorAction SilentlyContinue) { "py -3.11" } elseif (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { throw "Python 3.11 is required." }

Write-Host "1. Create virtual environment"
if (!(Test-Path ".\.venv\Scripts\python.exe")) {
  Invoke-Expression "$pythonCommand -m venv .venv"
}

Write-Host "2. Install Python package"
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e .

Write-Host "3. Install frontend dependencies"
npm install

Write-Host "4. Verify manifests"
& .\.venv\Scripts\python.exe scripts\doctor.py

Write-Host "5. Ensure Rust desktop toolchain is installed"
& .\.venv\Scripts\python.exe scripts\ensure_desktop_toolchain.py

Write-Host "6. Ensure local Ollama is installed"
& .\.venv\Scripts\python.exe scripts\ensure_local_llm.py

Write-Host "7. Setup complete"
