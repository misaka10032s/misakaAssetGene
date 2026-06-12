"""API-level tests for the consultant session routes.

Uses FastAPI's TestClient against a project manager and engine rebound to a
temporary projects root, so the suite never writes into the repo and never
requires a live LLM.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

import core.main as main
from core.consultant.engine import ConsultantEngine
from core.project.manager import ProjectManager


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    projects_root = tmp_path / "projects"
    manager = ProjectManager(projects_root)
    monkeypatch.setattr(main, "project_manager", manager)

    def resolver(project_id: str) -> Path:
        _, project_dir = manager.get_project(project_id)
        return project_dir

    monkeypatch.setattr(main, "consultant_engine", ConsultantEngine(sessions_path_resolver=resolver))
    return TestClient(main.app)


def _create_project(client: TestClient) -> str:
    response = client.post(
        "/api/v1/projects",
        json={"name": "Demo", "type": "RPG", "synopsis": "异世界冒险"},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["project"]["id"]


def test_start_and_resume_session_via_api(client: TestClient) -> None:
    project_id = _create_project(client)

    start = client.post(
        f"/api/v1/projects/{project_id}/consultant/session",
        json={"prompt": "畫出角色立繪", "modality": "image"},
    )
    assert start.status_code == 200, start.text
    session = start.json()["data"]["session"]
    assert session["state"] == "clarify"
    session_id = session["session_id"]

    resume = client.get(f"/api/v1/projects/{project_id}/consultant/session")
    assert resume.status_code == 200
    assert resume.json()["data"]["session"]["session_id"] == session_id


def test_advance_session_reaches_generate_with_plan(client: TestClient) -> None:
    project_id = _create_project(client)
    start = client.post(
        f"/api/v1/projects/{project_id}/consultant/session",
        json={"prompt": "生成角色的所有官方服裝立繪", "modality": "image"},
    )
    session_id = start.json()["data"]["session"]["session_id"]

    complete_slots = {"usage": "portrait", "resolution": "1024", "style": "anime", "background": "transparent"}
    advanced = client.post(
        f"/api/v1/projects/{project_id}/consultant/session/advance",
        json={"session_id": session_id, "slots": complete_slots},
    )
    assert advanced.json()["data"]["session"]["state"] == "summary"

    generated = client.post(
        f"/api/v1/projects/{project_id}/consultant/session/advance",
        json={"session_id": session_id, "slots": {}},
    )
    body = generated.json()["data"]
    assert body["session"]["state"] == "generate"
    assert body["session"]["plan"] is not None
    assert len(body["session"]["plan"]["execution_steps"]) > 0


def test_advance_unknown_session_returns_404(client: TestClient) -> None:
    project_id = _create_project(client)
    response = client.post(
        f"/api/v1/projects/{project_id}/consultant/session/advance",
        json={"session_id": "missing", "slots": {}},
    )
    assert response.status_code == 404
