from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx


def main() -> int:
    health_url = os.environ.get("MISAKA_WORKER_HEALTH_URL", "").strip()
    checkpoint_dir = Path(os.environ.get("MISAKA_WORKER_CHECKPOINT_DIR", ""))
    if checkpoint_dir.exists():
        checkpoints = [item for item in checkpoint_dir.iterdir() if item.is_file() and item.name != "put_checkpoints_here"]
        if not checkpoints:
            print(json.dumps({"ok": False, "detail": "No ComfyUI checkpoint is installed."}, ensure_ascii=False))
            return 1
    try:
        response = httpx.get(health_url, timeout=5.0)
        response.raise_for_status()
    except httpx.HTTPError as error:
        print(json.dumps({"ok": False, "detail": f"ComfyUI health check failed: {error}"}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "detail": "ComfyUI health check succeeded."}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
