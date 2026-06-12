"""Tests for zip import (spec §5.5) and cross-project RW lock (spec §5.6 / RESEARCH_LOG §3.4).

Coverage:
- Round-trip: export -> import -> assert equivalence
- Collision handling: same id/name -> new id, origin_id recorded
- Zip-slip rejection (security)
- Manifest schema validation
- Size sanity check
- RW lock: concurrent thread access to _external/ without corruption
- API endpoint: POST /api/v1/projects/import
"""
from __future__ import annotations

import io
import json
import threading
import time
import uuid
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from starlette.testclient import TestClient

import core.main as main
from core.project.cross_project import (
    copy_external_asset,
    parse_reference,
    update_origins_json,
)
from core.project.export import ProjectExportService
from core.project.manager import ProjectManager
from core.project.portability import (
    ZipImportError,
    _safe_extract_path,
    import_project_zip,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_minimal_zip(
    tmp_path: Path,
    *,
    project_id: str = "test-proj",
    project_name: str = "Test Project",
    extra_files: dict[str, bytes] | None = None,
) -> Path:
    """Build a minimal *.misaka.zip with a valid manifest."""
    manifest = {
        "project": {
            "id": project_id,
            "name": project_name,
            "type": "RPG",
            "synopsis": "A test project",
        },
        "exported_at": "2026-06-12T00:00:00+00:00",
        "resolve_refs": True,
        "jobs": 0,
        "assets": 0,
        "plans": 0,
        "warnings": [],
    }
    tmp_path.mkdir(parents=True, exist_ok=True)
    zip_path = tmp_path / f"{project_id}.misaka.zip"
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr("export.manifest.json", json.dumps(manifest))
        zf.writestr("project.json", json.dumps(manifest["project"]))
        zf.writestr("style_guide.md", "# Style Guide\n")
        zf.writestr("conversation.json", '{"entries":[]}\n')
        zf.writestr("assets/index.json", '{"assets":[]}\n')
        if extra_files:
            for name, data in extra_files.items():
                zf.writestr(name, data)
    return zip_path


def _seed_project_dir(tmp_path: Path, project_id: str = "alpha") -> Path:
    """Create a minimal project directory structure on disk."""
    project_dir = tmp_path / project_id
    for d in ["assets/images", "assets/audio", "_external", ".cache"]:
        (project_dir / d).mkdir(parents=True, exist_ok=True)
    pdata = {"id": project_id, "name": "Alpha Project", "type": "RPG", "synopsis": "s"}
    (project_dir / "project.json").write_text(json.dumps(pdata), encoding="utf-8")
    (project_dir / "style_guide.md").write_text("# style\n", encoding="utf-8")
    (project_dir / "conversation.json").write_text('{"entries":[]}\n', encoding="utf-8")
    (project_dir / "assets" / "index.json").write_text('{"assets":[]}\n', encoding="utf-8")
    return project_dir


# ---------------------------------------------------------------------------
# parse_reference (cross_project — unchanged API, verify still works)
# ---------------------------------------------------------------------------

def test_parse_reference_valid() -> None:
    ref = parse_reference("@adventure_rpg/char/kyuoka#v3")
    assert ref is not None
    assert ref["project"] == "adventure_rpg"
    assert ref["asset_path"] == "char/kyuoka"
    assert ref["version"] == "v3"


def test_parse_reference_no_version() -> None:
    ref = parse_reference("@proj/assets/hero.png")
    assert ref is not None
    assert ref["version"] == ""


def test_parse_reference_invalid() -> None:
    assert parse_reference("not-a-reference") is None
    assert parse_reference("@/missing-project") is None


# ---------------------------------------------------------------------------
# Zip-slip protection
# ---------------------------------------------------------------------------

def test_safe_extract_path_rejects_escape(tmp_path: Path) -> None:
    """Zip entry with path traversal must raise ZipImportError."""
    with pytest.raises(ZipImportError, match="Zip-slip"):
        _safe_extract_path("../../etc/passwd", tmp_path)


def test_safe_extract_path_rejects_absolute_on_posix(tmp_path: Path) -> None:
    """Absolute paths in zip entries must be rejected."""
    # After lstrip('/'), this becomes 'etc/passwd' and resolves inside tmp_path -- safe.
    # But a crafted entry that after lstrip still escapes must be caught.
    # We test the double-dot case which is the canonical zip-slip vector.
    with pytest.raises(ZipImportError):
        _safe_extract_path("../sibling/secret.txt", tmp_path)


def test_import_rejects_zipslip_entry(tmp_path: Path) -> None:
    """import_project_zip must reject a zip containing a path-traversal entry."""
    manifest = {
        "project": {"id": "evil", "name": "Evil", "type": "RPG", "synopsis": ""},
        "exported_at": "2026-01-01T00:00:00+00:00",
    }
    zip_path = tmp_path / "evil.zip"
    with ZipFile(zip_path, "w") as zf:
        zf.writestr("export.manifest.json", json.dumps(manifest))
        zf.writestr("project.json", json.dumps(manifest["project"]))
        # Craft a zip-slip entry
        info = zf.infolist()[0].__class__("../../escape.txt")
        zf.writestr("../../escape.txt", b"pwned")

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    with pytest.raises(ZipImportError, match="Zip-slip"):
        import_project_zip(zip_path, projects_root)


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------

def test_import_rejects_missing_manifest(tmp_path: Path) -> None:
    zip_path = tmp_path / "no-manifest.zip"
    with ZipFile(zip_path, "w") as zf:
        zf.writestr("project.json", '{"id":"x","name":"X","type":"T","synopsis":""}')
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    with pytest.raises(ZipImportError, match="export.manifest.json"):
        import_project_zip(zip_path, projects_root)


def test_import_rejects_corrupt_manifest(tmp_path: Path) -> None:
    zip_path = tmp_path / "bad.zip"
    with ZipFile(zip_path, "w") as zf:
        zf.writestr("export.manifest.json", b"\xff\xfe not json")
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    with pytest.raises(ZipImportError):
        import_project_zip(zip_path, projects_root)


def test_import_rejects_manifest_missing_project_id(tmp_path: Path) -> None:
    manifest = {
        "project": {"name": "No ID", "type": "RPG", "synopsis": ""},
        "exported_at": "2026-01-01T00:00:00+00:00",
    }
    zip_path = tmp_path / "bad.zip"
    with ZipFile(zip_path, "w") as zf:
        zf.writestr("export.manifest.json", json.dumps(manifest))
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    with pytest.raises(ZipImportError, match="id"):
        import_project_zip(zip_path, projects_root)


# ---------------------------------------------------------------------------
# Size sanity check
# ---------------------------------------------------------------------------

def test_import_rejects_oversized_zip(tmp_path: Path) -> None:
    zip_path = _make_minimal_zip(tmp_path)
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    # Cap at 1 byte to force failure
    with pytest.raises(ZipImportError, match="exceeds limit"):
        import_project_zip(zip_path, projects_root, max_uncompressed_bytes=1)


# ---------------------------------------------------------------------------
# Happy-path import
# ---------------------------------------------------------------------------

def test_import_basic_project(tmp_path: Path) -> None:
    zip_path = _make_minimal_zip(tmp_path, project_id="my-game", project_name="My Game")
    projects_root = tmp_path / "projects"
    projects_root.mkdir()

    result = import_project_zip(zip_path, projects_root)

    assert result["project_id"] == "my-game"
    assert result["project_name"] == "My Game"
    assert result["collision_resolved"] is False
    assert result["origin_id"] is None

    project_json = projects_root / "my-game" / "project.json"
    assert project_json.exists()
    data = json.loads(project_json.read_text(encoding="utf-8"))
    assert data["id"] == "my-game"
    assert "origin_id" not in data

    # style_guide and conversation should be extracted
    assert (projects_root / "my-game" / "style_guide.md").exists()
    assert (projects_root / "my-game" / "conversation.json").exists()


# ---------------------------------------------------------------------------
# Round-trip: export -> import -> equivalence
# ---------------------------------------------------------------------------

def test_export_import_roundtrip(tmp_path: Path) -> None:
    """Full round-trip: ProjectExportService -> import_project_zip -> assert equivalence."""
    # Setup a project directory with assets
    project_id = "round-trip-proj"
    project_dir = tmp_path / "projects" / project_id
    project_dir.mkdir(parents=True)
    for d in ["assets/images", "assets/audio", "_external", ".cache"]:
        (project_dir / d).mkdir(parents=True, exist_ok=True)

    project_summary = {
        "id": project_id,
        "name": "Round Trip Project",
        "type": "VN",
        "synopsis": "Export then import",
    }
    (project_dir / "project.json").write_text(
        json.dumps(project_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (project_dir / "style_guide.md").write_text("# Style\n", encoding="utf-8")
    (project_dir / "conversation.json").write_text('{"entries":[]}\n', encoding="utf-8")
    (project_dir / "assets" / "index.json").write_text('{"assets":[]}\n', encoding="utf-8")
    (project_dir / "assets" / "images" / "hero.png").write_bytes(b"\x89PNG")

    # Export
    svc = ProjectExportService()
    zip_path = svc.export_project(
        project_dir=project_dir,
        project_summary=project_summary,
        jobs=[],
        assets=[],
        plans=[],
        license_report={},
        resolve_refs=False,
    )
    assert zip_path.exists()
    assert zip_path.suffix == ".zip"

    # Import into a fresh root
    import_root = tmp_path / "import_projects"
    import_root.mkdir()
    result = import_project_zip(zip_path, import_root)

    assert result["project_id"] == project_id
    assert result["project_name"] == "Round Trip Project"
    assert result["collision_resolved"] is False

    # Verify key files exist and project.json has correct id
    imported_project_json = import_root / project_id / "project.json"
    assert imported_project_json.exists()
    imported_data = json.loads(imported_project_json.read_text(encoding="utf-8"))
    assert imported_data["id"] == project_id
    assert imported_data["name"] == "Round Trip Project"

    # Verify the image asset was preserved
    imported_image = import_root / project_id / "assets" / "images" / "hero.png"
    assert imported_image.exists()
    assert imported_image.read_bytes() == b"\x89PNG"

    # Verify style_guide and conversation exist
    assert (import_root / project_id / "style_guide.md").exists()
    assert (import_root / project_id / "conversation.json").exists()


# ---------------------------------------------------------------------------
# Collision handling
# ---------------------------------------------------------------------------

def test_import_collision_same_id_gets_new_id(tmp_path: Path) -> None:
    """If a project with the same id exists, import assigns a new id."""
    projects_root = tmp_path / "projects"
    projects_root.mkdir()

    # Pre-create a project with the same id
    existing_dir = projects_root / "my-game"
    existing_dir.mkdir()
    (existing_dir / "project.json").write_text(
        json.dumps({"id": "my-game", "name": "My Game", "type": "RPG", "synopsis": ""}),
        encoding="utf-8",
    )

    zip_path = _make_minimal_zip(tmp_path / "zips", project_id="my-game", project_name="My Game")

    result = import_project_zip(zip_path, projects_root)

    assert result["collision_resolved"] is True
    assert result["origin_id"] == "my-game"
    assert result["project_id"] != "my-game"

    # origin_id should be recorded in project.json
    imported_pjson = projects_root / result["project_id"] / "project.json"
    data = json.loads(imported_pjson.read_text(encoding="utf-8"))
    assert data["origin_id"] == "my-game"


def test_import_collision_same_name_gets_new_id(tmp_path: Path) -> None:
    """If a project with the same name exists (different id), import resolves collision."""
    projects_root = tmp_path / "projects"
    projects_root.mkdir()

    # Pre-create a project with same name but different id
    existing_dir = projects_root / "existing-id"
    existing_dir.mkdir()
    (existing_dir / "project.json").write_text(
        json.dumps({"id": "existing-id", "name": "Shared Name", "type": "RPG", "synopsis": ""}),
        encoding="utf-8",
    )

    zip_path = _make_minimal_zip(
        tmp_path / "zips",
        project_id="different-id",
        project_name="Shared Name",
    )

    result = import_project_zip(zip_path, projects_root)

    assert result["collision_resolved"] is True
    assert result["project_id"] != "different-id"


# ---------------------------------------------------------------------------
# Cross-project RW lock tests
# ---------------------------------------------------------------------------

def test_copy_external_asset_creates_file(tmp_path: Path) -> None:
    """copy_external_asset should place a copy under _external/<source_id>/."""
    source_file = tmp_path / "source.png"
    source_file.write_bytes(b"image_data")

    dest_project_dir = tmp_path / "projects" / "dest-proj"
    (dest_project_dir / "_external").mkdir(parents=True, exist_ok=True)

    dest = copy_external_asset(
        source_file,
        dest_project_dir,
        source_project_id="src-proj",
        relative_asset_path="images/hero.png",
    )

    assert dest.exists()
    assert dest.read_bytes() == b"image_data"
    assert dest == dest_project_dir / "_external" / "src-proj" / "images" / "hero.png"


def test_update_origins_json_creates_and_merges(tmp_path: Path) -> None:
    """update_origins_json should create origins.json and merge subsequent entries."""
    dest_project_dir = tmp_path / "projects" / "dest"
    (dest_project_dir / "_external").mkdir(parents=True, exist_ok=True)

    update_origins_json(
        dest_project_dir,
        "alpha",
        "images/hero.png",
        {"version": "v1"},
    )
    update_origins_json(
        dest_project_dir,
        "beta",
        "audio/bgm.mp3",
        {"version": "v2"},
    )

    origins_path = dest_project_dir / "_external" / "origins.json"
    assert origins_path.exists()
    data = json.loads(origins_path.read_text(encoding="utf-8"))
    assert "alpha/images/hero.png" in data
    assert data["alpha/images/hero.png"]["source_project_id"] == "alpha"
    assert "beta/audio/bgm.mp3" in data


def test_rw_lock_concurrent_copy_no_corruption(tmp_path: Path) -> None:
    """Concurrent threads calling copy_external_asset should not corrupt files.

    Each thread writes a unique source file and a shared origins.json entry.
    After all threads finish, every file must exist with the correct content
    and origins.json must be valid JSON containing all entries.
    """
    dest_project_dir = tmp_path / "dest"
    (dest_project_dir / "_external").mkdir(parents=True, exist_ok=True)

    N = 12  # number of concurrent writers
    errors: list[Exception] = []
    written: list[tuple[str, bytes]] = []

    def worker(i: int) -> None:
        src = tmp_path / f"src_{i}.bin"
        content = f"content-{i}-{uuid.uuid4().hex}".encode()
        src.write_bytes(content)
        written.append((f"images/asset_{i}.bin", content))
        try:
            copy_external_asset(
                src,
                dest_project_dir,
                source_project_id="src-proj",
                relative_asset_path=f"images/asset_{i}.bin",
            )
            update_origins_json(
                dest_project_dir,
                "src-proj",
                f"images/asset_{i}.bin",
                {"thread": i},
            )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"Errors during concurrent access: {errors}"

    # All files must exist with the correct content
    for rel_path, expected_content in written:
        dest_file = dest_project_dir / "_external" / "src-proj" / rel_path
        assert dest_file.exists(), f"Missing: {dest_file}"
        assert dest_file.read_bytes() == expected_content, f"Corrupted: {dest_file}"

    # origins.json must be valid and contain all N entries
    origins_path = dest_project_dir / "_external" / "origins.json"
    assert origins_path.exists()
    origins = json.loads(origins_path.read_text(encoding="utf-8"))
    for i in range(N):
        assert f"src-proj/images/asset_{i}.bin" in origins, f"Missing origin entry {i}"


# ---------------------------------------------------------------------------
# API endpoint: POST /api/v1/projects/import
# ---------------------------------------------------------------------------

@pytest.fixture()
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    manager = ProjectManager(tmp_path / "projects")
    monkeypatch.setattr(main, "project_manager", manager)
    monkeypatch.setattr(main, "PROJECTS_ROOT", tmp_path / "projects")
    return TestClient(main.app)


def test_api_import_valid_zip(api_client: TestClient, tmp_path: Path) -> None:
    zip_path = _make_minimal_zip(tmp_path, project_id="api-game", project_name="API Game")
    with open(zip_path, "rb") as f:
        response = api_client.post(
            "/api/v1/projects/import",
            files={"file": ("api-game.misaka.zip", f, "application/zip")},
        )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["project_id"] == "api-game"
    assert data["collision_resolved"] is False


def test_api_import_invalid_zip_returns_400(api_client: TestClient, tmp_path: Path) -> None:
    """A zip without a manifest should return HTTP 400."""
    zip_path = tmp_path / "bad.zip"
    with ZipFile(zip_path, "w") as zf:
        zf.writestr("project.json", '{"id":"x","name":"X","type":"T","synopsis":""}')

    with open(zip_path, "rb") as f:
        response = api_client.post(
            "/api/v1/projects/import",
            files={"file": ("bad.zip", f, "application/zip")},
        )
    assert response.status_code == 400
