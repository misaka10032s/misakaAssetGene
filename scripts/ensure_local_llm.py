from __future__ import annotations

from core.llm.local_manager import LocalLlmManager


def main() -> int:
    manager = LocalLlmManager()
    executable_path = manager.install()
    print(f"[setup] ollama ready: {executable_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
