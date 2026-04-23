import json
from pathlib import Path

from core.models.schemas import WorkerSnapshot


class WorkersService:
    def __init__(self, manifest_path: Path) -> None:
        self.manifest_path = manifest_path

    def list_workers(self) -> list[WorkerSnapshot]:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        snapshots: list[WorkerSnapshot] = []
        for name, worker in manifest.get("workers", {}).items():
            recommended = worker.get("recommended", {})
            reference = f"{recommended.get('tag', 'n/a')} @ {recommended.get('commit', 'n/a')}"
            snapshots.append(WorkerSnapshot(name=name, reference=reference))
        return sorted(snapshots, key=lambda snapshot: snapshot.name)
