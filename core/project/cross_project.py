"""Cross-project reference resolver with RW lock (spec §5.6).

The RW lock protects the _external/ copy step and origin updates from
concurrent write conflicts (RESEARCH_LOG §3.4 conclusion).

Implementation uses a per-path lock-file strategy that is Windows-compatible
without requiring any third-party library:
  - msvcrt.locking on Windows (exclusive byte-range lock on a .lock file)
  - fcntl.flock on POSIX

The public helpers copy_external_asset() and update_origins_json() acquire an
exclusive lock around their write operations.

M5.3 additions:
  - resolve_reference()  — §5.6.2 four-status read-side resolver
  - collect_project_refs() — extract all @refs from a project's assets + style_guide
  - detect_cycles()      — §5.6.5 cycle detection (warning, not error)
  - materialize_reference() — §16 Q4 deprecation/materialization tool
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Generator


REFERENCE_PATTERN = re.compile(
    r"^@(?P<project>[a-zA-Z0-9_-]+)/(?P<asset_path>[a-zA-Z0-9_./-]+)(?:#(?P<version>[a-zA-Z0-9_-]+))?$"
)


# ---------------------------------------------------------------------------
# Reference status enum (§5.6.2 / §5.6.3)
# ---------------------------------------------------------------------------

class RefStatus(str, Enum):
    """Four resolver statuses per spec §5.6.2 / §5.6.3."""
    LIVE = "live"          # found in source project, hash matches
    OUTDATED = "outdated"  # found in source but hash differs from origins.json record
    EXTERNAL = "external"  # source not available; served from _external/ copy
    BROKEN = "broken"      # source project/asset/version gone AND no usable copy


def _sha256_file(path: Path) -> str:
    """Return the hex SHA-256 digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(131072), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_reference(reference: str) -> dict[str, str] | None:
    """Parse an @project/path#version reference string.

    Returns a dict with keys 'project', 'asset_path', 'version', or None if the
    reference does not match the expected syntax.
    """
    match = REFERENCE_PATTERN.match(reference)
    if not match:
        return None
    return {key: value or "" for key, value in match.groupdict().items()}


# ---------------------------------------------------------------------------
# Lock-file based RW lock (Windows + POSIX compatible, no extra dependencies)
# ---------------------------------------------------------------------------

_lock_registry: dict[str, threading.Lock] = {}
_lock_registry_mu = threading.Lock()


def _get_thread_lock(lock_path: Path) -> threading.Lock:
    """Return a per-path threading.Lock (in-process serialisation layer)."""
    key = str(lock_path.resolve())
    with _lock_registry_mu:
        if key not in _lock_registry:
            _lock_registry[key] = threading.Lock()
        return _lock_registry[key]


@contextmanager
def _acquire_file_lock(lock_path: Path, timeout: float = 10.0) -> Generator[None, None, None]:
    """Acquire an exclusive OS-level lock on lock_path for the duration of the context.

    Uses msvcrt on Windows and fcntl on POSIX.  Falls back to a busy-wait with
    a timeout if the OS-level lock is unavailable (should not happen under
    normal conditions).

    Also acquires the in-process threading.Lock so that concurrent threads in
    the same process are serialised even before touching the file.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    thread_lock = _get_thread_lock(lock_path)

    deadline = time.monotonic() + timeout
    acquired_thread = thread_lock.acquire(timeout=timeout)
    if not acquired_thread:
        raise TimeoutError(f"Could not acquire thread lock for {lock_path} within {timeout}s")

    try:
        fh = open(lock_path, "w")  # noqa: WPS515 (open in context)
        try:
            if sys.platform == "win32":
                import msvcrt
                _deadline = deadline
                while True:
                    try:
                        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() > _deadline:
                            raise TimeoutError(
                                f"Could not acquire file lock for {lock_path} within {timeout}s"
                            )
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    try:
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
            else:
                import fcntl
                deadline2 = deadline
                while True:
                    try:
                        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() > deadline2:
                            raise TimeoutError(
                                f"Could not acquire file lock for {lock_path} within {timeout}s"
                            )
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)
        finally:
            fh.close()
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass
    finally:
        thread_lock.release()


def _external_lock_path(project_dir: Path) -> Path:
    """Return the lock file path for a project's _external/ directory."""
    return project_dir / "_external" / ".import.lock"


# ---------------------------------------------------------------------------
# Public cross-project helpers
# ---------------------------------------------------------------------------

def copy_external_asset(
    source_path: Path,
    dest_project_dir: Path,
    source_project_id: str,
    relative_asset_path: str,
    *,
    lock_timeout: float = 10.0,
) -> Path:
    """Copy source_path into dest_project_dir/_external/<source_project_id>/... under an exclusive lock.

    Returns the destination path.

    The lock prevents two concurrent processes/threads from writing the same
    _external/ entry simultaneously (RESEARCH_LOG §3.4).
    """
    dest_external = dest_project_dir / "_external" / source_project_id
    dest_file = dest_external / relative_asset_path

    lock_path = _external_lock_path(dest_project_dir)
    with _acquire_file_lock(lock_path, timeout=lock_timeout):
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, dest_file)

    return dest_file


def update_origins_json(
    dest_project_dir: Path,
    source_project_id: str,
    relative_asset_path: str,
    origin_metadata: dict,
    *,
    lock_timeout: float = 10.0,
) -> None:
    """Update _external/origins.json with a new or updated origin entry under an exclusive lock.

    origins.json schema (spec §5.6.1):
    {
      "schema_version": 1,
      "entries": [
        {
          "local_path": "_external/<source_project_id>/<relative_asset_path>",
          "origin": {
            "project": "<source_project_id>",
            "asset_path": "<relative_asset_path>",
            "version": "<version or ''>",
            "version_id": "<uuid or ''>",
            "sha256": "<hash or ''>"
          },
          "copied_at": "<ISO-8601 timestamp>"
        }
      ]
    }

    The §5.6.2 resolver depends on the entries list and sha256 field.
    """
    import datetime

    origins_path = dest_project_dir / "_external" / "origins.json"
    lock_path = _external_lock_path(dest_project_dir)
    local_path = f"_external/{source_project_id}/{relative_asset_path}"

    with _acquire_file_lock(lock_path, timeout=lock_timeout):
        if origins_path.exists():
            try:
                doc = json.loads(origins_path.read_text(encoding="utf-8"))
                # Validate schema_version; discard unrecognised formats.
                if not isinstance(doc, dict) or doc.get("schema_version") != 1:
                    doc = {"schema_version": 1, "entries": []}
            except (json.JSONDecodeError, OSError):
                doc = {"schema_version": 1, "entries": []}
        else:
            doc = {"schema_version": 1, "entries": []}

        entries: list[dict] = doc.get("entries", [])
        if not isinstance(entries, list):
            entries = []

        # Build the new/updated entry per §5.6.1.
        # Known standard fields are promoted into the origin sub-dict;
        # additional fields (e.g. original_ref for provenance audit) are
        # preserved verbatim so callers can store arbitrary metadata.
        _reserved_top = {"copied_at"}
        _known_origin = {"version", "version_id", "sha256"}
        extra_origin = {
            k: v for k, v in origin_metadata.items()
            if k not in _reserved_top and k not in _known_origin
        }
        origin_dict: dict = {
            "project": source_project_id,
            "asset_path": relative_asset_path,
            "version": origin_metadata.get("version", ""),
            "version_id": origin_metadata.get("version_id", ""),
            "sha256": origin_metadata.get("sha256", ""),
        }
        origin_dict.update(extra_origin)
        new_entry = {
            "local_path": local_path,
            "origin": origin_dict,
            "copied_at": origin_metadata.get(
                "copied_at",
                datetime.datetime.now(datetime.timezone.utc).isoformat(),
            ),
        }

        # Update existing entry for the same local_path or append.
        for idx, entry in enumerate(entries):
            if entry.get("local_path") == local_path:
                entries[idx] = new_entry
                break
        else:
            entries.append(new_entry)

        doc["entries"] = entries
        origins_path.parent.mkdir(parents=True, exist_ok=True)
        origins_path.write_text(
            json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# §5.6.2 — Resolver read-side (M5.3)
# ---------------------------------------------------------------------------

def _load_origins(dest_project_dir: Path) -> list[dict]:
    """Load _external/origins.json entries, returning [] on missing/corrupt."""
    origins_path = dest_project_dir / "_external" / "origins.json"
    if not origins_path.exists():
        return []
    try:
        doc = json.loads(origins_path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or doc.get("schema_version") != 1:
            return []
        entries = doc.get("entries", [])
        return entries if isinstance(entries, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def resolve_reference(
    reference: str,
    current_project_dir: Path,
    projects_root: Path,
    *,
    source_project_dir: Path | None = None,
) -> dict:
    """Resolve a cross-project reference to a concrete file path + status (§5.6.2).

    Parameters
    ----------
    reference:
        A raw @ref string, e.g. ``@adventure_rpg/char/kyuoka#v3``.
    current_project_dir:
        The directory of the project that CONTAINS this reference
        (i.e. the consumer project, whose ``_external/`` is searched as fallback).
    projects_root:
        The root directory that holds all projects (``<repo>/projects/``).
    source_project_dir:
        Override the source project directory (used in tests / when the
        source project lives outside projects_root).  When ``None``, the
        source project is looked up as ``projects_root/<source_project_id>``.

    Returns
    -------
    dict with keys:
        ``ref``        — the original reference string
        ``status``     — one of RefStatus (live / outdated / external / broken)
        ``path``       — resolved Path or None (None only when status=broken)
        ``hash``       — sha256 of the resolved file, or None
        ``origin_hash``— sha256 recorded in origins.json, or None
        ``message``    — human-readable note

    Never raises for a broken reference — returns broken status instead.
    """
    parsed = parse_reference(reference)
    if parsed is None:
        return {
            "ref": reference,
            "status": RefStatus.BROKEN,
            "path": None,
            "hash": None,
            "origin_hash": None,
            "message": f"Reference '{reference}' does not match @project/path[#version] syntax.",
        }

    source_project_id: str = parsed["project"]
    asset_path: str = parsed["asset_path"]
    version: str = parsed["version"]  # may be ""

    # --- Step 1: Look for the source project live (§5.6.2 Step 1) ----------
    if source_project_dir is None:
        _candidate = projects_root / source_project_id
        if _candidate.is_dir():
            source_project_dir = _candidate

    live_path: Path | None = None
    if source_project_dir is not None and source_project_dir.is_dir():
        # Try to find the versioned file under assets/<asset_path>/<version>.*
        # or the exact path <asset_path> if it points directly at a file.
        live_path = _find_asset_file(source_project_dir, asset_path, version)

    # Load origins entry for hash comparison
    origins = _load_origins(current_project_dir)
    local_key = f"_external/{source_project_id}/{asset_path}"
    if version:
        local_key = f"_external/{source_project_id}/{asset_path}/{version}"
    origin_entry = next(
        (e for e in origins if e.get("local_path", "").startswith(
            f"_external/{source_project_id}/{asset_path}"
        )),
        None,
    )
    origin_hash: str | None = (
        origin_entry["origin"].get("sha256") or None
        if origin_entry else None
    )

    if live_path is not None and live_path.is_file():
        live_hash = _sha256_file(live_path)
        if origin_hash and live_hash != origin_hash:
            return {
                "ref": reference,
                "status": RefStatus.OUTDATED,
                "path": live_path,
                "hash": live_hash,
                "origin_hash": origin_hash,
                "message": (
                    f"Live asset hash ({live_hash[:8]}…) differs from "
                    f"origins.json record ({origin_hash[:8]}…). "
                    "Use live or external copy?"
                ),
            }
        return {
            "ref": reference,
            "status": RefStatus.LIVE,
            "path": live_path,
            "hash": live_hash,
            "origin_hash": origin_hash,
            "message": "Live source found; hash matches.",
        }

    # --- Step 2: Fall back to _external/ copy (§5.6.2 Step 2) --------------
    external_path = _find_external_copy(current_project_dir, source_project_id, asset_path, version)
    if external_path is not None and external_path.is_file():
        ext_hash = _sha256_file(external_path)
        if origin_hash and ext_hash != origin_hash:
            return {
                "ref": reference,
                "status": RefStatus.BROKEN,
                "path": None,
                "hash": ext_hash,
                "origin_hash": origin_hash,
                "message": (
                    f"_external/ copy hash ({ext_hash[:8]}…) does not match "
                    f"origins.json ({origin_hash[:8]}…). Copy may be corrupted."
                ),
            }
        return {
            "ref": reference,
            "status": RefStatus.EXTERNAL,
            "path": external_path,
            "hash": ext_hash,
            "origin_hash": origin_hash,
            "message": "Source project unavailable; served from _external/ copy.",
        }

    # --- Broken: neither live nor external ----------------------------------
    return {
        "ref": reference,
        "status": RefStatus.BROKEN,
        "path": None,
        "hash": None,
        "origin_hash": origin_hash,
        "message": (
            f"Cannot resolve '{reference}': "
            "source project/asset not found and no _external/ copy available."
        ),
    }


def _find_asset_file(source_project_dir: Path, asset_path: str, version: str) -> Path | None:
    """Locate the actual file for an asset within a project directory.

    Search strategy (most specific to least):
    1. assets/<asset_path>/<version>.<any extension>  (versioned file)
    2. assets/<asset_path>/<version>                  (exact named file, no ext)
    3. assets/<asset_path>                            (direct path, no version)
    4. <asset_path> relative to project root          (fully qualified relative path)
    """
    asset_dir = source_project_dir / "assets" / asset_path

    if version:
        # Look for <version>.* under the asset directory
        if asset_dir.is_dir():
            for candidate in asset_dir.iterdir():
                if candidate.stem == version and candidate.is_file():
                    return candidate
            # Exact named entry
            exact = asset_dir / version
            if exact.is_file():
                return exact
        # Maybe asset_path already contains the version in the filename
        direct = source_project_dir / "assets" / asset_path
        if direct.is_file():
            return direct

    # No version or fallback to any file in the asset directory
    if asset_dir.is_dir():
        # Return the first file found (fallback strategy)
        candidates = [f for f in asset_dir.iterdir() if f.is_file()]
        if candidates:
            return sorted(candidates)[0]

    # Try as a direct path from project root
    direct_root = source_project_dir / asset_path
    if direct_root.is_file():
        return direct_root

    return None


def _find_external_copy(
    consumer_project_dir: Path,
    source_project_id: str,
    asset_path: str,
    version: str,
) -> Path | None:
    """Locate the _external/ fallback copy for a reference."""
    external_base = consumer_project_dir / "_external" / source_project_id

    if version:
        # Look for <version>.* under _external/<source>/<asset_path>/
        versioned_dir = external_base / asset_path
        if versioned_dir.is_dir():
            for candidate in versioned_dir.iterdir():
                if candidate.stem == version and candidate.is_file():
                    return candidate
            exact = versioned_dir / version
            if exact.is_file():
                return exact
        # Direct external path
        direct = external_base / asset_path
        if direct.is_file():
            return direct

    # No version — return first file in directory
    asset_dir = external_base / asset_path
    if asset_dir.is_dir():
        candidates = [f for f in asset_dir.iterdir() if f.is_file()]
        if candidates:
            return sorted(candidates)[0]

    direct_root = external_base / asset_path
    if direct_root.is_file():
        return direct_root

    return None


# ---------------------------------------------------------------------------
# §5.6.4 — Collect project references (used by export re-resolution)
# ---------------------------------------------------------------------------

_INLINE_REF_PATTERN = re.compile(
    r"@(?P<project>[a-zA-Z0-9_-]+)/(?P<asset_path>[a-zA-Z0-9_./-]+)(?:#(?P<version>[a-zA-Z0-9_-]+))?"
)


def collect_project_refs(project_dir: Path) -> list[str]:
    """Return all unique @ref strings found in a project's assets + style_guide.

    Scanned sources:
    - style_guide.md (visual_anchors / voice references — spec §5.6)
    - assets/index.json → ``dependencies`` fields in asset records
    - jobs.json → ``dependencies`` fields in generation jobs

    Only the fields where @refs are valid (spec §5.6 allowed locations) are parsed.
    """
    refs: set[str] = set()

    # style_guide.md
    sg = project_dir / "style_guide.md"
    if sg.is_file():
        for m in _INLINE_REF_PATTERN.finditer(sg.read_text(encoding="utf-8", errors="replace")):
            refs.add(
                "@{project}/{asset_path}{ver}".format(
                    project=m.group("project"),
                    asset_path=m.group("asset_path"),
                    ver=f"#{m.group('version')}" if m.group("version") else "",
                )
            )

    def _scan_dependencies(deps: object) -> None:
        if not isinstance(deps, list):
            return
        for dep in deps:
            if isinstance(dep, str) and dep.startswith("@"):
                refs.add(dep)

    # assets/index.json
    index_path = project_dir / "assets" / "index.json"
    if index_path.is_file():
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
            for asset in data.get("assets", []):
                _scan_dependencies(asset.get("dependencies", []))
        except (json.JSONDecodeError, OSError):
            pass

    # jobs.json
    jobs_path = project_dir / "jobs.json"
    if jobs_path.is_file():
        try:
            data = json.loads(jobs_path.read_text(encoding="utf-8"))
            jobs_list = data if isinstance(data, list) else data.get("jobs", [])
            for job in jobs_list:
                _scan_dependencies(job.get("dependencies", []))
        except (json.JSONDecodeError, OSError):
            pass

    return sorted(refs)


# ---------------------------------------------------------------------------
# §5.6.5 — Circular dependency detection (M5.3)
# ---------------------------------------------------------------------------

def detect_cycles(
    project_id: str,
    projects_root: Path,
    *,
    _visited: frozenset[str] | None = None,
    _path: tuple[str, ...] | None = None,
) -> list[list[str]]:
    """Detect cross-project reference cycles starting from project_id (§5.6.5).

    Cycles are ALLOWED per spec — this function only detects and reports them;
    it does NOT raise.  Returns a list of cycles, where each cycle is the
    path of project IDs that form the loop (e.g. ["A", "B", "A"]).

    Example: A→B→A returns [["A", "B", "A"]].
    """
    if _visited is None:
        _visited = frozenset()
    if _path is None:
        _path = (project_id,)

    project_dir = projects_root / project_id
    if not project_dir.is_dir():
        return []

    refs = collect_project_refs(project_dir)
    cycles: list[list[str]] = []

    for ref in refs:
        parsed = parse_reference(ref)
        if parsed is None:
            continue
        dep_project = parsed["project"]
        if dep_project == project_id:
            continue  # self-reference, not a cross-project cycle

        current_path = _path + (dep_project,)

        if dep_project in _visited:
            # Found a cycle — record the cycle path
            cycles.append(list(current_path))
            continue

        sub_cycles = detect_cycles(
            dep_project,
            projects_root,
            _visited=_visited | {project_id},
            _path=current_path,
        )
        cycles.extend(sub_cycles)

    return cycles


# ---------------------------------------------------------------------------
# §16 Q4 — Materialization tool (M5.3)
# ---------------------------------------------------------------------------

class MaterializeError(Exception):
    """Raised when a reference cannot be materialized."""


def materialize_reference(
    reference: str,
    current_project_dir: Path,
    projects_root: Path,
    *,
    new_asset_id: str | None = None,
    source_project_dir: Path | None = None,
    lock_timeout: float = 10.0,
) -> dict:
    """Materialize a cross-project reference into a local asset copy (§16 Q4).

    Copies the referenced asset file into ``current_project_dir/_external/``
    (if not already there from a previous copy), then updates origins.json to
    record the original ref as provenance metadata.

    This is an EXPLICIT / OPT-IN operation — never called automatically.

    Parameters
    ----------
    reference:
        The @ref string to materialize, e.g. ``@adventure_rpg/char/kyuoka#v3``.
    current_project_dir:
        The project directory where the local copy will be placed.
    projects_root:
        Root of all projects (used by resolve_reference).
    new_asset_id:
        Optional id to use as the local asset identifier in provenance.
        Defaults to the reference string with special chars stripped.
    source_project_dir:
        Override the source project directory (used in tests).
    lock_timeout:
        Timeout in seconds for the RW lock (passed to copy_external_asset).

    Returns
    -------
    dict with keys:
        ``ref``          — original reference
        ``status``       — "materialized" | "already_external" | "broken"
        ``local_path``   — Path of the materialized file (or None if broken)
        ``provenance``   — dict recorded in origins.json for audit
        ``message``      — human-readable note

    Broken refs are NOT raised — they are returned as status="broken".
    This allows bulk materialization to continue past individual failures.
    """
    import datetime as _dt

    resolution = resolve_reference(
        reference,
        current_project_dir,
        projects_root,
        source_project_dir=source_project_dir,
    )

    if resolution["status"] == RefStatus.BROKEN:
        return {
            "ref": reference,
            "status": "broken",
            "local_path": None,
            "provenance": None,
            "message": resolution["message"],
        }

    source_file: Path = resolution["path"]
    parsed = parse_reference(reference)
    assert parsed is not None  # guaranteed non-None since status != broken

    source_project_id = parsed["project"]
    asset_path = parsed["asset_path"]
    version = parsed["version"]

    # Security: ensure source_file is inside the source project directory.
    # This prevents materializing files outside project boundaries.
    if source_project_dir is None:
        source_project_dir = projects_root / source_project_id
    try:
        source_file.resolve().relative_to(source_project_dir.resolve())
    except ValueError:
        # Source file resolves outside the expected project boundary —
        # this should not happen under normal use, but guard defensively.
        return {
            "ref": reference,
            "status": "broken",
            "local_path": None,
            "provenance": None,
            "message": (
                f"Security: resolved path {source_file} is outside "
                f"source project dir {source_project_dir}."
            ),
        }

    # Build relative asset path that will be used in _external/
    rel_path = f"{asset_path}/{version}{source_file.suffix}" if version else (
        f"{asset_path}{source_file.suffix}" if not source_file.suffix else
        f"{asset_path}"
    )
    # Normalize to not have double extensions
    if rel_path.count(source_file.suffix) > 1 and source_file.suffix:
        rel_path = f"{asset_path}/{version}" if version else asset_path

    file_hash = _sha256_file(source_file)
    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()

    provenance = {
        "original_ref": reference,
        "project": source_project_id,
        "asset_path": asset_path,
        "version": version or "",
        "sha256": file_hash,
        "materialized_at": now_iso,
        "original_asset_id": new_asset_id or _ref_to_id(reference),
    }

    # Copy file into _external/ under exclusive lock
    dest_file = copy_external_asset(
        source_file,
        current_project_dir,
        source_project_id,
        rel_path,
        lock_timeout=lock_timeout,
    )

    # Update origins.json with provenance (original_ref preserved for audit)
    update_origins_json(
        current_project_dir,
        source_project_id,
        rel_path,
        {
            "version": version or "",
            "version_id": new_asset_id or _ref_to_id(reference),
            "sha256": file_hash,
            "copied_at": now_iso,
            "original_ref": reference,  # provenance audit field
        },
        lock_timeout=lock_timeout,
    )

    status_label = (
        "already_external"
        if resolution["status"] == RefStatus.EXTERNAL
        else "materialized"
    )

    return {
        "ref": reference,
        "status": status_label,
        "local_path": dest_file,
        "provenance": provenance,
        "message": (
            f"Reference '{reference}' materialized to {dest_file}. "
            "Provenance recorded in origins.json."
        ),
    }


def materialize_project_refs(
    current_project_dir: Path,
    projects_root: Path,
    *,
    refs: list[str] | None = None,
    lock_timeout: float = 10.0,
) -> dict:
    """Materialize all (or a specified subset of) cross-project refs in a project.

    Parameters
    ----------
    current_project_dir:
        The project whose refs should be materialized.
    projects_root:
        Root of all projects.
    refs:
        Optional list of specific @ref strings to materialize.  If None,
        all refs found via collect_project_refs() are processed.
    lock_timeout:
        Per-operation RW lock timeout.

    Returns
    -------
    dict with keys:
        ``materialized``   — list of result dicts for successful materializations
        ``broken``         — list of result dicts for refs that couldn't be resolved
        ``total``          — total refs processed
    """
    if refs is None:
        refs = collect_project_refs(current_project_dir)

    materialized: list[dict] = []
    broken: list[dict] = []

    for ref in refs:
        result = materialize_reference(
            ref,
            current_project_dir,
            projects_root,
            lock_timeout=lock_timeout,
        )
        if result["status"] == "broken":
            broken.append(result)
        else:
            materialized.append(result)

    return {
        "materialized": materialized,
        "broken": broken,
        "total": len(refs),
    }


def _ref_to_id(reference: str) -> str:
    """Convert an @ref string to a filesystem-safe identifier."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", reference.lstrip("@"))
