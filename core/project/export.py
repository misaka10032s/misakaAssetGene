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

        # §5.6.4 — re-resolve all cross-project references before packing, so
        # the exported zip is self-contained and current.  Stale _external/
        # entries are refreshed; the resolved state is recorded in the manifest.
        ref_resolution_summary: list[dict] = []
        if resolve_refs:
            ref_resolution_summary = self._refresh_external_copies(project_dir, project_summary)

        export_manifest = {
            "project": project_summary,
            "exported_at": exported_at.isoformat(),
            "resolve_refs": resolve_refs,
            "jobs": len(jobs),
            "assets": len(assets),
            "plans": len(plans),
            "ref_resolution": ref_resolution_summary,
            "warnings": [],
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

    def _refresh_external_copies(
        self,
        project_dir: Path,
        project_summary: dict,
    ) -> list[dict]:
        """Re-parse all @refs and refresh _external/ copies + origins.json (§5.6.4).

        For each resolved ref:
        - If the source is LIVE: copy current live file into _external/, update origins.json.
        - If the source is EXTERNAL (already a copy): keep as-is (pinned versions preserved).
        - If the source is OUTDATED: refresh the _external/ copy from the live source.
        - If the source is BROKEN: record in the summary; do NOT pack stale copies.

        Returns a list of per-ref summary dicts for the export manifest.
        """
        from core.project.cross_project import (
            RefStatus,
            collect_project_refs,
            resolve_reference,
            copy_external_asset,
            update_origins_json,
            _sha256_file,
        )
        import hashlib
        import datetime as _dt

        # Locate projects_root as the parent of project_dir
        projects_root = project_dir.parent

        refs = collect_project_refs(project_dir)
        summary: list[dict] = []

        for ref in refs:
            resolution = resolve_reference(ref, project_dir, projects_root)
            status = resolution["status"]
            entry: dict = {"ref": ref, "status": status, "message": resolution["message"]}

            if status in (RefStatus.LIVE, RefStatus.OUTDATED):
                # Refresh the _external/ copy from the live source
                source_file: Path = resolution["path"]
                from core.project.cross_project import parse_reference, _ref_to_id
                parsed = parse_reference(ref)
                if parsed is None:
                    entry["message"] = f"Could not re-parse ref '{ref}' during refresh."
                    summary.append(entry)
                    continue

                source_project_id = parsed["project"]
                asset_path = parsed["asset_path"]
                version = parsed["version"]

                # BLOCKER 2 guard: verify source_file is inside the source project root
                # before copying it into _external/.  A ref whose asset_path contains '..'
                # could resolve to a file outside the project boundary.
                source_project_dir = projects_root / source_project_id
                try:
                    source_file.resolve().relative_to(source_project_dir.resolve())
                except ValueError:
                    # Source file escapes the source project root — skip + mark broken.
                    entry["status"] = RefStatus.BROKEN
                    entry["message"] = (
                        f"Security: source file {source_file} resolves outside "
                        f"source project dir {source_project_dir}. Skipping copy."
                    )
                    summary.append(entry)
                    continue

                file_hash = _sha256_file(source_file)
                now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()

                rel_path = f"{asset_path}/{version}{source_file.suffix}" if version else (
                    f"{asset_path}" if not version else asset_path
                )
                if version and rel_path.count(source_file.suffix) > 1 and source_file.suffix:
                    rel_path = f"{asset_path}/{version}"

                try:
                    copy_external_asset(
                        source_file,
                        project_dir,
                        source_project_id,
                        rel_path,
                    )
                    update_origins_json(
                        project_dir,
                        source_project_id,
                        rel_path,
                        {"version": version or "", "sha256": file_hash, "copied_at": now_iso},
                    )
                    entry["refreshed_path"] = str(rel_path)
                    entry["hash"] = file_hash
                except Exception as exc:
                    entry["status"] = RefStatus.BROKEN
                    entry["message"] = f"Failed to refresh _external/ copy: {exc}"

            elif status == RefStatus.EXTERNAL:
                # Already have a copy — origins.json is current; keep as-is.
                entry["path"] = str(resolution.get("path", ""))

            elif status == RefStatus.BROKEN:
                # Remove any stale _external/ copy to avoid packing outdated data.
                self._remove_stale_external(project_dir, ref)

            summary.append(entry)

        return summary

    def _remove_stale_external(self, project_dir: Path, ref: str) -> None:
        """Remove _external/ entries for a broken ref to avoid packing stale data."""
        from core.project.cross_project import parse_reference, _load_origins
        parsed = parse_reference(ref)
        if parsed is None:
            return
        source_project_id = parsed["project"]
        asset_path = parsed["asset_path"]
        external_dir = project_dir / "_external" / source_project_id / asset_path
        if external_dir.exists():
            import shutil
            shutil.rmtree(external_dir, ignore_errors=True)

    def _should_skip(self, candidate: Path, project_dir: Path, export_root: Path) -> bool:
        if candidate == project_dir / "assets" / "index.json":
            return False
        if candidate.is_relative_to(export_root):
            return True
        # Exclude consultant cache: plans are user-private AI working memory,
        # not portable user assets (spec §5.14).  Legacy zips that contain
        # .cache/consultant/ entries are handled gracefully on import (skipped).
        consultant_cache = project_dir / ".cache" / "consultant"
        if candidate.is_relative_to(consultant_cache):
            return True
        return candidate.suffix.lower() in {".tmp", ".temp"}
