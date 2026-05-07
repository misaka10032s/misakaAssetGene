import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from core.models.schemas import WorkerSmokeResult, WorkerSnapshot
from core.scheduler.vram import RuntimeState


class WorkersService:
    def __init__(self, manifest_path: Path) -> None:
        self.manifest_path = manifest_path.resolve()
        self.workers_root = self.manifest_path.parent
        self.repo_root = self.manifest_path.parent.parent
        self.runtime_root = self.workers_root / ".runtime"
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self._snapshot_cache: tuple[float, list[WorkerSnapshot]] | None = None

    def list_workers(self, refresh: bool = False) -> list[WorkerSnapshot]:
        if not refresh and self._snapshot_cache is not None:
            cached_at, snapshots = self._snapshot_cache
            if time.monotonic() - cached_at <= 2.0:
                return snapshots
        manifest = self._load_manifest()
        snapshots: list[WorkerSnapshot] = []
        health_cache: dict[str, bool] = {}
        for name, worker in manifest.get("workers", {}).items():
            snapshots.append(self._build_snapshot(name, worker, health_cache))
        snapshots = sorted(snapshots, key=lambda snapshot: snapshot.name)
        self._snapshot_cache = (time.monotonic(), snapshots)
        return snapshots

    def get_worker(self, worker_name: str) -> WorkerSnapshot:
        manifest = self._load_manifest()
        worker = manifest.get("workers", {}).get(worker_name)
        if worker is None:
            raise KeyError(f"Unknown worker: {worker_name}")
        return self._build_snapshot(worker_name, worker, {})

    def worker_definition_count(self) -> int:
        manifest = self._load_manifest()
        return len(manifest.get("workers", {}))

    def get_worker_definition(self, worker_name: str) -> dict:
        manifest = self._load_manifest()
        worker = manifest.get("workers", {}).get(worker_name)
        if worker is None:
            raise KeyError(f"Unknown worker: {worker_name}")
        return worker

    def install_worker(self, worker_name: str) -> WorkerSnapshot:
        manifest = self._load_manifest()
        worker = manifest.get("workers", {}).get(worker_name)
        if worker is None:
            raise KeyError(f"Unknown worker: {worker_name}")

        clone_path = self._worker_clone_path(worker_name, worker)
        repo_url = str(worker.get("repo") or "").strip()
        if not repo_url:
            raise ValueError(f"Worker {worker_name} is missing a repository URL.")

        if clone_path.exists() and not (clone_path / ".git").exists():
            raise FileExistsError(f"{clone_path} exists but is not a git repository.")

        if not clone_path.exists():
            self._run_command("git", "clone", "--filter=blob:none", repo_url, str(clone_path))

        recommended_commit = str(worker.get("recommended", {}).get("commit") or "").strip()
        if recommended_commit:
            self._run_command("git", "-C", str(clone_path), "fetch", "--depth", "1", "origin", recommended_commit)
            self._run_command("git", "-C", str(clone_path), "checkout", recommended_commit)
        install_cmd = str(worker.get("install_cmd") or "").strip()
        try:
            self._ensure_worker_venv(worker, clone_path)
            if install_cmd:
                self._run_command(*self._normalize_install_command(install_cmd, clone_path), cwd=clone_path)
            self._save_install_state(
                worker_name,
                {
                    "status": "success",
                    "install_cmd": install_cmd,
                    "installed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            self._clear_runtime_artifacts(worker_name)
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            self._save_install_state(
                worker_name,
                {
                    "status": "failed",
                    "install_cmd": install_cmd,
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                    "error": str(exc),
                },
            )

        self._snapshot_cache = None
        return self._build_snapshot(worker_name, worker, {})

    def start_worker(self, worker_name: str) -> WorkerSnapshot:
        manifest = self._load_manifest()
        worker = manifest.get("workers", {}).get(worker_name)
        if worker is None:
            raise KeyError(f"Unknown worker: {worker_name}")
        snapshot = self._build_snapshot(worker_name, worker, {})
        if snapshot.is_running:
            return snapshot
        if not snapshot.is_installed:
            raise ValueError(f"{worker_name} is not installed yet.")
        if not self._has_install_state(worker_name):
            raise ValueError(f"{worker_name} dependencies are not installed yet. Run install first.")

        start_cmd = str(worker.get("start_cmd") or "").strip()
        if not start_cmd:
            raise ValueError(f"{worker_name} is missing a start command.")

        clone_path = self._worker_clone_path(worker_name, worker)
        log_path = self.runtime_root / f"{worker_name}.log"
        log_path.unlink(missing_ok=True)
        with log_path.open("a", encoding="utf-8") as log_handle:
            process = subprocess.Popen(
                self._normalize_start_command(worker, start_cmd, clone_path),
                cwd=clone_path,
                shell=False,
                stdout=log_handle,
                stderr=log_handle,
            )
        self._save_runtime_state(
            worker_name,
            {
                "pid": process.pid,
                "log_path": str(log_path),
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        for _ in range(30):
            time.sleep(1)
            snapshot = self._build_snapshot(worker_name, worker, {})
            if snapshot.is_running:
                break
            if process.poll() is not None:
                break
        self._snapshot_cache = None
        return self._build_snapshot(worker_name, worker, {})

    def stop_worker(self, worker_name: str) -> WorkerSnapshot:
        manifest = self._load_manifest()
        worker = manifest.get("workers", {}).get(worker_name)
        if worker is None:
            raise KeyError(f"Unknown worker: {worker_name}")
        runtime_state = self._load_runtime_state(worker_name)
        pid = int(runtime_state.get("pid") or 0)
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        self._clear_runtime_state(worker_name)
        self._snapshot_cache = None
        return self._build_snapshot(worker_name, worker, {})

    def smoke_test_worker(self, worker_name: str) -> WorkerSmokeResult:
        manifest = self._load_manifest()
        worker = manifest.get("workers", {}).get(worker_name)
        if worker is None:
            raise KeyError(f"Unknown worker: {worker_name}")
        smoke_script = str(worker.get("smoke_test") or "").strip()
        if smoke_script:
            return self._run_smoke_script(worker_name, worker, smoke_script)
        snapshot = self.get_worker(worker_name)
        detail = snapshot.readiness_note or "Worker is reachable."
        ok = snapshot.is_running and not snapshot.readiness_note
        return WorkerSmokeResult(
            worker_name=worker_name,
            ok=ok,
            detail=detail,
            checked_at=datetime.now(timezone.utc),
        )

    def readiness_note(self, worker_name: str) -> str | None:
        return self.get_worker(worker_name).readiness_note

    def start_installed_workers(self) -> list[WorkerSnapshot]:
        manifest = self._load_manifest()
        snapshots: list[WorkerSnapshot] = []
        for worker_name, worker in manifest.get("workers", {}).items():
            snapshot = self._build_snapshot(worker_name, worker, {})
            if not bool(worker.get("auto_start", True)):
                snapshots.append(snapshot)
                continue
            if snapshot.is_installed and not snapshot.is_running:
                try:
                    snapshot = self.start_worker(worker_name)
                except Exception:
                    snapshot = self._build_snapshot(worker_name, worker, {})
            snapshots.append(snapshot)
        return snapshots

    def _load_manifest(self) -> dict:
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def _build_snapshot(self, worker_name: str, worker: dict, health_cache: dict[str, bool]) -> WorkerSnapshot:
        recommended = worker.get("recommended", {})
        clone_path = self._worker_clone_path(worker_name, worker)
        installed_reference = self._resolve_installed_reference(clone_path)
        runtime_state = self._load_runtime_state(worker_name)
        managed_pid = self._resolve_managed_pid(runtime_state)
        health_check = worker.get("health_check")
        is_running = self._is_worker_running(health_check, health_cache)
        return WorkerSnapshot(
            name=worker_name,
            display_name=worker.get("display_name", worker_name),
            repo=worker.get("repo", ""),
            path=str(clone_path),
            recommended_reference=self._format_reference(recommended.get("tag"), recommended.get("commit")),
            installed_reference=installed_reference,
            health_check=health_check,
            is_installed=installed_reference is not None,
            is_running=is_running,
            managed_pid=managed_pid,
            vram_requirement_mb=int(worker.get("vram_requirement_mb") or 0),
            runtime_state=self._resolve_runtime_state(runtime_state, is_running),
            last_job_at=runtime_state.get("last_job_at"),
            readiness_note=self._build_readiness_note(worker_name, worker, clone_path, installed_reference, is_running, health_cache),
        )

    def _worker_clone_path(self, worker_name: str, worker: dict) -> Path:
        directory = str(worker.get("directory") or worker_name)
        return self.workers_root / directory

    def _resolve_installed_reference(self, clone_path: Path) -> str | None:
        if not (clone_path / ".git").exists():
            return None

        commit = self._run_git(clone_path, "rev-parse", "--short", "HEAD")
        tag = self._run_git(clone_path, "describe", "--tags", "--abbrev=0")
        return self._format_reference(tag, commit)

    def _run_git(self, clone_path: Path, *args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", "-C", str(clone_path), *args],
                capture_output=True,
                check=True,
                text=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            return None

        output = completed.stdout.strip()
        return output or None

    def _run_command(self, *args: str, cwd: Path | None = None) -> None:
        subprocess.run(
            [*args],
            cwd=cwd or self.repo_root,
            check=True,
            text=True,
        )

    def _is_worker_running(self, health_check: str | None, health_cache: dict[str, bool] | None = None) -> bool:
        if not health_check:
            return False
        if health_cache is not None and health_check in health_cache:
            return health_cache[health_check]
        try:
            response = httpx.get(health_check, timeout=0.75)
        except httpx.HTTPError:
            if health_cache is not None:
                health_cache[health_check] = False
            return False
        result = response.is_success
        if health_cache is not None:
            health_cache[health_check] = result
        return result

    def _format_reference(self, tag: str | None, commit: str | None) -> str:
        normalized_tag = (tag or "").strip()
        normalized_commit = (commit or "").strip()
        if normalized_tag and normalized_commit:
            return f"{normalized_tag} @ {normalized_commit}"
        if normalized_tag:
            return normalized_tag
        if normalized_commit:
            return normalized_commit
        return "n/a"

    def _runtime_state_path(self, worker_name: str) -> Path:
        return self.runtime_root / f"{worker_name}.json"

    def _load_runtime_state(self, worker_name: str) -> dict:
        path = self._runtime_state_path(worker_name)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _install_state_path(self, worker_name: str) -> Path:
        return self.runtime_root / f"{worker_name}.install.json"

    def _load_install_state(self, worker_name: str) -> dict:
        path = self._install_state_path(worker_name)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _save_install_state(self, worker_name: str, payload: dict) -> None:
        self._install_state_path(worker_name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _save_runtime_state(self, worker_name: str, payload: dict) -> None:
        self._runtime_state_path(worker_name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _clear_runtime_state(self, worker_name: str) -> None:
        self._runtime_state_path(worker_name).unlink(missing_ok=True)

    def _clear_runtime_artifacts(self, worker_name: str) -> None:
        self._clear_runtime_state(worker_name)
        (self.runtime_root / f"{worker_name}.log").unlink(missing_ok=True)

    def mark_worker_activity(self, worker_name: str, *, active: bool) -> None:
        runtime_state = self._load_runtime_state(worker_name)
        runtime_state["last_job_at"] = datetime.now(timezone.utc).isoformat()
        runtime_state["runtime_state"] = RuntimeState.ACTIVE.value if active else RuntimeState.WARM.value
        self._save_runtime_state(worker_name, runtime_state)
        self._snapshot_cache = None

    def _resolve_managed_pid(self, runtime_state: dict) -> int | None:
        pid = int(runtime_state.get("pid") or 0)
        if pid <= 0:
            return None
        try:
            os.kill(pid, 0)
        except OSError:
            return None
        return pid

    def _resolve_runtime_state(self, runtime_state: dict, is_running: bool) -> RuntimeState:
        if not is_running:
            return RuntimeState.COLD
        raw_state = str(runtime_state.get("runtime_state") or "").strip().lower()
        try:
            return RuntimeState(raw_state)
        except ValueError:
            return RuntimeState.ACTIVE

    def _build_readiness_note(
        self,
        worker_name: str,
        worker: dict,
        clone_path: Path,
        installed_reference: str | None,
        is_running: bool,
        health_cache: dict[str, bool],
    ) -> str | None:
        if installed_reference is None:
            return "Repository is not installed."
        install_state = self._load_install_state(worker_name)
        if install_state.get("status") == "failed":
            install_error = str(install_state.get("error") or "").strip()
            if install_error:
                return f"Dependency install failed: {install_error}"
            return "Dependency install failed."
        if not self._install_succeeded(install_state) and not is_running:
            return "Worker dependencies are not installed yet."
        if worker_name == "comfyui":
            checkpoints_dir = clone_path / "models" / "checkpoints"
            checkpoint_files = [
                item
                for item in checkpoints_dir.glob("*")
                if item.is_file() and item.name != "put_checkpoints_here"
            ]
            if not checkpoint_files:
                return "No ComfyUI checkpoint is installed."
        health_check = worker.get("health_check")
        if health_check and not is_running:
            startup_error = self._read_last_log_line(worker_name)
            if startup_error:
                return startup_error
            return "Worker server is not running."
        return None

    def _run_smoke_script(self, worker_name: str, worker: dict, smoke_script: str) -> WorkerSmokeResult:
        script_path = self.repo_root / smoke_script
        if not script_path.exists():
            return WorkerSmokeResult(
                worker_name=worker_name,
                ok=False,
                detail=f"Smoke script is missing: {smoke_script}",
                checked_at=datetime.now(timezone.utc),
            )
        clone_path = self._worker_clone_path(worker_name, worker)
        environment = os.environ.copy()
        environment["MISAKA_WORKER_NAME"] = worker_name
        environment["MISAKA_WORKER_DISPLAY_NAME"] = str(worker.get("display_name") or worker_name)
        environment["MISAKA_WORKER_PATH"] = str(clone_path)
        environment["MISAKA_WORKER_HEALTH_URL"] = str(worker.get("health_check") or "")
        environment["MISAKA_WORKER_CHECKPOINT_DIR"] = str(clone_path / "models" / "checkpoints")
        completed = subprocess.run(
            [self._python_executable(), str(script_path)],
            cwd=self.repo_root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        detail = completed.stdout.strip() or completed.stderr.strip() or "Smoke script returned no output."
        ok = completed.returncode == 0
        try:
            payload = json.loads(detail.splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            payload = None
        if isinstance(payload, dict):
            ok = bool(payload.get("ok", ok))
            detail = str(payload.get("detail") or detail)
        return WorkerSmokeResult(
            worker_name=worker_name,
            ok=ok,
            detail=detail,
            checked_at=datetime.now(timezone.utc),
        )

    def _python_executable(self) -> str:
        venv_python = self.repo_root / ".venv" / "Scripts" / "python.exe"
        if venv_python.exists():
            return str(venv_python)
        return sys.executable

    def _normalize_start_command(self, worker: dict, command: str, clone_path: Path) -> list[str]:
        tokens = shlex.split(command, posix=False)
        if not tokens:
            return tokens
        executable = tokens[0].lower()
        if executable in {"python", "python.exe", "py"}:
            tokens[0] = self._worker_python_executable(worker, clone_path)
        return tokens

    def _has_install_state(self, worker_name: str) -> bool:
        return self._install_succeeded(self._load_install_state(worker_name))

    def _install_succeeded(self, install_state: dict) -> bool:
        status = str(install_state.get("status") or "").strip().lower()
        if status == "success":
            return True
        return bool(install_state.get("installed_at"))

    def _read_last_log_line(self, worker_name: str) -> str | None:
        runtime_state = self._load_runtime_state(worker_name)
        log_path = Path(str(runtime_state.get("log_path") or self.runtime_root / f"{worker_name}.log"))
        if not log_path.is_absolute():
            log_path = self.repo_root / log_path
        if not log_path.exists():
            return None
        lines = [line.strip() for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip()]
        if not lines:
            return None
        return f"Startup failed: {lines[-1]}"

    def _ensure_worker_venv(self, worker: dict, clone_path: Path) -> None:
        worker_python = Path(self._worker_python_executable(worker, clone_path))
        if worker_python.exists() and self._worker_venv_matches_requested_python(worker, worker_python):
            return
        if self._worker_venv_path(clone_path).exists():
            shutil.rmtree(self._worker_venv_path(clone_path), ignore_errors=True)
        command = self._worker_venv_create_command(worker, clone_path)
        subprocess.run(
            command,
            cwd=clone_path,
            check=True,
            text=True,
        )

    def _worker_venv_path(self, clone_path: Path) -> Path:
        return clone_path / ".venv"

    def _worker_python_executable(self, worker: dict, clone_path: Path) -> str:
        return str(self._worker_venv_path(clone_path) / "Scripts" / "python.exe")

    def _normalize_install_command(self, command: str, clone_path: Path) -> list[str]:
        tokens = shlex.split(command, posix=False)
        if not tokens:
            return tokens
        executable = tokens[0].lower()
        worker_python = str(self._worker_venv_path(clone_path) / "Scripts" / "python.exe")
        if executable in {"python", "python.exe", "py"}:
            tokens[0] = worker_python
            return tokens
        if executable == "uv" and len(tokens) >= 2 and tokens[1].lower() == "pip":
            return [worker_python, "-m", "pip", *tokens[2:]]
        return tokens

    def _worker_venv_create_command(self, worker: dict, clone_path: Path) -> list[str]:
        requested_python = str(worker.get("python") or "").strip()
        venv_path = str(self._worker_venv_path(clone_path))
        if requested_python:
            py_launcher = self._resolve_py_launcher()
            if py_launcher:
                return [py_launcher, f"-{requested_python}", "-m", "venv", venv_path]
        return [self._python_executable(), "-m", "venv", venv_path]

    def _resolve_py_launcher(self) -> str | None:
        if os.name != "nt":
            return None
        return "py"

    def _worker_venv_matches_requested_python(self, worker: dict, worker_python: Path) -> bool:
        requested_python = str(worker.get("python") or "").strip()
        if not requested_python:
            return True
        try:
            completed = subprocess.run(
                [str(worker_python), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
                capture_output=True,
                check=True,
                text=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False
        return completed.stdout.strip() == requested_python
