import json
from pathlib import Path

from core.models.schemas import ToolSnapshot


class ToolsService:
    def __init__(self, manifest_path: Path) -> None:
        self.manifest_path = manifest_path

    def list_tools(self) -> list[ToolSnapshot]:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        return [
            ToolSnapshot(name=name, version=tool["version"])
            for name, tool in manifest.get("tools", {}).items()
        ]
