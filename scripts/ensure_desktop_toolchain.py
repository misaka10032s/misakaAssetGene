from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def find_executable(name: str) -> str | None:
    candidates = [
        shutil.which(name),
        shutil.which(f"{name}.exe"),
        str(Path(os.environ.get("USERPROFILE", "")) / ".cargo" / "bin" / f"{name}.exe"),
        str(Path.home() / ".cargo" / "bin" / name),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def ensure_rust_toolchain() -> str:
    cargo_path = find_executable("cargo")
    if cargo_path is not None:
        return cargo_path

    if os.name == "nt":
        winget_path = shutil.which("winget")
        if winget_path is None:
            raise FileNotFoundError("winget was not found, so Rust cannot be installed automatically.")
        subprocess.run(
            [
                winget_path,
                "install",
                "--id",
                "Rustlang.Rustup",
                "--silent",
                "--accept-package-agreements",
                "--accept-source-agreements",
            ],
            check=True,
        )
        rustup_path = find_executable("rustup")
        if rustup_path is None:
            raise FileNotFoundError("Rustup was installed but rustup.exe could not be found.")
        subprocess.run([rustup_path, "default", "stable"], check=True)
    else:
        install_script = "curl https://sh.rustup.rs -sSf | sh -s -- -y"
        subprocess.run(install_script, check=True, shell=True)

    cargo_path = find_executable("cargo")
    if cargo_path is None:
        raise FileNotFoundError("Rust installation completed but cargo could not be found.")
    return cargo_path


def main() -> int:
    cargo_path = ensure_rust_toolchain()
    print(f"[setup] rust ready: {cargo_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
