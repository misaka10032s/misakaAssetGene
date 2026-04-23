import json
from pathlib import Path


class ModelRegistryService:
    def __init__(self, registry_path: Path) -> None:
        self.registry_path = registry_path

    def list_categories(self) -> list[str]:
        registry = json.loads(self.registry_path.read_text(encoding="utf-8"))
        return sorted(registry.get("categories", {}).keys())
