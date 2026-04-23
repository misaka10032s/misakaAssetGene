from __future__ import annotations

import json
from pathlib import Path


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def render() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    tools_manifest = load_json(repo_root / "tools" / "manifest.json")
    workers_manifest = load_json(repo_root / "workers" / "manifest.json")
    registry = load_json(repo_root / "core" / "models" / "registry.json")

    lines = [
        "misakaAssetGene - Integration Manager",
        "",
        "Binary Tools:",
    ]
    for name, tool in tools_manifest["tools"].items():
        lines.append(f"- {name}: {tool['version']}")

    lines.append("")
    lines.append("Workers:")
    for name, worker in workers_manifest["workers"].items():
        recommended = worker["recommended"]
        lines.append(f"- {name}: {recommended['tag']} @ {recommended['commit']}")

    lines.append("")
    lines.append("Model Registry Categories:")
    for category in sorted(registry["categories"].keys()):
        lines.append(f"- {category}")

    return "\n".join(lines)


def main() -> None:
    print("[manager] rendering integration snapshot")
    print(render())
    print("[manager] status: ok")


if __name__ == "__main__":
    main()
