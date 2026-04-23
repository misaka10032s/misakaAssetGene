import json
import logging
import re
from pathlib import Path

from core.models.schemas import ProjectCreateRequest, ProjectSummary, ProjectTypeSuggestion
from core.project.style_guide import build_initial_style_guide

PROJECT_TYPES = [project_type.value for project_type in ProjectTypeSuggestion]
NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
logger = logging.getLogger("misaka.project")

class ProjectValidationError(ValueError):
    """Raised when a project request does not satisfy schema rules."""


class ProjectConflictError(FileExistsError):
    """Raised when a project already exists."""


class ProjectNotFoundError(FileNotFoundError):
    """Raised when a selected project does not exist."""


class ProjectManager:
    def __init__(self, projects_root: Path) -> None:
        self.projects_root = projects_root
        self.projects_root.mkdir(parents=True, exist_ok=True)
        self.selection_path = self.projects_root / ".current_project.json"
        logger.info("ProjectManager initialized at %s", self.projects_root)

    def list_projects(self) -> list[ProjectSummary]:
        projects: list[ProjectSummary] = []
        for project_json in self.projects_root.glob("*/project.json"):
            data = json.loads(project_json.read_text(encoding="utf-8"))
            projects.append(ProjectSummary(**data))
        logger.info("Loaded %d project(s) from disk", len(projects))
        return sorted(projects, key=lambda item: item.name)

    def create_project(self, request: ProjectCreateRequest) -> ProjectSummary:
        self._validate_request(request)
        project_dir = self.projects_root / request.name
        try:
            project_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError as error:
            raise ProjectConflictError(f"Project already exists: {request.name}") from error
        logger.info("Creating project directory %s", project_dir)

        for relative_dir in [
            "assets/images",
            "assets/audio",
            "assets/video",
            "assets/text",
            "vectors",
            "_external",
            ".cache",
        ]:
            (project_dir / relative_dir).mkdir(parents=True, exist_ok=True)

        project_summary = ProjectSummary(
            name=request.name,
            type=request.type,
            synopsis=request.synopsis.strip(),
        )
        (project_dir / "project.json").write_text(
            json.dumps(project_summary.model_dump(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (project_dir / "style_guide.md").write_text(
            build_initial_style_guide(request),
            encoding="utf-8",
        )
        if self.get_current_project_name() is None:
            self.select_project(request.name)
        logger.info("Project %s initialized with project.json and style_guide.md", request.name)
        return project_summary

    def select_project(self, project_name: str) -> ProjectSummary:
        project_path = self.projects_root / project_name / "project.json"
        if not project_path.exists():
            raise ProjectNotFoundError(f"Project not found: {project_name}")

        data = json.loads(project_path.read_text(encoding="utf-8"))
        summary = ProjectSummary(**data)
        self.selection_path.write_text(
            json.dumps({"name": summary.name}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info("Selected project %s", summary.name)
        return summary

    def get_current_project_name(self) -> str | None:
        if not self.selection_path.exists():
            return None
        payload = json.loads(self.selection_path.read_text(encoding="utf-8"))
        selected_name = str(payload.get("name") or "") or None
        if not selected_name:
            return None

        project_path = self.projects_root / selected_name / "project.json"
        if project_path.exists():
            return selected_name

        logger.warning("Current project selection is stale; clearing %s", selected_name)
        self.selection_path.unlink(missing_ok=True)
        return None

    def _validate_request(self, request: ProjectCreateRequest) -> None:
        if not request.type.strip():
            raise ProjectValidationError("Project type must not be empty.")
        if not NAME_PATTERN.match(request.name):
            raise ProjectValidationError("Project name must use letters, numbers, underscore, or hyphen.")
        logger.info("Validated project request name=%s type=%s", request.name, request.type)
