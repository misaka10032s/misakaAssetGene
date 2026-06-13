"""Setup diagnostics — known-error whitelist, setup.log writer, console summary.

Spec ref: §11.3 (known-error whitelist + friendly messages; unknown errors →
setup.log + console summary + y/n/s prompt).

Security: API keys and local paths are NEVER written to setup.log or to the
console.  ``_redact`` scrubs key-like strings before any text hits disk or
stdout.

Design notes (recorded in .plan/RESEARCH_LOG.md §8):
- All text stored in setup.log is first passed through ``_redact`` to strip
  API-key-shaped tokens (≥20 chars of alphanumeric + special chars).
- The whitelist matches by substring / regex against the raw error string; only
  the *friendly* remediation message reaches the console.
- Unknown errors write the full traceback to setup.log (redacted) and print
  only a one-line summary to the console.
"""

from __future__ import annotations

import platform
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Secret-redaction: import from the shared app module so setup.log and the
# app-wide logging filter stay in sync.  If the core package is not on the
# Python path (e.g. early bootstrap before venv is ready), fall back to a
# minimal local implementation that covers the same patterns.
# ---------------------------------------------------------------------------
try:
    from core.logging_redaction import redact as _redact  # noqa: F401
    import re  # still needed for compile-time checks below
except ImportError:  # pragma: no cover — fallback for pre-venv bootstrap
    import re
    _SECRET_RE_FALLBACK = re.compile(
        r"""
        (?:
            (?:sk-ant-api03-|sk-proj-|sk-)[A-Za-z0-9_\-]{10,}
        |
            (?:AIza|AIZA)[A-Za-z0-9_\-]{10,}
        |
            Bearer\s+[A-Za-z0-9_\-\.]{10,}
        |
            [A-Za-z0-9][A-Za-z0-9+/=_\-\.]{19,}
        )
        """,
        re.VERBOSE,
    )

    def _redact(text: str) -> str:  # type: ignore[misc]
        return _SECRET_RE_FALLBACK.sub("[REDACTED]", text)


# ---------------------------------------------------------------------------
# Known-error whitelist
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class KnownError:
    """A single entry in the known-error whitelist."""

    key: str
    """Machine-readable identifier (e.g. ``network_timeout``)."""

    patterns: tuple[str, ...]
    """Substrings / regex fragments to match against the raw error string."""

    friendly_zh: str
    """Friendly remediation message shown on the console (zh-TW)."""

    remediation_cmd: Optional[str] = None
    """Optional shell command hint to show to the user."""


#: Whitelist — order matters: first match wins.
KNOWN_ERRORS: tuple[KnownError, ...] = (
    KnownError(
        key="network_timeout",
        patterns=(
            "timed out",
            "timeout",
            "connection timed out",
            "read timed out",
            "ConnectTimeout",
            "ReadTimeout",
            "urlopen error timed out",
        ),
        friendly_zh="網路不穩，按 Enter 重試；如需 proxy 請設定 HTTPS_PROXY 環境變數。",
        remediation_cmd="set HTTPS_PROXY=http://your-proxy:port (Windows) / export HTTPS_PROXY=… (Unix)",
    ),
    KnownError(
        key="network_unreachable",
        patterns=(
            "network is unreachable",
            "No route to host",
            "Name or service not known",
            "nodename nor servname provided",
            "getaddrinfo failed",
            "ConnectionRefusedError",
            "ConnectionError",
        ),
        friendly_zh="無法連線到網際網路。請確認網路連線後重試。",
    ),
    KnownError(
        key="disk_full",
        patterns=(
            "no space left on device",
            "disk full",
            "not enough space",
            "OSError: [Errno 28]",
            "WinError 112",
            "There is not enough space",
        ),
        friendly_zh="磁碟空間不足。安裝需要至少 15 GB；請清理磁碟後重試。",
    ),
    KnownError(
        key="cuda_missing",
        patterns=(
            "libcuda.so",
            "libcuda.so.1",
            "CUDA driver version is insufficient",
            "no CUDA-capable device",
            "NVIDIA-SMI has failed",
            "nvidia-smi",
            "cuda driver",
        ),
        friendly_zh=(
            "未偵測到 NVIDIA 驅動程式或 CUDA。\n"
            "   請至 https://www.nvidia.com/drivers 安裝最新顯示卡驅動（CUDA ≥ 12.1）。\n"
            "   若無 GPU，忽略此訊息——CPU 模式仍可運行。"
        ),
    ),
    KnownError(
        key="powershell_execution_policy",
        patterns=(
            "execution policy",
            "ExecutionPolicy",
            "running scripts is disabled",
            "UnauthorizedAccess",
            "File cannot be loaded",
        ),
        friendly_zh=(
            "PowerShell 執行策略阻止腳本執行。\n"
            "   請以管理員身份執行：Set-ExecutionPolicy RemoteSigned -Scope CurrentUser"
        ),
        remediation_cmd="Set-ExecutionPolicy RemoteSigned -Scope CurrentUser",
    ),
    KnownError(
        key="hash_mismatch",
        patterns=(
            "sha256",
            "hash mismatch",
            "checksum",
            "digest did not match",
            "integrity check failed",
        ),
        friendly_zh="下載檔案損毀（SHA256 不符），自動重試中；若連續失敗請檢查網路或代理設定。",
    ),
    KnownError(
        key="permission_denied",
        patterns=(
            "permission denied",
            "PermissionError",
            "access is denied",
            "WinError 5",
            "errno 13",
            "Operation not permitted",
        ),
        friendly_zh=(
            "權限不足，無法寫入檔案或目錄。\n"
            "   Windows：請以管理員身份重新執行 setup.ps1。\n"
            "   macOS/Linux：請以 sudo 或正確的使用者執行 setup.sh，或修正目錄擁有者。"
        ),
    ),
)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

@dataclass
class ClassifyResult:
    matched: bool
    key: Optional[str]
    friendly_message: Optional[str]
    remediation_cmd: Optional[str]


def classify_error(error_text: str) -> ClassifyResult:
    """Check ``error_text`` against the known-error whitelist.

    Returns a :class:`ClassifyResult` with ``matched=True`` and the friendly
    remediation message when a pattern hits; ``matched=False`` otherwise.
    """
    lower = error_text.lower()
    for entry in KNOWN_ERRORS:
        for pattern in entry.patterns:
            if pattern.lower() in lower:
                return ClassifyResult(
                    matched=True,
                    key=entry.key,
                    friendly_message=entry.friendly_zh,
                    remediation_cmd=entry.remediation_cmd,
                )
    return ClassifyResult(matched=False, key=None, friendly_message=None, remediation_cmd=None)


# ---------------------------------------------------------------------------
# setup.log writer
# ---------------------------------------------------------------------------

def _log_path(root: Optional[Path] = None) -> Path:
    base = root or Path.cwd()
    return base / "setup.log"


def _sysinfo() -> str:
    return (
        f"OS: {platform.system()} {platform.release()} {platform.machine()}\n"
        f"Python: {platform.python_version()}"
    )


def write_to_log(
    stage_label: str,
    error_text: str,
    exc: Optional[BaseException],
    root: Optional[Path] = None,
) -> Path:
    """Append a full error record to ``setup.log`` and return the log path.

    ``error_text`` and the traceback are redacted before writing (security).
    """
    log = _log_path(root)
    timestamp = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
    redacted_error = _redact(error_text)
    tb_text = ""
    if exc is not None:
        raw_tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        tb_text = _redact(raw_tb)

    lines = [
        f"\n{'=' * 60}",
        f"[{timestamp}] Stage: {stage_label}",
        f"Sysinfo: {_sysinfo()}",
        f"Error summary: {redacted_error}",
    ]
    if tb_text:
        lines.append("Traceback (redacted):")
        lines.append(tb_text)
    lines.append("=" * 60)

    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    return log


# ---------------------------------------------------------------------------
# Console summary builder (spec §11.3 layout)
# ---------------------------------------------------------------------------

def build_console_summary(
    stage_index: int,
    stage_total: int,
    one_line_summary: str,
    log_path: Path,
) -> str:
    """Return the console text for an unknown error (§11.3 layout).

    Does NOT include the y/n/s prompt — that is the caller's responsibility
    (interactive vs. non-interactive, testing).
    """
    redacted_summary = _redact(one_line_summary)
    return (
        f"⚠ 步驟 [{stage_index}/{stage_total}] 發生未知錯誤\n"
        f"   摘要: {redacted_summary}\n"
        f"   完整記錄已寫入 {log_path}\n"
        f"\n"
        f"   要讓 AI 解釋可能原因嗎？\n"
        f"   [y] 是（需設定 API key 或 Ollama）\n"
        f"   [n] 否\n"
        f"   [s] 跳過此步驟繼續"
    )
