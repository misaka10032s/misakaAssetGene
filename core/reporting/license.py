from __future__ import annotations

import json
from pathlib import Path

from core.models.schemas import (
    LicenseReportEntry,
    LicenseReportSummary,
    ProjectLicenseReport,
)

# ---------------------------------------------------------------------------
# Attribution lookup table — SPDX ids and common license names.
#
# Truthful-delivery invariant: only include licenses where attribution
# requirements are well-established. Any license not in these tables returns
# (None, None) so the report marks it as unknown rather than guessing.
#
# Table format: spdx_id_lower -> (attribution_required: bool, note: str)
# ---------------------------------------------------------------------------

_ATTRIBUTION_TABLE: dict[str, tuple[bool, str]] = {
    # Creative Commons — Attribution family
    "cc-by-4.0": (True, "CC-BY-4.0 requires attribution to the original author"),
    "cc-by-3.0": (True, "CC-BY-3.0 requires attribution to the original author"),
    "cc-by-2.0": (True, "CC-BY-2.0 requires attribution to the original author"),
    "cc-by-sa-4.0": (True, "CC-BY-SA-4.0 requires attribution and share-alike"),
    "cc-by-sa-3.0": (True, "CC-BY-SA-3.0 requires attribution and share-alike"),
    "cc-by-nc-4.0": (True, "CC-BY-NC-4.0 requires attribution; prohibits commercial use"),
    "cc-by-nc-3.0": (True, "CC-BY-NC-3.0 requires attribution; prohibits commercial use"),
    "cc-by-nc-sa-4.0": (True, "CC-BY-NC-SA-4.0 requires attribution, share-alike; prohibits commercial use"),
    "cc-by-nd-4.0": (True, "CC-BY-ND-4.0 requires attribution; prohibits derivatives"),
    # Apache
    "apache-2.0": (True, "Apache-2.0 requires NOTICE file preservation when distributing"),
    "apache 2.0": (True, "Apache-2.0 requires NOTICE file preservation when distributing"),
    # MIT — attribution required in source/binary distributions but minimal burden
    "mit": (True, "MIT license requires retaining the copyright notice in distributions"),
    # BSD family
    "bsd-2-clause": (True, "BSD-2-Clause requires retaining the copyright notice in distributions"),
    "bsd-3-clause": (True, "BSD-3-Clause requires retaining the copyright notice; prohibits use of contributor names in promotion"),
    # GPL / LGPL / AGPL — copyleft (attribution + share-alike)
    "gpl-2.0": (True, "GPL-2.0 requires providing source and retaining copyright notices"),
    "gpl-2.0-only": (True, "GPL-2.0-only requires providing source and retaining copyright notices"),
    "gpl-3.0": (True, "GPL-3.0 requires providing source and retaining copyright notices"),
    "gpl-3.0-only": (True, "GPL-3.0-only requires providing source and retaining copyright notices"),
    "lgpl-2.1": (True, "LGPL-2.1 requires retaining copyright notices in distributions"),
    "lgpl-3.0": (True, "LGPL-3.0 requires retaining copyright notices in distributions"),
    "agpl-3.0": (True, "AGPL-3.0 requires providing source and retaining copyright notices; extends to network use"),
    "agpl-3.0-only": (True, "AGPL-3.0-only requires providing source and retaining copyright notices; extends to network use"),
    # No attribution needed
    "cc0-1.0": (False, "CC0-1.0 dedicates the work to the public domain; no attribution required"),
    "cc0": (False, "CC0 dedicates the work to the public domain; no attribution required"),
    "unlicense": (False, "Unlicense dedicates the work to the public domain; no attribution required"),
    "0bsd": (False, "0BSD is a zero-clause BSD license; no attribution required"),
    "wtfpl": (False, "WTFPL — no restrictions"),
    # OpenRAIL / Stable Diffusion / custom model licenses — attribution varies;
    # not in SPDX — mark unknown so the report is honest.
    # "custom" and "flux" (from registry) are intentionally omitted here.
}

# Secondary lookup keyed on detected-license text patterns (lowercase) for the
# file-based fallback that returns the SPDX id string.
# This mirrors the _detect_license() output so the same lookup works for both
# SPDX ids coming from the registry and names detected from LICENSE files.
_SPDX_ALIASES: dict[str, str] = {
    "apache-2.0": "apache-2.0",
    "apache 2.0": "apache-2.0",
    "mit": "mit",
    "gpl-3.0": "gpl-3.0",
    "gpl-3.0-only": "gpl-3.0-only",
    "bsd-3-clause": "bsd-3-clause",
    "bsd-2-clause": "bsd-2-clause",
}


def _resolve_attribution(license_name: str | None) -> tuple[bool | None, str | None]:
    """Return (attribution_required, note) for a license id or name.

    Returns (None, None) when the license is unknown or cannot be mapped to a
    definitive attribution requirement. This is intentional — see truthful-delivery
    constraint in spec §2 / cluster-conventions.md.
    """
    if not license_name:
        return None, None
    normalized = license_name.strip().lower()
    if normalized in _ATTRIBUTION_TABLE:
        req, note = _ATTRIBUTION_TABLE[normalized]
        return req, note
    # Try alias resolution then retry
    alias = _SPDX_ALIASES.get(normalized)
    if alias and alias in _ATTRIBUTION_TABLE:
        req, note = _ATTRIBUTION_TABLE[alias]
        return req, note
    return None, None


class LicenseReportService:
    """Generate per-project license reports (spec §2 / §9.3 — M5.2).

    Deliverables (完整版 polish):
    1. commercial → real bool | None from registry (never coerced from string)
    2. attribution → derived from SPDX license id; unknown when uncertain
    3. nsfw → from registry nsfw field; None when absent
    4. summary → export-dialog counts (workers, commercial, attribution, nsfw)
    """

    def generate_report(
        self,
        *,
        project_summary: dict,
        jobs: list,
        assets: list,
        workers_service,
        registry_path: Path | None = None,
    ) -> ProjectLicenseReport:
        """Build a ProjectLicenseReport for the given project state.

        Parameters
        ----------
        project_summary : dict
            Must contain "id" and "name" keys; "generated_at" is optional.
        jobs : list of GenerationJob
            Used to count per-worker usage and collect modalities.
        assets : list of AssetRecord
            Used to count accepted assets per worker.
        workers_service : WorkersService
            Used to resolve worker definitions and snapshots.
        registry_path : Path or None
            Path to the model registry JSON for NSFW / commercial lookups.
            When None the registry lookup is skipped (safe default).
        """
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

        # Build registry lookup: model name -> {commercial, nsfw} for workers
        # that also appear in the model registry. The registry is keyed by model
        # name, not worker name, so we do a best-effort name match.
        registry_model_map = self._load_registry_model_map(registry_path)

        entries: list[LicenseReportEntry] = []
        warnings: list[str] = [
            "The current report covers worker-level provenance referenced by project jobs.",
            "Model-level license embedding will become more accurate after per-asset generation metadata is expanded.",
        ]

        for worker_name in sorted(job_counts):
            definition = workers_service.get_worker_definition(worker_name)
            snapshot = workers_service.get_worker(worker_name)
            detected_license = self._detect_license(Path(snapshot.path))

            # License: prefer explicit registry/manifest field, fall back to file detection
            license_name = str(definition.get("license") or detected_license or "").strip() or None
            if license_name is None:
                warnings.append(f"{snapshot.display_name} license could not be resolved automatically.")

            # commercial: must be a real bool from definition; None = unknown
            raw_commercial = definition.get("commercial")
            if raw_commercial is None:
                commercial: bool | None = None
            elif isinstance(raw_commercial, bool):
                commercial = raw_commercial
            else:
                # Coercion guard: if stored as a string (legacy), convert honestly.
                # Only accept "true"/"false" equivalents; anything else → None.
                val = str(raw_commercial).strip().lower()
                if val == "true":
                    commercial = True
                elif val == "false":
                    commercial = False
                else:
                    commercial = None
                    warnings.append(
                        f"{snapshot.display_name}: 'commercial' field value {raw_commercial!r} is not a "
                        f"boolean — treating as unknown. Fix the workers/manifest.json entry."
                    )

            # attribution: derive from license id
            attribution, attribution_note = _resolve_attribution(license_name)

            # nsfw: read from worker definition first, then fall back to registry
            raw_nsfw = definition.get("nsfw")
            if raw_nsfw is None:
                # Try to find a matching model entry in the registry
                registry_entry = registry_model_map.get(snapshot.display_name) or registry_model_map.get(worker_name)
                raw_nsfw = registry_entry.get("nsfw") if registry_entry else None
            nsfw: bool | None
            if raw_nsfw is None:
                nsfw = None
            elif isinstance(raw_nsfw, bool):
                nsfw = raw_nsfw
            else:
                val = str(raw_nsfw).strip().lower()
                nsfw = val == "true" if val in ("true", "false") else None

            entries.append(
                LicenseReportEntry(
                    worker_name=worker_name,
                    display_name=snapshot.display_name,
                    repo=snapshot.repo,
                    recommended_reference=snapshot.recommended_reference,
                    installed_reference=snapshot.installed_reference,
                    license=license_name,
                    commercial=commercial,
                    attribution=attribution,
                    attribution_note=attribution_note,
                    nsfw=nsfw,
                    job_count=job_counts.get(worker_name, 0),
                    asset_count=asset_counts.get(worker_name, 0),
                    modalities=sorted(modalities.get(worker_name, set())),
                    readiness_note=snapshot.readiness_note,
                )
            )

        summary = self._build_summary(entries)

        return ProjectLicenseReport(
            project_id=project_summary["id"],
            project_name=project_summary["name"],
            generated_at=project_summary.get("generated_at") or None,
            entries=entries,
            summary=summary,
            warnings=list(dict.fromkeys(warnings)),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_summary(self, entries: list[LicenseReportEntry]) -> LicenseReportSummary:
        """Build counts across all entries for the export-confirm dialog."""
        total = len(entries)
        commercial_ok = sum(1 for e in entries if e.commercial is True)
        commercial_no = sum(1 for e in entries if e.commercial is False)
        commercial_unknown = sum(1 for e in entries if e.commercial is None)
        attr_req = sum(1 for e in entries if e.attribution is True)
        attr_not = sum(1 for e in entries if e.attribution is False)
        attr_unk = sum(1 for e in entries if e.attribution is None)
        nsfw_yes = sum(1 for e in entries if e.nsfw is True)
        nsfw_no = sum(1 for e in entries if e.nsfw is False)
        nsfw_unk = sum(1 for e in entries if e.nsfw is None)
        return LicenseReportSummary(
            total_workers=total,
            commercial_ok=commercial_ok,
            commercial_no=commercial_no,
            commercial_unknown=commercial_unknown,
            attribution_required=attr_req,
            attribution_not_required=attr_not,
            attribution_unknown=attr_unk,
            nsfw_present=nsfw_yes,
            nsfw_absent=nsfw_no,
            nsfw_unknown=nsfw_unk,
            has_nsfw=nsfw_yes > 0,
        )

    def _load_registry_model_map(self, registry_path: Path | None) -> dict[str, dict]:
        """Return a flat dict mapping model name -> registry dict.

        Used for NSFW / commercial fallback when a worker definition is missing the
        field but the model appears in the registry by display_name.
        """
        if registry_path is None or not registry_path.exists():
            return {}
        try:
            data = json.loads(registry_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        result: dict[str, dict] = {}
        for models in data.get("categories", {}).values():
            for model in models:
                name = str(model.get("name") or "").strip()
                if name:
                    result[name] = model
        return result

    def _detect_license(self, worker_path: Path) -> str | None:
        """Scan common LICENSE file locations and return the SPDX id if detectable."""
        if not worker_path.exists():
            return None
        for name in ("LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING"):
            license_path = worker_path / name
            if not license_path.exists():
                continue
            text = license_path.read_text(encoding="utf-8", errors="ignore").lower()
            if "apache license" in text and "version 2" in text:
                return "Apache-2.0"
            if "apache license" in text:
                return "Apache-2.0"
            if "mit license" in text:
                return "MIT"
            if "gnu general public license" in text and "version 3" in text:
                return "GPL-3.0"
            if "bsd 3-clause" in text:
                return "BSD-3-Clause"
            if "bsd 2-clause" in text:
                return "BSD-2-Clause"
            return license_path.name
        return None
