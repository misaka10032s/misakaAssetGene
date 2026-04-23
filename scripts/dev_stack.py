from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from core.config import get_settings
from core.llm.local_manager import LocalLlmManager

REPO_ROOT = Path(__file__).resolve().parents[1]
settings = get_settings()
local_llm_manager = LocalLlmManager()


def build_core_command() -> list[str]:
    python_executable = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    if python_executable.exists():
        python_path = str(python_executable)
    else:
        python_path = sys.executable
    return [
        python_path,
        "-m",
        "uvicorn",
        "core.main:app",
        "--reload",
        "--host",
        settings.misaka_api_host,
        "--port",
        str(settings.misaka_api_port),
    ]


def build_frontend_command() -> list[str]:
    npm_command = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm_command:
        raise FileNotFoundError("npm executable was not found in PATH.")
    return [
        npm_command,
        "run",
        "dev",
        "--",
        "--host",
        "127.0.0.1",
        "--port",
        str(settings.misaka_frontend_port),
    ]


def build_ollama_command() -> list[str] | None:
    should_start_ollama = settings.misaka_auto_start_ollama or "ollama" in settings.llm_provider_order
    if not should_start_ollama:
        return None
    try:
        ollama_path = local_llm_manager.install()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return [ollama_path, "serve"]


def spawn_process(command: list[str], environment: dict[str, str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        env=environment,
        text=True,
    )


def main() -> int:
    environment = os.environ.copy()
    environment["MISAKA_ENV"] = environment.get("MISAKA_ENV", "dev")
    environment["VITE_MISAKA_ENV"] = environment.get("VITE_MISAKA_ENV", "dev")
    environment["VITE_MISAKA_API_BASE"] = environment.get(
        "VITE_MISAKA_API_BASE",
        f"http://{settings.misaka_api_host}:{settings.misaka_api_port}",
    )

    managed_processes: list[subprocess.Popen[str]] = []
    try:
        print(
            f"[dev-stack] core=http://{settings.misaka_api_host}:{settings.misaka_api_port} "
            f"frontend=http://127.0.0.1:{settings.misaka_frontend_port}"
        )
        print(f"[dev-stack] model search paths={settings.model_search_paths}")

        ollama_status = local_llm_manager.status(settings)
        ollama_command = build_ollama_command()
        if bool(ollama_status["is_running"]):
            print("[dev-stack] ollama service already running")
        elif ollama_command is not None:
            managed_processes.append(spawn_process(ollama_command, environment))
            print("[dev-stack] ollama service started")
            time.sleep(2)
        else:
            print("[dev-stack] ollama auto-start skipped")

        managed_processes.append(spawn_process(build_core_command(), environment))
        managed_processes.append(spawn_process(build_frontend_command(), environment))

        while True:
            for managed_process in managed_processes:
                exit_code = managed_process.poll()
                if exit_code is not None:
                    print(f"[dev-stack] child process exited with code={exit_code}")
                    return exit_code
            time.sleep(1)
    except KeyboardInterrupt:
        print("[dev-stack] stopping services")
        return 0
    finally:
        for managed_process in managed_processes:
            if managed_process.poll() is None:
                if os.name == "nt":
                    managed_process.terminate()
                else:
                    managed_process.send_signal(signal.SIGINT)
        for managed_process in managed_processes:
            if managed_process.poll() is None:
                try:
                    managed_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    managed_process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
