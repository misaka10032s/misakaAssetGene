from __future__ import annotations

import json
import os

import httpx


def main() -> int:
    health_url = os.environ.get("MISAKA_WORKER_HEALTH_URL", "").strip()
    try:
        response = httpx.get(health_url, timeout=5.0)
        response.raise_for_status()
    except httpx.HTTPError as error:
        print(json.dumps({"ok": False, "detail": f"ultimate-rvc health check failed: {error}"}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "detail": "ultimate-rvc health check succeeded."}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
