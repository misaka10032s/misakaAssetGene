"""Security tests for project_id path validation (whitelist ^[a-z0-9_-]+$).

Covers the route-layer FastAPI dependency (``enforce_valid_project_id``) and the
defensive check inside ``ProjectManager.get_project``. A crafted ``project_id``
containing ``..`` or path separators must be REFUSED before it is ever joined
onto ``projects_root`` — otherwise it could traverse out of the projects
directory (and make the asset-file route's assets_root guard a circular
defence).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

import core.main as main
from core.project.manager import (
    ProjectManager,
    ProjectNotFoundError,
    ProjectValidationError,
    validate_project_id,
)


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    manager = ProjectManager(tmp_path / "projects")
    monkeypatch.setattr(main, "project_manager", manager)
    monkeypatch.setattr(main.generation_service, "project_manager", manager)
    return TestClient(main.app)


# ---------------------------------------------------------------------------
# Unit: the shared validator
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "good",
    ["adventure-rpg", "proj_001", "a", "char-kyuoka-v3", "0", "abc-123_xyz"],
)
def test_validate_accepts_whitelisted_ids(good: str) -> None:
    assert validate_project_id(good) == good


@pytest.mark.parametrize(
    "bad",
    [
        "..",
        "../secret",
        "../../etc/passwd",
        "a/../../b",
        "foo/bar",
        "foo\\bar",
        "/abs/path",
        "C:\\Windows",
        "Upper",          # uppercase not allowed (ids are lower-cased)
        "with space",
        "dot.name",
        "tilde~",
        "nul\x00byte",
        "",
        "中文",
    ],
)
def test_validate_rejects_traversal_and_separators(bad: str) -> None:
    with pytest.raises(ProjectValidationError):
        validate_project_id(bad)


# ---------------------------------------------------------------------------
# Manager-level defensive check (covers body-sourced ids too)
# ---------------------------------------------------------------------------

def test_get_project_rejects_traversal_before_filesystem(tmp_path: Path) -> None:
    manager = ProjectManager(tmp_path / "projects")
    # Plant a project.json two levels up to prove the traversal would succeed
    # if the id were joined naively.
    outside = tmp_path / "project.json"
    outside.write_text('{"id":"x","name":"x","type":"RPG","synopsis":""}', encoding="utf-8")
    with pytest.raises(ProjectValidationError):
        manager.get_project("../..")


# ---------------------------------------------------------------------------
# Route-layer dependency (path param)
# ---------------------------------------------------------------------------

def _create_project(client: TestClient) -> str:
    resp = client.post("/api/v1/projects", json={"name": "TestProj", "type": "RPG", "synopsis": "s"})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["project"]["id"]


def test_route_rejects_dotdot_segment(client: TestClient) -> None:
    # URL-encoded ../ so it reaches the path param rather than collapsing in
    # the router. Starlette decodes %2F into the segment value.
    resp = client.get("/api/v1/projects/..%2F..%2Fsecret")
    assert resp.status_code == 404, resp.text


def test_route_rejects_separator_in_workspace(client: TestClient) -> None:
    resp = client.get("/api/v1/projects/foo%2Fbar/workspace")
    assert resp.status_code == 404, resp.text


def test_route_still_serves_valid_project(client: TestClient) -> None:
    project_id = _create_project(client)
    resp = client.get(f"/api/v1/projects/{project_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["project"]["id"] == project_id


def test_unknown_but_wellformed_id_is_404(client: TestClient) -> None:
    resp = client.get("/api/v1/projects/no-such-project")
    assert resp.status_code == 404, resp.text
