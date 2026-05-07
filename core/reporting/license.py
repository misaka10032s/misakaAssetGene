from __future__ import annotations

from pathlib import Path

from core.models.schemas import LicenseReportEntry, ProjectLicenseReport


class LicenseReportService:
    def generate_report(self, *, project_summary: dict, jobs: list, assets: list, workers_service) -> ProjectLicenseReport:
        job_counts: dict[str, int] = {}
        asset_counts: dict[str, int] = {}
        modalities: dict[str, set[str]] = {}
        asset_job_ids = {asset.job_id for asset in assets if asset.job_id}

        for job in jobs:
            if not job.worker:
                continue
            job_counts[job.worker] = job_counts.get(job.worker, 0) + 1
            modalities.setdefault(job.worker, set()).add(job.modality.value)
            if job.id in asset_job_ids:
                asset_counts[job.worker] = asset_counts.get(job.worker, 0) + 1

        entries: list[LicenseReportEntry] = []
        warnings: list[str] = [
            "The current report covers worker-level provenance referenced by project jobs.",
            "Model-level license embedding will become more accurate after per-asset generation metadata is expanded.",
        ]

        for worker_name in sorted(job_counts):
            definition = workers_service.get_worker_definition(worker_name)
            snapshot = workers_service.get_worker(worker_name)
            detected_license = self._detect_license(Path(snapshot.path))
            license_name = str(definition.get("license") or detected_license or "").strip() or None
            if license_name is None:
                warnings.append(f"{snapshot.display_name} license could not be resolved automatically.")
            entries.append(
                LicenseReportEntry(
                    worker_name=worker_name,
                    display_name=snapshot.display_name,
                    repo=snapshot.repo,
                    recommended_reference=snapshot.recommended_reference,
                    installed_reference=snapshot.installed_reference,
                    license=license_name,
                    commercial=str(definition.get("commercial") or "").strip() or None,
                    job_count=job_counts.get(worker_name, 0),
                    asset_count=asset_counts.get(worker_name, 0),
                    modalities=sorted(modalities.get(worker_name, set())),
                    readiness_note=snapshot.readiness_note,
                )
            )

        return ProjectLicenseReport(
            project_id=project_summary["id"],
            project_name=project_summary["name"],
            generated_at=project_summary.get("generated_at") or None,
            entries=entries,
            warnings=list(dict.fromkeys(warnings)),
        )

    def _detect_license(self, worker_path: Path) -> str | None:
        if not worker_path.exists():
            return None
        for name in ("LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING"):
            license_path = worker_path / name
            if not license_path.exists():
                continue
            text = license_path.read_text(encoding="utf-8", errors="ignore").lower()
            if "apache license" in text:
                return "Apache-2.0"
            if "mit license" in text:
                return "MIT"
            if "gnu general public license" in text and "version 3" in text:
                return "GPL-3.0"
            if "bsd 3-clause" in text:
                return "BSD-3-Clause"
            return license_path.name
        return None
