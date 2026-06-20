"""API-level tests for GET /api/v1/projects/{project_id}/assets/{asset_id}/file (M5.9).

Covers:
- 200 + correct bytes served for a real asset.
- 404 for unknown project / unknown asset / file missing on disk.
- SECURITY: asset whose resolved path escapes the assets root is refused (404),
  not served.  Mirrors the traversal-test style from test_cross_project_resolver.py.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import core.main as main
from core.project.manager import ProjectManager


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    manager = ProjectManager(tmp_path / "projects")
    monkeypatch.setattr(main, "project_manager", manager)
    monkeypatch.setattr(main.generation_service, "project_manager", manager)
    return TestClient(main.app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_project(client: TestClient) -> str:
    resp = client.post("/api/v1/projects", json={"name": "TestProj", "type": "RPG", "synopsis": "s"})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["project"]["id"]


def _import_image(client: TestClient, project_id: str, content: bytes = b"PNGDATA") -> str:
    """Import a PNG asset and return its asset_id."""
    resp = client.post(
        f"/api/v1/projects/{project_id}/assets/import",
        files={"file": ("test.png", io.BytesIO(content), "image/png")},
        data={"modality": "image", "asset_type": "image", "title": "Test"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["assets"][-1]["id"]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_serve_existing_asset_returns_200_and_correct_bytes(client: TestClient) -> None:
    """200 with the exact bytes that were imported."""
    project_id = _create_project(client)
    content = b"\x89PNG\r\n\x1a\nFAKEPNG"
    asset_id = _import_image(client, project_id, content)

    resp = client.get(f"/api/v1/projects/{project_id}/assets/{asset_id}/file")
    assert resp.status_code == 200, resp.text
    assert resp.content == content


def test_serve_existing_asset_has_correct_media_type(client: TestClient) -> None:
    """image/png media_type is inferred from the .png extension."""
    project_id = _create_project(client)
    asset_id = _import_image(client, project_id)

    resp = client.get(f"/api/v1/projects/{project_id}/assets/{asset_id}/file")
    assert resp.status_code == 200, resp.text
    assert "image/png" in resp.headers["content-type"]


def test_serve_asset_has_inline_content_disposition(client: TestClient) -> None:
    """Content-Disposition must be inline so browsers render images."""
    project_id = _create_project(client)
    asset_id = _import_image(client, project_id)

    resp = client.get(f"/api/v1/projects/{project_id}/assets/{asset_id}/file")
    assert resp.status_code == 200, resp.text
    disp = resp.headers.get("content-disposition", "")
    assert disp.startswith("inline"), f"Expected inline disposition, got: {disp!r}"


# ---------------------------------------------------------------------------
# 404 paths
# ---------------------------------------------------------------------------

def test_unknown_project_returns_404(client: TestClient) -> None:
    resp = client.get("/api/v1/projects/no-such-project/assets/no-such-asset/file")
    assert resp.status_code == 404


def test_unknown_asset_returns_404(client: TestClient) -> None:
    project_id = _create_project(client)
    resp = client.get(f"/api/v1/projects/{project_id}/assets/nonexistent-asset-id/file")
    assert resp.status_code == 404


def test_missing_file_on_disk_returns_404(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asset record exists in index but the physical file has been deleted."""
    project_id = _create_project(client)
    asset_id = _import_image(client, project_id, b"data")

    # Locate the project_dir through the project manager and delete the file.
    manager: ProjectManager = main.project_manager  # type: ignore[attr-defined]
    _, project_dir = manager.get_project(project_id)
    assets_index = project_dir / "assets" / "index.json"
    payload = json.loads(assets_index.read_text(encoding="utf-8"))
    asset_rec = next(a for a in payload["assets"] if a["id"] == asset_id)
    (project_dir / asset_rec["path"]).unlink()

    resp = client.get(f"/api/v1/projects/{project_id}/assets/{asset_id}/file")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# SECURITY: path-traversal must be refused (NEVER served)
# ---------------------------------------------------------------------------

def test_asset_path_escaping_assets_root_returns_404(
    client: TestClient, tmp_path: Path
) -> None:
    """SECURITY: an AssetRecord whose stored path resolves OUTSIDE project_dir/assets/
    must be refused with 404, not served.

    Attack scenario: a tampered or adversarially crafted assets/index.json contains
    a record with path='../SECRET.txt' pointing at a file outside the assets/ subtree.
    The route must detect the escape via resolve().relative_to(assets_root) and return 404.
    """
    project_id = _create_project(client)

    # Obtain project_dir via the live project manager.
    manager: ProjectManager = main.project_manager  # type: ignore[attr-defined]
    _, project_dir = manager.get_project(project_id)

    # Plant a "secret" file one level above assets/ (still inside project_dir).
    secret = project_dir / "SECRET.txt"
    secret.write_bytes(b"should-never-be-served")

    # Also import a legitimate asset so the project has an asset index file.
    legit_asset_id = _import_image(client, project_id, b"legit")

    # Inject a malicious record into the assets index whose path traverses
    # upward from assets/ to reach SECRET.txt.
    assets_index = project_dir / "assets" / "index.json"
    payload = json.loads(assets_index.read_text(encoding="utf-8"))
    evil_id = "evil-traversal-asset"
    payload["assets"].append({
        "id": evil_id,
        "job_id": None,
        "modality": "image",
        "asset_type": "image",
        "title": "Evil",
        "path": "../SECRET.txt",   # escapes assets/ root
        "description": "",
        "parent_version_id": None,
        "refine_strategy": None,
        "mask_asset_id": None,
        "prompt_delta": None,
        "param_delta": {},
        "prompt_hash": None,
        "backend": None,
        "params": {},
        "tags": [],
        "user_note": None,
        "is_favorite": False,
        "created_at": "2024-01-01T00:00:00+00:00",
    })
    assets_index.write_text(json.dumps(payload), encoding="utf-8")

    resp = client.get(f"/api/v1/projects/{project_id}/assets/{evil_id}/file")
    assert resp.status_code == 404, (
        f"Path-traversal escape was NOT blocked — route returned {resp.status_code}; "
        f"body: {resp.text}"
    )
    # The secret file must still exist untouched (not served/consumed).
    assert secret.exists(), "Secret file was unexpectedly removed."


def test_asset_path_absolute_outside_root_returns_404(
    client: TestClient, tmp_path: Path
) -> None:
    """SECURITY: an AssetRecord with an absolute path outside the project is refused."""
    project_id = _create_project(client)
    manager: ProjectManager = main.project_manager  # type: ignore[attr-defined]
    _, project_dir = manager.get_project(project_id)

    # Plant a file completely outside the project directory.
    outside_file = tmp_path / "outside.png"
    outside_file.write_bytes(b"outside-data")

    # Import a legit asset to seed the index file.
    _import_image(client, project_id, b"legit")

    # Inject a record with an absolute path that escapes the project root.
    assets_index = project_dir / "assets" / "index.json"
    payload = json.loads(assets_index.read_text(encoding="utf-8"))
    evil_id = "evil-absolute-asset"
    payload["assets"].append({
        "id": evil_id,
        "job_id": None,
        "modality": "image",
        "asset_type": "image",
        "title": "Evil Absolute",
        "path": str(outside_file),   # absolute path outside project
        "description": "",
        "parent_version_id": None,
        "refine_strategy": None,
        "mask_asset_id": None,
        "prompt_delta": None,
        "param_delta": {},
        "prompt_hash": None,
        "backend": None,
        "params": {},
        "tags": [],
        "user_note": None,
        "is_favorite": False,
        "created_at": "2024-01-01T00:00:00+00:00",
    })
    assets_index.write_text(json.dumps(payload), encoding="utf-8")

    resp = client.get(f"/api/v1/projects/{project_id}/assets/{evil_id}/file")
    assert resp.status_code == 404, (
        f"Absolute-path escape was NOT blocked — route returned {resp.status_code}; "
        f"body: {resp.text}"
    )


# ---------------------------------------------------------------------------
# REGRESSION: CJK filename must not cause 500
# ---------------------------------------------------------------------------

def test_cjk_filename_returns_200_not_500(client: TestClient) -> None:
    """Regression: GET .../file must return 200 (not 500) when the stored filename
    contains CJK characters.  Root cause: the old hand-built header
        {"Content-Disposition": f'inline; filename="{file_path.name}"'}
    passes a CJK string to Starlette which encodes headers as latin-1 →
    UnicodeEncodeError → HTTP 500.
    Fix: use FileResponse(filename=..., content_disposition_type="inline") so
    Starlette applies RFC 5987 percent-encoding automatically.
    """
    project_id = _create_project(client)
    cjk_filename = "動漫女僕魔女系列_001.png"
    content = b"\x89PNG\r\n\x1a\nFAKECJKPNG"
    resp = client.post(
        f"/api/v1/projects/{project_id}/assets/import",
        files={"file": (cjk_filename, io.BytesIO(content), "image/png")},
        data={"modality": "image", "asset_type": "image", "title": "CJK Test"},
    )
    assert resp.status_code == 200, resp.text
    asset_id = resp.json()["data"]["assets"][-1]["id"]

    resp = client.get(f"/api/v1/projects/{project_id}/assets/{asset_id}/file")
    assert resp.status_code == 200, (
        f"CJK filename caused non-200 response: {resp.status_code}; body: {resp.text[:300]}"
    )
    assert resp.content == content, "Response bytes differ from what was imported"
    disp = resp.headers.get("content-disposition", "")
    assert disp.startswith("inline"), f"Expected inline disposition, got: {disp!r}"
    # RFC 5987: CJK names must appear as filename*=utf-8''<percent-encoded>
    assert "filename*=" in disp.lower(), (
        f"CJK filename must be RFC 5987 encoded (filename*=UTF-8''...) in Content-Disposition; got: {disp!r}"
    )
