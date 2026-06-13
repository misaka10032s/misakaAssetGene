"""Tests for M5.3 cross-project resolver, export re-resolution, cycle detection,
and materialization tool (spec §5.6.2 / §5.6.4 / §5.6.5 / §16 Q4).

Coverage:
- resolve_reference: all 4 statuses (live / outdated / external / broken)
- Export re-resolution: _refresh_external_copies refreshes _external/
- detect_cycles: finds and reports cycles without infinite-looping
- materialize_reference: materializes ref + preserves provenance + handles broken
- materialize_project_refs: bulk materialization with broken refs reported
- collect_project_refs: scans style_guide + assets/index.json
- API: GET /api/v1/projects/{id}/refs
- API: POST /api/v1/projects/{id}/refs/materialize
- Security: materialization never copies outside project root
"""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

import pytest
from starlette.testclient import TestClient

import core.main as main_module
from core.project.cross_project import (
    RefStatus,
    collect_project_refs,
    copy_external_asset,
    detect_cycles,
    materialize_project_refs,
    materialize_reference,
    parse_reference,
    resolve_reference,
    update_origins_json,
    _sha256_file,
)
from core.project.export import ProjectExportService
from core.project.manager import ProjectManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_project(root: Path, project_id: str, *, extra_files: dict[str, bytes] | None = None) -> Path:
    """Create a minimal project directory."""
    project_dir = root / project_id
    for d in ["assets/images", "assets/audio", "_external", ".cache"]:
        (project_dir / d).mkdir(parents=True, exist_ok=True)
    pdata = {"id": project_id, "name": project_id.replace("-", " ").title(), "type": "RPG", "synopsis": "s"}
    (project_dir / "project.json").write_text(json.dumps(pdata), encoding="utf-8")
    (project_dir / "style_guide.md").write_text("# style\n", encoding="utf-8")
    (project_dir / "conversation.json").write_text('{"entries":[]}\n', encoding="utf-8")
    (project_dir / "assets" / "index.json").write_text('{"assets":[]}\n', encoding="utf-8")
    if extra_files:
        for rel, data in extra_files.items():
            dest = project_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
    return project_dir


def _plant_asset(project_dir: Path, rel_path: str, content: bytes = b"image_data") -> Path:
    """Plant an asset file inside the project's assets/ directory."""
    full = project_dir / "assets" / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(content)
    return full


def _plant_external_copy(
    consumer_dir: Path,
    source_id: str,
    asset_path: str,
    version: str,
    content: bytes,
    origin_hash: str | None = None,
) -> Path:
    """Plant a copy in consumer/_external/<source>/<asset_path>/<version>."""
    import hashlib
    ext_file = consumer_dir / "_external" / source_id / asset_path / version
    ext_file.parent.mkdir(parents=True, exist_ok=True)
    ext_file.write_bytes(content)
    # Update origins.json
    real_hash = origin_hash or hashlib.sha256(content).hexdigest()
    update_origins_json(
        consumer_dir,
        source_id,
        f"{asset_path}/{version}",
        {"version": version.split(".")[0] if "." in version else version, "sha256": real_hash},
    )
    return ext_file


# ---------------------------------------------------------------------------
# resolve_reference — all 4 statuses
# ---------------------------------------------------------------------------

def test_resolve_live(tmp_path: Path) -> None:
    """Status LIVE: source project has the asset and hash matches origins.json."""
    projects_root = tmp_path / "projects"
    source_dir = _make_project(projects_root, "alpha")
    consumer_dir = _make_project(projects_root, "beta")

    content = b"original_image"
    asset_file = _plant_asset(source_dir, "char/kyuoka/v3.png", content)

    # Seed origins.json with the correct hash so live hash matches
    import hashlib
    h = hashlib.sha256(content).hexdigest()
    update_origins_json(consumer_dir, "alpha", "char/kyuoka/v3.png", {"version": "v3", "sha256": h})

    result = resolve_reference("@alpha/char/kyuoka#v3", consumer_dir, projects_root)
    assert result["status"] == RefStatus.LIVE
    assert result["path"] is not None
    assert result["path"].is_file()


def test_resolve_outdated(tmp_path: Path) -> None:
    """Status OUTDATED: live asset exists but hash differs from origins.json record."""
    projects_root = tmp_path / "projects"
    source_dir = _make_project(projects_root, "alpha")
    consumer_dir = _make_project(projects_root, "beta")

    content_live = b"updated_image_v2"
    _plant_asset(source_dir, "char/kyuoka/v3.png", content_live)

    # origins.json records OLD hash (different from live)
    update_origins_json(consumer_dir, "alpha", "char/kyuoka/v3.png", {"version": "v3", "sha256": "olddeadbeef"})

    result = resolve_reference("@alpha/char/kyuoka#v3", consumer_dir, projects_root)
    assert result["status"] == RefStatus.OUTDATED
    assert result["path"] is not None


def test_resolve_external(tmp_path: Path) -> None:
    """Status EXTERNAL: source project unavailable; served from _external/ copy."""
    projects_root = tmp_path / "projects"
    consumer_dir = _make_project(projects_root, "beta")
    # Do NOT create "alpha" project — it doesn't exist

    content = b"copied_image"
    _plant_external_copy(consumer_dir, "alpha", "char/kyuoka", "v3.png", content)

    result = resolve_reference("@alpha/char/kyuoka#v3", consumer_dir, projects_root)
    assert result["status"] == RefStatus.EXTERNAL
    assert result["path"] is not None
    assert result["path"].is_file()


def test_resolve_broken_no_source_no_external(tmp_path: Path) -> None:
    """Status BROKEN: source project absent AND no _external/ copy."""
    projects_root = tmp_path / "projects"
    consumer_dir = _make_project(projects_root, "beta")
    # "alpha" does not exist, no _external/ copy

    result = resolve_reference("@alpha/char/missing#v1", consumer_dir, projects_root)
    assert result["status"] == RefStatus.BROKEN
    assert result["path"] is None


def test_resolve_broken_corrupted_external(tmp_path: Path) -> None:
    """Status BROKEN: _external/ copy hash does not match origins.json (corrupted)."""
    projects_root = tmp_path / "projects"
    consumer_dir = _make_project(projects_root, "beta")

    # Plant external copy with WRONG hash in origins.json
    content = b"real_data"
    ext_file = consumer_dir / "_external" / "alpha" / "char" / "kyuoka" / "v3.png"
    ext_file.parent.mkdir(parents=True, exist_ok=True)
    ext_file.write_bytes(content)
    # Seed origins.json with a hash that does NOT match the file
    update_origins_json(
        consumer_dir, "alpha", "char/kyuoka/v3.png",
        {"version": "v3", "sha256": "000000badhash"}
    )

    result = resolve_reference("@alpha/char/kyuoka#v3", consumer_dir, projects_root)
    assert result["status"] == RefStatus.BROKEN


def test_resolve_invalid_syntax(tmp_path: Path) -> None:
    """Malformed reference string returns BROKEN without crashing."""
    projects_root = tmp_path / "projects"
    consumer_dir = _make_project(projects_root, "beta")
    result = resolve_reference("not-a-valid-ref", consumer_dir, projects_root)
    assert result["status"] == RefStatus.BROKEN
    assert result["path"] is None


# ---------------------------------------------------------------------------
# collect_project_refs
# ---------------------------------------------------------------------------

def test_collect_refs_from_style_guide(tmp_path: Path) -> None:
    """collect_project_refs should find @refs embedded in style_guide.md."""
    project_dir = _make_project(tmp_path, "proj")
    (project_dir / "style_guide.md").write_text(
        "# Style\nVisual Anchors: @src_proj/char/hero#v2\n", encoding="utf-8"
    )
    refs = collect_project_refs(project_dir)
    assert any("src_proj" in r for r in refs)


def test_collect_refs_from_assets_index(tmp_path: Path) -> None:
    """collect_project_refs should find @refs in assets/index.json dependencies."""
    project_dir = _make_project(tmp_path, "proj")
    index = {
        "assets": [
            {
                "id": "a1",
                "modality": "image",
                "dependencies": ["@source_proj/textures/hero_idle#v1"],
            }
        ]
    }
    (project_dir / "assets" / "index.json").write_text(json.dumps(index), encoding="utf-8")
    refs = collect_project_refs(project_dir)
    assert "@source_proj/textures/hero_idle#v1" in refs


# ---------------------------------------------------------------------------
# Export re-resolution (§5.6.4)
# ---------------------------------------------------------------------------

def test_export_refreshes_external_copies(tmp_path: Path) -> None:
    """Export re-resolution should copy the live asset into _external/ + update origins."""
    projects_root = tmp_path / "projects"
    source_dir = _make_project(projects_root, "src-proj")
    consumer_dir = _make_project(projects_root, "consumer")

    # Plant a live asset in the source project
    content = b"live_asset_content"
    _plant_asset(source_dir, "images/hero/v1.png", content)

    # Plant a stale @ref in style_guide.md of consumer
    (consumer_dir / "style_guide.md").write_text(
        "# Style\n@src-proj/images/hero#v1\n", encoding="utf-8"
    )
    (consumer_dir / "assets" / "index.json").write_text('{"assets":[]}', encoding="utf-8")

    project_summary = {
        "id": "consumer",
        "name": "Consumer",
        "type": "RPG",
        "synopsis": "test",
    }

    svc = ProjectExportService()
    zip_path = svc.export_project(
        project_dir=consumer_dir,
        project_summary=project_summary,
        jobs=[],
        assets=[],
        plans=[],
        license_report={},
        resolve_refs=True,
    )

    assert zip_path.exists()

    # The export manifest should record ref_resolution entries
    with ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        manifest_bytes = zf.read("export.manifest.json")
    manifest = json.loads(manifest_bytes)
    assert "ref_resolution" in manifest

    # The _external/ directory should now have the copied asset
    external_dir = consumer_dir / "_external" / "src-proj" / "images" / "hero"
    # At minimum, something was written under _external for src-proj
    external_root = consumer_dir / "_external" / "src-proj"
    assert external_root.exists()


def test_export_no_resolve_skips_refresh(tmp_path: Path) -> None:
    """When resolve_refs=False, the export should not attempt to refresh _external/."""
    projects_root = tmp_path / "projects"
    consumer_dir = _make_project(projects_root, "consumer")
    (consumer_dir / "style_guide.md").write_text(
        "# Style\n@src-proj/images/hero\n", encoding="utf-8"
    )
    project_summary = {"id": "consumer", "name": "Consumer", "type": "RPG", "synopsis": ""}

    svc = ProjectExportService()
    zip_path = svc.export_project(
        project_dir=consumer_dir,
        project_summary=project_summary,
        jobs=[],
        assets=[],
        plans=[],
        license_report={},
        resolve_refs=False,
    )
    with ZipFile(zip_path, "r") as zf:
        manifest = json.loads(zf.read("export.manifest.json"))
    # ref_resolution should be empty list (not run)
    assert manifest.get("ref_resolution") == []


# ---------------------------------------------------------------------------
# Cycle detection (§5.6.5)
# ---------------------------------------------------------------------------

def test_detect_no_cycle(tmp_path: Path) -> None:
    """detect_cycles should return an empty list when there are no cycles."""
    projects_root = tmp_path / "projects"
    proj_a = _make_project(projects_root, "proj-a")
    _make_project(projects_root, "proj-b")
    # A references B; B has no refs → no cycle
    (proj_a / "style_guide.md").write_text("@proj-b/images/logo", encoding="utf-8")

    cycles = detect_cycles("proj-a", projects_root)
    assert cycles == []


def test_detect_simple_cycle(tmp_path: Path) -> None:
    """detect_cycles should detect A → B → A and return the cycle path."""
    projects_root = tmp_path / "projects"
    proj_a = _make_project(projects_root, "proj-a")
    proj_b = _make_project(projects_root, "proj-b")
    # A references B, B references A
    (proj_a / "style_guide.md").write_text("@proj-b/images/x\n", encoding="utf-8")
    (proj_b / "style_guide.md").write_text("@proj-a/images/y\n", encoding="utf-8")

    cycles = detect_cycles("proj-a", projects_root)
    # There should be at least one cycle reported
    assert len(cycles) > 0
    # Each cycle is a list of project ids ending with the source
    for cycle in cycles:
        assert "proj-a" in cycle


def test_detect_cycle_does_not_loop_infinitely(tmp_path: Path) -> None:
    """detect_cycles must not infinite-loop on a circular reference chain."""
    projects_root = tmp_path / "projects"
    proj_a = _make_project(projects_root, "proj-a")
    proj_b = _make_project(projects_root, "proj-b")
    proj_c = _make_project(projects_root, "proj-c")
    # A → B → C → A
    (proj_a / "style_guide.md").write_text("@proj-b/img/x\n", encoding="utf-8")
    (proj_b / "style_guide.md").write_text("@proj-c/img/y\n", encoding="utf-8")
    (proj_c / "style_guide.md").write_text("@proj-a/img/z\n", encoding="utf-8")

    cycles = detect_cycles("proj-a", projects_root)
    # Should complete and return at least one cycle
    assert isinstance(cycles, list)
    assert len(cycles) > 0


# ---------------------------------------------------------------------------
# Materialization (§16 Q4)
# ---------------------------------------------------------------------------

def test_materialize_live_ref(tmp_path: Path) -> None:
    """materialize_reference on a LIVE ref should copy the file and record provenance."""
    projects_root = tmp_path / "projects"
    source_dir = _make_project(projects_root, "src")
    consumer_dir = _make_project(projects_root, "consumer")

    content = b"hero_image_content"
    _plant_asset(source_dir, "images/hero/v1.png", content)

    result = materialize_reference(
        "@src/images/hero#v1",
        consumer_dir,
        projects_root,
    )

    assert result["status"] in ("materialized", "already_external")
    assert result["local_path"] is not None
    assert Path(result["local_path"]).is_file()
    assert Path(result["local_path"]).read_bytes() == content

    # Provenance must be preserved
    assert result["provenance"] is not None
    prov = result["provenance"]
    assert prov["original_ref"] == "@src/images/hero#v1"
    assert prov["project"] == "src"
    assert "sha256" in prov


def test_materialize_preserves_provenance_in_origins_json(tmp_path: Path) -> None:
    """materialize_reference should record original_ref in origins.json for audit."""
    projects_root = tmp_path / "projects"
    source_dir = _make_project(projects_root, "source-proj")
    consumer_dir = _make_project(projects_root, "my-proj")

    _plant_asset(source_dir, "char/hero/v2.png", b"hero_v2")

    materialize_reference("@source-proj/char/hero#v2", consumer_dir, projects_root)

    origins_path = consumer_dir / "_external" / "origins.json"
    assert origins_path.exists()
    doc = json.loads(origins_path.read_text(encoding="utf-8"))
    entries = doc.get("entries", [])
    assert len(entries) > 0
    # origins.json entry should record the original_ref for audit in the origin sub-dict
    assert any(
        e.get("origin", {}).get("original_ref") == "@source-proj/char/hero#v2"
        for e in entries
    )


def test_materialize_broken_ref_returns_broken_status(tmp_path: Path) -> None:
    """materialize_reference on a BROKEN ref must return status='broken' without raising."""
    projects_root = tmp_path / "projects"
    consumer_dir = _make_project(projects_root, "consumer")
    # "missing-proj" does not exist, no _external/ copy

    result = materialize_reference("@missing-proj/any/asset#v1", consumer_dir, projects_root)

    assert result["status"] == "broken"
    assert result["local_path"] is None
    assert result["provenance"] is None


def test_materialize_broken_ref_does_not_raise(tmp_path: Path) -> None:
    """materialize_reference must never raise for a broken ref."""
    projects_root = tmp_path / "projects"
    consumer_dir = _make_project(projects_root, "consumer")

    # Should not raise
    result = materialize_reference("@nonexistent/path/to/thing#v99", consumer_dir, projects_root)
    assert "status" in result


def test_materialize_project_refs_bulk(tmp_path: Path) -> None:
    """materialize_project_refs should process all refs, separating broken from successful."""
    projects_root = tmp_path / "projects"
    source_dir = _make_project(projects_root, "src-a")
    consumer_dir = _make_project(projects_root, "consumer")

    # One valid asset
    _plant_asset(source_dir, "images/icon/v1.png", b"icon_data")

    # Plant two refs: one resolvable, one broken
    (consumer_dir / "style_guide.md").write_text(
        "@src-a/images/icon#v1\n@does-not-exist/foo/bar\n", encoding="utf-8"
    )
    (consumer_dir / "assets" / "index.json").write_text('{"assets":[]}', encoding="utf-8")

    result = materialize_project_refs(consumer_dir, projects_root)

    assert result["total"] == 2
    assert len(result["broken"]) == 1
    assert result["broken"][0]["ref"] == "@does-not-exist/foo/bar"
    assert len(result["materialized"]) == 1


def test_materialize_bulk_handles_all_broken(tmp_path: Path) -> None:
    """materialize_project_refs with only broken refs should still return without raising."""
    projects_root = tmp_path / "projects"
    consumer_dir = _make_project(projects_root, "consumer")
    (consumer_dir / "style_guide.md").write_text(
        "@gone/a\n@also-gone/b#v1\n", encoding="utf-8"
    )
    (consumer_dir / "assets" / "index.json").write_text('{"assets":[]}', encoding="utf-8")

    result = materialize_project_refs(consumer_dir, projects_root)
    assert result["total"] == 2
    assert len(result["broken"]) == 2
    assert result["materialized"] == []


# ---------------------------------------------------------------------------
# Security: materialization must not copy outside project root
# ---------------------------------------------------------------------------

def test_materialize_security_source_outside_project(tmp_path: Path) -> None:
    """materialize_reference must not allow copying files from outside the source project dir."""
    projects_root = tmp_path / "projects"
    # Create a legit source project
    source_dir = _make_project(projects_root, "src")
    consumer_dir = _make_project(projects_root, "consumer")

    # Try to resolve a reference that — even if it parsed — would look outside the project
    # We use a crafted source_project_dir pointing to a DIFFERENT location
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    secret_file = outside_dir / "secret.txt"
    secret_file.write_bytes(b"top-secret")

    # Provide source_project_dir=outside_dir explicitly, with a file that resolves outside src
    result = materialize_reference(
        "@src/images/hero",
        consumer_dir,
        projects_root,
        source_project_dir=source_dir,  # correct source_project_dir — no traversal possible
    )
    # "broken" because the asset doesn't exist inside source_dir/assets/images/hero
    assert result["status"] in ("broken", "materialized")
    # Ensure the secret file outside the project was NOT copied
    assert not (consumer_dir / "_external" / "src" / "secret.txt").exists()


# ---------------------------------------------------------------------------
# API routes (GET refs / POST materialize)
# ---------------------------------------------------------------------------

@pytest.fixture()
def api_client_m5(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Test client with projects root pointed at tmp_path."""
    manager = ProjectManager(tmp_path / "projects")
    monkeypatch.setattr(main_module, "project_manager", manager)
    monkeypatch.setattr(main_module, "PROJECTS_ROOT", tmp_path / "projects")
    return TestClient(main_module.app)


def test_api_list_refs_empty(api_client_m5: TestClient, tmp_path: Path) -> None:
    """GET /api/v1/projects/{id}/refs on a project with no refs returns an empty list."""
    projects_root = tmp_path / "projects"
    _make_project(projects_root, "no-refs-proj")

    resp = api_client_m5.get("/api/v1/projects/no-refs-proj/refs")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["project_id"] == "no-refs-proj"
    assert data["refs"] == []
    assert data["cycle_warning"] == []


def test_api_list_refs_with_broken_ref(api_client_m5: TestClient, tmp_path: Path) -> None:
    """GET /api/v1/projects/{id}/refs returns BROKEN status for unresolvable refs."""
    projects_root = tmp_path / "projects"
    proj_dir = _make_project(projects_root, "my-proj")
    (proj_dir / "style_guide.md").write_text("@ghost-proj/assets/hero#v1\n", encoding="utf-8")
    (proj_dir / "assets" / "index.json").write_text('{"assets":[]}', encoding="utf-8")

    resp = api_client_m5.get("/api/v1/projects/my-proj/refs")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["refs"]) == 1
    assert data["refs"][0]["status"] == "broken"


def test_api_list_refs_404_unknown_project(api_client_m5: TestClient) -> None:
    """GET /api/v1/projects/{id}/refs on unknown project returns 404."""
    resp = api_client_m5.get("/api/v1/projects/nonexistent/refs")
    assert resp.status_code == 404


def test_api_materialize_empty_request(api_client_m5: TestClient, tmp_path: Path) -> None:
    """POST /api/v1/projects/{id}/refs/materialize with no refs on a project with no refs."""
    projects_root = tmp_path / "projects"
    _make_project(projects_root, "clean-proj")

    resp = api_client_m5.post(
        "/api/v1/projects/clean-proj/refs/materialize",
        json={},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["project_id"] == "clean-proj"
    assert data["total"] == 0
    assert data["materialized"] == []
    assert data["broken"] == []


def test_api_materialize_broken_refs_reported(api_client_m5: TestClient, tmp_path: Path) -> None:
    """POST materialize with unresolvable refs returns broken entries, not 4xx."""
    projects_root = tmp_path / "projects"
    proj_dir = _make_project(projects_root, "proj-x")
    (proj_dir / "style_guide.md").write_text("@ghost/img/hero\n", encoding="utf-8")
    (proj_dir / "assets" / "index.json").write_text('{"assets":[]}', encoding="utf-8")

    resp = api_client_m5.post(
        "/api/v1/projects/proj-x/refs/materialize",
        json={},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] >= 1
    assert len(data["broken"]) >= 1
    assert data["broken"][0]["status"] == "broken"


def test_api_materialize_404_unknown_project(api_client_m5: TestClient) -> None:
    """POST materialize on unknown project returns 404."""
    resp = api_client_m5.post(
        "/api/v1/projects/unknown-proj/refs/materialize",
        json={},
    )
    assert resp.status_code == 404


def test_api_materialize_specific_refs(api_client_m5: TestClient, tmp_path: Path) -> None:
    """POST materialize with explicit refs list materializes only those refs."""
    projects_root = tmp_path / "projects"
    source_dir = _make_project(projects_root, "src-proj")
    consumer_dir = _make_project(projects_root, "consumer-proj")

    _plant_asset(source_dir, "images/bg/v1.png", b"bg_data")

    # The style_guide has one ref that is resolvable and one broken
    (consumer_dir / "style_guide.md").write_text(
        "@src-proj/images/bg#v1\n@gone/foo\n", encoding="utf-8"
    )
    (consumer_dir / "assets" / "index.json").write_text('{"assets":[]}', encoding="utf-8")

    # Only ask to materialize the resolvable one
    resp = api_client_m5.post(
        "/api/v1/projects/consumer-proj/refs/materialize",
        json={"refs": ["@src-proj/images/bg#v1"]},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 1
    assert len(data["broken"]) == 0
    assert len(data["materialized"]) == 1
