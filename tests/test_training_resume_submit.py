"""Containment tests for the spec §7.3 resume-checkpoint submit path.

``TrainingJobCreateRequest.resume_checkpoint_path`` is a client-supplied value
that TrainingService.submit_job stores on the new job and the executor later
splices into a subprocess argv (core/training/lora.py build_lora_command,
``--resume <path>``). Because it comes from a client, it must be validated the
same way core/project/manager.py's ``validate_project_id`` /
core/main.py's ``enforce_valid_project_id`` treat project_id: resolved and
confined under a known-safe root before it is trusted at all.

These tests do NOT exercise the executor / live subprocess path (see
tests/test_training_resume.py's TestResumeCheckpointWiredThroughExecutor for
that) — they exercise only the validation boundary in submit_job and the
matching HTTP 400 at the API layer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

import core.main as main
from core.models.schemas import Modality, ProjectCreateRequest, TrainingJobCreateRequest
from core.project.manager import ProjectManager
from core.training.service import TrainingService, TrainingValidationError

# ---------------------------------------------------------------------------
# Service-level: TrainingService.submit_job containment
# ---------------------------------------------------------------------------

@pytest.fixture()
def service_with_project(tmp_path: Path) -> tuple[TrainingService, str, Path]:
    """A fresh TrainingService (no executor wired) with one real project on disk."""
    manager = ProjectManager(tmp_path / "projects")
    project = manager.create_project(
        ProjectCreateRequest(name="ResumeSubmitProj", type="RPG", synopsis="s")
    )
    project_dir = tmp_path / "projects" / project.id
    service = TrainingService(manager)  # executor=None: submit_job stops at PLANNED
    return service, project.id, project_dir


def _payload(dataset_path: str = "/data/ds", resume_checkpoint_path: str | None = None) -> TrainingJobCreateRequest:
    return TrainingJobCreateRequest(
        title="Resume test",
        modality=Modality.IMAGE,
        dataset_path=dataset_path,
        resume_checkpoint_path=resume_checkpoint_path,
    )


class TestSubmitJobResumePathContainment:
    def test_none_path_is_accepted_and_stays_none(
        self, service_with_project: tuple[TrainingService, str, Path]
    ) -> None:
        """Fresh submit (no resume_checkpoint_path) must be entirely unaffected."""
        service, project_id, _project_dir = service_with_project
        result = service.submit_job(project_id, _payload())
        assert result.jobs[-1].resume_checkpoint_path is None

    def test_valid_path_under_models_dir_is_accepted(
        self, service_with_project: tuple[TrainingService, str, Path]
    ) -> None:
        """A path that resolves to an existing directory under
        <project_dir>/models must be accepted and stored (resolved)."""
        service, project_id, project_dir = service_with_project
        checkpoint_dir = project_dir / "models" / "kyuoka_lora-state"
        checkpoint_dir.mkdir(parents=True)

        result = service.submit_job(project_id, _payload(resume_checkpoint_path=str(checkpoint_dir)))
        stored = result.jobs[-1].resume_checkpoint_path
        assert stored is not None
        assert Path(stored) == checkpoint_dir.resolve()

    def test_path_escaping_project_root_is_rejected(
        self, service_with_project: tuple[TrainingService, str, Path]
    ) -> None:
        """A path entirely outside the project directory must be rejected —
        this is the exact shape of value that would otherwise reach a
        subprocess argv verbatim."""
        service, project_id, project_dir = service_with_project
        outside_dir = project_dir.parent / "not-this-project" / "models" / "evil-state"
        outside_dir.mkdir(parents=True)

        with pytest.raises(TrainingValidationError):
            service.submit_job(project_id, _payload(resume_checkpoint_path=str(outside_dir)))

    def test_traversal_payload_resolving_outside_models_dir_is_rejected(
        self, service_with_project: tuple[TrainingService, str, Path]
    ) -> None:
        """A ``models/../../..`` style traversal payload must resolve outside
        the containment root and be rejected — the resolve()-then-relative_to()
        pattern must actually follow ``..`` segments, not just string-match."""
        service, project_id, project_dir = service_with_project
        models_dir = project_dir / "models"
        models_dir.mkdir(parents=True)
        traversal = f"{models_dir}/../../../etc"

        with pytest.raises(TrainingValidationError):
            service.submit_job(project_id, _payload(resume_checkpoint_path=traversal))

    def test_path_within_project_but_outside_models_dir_is_rejected(
        self, service_with_project: tuple[TrainingService, str, Path]
    ) -> None:
        """A path under the project dir but NOT under models/ (e.g. assets/)
        must still be rejected — containment root is models/, not the whole
        project directory."""
        service, project_id, project_dir = service_with_project
        sibling_dir = project_dir / "assets" / "images"
        sibling_dir.mkdir(parents=True, exist_ok=True)

        with pytest.raises(TrainingValidationError):
            service.submit_job(project_id, _payload(resume_checkpoint_path=str(sibling_dir)))

    def test_nonexistent_path_under_models_dir_is_rejected(
        self, service_with_project: tuple[TrainingService, str, Path]
    ) -> None:
        """A syntactically-contained path that does not actually exist on disk
        must be rejected — resume can only target a real, previously-discovered
        checkpoint dir, never a fabricated one."""
        service, project_id, project_dir = service_with_project
        missing_dir = project_dir / "models" / "never-created-state"

        with pytest.raises(TrainingValidationError):
            service.submit_job(project_id, _payload(resume_checkpoint_path=str(missing_dir)))

    def test_symlink_escaping_models_dir_is_rejected(
        self, service_with_project: tuple[TrainingService, str, Path]
    ) -> None:
        """A symlink planted inside models/ pointing outside the project must
        not be usable to escape containment — resolve() must follow it before
        the relative_to() check runs."""
        service, project_id, project_dir = service_with_project
        models_dir = project_dir / "models"
        models_dir.mkdir(parents=True)
        real_outside = project_dir.parent / "outside-target"
        real_outside.mkdir(parents=True)
        symlink_path = models_dir / "sneaky-state"
        try:
            symlink_path.symlink_to(real_outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation not permitted in this environment")

        with pytest.raises(TrainingValidationError):
            service.submit_job(project_id, _payload(resume_checkpoint_path=str(symlink_path)))


# ---------------------------------------------------------------------------
# API-level: POST /training endpoint maps TrainingValidationError -> 400
# ---------------------------------------------------------------------------

@pytest.fixture()
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    manager = ProjectManager(tmp_path / "projects")
    monkeypatch.setattr(main, "project_manager", manager)
    monkeypatch.setattr(main.generation_service, "project_manager", manager)
    monkeypatch.setattr(main.training_service, "project_manager", manager)
    # Avoid wiring a real executor (which would spawn a real subprocess via
    # SubprocessRunner) — training_service.submit_job with executor=None stops
    # at PLANNED, which is all these validation-boundary tests need.
    monkeypatch.setattr(main, "_get_or_create_executor", lambda: None)
    return TestClient(main.app, base_url="http://127.0.0.1:8401")


def _create_project(client: TestClient) -> tuple[str, Path]:
    resp = client.post("/api/v1/projects", json={"name": "ResumeApiProj", "type": "RPG", "synopsis": "s"})
    assert resp.status_code == 200, resp.text
    project_id = resp.json()["data"]["project"]["id"]
    return project_id, main.project_manager.get_project(project_id)[1]


class TestCreateTrainingJobEndpointResumePath:
    def test_valid_checkpoint_path_returns_200(self, api_client: TestClient) -> None:
        project_id, project_dir = _create_project(api_client)
        checkpoint_dir = project_dir / "models" / "kyuoka_lora-state"
        checkpoint_dir.mkdir(parents=True)

        resp = api_client.post(
            f"/api/v1/projects/{project_id}/training",
            json={
                "title": "Resumed job",
                "modality": "image",
                "dataset_path": "/data/ds",
                "resume_checkpoint_path": str(checkpoint_dir),
            },
        )
        assert resp.status_code == 200, resp.text
        jobs = resp.json()["data"]["jobs"]
        assert jobs[-1]["resume_checkpoint_path"] is not None
        assert Path(jobs[-1]["resume_checkpoint_path"]) == checkpoint_dir.resolve()

    def test_path_escaping_project_root_returns_400(self, api_client: TestClient) -> None:
        project_id, project_dir = _create_project(api_client)
        outside_dir = project_dir.parent / "evil-project" / "models" / "evil-state"
        outside_dir.mkdir(parents=True)

        resp = api_client.post(
            f"/api/v1/projects/{project_id}/training",
            json={
                "title": "Malicious resume",
                "modality": "image",
                "dataset_path": "/data/ds",
                "resume_checkpoint_path": str(outside_dir),
            },
        )
        assert resp.status_code == 400, resp.text

    def test_omitted_resume_path_still_returns_200(self, api_client: TestClient) -> None:
        """Regression guard: adding the field must not break a plain submit."""
        project_id, _project_dir = _create_project(api_client)
        resp = api_client.post(
            f"/api/v1/projects/{project_id}/training",
            json={"title": "Fresh job", "modality": "image", "dataset_path": "/data/ds"},
        )
        assert resp.status_code == 200, resp.text
        jobs = resp.json()["data"]["jobs"]
        assert jobs[-1]["resume_checkpoint_path"] is None
