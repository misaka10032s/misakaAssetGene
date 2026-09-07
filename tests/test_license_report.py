"""Tests for M5.2 License Report polish — commercial bool, attribution, NSFW, export summary.

Coverage:
- commercial bool read correctly from workers/manifest.json (True / False / missing → None)
- commercial legacy string coercion (truthful guard: non-bool string → None + warning)
- attribution detection per known SPDX ids (Apache-2.0, MIT, CC-BY-NC-4.0, CC0-1.0, custom)
- NSFW rollup: none present → has_nsfw=False; some present → has_nsfw=True
- NSFW read from worker definition vs. registry fallback
- summary counts (total_workers, commercial_ok, attribution_required, nsfw_present, etc.)
- export still embeds the enriched report (ProjectExportService integration)
- ProjectLicenseReport.summary field present and correct type
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.models.schemas import (
    GenerationJobStatus,
    LicenseReportEntry,
    LicenseReportSummary,
    Modality,
    ProjectLicenseReport,
)
from core.project.export import ProjectExportService
from core.reporting.license import LicenseReportService, _resolve_attribution


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_job(worker: str, modality: str = "image", job_id: str = "job1"):
    job = MagicMock()
    job.worker = worker
    job.modality = MagicMock()
    job.modality.value = modality
    job.id = job_id
    return job


def _make_asset(job_id: str = "job1"):
    asset = MagicMock()
    asset.job_id = job_id
    return asset


def _make_workers_service(
    *,
    worker_name: str = "comfyui",
    display_name: str = "ComfyUI",
    definition: dict | None = None,
    path: str = "/tmp/nonexistent_path",
    readiness_note: str | None = None,
) -> MagicMock:
    """Build a minimal mock WorkersService for a single worker."""
    if definition is None:
        definition = {"license": "GPL-3.0", "commercial": False}

    snapshot = MagicMock()
    snapshot.display_name = display_name
    snapshot.repo = "https://github.com/test/repo.git"
    snapshot.path = path
    snapshot.recommended_reference = "v1.0"
    snapshot.installed_reference = "v1.0"
    snapshot.readiness_note = readiness_note

    svc = MagicMock()
    svc.get_worker_definition.return_value = definition
    svc.get_worker.return_value = snapshot
    return svc


def _report_for(
    *,
    worker_name: str = "comfyui",
    display_name: str = "ComfyUI",
    definition: dict | None = None,
    registry_path: Path | None = None,
    n_jobs: int = 1,
    n_assets: int = 1,
) -> ProjectLicenseReport:
    """Generate a report for a single worker with configurable definition."""
    svc = LicenseReportService()
    workers_service = _make_workers_service(
        worker_name=worker_name,
        display_name=display_name,
        definition=definition or {"license": "Apache-2.0", "commercial": True},
    )
    jobs = [_make_job(worker_name, job_id=f"job{i}") for i in range(n_jobs)]
    assets = [_make_asset(job_id=f"job{i}") for i in range(n_assets)]
    return svc.generate_report(
        project_summary={"id": "proj1", "name": "Test Project"},
        jobs=jobs,
        assets=assets,
        workers_service=workers_service,
        registry_path=registry_path,
    )


# ---------------------------------------------------------------------------
# 1. commercial bool from registry
# ---------------------------------------------------------------------------

def test_commercial_true_from_definition() -> None:
    report = _report_for(definition={"license": "Apache-2.0", "commercial": True})
    assert len(report.entries) == 1
    assert report.entries[0].commercial is True


def test_commercial_false_from_definition() -> None:
    report = _report_for(definition={"license": "CC-BY-NC-4.0", "commercial": False})
    assert report.entries[0].commercial is False


def test_commercial_none_when_field_absent() -> None:
    report = _report_for(definition={"license": "MIT"})
    assert report.entries[0].commercial is None


def test_commercial_none_when_field_is_null() -> None:
    report = _report_for(definition={"license": "custom", "commercial": None})
    assert report.entries[0].commercial is None


def test_commercial_legacy_string_true_coerced() -> None:
    """Legacy string 'true' should be coerced to True bool (backwards compat)."""
    report = _report_for(definition={"license": "MIT", "commercial": "true"})
    assert report.entries[0].commercial is True


def test_commercial_legacy_string_false_coerced() -> None:
    report = _report_for(definition={"license": "MIT", "commercial": "false"})
    assert report.entries[0].commercial is False


def test_commercial_legacy_string_unknown_becomes_none_with_warning() -> None:
    """Non-boolean string value should become None and add a warning."""
    report = _report_for(definition={"license": "MIT", "commercial": "maybe"})
    assert report.entries[0].commercial is None
    assert any("not a boolean" in w for w in report.warnings)


# ---------------------------------------------------------------------------
# 2. Attribution detection per license types
# ---------------------------------------------------------------------------

def test_attribution_apache_required() -> None:
    req, note = _resolve_attribution("Apache-2.0")
    assert req is True
    assert note is not None and "NOTICE" in note


def test_attribution_mit_required() -> None:
    req, note = _resolve_attribution("MIT")
    assert req is True
    assert note is not None


def test_attribution_cc_by_nc_required() -> None:
    req, note = _resolve_attribution("CC-BY-NC-4.0")
    assert req is True
    assert note is not None


def test_attribution_cc0_not_required() -> None:
    req, note = _resolve_attribution("CC0-1.0")
    assert req is False
    assert note is not None


def test_attribution_unlicense_not_required() -> None:
    req, note = _resolve_attribution("Unlicense")
    assert req is False


def test_attribution_custom_license_unknown() -> None:
    req, note = _resolve_attribution("custom")
    assert req is None
    assert note is None


def test_attribution_flux_license_unknown() -> None:
    req, note = _resolve_attribution("Flux")
    assert req is None
    assert note is None


def test_attribution_none_license_unknown() -> None:
    req, note = _resolve_attribution(None)
    assert req is None
    assert note is None


def test_attribution_gpl3_required() -> None:
    req, note = _resolve_attribution("GPL-3.0")
    assert req is True


def test_attribution_bsd3_required() -> None:
    req, note = _resolve_attribution("BSD-3-Clause")
    assert req is True


def test_attribution_populated_on_entry_apache() -> None:
    report = _report_for(definition={"license": "Apache-2.0", "commercial": True})
    entry = report.entries[0]
    assert entry.attribution is True
    assert entry.attribution_note is not None and "NOTICE" in entry.attribution_note


def test_attribution_populated_on_entry_cc0() -> None:
    report = _report_for(definition={"license": "CC0-1.0", "commercial": True})
    entry = report.entries[0]
    assert entry.attribution is False


def test_attribution_unknown_for_custom_license() -> None:
    report = _report_for(definition={"license": "custom"})
    entry = report.entries[0]
    assert entry.attribution is None
    assert entry.attribution_note is None


# ---------------------------------------------------------------------------
# 3. NSFW rollup — none / some
# ---------------------------------------------------------------------------

def _make_multi_worker_report(
    definitions: dict[str, dict],
    registry_path: Path | None = None,
) -> ProjectLicenseReport:
    """Generate a report for multiple workers."""
    svc = LicenseReportService()
    workers_service = MagicMock()

    def _get_definition(name: str) -> dict:
        return definitions.get(name, {"license": "MIT"})

    def _get_snapshot(name: str) -> MagicMock:
        snap = MagicMock()
        snap.display_name = name
        snap.repo = "https://github.com/test/repo.git"
        snap.path = "/tmp/nonexistent_path"
        snap.recommended_reference = "v1.0"
        snap.installed_reference = "v1.0"
        snap.readiness_note = None
        return snap

    workers_service.get_worker_definition.side_effect = _get_definition
    workers_service.get_worker.side_effect = _get_snapshot

    jobs = [_make_job(name, job_id=f"job_{name}") for name in definitions]
    assets = [_make_asset(job_id=f"job_{name}") for name in definitions]
    return svc.generate_report(
        project_summary={"id": "proj1", "name": "Test"},
        jobs=jobs,
        assets=assets,
        workers_service=workers_service,
        registry_path=registry_path,
    )


def test_nsfw_rollup_none_when_all_false() -> None:
    report = _make_multi_worker_report({
        "comfyui": {"license": "GPL-3.0", "commercial": False, "nsfw": False},
        "voxcpm": {"license": "Apache-2.0", "commercial": True, "nsfw": False},
    })
    assert report.summary.has_nsfw is False
    assert report.summary.nsfw_present == 0
    assert report.summary.nsfw_absent == 2


def test_nsfw_rollup_true_when_any_nsfw() -> None:
    report = _make_multi_worker_report({
        "comfyui": {"license": "GPL-3.0", "commercial": False, "nsfw": True},
        "voxcpm": {"license": "Apache-2.0", "commercial": True, "nsfw": False},
    })
    assert report.summary.has_nsfw is True
    assert report.summary.nsfw_present == 1
    assert report.summary.nsfw_absent == 1


def test_nsfw_unknown_when_field_absent() -> None:
    report = _report_for(definition={"license": "MIT"})
    # nsfw field absent from definition and no registry — should be None
    assert report.entries[0].nsfw is None


def test_nsfw_from_worker_definition() -> None:
    report = _report_for(definition={"license": "Apache-2.0", "commercial": True, "nsfw": True})
    assert report.entries[0].nsfw is True


def test_nsfw_false_from_worker_definition() -> None:
    report = _report_for(definition={"license": "Apache-2.0", "commercial": True, "nsfw": False})
    assert report.entries[0].nsfw is False


def test_nsfw_from_registry_fallback(tmp_path: Path) -> None:
    """When worker definition has no nsfw field, fall back to the model registry."""
    registry = {
        "schema_version": 2,
        "categories": {
            "image_checkpoint": [
                {"name": "ComfyUI", "license": "GPL-3.0", "commercial": False, "nsfw": True}
            ]
        }
    }
    reg_path = tmp_path / "registry.json"
    reg_path.write_text(json.dumps(registry), encoding="utf-8")

    report = _report_for(
        display_name="ComfyUI",
        definition={"license": "GPL-3.0", "commercial": False},
        registry_path=reg_path,
    )
    # Registry entry name "ComfyUI" matches snapshot.display_name "ComfyUI"
    assert report.entries[0].nsfw is True


def test_nsfw_registry_absent_returns_unknown(tmp_path: Path) -> None:
    """When the registry has no matching model entry, nsfw stays None."""
    registry = {"schema_version": 2, "categories": {"music": [{"name": "MusicGen", "license": "CC-BY-NC-4.0", "nsfw": False}]}}
    reg_path = tmp_path / "registry.json"
    reg_path.write_text(json.dumps(registry), encoding="utf-8")

    report = _report_for(
        display_name="ComfyUI",
        definition={"license": "GPL-3.0"},
        registry_path=reg_path,
    )
    assert report.entries[0].nsfw is None


# ---------------------------------------------------------------------------
# 4. Summary counts
# ---------------------------------------------------------------------------

def test_summary_counts_totals() -> None:
    report = _make_multi_worker_report({
        "w1": {"license": "Apache-2.0", "commercial": True, "nsfw": False},
        "w2": {"license": "CC-BY-NC-4.0", "commercial": False, "nsfw": False},
        "w3": {"license": "custom"},  # commercial=None, attribution=None, nsfw=None
    })
    s = report.summary
    assert s.total_workers == 3
    assert s.commercial_ok == 1
    assert s.commercial_no == 1
    assert s.commercial_unknown == 1
    assert s.attribution_required == 2   # Apache-2.0 and CC-BY-NC-4.0
    assert s.attribution_not_required == 0
    assert s.attribution_unknown == 1    # custom
    assert s.nsfw_absent == 2
    assert s.nsfw_unknown == 1
    assert s.nsfw_present == 0
    assert s.has_nsfw is False


def test_summary_present_on_report() -> None:
    report = _report_for()
    assert isinstance(report.summary, LicenseReportSummary)


def test_summary_zero_entries() -> None:
    svc = LicenseReportService()
    ws = MagicMock()
    report = svc.generate_report(
        project_summary={"id": "p1", "name": "Empty"},
        jobs=[],
        assets=[],
        workers_service=ws,
    )
    assert report.summary.total_workers == 0
    assert report.summary.has_nsfw is False


# ---------------------------------------------------------------------------
# 5. Export embeds enriched report
# ---------------------------------------------------------------------------

def test_export_embeds_license_report(tmp_path: Path) -> None:
    """ProjectExportService must write the enriched license-report.json into the zip."""
    from zipfile import ZipFile

    project_dir = tmp_path / "projects" / "test_proj"
    project_dir.mkdir(parents=True)
    (project_dir / "project.json").write_text(
        json.dumps({"id": "test_proj", "name": "Test", "type": "RPG", "synopsis": ""}),
        encoding="utf-8",
    )

    # Build an enriched license report with the new fields
    entry = LicenseReportEntry(
        worker_name="comfyui",
        display_name="ComfyUI",
        repo="https://github.com/test/repo.git",
        recommended_reference="v1.0",
        license="GPL-3.0",
        commercial=False,
        attribution=True,
        attribution_note="GPL-3.0 requires providing source and retaining copyright notices",
        nsfw=False,
        job_count=2,
        asset_count=1,
        modalities=["image"],
    )
    report = ProjectLicenseReport(
        project_id="test_proj",
        project_name="Test",
        entries=[entry],
        summary=LicenseReportSummary(
            total_workers=1,
            commercial_no=1,
            attribution_required=1,
            nsfw_absent=1,
        ),
    )

    export_svc = ProjectExportService()
    zip_path = export_svc.export_project(
        project_dir=project_dir,
        project_summary={"id": "test_proj", "name": "Test", "type": "RPG", "synopsis": ""},
        jobs=[],
        assets=[],
        plans=[],
        license_report=report.model_dump(mode="json"),
        resolve_refs=True,
    )

    assert zip_path.exists(), f"Export zip not created at {zip_path}"
    with ZipFile(zip_path) as z:
        names = z.namelist()
        assert "license-report.json" in names, f"license-report.json missing from zip; found: {names}"
        raw = json.loads(z.read("license-report.json").decode("utf-8"))

    # Verify enriched fields are present in the exported JSON
    assert len(raw["entries"]) == 1
    e = raw["entries"][0]
    assert e["commercial"] is False
    assert e["attribution"] is True
    assert e["nsfw"] is False
    assert e["attribution_note"] is not None

    # Verify summary block is present
    assert "summary" in raw
    assert raw["summary"]["total_workers"] == 1
    assert raw["summary"]["has_nsfw"] is False


# ---------------------------------------------------------------------------
# 6. Real registry.json schema (integration smoke)
# ---------------------------------------------------------------------------

def test_registry_has_nsfw_field_on_all_entries() -> None:
    """Verify every entry in the committed registry.json now carries a 'nsfw' key."""
    registry_path = Path(__file__).resolve().parents[1] / "core" / "models" / "registry.json"
    assert registry_path.exists(), "registry.json not found"
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    for category, models in data.get("categories", {}).items():
        for model in models:
            assert "nsfw" in model, (
                f"Model {model.get('name')!r} in category {category!r} is missing 'nsfw' field"
            )


def test_registry_commercial_is_bool_or_null() -> None:
    """Every 'commercial' field in registry.json must be a boolean or null."""
    registry_path = Path(__file__).resolve().parents[1] / "core" / "models" / "registry.json"
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    for category, models in data.get("categories", {}).items():
        for model in models:
            if "commercial" in model:
                val = model["commercial"]
                assert val is None or isinstance(val, bool), (
                    f"Model {model.get('name')!r} in {category!r}: commercial={val!r} is not bool or null"
                )


# ---------------------------------------------------------------------------
# 7. Production-path: /api/v1/projects/{id}/license-report route wiring
#
# Guards that registry_path is passed to generate_report() in the live endpoint,
# NOT just in tests.  If the wiring is reverted (registry_path omitted from the
# call site), the registry lookup returns {} and nsfw stays None — this test fails.
# ---------------------------------------------------------------------------

def test_license_report_endpoint_delivers_registry_nsfw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /api/v1/projects/{id}/license-report must return registry-derived nsfw (not None).

    gpt-sovits has no nsfw field in workers/manifest.json; its value must come
    from registry.json (entry "GPT-SoVITS", nsfw: false).  The test fails if
    registry_path is not wired at the call site.
    """
    from starlette.testclient import TestClient

    import core.main as main
    from core.models.schemas import GenerationJob, GenerationJobStatus, Modality
    from core.project.manager import ProjectManager

    manager = ProjectManager(tmp_path / "projects")
    monkeypatch.setattr(main, "project_manager", manager)
    monkeypatch.setattr(main.generation_service, "project_manager", manager)
    client = TestClient(main.app, base_url="http://127.0.0.1:8401")

    # Create a project
    resp = client.post("/api/v1/projects", json={"name": "WiringTest", "type": "RPG", "synopsis": "s"})
    assert resp.status_code == 200, resp.text
    project_id = resp.json()["data"]["project"]["id"]

    # Inject a job using worker "gpt-sovits" — manifest has no nsfw field for this worker,
    # so nsfw can only come from the registry entry "GPT-SoVITS" (nsfw: false).
    _, project_dir = manager.get_project(project_id)
    now = datetime.now(timezone.utc)
    job = GenerationJob(
        id="job-wiring-test",
        project_id=project_id,
        title="Test voice",
        modality=Modality.VOICE,
        asset_type="voice",
        status=GenerationJobStatus.PLANNED,
        prompt="test",
        summary="test",
        worker="gpt-sovits",
        created_at=now,
        updated_at=now,
    )
    jobs_path = project_dir / "jobs.json"
    jobs_path.write_text(
        json.dumps({"jobs": [job.model_dump(mode="json")]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Call the live endpoint
    resp = client.get(f"/api/v1/projects/{project_id}/license-report")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    entries = data["entries"]
    assert len(entries) == 1, f"Expected 1 entry, got: {entries}"
    entry = entries[0]
    assert entry["worker_name"] == "gpt-sovits"

    # nsfw MUST be False (from registry "GPT-SoVITS"), never None.
    # If registry_path is not wired at the call site this assertion fails.
    assert entry["nsfw"] is False, (
        f"nsfw={entry['nsfw']!r}: registry_path is NOT wired at the live call site — "
        "got None instead of False from registry entry 'GPT-SoVITS'"
    )
