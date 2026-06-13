#Requires -Version 5.1
<#
.SYNOPSIS
    MisakaAssetGene setup script (Portable-Release + Dev-Mode, Windows).
    Spec ref: §11.1–11.3  |  M4.e: uv-bootstrap
    Tauri cross-platform bundler: DEFERRED (to a later milestone pass).

.DESCRIPTION
    7 setup stages (§11.2):
      [1/7] Detect OS / hardware
      [2/7] Download uv binary (if absent)
      [3/7] Provision Python runtime via uv
      [4/7] Create virtual environment via uv
      [5/7] Install core dependencies via uv
      [6/7] Ensure ffmpeg binary
      [7/7] Initialize data folders

    Dev-Mode vs Portable-Release:
      - If uv already exists on PATH (dev clone) or in tools/bin/, it is reused.
      - If .venv already fully provisioned, steps 3/4/5 are skipped.
      - The script never re-downloads what is already present and intact.

    Error handling follows §11.3:
      - Known errors from the whitelist → friendly zh-TW message printed.
      - Unknown errors → full traceback written to setup.log; console shows
        one-line summary + [y/n/s] prompt; user may ask AI to explain.

    Security: API keys are NEVER echoed to the console.  setup.log is written
    by Python (scripts/lib/setup_diagnostics.py) which redacts key-like strings.
#>

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"   # speed up Invoke-WebRequest

Set-StrictMode -Version Latest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
$REPO_ROOT   = $PSScriptRoot | Split-Path -Parent
$TOOLS_BIN   = Join-Path $REPO_ROOT "tools" "bin"
$VENV_DIR    = Join-Path $REPO_ROOT ".venv"
$VENV_PYTHON = Join-Path $VENV_DIR "Scripts" "python.exe"
$SETUP_LOG   = Join-Path $REPO_ROOT "setup.log"

# ---------------------------------------------------------------------------
# uv binary location (search order: PATH → tools/bin)
# ---------------------------------------------------------------------------
function Find-Uv {
    $onPath = Get-Command uv -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    $inTools = Join-Path $TOOLS_BIN "uv.exe"
    if (Test-Path $inTools) { return $inTools }
    return $null
}

# ---------------------------------------------------------------------------
# uv download (Portable-Release path)
# uv publishes pre-built binaries at:
#   https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip
# ---------------------------------------------------------------------------
$UV_VERSION = "0.5.31"   # pinned; bump deliberately with changelog entry
$UV_ASSET   = "uv-x86_64-pc-windows-msvc.zip"
$UV_URL     = "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$UV_ASSET"

function Download-Uv {
    param([string]$DestDir)
    New-Item -ItemType Directory -Force -Path $DestDir | Out-Null
    $zipPath = Join-Path $env:TEMP "misaka_uv_download.zip"
    Write-Host "   Downloading uv $UV_VERSION from GitHub..." -ForegroundColor DarkGray
    try {
        Invoke-WebRequest -Uri $UV_URL -OutFile $zipPath -UseBasicParsing
    } catch {
        throw "Download failed: $_"
    }
    Expand-Archive -Path $zipPath -DestinationPath $DestDir -Force
    Remove-Item $zipPath -ErrorAction SilentlyContinue
    # uv.exe lands directly in $DestDir after extraction
    $uvExe = Join-Path $DestDir "uv.exe"
    if (-not (Test-Path $uvExe)) {
        # Some releases nest one level: uv-x86_64-pc-windows-msvc/uv.exe
        $nested = Get-ChildItem -Path $DestDir -Filter "uv.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($nested) {
            Move-Item $nested.FullName $uvExe
        } else {
            throw "uv.exe not found in extracted archive."
        }
    }
    return $uvExe
}

# ---------------------------------------------------------------------------
# Stage helpers
# ---------------------------------------------------------------------------
$STAGE_TOTAL = 7

function Write-Stage {
    param([int]$N, [string]$Label)
    Write-Host ("[{0}/{1}] {2}" -f $N, $STAGE_TOTAL, $Label) -ForegroundColor Cyan
}

function Write-StageOk {
    param([string]$Detail = "")
    if ($Detail) {
        Write-Host ("   OK: {0}" -f $Detail) -ForegroundColor Green
    } else {
        Write-Host "   OK" -ForegroundColor Green
    }
}

function Write-StageSkip {
    param([string]$Reason)
    Write-Host ("   SKIP: {0}" -f $Reason) -ForegroundColor DarkGray
}

function Invoke-PythonDiag {
    param(
        [int]$StageIndex,
        [string]$ErrorText,
        [string]$RawException
    )
    # Try to run the Python diagnostics module if venv already exists;
    # otherwise fall back to simple PowerShell classification.
    if (Test-Path $VENV_PYTHON) {
        $diagScript = @"
import sys, os
sys.path.insert(0, r'$REPO_ROOT')
from scripts.lib.setup_diagnostics import classify_error, write_to_log, build_console_summary, KNOWN_ERRORS
from pathlib import Path
root = Path(r'$REPO_ROOT')
error_text = r'$ErrorText'
result = classify_error(error_text)
if result.matched:
    print('KNOWN:' + result.key)
    print(result.friendly_message)
    if result.remediation_cmd:
        print('CMD:' + result.remediation_cmd)
else:
    log = write_to_log('[$StageIndex/$STAGE_TOTAL]', error_text, None, root)
    print('UNKNOWN')
    print(build_console_summary($StageIndex, $STAGE_TOTAL, error_text, log))
"@
        & $VENV_PYTHON -c $diagScript
    } else {
        # Venv not yet available — minimal fallback classification.
        # Redact API-key-shaped strings (≥20 alphanumeric chars, or sk-/AIza/Bearer prefix)
        # before any text is written to setup.log or printed to console.
        $redacted = [regex]::Replace(
            $ErrorText,
            '(?:(?:sk-|AIza|AIZA|Bearer\s+)[A-Za-z0-9_\-]{10,}|[A-Za-z0-9][A-Za-z0-9+/=_\-]{19,})',
            '[REDACTED]'
        )
        Write-Host ("⚠ 步驟 [{0}/{1}] 發生錯誤" -f $StageIndex, $STAGE_TOTAL) -ForegroundColor Yellow
        Write-Host ("   摘要: {0}" -f $redacted)
        Write-Host ("   完整記錄已寫入 {0}" -f $SETUP_LOG)
        Add-Content -Path $SETUP_LOG -Value ("[{0}] Stage [{1}/{2}] error: {3}" -f (Get-Date -Format "o"), $StageIndex, $STAGE_TOTAL, $redacted)
    }
}

# ---------------------------------------------------------------------------
# [1/7] Detect OS / hardware
# ---------------------------------------------------------------------------
Write-Stage 1 "偵測作業系統與硬體..."

$osInfo   = [System.Environment]::OSVersion.VersionString
$arch     = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture

# GPU detection (best-effort; non-fatal)
$gpuInfo = "unknown"
try {
    $gpu = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue |
           Select-Object -First 1 -ExpandProperty Name
    if ($gpu) { $gpuInfo = $gpu }
} catch { }

Write-StageOk ("{0} ({1})  GPU: {2}" -f $osInfo, $arch, $gpuInfo)

# ---------------------------------------------------------------------------
# [2/7] Download uv (if absent)
# ---------------------------------------------------------------------------
Write-Stage 2 "下載 uv (18 MB)..."

$uvBin = Find-Uv
if ($uvBin) {
    Write-StageSkip "已找到 uv: $uvBin"
} else {
    try {
        $uvBin = Download-Uv -DestDir $TOOLS_BIN
        Write-StageOk "uv $UV_VERSION → $uvBin"
    } catch {
        Invoke-PythonDiag -StageIndex 2 -ErrorText $_.Exception.Message -RawException $_
        exit 1
    }
}

# ---------------------------------------------------------------------------
# [3/7] Provision Python runtime via uv
# ---------------------------------------------------------------------------
Write-Stage 3 "下載 Python runtime..."

$PYTHON_VERSION = "3.11"

# Check if python is already available inside venv (dev mode shortcut)
if (Test-Path $VENV_PYTHON) {
    Write-StageSkip ".venv 已存在，跳過 Python 下載"
} else {
    try {
        & $uvBin python install $PYTHON_VERSION 2>&1 | ForEach-Object { Write-Host "   $_" -ForegroundColor DarkGray }
        Write-StageOk "CPython $PYTHON_VERSION (managed by uv)"
    } catch {
        Invoke-PythonDiag -StageIndex 3 -ErrorText $_.Exception.Message -RawException $_
        exit 1
    }
}

# ---------------------------------------------------------------------------
# [4/7] Create virtual environment via uv
# ---------------------------------------------------------------------------
Write-Stage 4 "建立虛擬環境..."

if (Test-Path $VENV_PYTHON) {
    Write-StageSkip ".venv 已存在"
} else {
    try {
        & $uvBin venv $VENV_DIR --python $PYTHON_VERSION 2>&1 | ForEach-Object { Write-Host "   $_" -ForegroundColor DarkGray }
        Write-StageOk ".venv → $VENV_DIR"
    } catch {
        Invoke-PythonDiag -StageIndex 4 -ErrorText $_.Exception.Message -RawException $_
        exit 1
    }
}

# ---------------------------------------------------------------------------
# [5/7] Install core dependencies via uv
# ---------------------------------------------------------------------------
Write-Stage 5 "安裝核心依賴..."

try {
    & $uvBin pip install --python $VENV_PYTHON -e (Join-Path $REPO_ROOT ".") 2>&1 |
        ForEach-Object { Write-Host "   $_" -ForegroundColor DarkGray }
    Write-StageOk "dependencies installed"
} catch {
    Invoke-PythonDiag -StageIndex 5 -ErrorText $_.Exception.Message -RawException $_
    exit 1
}

# ---------------------------------------------------------------------------
# [6/7] Ensure ffmpeg
# ---------------------------------------------------------------------------
Write-Stage 6 "下載 ffmpeg (50 MB)..."

$ffmpegBin = Join-Path $TOOLS_BIN "ffmpeg.exe"
if (Test-Path $ffmpegBin) {
    Write-StageSkip "ffmpeg 已存在: $ffmpegBin"
} elseif (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Write-StageSkip "ffmpeg 在 PATH 中"
} else {
    try {
        & $VENV_PYTHON (Join-Path $PSScriptRoot "ensure_desktop_toolchain.py") 2>&1 |
            ForEach-Object { Write-Host "   $_" -ForegroundColor DarkGray }
        Write-StageOk "ffmpeg provisioned"
    } catch {
        # Non-fatal: warn and continue
        Write-Host ("   ⚠ ffmpeg 安裝失敗（非致命）: {0}" -f $_.Exception.Message) -ForegroundColor Yellow
        Write-Host "   部分影片/音訊功能將不可用。請手動安裝 ffmpeg 並加入 PATH。"
    }
}

# ---------------------------------------------------------------------------
# [7/7] Initialize data folders
# ---------------------------------------------------------------------------
Write-Stage 7 "初始化資料夾結構..."

try {
    $foldersScript = @"
import sys, os
sys.path.insert(0, r'$REPO_ROOT')
from pathlib import Path
root = Path(r'$REPO_ROOT')
for d in ['projects', 'logs', 'tmp']:
    (root / d).mkdir(parents=True, exist_ok=True)
print('folders OK')
"@
    & $VENV_PYTHON -c $foldersScript
    Write-StageOk "projects/ logs/ tmp/"
} catch {
    Invoke-PythonDiag -StageIndex 7 -ErrorText $_.Exception.Message -RawException $_
    exit 1
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Setup 完成！" -ForegroundColor Green
Write-Host "啟動：.venv\Scripts\python.exe -m uvicorn core.main:app --host 127.0.0.1 --port 8401"
Write-Host ""
Write-Host "注意：Tauri 跨平台封裝（.msi / .dmg / .AppImage）已延後至後續里程碑實作。"
