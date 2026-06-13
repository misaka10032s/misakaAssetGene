#!/usr/bin/env bash
# MisakaAssetGene setup script (Portable-Release + Dev-Mode, macOS / Linux).
# Spec ref: §11.1–11.3  |  M4.e: uv-bootstrap
# Tauri cross-platform bundler: DEFERRED (to a later milestone pass).
#
# 7 setup stages (§11.2):
#   [1/7] Detect OS / hardware
#   [2/7] Download uv binary (if absent)
#   [3/7] Provision Python runtime via uv
#   [4/7] Create virtual environment via uv
#   [5/7] Install core dependencies via uv
#   [6/7] Ensure ffmpeg
#   [7/7] Initialize data folders
#
# Dev-Mode: if uv is already on PATH or in tools/bin/, it is reused.
#           if .venv/bin/python already exists, steps 3/4/5 are skipped.
#
# Error handling (§11.3):
#   - Known errors → friendly zh-TW message.
#   - Unknown errors → traceback to setup.log; console shows [y/n/s] prompt.
#
# Security: API keys are never echoed.  setup.log is written by Python
#           (scripts/lib/setup_diagnostics.py) which redacts key-like strings.
set -euo pipefail

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TOOLS_BIN="$REPO_ROOT/tools/bin"
VENV_DIR="$REPO_ROOT/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
SETUP_LOG="$REPO_ROOT/setup.log"
STAGE_TOTAL=7

UV_VERSION="0.5.31"   # pinned; bump deliberately with changelog entry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_cyan()  { printf '\033[0;36m%s\033[0m\n' "$*"; }
_green() { printf '\033[0;32m%s\033[0m\n' "$*"; }
_gray()  { printf '\033[0;37m%s\033[0m\n' "$*"; }
_yellow(){ printf '\033[0;33m%s\033[0m\n' "$*"; }

write_stage() {
    local n=$1; local label=$2
    _cyan "[$n/$STAGE_TOTAL] $label"
}
write_ok()   { _green "   OK${1:+: $1}"; }
write_skip() { _gray  "   SKIP: $1"; }

# ---------------------------------------------------------------------------
# uv binary location (search order: PATH → tools/bin)
# ---------------------------------------------------------------------------
find_uv() {
    if command -v uv &>/dev/null; then
        command -v uv
        return
    fi
    if [ -x "$TOOLS_BIN/uv" ]; then
        echo "$TOOLS_BIN/uv"
        return
    fi
    echo ""
}

# ---------------------------------------------------------------------------
# uv download (Portable-Release path)
# uv publishes pre-built tarballs at:
#   https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-gnu.tar.gz
# ---------------------------------------------------------------------------
download_uv() {
    local dest_dir="$1"
    mkdir -p "$dest_dir"

    local os_name arch_name
    os_name="$(uname -s | tr '[:upper:]' '[:lower:]')"
    arch_name="$(uname -m)"

    # Map to uv asset naming
    case "$os_name" in
        darwin)
            case "$arch_name" in
                arm64)  uv_asset="uv-aarch64-apple-darwin.tar.gz" ;;
                x86_64) uv_asset="uv-x86_64-apple-darwin.tar.gz" ;;
                *)      echo "Unsupported macOS arch: $arch_name" >&2; return 1 ;;
            esac ;;
        linux)
            case "$arch_name" in
                x86_64|amd64) uv_asset="uv-x86_64-unknown-linux-gnu.tar.gz" ;;
                aarch64|arm64) uv_asset="uv-aarch64-unknown-linux-gnu.tar.gz" ;;
                *)      echo "Unsupported Linux arch: $arch_name" >&2; return 1 ;;
            esac ;;
        *)
            echo "Unsupported OS: $os_name" >&2; return 1 ;;
    esac

    local uv_url="https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$uv_asset"
    local tmp_tar="/tmp/misaka_uv_download.tar.gz"

    _gray "   Downloading uv $UV_VERSION from GitHub..."
    if command -v curl &>/dev/null; then
        curl -fsSL -o "$tmp_tar" "$uv_url"
    elif command -v wget &>/dev/null; then
        wget -q -O "$tmp_tar" "$uv_url"
    else
        echo "Neither curl nor wget found. Cannot download uv." >&2
        return 1
    fi

    tar -xzf "$tmp_tar" -C "$dest_dir" --strip-components=1 2>/dev/null || \
        tar -xzf "$tmp_tar" -C "$dest_dir"  # fallback without strip
    rm -f "$tmp_tar"

    local uv_bin="$dest_dir/uv"
    if [ ! -x "$uv_bin" ]; then
        # Some releases use a nested dir
        local found
        found="$(find "$dest_dir" -name "uv" -type f 2>/dev/null | head -1)"
        if [ -n "$found" ]; then
            mv "$found" "$uv_bin"
        else
            echo "uv binary not found in extracted archive." >&2
            return 1
        fi
    fi
    chmod +x "$uv_bin"
    echo "$uv_bin"
}

# ---------------------------------------------------------------------------
# Python diagnostics fallback (when venv not yet available)
# ---------------------------------------------------------------------------
python_diag() {
    local stage_index=$1
    local error_text=$2

    if [ -x "$VENV_PYTHON" ]; then
        "$VENV_PYTHON" - <<PYEOF
import sys
sys.path.insert(0, '$REPO_ROOT')
from scripts.lib.setup_diagnostics import classify_error, write_to_log, build_console_summary
from pathlib import Path
root = Path('$REPO_ROOT')
error_text = '''$error_text'''
result = classify_error(error_text)
if result.matched:
    print('KNOWN:', result.key)
    print(result.friendly_message)
    if result.remediation_cmd:
        print('CMD:', result.remediation_cmd)
else:
    log = write_to_log('[$stage_index/$STAGE_TOTAL]', error_text, None, root)
    print(build_console_summary($stage_index, $STAGE_TOTAL, error_text, log))
PYEOF
    else
        # Venv not yet available — redact API-key-shaped strings inline before
        # writing to setup.log or printing to console (security: no raw token exposure).
        redacted_text="$(printf '%s' "$error_text" | \
            sed 's/\(sk-\|AIza\|AIZA\|Bearer \)[A-Za-z0-9_-]\{10,\}/[REDACTED]/g; \
                 s/[A-Za-z0-9][A-Za-z0-9+\/=_-]\{19,\}/[REDACTED]/g')"
        _yellow "⚠ 步驟 [$stage_index/$STAGE_TOTAL] 發生錯誤"
        echo "   摘要: $redacted_text"
        echo "   完整記錄已寫入 $SETUP_LOG"
        printf '[%s] Stage [%d/%d] error: %s\n' \
            "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$stage_index" "$STAGE_TOTAL" "$redacted_text" \
            >> "$SETUP_LOG"
    fi
}

# ---------------------------------------------------------------------------
# [1/7] Detect OS / hardware
# ---------------------------------------------------------------------------
write_stage 1 "偵測作業系統與硬體..."

OS_NAME="$(uname -s)"
ARCH_NAME="$(uname -m)"
GPU_INFO="unknown"

# GPU detection (best-effort; non-fatal)
if command -v nvidia-smi &>/dev/null 2>&1; then
    GPU_INFO="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo 'NVIDIA (details unavailable)')"
elif [[ "$OS_NAME" == "Darwin" ]]; then
    GPU_INFO="Apple Silicon / integrated"
fi

write_ok "$OS_NAME $ARCH_NAME  GPU: $GPU_INFO"

# ---------------------------------------------------------------------------
# [2/7] Download uv (if absent)
# ---------------------------------------------------------------------------
write_stage 2 "下載 uv (18 MB)..."

UV_BIN="$(find_uv)"
if [ -n "$UV_BIN" ]; then
    write_skip "已找到 uv: $UV_BIN"
else
    if UV_BIN="$(download_uv "$TOOLS_BIN")"; then
        write_ok "uv $UV_VERSION → $UV_BIN"
    else
        python_diag 2 "Failed to download uv binary"
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# [3/7] Provision Python runtime via uv
# ---------------------------------------------------------------------------
write_stage 3 "下載 Python runtime..."
PYTHON_VERSION="3.11"

if [ -x "$VENV_PYTHON" ]; then
    write_skip ".venv 已存在，跳過 Python 下載"
else
    if "$UV_BIN" python install "$PYTHON_VERSION" 2>&1 | while IFS= read -r line; do _gray "   $line"; done; then
        write_ok "CPython $PYTHON_VERSION (managed by uv)"
    else
        python_diag 3 "uv python install $PYTHON_VERSION failed"
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# [4/7] Create virtual environment via uv
# ---------------------------------------------------------------------------
write_stage 4 "建立虛擬環境..."

if [ -x "$VENV_PYTHON" ]; then
    write_skip ".venv 已存在"
else
    if "$UV_BIN" venv "$VENV_DIR" --python "$PYTHON_VERSION" 2>&1 | while IFS= read -r line; do _gray "   $line"; done; then
        write_ok ".venv → $VENV_DIR"
    else
        python_diag 4 "uv venv creation failed"
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# [5/7] Install core dependencies via uv
# ---------------------------------------------------------------------------
write_stage 5 "安裝核心依賴..."

if "$UV_BIN" pip install --python "$VENV_PYTHON" -e "$REPO_ROOT" 2>&1 | while IFS= read -r line; do _gray "   $line"; done; then
    write_ok "dependencies installed"
else
    python_diag 5 "uv pip install failed"
    exit 1
fi

# ---------------------------------------------------------------------------
# [6/7] Ensure ffmpeg
# ---------------------------------------------------------------------------
write_stage 6 "下載 ffmpeg (50 MB)..."

if [ -x "$TOOLS_BIN/ffmpeg" ]; then
    write_skip "ffmpeg 已存在: $TOOLS_BIN/ffmpeg"
elif command -v ffmpeg &>/dev/null; then
    write_skip "ffmpeg 在 PATH 中"
else
    if "$VENV_PYTHON" "$SCRIPT_DIR/ensure_desktop_toolchain.py" 2>&1 | while IFS= read -r line; do _gray "   $line"; done; then
        write_ok "ffmpeg provisioned"
    else
        _yellow "   ⚠ ffmpeg 安裝失敗（非致命）"
        echo "   部分影片/音訊功能將不可用。請手動安裝 ffmpeg 並加入 PATH。"
    fi
fi

# ---------------------------------------------------------------------------
# [7/7] Initialize data folders
# ---------------------------------------------------------------------------
write_stage 7 "初始化資料夾結構..."

"$VENV_PYTHON" -c "
from pathlib import Path
root = Path('$REPO_ROOT')
for d in ['projects', 'logs', 'tmp']:
    (root / d).mkdir(parents=True, exist_ok=True)
print('folders OK')
"

write_ok "projects/ logs/ tmp/"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
_green "Setup 完成！"
echo "啟動：.venv/bin/python -m uvicorn core.main:app --host 127.0.0.1 --port 8401"
echo ""
echo "注意：Tauri 跨平台封裝（.msi / .dmg / .AppImage）已延後至後續里程碑實作。"
