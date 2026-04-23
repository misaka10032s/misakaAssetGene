from pathlib import Path


def ensure_relative(path: Path, root: Path) -> str:
    if path.is_absolute():
        raise ValueError("Absolute paths are not allowed in portable project metadata.")
    return str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
