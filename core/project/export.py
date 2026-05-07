from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


class ProjectExportService:
    def export_project(
        self,
        *,
        project_dir: Path,
        project_summary: dict,
        jobs: list[dict],
        assets: list[dict],
        plans: list[dict],
        license_report: dict,
        resolve_refs: bool,
    ) -> Path:
        export_root = project_dir / ".cache" / "exports"
        export_root.mkdir(parents=True, exist_ok=True)
        exported_at = datetime.now(timezone.utc)
        export_name = f"{project_summary['id']}-{exported_at.strftime('%Y%m%d-%H%M%S')}.zip"
        export_path = export_root / export_name

        export_manifest = {
            "project": project_summary,
            "exported_at": exported_at.isoformat(),
            "resolve_refs": resolve_refs,
            "jobs": len(jobs),
            "assets": len(assets),
            "plans": len(plans),
            "warnings": [
                "Cross-project references are preserved as relative project files in this export.",
            ],
        }
        files_to_pack = [
            candidate
            for candidate in project_dir.rglob("*")
            if candidate.is_file() and not self._should_skip(candidate, project_dir, export_root)
        ]
        with ZipFile(export_path, "w", compression=ZIP_DEFLATED) as archive:
            for file_path in files_to_pack:
                archive.write(file_path, arcname=str(file_path.relative_to(project_dir)))
            archive.writestr(
                "export.manifest.json",
                json.dumps(export_manifest, ensure_ascii=False, indent=2) + "\n",
            )
            archive.writestr(
                "license-report.json",
                json.dumps(license_report, ensure_ascii=False, indent=2) + "\n",
            )
        return export_path

    def _should_skip(self, candidate: Path, project_dir: Path, export_root: Path) -> bool:
        if candidate == project_dir / "assets" / "index.json":
            return False
        if candidate.is_relative_to(export_root):
            return True
        return candidate.suffix.lower() in {".tmp", ".temp"}
