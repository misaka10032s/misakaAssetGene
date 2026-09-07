from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urljoin

import httpx

from core.config import Settings
from core.network.safe_url import MAX_REDIRECT_HOPS, UnsafeUrlError, validate_download_url

# Redirect status codes httpx would otherwise follow automatically; handled
# manually here so every hop can be re-validated (待回答 #48).
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})


class LocalLlmManager:
    def __init__(self) -> None:
        self._managed_process: subprocess.Popen[str] | None = None

    def _ollama_executable(self) -> str | None:
        known_candidates = [
            shutil.which("ollama"),
            shutil.which("ollama.exe"),
            str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"),
            str(Path("C:\\Program Files\\Ollama\\ollama.exe")),
        ]
        for candidate in known_candidates:
            if candidate and Path(candidate).exists():
                return candidate
        return None

    def install(self) -> str:
        executable_path = self._ollama_executable()
        if executable_path is not None:
            return executable_path

        if os.name == "nt":
            winget_path = shutil.which("winget")
            if winget_path is None:
                raise FileNotFoundError("winget was not found, so Ollama cannot be installed automatically.")
            subprocess.run(
                [
                    winget_path,
                    "install",
                    "--id",
                    "Ollama.Ollama",
                    "--silent",
                    "--accept-package-agreements",
                    "--accept-source-agreements",
                ],
                check=True,
            )
        else:
            brew_path = shutil.which("brew")
            if brew_path is not None:
                subprocess.run([brew_path, "install", "ollama"], check=True)
            else:
                install_script = "curl -fsSL https://ollama.com/install.sh | sh"
                subprocess.run(install_script, check=True, shell=True)

        executable_path = self._ollama_executable()
        if executable_path is None:
            raise FileNotFoundError("Ollama installation completed but the executable could not be found.")
        return executable_path

    def _is_server_running(self, settings: Settings) -> bool:
        try:
            response = httpx.get(f"{settings.misaka_ollama_base_url.rstrip('/')}/api/tags", timeout=2.0)
            return response.is_success
        except httpx.HTTPError:
            return False

    def status(self, settings: Settings) -> dict[str, object]:
        process_running = self._managed_process is not None and self._managed_process.poll() is None
        server_running = self._is_server_running(settings)
        executable_path = self._ollama_executable()
        return {
            "server": "ollama",
            "base_url": settings.misaka_ollama_base_url,
            "is_running": server_running,
            "managed_by_app": process_running,
            "executable_found": executable_path is not None,
            "executable_path": executable_path,
            "provider_order": settings.llm_provider_order,
        }

    def start(self, settings: Settings) -> dict[str, object]:
        status = self.status(settings)
        if bool(status["is_running"]):
            return status

        executable_path = self._ollama_executable()
        if executable_path is None:
            executable_path = self.install()

        self._managed_process = subprocess.Popen(
            [executable_path, "serve"],
            text=True,
        )
        return self.status(settings)

    def ensure_started(self, settings: Settings) -> dict[str, object]:
        if "ollama" not in settings.llm_provider_order and not settings.misaka_auto_start_ollama:
            return self.status(settings)
        try:
            return self.start(settings)
        except (FileNotFoundError, subprocess.CalledProcessError):
            return self.status(settings)

    def download_model(self, settings: Settings, url: str) -> dict[str, object]:
        normalized_url = url.strip().replace("/blob/", "/resolve/")

        # Step 1/2 of the SSRF validator (scheme+host allow-list, then DNS
        # resolution vs. private/loopback/reserved ranges) — 待回答 #48. A
        # bare substring check on "huggingface.co" used to accept
        # "https://evil.example/?u=huggingface.co" and
        # "https://huggingface.co.evil.example/"; this validator rejects both.
        try:
            parsed_url = validate_download_url(normalized_url)
        except UnsafeUrlError as error:
            raise ValueError(str(error)) from error

        filename = Path(parsed_url.path).name
        if not re.search(r"\.[a-zA-Z0-9]{2,10}$", filename):
            raise ValueError("The Hugging Face URL must point to a concrete file.")

        target_root = Path(settings.model_search_paths[0])
        target_root.mkdir(parents=True, exist_ok=True)
        target_path = target_root / filename

        # Step 3: manual redirect following, `follow_redirects=False` — every
        # hop's Location target is re-validated (full steps 1/2) BEFORE it is
        # requested, so an allowed host cannot bounce the fetch to a private
        # address via a redirect (the other half of the SSRF the automatic
        # `follow_redirects=True` previously allowed).
        current_url = normalized_url
        for hop in range(MAX_REDIRECT_HOPS + 1):
            with httpx.stream("GET", current_url, follow_redirects=False, timeout=60.0) as response:
                if response.status_code in _REDIRECT_STATUS_CODES:
                    if hop == MAX_REDIRECT_HOPS:
                        raise ValueError(
                            f"Exceeded the maximum of {MAX_REDIRECT_HOPS} redirects while "
                            f"downloading {normalized_url}."
                        )
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError(f"Redirect from {current_url} carried no Location header.")
                    next_url = urljoin(current_url, location)
                    try:
                        validate_download_url(next_url)
                    except UnsafeUrlError as error:
                        raise ValueError(f"Redirect target rejected: {error}") from error
                    current_url = next_url
                    continue

                response.raise_for_status()
                with target_path.open("wb") as output_handle:
                    for chunk in response.iter_bytes():
                        output_handle.write(chunk)
                return {
                    "filename": filename,
                    "saved_path": str(target_path),
                    "source_url": normalized_url,
                }

        # Unreachable: the loop above always either returns or raises on the
        # final iteration (hop == MAX_REDIRECT_HOPS).
        raise ValueError(f"Exceeded the maximum of {MAX_REDIRECT_HOPS} redirects while downloading {normalized_url}.")
