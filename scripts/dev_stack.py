from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
import threading
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from core.config import get_settings
from core.integration.workers import WorkersService
from core.llm.local_manager import LocalLlmManager

REPO_ROOT = Path(__file__).resolve().parents[1]
settings = get_settings()
local_llm_manager = LocalLlmManager()
workers_service = WorkersService(REPO_ROOT / "workers" / "manifest.json")


def build_core_command() -> list[str]:
    python_executable = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    if python_executable.exists():
        python_path = str(python_executable)
    else:
        python_path = sys.executable
    command = [
        python_path,
        "-m",
        "uvicorn",
        "core.main:app",
        "--host",
        settings.misaka_api_host,
        "--port",
        str(settings.misaka_api_port),
    ]
    if os.environ.get("MISAKA_CORE_RELOAD", "").lower() in {"1", "true", "yes"}:
        command.append("--reload")
    return command


def build_frontend_command() -> list[str]:
    node_command = shutil.which("node.exe") or shutil.which("node")
    if not node_command:
        raise FileNotFoundError("node executable was not found in PATH.")
    vite_cli = REPO_ROOT / "node_modules" / "vite" / "bin" / "vite.js"
    if not vite_cli.exists():
        raise FileNotFoundError(f"Vite CLI was not found at {vite_cli}. Run npm install first.")
    return [
        node_command,
        str(vite_cli),
        "--mode",
        "development",
        "--config",
        "frontend/vite.config.ts",
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
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        creationflags=creationflags,
    )


def wait_for_http_ready(url: str, process: subprocess.Popen[str], label: str, timeout_seconds: int = 60) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            raise RuntimeError(f"{label} exited before becoming ready (code={exit_code}).")
        try:
            with urlopen(url, timeout=2):
                return
        except URLError:
            time.sleep(1)
    raise TimeoutError(f"{label} did not become ready within {timeout_seconds} seconds: {url}")


def start_workers_in_background() -> None:
    try:
        worker_snapshots = workers_service.start_installed_workers()
        started_workers = [worker.display_name for worker in worker_snapshots if worker.is_running]
        if started_workers:
            print(f"[dev-stack] started workers: {', '.join(started_workers)}")
        else:
            print("[dev-stack] no installed workers were started")
    except Exception as exc:
        print(f"[dev-stack] worker auto-start failed: {exc}")


def main() -> int:
    environment = os.environ.copy()
    environment["MISAKA_ENV"] = environment.get("MISAKA_ENV", "dev")
    environment["VITE_MISAKA_ENV"] = environment.get("VITE_MISAKA_ENV", "dev")
    environment["VITE_MISAKA_API_BASE"] = environment.get(
        "VITE_MISAKA_API_BASE",
        f"http://{settings.misaka_api_host}:{settings.misaka_api_port}",
    )

    managed_processes: list[tuple[str, subprocess.Popen[str], list[str]]] = []
    shutdown_requested = threading.Event()

    last_sigint_at = 0.0

    def request_shutdown(signum: int, _frame: object) -> None:
        nonlocal last_sigint_at
        try:
            signal_name = signal.Signals(signum).name
        except ValueError:
            signal_name = str(signum)
        if shutdown_requested.is_set():
            return
        if signum == signal.SIGINT:
            now = time.monotonic()
            if now - last_sigint_at <= 2.0:
                print("[dev-stack] received shutdown signal: SIGINT")
                shutdown_requested.set()
                return
            last_sigint_at = now
            print("[dev-stack] ignoring first SIGINT; press Ctrl+C again within 2 seconds to stop")
            return
        print(f"[dev-stack] received shutdown signal: {signal_name}")
        shutdown_requested.set()

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, request_shutdown)

    def wait_for_core_and_workers(core_process: subprocess.Popen[str]) -> None:
        try:
            wait_for_http_ready(
                f"http://{settings.misaka_api_host}:{settings.misaka_api_port}/healthz",
                core_process,
                "core",
                timeout_seconds=90,
            )
            print("[dev-stack] core ready")
            if environment.get("MISAKA_AUTO_START_WORKERS", "true").lower() not in {"0", "false", "no"}:
                start_workers_in_background()
        except Exception as exc:
            print(f"[dev-stack] core readiness failed: {exc}")
            shutdown_requested.set()

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
            ollama_process = spawn_process(ollama_command, environment)
            managed_processes.append(("ollama", ollama_process, ollama_command))
            print(f"[dev-stack] spawned ollama pid={ollama_process.pid}")
            print("[dev-stack] ollama service started")
            time.sleep(2)
        else:
            print("[dev-stack] ollama auto-start skipped")

        core_process = spawn_process(build_core_command(), environment)
        managed_processes.append(("core", core_process, build_core_command()))
        print(f"[dev-stack] spawned core pid={core_process.pid}")

        frontend_process = spawn_process(build_frontend_command(), environment)
        managed_processes.append(("frontend", frontend_process, build_frontend_command()))
        print(f"[dev-stack] spawned frontend pid={frontend_process.pid}")
        threading.Thread(
            target=wait_for_core_and_workers,
            args=(core_process,),
            name="misaka-core-ready",
            daemon=True,
        ).start()

        while not shutdown_requested.is_set():
            for label, managed_process, command in managed_processes:
                exit_code = managed_process.poll()
                if exit_code is not None:
                    print(
                        f"[dev-stack] child process exited: label={label} pid={managed_process.pid} "
                        f"code={exit_code} command={' '.join(command)}"
                    )
                    return exit_code
            time.sleep(1)
        print("[dev-stack] stopping services")
        return 0
    except Exception as exc:
        print(f"[dev-stack] fatal error: {exc}")
        return 1
    finally:
        for _, managed_process, _ in managed_processes:
            if managed_process.poll() is None:
                if os.name == "nt":
                    managed_process.terminate()
                else:
                    managed_process.send_signal(signal.SIGINT)
        for _, managed_process, _ in managed_processes:
            if managed_process.poll() is None:
                try:
                    managed_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    managed_process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
