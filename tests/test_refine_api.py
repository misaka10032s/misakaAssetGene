"""API-level test for the asset refine route (spec §5.11 / §6.2).

Drives the FastAPI TestClient against a temporary projects root and seeds an
image asset via the import endpoint, then verifies the refine route produces a
lineage-bearing refine job. The ComfyUI worker is not started, so the job is
expected to come back BLOCKED on worker readiness rather than executing.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import core.main as main
from core.project.manager import ProjectManager


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    manager = ProjectManager(tmp_path / "projects")
    monkeypatch.setattr(main, "project_manager", manager)
    monkeypatch.setattr(main.generation_service, "project_manager", manager)
    return TestClient(main.app)


def _create_project(client: TestClient) -> str:
    response = client.post("/api/v1/projects", json={"name": "Demo", "type": "RPG", "synopsis": "s"})
    assert response.status_code == 200, response.text
    return response.json()["data"]["project"]["id"]


def _import_image(client: TestClient, project_id: str) -> str:
    response = client.post(
        f"/api/v1/projects/{project_id}/assets/import",
        files={"file": ("base.png", io.BytesIO(b"PNG"), "image/png")},
        data={"modality": "image", "asset_type": "image", "title": "Base"},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["assets"][-1]["id"]


def test_refine_endpoint_creates_lineage_job(client: TestClient) -> None:
    project_id = _create_project(client)
    asset_id = _import_image(client, project_id)

    response = client.post(
        f"/api/v1/projects/{project_id}/assets/{asset_id}/refine",
        json={"instruction": "讓氛圍更溫暖一點，手再抬高一點"},
    )
    assert response.status_code == 200, response.text
    jobs = response.json()["data"]["jobs"]
    refine_job = next(j for j in jobs if j["parent_asset_id"] == asset_id)
    assert refine_job["refine_strategy"] == "img2img"
    assert refine_job["recipe"] == "img2img"
    assert refine_job["source_asset_id"] == asset_id


def test_refine_unknown_asset_returns_404(client: TestClient) -> None:
    project_id = _create_project(client)
    response = client.post(
        f"/api/v1/projects/{project_id}/assets/missing/refine",
        json={"instruction": "調暖一點"},
    )
    assert response.status_code == 404


def test_metadata_only_refine_completes(client: TestClient) -> None:
    project_id = _create_project(client)
    asset_id = _import_image(client, project_id)
    response = client.post(
        f"/api/v1/projects/{project_id}/assets/{asset_id}/refine",
        json={"instruction": "標記為最愛並加上 #warm 標籤"},
    )
    assert response.status_code == 200, response.text
    jobs = response.json()["data"]["jobs"]
    job = next(j for j in jobs if j["parent_asset_id"] == asset_id)
    assert job["refine_strategy"] == "metadata_only"
    assert job["status"] == "completed"
