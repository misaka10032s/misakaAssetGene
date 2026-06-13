"""Project portability -- export helpers and zip import (spec §5.5).

Zip structure produced by ProjectExportService:
  <any files relative to project_dir>/
  export.manifest.json   <- required
  license-report.json    <- optional

Import validates the manifest, rejects zip-slip entries, resolves collisions
(same project id or name -> reassign a new id and record origin_id in project.json).
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from zipfile import ZipFile


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def ensure_relative(path: Path, root: Path) -> str:
    """Return a relative path string, rejecting absolute paths.

    Used by ProjectManager to normalise paths before writing project.json.
    Spec §5.5: absolute paths are rejected on write.
    """
    if path.is_absolute():
        raise ValueError("Absolute paths are not allowed in portable project metadata.")
    return str(path.relative_to(root)) if path.is_relative_to(root) else str(path)


# ---------------------------------------------------------------------------
# Manifest constants
# ---------------------------------------------------------------------------

MANIFEST_REQUIRED_KEYS = {"project", "exported_at"}
MAX_UNCOMPRESSED_BYTES = 4 * 1024 ** 3  # 4 GB hard cap


class ZipImportError(Exception):
    """Raised for any import validation failure."""


# ---------------------------------------------------------------------------
# Zip-slip guard
# ---------------------------------------------------------------------------

def _safe_extract_path(zip_entry_name: str, target_dir: Path) -> Path:
    """Resolve a zip entry to an absolute path; raise ZipImportError on zip-slip.

    A zip-slip attack uses entries like ../../etc/passwd to escape the intended
    extraction directory. We resolve the target and verify it is still inside
    target_dir.
    """
    safe_name = zip_entry_name.replace("\\", "/").lstrip("/")
    resolved = (target_dir / safe_name).resolve()
    try:
        resolved.relative_to(target_dir.resolve())
    except ValueError as exc:
        raise ZipImportError(
            f"Zip-slip detected: entry '{zip_entry_name}' escapes target directory."
        ) from exc
    return resolved


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------

def _validate_manifest(manifest: dict) -> None:
    missing = MANIFEST_REQUIRED_KEYS - manifest.keys()
    if missing:
        raise ZipImportError(f"Manifest missing required keys: {missing}")

    project = manifest.get("project")
    if not isinstance(project, dict):
        raise ZipImportError("Manifest 'project' must be a JSON object.")

    for key in ("id", "name"):
        if not str(project.get(key, "")).strip():
            raise ZipImportError(f"Manifest project.{key} must be a non-empty string.")


# ---------------------------------------------------------------------------
# Collision detection helpers
# ---------------------------------------------------------------------------

def _build_project_id(project_name: str) -> str:
    """Derive a filesystem-safe id from a project name.

    Mirrors ProjectManager._build_project_id without importing it (avoids circular import).
    """
    normalized = re.sub(r"[^\w\s-]", "", project_name.strip(), flags=re.UNICODE)
    normalized = re.sub(r"[-\s]+", "-", normalized, flags=re.UNICODE).strip("-_").lower()
    if not normalized:
        raise ZipImportError("Project name contains no usable characters.")
    return normalized


def _unique_id(base: str, existing_ids: set[str]) -> str:
    """Append a short random suffix until the id is absent from existing_ids."""
    candidate = base
    while candidate in existing_ids:
        suffix = uuid.uuid4().hex[:6]
        candidate = f"{base}-{suffix}"
    return candidate


# ---------------------------------------------------------------------------
# Public import entry point
# ---------------------------------------------------------------------------

def import_project_zip(
    zip_path: Path,
    projects_root: Path,
    *,
    max_uncompressed_bytes: int = MAX_UNCOMPRESSED_BYTES,
) -> dict:
    """Import a *.misaka.zip archive into projects_root (spec §5.5).

    Steps
    -----
    1. Open and validate the archive (manifest presence, schema, zip-slip).
    2. Enforce an uncompressed-size sanity cap.
    3. Detect collision: if a project with the same id or name already exists,
       generate a new id and record origin_id in the imported project.json.
    4. Extract all entries (except export.manifest.json / license-report.json)
       to projects_root/<new_id>/.
    5. Write / overwrite project.json with the (possibly reassigned) id.
    6. Return a result dict with project_id, project_name,
       collision_resolved (bool), and origin_id (str | None).

    Raises
    ------
    ZipImportError
        On any validation failure (missing manifest, zip-slip, oversized, etc.).
    """
    if not zip_path.is_file():
        raise ZipImportError(f"Zip file not found: {zip_path}")

    with ZipFile(zip_path, "r") as zf:
        name_list = zf.namelist()

        # 1. Locate and read manifest
        if "export.manifest.json" not in name_list:
            raise ZipImportError("Archive is missing 'export.manifest.json'.")

        manifest_bytes = zf.read("export.manifest.json")
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ZipImportError(f"Manifest is not valid JSON: {exc}") from exc

        _validate_manifest(manifest)

        # 2. Size sanity check (header-based pre-flight; NOT trusted as the sole guard)
        # A zip-bomb can understate entry sizes in headers, so we also enforce a
        # running decompressed-byte budget during actual extraction (see step 4).
        total_header_size = sum(info.file_size for info in zf.infolist())
        if total_header_size > max_uncompressed_bytes:
            raise ZipImportError(
                f"Archive uncompressed size {total_header_size:,} bytes (from headers) "
                f"exceeds limit of {max_uncompressed_bytes:,} bytes."
            )

        # 3. Collision detection
        source_project = manifest["project"]
        source_id: str = str(source_project["id"]).strip()
        source_name: str = str(source_project["name"]).strip()

        existing_ids: set[str] = {
            p.parent.name
            for p in projects_root.glob("*/project.json")
        }
        existing_names: set[str] = set()
        for pjson in projects_root.glob("*/project.json"):
            try:
                data = json.loads(pjson.read_text(encoding="utf-8"))
                existing_names.add(str(data.get("name", "")).strip().lower())
            except Exception:
                pass  # Corrupt existing project -- skip gracefully.

        collision_resolved = False
        origin_id: str | None = None

        # Sanitize source_id: re-normalize it through _build_project_id logic so
        # path-traversal sequences like "../../evil" or "sub/dir" are stripped.
        # If the id is already safe (round-trips unchanged) we keep it; otherwise
        # we fall back to an id derived from the project name.
        try:
            sanitized_source_id = _build_project_id(source_id)
        except ZipImportError:
            sanitized_source_id = None

        if sanitized_source_id == source_id:
            # source_id is already filesystem-safe; use it as-is (no normalization needed).
            safe_source_id = source_id
        else:
            # source_id contained unsafe characters — derive from project name instead.
            safe_source_id = _build_project_id(source_name)

        if safe_source_id in existing_ids or source_name.lower() in existing_names:
            collision_resolved = True
            origin_id = source_id
            # Derive the collision-resolved id from the project name (same as before),
            # then make it unique.  Do NOT reuse safe_source_id as the base — when only
            # the name collides, safe_source_id may not be in existing_ids and would
            # be returned unchanged, producing a "collision_resolved=True" result with
            # the original id, which contradicts the caller's expectation.
            base_id = _build_project_id(source_name)
            new_id = _unique_id(base_id, existing_ids)
        else:
            new_id = safe_source_id

        target_dir = projects_root / new_id

        # Guard: ensure target_dir is inside projects_root even after .resolve()
        # This is the final backstop against any residual path traversal.
        try:
            target_dir.resolve().relative_to(projects_root.resolve())
        except ValueError as exc:
            raise ZipImportError(
                f"Computed target directory '{target_dir}' would escape projects root. "
                f"Rejecting malicious manifest id."
            ) from exc

        if target_dir.exists():
            raise ZipImportError(
                f"Target directory '{target_dir}' already exists. Aborting import."
            )

        # 4. Extract files (with zip-slip protection and running decompressed-byte budget)
        # We do NOT trust entry headers for size enforcement — a zip-bomb can store
        # tiny header sizes while decompressing to gigabytes.  Instead we stream each
        # entry in chunks and count actual decompressed bytes, aborting and cleaning up
        # if the running total exceeds max_uncompressed_bytes.
        _CHUNK_SIZE = 256 * 1024  # 256 KiB per read chunk
        skip_entries = {"export.manifest.json", "license-report.json"}
        # Skip consultant cache entries that may appear in legacy zips created
        # before spec §5.14 enforced exclusion (they are private AI working memory,
        # not portable user assets — do NOT restore them on import).
        _CONSULTANT_CACHE_PREFIX = ".cache/consultant/"
        target_dir.mkdir(parents=True, exist_ok=False)

        running_bytes = 0
        import shutil

        try:
            for entry_name in name_list:
                if entry_name in skip_entries:
                    continue
                # Skip consultant cache entries from legacy zips (spec §5.14).
                if entry_name.startswith(_CONSULTANT_CACHE_PREFIX):
                    continue
                if entry_name.endswith("/"):
                    _safe_extract_path(entry_name, target_dir).mkdir(
                        parents=True, exist_ok=True
                    )
                    continue

                dest = _safe_extract_path(entry_name, target_dir)
                dest.parent.mkdir(parents=True, exist_ok=True)

                # Stream entry in chunks, counting actual decompressed bytes.
                with zf.open(entry_name) as entry_fh, open(dest, "wb") as out_fh:
                    while True:
                        chunk = entry_fh.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        running_bytes += len(chunk)
                        if running_bytes > max_uncompressed_bytes:
                            out_fh.write(chunk)  # flush partial before raising
                            raise ZipImportError(
                                f"Archive real decompressed size exceeded limit of "
                                f"{max_uncompressed_bytes:,} bytes (zip-bomb guard). "
                                f"Aborting extraction."
                            )
                        out_fh.write(chunk)
        except ZipImportError:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise

        # 5. Write (updated) project.json with the resolved id
        new_project_data = {**source_project, "id": new_id}
        if origin_id is not None:
            new_project_data["origin_id"] = origin_id

        (target_dir / "project.json").write_text(
            json.dumps(new_project_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    return {
        "project_id": new_id,
        "project_name": source_name,
        "collision_resolved": collision_resolved,
        "origin_id": origin_id,
    }
