from __future__ import annotations

import json
import platform
from pathlib import Path

from core.integration.workers import WorkersService


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    tools_manifest = json.loads((repo_root / "tools" / "manifest.json").read_text(encoding="utf-8"))
    workers_manifest = json.loads((repo_root / "workers" / "manifest.json").read_text(encoding="utf-8"))
    registry = json.loads((repo_root / "core" / "models" / "registry.json").read_text(encoding="utf-8"))
    workers_service = WorkersService(repo_root / "workers" / "manifest.json")
    installed_workers = [worker.name for worker in workers_service.list_workers() if worker.is_installed]

    print("[doctor] MisakaAssetGene doctor")
    print(f"[doctor] platform: {platform.system()} {platform.release()}")
    print(f"[doctor] tools tracked: {', '.join(sorted(tools_manifest['tools'].keys()))}")
    print(f"[doctor] workers tracked: {', '.join(sorted(workers_manifest['workers'].keys()))}")
    print(f"[doctor] workers installed: {', '.join(sorted(installed_workers)) if installed_workers else '(none)'}")
    print(f"[doctor] registry categories: {', '.join(sorted(registry['categories'].keys()))}")
    print("[doctor] status: ok")


if __name__ == "__main__":
    main()
